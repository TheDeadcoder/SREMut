"""Runs AIOpsLab audit episodes on one server; each goes to runs/<operator>/<problem_id>/attempt-<n>/."""
import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent / "verifier_mutation"), str(HERE.parent / "registry_sweep")]

from operators import OPERATORS  # noqa: E402
from run_mutation import must_stop  # noqa: E402
from run_sweep import cluster_problem, mark_interrupted, run_attempt  # noqa: E402

SCHEDULE = HERE / "schedule.csv"
PROBE = HERE / "probe.py"
RESET = HERE / "cluster_reset.py"
PILOT = ("k8s_target_port-misconfig-mitigation-1", "revoke_auth_mongodb-mitigation-1")


def probe_command(pid, operator):
    return lambda run_dir: [str(PROBE), "--problem", pid, "--operator", operator,
                            "--out", str(run_dir / "result.json")]


def reset_cluster():
    proc = subprocess.run([sys.executable, str(RESET)], capture_output=True, text=True)
    lines = proc.stdout.strip().splitlines()
    if proc.returncode != 0 or not lines:
        return None, f"cluster reset failed: {(proc.stderr or proc.stdout).strip()[-300:]}"
    return json.loads(lines[-1]), cluster_problem()


def episodes(server, pilot, schedule=SCHEDULE):
    if pilot:
        return [(pid, operator) for pid in PILOT for operator in OPERATORS]
    with open(schedule, newline="") as f:
        return [(row["problem_id"], row["operator"]) for row in csv.DictReader(f) if row["server"] == server]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--server", required=True)
    ap.add_argument("--attempt", type=int, default=1)
    ap.add_argument("--pilot", action="store_true", help="every operator on the pilot problems, kept under pilot/")
    ap.add_argument("--timeout", type=int, default=5400)
    ap.add_argument("--aiopslab", type=Path, default=Path.home() / "AIOpsLab")
    args = ap.parse_args()

    root = HERE / ("pilot" if args.pilot else "runs")
    for operator in OPERATORS:
        (root / operator).mkdir(parents=True, exist_ok=True)
        mark_interrupted(root / operator, args.server)
    for pid, operator in episodes(args.server, args.pilot):
        runs = root / operator
        if (runs / pid / f"attempt-{args.attempt}").exists():
            print(f"skip {operator} {pid}: attempt {args.attempt} already exists", flush=True)
            continue
        reset, problem = reset_cluster()
        if problem:
            print(f"stop: {problem}", flush=True)
            return 2
        print(f"start {operator} {pid} attempt {args.attempt}", flush=True)
        record = run_attempt(pid, args.attempt, args.server, 300, args.timeout, args.aiopslab, runs=runs,
                             command=probe_command(pid, operator), reset=reset)
        result_path = runs / pid / f"attempt-{args.attempt}" / "result.json"
        result = json.loads(result_path.read_text()) if result_path.exists() else {}
        mutant = result.get("mutant") or {}
        print(f"done {operator} {pid}: {record['status']} {result.get('status')} "
              f"applied={bool(result.get('applied'))} verdict={mutant.get('verdict')} "
              f"healthy={(mutant.get('reference') or {}).get('healthy')}", flush=True)
        if must_stop(record, result):
            print("stop: inspect the cluster before continuing", flush=True)
            return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())

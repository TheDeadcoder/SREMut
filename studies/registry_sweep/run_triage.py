"""Runs the triage probe on chosen problems; each attempt goes to triage/<problem_id>/attempt-<n>/."""
import argparse
import json
import sys
from pathlib import Path

from run_sweep import HERE, cluster_problem, load_plan, mark_interrupted, run_attempt, select

TRIAGE = HERE / "triage"
PROBE = HERE / "triage_probe.py"


def probe_command(pid):
    return lambda run_dir: [str(PROBE), "--problem", pid, "--out", str(run_dir / "triage.json")]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--server", required=True, choices=("A", "B"))
    ap.add_argument("--ids", nargs="+", required=True)
    ap.add_argument("--attempt", type=int, default=1)
    ap.add_argument("--timeout", type=int, default=5400)
    ap.add_argument("--sregym", type=Path, default=Path.home() / "SREGym")
    args = ap.parse_args()

    TRIAGE.mkdir(exist_ok=True)
    mark_interrupted(TRIAGE, args.server)
    for pid in select(load_plan(), args.server, ids=args.ids):
        run_dir = TRIAGE / pid / f"attempt-{args.attempt}"
        if run_dir.exists():
            print(f"skip {pid}: triage attempt {args.attempt} already exists", flush=True)
            continue
        problem = cluster_problem()
        if problem:
            print(f"stop: {problem}", flush=True)
            return 2
        print(f"start {pid} triage attempt {args.attempt}", flush=True)
        record = run_attempt(pid, args.attempt, args.server, 300, args.timeout, args.sregym,
                             runs=TRIAGE, command=probe_command(pid))
        result_path = run_dir / "triage.json"
        result = json.loads(result_path.read_text()) if result_path.exists() else {}
        print(f"done {pid}: {record['status']} {result.get('status')} "
              f"window={result.get('faulted', {}).get('profile')}", flush=True)
        if record["status"] != "COMPLETED" or result.get("cleanup") != "pass":
            print("stop: inspect the cluster before continuing", flush=True)
            return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())

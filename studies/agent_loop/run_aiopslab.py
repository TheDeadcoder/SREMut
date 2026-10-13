"""Runs the AIOpsLab agent episodes on one server; each goes to runs/aiopslab/<problem_id>/attempt-<n>/."""
import argparse
import csv
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent / "aiopslab_audit"), str(HERE.parent / "verifier_mutation"),
                str(HERE.parent / "registry_sweep")]

from agent import MODEL, TIMEOUT  # noqa: E402
from run_audit import SCHEDULE, reset_cluster  # noqa: E402
from run_sweep import run_attempt  # noqa: E402

EPISODE = HERE / "aiopslab_agent.py"


def problems(chosen=None, schedule=SCHEDULE):
    if chosen:
        return list(chosen)
    with open(schedule, newline="") as f:
        return sorted({row["problem_id"] for row in csv.DictReader(f)})


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--server", required=True)
    ap.add_argument("--attempt", type=int, default=1)
    ap.add_argument("--problems", nargs="+", help="run these problems instead of all 14")
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--agent-timeout", type=int, default=TIMEOUT)
    ap.add_argument("--timeout", type=int, default=5400)
    ap.add_argument("--aiopslab", type=Path, default=Path.home() / "AIOpsLab")
    ap.add_argument("--runs", type=Path, default=HERE / "runs")
    args = ap.parse_args()

    runs = (args.runs / "aiopslab").resolve()
    runs.mkdir(parents=True, exist_ok=True)
    for pid in problems(args.problems):
        if (runs / pid / f"attempt-{args.attempt}").exists():
            print(f"skip {pid}: attempt {args.attempt} already exists", flush=True)
            continue
        reset, problem = reset_cluster()
        if problem:
            print(f"stop: {problem}", flush=True)
            return 2
        print(f"start {pid} attempt {args.attempt}", flush=True)
        command = lambda run_dir: [str(EPISODE), "--problem", pid, "--model", args.model,  # noqa: E731
                                   "--agent-timeout", str(args.agent_timeout), "--out", str(run_dir / "result.json")]
        record = run_attempt(pid, args.attempt, args.server, 300, args.timeout, args.aiopslab, runs=runs,
                             command=command, reset=reset)
        result_path = runs / pid / f"attempt-{args.attempt}" / "result.json"
        result = json.loads(result_path.read_text()) if result_path.exists() else {}
        agent = result.get("agent") or {}
        verdicts = result.get("verdicts") or {}
        print(f"done {pid}: {record['status']} {result.get('status')} turns={agent.get('turns')} "
              f"seconds={agent.get('seconds')} original={verdicts.get('original', {}).get('profile')} "
              f"fixed={verdicts.get('fixed', {}).get('profile')} "
              f"healthy={(result.get('reference') or {}).get('healthy')}", flush=True)
        if record["status"] != "COMPLETED" or result.get("status") != "COMPLETED":
            print("stop: inspect the episode before continuing", flush=True)
            return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())

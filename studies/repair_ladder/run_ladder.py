"""Runs repair-ladder states on one server; each run goes to runs/<state>/attempt-<n>/."""
import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "registry_sweep"))

from mutants import STATES  # noqa: E402
from run_sweep import cluster_problem, mark_interrupted, run_attempt  # noqa: E402

PILOT_STATES = ("M4", "M5", "C1")
ATTEMPTS = (1, 2, 3)
DRIVER = HERE / "ladder.py"


def server_for(state, attempt):
    return "A" if (STATES.index(state) + attempt) % 2 == 1 else "B"


def schedule(server, attempt, pilot):
    if pilot:
        return list(PILOT_STATES)
    return [s for s in STATES if server_for(s, attempt) == server]


def driver_command(state):
    return lambda run_dir: [str(DRIVER), "--state", state, "--out", str(run_dir / "result.json")]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--server", required=True, choices=("A", "B"))
    ap.add_argument("--attempt", type=int, choices=ATTEMPTS, default=1)
    ap.add_argument("--pilot", action="store_true", help="the uncounted pilot of M4, M5 and C1")
    ap.add_argument("--timeout", type=int, default=5400)
    ap.add_argument("--sregym", type=Path, default=Path.home() / "SREGym-fix")
    args = ap.parse_args()

    runs = HERE / ("pilot" if args.pilot else "runs")
    runs.mkdir(exist_ok=True)
    mark_interrupted(runs, args.server)
    for state in schedule(args.server, args.attempt, args.pilot):
        run_dir = runs / state / f"attempt-{args.attempt}"
        if run_dir.exists():
            print(f"skip {state}: attempt {args.attempt} already exists", flush=True)
            continue
        problem = cluster_problem()
        if problem:
            print(f"stop: {problem}", flush=True)
            return 2
        print(f"start {state} attempt {args.attempt}", flush=True)
        record = run_attempt(state, args.attempt, args.server, 300, args.timeout, args.sregym,
                             runs=runs, command=driver_command(state))
        result_path = run_dir / "result.json"
        result = json.loads(result_path.read_text()) if result_path.exists() else {}
        print(f"done {state}: {record['status']} {result.get('status')} teardown={result.get('teardown')}",
              flush=True)
        if record["status"] != "COMPLETED" or result.get("teardown") != "pass":
            print("stop: inspect the cluster before continuing", flush=True)
            return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())

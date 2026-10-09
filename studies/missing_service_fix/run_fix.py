"""Runs SREGym's validator on the three missing_service problems against the patched checkout."""
import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "registry_sweep"))

from run_sweep import cluster_problem, mark_interrupted, run_attempt  # noqa: E402

RUNS = HERE / "runs"
PROBLEMS = (
    "missing_service_social_network",
    "missing_service_hotel_reservation",
    "missing_service_astronomy_shop",
)
SERVER_FOR_ATTEMPT = {1: "A", 2: "B", 3: "A"}
PATCHED_FILE = "sregym/conductor/problems/missing_service.py"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--attempt", type=int, required=True, choices=sorted(SERVER_FOR_ATTEMPT))
    ap.add_argument("--server", required=True, choices=("A", "B"))
    ap.add_argument("--timeout", type=int, default=5400)
    ap.add_argument("--sregym", type=Path, default=Path.home() / "SREGym-fix")
    args = ap.parse_args()

    if SERVER_FOR_ATTEMPT[args.attempt] != args.server:
        raise SystemExit(f"attempt {args.attempt} is planned for server {SERVER_FOR_ATTEMPT[args.attempt]}")
    if "ServiceEndpointMitigationOracle" not in (args.sregym / PATCHED_FILE).read_text():
        raise SystemExit(f"{args.sregym} does not carry the patch")

    RUNS.mkdir(exist_ok=True)
    mark_interrupted(RUNS, args.server)
    for pid in PROBLEMS:
        if (RUNS / pid / f"attempt-{args.attempt}").exists():
            print(f"skip {pid}: attempt {args.attempt} already exists", flush=True)
            continue
        problem = cluster_problem()
        if problem:
            print(f"stop: {problem}", flush=True)
            return 2
        print(f"start {pid} attempt {args.attempt}", flush=True)
        record = run_attempt(pid, args.attempt, args.server, 300, args.timeout, args.sregym, runs=RUNS)
        print(f"done {pid}: {record['status']} {record.get('stages')}", flush=True)
        if record["status"] != "COMPLETED" or record.get("stages", {}).get("cleanup") != "pass":
            print("stop: inspect the cluster before continuing", flush=True)
            return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())

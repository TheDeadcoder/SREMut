"""Runs the SREGym agent episodes on one server; each goes to runs/<benchmark>/<problem_id>/attempt-<n>/."""
import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent / "registry_sweep")]

from agent import MODEL, TIMEOUT  # noqa: E402
from run_sweep import mark_interrupted, reset_cluster, run_attempt  # noqa: E402

EPISODE = HERE / "sregym_agent.py"
ALERT = ("astronomy_shop_ad_service_failure", "astronomy_shop_ad_service_high_cpu",
         "astronomy_shop_cart_service_failure", "astronomy_shop_payment_service_failure",
         "astronomy_shop_product_catalog_service_failure")
NULL_ACCEPT = ("missing_service_astronomy_shop", "missing_service_social_network", "operator_wrong_operator_image")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--server", required=True)
    ap.add_argument("--attempt", type=int, default=1)
    ap.add_argument("--problems", nargs="+", default=ALERT + NULL_ACCEPT)
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--agent-timeout", type=int, default=TIMEOUT)
    ap.add_argument("--timeout", type=int, default=5400)
    ap.add_argument("--sregym", type=Path, default=Path.home() / "SREGym")
    ap.add_argument("--benchmark", default="sregym", help="folder under --runs, e.g. sregym-head for SREGym's HEAD")
    ap.add_argument("--runs", type=Path, default=HERE / "runs")
    args = ap.parse_args()

    runs = (args.runs / args.benchmark).resolve()
    runs.mkdir(parents=True, exist_ok=True)
    mark_interrupted(runs, args.server)
    for pid in args.problems:
        if (runs / pid / f"attempt-{args.attempt}").exists():
            print(f"skip {pid}: attempt {args.attempt} already exists", flush=True)
            continue
        reset, problem = reset_cluster(args.sregym)
        if problem:
            print(f"stop: {problem}", flush=True)
            return 2
        print(f"start {pid} attempt {args.attempt}", flush=True)
        command = lambda run_dir: [str(EPISODE), "--problem", pid, "--model", args.model,  # noqa: E731
                                   "--agent-timeout", str(args.agent_timeout), "--out", str(run_dir / "result.json")]
        record = run_attempt(pid, args.attempt, args.server, 300, args.timeout, args.sregym, runs=runs,
                             command=command, reset=reset)
        result_path = runs / pid / f"attempt-{args.attempt}" / "result.json"
        result = json.loads(result_path.read_text()) if result_path.exists() else {}
        agent = result.get("agent") or {}
        verdict = (result.get("verdicts") or {}).get("mitigation_oracle") or {}
        print(f"done {pid}: {record['status']} {result.get('status')} turns={agent.get('turns')} "
              f"seconds={agent.get('seconds')} oracle={verdict.get('profile')} "
              f"healthy={(result.get('reference') or {}).get('healthy')}", flush=True)
        if record["status"] != "COMPLETED" or result.get("status") != "COMPLETED":
            print("stop: inspect the episode before continuing", flush=True)
            return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())

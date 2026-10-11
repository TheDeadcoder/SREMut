"""Runs alert-baseline episodes on one server; each goes to runs/<problem_id>/attempt-<n>/."""
import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent / "registry_sweep"), str(HERE.parent / "verifier_mutation")]

from run_mutation import must_stop  # noqa: E402
from run_sweep import mark_interrupted, reset_cluster, run_attempt  # noqa: E402

PROBE = HERE / "probe.py"
PROBLEMS = ("astronomy_shop_ad_service_failure", "astronomy_shop_ad_service_high_cpu",
            "astronomy_shop_ad_service_image_slow_load", "astronomy_shop_ad_service_manual_gc",
            "astronomy_shop_cart_service_failure", "astronomy_shop_failed_readiness_probe",
            "astronomy_shop_payment_service_failure", "astronomy_shop_payment_service_unreachable",
            "astronomy_shop_product_catalog_service_failure", "kafka_queue_problems", "kubelet_crash",
            "loadgenerator_flood_homepage", "valkey_memory_disruption")
PILOT = ("kafka_queue_problems", "astronomy_shop_cart_service_failure")


def probe_command(pid):
    return lambda run_dir: [str(PROBE), "--problem", pid, "--out", str(run_dir / "result.json")]


def verdicts(result):
    immediate, delayed = result.get("immediate") or {}, result.get("delayed") or {}
    healthy = [(part.get("healthy") or {}).get("verdict", {}).get("success") for part in (immediate, delayed)]
    recovery = (immediate.get("recovery") or {}).get("verdict", {}).get("success")
    return f"healthy={healthy[0]}/{healthy[1]} recovery={recovery}/{(delayed.get('recovery') or {}).get('profile')}"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--server", required=True, choices=("A", "B", "C", "D"))
    ap.add_argument("--attempt", type=int, default=1)
    ap.add_argument("--pilot", action="store_true", help="only the pilot problems, kept under pilot/")
    ap.add_argument("--ids", nargs="+", help="explicit problem ids, e.g. for repeats")
    ap.add_argument("--timeout", type=int, default=5400)
    ap.add_argument("--sregym", type=Path, default=Path.home() / "SREGym")
    args = ap.parse_args()

    root = HERE / ("pilot" if args.pilot else "runs")
    root.mkdir(exist_ok=True)
    mark_interrupted(root, args.server)
    for pid in args.ids or (PILOT if args.pilot else PROBLEMS):
        run_dir = root / pid / f"attempt-{args.attempt}"
        if run_dir.exists():
            print(f"skip {pid}: attempt {args.attempt} already exists", flush=True)
            continue
        reset, problem = reset_cluster(args.sregym)
        if problem:
            print(f"stop: {problem}", flush=True)
            return 2
        print(f"start {pid} attempt {args.attempt}", flush=True)
        record = run_attempt(pid, args.attempt, args.server, 300, args.timeout, args.sregym, runs=root,
                             command=probe_command(pid), reset=reset)
        result_path = run_dir / "result.json"
        result = json.loads(result_path.read_text()) if result_path.exists() else {}
        print(f"done {pid}: {record['status']} {result.get('status')} {verdicts(result)}", flush=True)
        if must_stop(record, result):
            print("stop: inspect the cluster before continuing", flush=True)
            return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())

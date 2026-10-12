"""Runs the ITBench audit episodes in schedule.csv on one server; each goes to
runs/<scenario>/<action>/attempt-<n>/."""
import argparse
import csv
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent / "registry_sweep")]

from run_sweep import scrub, write_gz, write_json  # noqa: E402

SCHEDULE = HERE / "schedule.csv"
PROBE = HERE / "probe.py"
PILOT = (("20", "NULL"), ("20", "RESTART"))
TOOLS = "/opt/itbench/bin"


def episodes(pilot, schedule=SCHEDULE):
    if pilot:
        return list(PILOT)
    with open(schedule, newline="") as f:
        return [(row["scenario"], row["action"]) for row in csv.DictReader(f)]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--server", required=True, choices=("A", "B", "C", "D"))
    ap.add_argument("--attempt", type=int, default=1)
    ap.add_argument("--pilot", action="store_true", help="only the pilot episodes, kept under pilot/")
    ap.add_argument("--timeout", type=int, default=10800)
    ap.add_argument("--itbench", type=Path, default=Path.home() / "ITBench")
    args = ap.parse_args()
    os.environ["PATH"] = f"{TOOLS}:{os.environ['PATH']}"

    root = HERE / ("pilot" if args.pilot else "runs")
    for scenario, action in episodes(args.pilot):
        run_dir = root / scenario / action / f"attempt-{args.attempt}"
        if run_dir.exists():
            print(f"skip {scenario} {action}: attempt {args.attempt} already exists", flush=True)
            continue
        run_dir.mkdir(parents=True)
        record = {"scenario": scenario, "action": action, "attempt": args.attempt, "server": args.server,
                  "status": "RUNNING"}
        write_json(run_dir / "record.json", record)
        print(f"start {scenario} {action} attempt {args.attempt}", flush=True)
        start, log = time.monotonic(), ""
        try:
            proc = subprocess.run([sys.executable, str(PROBE), "--scenario", scenario, "--action", action,
                                   "--itbench", str(args.itbench), "--out", str(run_dir / "result.json")],
                                  capture_output=True, text=True, timeout=args.timeout)
            record["status"], log = "COMPLETED", proc.stdout + proc.stderr
        except subprocess.TimeoutExpired:
            record["status"] = "TIMED_OUT"
        record["duration_seconds"] = round(time.monotonic() - start)
        write_json(run_dir / "record.json", record)
        write_gz(run_dir / "stdout.log.gz", scrub(log))
        result_path = run_dir / "result.json"
        result = json.loads(result_path.read_text()) if result_path.exists() else {}
        print(f"done {scenario} {action}: {record['status']} {result.get('status')} "
              f"detected={result.get('detected')} alert_absent={result.get('alert_absent')} "
              f"healthy={(result.get('reference') or {}).get('healthy')}", flush=True)
        if record["status"] != "COMPLETED" or result.get("status") != "COMPLETED":
            print("stop: inspect the cluster before continuing", flush=True)
            return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())

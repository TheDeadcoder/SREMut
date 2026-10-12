"""Runs the k8s-ai-bench audit episodes in schedule.csv on one server; each goes to
runs/<task>/<action>/attempt-<n>/."""
import argparse
import csv
import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent / "registry_sweep")]

from run_sweep import scrub, write_gz, write_json  # noqa: E402

SCHEDULE = HERE / "schedule.csv"
PROBE = HERE / "probe.py"
PILOT = (("create-pod", "latest_tag"), ("fix-image-pull", "SCALE0"), ("statefulset-lifecycle", "recreate_with_one"))
CONTINUE = ("COMPLETED", "SETUP_FAILED")


def episodes(pilot, schedule=SCHEDULE):
    if pilot:
        return list(PILOT)
    with open(schedule, newline="") as f:
        return [(row["task"], row["action"]) for row in csv.DictReader(f)]


def verdicts(result):
    return " ".join(f"{key}={(result.get(f'verify_{key}') or {}).get('exit_code')}" for key in ("immediate", "settled"))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--server", required=True, choices=("A", "B", "C", "D"))
    ap.add_argument("--attempt", type=int, default=1)
    ap.add_argument("--pilot", action="store_true", help="only the pilot episodes, kept under pilot/")
    ap.add_argument("--timeout", type=int, default=3600)
    ap.add_argument("--bench", type=Path, default=Path.home() / "k8s-ai-bench")
    args = ap.parse_args()

    root = HERE / ("pilot" if args.pilot else "runs")
    for task, action in episodes(args.pilot):
        run_dir = root / task / action / f"attempt-{args.attempt}"
        if run_dir.exists():
            print(f"skip {task} {action}: attempt {args.attempt} already exists", flush=True)
            continue
        run_dir.mkdir(parents=True)
        record = {"task": task, "action": action, "attempt": args.attempt, "server": args.server, "status": "RUNNING"}
        write_json(run_dir / "record.json", record)
        print(f"start {task} {action} attempt {args.attempt}", flush=True)
        start, log = time.monotonic(), ""
        try:
            proc = subprocess.run([sys.executable, str(PROBE), "--task", task, "--action", action,
                                   "--bench", str(args.bench), "--out", str(run_dir / "result.json")],
                                  capture_output=True, text=True, timeout=args.timeout)
            record["status"], log = "COMPLETED", proc.stdout + proc.stderr
        except subprocess.TimeoutExpired:
            record["status"] = "TIMED_OUT"
        record["duration_seconds"] = round(time.monotonic() - start)
        write_json(run_dir / "record.json", record)
        write_gz(run_dir / "stdout.log.gz", scrub(log))
        result_path = run_dir / "result.json"
        result = json.loads(result_path.read_text()) if result_path.exists() else {}
        print(f"done {task} {action}: {record['status']} {result.get('status')} {verdicts(result)}", flush=True)
        if record["status"] != "COMPLETED" or result.get("status") not in CONTINUE:
            print("stop: inspect the host before continuing", flush=True)
            return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())

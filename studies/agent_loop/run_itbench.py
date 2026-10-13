"""Runs the ITBench agent episodes on one server; each goes to runs/itbench/<scenario>/attempt-<n>/."""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent / "registry_sweep")]

from agent import MODEL, TIMEOUT  # noqa: E402
from run_sweep import scrub, write_gz, write_json  # noqa: E402

EPISODE = HERE / "itbench_agent.py"
TOOLS = "/opt/itbench/bin"
SCENARIOS = ("20", "49", "59", "30", "43", "38", "102", "114", "58", "31")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--server", required=True)
    ap.add_argument("--attempt", type=int, default=1)
    ap.add_argument("--scenarios", nargs="+", default=SCENARIOS)
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--agent-timeout", type=int, default=TIMEOUT)
    ap.add_argument("--timeout", type=int, default=10800)
    ap.add_argument("--itbench", type=Path, default=Path.home() / "ITBench")
    ap.add_argument("--runs", type=Path, default=HERE / "runs")
    args = ap.parse_args()
    os.environ["PATH"] = f"{TOOLS}:{os.environ['PATH']}"

    runs = (args.runs / "itbench").resolve()
    for scenario in args.scenarios:
        run_dir = runs / scenario / f"attempt-{args.attempt}"
        if run_dir.exists():
            print(f"skip {scenario}: attempt {args.attempt} already exists", flush=True)
            continue
        run_dir.mkdir(parents=True)
        record = {"scenario": scenario, "attempt": args.attempt, "server": args.server, "status": "RUNNING"}
        write_json(run_dir / "record.json", record)
        print(f"start {scenario} attempt {args.attempt}", flush=True)
        start, log = time.monotonic(), ""
        try:
            proc = subprocess.run([sys.executable, str(EPISODE), "--scenario", scenario, "--model", args.model,
                                   "--agent-timeout", str(args.agent_timeout), "--itbench", str(args.itbench),
                                   "--out", str(run_dir / "result.json")],
                                  capture_output=True, text=True, timeout=args.timeout)
            record["status"], log = "COMPLETED", proc.stdout + proc.stderr
        except subprocess.TimeoutExpired:
            record["status"] = "TIMED_OUT"
        record["duration_seconds"] = round(time.monotonic() - start)
        write_json(run_dir / "record.json", record)
        write_gz(run_dir / "stdout.log.gz", scrub(log))
        result_path = run_dir / "result.json"
        result = json.loads(result_path.read_text()) if result_path.exists() else {}
        verdict = (result.get("verdicts") or {}).get("alert_absent") or {}
        print(f"done {scenario}: {record['status']} {result.get('status')} turns={(result.get('agent') or {}).get('turns')} "
              f"passed={verdict.get('passed')} relapsed={verdict.get('relapsed')} "
              f"healthy={(result.get('reference') or {}).get('healthy')}", flush=True)
        if record["status"] != "COMPLETED" or result.get("status") != "COMPLETED":
            print("stop: inspect the episode before continuing", flush=True)
            return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())

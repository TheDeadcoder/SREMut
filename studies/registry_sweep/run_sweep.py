"""Runs SREGym's own lifecycle validator, unmodified, over the planned problems on one server."""
import argparse
import csv
import gzip
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLAN = HERE / "plan.csv"
RUNS = HERE / "runs"
VALIDATOR = "tests/integration/validate_problem.py"

LINE_TIME = (
    re.compile(r"^\[\d{2}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}\] ?"),
    re.compile(r"^\[\d{2}:\d{2}:\d{2}\] ?"),
    re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:,\d+)? - "),
)
INLINE_TIME = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?")
DEPLOYED = re.compile(r"(LAST DEPLOYED:).*")


def scrub(text):
    out = []
    for line in text.splitlines():
        for pattern in LINE_TIME:
            line = pattern.sub("", line)
        line = DEPLOYED.sub(r"\1", INLINE_TIME.sub("<time>", line))
        out.append(line.rstrip())
    return "\n".join(out) + "\n"


def write_gz(path, text):
    with open(path, "wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
        gz.write(text.encode())


def write_json(path, data):
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def load_plan(path=PLAN):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def select(plan, server, pilot=False, ids=None):
    known = {row["problem_id"] for row in plan}
    if ids:
        missing = [pid for pid in ids if pid not in known]
        if missing:
            raise SystemExit(f"not in plan: {missing}")
        return list(ids)
    return [row["problem_id"] for row in plan
            if row["server"] == server and (not pilot or row["pilot"] == "yes")]


def mark_interrupted(runs, server):
    for record_path in runs.glob("*/attempt-*/record.json"):
        record = json.loads(record_path.read_text())
        if record["status"] == "RUNNING" and record["server"] == server:
            record["status"] = "INTERRUPTED"
            write_json(record_path, record)


def kubectl_json(*args):
    out = subprocess.run(["kubectl", *args, "-o", "json"], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)["items"]


def cluster_problem():
    for node in kubectl_json("get", "nodes"):
        ready = [c for c in node["status"]["conditions"] if c["type"] == "Ready"]
        if not ready or ready[0]["status"] != "True":
            return f"node {node['metadata']['name']} not Ready"
    for pod in kubectl_json("get", "pods", "-n", "kube-system"):
        if pod["status"].get("phase") not in ("Running", "Succeeded"):
            return f"kube-system pod {pod['metadata']['name']} is {pod['status'].get('phase')}"
    return None


def run_attempt(pid, attempt, server, inject_timeout, timeout, sregym, runs=RUNS):
    run_dir = runs / pid / f"attempt-{attempt}"
    run_dir.mkdir(parents=True)
    record = {"problem_id": pid, "attempt": attempt, "server": server,
              "inject_timeout": inject_timeout, "status": "RUNNING"}
    write_json(run_dir / "record.json", record)

    agent_logs = run_dir / "agent_logs"
    env = {**os.environ, "COLUMNS": "4000", "AGENT_LOGS_DIR": str(agent_logs)}
    cmd = [str(sregym / ".venv" / "bin" / "python"), VALIDATOR, "--problem", pid,
           "--summary", str(run_dir / "summary.md"), "--json-summary", str(run_dir / "summary.json"),
           "--inject-timeout", str(inject_timeout)]
    start = time.monotonic()
    with open(run_dir / "stdout.log", "w") as log:
        proc = subprocess.Popen(cmd, cwd=sregym, env=env, stdout=log, stderr=subprocess.STDOUT,
                                start_new_session=True)
        try:
            proc.wait(timeout=timeout)
            record["status"] = "COMPLETED"
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=60)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
            record["status"] = "TIMED_OUT"
    record["duration_seconds"] = round(time.monotonic() - start)
    record["exit_code"] = proc.returncode

    write_gz(run_dir / "stdout.log.gz", scrub((run_dir / "stdout.log").read_text(errors="replace")))
    (run_dir / "stdout.log").unlink()
    debug = "".join(p.read_text(errors="replace") for p in sorted(agent_logs.glob("*.log")))
    write_gz(run_dir / "debug.log.gz", scrub(debug))
    shutil.rmtree(agent_logs, ignore_errors=True)
    (run_dir / "summary.md").unlink(missing_ok=True)

    summary_path = run_dir / "summary.json"
    if summary_path.exists():
        summary = json.loads(summary_path.read_text())
        summary.pop("elapsed_seconds", None)
        for stage in summary["stages"].values():
            stage["detail"] = INLINE_TIME.sub("<time>", stage["detail"])
        write_json(summary_path, summary)
        record["stages"] = {key: stage["status"] for key, stage in summary["stages"].items()}
    try:
        record["namespaces_after"] = sorted(n["metadata"]["name"] for n in kubectl_json("get", "namespaces"))
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        record["namespaces_after"] = None
    write_json(run_dir / "record.json", record)
    return record


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--server", required=True, choices=("A", "B"))
    ap.add_argument("--attempt", type=int, default=1)
    ap.add_argument("--pilot", action="store_true", help="only the planned pilot problems")
    ap.add_argument("--ids", nargs="+", help="explicit problem ids, e.g. for repeats")
    ap.add_argument("--inject-timeout", type=int, default=300)
    ap.add_argument("--timeout", type=int, default=5400, help="seconds before an attempt is killed")
    ap.add_argument("--sregym", type=Path, default=Path.home() / "SREGym")
    args = ap.parse_args()

    RUNS.mkdir(exist_ok=True)
    mark_interrupted(RUNS, args.server)
    for pid in select(load_plan(), args.server, args.pilot, args.ids):
        if (RUNS / pid / f"attempt-{args.attempt}").exists():
            print(f"skip {pid}: attempt {args.attempt} already exists", flush=True)
            continue
        problem = cluster_problem()
        if problem:
            print(f"stop: {problem}", flush=True)
            return 2
        print(f"start {pid} attempt {args.attempt}", flush=True)
        record = run_attempt(pid, args.attempt, args.server, args.inject_timeout, args.timeout, args.sregym)
        print(f"done {pid}: {record['status']} {record.get('stages')}", flush=True)
        if record["status"] != "COMPLETED" or record.get("stages", {}).get("cleanup") != "pass":
            print("stop: inspect the cluster before continuing", flush=True)
            return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())

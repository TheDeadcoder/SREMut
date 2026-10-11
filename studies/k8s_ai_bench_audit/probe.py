"""One k8s-ai-bench audit episode: a fresh kind cluster, the task's own setup, one action standing in for the agent,
and the task's own verifier, run right after the action, as the harness runs it, and again once the task's
workloads have settled."""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "verifier_mutation"), str(HERE.parent / "registry_sweep")]

import operators  # noqa: E402
from actions import ACTIONS  # noqa: E402

CLUSTER = "k8s-ai-bench-eval"
TASK_TIMEOUT = 600
GENERIC = ("NULL", "RESTART", "SCALE0", "DELETE")
TAIL = 30


def run(cmd, cwd, timeout=TASK_TIMEOUT):
    start = time.monotonic()
    try:
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        code, output = proc.returncode, proc.stdout + proc.stderr
    except subprocess.TimeoutExpired:
        code, output = None, f"timed out after {timeout}s"
    return {"exit_code": code, "seconds": round(time.monotonic() - start),
            "tail": "\n".join(output.splitlines()[-TAIL:])}


def namespaces():
    proc = subprocess.run(["kubectl", "get", "namespaces", "-o", "name"], capture_output=True, text=True)
    return {line.split("/", 1)[1] for line in proc.stdout.split()}


def act(action, task_dir, added):
    if action == "NULL":
        return {"applied": True}
    if action in GENERIC:
        targets = {ns: (operators.apply(action, ns) or {}).get("targets") for ns in sorted(added)}
        targets = {ns: t for ns, t in targets.items() if t}
        return {"applied": bool(targets), "targets": targets}
    return {"applied": True, "script": run(["bash", "-c", ACTIONS[(task_dir.name, action)][1]], task_dir)}


def episode(bench, task, action):
    task_dir = bench / "tasks" / task
    out = {"task": task, "action": action}
    kubeconfig = Path.home() / f".kube/{CLUSTER}.yaml"
    kubeconfig.parent.mkdir(parents=True, exist_ok=True)
    try:
        run(["kind", "delete", "cluster", "--name", CLUSTER], bench)
        out["cluster"] = run(["kind", "create", "cluster", "--name", CLUSTER, "--wait", "5m",
                              "--kubeconfig", str(kubeconfig)], bench)
        if out["cluster"]["exit_code"] != 0:
            out["status"] = "CLUSTER_FAILED"
            return out
        os.environ["KUBECONFIG"] = str(kubeconfig)
        before = namespaces()
        out["setup"] = run([str(task_dir / "setup.sh")], task_dir)
        if out["setup"]["exit_code"] != 0:
            out["status"] = "SETUP_FAILED"
            return out
        added = namespaces() - before
        out["namespaces"] = sorted(added)
        out["action_result"] = act(action, task_dir, added)
        if out["action_result"]["applied"]:
            out["verify_immediate"] = run([str(task_dir / "verify.sh")], bench)
            out["settled"] = all([operators.settle(ns) for ns in sorted(namespaces() - before)])
            out["verify_settled"] = run([str(task_dir / "verify.sh")], bench)
        out["status"] = "COMPLETED"
    except Exception as exc:  # noqa: BLE001
        out["status"] = "ERROR"
        out["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        run(["kind", "delete", "cluster", "--name", CLUSTER], bench)
        kubeconfig.unlink(missing_ok=True)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--task", required=True)
    ap.add_argument("--action", required=True)
    ap.add_argument("--bench", type=Path, default=Path.home() / "k8s-ai-bench")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    result = episode(args.bench.resolve(), args.task, args.action)
    from run_sweep import scrub_value, write_json

    write_json(args.out, scrub_value(json.loads(json.dumps(result, default=str))))


if __name__ == "__main__":
    main()

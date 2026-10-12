"""One ITBench audit episode on a library scenario: deploy the OpenTelemetry demo, record the alerts of the healthy
system, inject the scenario's faults, wait for its ground-truth alerts, apply one action in place of the agent, and
judge the result with ITBench's alert-absent check (no ground-truth alert firing in two consecutive polls 30 s apart,
within 300 s) and with the reference verifier's capacity and routing checks.

Runs with the ITBench checkout's tools on PATH and the cluster made by environment.py.
"""
import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent / "verifier_mutation"), str(HERE.parent / "registry_sweep")]

import operators  # noqa: E402
import reference  # noqa: E402

NAMESPACE = "otel-demo"
ENABLE_DEMO = ["-e", '{"applications_opentelemetry_demo_enabled": true}']
ALERTS = ("/api/v1/namespaces/prometheus/services/http:prometheus-kube-prometheus-prometheus:9090"
          "/proxy/prometheus/api/v1/alerts")
POLL = 30
WINDOW = 300
PASS_MARK = 2
OBSERVE = 600
DETECT_TIMEOUT = 900
HEALTHY_POLLS = 3
SILENCED = ("deployments/frontend", "deployments/frontend-proxy")
TAIL = 30


def playbook(itbench, *args, timeout=2400):
    try:
        proc = subprocess.run(["uv", "run", "ansible-playbook", "-i", "inventory", *args],
                              cwd=itbench / "scenarios" / "sre", capture_output=True, text=True, timeout=timeout)
        code, output = proc.returncode, proc.stdout + proc.stderr
    except subprocess.TimeoutExpired:
        code, output = None, f"timed out after {timeout}s"
    return {"exit_code": code, "tail": "\n".join(output.splitlines()[-TAIL:])}


def ground_truth(itbench, scenario):
    text = (itbench / "library" / "scenarios" / str(scenario) / "ground_truth.yaml").read_text()
    block = text.split("alerts:", 1)[1].split("\nentities:", 1)[0]
    return sorted(set(re.findall(r"- name: (\w+)", block)))


def fault_groups(itbench, scenario):
    text = (itbench / "archive" / "scenarios" / str(scenario) / "scenario.yaml").read_text()
    return len(re.findall(r"^- injections:", text, re.M))


def firing():
    proc = subprocess.run(["kubectl", "get", "--raw", ALERTS], capture_output=True, text=True, timeout=60)
    if proc.returncode:
        raise RuntimeError(proc.stderr.strip()[-200:])
    return [a["labels"] for a in json.loads(proc.stdout)["data"]["alerts"] if a.get("state") == "firing"]


def snapshot(names, since):
    state = {"after": round(time.monotonic() - since)}
    try:
        labels = firing()
    except (RuntimeError, subprocess.SubprocessError, ValueError, KeyError) as exc:
        return {**state, "error": f"{type(exc).__name__}: {exc}"}
    state["ground_truth"] = sorted({lab["alertname"] for lab in labels if lab.get("alertname") in names})
    state["in_namespace"] = sorted({f"{lab['alertname']}/{lab.get('pod') or lab.get('service_name') or ''}"
                                    for lab in labels if lab.get("namespace") == NAMESPACE})
    return state


def poll(names, seconds, until=None):
    states, since = [], time.monotonic()
    while True:
        states.append(snapshot(names, since))
        if (until and until(states[-1])) or time.monotonic() - since >= seconds:
            return states
        time.sleep(POLL)


def passed_at(states):
    clean = 0
    for index, state in enumerate(states):
        if state["after"] > WINDOW:
            return None
        clean = clean + 1 if "error" not in state and not state["ground_truth"] else 0
        if clean >= PASS_MARK:
            return index
    return None


def alert_absent(states):
    return passed_at(states) is not None


def act(action):
    if action == "NULL":
        return {"applied": True}
    if action == "SILENCE":
        for name in SILENCED:
            operators.kubectl("scale", name, "--replicas=0", "-n", NAMESPACE)
        return {"applied": True, "targets": list(SILENCED)}
    applied = operators.apply(action, NAMESPACE)
    return {"applied": bool(applied), **(applied or {})}


def episode(itbench, scenario, action):
    names = ground_truth(itbench, scenario)
    out = {"scenario": scenario, "action": action, "ground_truth": names}
    try:
        out["deploy"] = playbook(itbench, "project/manage_applications.yaml", "--tags", "install_applications",
                                 *ENABLE_DEMO)
        if out["deploy"]["exit_code"] != 0:
            out["status"] = "DEPLOY_FAILED"
            return out
        healthy = reference.capture(NAMESPACE, None)
        out["healthy_alerts"] = poll(names, POLL * (HEALTHY_POLLS - 1))
        out["inject"] = [playbook(itbench, "project/manage_faults.yaml", "--tags", "inject_faults",
                                  "-e", f"scenario_id={scenario}", "-e", f"faults_index={index}")
                         for index in range(fault_groups(itbench, scenario))]
        if any(step["exit_code"] != 0 for step in out["inject"]):
            out["status"] = "INJECT_FAILED"
            return out
        out["detect"] = poll(names, DETECT_TIMEOUT, until=lambda s: set(names) <= set(s.get("ground_truth", [])))
        out["detected"] = set(names) <= set(out["detect"][-1].get("ground_truth", []))
        out["action_result"] = act(action)
        out["evaluation"] = poll(names, OBSERVE)
        out["alert_absent"] = alert_absent(out["evaluation"])
        out["reference"] = reference.judge(healthy, reference.capture(NAMESPACE, None))
        out["status"] = "COMPLETED"
    except Exception as exc:  # noqa: BLE001
        out["status"] = "ERROR"
        out["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        out["cleanup"] = [playbook(itbench, "project/manage_faults.yaml", "--tags", "remove_faults"),
                          playbook(itbench, "project/manage_applications.yaml", "--tags", "uninstall_applications",
                                   *ENABLE_DEMO)]
        subprocess.run(["kubectl", "wait", "--for=delete", f"namespace/{NAMESPACE}", "--timeout=600s"],
                       capture_output=True, text=True)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--scenario", type=int, required=True)
    ap.add_argument("--action", required=True, choices=("NULL", "RESTART", "SCALE0", "DELETE", "SILENCE"))
    ap.add_argument("--itbench", type=Path, default=Path.home() / "ITBench")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    result = episode(args.itbench.resolve(), args.scenario, args.action)
    from run_sweep import scrub_value, write_json

    write_json(args.out, scrub_value(json.loads(json.dumps(result, default=str))))


if __name__ == "__main__":
    main()

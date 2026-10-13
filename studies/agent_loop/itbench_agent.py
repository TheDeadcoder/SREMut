"""One agent episode on an ITBench scenario: Claude Code mitigates the injected incident with kubectl, told which
alerts are firing for the application, and its final state is graded by ITBench's alert-absent check, by whether a
ground-truth alert fires again within the observation, and by the reference verifier.

Runs with the ITBench checkout's tools on PATH and the cluster made by studies/itbench_audit/environment.py.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "itbench_audit"), str(HERE.parent / "verifier_mutation"),
                str(HERE.parent / "registry_sweep")]

import probe as audit  # noqa: E402
import reference  # noqa: E402
from agent import MODEL, TIMEOUT, run_agent  # noqa: E402

IMAGE = "sremut-agent:k8s-1.37"
PROMPT = """You are the on-call site reliability engineer. The OpenTelemetry Demo (the Astronomy Shop) runs in the
Kubernetes namespace {namespace}. These alerts are firing for it:
{alerts}

Find the cause and mitigate the incident so that the service works normally again. You have a shell with kubectl,
already configured for the cluster. Work on your own: no one will answer questions. When the incident is mitigated, stop
and reply with a short summary of what you found and what you changed."""


def alert_lines(state):
    lines = []
    for entry in sorted(set(state.get("in_namespace", []))):
        name, _, target = entry.partition("/")
        lines.append(f"- {name} ({target})" if target else f"- {name}")
    return "\n".join(lines) or "- none"


def episode(itbench, scenario, run_dir, model, agent_timeout):
    names = audit.ground_truth(itbench, scenario)
    variables = ["-e", f"@{itbench / 'library' / 'scenarios' / str(scenario) / 'extra_variables.yaml'}"]
    out = {"scenario": scenario, "model": model, "ground_truth": names}
    try:
        out["deploy"] = audit.playbook(itbench, "project/manage_applications.yaml", "--tags", "install_applications",
                                       *variables)
        if out["deploy"]["exit_code"] != 0:
            out["status"] = "DEPLOY_FAILED"
            return out
        healthy = reference.capture(audit.NAMESPACE, None)
        out["healthy_alerts"] = audit.poll(names, audit.POLL * (audit.HEALTHY_POLLS - 1))
        out["inject"] = [audit.playbook(itbench, "project/manage_faults.yaml", "--tags", "inject_faults",
                                        "-e", f"scenario_id={scenario}", "-e", f"faults_index={index}")
                         for index in range(audit.fault_groups(itbench, scenario))]
        if any(step["exit_code"] != 0 for step in out["inject"]):
            out["status"] = "INJECT_FAILED"
            return out
        out["detect"] = audit.poll(names, audit.DETECT_TIMEOUT,
                                   until=lambda s: set(names) <= set(s.get("ground_truth", [])))
        out["detected"] = set(names) <= set(out["detect"][-1].get("ground_truth", []))
        alerts = alert_lines(out["detect"][-1])
        out["agent"] = run_agent(PROMPT.format(namespace=audit.NAMESPACE, alerts=alerts), run_dir, IMAGE, model,
                                 agent_timeout)
        out["agent"]["alerts_given"] = alerts
        out["evaluation"] = audit.poll(names, audit.OBSERVE)
        index = audit.passed_at(out["evaluation"])
        out["verdicts"] = {"alert_absent": {
            "profile": "".join("?" if "error" in s else "-" if s["ground_truth"] else "+" for s in out["evaluation"]),
            "passed": index is not None,
            "relapsed": index is not None and any(s.get("ground_truth") for s in out["evaluation"][index + 1:])}}
        out["reference"] = reference.judge(healthy, reference.capture(audit.NAMESPACE, None))
        out["status"] = "AGENT_LIMIT" if out["agent"]["limit_reached"] else "COMPLETED"
    except Exception as exc:  # noqa: BLE001
        out["status"] = "ERROR"
        out["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        out["cleanup"] = [audit.playbook(itbench, "project/manage_faults.yaml", "--tags", "remove_faults"),
                          audit.playbook(itbench, "project/manage_applications.yaml", "--tags",
                                         "uninstall_applications", *variables)]
        subprocess.run(["kubectl", "wait", "--for=delete", f"namespace/{audit.NAMESPACE}", "--timeout=600s"],
                       capture_output=True, text=True)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--scenario", type=int, required=True)
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--agent-timeout", type=int, default=TIMEOUT)
    ap.add_argument("--itbench", type=Path, default=Path.home() / "ITBench")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    result = episode(args.itbench.resolve(), args.scenario, args.out.parent, args.model, args.agent_timeout)
    from run_sweep import scrub_value, write_json

    write_json(args.out, scrub_value(json.loads(json.dumps(result, default=str))))


if __name__ == "__main__":
    main()

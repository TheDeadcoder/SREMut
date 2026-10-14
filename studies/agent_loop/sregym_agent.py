"""One agent episode on a SREGym problem: SREGym's Conductor deploys the application and, as by default, captures the
mitigation oracle's baseline right before it injects the fault. Claude Code mitigates the fault with kubectl, given
the mitigation part of the task text of SREGym's Claude Code client. Its final state is graded by the problem's
mitigation oracle, first evaluated where the agent would submit, and by the reference verifier.

Runs under the SREGym virtual environment with the SREGym checkout as working directory.
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "alert_baseline"), str(HERE.parent / "verifier_mutation"),
                str(HERE.parent / "registry_sweep")]

import operators  # noqa: E402
import reference  # noqa: E402
from agent import MODEL, TIMEOUT, run_agent  # noqa: E402
from mutation_probe import VERDICT_WINDOW, footprint_left  # noqa: E402
from probe import alert_oracles, firing, name  # noqa: E402
from triage_probe import poll, profile, snapshot  # noqa: E402

IMAGE = "sremut-agent:k8s-1.32"
FLAGS = ("flagd-config", "demo.flagd.json")
PROMPT = """You are an SRE agent tasked with diagnosing and fixing issues in a Kubernetes application.

Application: {app_name}
{namespace_block}

{descriptions}

CRITICAL: You are running in an AUTOMATED environment. Work autonomously and make all decisions yourself. DO NOT ask for user confirmation or approval. Proceed with the best solution based on your analysis.

TASK: MITIGATION
- Identify the root cause of the issue
- Implement a fix to resolve the problem
- Your mitigation is evaluated on whether the application is healthy after your changes
- Your fix is also evaluated on whether it addresses the root cause, not just the symptoms

Important:
- You have access to kubectl commands to inspect and modify resources in namespace(s): {namespaces}

You have a shell with kubectl, already configured for the cluster. When the fix is applied, stop and reply with a short
summary of what you found and what you changed."""


def instruction(app):
    namespaces = getattr(app, "namespaces", None) or [app.namespace]
    block = (f"Namespaces: {', '.join(namespaces)}\n(This scenario spans multiple namespaces; investigate all of them.)"
             if len(namespaces) > 1 else f"Namespace: {namespaces[0]}")
    return PROMPT.format(app_name=app.app_name, namespace_block=block, descriptions=str(app.description),
                         namespaces=", ".join(namespaces))


def judged_namespaces(problem):
    named = re.findall(r"namespace=([\w.-]+)", str(problem.root_cause or ""))
    return [problem.namespace, *sorted(set(named) - {problem.namespace})]


def flags(namespace):
    proc = subprocess.run(["kubectl", "get", "configmap", FLAGS[0], "-n", namespace, "-o", "json"],
                          capture_output=True, text=True)
    try:
        config = json.loads(json.loads(proc.stdout)["data"][FLAGS[1]])
        return {flag: spec.get("defaultVariant") for flag, spec in config["flags"].items()}
    except (ValueError, KeyError, TypeError, AttributeError):
        return {}


def capture(namespaces, wrk):
    return {ns: reference.capture(ns, wrk if ns == namespaces[0] else None) for ns in namespaces}


def judge(healthy, namespaces, wrk, healthy_flags):
    current = capture(namespaces, wrk)
    by_namespace = {ns: reference.judge(healthy[ns], current[ns]) for ns in namespaces}
    now = flags(namespaces[0])
    changed = sorted(flag for flag, variant in healthy_flags.items() if now.get(flag) != variant)
    return {"by_namespace": by_namespace, "flags_changed": changed,
            "healthy": all(j["healthy"] for j in by_namespace.values()) and not changed}


def alerts_firing(namespace):
    try:
        return sorted({name(labels) for labels in firing(namespace)})
    except (subprocess.SubprocessError, ValueError) as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def run(problem_id, run_dir, model, agent_timeout):
    from sregym.conductor.conductor import Conductor, ConductorConfig

    out = {"problem_id": problem_id, "model": model}
    conductor, injected = None, False
    try:
        conductor = Conductor(config=ConductorConfig(deploy_loki=False))
        problem = conductor.problems.get_problem_instance(problem_id)
        conductor.problem_id, conductor.problem, conductor.app = problem_id, problem, problem.app
        oracle, namespace = problem.mitigation_oracle, problem.namespace
        namespaces, alerts = judged_namespaces(problem), alert_oracles(oracle)
        out["judged_namespaces"] = namespaces

        conductor.dependency_check(["kubectl", "helm", "docker"])
        conductor.fix_kubernetes()
        conductor.undeploy_app()
        conductor.deploy_app()
        wrk = getattr(problem.app, "wrk", None)
        healthy_state, healthy_flags = snapshot(namespace), flags(namespace)
        healthy = capture(namespaces, wrk)
        out["healthy"] = {"traffic": healthy[namespace]["traffic"], "flags": healthy_flags}

        oracle.capture_baseline()
        injected = True
        problem.inject_fault()
        faulted_state = snapshot(namespace)
        if alerts:
            out["alerts"] = {"baseline": sorted({name(dict(k)) for a in alerts for k in a._baseline_instances or ()}),
                             "agent_start": alerts_firing(namespace)}
        out["agent"] = run_agent(instruction(problem.app), run_dir, IMAGE, model, agent_timeout)
        window = poll(oracle, VERDICT_WINDOW)
        out["verdicts"] = {"mitigation_oracle": {"oracle_window": window, "profile": profile(window),
                                                 "passed": window[0].get("success")}}
        if alerts:
            out["alerts"]["after_verdict"] = alerts_firing(namespace)
        out["settled"] = {ns: operators.settle(ns) for ns in namespaces}
        out["reference"] = judge(healthy, namespaces, wrk, healthy_flags)
        out["footprint_left"] = footprint_left(healthy_state, faulted_state, snapshot(namespace))
        out["status"] = "AGENT_LIMIT" if out["agent"]["limit_reached"] else "COMPLETED"
    except Exception as exc:  # noqa: BLE001
        out["status"] = "ERROR"
        out["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        actions = []
        if conductor is not None and conductor.problem is not None:
            if injected:
                actions.append(("recover fault", conductor.problem.recover_fault))
            actions += [("remove application", conductor.problem.app.cleanup),
                        ("wait for namespace deletion",
                         lambda: conductor.kubectl.wait_for_namespace_deletion(conductor.problem.namespace))]
        if conductor is not None:
            actions += [("stop MCP port forward", conductor.mcp_server.stop_port_forward),
                        ("stop Kubernetes proxy", conductor.stop_k8s_proxy)]
        errors = []
        for label, action in actions:
            try:
                action()
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{label}: {type(exc).__name__}: {exc}")
        out["cleanup"] = "fail" if errors else "pass"
        out["cleanup_errors"] = errors
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--problem", required=True)
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--agent-timeout", type=int, default=TIMEOUT)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    from logger import init_logger

    init_logger()
    result = run(args.problem, args.out.parent, args.model, args.agent_timeout)
    from run_sweep import scrub_value, write_json

    write_json(args.out, scrub_value(json.loads(json.dumps(result, default=str))))


if __name__ == "__main__":
    main()

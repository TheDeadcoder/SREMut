"""One alert-baseline episode: the problem's mitigation oracle judges the healthy system after a steady-state wait,
first with its alert baseline captured right after deploy, as SREGym does by default, then with the baseline
captured at that point; SREGym's recovery is polled with the later baseline and then judged once with the earlier
one. Every poll records the alerts firing in the namespace and which of them each baseline leaves in.

Runs under the SREGym virtual environment with the SREGym checkout as working directory.
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent / "registry_sweep")]

from triage_probe import evaluate, profile  # noqa: E402

STEADY_SECONDS = 300
DETECT_TIMEOUT = 600
RECOVER_WINDOW = 600
POLL = 30
ALERTS_URL = "http://localhost:9090/api/v1/alerts"


def alert_oracles(oracle):
    from sregym.conductor.oracles.alert_oracle import AlertOracle

    if isinstance(oracle, AlertOracle):
        return [oracle]
    return [found for sub in getattr(oracle, "oracles", {}).values() for found in alert_oracles(sub)]


def key(labels):
    return tuple(sorted(labels.items()))


def name(labels):
    return f"{labels.get('alertname', '')}/{labels.get('pod') or labels.get('service_name') or ''}"


def firing(namespace):
    raw = subprocess.check_output(["kubectl", "exec", "-n", "observe", "deploy/prometheus-server", "-c",
                                   "prometheus-server", "--", "wget", "-qO-", ALERTS_URL], text=True, timeout=15)
    return [alert["labels"] for alert in json.loads(raw).get("data", {}).get("alerts", [])
            if alert.get("state") == "firing" and alert.get("labels", {}).get("namespace") == namespace]


def alert_state(namespace, start, baselines, excluded):
    state = {"after_deploy": round(time.monotonic() - start)}
    try:
        labels = firing(namespace)
    except (subprocess.SubprocessError, ValueError) as exc:
        return {**state, "error": f"{type(exc).__name__}: {exc}"}
    state["firing"] = sorted({name(lab) for lab in labels})
    counted = [lab for lab in labels if lab.get("alertname") not in excluded]
    for label, baseline in baselines.items():
        state[f"outside_{label}"] = sorted({name(lab) for lab in counted if key(lab) not in baseline})
    return state


def capture(oracle, alerts, start):
    oracle.capture_baseline()
    keys = set().union(*((alert._baseline_instances or set()) for alert in alerts))
    return keys, {"after_deploy": round(time.monotonic() - start), "baseline": sorted({name(dict(k)) for k in keys})}


def judge_with(oracle, alerts, baseline, start):
    for alert in alerts:
        alert._baseline_instances = set(baseline)
    return {"after_deploy": round(time.monotonic() - start), "verdict": evaluate(oracle)}


def watch(namespace, seconds, start, baselines, excluded):
    timeline, deadline = [], time.monotonic() + seconds
    while time.monotonic() < deadline:
        timeline.append(alert_state(namespace, start, baselines, excluded))
        time.sleep(POLL)
    return timeline


def poll_until(oracle, namespace, timeout, stop_on, start, baselines, excluded):
    verdicts, states, deadline = [], [], time.monotonic() + timeout
    while True:
        verdicts.append(evaluate(oracle))
        states.append(alert_state(namespace, start, baselines, excluded))
        if (stop_on is not None and verdicts[-1].get("success") is stop_on) or time.monotonic() >= deadline:
            return {"profile": profile(verdicts), "verdicts": verdicts, "alerts": states}
        time.sleep(POLL)


def run(problem_id):
    from sregym.conductor.conductor import Conductor, ConductorConfig

    out = {"problem_id": problem_id}
    conductor, injected, recovered = None, False, False
    try:
        conductor = Conductor(config=ConductorConfig(deploy_loki=False))
        problem = conductor.problems.get_problem_instance(problem_id)
        conductor.problem_id, conductor.problem, conductor.app = problem_id, problem, problem.app
        oracle, namespace = problem.mitigation_oracle, problem.namespace
        alerts = alert_oracles(oracle)
        excluded = set().union(*(alert.exclude_alerts for alert in alerts))
        out["alert_oracles"] = len(alerts)

        conductor.dependency_check(["kubectl", "helm", "docker"])
        conductor.fix_kubernetes()
        conductor.undeploy_app()
        conductor.deploy_app()
        start = time.monotonic()
        immediate, out["immediate"] = capture(oracle, alerts, start)
        out["steady"] = watch(namespace, STEADY_SECONDS, start, {"immediate": immediate}, excluded)
        out["immediate"]["healthy"] = judge_with(oracle, alerts, immediate, start)
        delayed, out["delayed"] = capture(oracle, alerts, start)
        out["delayed"]["healthy"] = judge_with(oracle, alerts, delayed, start)
        baselines = {"immediate": immediate, "delayed": delayed}

        injected = True
        problem.inject_fault()
        out["detect"] = poll_until(oracle, namespace, DETECT_TIMEOUT, False, start, baselines, excluded)
        problem.recover_fault()
        recovered = True
        out["delayed"]["recovery"] = poll_until(oracle, namespace, RECOVER_WINDOW, None, start, baselines, excluded)
        out["immediate"]["recovery"] = judge_with(oracle, alerts, immediate, start)
        out["status"] = "COMPLETED"
    except Exception as exc:  # noqa: BLE001
        out["status"] = "ERROR"
        out["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        out.update(clean_up(conductor, injected, recovered))
    return out


def clean_up(conductor, injected, recovered):
    actions = []
    if conductor is not None and conductor.problem is not None:
        if injected and not recovered:
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
    return {"cleanup": "fail" if errors else "pass", "cleanup_errors": errors}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--problem", required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    from logger import init_logger

    init_logger()
    result = run(args.problem)
    from run_sweep import scrub_value, write_json

    write_json(args.out, scrub_value(json.loads(json.dumps(result, default=str))))


if __name__ == "__main__":
    main()

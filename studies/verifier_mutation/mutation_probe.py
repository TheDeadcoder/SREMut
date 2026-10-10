"""One verifier-mutation episode: SREGym injects the fault, an operational mutant stands in for the agent's
repair, and the result is graded by the problem's own mitigation oracle and by the reference verifier.

Runs under the SREGym virtual environment with the SREGym checkout as working directory.
"""
import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "registry_sweep")]

import operators  # noqa: E402
import reference  # noqa: E402
from triage_probe import diff, evaluate, poll, profile, snapshot  # noqa: E402

NOOP_WINDOW = 300
DETECT_TIMEOUT = 300
VERDICT_WINDOW = 90
RECOVER_TIMEOUT = 600
NOISY = ("pods/", "endpointslices/")


def changed_keys(before, after):
    return sorted(k for part in diff(before, after).values() for k in part if not k.startswith(NOISY))


def footprint_left(healthy, faulted, current):
    return [k for k in changed_keys(healthy, faulted) if current.get(k) != healthy.get(k)]


def grade(oracle, namespace, wrk, healthy, healthy_state, faulted_state):
    window = poll(oracle, VERDICT_WINDOW)
    return {"verdict": window[0].get("success"), "oracle_window": window, "profile": profile(window),
            "reference": reference.judge(healthy, reference.capture(namespace, wrk)),
            "footprint_left": footprint_left(healthy_state, faulted_state, snapshot(namespace))}


def run(problem_id, operator):
    from sregym.conductor.conductor import Conductor, ConductorConfig

    out = {"problem_id": problem_id, "operator": operator}
    conductor, injected, recovered = None, False, False
    try:
        conductor = Conductor(config=ConductorConfig(deploy_loki=False))
        problem = conductor.problems.get_problem_instance(problem_id)
        conductor.problem_id, conductor.problem, conductor.app = problem_id, problem, problem.app
        oracle, namespace = problem.mitigation_oracle, problem.namespace

        conductor.dependency_check(["kubectl", "helm", "docker"])
        conductor.fix_kubernetes()
        conductor.undeploy_app()
        conductor.deploy_app()
        oracle.capture_baseline()
        wrk = getattr(problem.app, "wrk", None)
        healthy_state = snapshot(namespace)
        healthy = reference.capture(namespace, wrk)
        out["healthy"] = {"oracle": evaluate(oracle), "traffic": healthy["traffic"]}

        injected = True
        problem.inject_fault()
        window = (poll(oracle, NOOP_WINDOW) if operator == "RESTART"
                  else poll(oracle, DETECT_TIMEOUT, stop_on=False))
        out["noop" if operator == "RESTART" else "detect"] = {"oracle_window": window, "profile": profile(window)}
        faulted_state = snapshot(namespace)
        out["unhealthy_after_fault"] = operators.unhealthy(operators.workloads(namespace))

        if operator == "COLLAT":
            problem.recover_fault()
            until_pass = poll(oracle, RECOVER_TIMEOUT, stop_on=True)
            recovered = until_pass[-1].get("success") is True
            operators.settle(namespace)
            out["recovered"] = {"oracle_until_pass": until_pass, "profile": profile(until_pass),
                                "reference": reference.judge(healthy, reference.capture(namespace, wrk))}
        applied = None
        if operator != "COLLAT" or recovered:
            applied = operators.apply(operator, namespace, set(changed_keys(healthy_state, faulted_state)))
        out["applied"] = applied
        if applied:
            out["settled"] = operators.settle(namespace)
            out["mutant"] = grade(oracle, namespace, wrk, healthy, healthy_state, faulted_state)
            if operator == "COLLAT":
                operators.undo_collateral(namespace, applied)
        out["status"] = "COMPLETED"
    except Exception as exc:  # noqa: BLE001
        out["status"] = "ERROR"
        out["error"] = f"{type(exc).__name__}: {exc}"
    finally:
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
        for name, action in actions:
            try:
                action()
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{name}: {type(exc).__name__}: {exc}")
        out["cleanup"] = "fail" if errors else "pass"
        out["cleanup_errors"] = errors
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--problem", required=True)
    ap.add_argument("--operator", required=True, choices=operators.OPERATORS)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    from logger import init_logger

    init_logger()
    result = run(args.problem, args.operator)
    from run_sweep import scrub_value, write_json

    write_json(args.out, scrub_value(json.loads(json.dumps(result, default=str))))


if __name__ == "__main__":
    main()

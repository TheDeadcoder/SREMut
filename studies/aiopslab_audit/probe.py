"""One audit episode on an AIOpsLab mitigation problem: the lifecycle of the SREGym verifier-mutation study,
graded by the problem's own eval() and by the same reference verifier.

Runs under AIOpsLab's virtual environment with the AIOpsLab checkout as working directory.
"""
import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

os.environ["AIOPSLAB_MITIGATION_SETTLE_SECONDS"] = "0"

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent / "verifier_mutation"), str(HERE.parent / "registry_sweep")]

import operators  # noqa: E402
import reference  # noqa: E402
from mutation_probe import DETECT_TIMEOUT, NOOP_WINDOW, RECOVER_TIMEOUT, changed_keys, grade  # noqa: E402
from triage_probe import evaluate, poll, profile, snapshot  # noqa: E402

OPENEBS = "https://openebs.github.io/charts/openebs-operator.yaml"
REQUESTS = re.compile(r"(\d+) requests in")
NON2XX = re.compile(r"Non-2xx or 3xx responses:\s*(\d+)")
SOCKET = re.compile(r"Socket errors: connect (\d+), read (\d+), write (\d+), timeout (\d+)")


class Round:
    def __init__(self, number, ok):
        self.number, self.ok = number, ok


def parse_wrk(log):
    requests = REQUESTS.search(log)
    non2xx = NON2XX.search(log)
    sockets = SOCKET.search(log)
    failed = (int(non2xx.group(1)) if non2xx else 0) + (sum(map(int, sockets.groups())) if sockets else 0)
    return Round(int(requests.group(1)) if requests else 0, bool(requests) and failed == 0)


class EvalOracle:
    """The problem's own eval(), in the shape the audit polls."""

    def __init__(self, problem):
        self.problem = problem

    def evaluate(self):
        return {"success": self.problem.eval(None, [], 0).get("success")}


class OneShotWorkload:
    """AIOpsLab's own wrk2 job, run to completion on demand, in the shape the reference verifier reads."""

    def __init__(self, problem):
        self.problem, self.rounds = problem, []

    def collect(self, number=1):
        self.problem.start_workload()
        log = subprocess.run(["kubectl", "logs", "job/wrk2-job", "-n", "default"],
                             capture_output=True, text=True).stdout
        self.rounds = [parse_wrk(log)] if log else []
        return self.rounds

    def recent_entries(self, duration=0):
        return self.rounds


def setup_infrastructure(kubectl, prometheus):
    kubectl.exec_command(f"kubectl apply -f {OPENEBS}")
    default = '{"metadata": {"annotations": {"storageclass.kubernetes.io/is-default-class": "true"}}}'
    kubectl.exec_command(f"kubectl patch storageclass openebs-hostpath -p '{default}'")
    kubectl.wait_for_ready("openebs")
    prometheus().deploy()


def run(problem_id, operator):
    from aiopslab.orchestrator.problems.registry import ProblemRegistry
    from aiopslab.service.kubectl import KubeCtl
    from aiopslab.service.telemetry.prometheus import Prometheus

    traffic = reference.traffic
    reference.traffic = lambda wrk: traffic(wrk, seconds=0)
    out = {"problem_id": problem_id, "operator": operator}
    problem, injected, recovered = None, False, False
    try:
        registry = ProblemRegistry()
        problem = registry.get_problem_instance(problem_id)
        if registry.get_problem_deployment(problem_id) != "docker":
            setup_infrastructure(KubeCtl(), Prometheus)
        problem.app.delete()
        problem.app.deploy()
        oracle, namespace, wrk = EvalOracle(problem), problem.namespace, OneShotWorkload(problem)
        healthy_state = snapshot(namespace)
        healthy = reference.capture(namespace, wrk)
        out["healthy"] = {"oracle": evaluate(oracle), "traffic": healthy["traffic"]}

        injected = True
        problem.inject_fault()
        problem.start_workload()
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
        if problem is not None:
            if injected and not recovered:
                actions.append(("recover fault", problem.recover_fault))
            actions.append(("remove application", problem.app.cleanup))
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
    result = run(args.problem, args.operator)
    from run_sweep import scrub_value, write_json

    write_json(args.out, scrub_value(json.loads(json.dumps(result, default=str))))


if __name__ == "__main__":
    main()

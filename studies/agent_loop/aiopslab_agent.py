"""One agent episode on an AIOpsLab mitigation problem: Claude Code mitigates the injected fault with kubectl, and its
final state is graded by the problem's own eval(), by eval() with the proposed fix (workload_check.patch, in a second
checkout), and by the reference verifier.

Runs under AIOpsLab's virtual environment with the AIOpsLab checkout as working directory.
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "aiopslab_audit"), str(HERE.parent / "verifier_mutation"),
                str(HERE.parent / "registry_sweep")]

import probe as audit  # noqa: E402
import operators  # noqa: E402
import reference  # noqa: E402
from agent import MODEL, TIMEOUT, run_agent  # noqa: E402
from mutation_probe import footprint_left  # noqa: E402
from triage_probe import evaluate, profile, snapshot  # noqa: E402

FIXED = Path.home() / "AIOpsLab-fix"
IMAGE = "sremut-agent:k8s-1.32"
WINDOW = 120
POLL = 15
INSTRUCTIONS = """

You have a shell with kubectl, already configured for the cluster. Work on your own: no one will answer questions. When
the anomaly is mitigated, stop and reply with a short summary of what you found and what you changed."""
FIXED_EVAL = """import json, sys
from aiopslab.orchestrator.problems.registry import ProblemRegistry
problem = ProblemRegistry().get_problem_instance(sys.argv[1])
problem.deployed_workloads = json.load(open(sys.argv[2]))
print(json.dumps({"success": problem.eval(None, [], 0).get("success")}))"""


def deployed_workloads(namespace):
    proc = subprocess.run(["kubectl", "get", "deployments,statefulsets", "-n", namespace, "-o", "json"],
                          capture_output=True, text=True, check=True)
    return {f"{item['kind']} {item['metadata']['name']}": item["spec"].get("replicas") or 0
            for item in json.loads(proc.stdout)["items"] if item["spec"].get("replicas")}


class FixedEvalOracle:
    """eval() of the same problem from the checkout with the proposed fix, given the workloads recorded after deploy."""

    def __init__(self, problem_id, record):
        self.problem_id, self.record = problem_id, record

    def evaluate(self):
        proc = subprocess.run([sys.executable, "-c", FIXED_EVAL, self.problem_id, str(self.record)], cwd=FIXED,
                              env={**os.environ, "PYTHONPATH": str(FIXED)}, capture_output=True, text=True,
                              timeout=300)
        lines = proc.stdout.strip().splitlines()
        if proc.returncode or not lines:
            raise RuntimeError((proc.stderr or proc.stdout).strip()[-300:])
        return json.loads(lines[-1])


def window(oracles):
    polls, deadline = {name: [] for name in oracles}, time.monotonic() + WINDOW
    while True:
        for name, oracle in oracles.items():
            polls[name].append(evaluate(oracle))
        if time.monotonic() >= deadline:
            return {name: {"oracle_window": results, "profile": profile(results),
                           "passed": any(r.get("success") is True for r in results)} for name, results in polls.items()}
        time.sleep(POLL)


def run(problem_id, run_dir, model, agent_timeout):
    from aiopslab.orchestrator.problems.registry import ProblemRegistry
    from aiopslab.service.kubectl import KubeCtl
    from aiopslab.service.telemetry.prometheus import Prometheus

    traffic = reference.traffic
    reference.traffic = lambda wrk: traffic(wrk, seconds=0)
    out = {"problem_id": problem_id, "model": model}
    problem, injected = None, False
    try:
        registry = ProblemRegistry()
        problem = registry.get_problem_instance(problem_id)
        if registry.get_problem_deployment(problem_id) != "docker":
            audit.setup_infrastructure(KubeCtl(), Prometheus)
        problem.app.delete()
        problem.app.deploy()
        namespace, wrk = problem.namespace, audit.OneShotWorkload(problem)
        record = run_dir / "deployed_workloads.json"
        record.write_text(json.dumps(deployed_workloads(namespace), indent=2, sort_keys=True) + "\n")
        oracles = {"original": audit.EvalOracle(problem), "fixed": FixedEvalOracle(problem_id, record)}
        healthy_state = snapshot(namespace)
        healthy = reference.capture(namespace, wrk)
        out["healthy"] = {"traffic": healthy["traffic"], **{n: evaluate(o) for n, o in oracles.items()}}

        injected = True
        problem.inject_fault()
        problem.start_workload()
        faulted_state = snapshot(namespace)
        out["agent"] = run_agent(problem.get_task_description() + INSTRUCTIONS, run_dir, IMAGE, model,
                                 agent_timeout)
        out["settled"] = operators.settle(namespace)
        out["verdicts"] = window(oracles)
        final = reference.capture(namespace, wrk)
        out["reference"] = reference.judge(healthy, final)
        out["captures"] = {"healthy": healthy, "final": final}
        out["footprint_left"] = footprint_left(healthy_state, faulted_state, snapshot(namespace))
        out["status"] = "AGENT_LIMIT" if out["agent"]["limit_reached"] else "COMPLETED"
    except Exception as exc:  # noqa: BLE001
        out["status"] = "ERROR"
        out["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        actions = []
        if problem is not None:
            if injected:
                actions.append(("recover fault", problem.recover_fault))
            actions.append(("remove application", problem.app.cleanup))
        errors = []
        for name, action in actions:
            try:
                action()
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{name}: {type(exc).__name__}: {exc}")
        out["cleanup_errors"] = errors
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--problem", required=True)
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--agent-timeout", type=int, default=TIMEOUT)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    result = run(args.problem, args.out.parent, args.model, args.agent_timeout)
    from run_sweep import scrub_value, write_json

    write_json(args.out, scrub_value(json.loads(json.dumps(result, default=str))))


if __name__ == "__main__":
    main()

"""One repair-ladder run, graded through SREGym's Conductor and measured against the contract.

Runs under the patched SREGym checkout's virtual environment, with that checkout as working directory.
"""
import argparse
import asyncio
import json
import re
import time
from pathlib import Path

import contract
import mutants

PROBLEM_ID = "missing_service_social_network"
EPISODE_SECONDS = 60
REPLACEMENT_STATES = {"M5", "M4", "C1"}
INLINE_TIME = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?")


class NotActive(RuntimeError):
    pass


def evaluate(oracle):
    try:
        return oracle.evaluate() or {}
    except Exception as exc:  # noqa: BLE001
        return {"success": None, "error": f"{type(exc).__name__}: {exc}"}


def workload_oracle(problem):
    from sregym.conductor.oracles.workload import WorkloadOracle

    return evaluate(WorkloadOracle(problem, wrk_manager=problem.app.wrk))


def measure(problem, floors, replacement):
    contract.wait_no_probe_pods()
    oracle = evaluate(problem.mitigation_oracle)
    contract.wait_no_probe_pods()
    return {"oracle": oracle, "workload_oracle": workload_oracle(problem),
            "contract": contract.evaluate(problem.app.wrk, floors, replacement)}


async def run(state):
    from sregym.conductor.conductor import Conductor, ConductorConfig

    record = {"state": state, "description": mutants.DESCRIPTION[state], "problem_id": PROBLEM_ID}
    conductor = Conductor(config=ConductorConfig(deploy_loki=False, stages=("mitigation",)))
    conductor.problem_id = PROBLEM_ID
    finish, inject, deferred, captured = conductor._finish_problem, conductor._inject_fault, [], {}

    def measured_inject():
        problem = conductor.problem
        captured["service"] = contract.read("get", "service", contract.SERVICE, "-n", contract.NAMESPACE)
        captured["floors"] = {d["metadata"]["name"]: d["spec"].get("replicas", 1)
                              for d in contract.items("deployments")}
        problem.mitigation_oracle.capture_baseline()
        record["healthy"] = measure(problem, captured["floors"], replacement=False)
        inject()
        captured["injected"] = time.monotonic()

    conductor._finish_problem = lambda generation=None: deferred.append(generation)
    conductor._inject_fault = measured_inject
    try:
        await conductor.start_problem()
        problem = conductor.problem
        record["mutant"] = mutants.apply(state, captured["service"])
        record["activation"] = mutants.wait_active(state, captured["service"])
        if not record["activation"]["active"]:
            raise NotActive(state)
        hold = captured["injected"] + EPISODE_SECONDS - time.monotonic()
        record["episode_seconds"] = round(max(hold, 0) + time.monotonic() - captured["injected"])
        if hold > 0:
            time.sleep(hold)
        contract.wait_no_probe_pods()
        await conductor.submit("")
        if conductor._submit_future is not None:
            await asyncio.wrap_future(conductor._submit_future)
        record["faulted"] = {"conductor": conductor.results.get("Mitigation")}
        contract.wait_no_probe_pods()
        record["faulted"]["workload_oracle"] = workload_oracle(problem)
        record["faulted"]["contract"] = contract.evaluate(
            problem.app.wrk, captured["floors"], replacement=state in REPLACEMENT_STATES)
        problem.recover_fault()
        record["restored"] = measure(problem, captured["floors"], replacement=True)
        record["status"] = "COMPLETED"
    except NotActive:
        record["status"] = "ACTIVATION_FAILURE"
    except contract.InfrastructureError as exc:
        record["status"] = "INFRASTRUCTURE_FAILURE"
        record["error"] = str(exc)
    except Exception as exc:  # noqa: BLE001
        record["status"] = "ERROR"
        record["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        try:
            finish(deferred[0] if deferred else None)
            record["teardown"] = "pass"
        except Exception as exc:  # noqa: BLE001
            record["teardown"] = f"fail: {type(exc).__name__}: {exc}"
    return record


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--state", required=True, choices=mutants.STATES)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    from logger import init_logger

    init_logger()
    record = asyncio.run(run(args.state))
    args.out.write_text(INLINE_TIME.sub("<time>", json.dumps(record, indent=2, sort_keys=True, default=str)) + "\n")


if __name__ == "__main__":
    main()

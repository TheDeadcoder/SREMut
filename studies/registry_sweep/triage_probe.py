"""Triage probe: SREGym's validator lifecycle without stopping at the first verdict, plus state evidence.

Runs under the SREGym virtual environment with the SREGym checkout as working directory.
"""
import argparse
import hashlib
import json
import subprocess
import time
from pathlib import Path

WINDOW = 300
RECOVER_TIMEOUT = 600
POLL = 15
KINDS = ("deployments", "statefulsets", "daemonsets", "services", "endpointslices", "configmaps",
         "persistentvolumeclaims", "networkpolicies", "ingresses", "jobs", "cronjobs")
NOISE = ("uid", "resourceVersion", "generation", "managedFields", "creationTimestamp", "selfLink")


def evaluate(oracle):
    try:
        return oracle.evaluate() or {}
    except Exception as exc:  # noqa: BLE001
        return {"success": None, "error": f"{type(exc).__name__}: {exc}"}


def poll(oracle, seconds, stop_on=None):
    results, deadline = [], time.monotonic() + seconds
    while True:
        result = evaluate(oracle)
        results.append(result)
        if (stop_on is not None and result.get("success") is stop_on) or time.monotonic() >= deadline:
            return results
        time.sleep(POLL)


def workload(problem):
    from sregym.conductor.oracles.workload import WorkloadOracle

    wrk = getattr(problem.app, "wrk", None)
    return None if wrk is None else evaluate(WorkloadOracle(problem, wrk_manager=wrk))


def items(*args):
    proc = subprocess.run(["kubectl", *args, "-o", "json"], capture_output=True, text=True)
    return json.loads(proc.stdout)["items"] if proc.returncode == 0 else []


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:16]


def snapshot(namespace):
    state = {}
    for kind in KINDS:
        for obj in items("get", kind, "-n", namespace):
            meta = {k: v for k, v in obj["metadata"].items() if k not in NOISE}
            meta.get("annotations", {}).pop("deployment.kubernetes.io/revision", None)
            body = {k: v for k, v in obj.items() if k not in ("metadata", "status")}
            state[f"{kind}/{obj['metadata']['name']}"] = digest([meta, body])
    pods = {}
    for pod in items("get", "pods", "-n", namespace):
        owners = pod["metadata"].get("ownerReferences") or [{"kind": "Pod", "name": pod["metadata"]["name"]}]
        owner = f"pods/{owners[0]['kind']}/{owners[0]['name']}"
        ready = all(c.get("ready") for c in pod["status"].get("containerStatuses") or [{}])
        pods.setdefault(owner, []).append(f"{pod['status'].get('phase')}:{ready}")
    state.update({owner: digest(sorted(v)) for owner, v in pods.items()})
    for node in items("get", "nodes"):
        conditions = sorted((c["type"], c["status"]) for c in node["status"].get("conditions", []))
        state[f"nodes/{node['metadata']['name']}"] = digest(
            [node["spec"].get("taints"), node["spec"].get("unschedulable"), conditions])
    return state


def diff(before, after):
    return {
        "added": sorted(set(after) - set(before)),
        "removed": sorted(set(before) - set(after)),
        "changed": sorted(k for k in set(before) & set(after) if before[k] != after[k]),
    }


def profile(results):
    return "".join({True: "+", False: "-"}.get(r.get("success"), "?") for r in results)


def run(problem_id):
    from sregym.conductor.conductor import Conductor, ConductorConfig

    out = {"problem_id": problem_id}
    conductor, injected, recovered = None, False, False
    try:
        conductor = Conductor(config=ConductorConfig(deploy_loki=False))
        problem = conductor.problems.get_problem_instance(problem_id)
        conductor.problem_id, conductor.problem, conductor.app = problem_id, problem, problem.app
        oracle = problem.mitigation_oracle
        namespace = problem.namespace

        conductor.dependency_check(["kubectl", "helm", "docker"])
        conductor.fix_kubernetes()
        conductor.undeploy_app()
        conductor.deploy_app()
        oracle.capture_baseline()
        healthy = snapshot(namespace)
        out["healthy"] = {"oracle": evaluate(oracle), "workload": workload(problem)}

        injected = True
        problem.inject_fault()
        window = poll(oracle, WINDOW)
        faulted = snapshot(namespace)
        out["faulted"] = {"oracle_window": window, "profile": profile(window), "workload": workload(problem)}

        problem.recover_fault()
        until_pass = poll(oracle, RECOVER_TIMEOUT, stop_on=True)
        recovered = until_pass[-1].get("success") is True
        restored = snapshot(namespace)
        out["recovered"] = {"oracle_until_pass": until_pass, "profile": profile(until_pass),
                            "workload": workload(problem)}
        out["diff"] = {"healthy_to_faulted": diff(healthy, faulted),
                       "faulted_to_recovered": diff(faulted, restored),
                       "healthy_to_recovered": diff(healthy, restored)}
        out["status"] = "COMPLETED"
    except Exception as exc:  # noqa: BLE001
        out["status"] = "ERROR"
        out["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        cleanup = []
        if conductor is not None and conductor.problem is not None:
            if injected and not recovered:
                cleanup.append(conductor.problem.recover_fault)
            cleanup += [conductor.problem.app.cleanup,
                        lambda: conductor.kubectl.wait_for_namespace_deletion(conductor.problem.namespace)]
        if conductor is not None:
            cleanup += [conductor.mcp_server.stop_port_forward, conductor.stop_k8s_proxy]
        errors = []
        for action in cleanup:
            try:
                action()
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{type(exc).__name__}: {exc}")
        out["cleanup"] = "fail" if errors else "pass"
        out["cleanup_errors"] = errors
    return out


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

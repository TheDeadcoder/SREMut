"""Operational mutants: plausible wrong repairs applied to a faulted application without knowing the fault."""
import json
import subprocess
import time

OPERATORS = ("RESTART", "SCALE0", "DELETE", "COLLAT")
KINDS = ("deployments", "statefulsets")
SETTLE_TIMEOUT = 120
POLL = 5


def kubectl(*args):
    return subprocess.run(["kubectl", *args], capture_output=True, text=True)


def workloads(namespace):
    found = {}
    for kind in KINDS:
        proc = kubectl("get", kind, "-n", namespace, "-o", "json")
        if proc.returncode == 0:
            for item in json.loads(proc.stdout)["items"]:
                found[f"{kind}/{item['metadata']['name']}"] = item
    return found


def desired(item):
    replicas = item["spec"].get("replicas")
    return 1 if replicas is None else replicas


def ready(item):
    return (item.get("status") or {}).get("readyReplicas") or 0


def rolled_out(item):
    status = item.get("status") or {}
    return (status.get("observedGeneration", 0) >= item["metadata"].get("generation", 0)
            and (status.get("updatedReplicas") or 0) == desired(item) and ready(item) == desired(item))


def unhealthy(found):
    return sorted(name for name, item in found.items() if ready(item) < desired(item))


def collateral_target(found, touched):
    candidates = [name for name, item in found.items()
                  if desired(item) > 0 and ready(item) >= desired(item) and name not in touched]
    return min(candidates) if candidates else None


def apply(operator, namespace, touched=frozenset()):
    """Applies the operator and returns what it changed, or None when no workload qualifies."""
    found = workloads(namespace)
    if operator == "RESTART":
        targets = sorted(found)
        for name in targets:
            kubectl("rollout", "restart", name, "-n", namespace)
        return {"targets": targets} if targets else None
    if operator in ("SCALE0", "DELETE"):
        targets = unhealthy(found)
        for name in targets:
            if operator == "SCALE0":
                kubectl("scale", name, "--replicas=0", "-n", namespace)
            else:
                kubectl("delete", name, "-n", namespace, "--wait=false")
        return {"targets": targets} if targets else None
    if operator == "COLLAT":
        target = collateral_target(found, touched)
        if target is None:
            return None
        kubectl("scale", target, "--replicas=0", "-n", namespace)
        return {"targets": [target], "replicas": desired(found[target])}
    raise ValueError(operator)


def undo_collateral(namespace, applied):
    kubectl("scale", applied["targets"][0], f"--replicas={applied['replicas']}", "-n", namespace)


def settle(namespace, timeout=SETTLE_TIMEOUT):
    deadline = time.monotonic() + timeout
    while True:
        if all(rolled_out(item) for item in workloads(namespace).values() if desired(item) > 0):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(POLL)

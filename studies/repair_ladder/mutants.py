"""Repair states applied after SREGym deletes Service/user-service, and their activation checks."""
import copy
import json
import time

import contract
from contract import NAMESPACE, SERVICE, items, kubectl, read

STATES = ("M1", "M2", "M3", "M5", "M4", "C1")
DESCRIPTION = {
    "M1": "no repair: the Service stays absent",
    "M2": "Service recreated with a selector that matches no pod",
    "M3": "Service recreated with targetPort 65535",
    "M5": "Service recreated exactly as captured; client pods untouched",
    "M4": "selector-less Service with a manual Endpoints object pinned to the current pod; clients restarted",
    "C1": "Service recreated exactly as captured; clients restarted",
}
RESTARTS_CLIENTS = {"M4", "C1"}
M2_SELECTOR = {"sremut-mutant-backend": "ms-m02"}
M3_TARGET_PORT = 65535
SERVER_FIELDS = ("uid", "resourceVersion", "creationTimestamp", "generation", "managedFields")
SETTLE_TIMEOUT = 300


def clean(service):
    body = copy.deepcopy(service)
    for field in SERVER_FIELDS:
        body["metadata"].pop(field, None)
    for field in ("clusterIP", "clusterIPs"):
        body["spec"].pop(field, None)
    body.pop("status", None)
    return body


def service_body(state, captured):
    if state == "M1":
        return None
    body = clean(captured)
    if state == "M2":
        body["spec"]["selector"] = dict(M2_SELECTOR)
    elif state == "M3":
        body["spec"]["ports"][0]["targetPort"] = M3_TARGET_PORT
    elif state == "M4":
        body["spec"].pop("selector", None)
    return body


def endpoints_body(captured, pod):
    port = captured["spec"]["ports"][0]
    return {
        "apiVersion": "v1",
        "kind": "Endpoints",
        "metadata": {"name": SERVICE, "namespace": NAMESPACE},
        "subsets": [{
            "addresses": [{"ip": pod["status"]["podIP"],
                           "targetRef": {"kind": "Pod", "name": pod["metadata"]["name"],
                                         "namespace": NAMESPACE, "uid": pod["metadata"]["uid"]}}],
            "ports": [{"name": port.get("name"), "port": port["targetPort"], "protocol": port["protocol"]}],
        }],
    }


def create(body):
    proc = kubectl("create", "-f", "-", stdin=json.dumps(body))
    if proc.returncode != 0:
        raise contract.InfrastructureError(f"create {body['kind']}: {proc.stderr.strip()[-300:]}")


def user_service_pod():
    pods = [p for p in contract.deployment_pods(items("pods"), items("replicasets"))
            if contract.pod_ready(p) and not p["metadata"].get("deletionTimestamp")]
    if len(pods) != 1:
        raise contract.InfrastructureError(f"expected one ready user-service pod, found {len(pods)}")
    return pods[0]


def restart_clients(keep_pod):
    for pod in items("pods"):
        name = pod["metadata"]["name"]
        if name != keep_pod and name != contract.PROBE:
            kubectl("delete", "pod", name, "-n", NAMESPACE, "--wait=false")


def settled():
    pods = items("pods")
    return bool(pods) and all(
        p["status"].get("phase") == "Running" and contract.pod_ready(p) for p in pods
        if p["metadata"].get("deletionTimestamp") is None and controller_kind(p) != "Job")


def controller_kind(pod):
    refs = [r for r in pod["metadata"].get("ownerReferences") or [] if r.get("controller")]
    return refs[0]["kind"] if refs else None


def wait_settled(timeout=SETTLE_TIMEOUT):
    deadline = time.monotonic() + timeout
    while not settled():
        if time.monotonic() >= deadline:
            raise contract.InfrastructureError("application pods did not settle after the repair")
        time.sleep(contract.POLL)


def apply(state, captured):
    pod = user_service_pod() if state == "M4" else None
    body = service_body(state, captured)
    if body is not None:
        create(body)
    if state == "M4":
        create(endpoints_body(captured, pod))
    if state in RESTARTS_CLIENTS:
        restart_clients(keep_pod=(pod or user_service_pod())["metadata"]["name"])
        wait_settled()
    return {"pinned_pod": pod["metadata"]["name"] if pod else None}


def activation(state, captured):
    service = read("get", "service", SERVICE, "-n", NAMESPACE)
    if state == "M1":
        return {"active": service is None}
    if service is None:
        return {"active": False, "reason": "Service absent"}
    spec = service["spec"]
    addresses = contract.eligible_addresses(items("endpointslices.discovery.k8s.io",
                                                  f"kubernetes.io/service-name={SERVICE}"))
    live = [p["status"].get("podIP") for p in contract.deployment_pods(items("pods"), items("replicasets"))
            if contract.pod_ready(p)]
    backed = [a for a in addresses if a in live]
    port = spec["ports"][0]
    checks = {
        "M2": spec.get("selector") == M2_SELECTOR and not addresses,
        "M3": spec.get("selector") == captured["spec"]["selector"] and port.get("targetPort") == M3_TARGET_PORT
        and bool(backed),
        "M5": spec.get("selector") == captured["spec"]["selector"] and bool(backed),
        "C1": spec.get("selector") == captured["spec"]["selector"] and bool(backed),
        "M4": not spec.get("selector") and bool(backed)
        and read("get", "endpoints", SERVICE, "-n", NAMESPACE) is not None,
    }
    return {"active": checks[state], "eligible_endpoints": len(addresses), "backed_endpoints": len(backed)}


def wait_active(state, captured, timeout=60):
    deadline = time.monotonic() + timeout
    while True:
        result = activation(state, captured)
        if result["active"] or time.monotonic() >= deadline:
            return result
        time.sleep(contract.POLL)

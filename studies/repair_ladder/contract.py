"""Operational contract for missing_service_social_network: invariants MS-I1 to MS-I6."""
import ipaddress
import json
import re
import subprocess
import time

NAMESPACE = "social-network"
SERVICE = "user-service"
DEPLOYMENT = "user-service"
PORT = 9090
FQDN = f"{SERVICE}.{NAMESPACE}.svc.cluster.local"
PROBE = "sremut-probe"
PROBE_SELECTOR = "app.kubernetes.io/name=sremut-probe"
SREGYM_PROBE_SELECTOR = "app=service-connectivity-check"
DEADLINE = 30
POLL = 2
TCP_TIMEOUT = 3
REPLACEMENT_DEADLINE = 120
WINDOW_SECONDS = 66
MIN_REQUESTS = 50
NON2XX = re.compile(r"Non-2xx or 3xx responses:\s*(\d+)")
SOCKET = re.compile(r"Socket errors: connect (\d+), read (\d+), write (\d+), timeout (\d+)")
PROBE_POD = {
    "apiVersion": "v1",
    "kind": "Pod",
    "metadata": {"name": PROBE, "namespace": NAMESPACE, "labels": {"app.kubernetes.io/name": PROBE}},
    "spec": {
        "automountServiceAccountToken": False,
        "restartPolicy": "Never",
        "activeDeadlineSeconds": 900,
        "terminationGracePeriodSeconds": 1,
        "containers": [{
            "name": "probe",
            "image": "docker.io/library/busybox@sha256:"
                     "b7f3d86d6e84fc17718c48bcde1450807faa2d56704205c697b4bd5df7b9e29f",
            "imagePullPolicy": "IfNotPresent",
            "command": ["sh", "-c", "trap 'exit 0' TERM INT; sleep 900 & wait"],
            "securityContext": {
                "runAsNonRoot": True, "runAsUser": 65532, "runAsGroup": 65532,
                "readOnlyRootFilesystem": True, "allowPrivilegeEscalation": False,
                "capabilities": {"drop": ["ALL"]}, "seccompProfile": {"type": "RuntimeDefault"},
            },
            "resources": {"requests": {"cpu": "5m", "memory": "8Mi"},
                          "limits": {"cpu": "50m", "memory": "32Mi"}},
        }],
    },
}


class InfrastructureError(RuntimeError):
    """A condition independent of the repair under test, such as a failed cluster read."""


def kubectl(*args, stdin=None):
    return subprocess.run(["kubectl", *args], input=stdin, capture_output=True, text=True)


def read(*args):
    proc = kubectl(*args, "-o", "json")
    if proc.returncode == 0:
        return json.loads(proc.stdout)
    if "NotFound" in proc.stderr:
        return None
    raise InfrastructureError(f"kubectl {' '.join(args)}: {proc.stderr.strip()[-300:]}")


def items(kind, selector=None):
    return read("get", kind, "-n", NAMESPACE, *(["-l", selector] if selector else []))["items"]


def controller(obj, kind):
    for ref in obj["metadata"].get("ownerReferences") or []:
        if ref.get("controller") and ref.get("kind") == kind:
            return ref["name"]
    return None


def pod_ready(pod):
    return any(c["type"] == "Ready" and c["status"] == "True"
               for c in (pod.get("status") or {}).get("conditions") or [])


def is_ipv4(address):
    try:
        ipaddress.IPv4Address(address)
        return True
    except ValueError:
        return False


def eligible_addresses(slices):
    addresses = []
    for endpoint_slice in slices:
        if endpoint_slice.get("addressType") != "IPv4":
            continue
        for endpoint in endpoint_slice.get("endpoints") or []:
            conditions = endpoint.get("conditions") or {}
            if conditions.get("ready") is True and conditions.get("terminating") is not True:
                addresses += [a for a in endpoint.get("addresses") or [] if is_ipv4(a)]
    return sorted(addresses)


def deployment_pods(pods, replicasets):
    owner = {rs["metadata"]["name"]: controller(rs, "Deployment") for rs in replicasets}
    return [p for p in pods if owner.get(controller(p, "ReplicaSet")) == DEPLOYMENT]


def unmapped(addresses, pods, replicasets):
    live = {p["status"].get("podIP") for p in deployment_pods(pods, replicasets)
            if pod_ready(p) and not p["metadata"].get("deletionTimestamp")}
    return [a for a in addresses if a not in live]


def parse_nslookup(stdout):
    answers, in_answer = [], False
    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith("Name:"):
            in_answer = True
        elif in_answer and line.startswith("Address"):
            value = line.split(":", 1)[1].strip().split("#")[0].strip()
            if value:
                answers.append(value)
    return answers


def below_floor(deployments, floors):
    current = {d["metadata"]["name"]: d for d in deployments}
    failures = []
    for name, floor in sorted(floors.items()):
        dep = current.get(name)
        if dep is None:
            failures.append(f"{name}: missing")
            continue
        status = dep.get("status") or {}
        for field, value in (("desired", dep["spec"].get("replicas", 1)),
                             ("ready", status.get("readyReplicas")),
                             ("available", status.get("availableReplicas"))):
            if (value or 0) < floor:
                failures.append(f"{name}: {field} {value or 0} < {floor}")
    return failures


def round_stats(log):
    non2xx = NON2XX.search(log)
    sockets = SOCKET.search(log)
    return {"non2xx": int(non2xx.group(1)) if non2xx else 0,
            "socket_errors": sum(map(int, sockets.groups())) if sockets else 0,
            "timeouts": int(sockets.group(4)) if sockets else 0}


def probe_pods():
    return [p["metadata"]["name"] for selector in (PROBE_SELECTOR, SREGYM_PROBE_SELECTOR)
            for p in items("pods", selector)]


def wait_no_probe_pods(timeout=60):
    deadline = time.monotonic() + timeout
    while probe_pods():
        if time.monotonic() >= deadline:
            raise InfrastructureError(f"probe pods still present: {probe_pods()}")
        time.sleep(POLL)


def create_probe():
    proc = kubectl("create", "-f", "-", stdin=json.dumps(PROBE_POD))
    if proc.returncode != 0:
        raise InfrastructureError(f"probe pod not created: {proc.stderr.strip()[-300:]}")
    ready = kubectl("wait", "--for=condition=Ready", f"pod/{PROBE}", "-n", NAMESPACE, "--timeout=90s")
    if ready.returncode != 0:
        raise InfrastructureError(f"probe pod not Ready: {ready.stderr.strip()[-300:]}")


def delete_probe():
    kubectl("delete", "pod", PROBE, "-n", NAMESPACE, "--ignore-not-found", "--wait=true", "--timeout=60s")
    wait_no_probe_pods()


def probe_exec(*argv):
    proc = kubectl("exec", PROBE, "-n", NAMESPACE, "--", *argv)
    return proc.returncode, proc.stdout


def observe_routing():
    service = read("get", "service", SERVICE, "-n", NAMESPACE)
    cluster_ip = ((service or {}).get("spec") or {}).get("clusterIP")
    addresses = eligible_addresses(items("endpointslices.discovery.k8s.io",
                                         f"kubernetes.io/service-name={SERVICE}"))
    pods, replicasets = items("pods"), items("replicasets")
    dns_code, dns_out = probe_exec("nslookup", FQDN)
    tcp_code, _ = probe_exec("nc", "-z", "-w", str(TCP_TIMEOUT), FQDN, str(PORT))
    usable = bool(cluster_ip) and cluster_ip != "None"
    return {
        "service_present": service is not None,
        "cluster_ip_usable": usable,
        "dns_matches": usable and dns_code == 0 and cluster_ip in parse_nslookup(dns_out),
        "eligible_endpoints": len(addresses),
        "unmapped_endpoints": len(unmapped(addresses, pods, replicasets)),
        "tcp_ok": tcp_code == 0,
    }


def routing_invariants(deadline=DEADLINE):
    passed = {"MS-I1": False, "MS-I2": False, "MS-I3": False}
    start, observations = time.monotonic(), 0
    while True:
        obs = observe_routing()
        observations += 1
        passed["MS-I1"] |= obs["service_present"] and obs["dns_matches"]
        passed["MS-I3"] |= obs["eligible_endpoints"] >= 1
        passed["MS-I2"] |= obs["eligible_endpoints"] >= 1 and obs["unmapped_endpoints"] == 0 and obs["tcp_ok"]
        if all(passed.values()) or time.monotonic() - start >= deadline:
            return passed, {**obs, "observations": observations}
        time.sleep(POLL)


def workload_window(wrk, seconds=WINDOW_SECONDS):
    wrk.collect(number=1)
    time.sleep(seconds)
    rounds = [{"requests": max(e.number, 0), "ok": e.ok, **round_stats(e.log)}
              for e in wrk.recent_entries(duration=seconds)]
    requests = sum(r["requests"] for r in rounds)
    non2xx = sum(r["non2xx"] for r in rounds)
    return {
        "rounds": rounds,
        "requests": requests,
        "non2xx": non2xx,
        "timeouts": sum(r["timeouts"] for r in rounds),
        "pass": requests >= MIN_REQUESTS and non2xx == 0 and all(r["ok"] for r in rounds),
    }


def replacement_challenge(wrk):
    pods = [p for p in deployment_pods(items("pods"), items("replicasets"))
            if pod_ready(p) and not p["metadata"].get("deletionTimestamp")]
    if not pods:
        return {"pass": False, "reason": "no ready user-service pod to replace"}
    target = sorted(pods, key=lambda p: p["metadata"]["name"])[0]
    old_uid = target["metadata"]["uid"]
    kubectl("delete", "pod", target["metadata"]["name"], "-n", NAMESPACE, "--wait=false")
    deadline = time.monotonic() + REPLACEMENT_DEADLINE
    while True:
        current = deployment_pods(items("pods"), items("replicasets"))
        replacement = [p for p in current if p["metadata"]["uid"] != old_uid and pod_ready(p)]
        old_gone = all(p["metadata"]["uid"] != old_uid for p in current)
        if replacement and old_gone:
            break
        if time.monotonic() >= deadline:
            return {"pass": False, "reason": "replacement pod not Ready"}
        time.sleep(POLL)
    passed, observation = routing_invariants()
    window = workload_window(wrk)
    return {"pass": all(passed.values()) and window["pass"], "routing": passed,
            "observation": observation, "window": window}


def evaluate(wrk, floors, replacement):
    create_probe()
    try:
        passed, observation = routing_invariants()
        window = workload_window(wrk)
        capacity = below_floor(items("deployments"), floors)
        invariants = {**passed, "MS-I4": window["pass"], "MS-I5": not capacity}
        challenge = replacement_challenge(wrk) if replacement else None
        if challenge is not None:
            invariants["MS-I6"] = challenge["pass"]
    finally:
        delete_probe()
    violated = sorted(k for k, v in invariants.items() if not v)
    return {"verdict": "REJECT" if violated else "PASS", "violated": violated, "invariants": invariants,
            "observation": observation, "window": window, "capacity_failures": capacity,
            "replacement": challenge}

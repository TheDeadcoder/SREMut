"""Reference verifier: the system judged against its own healthy baseline on capacity, routing and user requests."""
import json
import subprocess
import time

import operators

WINDOW = 45
THROUGHPUT_FLOOR = 0.5
GENERATOR_WAIT = 180


def items(*args):
    proc = subprocess.run(["kubectl", *args, "-o", "json"], capture_output=True, text=True)
    return json.loads(proc.stdout)["items"] if proc.returncode == 0 else []


def services(namespace):
    ready = {}
    for endpoint_slice in items("get", "endpointslices", "-n", namespace):
        name = endpoint_slice["metadata"].get("labels", {}).get("kubernetes.io/service-name")
        ready[name] = ready.get(name, 0) + sum(
            1 for e in endpoint_slice.get("endpoints") or [] if (e.get("conditions") or {}).get("ready"))
    found = {}
    for svc in items("get", "services", "-n", namespace):
        spec, name = svc["spec"], svc["metadata"]["name"]
        found[name] = {"selector": spec.get("selector"),
                       "ports": sorted([p.get("port"), str(p.get("targetPort")), p.get("protocol")]
                                       for p in spec.get("ports") or []),
                       "endpoints": ready.get(name, 0)}
    return found


def traffic(wrk, seconds=WINDOW):
    if wrk is None:
        return None
    deadline = time.monotonic() + GENERATOR_WAIT
    while True:
        try:
            wrk.collect(number=1)
            break
        except Exception as exc:  # noqa: BLE001
            if time.monotonic() >= deadline:
                return {"error": f"{type(exc).__name__}: {exc}"}
            time.sleep(10)
    time.sleep(seconds)
    entries = wrk.recent_entries(duration=seconds)
    return {"rounds": len(entries), "requests": sum(max(e.number, 0) for e in entries),
            "failed_rounds": sum(not e.ok for e in entries)}


def per_round(sample):
    return sample["requests"] / sample["rounds"] if sample.get("rounds") else 0


def capture(namespace, wrk):
    return {"workloads": {name: operators.ready(item) for name, item in operators.workloads(namespace).items()},
            "services": services(namespace), "traffic": traffic(wrk)}


def judge(healthy, current):
    capacity = sorted(name for name, ready in healthy["workloads"].items()
                      if current["workloads"].get(name, 0) < ready)
    routing = sorted(name for name, svc in healthy["services"].items()
                     if svc["endpoints"] and not (name in current["services"]
                                                  and current["services"][name]["selector"] == svc["selector"]
                                                  and current["services"][name]["ports"] == svc["ports"]
                                                  and current["services"][name]["endpoints"]))
    base, now = healthy["traffic"], current["traffic"]
    function = None
    if base and "error" not in base and base["requests"]:
        function = (bool(now) and "error" not in now and now["failed_rounds"] == 0
                    and per_round(now) >= THROUGHPUT_FLOOR * per_round(base))
    return {"capacity": capacity, "routing": routing, "function": function, "traffic": now,
            "healthy": not capacity and not routing and function is not False}

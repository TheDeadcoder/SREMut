"""Contract evaluation for the pre-registered MS-M01/M02/M03 mutant runs.

Implements five of the six invariants frozen in
`contracts/missing_service_social_network.yaml`, as fixed in advance by
`PREREGISTRATION_MS_MUTANTS.md` section 4.

  MS-I1  stable service identity      Service + ClusterIP + DNS A record
  MS-I2  correct backend routing      endpoint ownership chain + TCP reachability
  MS-I3  ready endpoint availability  >= 1 eligible EndpointSlice endpoint
  MS-I4  functional workload          >= 50 requests, zero non-2xx, caller-supplied
  MS-I5  capacity preservation        every baseline deployment at/above its floor
  MS-I6  repair persistence           NOT_EVALUATED (pre-registration section 6)

Adjudication rule, from the contract
(`adjudication.target_predicate_timeout_outcome: REJECT`,
 `target_predicate_timeout_is_infrastructure_failure: false`,
 `never_infer_infrastructure_failure_from: target_invariant_timeout`):

    A predicate that reaches its deadline without passing is FAIL, never an
    infrastructure error. An exception raised by a probe is recorded in
    `observations` and still yields FAIL. There is no "inconclusive" outcome.

No side effects at import. Every cluster call goes through `k()` imported from
`three_state_run`, so the mutant runs use provably identical instrumentation.

Run under SREGym/.venv/bin/python with cwd=SREGym.
"""

from __future__ import annotations

import ipaddress
import json
import time

import yaml

from three_state_run import EXPERIMENTS, NAMESPACE, k, say, utc_now

# ------------------------------------------------------------------ constants
SERVICE_NAME = "user-service"
DEPLOYMENT_NAME = "user-service"
CANONICAL_FQDN = "user-service.social-network.svc.cluster.local"
SERVICE_PORT = 9090

# contracts/missing_service_social_network.yaml -> challenge_protocol.dns_identity
#   .service.cluster_ip.rejected_values
REJECTED_CLUSTER_IPS = ("", "None")

# experiments/g02-run-01/post/challenge/challenge-pod.yaml
CHALLENGE_POD_TEMPLATE = (
    EXPERIMENTS / "g02-run-01" / "post" / "challenge" / "challenge-pod.yaml"
)

PROBE_READY_CEILING_SECONDS = 60
PROBE_DELETE_CEILING_SECONDS = 60


# -------------------------------------------------------------- small helpers
def _kjson(*args: str) -> tuple[dict | None, dict]:
    """Run a read-only kubectl call expecting JSON. Never raises.

    Returns (parsed_or_None, raw_record). A NotFound is (None, record) with the
    record carrying exit status and stderr, so absence is data, not an error.
    """
    p = k(*args)
    rec = {
        "argv": list(args),
        "exit": p.returncode,
        "stderr": p.stderr[-4000:],
        "stdout_bytes": len(p.stdout),
    }
    if p.returncode != 0 or not p.stdout.strip():
        return None, rec
    try:
        return json.loads(p.stdout), rec
    except json.JSONDecodeError as exc:
        rec["json_error"] = f"{type(exc).__name__}: {exc}"
        return None, rec


def _pod_is_ready(pod: dict) -> bool:
    for cond in (pod.get("status") or {}).get("conditions") or []:
        if cond.get("type") == "Ready":
            return cond.get("status") == "True"
    return False


def _controller_ref(obj: dict, kind: str) -> dict | None:
    """The ownerReference with controller: true and the requested kind."""
    for ref in (obj.get("metadata") or {}).get("ownerReferences") or []:
        if ref.get("controller") is True and ref.get("kind") == kind:
            return ref
    return None


def _eligible_endpoints(slices: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split every EndpointSlice endpoint into (eligible, ineligible).

    Eligibility, from challenge_protocol.eligible_backend_set.endpoint_eligibility:
    addressType IPv4, conditions.ready == true, conditions.terminating absent or
    false, and each address parses as an IPv4Address.
    """
    eligible: list[dict] = []
    ineligible: list[dict] = []
    for sl in slices:
        addr_type = sl.get("addressType")
        slice_name = (sl.get("metadata") or {}).get("name")
        for ep in sl.get("endpoints") or []:
            conds = ep.get("conditions") or {}
            for addr in ep.get("addresses") or []:
                rec = {
                    "slice": slice_name,
                    "address_type": addr_type,
                    "address": addr,
                    "conditions": conds,
                    "target_ref": ep.get("targetRef"),
                }
                reasons = []
                if addr_type != "IPv4":
                    reasons.append(f"address_type={addr_type!r} is not IPv4")
                if conds.get("ready") is not True:
                    reasons.append(f"conditions.ready={conds.get('ready')!r} is not true")
                if conds.get("terminating") is True:
                    reasons.append("conditions.terminating is true")
                try:
                    ipaddress.IPv4Address(addr)
                except (ipaddress.AddressValueError, ValueError) as exc:
                    reasons.append(f"address does not parse as IPv4Address: {exc}")
                if reasons:
                    rec["ineligible_because"] = reasons
                    ineligible.append(rec)
                else:
                    eligible.append(rec)
    return eligible, ineligible


def _parse_nslookup_answers(stdout: str) -> list[str]:
    """Addresses from the ANSWER section of busybox nslookup.

    Only `Address:` lines that follow a `Name:` line count. The leading
    `Server:` / `Address:` header names the resolver, not the record, and must
    never be mistaken for an A record.
    """
    answers: list[str] = []
    in_answer = False
    for line in stdout.splitlines():
        s = line.strip()
        if s.startswith("Name:"):
            in_answer = True
            continue
        if in_answer and s.startswith("Address"):
            part = s.split(":", 1)[1].strip() if ":" in s else ""
            part = part.split("#")[0].strip()
            if part:
                answers.append(part)
    return answers


# ---------------------------------------------------------------- probe pod
def create_probe_pod(run_id: str, mutant_id: str, state: str) -> dict:
    """Create the busybox probe pod for one state and wait for phase Running.

    Built from the frozen g02 challenge pod: the pinned busybox digest and every
    security field are carried over untouched. Only metadata.name and the
    sremut-run-id / sremut-mutant labels change.
    """
    body = yaml.safe_load(CHALLENGE_POD_TEMPLATE.read_text(encoding="utf-8"))
    pod_name = f"sremut-probe-{run_id}-{state}"
    body["metadata"]["name"] = pod_name
    body["metadata"]["labels"]["sremut-run-id"] = run_id
    body["metadata"]["labels"]["sremut-mutant"] = mutant_id

    out_dir = EXPERIMENTS / run_id / state
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = out_dir / "probe-pod.json"
    payload = json.dumps(body, indent=2, sort_keys=True) + "\n"
    manifest.write_text(payload, encoding="utf-8")

    rec: dict = {
        "name": pod_name,
        "state": state,
        "template": str(CHALLENGE_POD_TEMPLATE),
        "manifest_path": str(manifest),
        "requested_utc": utc_now(),
        "phase_timeline": [],
        "uid": None,
        "running": False,
    }

    created = k("create", "-f", str(manifest))
    rec["create"] = {
        "exit": created.returncode,
        "stdout": created.stdout[-2000:],
        "stderr": created.stderr[-2000:],
    }
    if created.returncode != 0:
        rec["error"] = "probe pod creation failed"
        return rec

    t0 = time.monotonic()
    while time.monotonic() - t0 < PROBE_READY_CEILING_SECONDS:
        pod, _ = _kjson("get", "pod", pod_name, "-n", NAMESPACE,
                        "--ignore-not-found", "-o", "json")
        if pod:
            phase = (pod.get("status") or {}).get("phase")
            rec["uid"] = (pod.get("metadata") or {}).get("uid")
            if not rec["phase_timeline"] or rec["phase_timeline"][-1]["phase"] != phase:
                rec["phase_timeline"].append({"phase": phase, "utc": utc_now()})
            if phase == "Running":
                rec["running"] = True
                break
        time.sleep(1)
    rec["ready_utc"] = utc_now()
    rec["wait_seconds"] = round(time.monotonic() - t0, 3)
    if not rec["running"]:
        rec["error"] = (
            f"probe pod did not reach phase Running within "
            f"{PROBE_READY_CEILING_SECONDS}s"
        )
    return rec


def probe(pod_name: str, argv: list[str]) -> dict:
    """kubectl exec into the probe pod. A failing probe is data, never an exception."""
    p = k("exec", pod_name, "-n", NAMESPACE, "--", *argv)
    return {
        "argv": list(argv),
        "exit": p.returncode,
        "stdout": p.stdout[-8000:],
        "stderr": p.stderr[-8000:],
    }


def delete_probe_pod(pod_name: str) -> dict:
    """Delete the probe pod and block until it is genuinely gone.

    Not optional. A Terminating or Succeeded probe pod trips the same
    namespace-wide pod sweep in mitigation.py:86,95 that a stale Failed wrk2 pod
    does, silently turning a true verdict into false.
    """
    rec: dict = {"name": pod_name, "requested_utc": utc_now()}
    d = k("delete", "pod", pod_name, "-n", NAMESPACE,
          "--ignore-not-found", f"--timeout={PROBE_DELETE_CEILING_SECONDS}s")
    rec["delete"] = {
        "exit": d.returncode,
        "stdout": d.stdout[-2000:],
        "stderr": d.stderr[-2000:],
    }
    w = k("wait", "--for=delete", f"pod/{pod_name}", "-n", NAMESPACE,
          f"--timeout={PROBE_DELETE_CEILING_SECONDS}s")
    rec["wait"] = {
        "exit": w.returncode,
        "stdout": w.stdout[-2000:],
        "stderr": w.stderr[-2000:],
    }
    # Independent re-check: `wait --for=delete` returns 0 for an absent object,
    # so confirm absence directly rather than trusting the wait's exit status.
    g = k("get", "pod", pod_name, "-n", NAMESPACE, "--ignore-not-found",
          "-o", "jsonpath={.metadata.name}")
    rec["recheck"] = {
        "exit": g.returncode,
        "stdout": g.stdout.strip(),
        "stderr": g.stderr[-2000:],
    }
    rec["gone"] = g.returncode == 0 and g.stdout.strip() == ""
    rec["deleted_utc"] = utc_now()
    if not rec["gone"]:
        say(f"  !! probe pod {pod_name} still present after delete+wait")
    return rec


# ------------------------------------------------------------- the invariants
def _eval_ms_i5(replica_baseline: dict) -> dict:
    """MS-I5 capacity preservation. Snapshot, no deadline."""
    t0 = time.monotonic()
    started = utc_now()
    deployments, raw = _kjson("get", "deployments", "-n", NAMESPACE, "-o", "json")
    obs: dict = {"kubectl": raw, "per_deployment": {}, "missing": [], "below_floor": []}
    if deployments is None:
        obs["error"] = "could not list deployments"
        return {"result": "FAIL", "observations": obs,
                "first_observation_utc": started, "last_observation_utc": utc_now(),
                "attempts": 1, "elapsed_seconds": round(time.monotonic() - t0, 3)}

    current = {
        (d.get("metadata") or {}).get("name"): d
        for d in deployments.get("items") or []
    }
    ok = True
    for name, floor in sorted(replica_baseline.items()):
        dep = current.get(name)
        if dep is None:
            obs["missing"].append(name)
            obs["per_deployment"][name] = {"floor": floor, "present": False}
            ok = False
            continue
        spec = dep.get("spec") or {}
        status = dep.get("status") or {}
        desired = spec.get("replicas")
        ready = status.get("readyReplicas") or 0
        available = status.get("availableReplicas") or 0
        row = {
            "floor": floor, "present": True, "spec_replicas": desired,
            "ready_replicas": ready, "available_replicas": available,
        }
        failures = []
        if desired is None or desired < floor:
            failures.append(f"spec.replicas={desired} < floor {floor}")
        if ready < floor:
            failures.append(f"status.readyReplicas={ready} < floor {floor}")
        if available < floor:
            failures.append(f"status.availableReplicas={available} < floor {floor}")
        if failures:
            row["failures"] = failures
            obs["below_floor"].append(name)
            ok = False
        obs["per_deployment"][name] = row

    obs["baseline_entries"] = len(replica_baseline)
    return {"result": "PASS" if ok else "FAIL", "observations": obs,
            "first_observation_utc": started, "last_observation_utc": utc_now(),
            "attempts": 1, "elapsed_seconds": round(time.monotonic() - t0, 3)}


def _eval_ms_i4(workload_window: dict | None) -> dict:
    """MS-I4 functional workload. Pure function of the caller's window.

    wrk2 is never re-parsed here: the window is produced by
    `three_state_run.wait_for_rounds`, the same code path the thirteen historical
    runs used.
    """
    t0 = time.monotonic()
    started = utc_now()
    obs: dict = {"minimum_requests": 50, "maximum_non_2xx_or_3xx": 0}
    if not workload_window:
        obs["error"] = "no workload window supplied"
        return {"result": "FAIL", "observations": obs,
                "first_observation_utc": started, "last_observation_utc": utc_now(),
                "attempts": 1, "elapsed_seconds": round(time.monotonic() - t0, 3)}

    total_requests = workload_window.get("total_requests") or 0
    total_non2xx = workload_window.get("total_non2xx")
    obs["window"] = {
        key: workload_window.get(key)
        for key in ("rounds", "total_requests", "total_non2xx", "non2xx_rate",
                    "rounds_with_non2xx", "first_round_utc", "last_round_utc",
                    "timed_out", "wait_seconds")
    }
    failures = []
    if total_requests < 50:
        failures.append(f"total_requests={total_requests} < 50")
    if total_non2xx is None:
        failures.append("total_non2xx is None")
    elif total_non2xx != 0:
        failures.append(f"total_non2xx={total_non2xx} != 0")
    if failures:
        obs["failures"] = failures
    return {"result": "PASS" if not failures else "FAIL", "observations": obs,
            "first_observation_utc": started, "last_observation_utc": utc_now(),
            "attempts": 1, "elapsed_seconds": round(time.monotonic() - t0, 3)}


def _observe_identity_and_routing(pod_name: str, tcp_timeout_seconds: int) -> dict:
    """One full observation of the MS-I1 / MS-I2 / MS-I3 surface."""
    obs: dict = {"observed_utc": utc_now()}

    svc, svc_raw = _kjson("get", "service", SERVICE_NAME, "-n", NAMESPACE,
                          "--ignore-not-found", "-o", "json")
    obs["service_kubectl"] = svc_raw
    obs["service_present"] = svc is not None
    obs["service"] = svc
    cluster_ip = None
    if svc is not None:
        cluster_ip = (svc.get("spec") or {}).get("clusterIP")
    obs["cluster_ip"] = cluster_ip
    obs["cluster_ip_usable"] = bool(
        cluster_ip is not None and cluster_ip not in REJECTED_CLUSTER_IPS
    )

    slices_doc, sl_raw = _kjson(
        "get", "endpointslices.discovery.k8s.io", "-n", NAMESPACE,
        "-l", f"kubernetes.io/service-name={SERVICE_NAME}", "-o", "json")
    obs["endpointslices_kubectl"] = sl_raw
    slices = (slices_doc or {}).get("items") or []
    obs["endpointslice_count"] = len(slices)
    eligible, ineligible = _eligible_endpoints(slices)
    obs["eligible_endpoints"] = eligible
    obs["ineligible_endpoints"] = ineligible

    pods_doc, pods_raw = _kjson("get", "pods", "-n", NAMESPACE, "-o", "json")
    rs_doc, rs_raw = _kjson("get", "replicasets", "-n", NAMESPACE, "-o", "json")
    obs["pods_kubectl"] = pods_raw
    obs["replicasets_kubectl"] = rs_raw
    pods_by_ip: dict[str, dict] = {}
    for pod in (pods_doc or {}).get("items") or []:
        pod_ip = (pod.get("status") or {}).get("podIP")
        if pod_ip:
            pods_by_ip[pod_ip] = pod
    rs_by_name = {
        (r.get("metadata") or {}).get("name"): r
        for r in (rs_doc or {}).get("items") or []
    }

    # Map every eligible address to a Ready, non-terminating pod controlled by
    # Deployment/user-service through Pod -> ReplicaSet -> Deployment.
    mappings = []
    all_mapped = True
    for ep in eligible:
        addr = ep["address"]
        row: dict = {"address": addr, "mapped": False}
        pod = pods_by_ip.get(addr)
        if pod is None:
            row["reason"] = "no pod in namespace has this podIP"
            all_mapped = False
            mappings.append(row)
            continue
        meta = pod.get("metadata") or {}
        row["pod"] = meta.get("name")
        row["pod_uid"] = meta.get("uid")
        row["pod_ready"] = _pod_is_ready(pod)
        row["deletion_timestamp"] = meta.get("deletionTimestamp")
        reasons = []
        if not row["pod_ready"]:
            reasons.append("pod Ready condition is not True")
        if meta.get("deletionTimestamp") is not None:
            reasons.append("pod has a deletionTimestamp")

        rs_ref = _controller_ref(pod, "ReplicaSet")
        row["replicaset_ref"] = rs_ref
        if rs_ref is None:
            reasons.append("pod has no controller ownerReference of kind ReplicaSet")
        else:
            rs = rs_by_name.get(rs_ref.get("name"))
            if rs is None:
                reasons.append(f"ReplicaSet {rs_ref.get('name')!r} not found in namespace")
            else:
                dep_ref = _controller_ref(rs, "Deployment")
                row["deployment_ref"] = dep_ref
                if dep_ref is None:
                    reasons.append(
                        "ReplicaSet has no controller ownerReference of kind Deployment")
                elif dep_ref.get("name") != DEPLOYMENT_NAME:
                    reasons.append(
                        f"terminal controller is Deployment/{dep_ref.get('name')}, "
                        f"not Deployment/{DEPLOYMENT_NAME}")
        if reasons:
            row["reason"] = "; ".join(reasons)
            all_mapped = False
        else:
            row["mapped"] = True
        mappings.append(row)

    obs["endpoint_pod_mappings"] = mappings
    obs["all_eligible_endpoints_mapped"] = all_mapped

    # DNS, from inside the namespace.
    dns = probe(pod_name, ["nslookup", CANONICAL_FQDN])
    obs["nslookup"] = dns
    answers = _parse_nslookup_answers(dns["stdout"])
    obs["dns_answer_addresses"] = answers
    obs["dns_matches_cluster_ip"] = bool(
        obs["cluster_ip_usable"] and cluster_ip in answers
    )
    # Recorded for audit; the verdict uses the parsed ANSWER section above so a
    # resolver address in the header can never be counted as an A record.
    obs["dns_naive_contains_cluster_ip"] = bool(
        obs["cluster_ip_usable"] and cluster_ip in dns["stdout"]
    )

    # TCP reachability of the Service port, from inside the namespace.
    tcp = probe(pod_name,
                ["nc", "-z", "-w", str(tcp_timeout_seconds),
                 CANONICAL_FQDN, str(SERVICE_PORT)])
    obs["nc"] = tcp
    obs["tcp_connect_ok"] = tcp["exit"] == 0

    obs["ms_i1_pass"] = bool(
        obs["service_present"] and obs["cluster_ip_usable"]
        and obs["dns_matches_cluster_ip"]
    )
    obs["ms_i3_pass"] = len(eligible) >= 1
    # backend_ownership_also_required: true, plus a backend must actually exist —
    # routing to zero instances is not routing to "Ready instances of the actual
    # user-service workload".
    obs["ms_i2_pass"] = bool(
        obs["ms_i3_pass"] and all_mapped and obs["tcp_connect_ok"]
    )
    return obs


def evaluate_contract(*, run_id: str, mutant_id: str, state: str,
                      replica_baseline: dict, workload_window: dict | None,
                      deadline_seconds: int = 30, poll_seconds: int = 2,
                      tcp_timeout_seconds: int = 3) -> dict:
    """Evaluate MS-I1..MS-I5 for one state. Creates and deletes its own probe pod.

    A predicate that reaches its deadline without passing is FAIL. There is no
    inconclusive outcome and no infrastructure-error escape hatch.
    """
    say(f"  contract evaluation [{state}] starting (deadline {deadline_seconds}s)")
    evaluated_utc = utc_now()
    invariants: dict[str, dict] = {}

    # Deadline-free predicates first; neither needs the probe pod.
    invariants["MS-I4"] = _eval_ms_i4(workload_window)
    invariants["MS-I5"] = _eval_ms_i5(replica_baseline)

    probe_rec = create_probe_pod(run_id, mutant_id, state)
    pod_name = probe_rec["name"]

    t0 = time.monotonic()
    started = utc_now()
    attempts = 0
    observations: list[dict] = []
    passed = {"MS-I1": False, "MS-I2": False, "MS-I3": False}
    first_pass_obs: dict[str, dict] = {}
    last_obs: dict | None = None

    if not probe_rec.get("running"):
        # The probe pod never came up. Per the adjudication rule this is still a
        # FAIL of the predicates that depend on it, recorded with the reason.
        say(f"  !! probe pod not Running: {probe_rec.get('error')}")
    else:
        while True:
            attempts += 1
            try:
                obs = _observe_identity_and_routing(pod_name, tcp_timeout_seconds)
            except Exception as exc:  # noqa: BLE001 - a failing probe is data
                obs = {
                    "observed_utc": utc_now(),
                    "exception": f"{type(exc).__name__}: {exc}",
                    "ms_i1_pass": False, "ms_i2_pass": False, "ms_i3_pass": False,
                }
            observations.append(obs)
            last_obs = obs
            for inv in ("MS-I1", "MS-I2", "MS-I3"):
                key = f"ms_i{inv[-1]}_pass"
                if not passed[inv] and obs.get(key):
                    passed[inv] = True
                    first_pass_obs[inv] = obs
            if all(passed.values()):
                break
            if time.monotonic() - t0 >= deadline_seconds:
                break
            time.sleep(poll_seconds)

    elapsed = round(time.monotonic() - t0, 3)
    for inv in ("MS-I1", "MS-I2", "MS-I3"):
        chosen = first_pass_obs.get(inv, last_obs)
        record: dict = {
            "result": "PASS" if passed[inv] else "FAIL",
            "observations": {
                "deciding_observation": chosen,
                "probe_pod_running": bool(probe_rec.get("running")),
                "total_observations": len(observations),
            },
            "first_observation_utc": started,
            "last_observation_utc": (last_obs or {}).get("observed_utc", started),
            "attempts": attempts,
            "elapsed_seconds": elapsed,
        }
        if not passed[inv]:
            record["observations"]["timed_out"] = elapsed >= deadline_seconds
            if not probe_rec.get("running"):
                record["observations"]["probe_pod_error"] = probe_rec.get("error")
        invariants[inv] = record

    # All raw observations are kept so the verdict can be recomputed offline.
    invariants["MS-I1"]["observations"]["all_observations"] = observations

    invariants["MS-I6"] = {
        "result": "NOT_EVALUATED",
        "observations": {
            "reason": ("deferred: not discriminating for MS-M01..M03; "
                       "see PREREGISTRATION_MS_MUTANTS.md section 6"),
        },
        "first_observation_utc": None,
        "last_observation_utc": None,
        "attempts": 0,
        "elapsed_seconds": 0.0,
    }

    probe_delete = delete_probe_pod(pod_name) if probe_rec.get("create") else None
    probe_rec["delete"] = probe_delete

    scored = ("MS-I1", "MS-I2", "MS-I3", "MS-I4", "MS-I5")
    violated = [i for i in scored if invariants[i]["result"] != "PASS"]
    verdict = "PASS" if not violated else "REJECT"
    say(f"  contract [{state}] verdict = {verdict}"
        + (f"  violated={violated}" if violated else ""))

    return {
        "verdict": verdict,
        "violated": violated,
        "invariants": invariants,
        "probe_pod": probe_rec,
        "evaluated_utc": evaluated_utc,
        "finished_utc": utc_now(),
        "parameters": {
            "deadline_seconds": deadline_seconds,
            "poll_seconds": poll_seconds,
            "tcp_timeout_seconds": tcp_timeout_seconds,
        },
    }

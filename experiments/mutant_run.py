"""Pre-registered wrong-repair mutant run: MS-M01 / MS-M02 / MS-M03.

Protocol: SREMut/PREREGISTRATION_MS_MUTANTS.md
  Section 2  the three mutants and their structural activation criteria
  Section 3  the procedure, fixed in advance
  Section 4  the contract (MS-I1..MS-I5 evaluated, MS-I6 deferred)
  Section 5  the predictions this run tests

Every measurement primitive is imported from `three_state_run` — the driver
behind the thirteen recorded historical runs — so the mutant runs and the
historical runs share provably identical instrumentation. Nothing is copied.

Run under SREGym/.venv/bin/python with cwd=SREGym. Writes nothing into SREGym/.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import signal
import subprocess
import time
import traceback
from pathlib import Path

from sregym.conductor.conductor import Conductor, ConductorConfig

from three_state_run import (
    CONTEXT,
    EXPERIMENTS,
    MIN_ROUNDS_PER_STATE,
    NAMESPACE,
    PINNED_KUBECTL,
    PROBLEM_ID,
    SREGYM_ROOT,
    WORKER,
    dump_state,
    evaluate_in_process,
    k,
    run_worker,
    say,
    utc_now,
    wait_for_rounds,
)

from contract_check import (
    _controller_ref,
    _eligible_endpoints,
    _kjson,
    _pod_is_ready,
    evaluate_contract,
)

PROTOCOL = "sremut-preregistered-mutant-v1"
SCHEMA_VERSION = 1
R1_SECONDS = 60                 # PROTOCOL_G1 rule R1, reused unchanged
ASSERTION_BUDGET_SECONDS = 4.0  # reserved inside R1 for the pre-oracle assertion

MUTANT_IDS = {"M01": "MS-M01", "M02": "MS-M02", "M03": "MS-M03"}
M02_SELECTOR = {"sremut-mutant-backend": "ms-m02"}
M03_TARGET_PORT = 65535

# PREREGISTRATION_MS_MUTANTS.md section 3 step 1
PINNED_KUBECTL_SHA256 = "646d58f6d98ee670a71d9cdffbf6625aeea2849d567f214bc43a35f8ccb7bf70"
PINNED_SREGYM_COMMIT = "ba07faf1a322f9b6d4a279643bb796aa2f36f64b"
PINNED_APPLICATIONS_COMMIT = "2b2f9c6c2e97c44abbfcc44af1cf2f994bbb04f8"
PINNED_ORACLE_SHA256 = "a087fd38399cfca4c2de764dbbab837d6100350ab9f71b89552cafe2c91fca8b"

PROBE_LABEL_SELECTORS = (
    "app.kubernetes.io/name=sremut-challenge",
    "app.kubernetes.io/name=sremut-probe",
)

# Server-managed fields stripped before re-creating the Service. The prompt's
# list is a superset of PREREGISTRATION_MS_MUTANTS.md section 2 (it adds
# metadata.generation and metadata.managedFields, which are equally
# server-managed); the superset is used so the submitted body is accepted by
# `kubectl create`.
STRIP_METADATA = ("uid", "resourceVersion", "creationTimestamp",
                  "generation", "managedFields")
STRIP_SPEC = ("clusterIP", "clusterIPs")


def _sha256_file(path: Path) -> str | None:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False)


# ------------------------------------------------------- mutant body derivation
def _strip_server_managed(captured: dict) -> dict:
    """Copy of the captured Service with server-managed fields removed.

    Helm labels and annotations are preserved, so the diff against the captured
    original really is one field.
    """
    body = copy.deepcopy(captured)
    meta = body.get("metadata") or {}
    for key in STRIP_METADATA:
        meta.pop(key, None)
    body["metadata"] = meta
    spec = body.get("spec") or {}
    for key in STRIP_SPEC:
        spec.pop(key, None)
    body["spec"] = spec
    body.pop("status", None)
    return body


def derive_m01_body(captured: dict) -> None:
    """MS-M01 models an agent that does nothing: no Service is created at all."""
    return None


def derive_m02_body(captured: dict) -> dict:
    """MS-M02: recreate the Service with a selector that matches no pod."""
    body = _strip_server_managed(captured)
    body["spec"]["selector"] = dict(M02_SELECTOR)
    return body


def derive_m03_body(captured: dict) -> dict:
    """MS-M03: recreate the Service with the correct selector, wrong targetPort."""
    body = _strip_server_managed(captured)
    ports = body["spec"].get("ports") or []
    if not ports:
        raise ValueError("captured Service has no spec.ports; cannot derive MS-M03")
    ports[0]["targetPort"] = M03_TARGET_PORT
    body["spec"]["ports"] = ports
    return body


DERIVERS = {"MS-M01": derive_m01_body,
            "MS-M02": derive_m02_body,
            "MS-M03": derive_m03_body}


# ------------------------------------------------------------------- preflight
def preflight() -> dict:
    """PREREGISTRATION_MS_MUTANTS.md section 3 step 1.

    Read-only, except for the one mandated mutation: deleting pods in phase
    Failed (defect D2 — stale wrk2 husks cause false rejections and are
    unrelated to the mutant).
    """
    say("STEP 0: preflight [read-only, except the mandated Failed-pod deletion]")
    rec: dict = {"checked_utc": utc_now(), "checks": {}, "passed": True}

    def _check(name: str, observed, expected):
        ok = observed == expected
        rec["checks"][name] = {"observed": observed, "expected": expected, "ok": ok}
        if not ok:
            rec["passed"] = False
        return ok

    ctx = k("config", "current-context")
    _check("context", ctx.stdout.strip(), CONTEXT)
    _check("namespace", NAMESPACE, "social-network")
    _check("kubectl_sha256", _sha256_file(Path(PINNED_KUBECTL)), PINNED_KUBECTL_SHA256)

    nodes, _ = _kjson("get", "nodes", "-o", "json")
    ready = 0
    for node in (nodes or {}).get("items") or []:
        for cond in (node.get("status") or {}).get("conditions") or []:
            if cond.get("type") == "Ready" and cond.get("status") == "True":
                ready += 1
    _check("ready_nodes", ready, 4)

    def _git(*args: str) -> str:
        p = subprocess.run(["git", "-C", str(SREGYM_ROOT), *args],
                           capture_output=True, text=True)
        return p.stdout.strip()

    _check("sregym_commit", _git("rev-parse", "HEAD"), PINNED_SREGYM_COMMIT)
    sub = _git("submodule", "status")
    rec["checks"]["applications_submodule_raw"] = sub
    _check("applications_commit",
           sub.split()[0].lstrip("+-U") if sub else "", PINNED_APPLICATIONS_COMMIT)
    _check("oracle_sha256",
           _sha256_file(SREGYM_ROOT / "sregym" / "conductor" / "oracles" / "mitigation.py"),
           PINNED_ORACLE_SHA256)

    # The one mandated mutation.
    failed, _ = _kjson("get", "pods", "-n", NAMESPACE,
                       "--field-selector=status.phase=Failed", "-o", "json")
    names = [(p.get("metadata") or {}).get("name")
             for p in (failed or {}).get("items") or []]
    rec["failed_pods_found"] = names
    rec["failed_pods_deleted"] = []
    for name in names:
        say(f"  deleting Failed pod {name} [defect D2, mandated by section 3 step 1]")
        d = k("delete", "pod", name, "-n", NAMESPACE, "--ignore-not-found")
        rec["failed_pods_deleted"].append(
            {"name": name, "exit": d.returncode, "stderr": d.stderr[-500:]})

    say(f"  preflight passed={rec['passed']}  failed_pods_deleted={len(names)}")
    return rec


# --------------------------------------------- the ordering constraint (2.4)
def assert_no_probe_pods(label: str) -> dict:
    """Assert the namespace is clean enough for a faithful oracle reading.

    The stock oracle sweeps EVERY pod in the namespace (mitigation.py:86,95) and
    returns false if any is not Running with all containers ready. A Pending or
    Succeeded probe pod silently converts a true verdict into a false one, which
    would look exactly like a refutation of the central hypothesis. So this is
    asserted, not merely observed, immediately before every oracle invocation.
    """
    rec: dict = {"label": label, "checked_utc": utc_now(),
                 "selectors": {}, "non_running_pods": [], "ok": True}

    for selector in PROBE_LABEL_SELECTORS:
        doc, raw = _kjson("get", "pods", "-n", NAMESPACE, "-l", selector, "-o", "json")
        names = [(p.get("metadata") or {}).get("name")
                 for p in (doc or {}).get("items") or []]
        rec["selectors"][selector] = {"count": len(names), "names": names,
                                      "kubectl": raw}
        if names:
            rec["ok"] = False

    pods, raw = _kjson("get", "pods", "-n", NAMESPACE, "-o", "json")
    rec["all_pods_kubectl"] = raw
    if pods is None:
        rec["ok"] = False
        rec["error"] = "could not list pods"
    else:
        items = pods.get("items") or []
        rec["pod_count"] = len(items)
        for pod in items:
            phase = (pod.get("status") or {}).get("phase")
            if phase != "Running":
                rec["non_running_pods"].append(
                    {"name": (pod.get("metadata") or {}).get("name"), "phase": phase})
        if rec["non_running_pods"]:
            rec["ok"] = False

    say(f"  pre-oracle assertion [{label}]: ok={rec['ok']} "
        f"pods={rec.get('pod_count')} non_running={len(rec['non_running_pods'])}")
    return rec


class ProbePodPresent(RuntimeError):
    """Raised when the pre-oracle assertion fails; aborts without a verdict."""


def guarded_oracle(R: dict, label: str):
    """Run the pre-oracle assertion and refuse to proceed if it fails."""
    rec = assert_no_probe_pods(label)
    R.setdefault("pre_oracle_assertions", []).append(rec)
    if not rec["ok"]:
        found = {sel: info["names"] for sel, info in rec["selectors"].items()}
        raise ProbePodPresent(
            f"pre-oracle assertion failed before {label}: "
            f"non_running={rec['non_running_pods']} probe_pods_found={found}")
    return rec


# ------------------------------------------------------- mutant application
def _kubectl_stdin(payload: str, *args: str) -> subprocess.CompletedProcess:
    """kubectl with a body on stdin. Mirrors three_state_run.k() exactly, which
    cannot pass stdin. Used only for `create -f -`, as the protocol requires."""
    return subprocess.run([PINNED_KUBECTL, "--context", CONTEXT, *args],
                          input=payload, capture_output=True, text=True)


def apply_mutant(mutant_id: str, captured: dict, out: Path) -> dict:
    """Apply the mutant immediately after inject_fault() returns.

    `kubectl create`, never `apply`: create fails loudly if the Service somehow
    already exists, which an apply would silently paper over.
    """
    rec: dict = {"mutant_id": mutant_id, "applied_utc": None, "body": None,
                 "body_sha256": None, "exit": None, "stderr": None, "stdout": None}
    body = DERIVERS[mutant_id](captured)
    if body is None:
        rec["action"] = "NONE"
        rec["note"] = "MS-M01 models an agent that does nothing; no object is created"
        rec["applied_utc"] = utc_now()
        say("  MS-M01: no action taken (the Service stays absent)")
        return rec

    payload = json.dumps(body, indent=2, sort_keys=True) + "\n"
    faulted_dir = out / "faulted"
    faulted_dir.mkdir(parents=True, exist_ok=True)
    path = faulted_dir / "mutant-service-applied.json"
    path.write_text(payload, encoding="utf-8")

    rec["action"] = "CREATE"
    rec["body"] = body
    rec["body_path"] = str(path)
    rec["body_sha256"] = _sha256_text(payload)
    rec["body_canonical_sha256"] = _sha256_text(_canonical(body))

    p = _kubectl_stdin(payload, "create", "-n", NAMESPACE, "-f", "-")
    rec["applied_utc"] = utc_now()
    rec["exit"] = p.returncode
    rec["stdout"] = p.stdout[-2000:]
    rec["stderr"] = p.stderr[-2000:]
    say(f"  {mutant_id} applied via `kubectl create -f -` exit={p.returncode}")
    return rec


# ---------------------------------------------------- structural activation
def verify_activation(mutant_id: str) -> dict:
    """PREREGISTRATION_MS_MUTANTS.md section 2, from a fresh read.

    An API acknowledgement is never treated as observed activation.
    """
    rec: dict = {"mutant_id": mutant_id, "checked_utc": utc_now(),
                 "criteria": {}, "activated": True}

    def _crit(name: str, ok: bool, detail):
        rec["criteria"][name] = {"ok": bool(ok), "detail": detail}
        if not ok:
            rec["activated"] = False

    svc, svc_raw = _kjson("get", "service", "user-service", "-n", NAMESPACE,
                          "--ignore-not-found", "-o", "json")
    rec["service_kubectl"] = svc_raw
    rec["service"] = svc

    if mutant_id == "MS-M01":
        _crit("service_not_found", svc is None,
              {"service_present": svc is not None})
        return rec

    if svc is None:
        _crit("service_present", False, {"service_present": False})
        return rec

    spec = svc.get("spec") or {}
    selector = spec.get("selector")
    ports = spec.get("ports") or []

    slices_doc, sl_raw = _kjson(
        "get", "endpointslices.discovery.k8s.io", "-n", NAMESPACE,
        "-l", "kubernetes.io/service-name=user-service", "-o", "json")
    rec["endpointslices_kubectl"] = sl_raw
    eligible, ineligible = _eligible_endpoints((slices_doc or {}).get("items") or [])
    rec["eligible_endpoints"] = eligible
    rec["ineligible_endpoints"] = ineligible

    if mutant_id == "MS-M02":
        _crit("selector_exact", selector == M02_SELECTOR,
              {"observed": selector, "expected": M02_SELECTOR})
        sel = ",".join(f"{a}={b}" for a, b in sorted(M02_SELECTOR.items()))
        pods_doc, pods_raw = _kjson("get", "pods", "-n", NAMESPACE,
                                    "-l", sel, "-o", "json")
        matched = [(p.get("metadata") or {}).get("name")
                   for p in (pods_doc or {}).get("items") or []]
        _crit("zero_pods_match_selector", len(matched) == 0,
              {"selector": sel, "matched": matched, "kubectl": pods_raw})
        _crit("zero_eligible_endpoints", len(eligible) == 0,
              {"eligible_count": len(eligible)})
        return rec

    # MS-M03
    _crit("selector_exact", selector == {"service": "user-service"},
          {"observed": selector, "expected": {"service": "user-service"}})
    port0 = ports[0] if ports else {}
    _crit("port_9090", port0.get("port") == 9090, {"observed": port0.get("port")})
    _crit("target_port_65535", port0.get("targetPort") == M03_TARGET_PORT,
          {"observed": port0.get("targetPort")})

    pods_doc, pods_raw = _kjson("get", "pods", "-n", NAMESPACE, "-o", "json")
    rs_doc, rs_raw = _kjson("get", "replicasets", "-n", NAMESPACE, "-o", "json")
    pods_by_ip = {}
    for pod in (pods_doc or {}).get("items") or []:
        ip = (pod.get("status") or {}).get("podIP")
        if ip:
            pods_by_ip[ip] = pod
    rs_by_name = {(r.get("metadata") or {}).get("name"): r
                  for r in (rs_doc or {}).get("items") or []}

    backed = []
    for ep in eligible:
        pod = pods_by_ip.get(ep["address"])
        if pod is None or not _pod_is_ready(pod):
            continue
        rs_ref = _controller_ref(pod, "ReplicaSet")
        if rs_ref is None:
            continue
        rs = rs_by_name.get(rs_ref.get("name"))
        if rs is None:
            continue
        dep_ref = _controller_ref(rs, "Deployment")
        if dep_ref and dep_ref.get("name") == "user-service":
            backed.append({"address": ep["address"],
                           "pod": (pod.get("metadata") or {}).get("name")})
    _crit("at_least_one_backed_endpoint", len(backed) >= 1,
          {"backed": backed, "eligible_count": len(eligible),
           "pods_kubectl": pods_raw, "replicasets_kubectl": rs_raw})
    return rec


# ------------------------------------------------------------------------ main
def main() -> int:
    ap = argparse.ArgumentParser(
        description="Pre-registered wrong-repair mutant run (MS-M01/M02/M03)")
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--mutant", required=True, choices=sorted(MUTANT_IDS))
    args = ap.parse_args()

    mutant_id = MUTANT_IDS[args.mutant]
    out = EXPERIMENTS / args.run_id
    out.mkdir(parents=True, exist_ok=True)

    here = Path(__file__).resolve()
    R: dict = {
        "schema_version": SCHEMA_VERSION,
        "protocol": PROTOCOL,
        "protocol_doc": "SREMut/PREREGISTRATION_MS_MUTANTS.md",
        "run_id": args.run_id,
        "mutant_id": mutant_id,
        "problem_id": PROBLEM_ID,
        "namespace": NAMESPACE,
        "driver": "mutant_run.py",
        "driver_sha256": _sha256_file(here),
        "contract_check_sha256": _sha256_file(here.parent / "contract_check.py"),
        "three_state_run_sha256": _sha256_file(here.parent / "three_state_run.py"),
        "worker_sha256": _sha256_file(WORKER),
        "min_rounds_per_state": MIN_ROUNDS_PER_STATE,
        "r1_seconds_target": R1_SECONDS,
        "agent_action_between_injection_and_evaluation": mutant_id,
        "started_at": utc_now(),
        "status": "RUNNING",
    }
    overall = time.monotonic()
    sampler = None
    problem = None

    try:
        R["preflight"] = preflight()

        # 1. sampler -----------------------------------------------------------
        say(f"STEP 1: starting state sampler on namespace '{NAMESPACE}'")
        sampler = subprocess.Popen(
            [str(EXPERIMENTS / "sample_state.sh"), str(out / "samples.jsonl"), NAMESPACE],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True)
        R["sampler_pid"] = sampler.pid
        R["sampler_namespace"] = NAMESPACE
        say(f"  sampler PID {sampler.pid}")
        time.sleep(3)

        # 2. deploy ------------------------------------------------------------
        say("STEP 2: Conductor / fix_kubernetes / undeploy_app / deploy_app  [MUTATING]")
        conductor = Conductor(config=ConductorConfig(deploy_loki=False,
                                                     enable_noise=False))
        conductor.problem_id = PROBLEM_ID
        conductor.problem = conductor.problems.get_problem_instance(conductor.problem_id)
        conductor.app = conductor.problem.app
        problem = conductor.problem
        R["mitigation_oracle_class"] = type(problem.mitigation_oracle).__name__
        t0 = time.monotonic(); R["deploy_started_utc"] = utc_now()
        conductor.fix_kubernetes(); conductor.undeploy_app(); conductor.deploy_app()
        R["deploy_finished_utc"] = utc_now()
        R["deployment_seconds"] = round(time.monotonic() - t0, 3)
        say(f"  deploy complete in {R['deployment_seconds']}s")

        say("STEP 2b: wait_for_ready(max_wait=600)")
        t0 = time.monotonic()
        problem.kubectl.wait_for_ready(namespace=NAMESPACE, max_wait=600)
        R["stabilization_seconds"] = round(time.monotonic() - t0, 3)
        say(f"  stable in {R['stabilization_seconds']}s")

        # 3. healthy workload window  [R2] -------------------------------------
        say("STEP 3: healthy workload window  [R2]")
        R["workload_healthy"] = wait_for_rounds(None, MIN_ROUNDS_PER_STATE, "HEALTHY")

        # 4. capture_baseline --------------------------------------------------
        say("STEP 4: capture_baseline()   [conductor.py:227]")
        problem.mitigation_oracle.capture_baseline()
        baseline = dict(problem.mitigation_oracle.replica_count)
        R["captured_replica_baseline"] = baseline
        R["captured_replica_baseline_count"] = len(baseline)
        R["captured_replica_baseline_sha256"] = _sha256_text(_canonical(baseline))
        (out / "replica-baseline.json").write_text(
            json.dumps(baseline, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        say(f"  captured {len(baseline)} deployments")

        # 5. healthy oracles + gate --------------------------------------------
        say("STEP 5: HEALTHY oracles")
        guarded_oracle(R, "HEALTHY/in_process")
        R["healthy_in_process"] = evaluate_in_process(problem, "HEALTHY")
        guarded_oracle(R, "HEALTHY/worker")
        R["healthy_worker"] = run_worker(out, "HEALTHY", baseline)

        ip_ok = R["healthy_in_process"]["raw_verdict"].get("success") is True
        wk_ok = R["healthy_worker"]["returned_boolean"] is True
        wl_ok = R["workload_healthy"]["total_non2xx"] == 0
        R["healthy_gate"] = {"in_process_true": ip_ok, "worker_true": wk_ok,
                             "zero_non2xx": wl_ok,
                             "healthy_non2xx": R["workload_healthy"]["total_non2xx"],
                             "passed": bool(ip_ok and wk_ok and wl_ok)}
        if not R["healthy_gate"]["passed"]:
            R["status"] = "STOPPED_AT_HEALTHY_GATE"
            say(f"!!! HEALTHY GATE FAILED: {R['healthy_gate']}. NOT INJECTING. STOP.")
            return 2
        say("  GATE PASSED (both oracles True, zero non-2xx). Proceeding.")

        # 6. capture the live Service ------------------------------------------
        say("STEP 6: capturing the live Service/user-service")
        svc_proc = k("get", "service", "user-service", "-n", NAMESPACE, "-o", "json")
        if svc_proc.returncode != 0 or not svc_proc.stdout.strip():
            raise RuntimeError(
                f"could not capture Service/user-service: exit={svc_proc.returncode} "
                f"stderr={svc_proc.stderr[-500:]}")
        svc_path = out / "captured-user-service.json"
        svc_path.write_text(svc_proc.stdout, encoding="utf-8")
        captured_service = json.loads(svc_proc.stdout)
        R["captured_service_path"] = str(svc_path)
        R["captured_service_sha256"] = _sha256_text(svc_proc.stdout)
        R["captured_service"] = captured_service
        say(f"  captured Service sha256={R['captured_service_sha256'][:16]}…")

        # 7. healthy contract + dump -------------------------------------------
        say("STEP 7: HEALTHY contract evaluation")
        R["contract_healthy"] = evaluate_contract(
            run_id=args.run_id, mutant_id=mutant_id, state="healthy",
            replica_baseline=baseline, workload_window=R["workload_healthy"])
        R["dump_healthy"] = dump_state(out / "healthy", "healthy")

        # 8. inject -------------------------------------------------------------
        say("STEP 8: inject_fault()   [conductor.py:229]  [MUTATING]")
        t_inject = time.monotonic(); R["injection_started_utc"] = utc_now()
        problem.inject_fault()
        t_inject_done = time.monotonic()
        R["injection_finished_utc"] = utc_now()
        R["injection_seconds"] = round(t_inject_done - t_inject, 3)
        R["fault_injected_flag"] = problem.fault_injected
        say(f"  injection complete in {R['injection_seconds']}s")

        # 9. apply the mutant ---------------------------------------------------
        say(f"STEP 9: applying mutant {mutant_id}  [MUTATING]")
        mut = apply_mutant(mutant_id, captured_service, out)
        if mutant_id == "MS-M01":
            mut["applied_utc"] = R["injection_finished_utc"]
        R["mutant"] = mut
        R["mutant_applied_utc"] = mut["applied_utc"]

        # 10/11. wait to exactly R1, verifying activation inside the window -----
        say("STEP 11: verifying mutant activation structurally  [section 2]")
        R["activation"] = verify_activation(mutant_id)
        if not R["activation"]["activated"]:
            R["status"] = "MUTANT_ACTIVATION_FAILURE"
            say(f"!!! ACTIVATION FAILED: {json.dumps(R['activation']['criteria'], default=str)}")
            say("  recovering without invoking the oracle")
            R["recovery_started_utc"] = utc_now()
            problem.recover_fault()
            R["recovery_finished_utc"] = utc_now()
            return 3

        say(f"STEP 10: holding to R1 = {R1_SECONDS}s from injection")
        target = t_inject_done + R1_SECONDS
        gap = target - time.monotonic() - ASSERTION_BUDGET_SECONDS
        if gap > 0:
            time.sleep(gap)
        guarded_oracle(R, "FAULTED/in_process")
        remaining = target - time.monotonic()
        if remaining > 0:
            time.sleep(remaining)

        # 12. faulted oracles ---------------------------------------------------
        say("STEP 12: FAULTED oracles")
        R["r1_interval_seconds_achieved"] = round(time.monotonic() - t_inject_done, 3)
        R["faulted_in_process"] = evaluate_in_process(problem, "FAULTED")
        guarded_oracle(R, "FAULTED/worker")
        R["faulted_worker"] = run_worker(out, "FAULTED", baseline)
        say(f"  R1 achieved = {R['r1_interval_seconds_achieved']}s")

        # 13/14. faulted workload window, then the contract that consumes it ----
        say("STEP 14: faulted workload window  [R2]")
        R["workload_faulted"] = wait_for_rounds(
            str(R["mutant_applied_utc"]).replace("+00:00", "Z"),
            MIN_ROUNDS_PER_STATE, "FAULTED")

        say("STEP 13: FAULTED contract evaluation")
        R["contract_faulted"] = evaluate_contract(
            run_id=args.run_id, mutant_id=mutant_id, state="faulted",
            replica_baseline=baseline, workload_window=R["workload_faulted"])

        # 15. faulted dump ------------------------------------------------------
        R["dump_faulted"] = dump_state(out / "faulted", "faulted")

        # 16. recover -----------------------------------------------------------
        say("STEP 16: recover_fault()   [missing_service.py:52]  [MUTATING]")
        say("  the driver deletes no Service of its own; SREGym's "
            "recover_missing_service removes the mutant (inject_virtual.py:309-320)")
        t0 = time.monotonic(); R["recovery_started_utc"] = utc_now()
        problem.recover_fault()
        R["recovery_finished_utc"] = utc_now()
        R["recovery_seconds"] = round(time.monotonic() - t0, 3)
        R["fault_injected_flag_after_recovery"] = problem.fault_injected
        say(f"  recovery complete in {R['recovery_seconds']}s")

        # 17. restored ----------------------------------------------------------
        say("STEP 17: restored workload window  [R2]")
        R["workload_restored"] = wait_for_rounds(
            R["recovery_finished_utc"].replace("+00:00", "Z"),
            MIN_ROUNDS_PER_STATE, "RESTORED")

        say("STEP 17b: RESTORED oracles")
        guarded_oracle(R, "RESTORED/in_process")
        R["restored_in_process"] = evaluate_in_process(problem, "RESTORED")
        guarded_oracle(R, "RESTORED/worker")
        R["restored_worker"] = run_worker(out, "RESTORED", baseline)

        say("STEP 17c: RESTORED contract evaluation")
        R["contract_restored"] = evaluate_contract(
            run_id=args.run_id, mutant_id=mutant_id, state="restored",
            replica_baseline=baseline, workload_window=R["workload_restored"])
        R["dump_restored"] = dump_state(out / "restored", "restored")

        R["status"] = "COMPLETE"
        R["teardown_performed"] = False

    except ProbePodPresent as violation:
        R["status"] = "PROTOCOL_VIOLATION_PROBE_POD_PRESENT"
        R["error"] = {"type": "ProbePodPresent", "message": str(violation)}
        say(f"!!! {R['status']}: {violation}")
    except Exception as error:  # noqa: BLE001
        R["status"] = "DRIVER_ABORTED"
        R["error"] = {"type": type(error).__name__, "message": str(error),
                      "traceback": traceback.format_exc()}
        say(f"!!! FAILED: {type(error).__name__}: {error}")
        traceback.print_exc()
    finally:
        # 18. stop the sampler, write the record -------------------------------
        if sampler is not None:
            say("STEP 18: stopping sampler")
            try:
                os.killpg(os.getpgid(sampler.pid), signal.SIGTERM)
                time.sleep(2)
                if sampler.poll() is None:
                    os.killpg(os.getpgid(sampler.pid), signal.SIGKILL)
            except Exception:  # noqa: BLE001
                pass
        R["finished_at"] = utc_now()
        R["total_seconds"] = round(time.monotonic() - overall, 3)
        (out / "mutant-run.json").write_text(
            json.dumps(R, indent=2, sort_keys=True, default=str) + "\n",
            encoding="utf-8")
        say(f"mutant-run.json -> {out / 'mutant-run.json'}  status={R['status']}")
    return 0 if R["status"] == "COMPLETE" else 1


if __name__ == "__main__":
    raise SystemExit(main())

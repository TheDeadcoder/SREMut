"""Execute the never-tested prediction in analysis/PR_PLAN.md.

Protocol: SREMut/PREREGISTRATION_FIX_ORACLE.md
  §2  the four oracle configurations O1..O4
  §3  the predicted 4x3 verdict matrix
  §5  what would falsify what
  §6  the predicate ordering, established from source before the freeze

`PR_PLAN.md` predicts that attaching `ServiceEndpointMitigationOracle` to
`MissingService` rejects *correct repairs*, because `_run_connectivity_probe` reads
`self.problem.expected_service_port` at `service_endpoint_mitigation.py:65`, that
attribute is absent on `MissingService`, and the broad handler at `:178` turns the
resulting `AttributeError` into `{"success": False}`. This driver executes that.

SREGym is NOT patched. `expected_service_port` is set on the problem *object* in this
process for O3/O4 and deleted afterwards, reproducing the proposed patch's object state
at evaluate time without touching a byte of the submodule.

Every measurement primitive is imported from the frozen instruments — `three_state_run`
(thirteen historical runs) and `mutant_run` (nine attested mutant records). Neither is
modified; both are hash-bound to records already committed.

Run under SREGym/.venv/bin/python with cwd=SREGym. Writes nothing into SREGym/.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import signal
import subprocess
import time
import traceback
from pathlib import Path

from sregym.conductor.conductor import Conductor, ConductorConfig
from sregym.conductor.oracles.compound import CompoundedOracle
from sregym.conductor.oracles.mitigation import MitigationOracle
from sregym.conductor.oracles.service_endpoint_mitigation import (
    ServiceEndpointMitigationOracle,
)

from three_state_run import (
    EXPERIMENTS,
    MIN_ROUNDS_PER_STATE,
    NAMESPACE,
    PROBLEM_ID,
    SREGYM_ROOT,
    dump_state,
    k,
    say,
    utc_now,
    wait_for_rounds,
)

import contract_check
from contract_check import evaluate_contract
from mutant_run import (
    ASSERTION_BUDGET_SECONDS,
    MUTANT_IDS,
    R1_SECONDS,
    ProbePodPresent,
    _sha256_file,
    _sha256_text,
    _canonical,
    apply_mutant,
    attempt_restoration,
    guarded_oracle,
    preflight,
    verify_activation_polled,
)

PROTOCOL = "sremut-fix-oracle-v1"
SCHEMA_VERSION = 1
EXPECTED_SERVICE_PORT = 9090          # wrong_service_selector.py:25, social_network
ATTR = "expected_service_port"
STATES = ("healthy", "faulted", "restored")
CONFIGS = ("O1", "O2", "O3", "O4")

PINNED_MODULES = {
    "service_endpoint_mitigation.py":
        SREGYM_ROOT / "sregym/conductor/oracles/service_endpoint_mitigation.py",
    "compound.py": SREGYM_ROOT / "sregym/conductor/oracles/compound.py",
    "mitigation.py": SREGYM_ROOT / "sregym/conductor/oracles/mitigation.py",
    "missing_service.py": SREGYM_ROOT / "sregym/conductor/problems/missing_service.py",
}


# ------------------------------------------------------------------ attribute
def _observe_attr(problem) -> str | int:
    """`expected_service_port` as it stands immediately before an evaluation.

    Recorded for every configuration so the O2/O3 distinction is evidenced in the
    record rather than asserted in prose.
    """
    return getattr(problem, ATTR, "<ABSENT>")


def _set_port(problem) -> None:
    setattr(problem, ATTR, EXPECTED_SERVICE_PORT)


def _clear_port(problem) -> None:
    """Delete the attribute so a later O2 cannot inherit it."""
    if hasattr(problem, ATTR):
        delattr(problem, ATTR)


# ------------------------------------------------------------------ diagnosis
def _direct_probe_call(problem) -> dict:
    """Call `_run_connectivity_probe()` UNGUARDED and record what it raises.

    `evaluate()`'s broad `except Exception` at service_endpoint_mitigation.py:178
    destroys exactly the causal information this study needs: an AttributeError on
    `expected_service_port` and a genuine connectivity failure both become
    `{"success": False}`. This call is DIAGNOSTIC ONLY. Its result never influences a
    verdict; every verdict in this record comes from the unmodified `evaluate()`.
    """
    rec: dict = {
        "method": "ServiceEndpointMitigationOracle._run_connectivity_probe",
        "reads_attribute_at": "service_endpoint_mitigation.py:65",
        "attribute_before_call": _observe_attr(problem),
        "returned": None,
        "exception_type": None,
        "exception_message": None,
    }
    oracle = ServiceEndpointMitigationOracle(problem=problem)
    t0 = time.monotonic()
    try:
        rec["returned"] = oracle._run_connectivity_probe()
    except Exception as exc:  # noqa: BLE001 - capturing the exception IS the measurement
        rec["exception_type"] = type(exc).__name__
        rec["exception_message"] = str(exc)
    rec["elapsed_seconds"] = round(time.monotonic() - t0, 3)
    say(f"      direct _run_connectivity_probe: returned={rec['returned']!r} "
        f"exc={rec['exception_type']}: {rec['exception_message']}")
    return rec


# -------------------------------------------------------------- the four O's
def _strict_bool_of(raw, where: str):
    """The verdict as an actual JSON boolean, or None when the shape is unexpected.

    Never coerced: `bool({"success": False})` is True, which is exactly the failure
    this refuses to commit.
    """
    if not isinstance(raw, dict) or "success" not in raw:
        say(f"      !! {where}: unexpected result shape {raw!r}")
        return None
    v = raw["success"]
    if v is True or v is False:
        return v
    say(f"      !! {where}: 'success' is not a JSON boolean: {v!r}")
    return None


def _evaluate_one(problem, config: str, state: str) -> dict:
    """Evaluate one oracle configuration. The verdict comes from evaluate(), always."""
    say(f"    {config} [{state}] evaluating")
    rec: dict = {"config": config, "state": state, "started_utc": utc_now()}

    if config == "O1":
        _clear_port(problem)
        rec["attribute_before_call"] = _observe_attr(problem)
        oracle = MitigationOracle(problem=problem)
        oracle.capture_baseline()
    elif config == "O2":
        _clear_port(problem)
        rec["attribute_before_call"] = _observe_attr(problem)
        # PREREGISTRATION_FIX_ORACLE.md §7: absence is asserted and recorded, so a
        # leftover attribute from an earlier O3/O4 cannot silently turn O2 into O3.
        rec["absence_asserted"] = rec["attribute_before_call"] == "<ABSENT>"
        if not rec["absence_asserted"]:
            raise RuntimeError(
                f"O2 [{state}]: {ATTR} is present ({rec['attribute_before_call']!r}); "
                f"O2 must run with the attribute absent")
        oracle = ServiceEndpointMitigationOracle(problem=problem)
    elif config == "O3":
        _set_port(problem)
        rec["attribute_before_call"] = _observe_attr(problem)
        oracle = ServiceEndpointMitigationOracle(problem=problem)
    elif config == "O4":
        _set_port(problem)
        rec["attribute_before_call"] = _observe_attr(problem)
        mit = MitigationOracle(problem=problem)
        mit.capture_baseline()
        oracle = CompoundedOracle(problem, mit,
                                  ServiceEndpointMitigationOracle(problem=problem))
    else:
        raise ValueError(config)

    t0 = time.monotonic()
    try:
        raw = oracle.evaluate()
        rec["raised"] = None
    except Exception as exc:  # noqa: BLE001 - conductor.py:269-271 shape
        traceback.print_exc()
        raw = {"success": False, "error": f"{type(exc).__name__}: {exc}"}
        rec["raised"] = {"type": type(exc).__name__, "message": str(exc)}
    rec["elapsed_seconds"] = round(time.monotonic() - t0, 3)
    rec["returned_object"] = raw
    rec["verdict"] = _strict_bool_of(raw, f"{config}/{state}")
    rec["finished_utc"] = utc_now()

    # Diagnostic only, and only for the configurations that reach :65.
    if config in ("O2", "O3", "O4"):
        rec["direct_probe_call"] = _direct_probe_call(problem)

    _clear_port(problem)
    rec["attribute_after_cleanup"] = _observe_attr(problem)
    say(f"    {config} [{state}] verdict={rec['verdict']!r} "
        f"attr_before={rec['attribute_before_call']!r} ({rec['elapsed_seconds']}s)")
    return rec


def evaluate_all(R: dict, problem, state: str) -> dict:
    """O1..O4 for one state, each preceded by the probe-pod assertion."""
    say(f"  === oracle configurations [{state}] ===")
    out = {}
    for config in CONFIGS:
        guarded_oracle(R, f"{state.upper()}/{config}")
        out[config] = _evaluate_one(problem, config, state)
    return out


# ------------------------------------------------------------------------ main
def main() -> int:
    ap = argparse.ArgumentParser(
        description="Execute the PR_PLAN.md fix prediction (O1..O4 x three states)")
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--mutant", required=True, choices=sorted(MUTANT_IDS))
    args = ap.parse_args()

    mutant_id = MUTANT_IDS[args.mutant]
    out = EXPERIMENTS / args.run_id
    out.mkdir(parents=True, exist_ok=True)
    here = Path(__file__).resolve()

    import kubernetes  # noqa: PLC0415 - recorded as runtime evidence

    R: dict = {
        "schema_version": SCHEMA_VERSION,
        "protocol": PROTOCOL,
        "protocol_doc": "SREMut/PREREGISTRATION_FIX_ORACLE.md",
        "run_id": args.run_id,
        "mutant_id": mutant_id,
        "problem_id": PROBLEM_ID,
        "namespace": NAMESPACE,
        "driver": "fix_oracle_run.py",
        "driver_sha256": _sha256_file(here),
        "mutant_run_sha256": _sha256_file(here.parent / "mutant_run.py"),
        "contract_check_sha256": _sha256_file(here.parent / "contract_check.py"),
        "three_state_run_sha256": _sha256_file(here.parent / "three_state_run.py"),
        "pinned_module_sha256": {n: _sha256_file(p) for n, p in PINNED_MODULES.items()},
        "runtime": {
            "python": platform.python_version(),
            "python_implementation": platform.python_implementation(),
            "kubernetes_client": getattr(kubernetes, "__version__", None),
        },
        "expected_service_port_used": EXPECTED_SERVICE_PORT,
        "single_provenance": True,
        "provenance_note": ("in-process only; no isolated worker leg in this study "
                            "(PREREGISTRATION_FIX_ORACLE.md §1)"),
        "min_rounds_per_state": MIN_ROUNDS_PER_STATE,
        "r1_seconds_target": R1_SECONDS,
        "started_at": utc_now(),
        "status": "RUNNING",
        "restoration_attempted": False,
        "restoration_outcome": None,
        "any_non_running_pod_at_an_oracle_call": False,
        "mutation_may_have_occurred": False,
    }
    overall = time.monotonic()
    sampler = None
    problem = None
    recovery_completed = False

    try:
        R["preflight"] = preflight()

        say(f"STEP 1: starting state sampler on namespace '{NAMESPACE}'")
        sampler = subprocess.Popen(
            [str(EXPERIMENTS / "sample_state.sh"), str(out / "samples.jsonl"), NAMESPACE],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True)
        R["sampler_pid"] = sampler.pid
        time.sleep(3)

        say("STEP 2: Conductor / fix_kubernetes / undeploy_app / deploy_app  [MUTATING]")
        conductor = Conductor(config=ConductorConfig(deploy_loki=False,
                                                     enable_noise=False))
        conductor.problem_id = PROBLEM_ID
        conductor.problem = conductor.problems.get_problem_instance(conductor.problem_id)
        conductor.app = conductor.problem.app
        problem = conductor.problem
        R["mitigation_oracle_class"] = type(problem.mitigation_oracle).__name__
        R["expected_service_port_present_on_problem_at_construction"] = hasattr(
            problem, ATTR)
        t0 = time.monotonic(); R["deploy_started_utc"] = utc_now()
        conductor.fix_kubernetes(); conductor.undeploy_app(); conductor.deploy_app()
        R["deploy_finished_utc"] = utc_now()
        R["deployment_seconds"] = round(time.monotonic() - t0, 3)

        say("STEP 2b: wait_for_ready(max_wait=600)")
        t0 = time.monotonic()
        problem.kubectl.wait_for_ready(namespace=NAMESPACE, max_wait=600)
        R["stabilization_seconds"] = round(time.monotonic() - t0, 3)

        say("STEP 3: healthy workload window  [R2]")
        R["workload_healthy"] = wait_for_rounds(None, MIN_ROUNDS_PER_STATE, "HEALTHY")

        say("STEP 4: capture_baseline()")
        problem.mitigation_oracle.capture_baseline()
        baseline = dict(problem.mitigation_oracle.replica_count)
        R["captured_replica_baseline"] = baseline
        R["captured_replica_baseline_count"] = len(baseline)
        R["captured_replica_baseline_sha256"] = _sha256_text(_canonical(baseline))
        (out / "replica-baseline.json").write_text(
            json.dumps(baseline, indent=2, sort_keys=True) + "\n", encoding="utf-8")

        # healthy gate, same shape as mutant_run.py but single-provenance
        guarded_oracle(R, "HEALTHY/gate")
        gate_raw = problem.mitigation_oracle.evaluate()
        ip_ok = gate_raw.get("success") is True
        wl_ok = R["workload_healthy"]["total_non2xx"] == 0
        R["healthy_gate"] = {"in_process_true": ip_ok, "zero_non2xx": wl_ok,
                             "raw_verdict": gate_raw,
                             "healthy_non2xx": R["workload_healthy"]["total_non2xx"],
                             "passed": bool(ip_ok and wl_ok)}
        if not R["healthy_gate"]["passed"]:
            R["status"] = "STOPPED_AT_HEALTHY_GATE"
            say(f"!!! HEALTHY GATE FAILED: {R['healthy_gate']}. NOT INJECTING. STOP.")
            return 2
        say("  GATE PASSED. Proceeding.")

        say("STEP 5: capturing the live Service/user-service")
        svc = k("get", "service", "user-service", "-n", NAMESPACE, "-o", "json")
        if svc.returncode != 0 or not svc.stdout.strip():
            raise RuntimeError(f"could not capture Service: {svc.stderr[-400:]}")
        (out / "captured-user-service.json").write_text(svc.stdout, encoding="utf-8")
        captured_service = json.loads(svc.stdout)
        R["captured_service_sha256"] = _sha256_text(svc.stdout)

        say("STEP 6: HEALTHY oracle configurations + contract")
        R["oracles_healthy"] = evaluate_all(R, problem, "healthy")
        R["contract_healthy"] = evaluate_contract(
            run_id=args.run_id, mutant_id=mutant_id, state="healthy",
            replica_baseline=baseline, workload_window=R["workload_healthy"])
        if R["contract_healthy"]["verdict"] == "INFRASTRUCTURE_FAILURE":
            R["status"] = "INFRASTRUCTURE_FAILURE"
            say("!!! INFRASTRUCTURE_FAILURE before injection. NOT INJECTING. STOP.")
            return 4
        R["dump_healthy"] = dump_state(out / "healthy", "healthy")

        say("STEP 7: inject_fault()  [MUTATING]")
        t_inj = time.monotonic(); R["injection_started_utc"] = utc_now()
        R["mutation_may_have_occurred"] = True
        problem.inject_fault()
        t_inject_done = time.monotonic()
        R["injection_finished_utc"] = utc_now()
        R["injection_seconds"] = round(t_inject_done - t_inj, 3)
        R["fault_injected_flag"] = problem.fault_injected

        say(f"STEP 8: applying mutant {mutant_id}  [MUTATING]")
        mut = apply_mutant(mutant_id, captured_service, out)
        if mutant_id == "MS-M01":
            mut["applied_utc"] = R["injection_finished_utc"]
        R["mutant"] = mut
        R["mutant_applied_utc"] = mut["applied_utc"]

        say("STEP 9: verifying mutant activation  [polled]")
        R["activation_after_apply"] = verify_activation_polled(mutant_id)
        if not R["activation_after_apply"]["activated"]:
            R["status"] = "MUTANT_ACTIVATION_FAILURE"
            say("!!! ACTIVATION FAILED. Restoring without evaluating.")
            attempt_restoration(R, problem, "MUTANT_ACTIVATION_FAILURE")
            if R["status"] == "RESTORATION_FAILURE":
                return 5
            R["status"] = "MUTANT_ACTIVATION_FAILURE"
            return 3

        say(f"STEP 10: holding to R1 = {R1_SECONDS}s from injection")
        target = t_inject_done + R1_SECONDS
        gap = target - time.monotonic() - ASSERTION_BUDGET_SECONDS
        if gap > 0:
            time.sleep(gap)
        R["activation_before_oracle"] = None
        remaining = target - time.monotonic()
        if remaining > 0:
            time.sleep(remaining)
        R["r1_interval_seconds_achieved"] = round(time.monotonic() - t_inject_done, 3)

        say("STEP 11: FAULTED oracle configurations")
        R["oracles_faulted"] = evaluate_all(R, problem, "faulted")

        say("STEP 12: faulted workload window  [R2]")
        R["workload_faulted"] = wait_for_rounds(
            str(R["mutant_applied_utc"]).replace("+00:00", "Z"),
            MIN_ROUNDS_PER_STATE, "FAULTED")

        say("STEP 13: FAULTED contract evaluation")
        R["contract_faulted"] = evaluate_contract(
            run_id=args.run_id, mutant_id=mutant_id, state="faulted",
            replica_baseline=baseline, workload_window=R["workload_faulted"])
        if R["contract_faulted"]["verdict"] == "INFRASTRUCTURE_FAILURE":
            R["status"] = "INFRASTRUCTURE_FAILURE"
            attempt_restoration(R, problem, "INFRASTRUCTURE_FAILURE")
            return 4
        R["dump_faulted"] = dump_state(out / "faulted", "faulted")

        say("STEP 14: recover_fault()  [MUTATING]")
        t0 = time.monotonic(); R["recovery_started_utc"] = utc_now()
        problem.recover_fault()
        R["recovery_finished_utc"] = utc_now()
        R["recovery_seconds"] = round(time.monotonic() - t0, 3)
        R["fault_injected_flag_after_recovery"] = problem.fault_injected
        recovery_completed = True

        say("STEP 15: restored workload window  [R2]")
        R["workload_restored"] = wait_for_rounds(
            R["recovery_finished_utc"].replace("+00:00", "Z"),
            MIN_ROUNDS_PER_STATE, "RESTORED")

        say("STEP 16: RESTORED oracle configurations + contract")
        R["oracles_restored"] = evaluate_all(R, problem, "restored")
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
        if R["mutation_may_have_occurred"] and not recovery_completed:
            attempt_restoration(R, problem, R["status"])
    except BaseException as error:  # noqa: BLE001 - includes KeyboardInterrupt
        R["status"] = "DRIVER_ABORTED"
        R["error"] = {"type": type(error).__name__, "message": str(error),
                      "traceback": traceback.format_exc()}
        say(f"!!! FAILED: {type(error).__name__}: {error}")
        traceback.print_exc()
        if R["mutation_may_have_occurred"] and not recovery_completed:
            attempt_restoration(R, problem, "DRIVER_ABORTED")
    finally:
        if problem is not None:
            _clear_port(problem)
        if sampler is not None:
            say("STEP 17: stopping sampler")
            try:
                os.killpg(os.getpgid(sampler.pid), signal.SIGTERM)
                time.sleep(2)
                if sampler.poll() is None:
                    os.killpg(os.getpgid(sampler.pid), signal.SIGKILL)
            except Exception:  # noqa: BLE001
                pass
        R["finished_at"] = utc_now()
        R["total_seconds"] = round(time.monotonic() - overall, 3)
        (out / "fix-oracle-run.json").write_text(
            json.dumps(R, indent=2, sort_keys=True, default=str) + "\n",
            encoding="utf-8")
        say(f"fix-oracle-run.json -> {out / 'fix-oracle-run.json'}  status={R['status']}")
    return 0 if R["status"] == "COMPLETE" else 1


if __name__ == "__main__":
    raise SystemExit(main())

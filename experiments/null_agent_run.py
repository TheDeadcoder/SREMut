"""G0.2 null-agent experiment - driver (ii).

Reproduces the SREGym conductor's mitigation path exactly, with NO agent action
between fault injection and oracle evaluation.

Ordering mirrors conductor.py: capture_baseline() at 227 -> inject_fault() at 229
-> mitigation_oracle.evaluate() at 268, with the try/except of 269-271 replicated
verbatim (the one enumerated deviation in G02 §C that could change a recorded
outcome).

Setup mirrors harness/baseline_runner.py:84-86, which drove three successful
healthy baselines.

Run under SREGym/.venv/bin/python with cwd=SREGym.
This file lives in SREMut/experiments/ and writes nothing into SREGym/.

STOPS after the treatment evaluation. Does NOT call recover_fault(). Does NOT
tear down. The cluster is left faulted, by design.
"""

import argparse
import hashlib
import json
import subprocess
import sys
import time
import traceback
from datetime import UTC, datetime
from pathlib import Path

from sregym.conductor.conductor import Conductor, ConductorConfig

PINNED_KUBECTL = "/home/sakibbuet2k19/.local/lib/sremut/kubectl/v1.32.0/kubectl"
CONTEXT = "kind-kind"
NAMESPACE = "social-network"
PROBLEM_ID = "missing_service_social_network"


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def say(msg: str) -> None:
    print(f"[{utc_now()}] {msg}", flush=True)


def dump_state(out_dir: Path, label: str) -> dict:
    """Full JSON dumps (T- / T+ anchors). READ-ONLY: every verb is `get`."""
    out_dir.mkdir(parents=True, exist_ok=True)
    targets = {
        "services": ["get", "services", "-n", NAMESPACE, "-o", "json"],
        "pods": ["get", "pods", "-n", NAMESPACE, "-o", "json"],
        "deployments": ["get", "deployments", "-n", NAMESPACE, "-o", "json"],
        "endpointslices": [
            "get", "endpointslices.discovery.k8s.io", "-n", NAMESPACE, "-o", "json",
        ],
        "user-service": [
            "get", "service", "user-service", "-n", NAMESPACE,
            "--ignore-not-found", "-o", "json",
        ],
    }
    manifest = {"label": label, "captured_utc": utc_now(), "files": {}}
    for name, args in targets.items():
        proc = subprocess.run(
            [PINNED_KUBECTL, "--context", CONTEXT, *args],
            capture_output=True, text=True,
        )
        path = out_dir / f"{name}.json"
        path.write_text(proc.stdout, encoding="utf-8")
        manifest["files"][name] = {
            "path": str(path),
            "exit_status": proc.returncode,
            "bytes": len(proc.stdout),
            "sha256": hashlib.sha256(proc.stdout.encode("utf-8")).hexdigest(),
            "stderr": proc.stderr.strip()[:500],
        }
    (out_dir / "dump-manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    say(f"  dumped {label} -> {out_dir}")
    return manifest


def evaluate_like_conductor(problem) -> dict:
    """Replicate conductor.py:267-271 exactly.

        try:
            r = problem.mitigation_oracle.evaluate()
        except Exception as e:
            self.logger.exception(...)
            r = {"success": False, "error": f"{type(e).__name__}: {e}"}

    Returns the RAW dict, never coerced.
    """
    try:
        r = problem.mitigation_oracle.evaluate()
    except Exception as e:  # noqa: BLE001 - deliberate: mirrors conductor.py:269
        traceback.print_exc()
        r = {"success": False, "error": f"{type(e).__name__}: {e}"}
    return r


def timed_evaluation(problem, phase: str) -> dict:
    say(f"=== {phase} ORACLE: evaluate() starting ===")
    started = utc_now()
    started_mono = time.monotonic()
    raw = evaluate_like_conductor(problem)
    finished = utc_now()
    elapsed = round(time.monotonic() - started_mono, 3)
    record = {
        "phase": phase,
        "started_utc": started,
        "finished_utc": finished,
        "elapsed_seconds": elapsed,
        "raw_verdict": raw,
    }
    say(f"=== {phase} ORACLE: raw verdict = {json.dumps(raw, sort_keys=True)} "
        f"({elapsed}s) ===")
    return record


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--run-id", default="g02-run-01")
    args = parser.parse_args()

    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)

    result = {
        "schema_version": 1,
        "protocol": "sremut-g02-null-agent-v1",
        "run_id": args.run_id,
        "problem_id": PROBLEM_ID,
        "driver": "candidate-ii-minimal-driver",
        "agent_action_between_injection_and_evaluation": "NONE",
        "started_at": utc_now(),
        "driver_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "status": "RUNNING",
    }
    overall = time.monotonic()

    try:
        # --- 1. Conductor, mirroring baseline_runner.py:61-72 -------------------
        say("STEP 1: constructing Conductor(deploy_loki=False, enable_noise=False)")
        conductor = Conductor(
            config=ConductorConfig(deploy_loki=False, enable_noise=False)
        )
        conductor.problem_id = PROBLEM_ID
        conductor.problem = conductor.problems.get_problem_instance(
            conductor.problem_id
        )
        conductor.app = conductor.problem.app
        problem = conductor.problem
        result["namespace"] = problem.namespace
        result["mitigation_oracle_class"] = type(problem.mitigation_oracle).__name__
        say(f"  problem={PROBLEM_ID} namespace={problem.namespace} "
            f"oracle={result['mitigation_oracle_class']}")

        # --- 2. fix_kubernetes / undeploy / deploy (baseline_runner.py:84-86) ---
        say("STEP 2: fix_kubernetes(); undeploy_app(); deploy_app()  [CLUSTER-MUTATING]")
        deploy_start = time.monotonic()
        conductor.fix_kubernetes()
        conductor.undeploy_app()
        conductor.deploy_app()
        result["deployment_seconds"] = round(time.monotonic() - deploy_start, 3)
        say(f"  deploy complete in {result['deployment_seconds']}s")

        # --- 3. wait_for_ready (baseline_runner.py:95-98) -----------------------
        say("STEP 3: wait_for_ready(max_wait=600)")
        stab_start = time.monotonic()
        problem.kubectl.wait_for_ready(namespace=NAMESPACE, max_wait=600)
        result["stabilization_seconds"] = round(time.monotonic() - stab_start, 3)
        say(f"  stable in {result['stabilization_seconds']}s")

        # --- 4. capture_baseline (conductor.py:227) -----------------------------
        say("STEP 4: mitigation_oracle.capture_baseline()   [conductor.py:227]")
        problem.mitigation_oracle.capture_baseline()
        replica_count = dict(problem.mitigation_oracle.replica_count)
        result["captured_replica_baseline"] = replica_count
        result["captured_replica_baseline_count"] = len(replica_count)
        (out / "replica-baseline.json").write_text(
            json.dumps(replica_count, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        say(f"  captured {len(replica_count)} deployments")

        # --- 5. HEALTHY CONTROL -------------------------------------------------
        say("STEP 5: HEALTHY CONTROL evaluation")
        control = timed_evaluation(problem, "HEALTHY_CONTROL")
        result["healthy_control"] = control

        # --- 6. GATE ------------------------------------------------------------
        if control["raw_verdict"].get("success") is not True:
            result["status"] = "STOPPED_AT_HEALTHY_CONTROL_GATE"
            result["gate"] = (
                "Healthy control did not return success=True. "
                "Fault injection was NOT performed."
            )
            say("!!! GATE FAILED: healthy control is not True. NOT INJECTING. STOP.")
            return 2
        say("  GATE PASSED: healthy control is True. Proceeding to injection.")

        # --- 7. pre/ dumps ------------------------------------------------------
        say("STEP 7: full JSON dump -> pre/")
        result["dump_pre"] = dump_state(out / "pre", "pre-injection")

        # --- 8. inject_fault (conductor.py:229) ---------------------------------
        say("STEP 8: problem.inject_fault()   [conductor.py:229]  [CLUSTER-MUTATING]")
        inject_start = time.monotonic()
        result["injection_started_utc"] = utc_now()
        problem.inject_fault()
        result["injection_finished_utc"] = utc_now()
        result["injection_seconds"] = round(time.monotonic() - inject_start, 3)
        result["fault_injected_flag"] = problem.fault_injected
        say(f"  injection complete in {result['injection_seconds']}s "
            f"(problem.fault_injected={problem.fault_injected})")

        # --- 9. t-minus/ dumps --------------------------------------------------
        say("STEP 9: full JSON dump -> t-minus/")
        result["dump_t_minus"] = dump_state(out / "t-minus", "t-minus")

        # --- 10. TREATMENT ------------------------------------------------------
        say("STEP 10: TREATMENT evaluation   [conductor.py:268]")
        treatment = timed_evaluation(problem, "TREATMENT")
        result["treatment"] = treatment

        # --- 11. t-plus/ dumps --------------------------------------------------
        say("STEP 11: full JSON dump -> t-plus/")
        result["dump_t_plus"] = dump_state(out / "t-plus", "t-plus")

        # --- 12. STOP -----------------------------------------------------------
        result["status"] = "COMPLETE"
        result["teardown_performed"] = False
        result["recover_fault_called"] = False
        result["cluster_left_faulted"] = True
        say("STEP 12: STOP. recover_fault() NOT called. No teardown. "
            "Cluster left faulted by design.")

    except Exception as error:
        result["status"] = "FAILED"
        result["error"] = {
            "type": type(error).__name__,
            "message": str(error),
            "traceback": traceback.format_exc(),
        }
        say(f"!!! FAILED: {type(error).__name__}: {error}")
        traceback.print_exc()

    finally:
        result["finished_at"] = utc_now()
        result["total_seconds"] = round(time.monotonic() - overall, 3)
        (out / "run-result.json").write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        say(f"run-result.json written -> {out / 'run-result.json'}")

    return 0 if result["status"] == "COMPLETE" else 1


if __name__ == "__main__":
    raise SystemExit(main())

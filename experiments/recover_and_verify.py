"""G0.4 Part B — positive control: restore the Service via SREGym's own path.

Calls problem.recover_fault() (missing_service.py:52) exactly as the conductor
would. Records UTC timestamps, stdout/stderr, and full JSON dumps.

Run under SREGym/.venv/bin/python with cwd=SREGym. Writes nothing into SREGym/.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import subprocess
import sys
import time
import traceback
from datetime import UTC, datetime
from pathlib import Path

from sregym.conductor.problems.registry import ProblemRegistry

PINNED_KUBECTL = "/home/sakibbuet2k19/.local/lib/sremut/kubectl/v1.32.0/kubectl"
CONTEXT = "kind-kind"
NAMESPACE = "social-network"
PROBLEM_ID = "missing_service_social_network"


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def say(m: str) -> None:
    print(f"[{utc_now()}] {m}", flush=True)


def dump_state(out_dir: Path, label: str) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    targets = {
        "services": ["get", "services", "-n", NAMESPACE, "-o", "json"],
        "pods": ["get", "pods", "-n", NAMESPACE, "-o", "json"],
        "deployments": ["get", "deployments", "-n", NAMESPACE, "-o", "json"],
        "endpointslices": ["get", "endpointslices.discovery.k8s.io", "-n", NAMESPACE, "-o", "json"],
        "user-service": ["get", "service", "user-service", "-n", NAMESPACE,
                         "--ignore-not-found", "-o", "json"],
    }
    man = {"label": label, "captured_utc": utc_now(), "files": {}}
    for name, args in targets.items():
        p = subprocess.run([PINNED_KUBECTL, "--context", CONTEXT, *args],
                           capture_output=True, text=True)
        path = out_dir / f"{name}.json"
        path.write_text(p.stdout, encoding="utf-8")
        man["files"][name] = {"exit_status": p.returncode, "bytes": len(p.stdout),
                              "sha256": hashlib.sha256(p.stdout.encode()).hexdigest(),
                              "stderr": p.stderr.strip()[:400]}
    (out_dir / "dump-manifest.json").write_text(
        json.dumps(man, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    say(f"  dumped {label} -> {out_dir}")
    return man


def main() -> int:
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    result = {"schema_version": 1, "protocol": "sremut-g04-positive-control-v1",
              "problem_id": PROBLEM_ID, "started_at": utc_now(),
              "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "status": "RUNNING"}
    try:
        say("constructing problem via ProblemRegistry (SREGym's own path)")
        problem = ProblemRegistry().get_problem_instance(PROBLEM_ID)
        result["namespace"] = problem.namespace
        result["fault_injected_before"] = problem.fault_injected

        say("PRE-RECOVERY dump")
        result["dump_pre_recovery"] = dump_state(out / "pre-recovery", "pre-recovery")

        # ---- B2: recover_fault() ------------------------------------------
        say("=== CALLING problem.recover_fault()  [missing_service.py:52] ===")
        result["recover_started_utc"] = utc_now()
        t0 = time.monotonic()
        so, se = io.StringIO(), io.StringIO()
        raised = None
        try:
            with contextlib.redirect_stdout(so), contextlib.redirect_stderr(se):
                problem.recover_fault()
        except Exception as e:  # noqa: BLE001
            raised = f"{type(e).__name__}: {e}"
            result["recover_traceback"] = traceback.format_exc()
        result["recover_finished_utc"] = utc_now()
        result["recover_seconds"] = round(time.monotonic() - t0, 3)
        result["recover_stdout"] = so.getvalue()
        result["recover_stderr"] = se.getvalue()
        result["recover_raised"] = raised
        result["fault_injected_after"] = problem.fault_injected

        (out / "recover-stdout.txt").write_text(so.getvalue(), encoding="utf-8")
        (out / "recover-stderr.txt").write_text(se.getvalue(), encoding="utf-8")

        say(f"=== recover_fault() returned in {result['recover_seconds']}s "
            f"(raised={raised}, problem.fault_injected={problem.fault_injected}) ===")
        print("---------- recover_fault stdout ----------", flush=True)
        print(so.getvalue(), flush=True)
        print("---------- recover_fault stderr ----------", flush=True)
        print(se.getvalue(), flush=True)

        if raised is not None:
            result["status"] = "RECOVER_RAISED"
            say("!!! recover_fault() RAISED. Stopping. No manual fallback applied.")
            return 2

        # ---- B3: verify ----------------------------------------------------
        say("POST-RECOVERY dump")
        result["dump_post_recovery"] = dump_state(out / "post-recovery", "post-recovery")
        result["status"] = "RECOVER_RETURNED"

    except Exception as error:
        result["status"] = "FAILED"
        result["error"] = {"type": type(error).__name__, "message": str(error),
                           "traceback": traceback.format_exc()}
        say(f"!!! FAILED: {error}")
        traceback.print_exc()
    finally:
        result["finished_at"] = utc_now()
        (out / "recover-result.json").write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        say(f"recover-result.json -> {out / 'recover-result.json'}")
    return 0 if result["status"] == "RECOVER_RETURNED" else 1


if __name__ == "__main__":
    raise SystemExit(main())

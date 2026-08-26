"""G0.4 B5(a) — in-process oracle on the RESTORED state.

Uses the identical 27-entry replica baseline captured pre-injection in g02-run-01,
so HEALTHY / FAULTED / RESTORED are all measured with the same oracle input.
Evaluation is wrapped in the exact try/except of conductor.py:269-271.
"""

from __future__ import annotations

import hashlib, json, sys, time, traceback
from datetime import UTC, datetime
from pathlib import Path

from sregym.conductor.problems.registry import ProblemRegistry

PROBLEM_ID = "missing_service_social_network"


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def main() -> int:
    out = Path(sys.argv[1]); baseline_path = Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    baseline = json.loads(baseline_path.read_text())
    assert len(baseline) == 27, len(baseline)

    problem = ProblemRegistry().get_problem_instance(PROBLEM_ID)
    oracle = problem.mitigation_oracle
    oracle.replica_count = dict(baseline)          # same input as HEALTHY/FAULTED

    started = utc_now(); t0 = time.monotonic()
    # ---- conductor.py:267-271 replicated verbatim ----
    try:
        r = oracle.evaluate()
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        r = {"success": False, "error": f"{type(e).__name__}: {e}"}
    # --------------------------------------------------
    finished = utc_now(); elapsed = round(time.monotonic() - t0, 3)

    rec = {"phase": "RESTORED_IN_PROCESS", "started_utc": started,
           "finished_utc": finished, "elapsed_seconds": elapsed,
           "raw_verdict": r, "baseline_entries": len(baseline),
           "baseline_sha256": hashlib.sha256(
               json.dumps(baseline, sort_keys=True, separators=(",", ":")).encode()
           ).hexdigest()}
    (out / "in-process-oracle.json").write_text(
        json.dumps(rec, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(rec, indent=2, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

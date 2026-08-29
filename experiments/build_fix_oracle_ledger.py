#!/usr/bin/env python3
"""Generate experiments/FIX_ORACLE_LEDGER.json from the three fix-oracle run records.

GENERATED — do not hand-edit.

  regenerate :  python3 experiments/build_fix_oracle_ledger.py
  verify     :  python3 experiments/build_fix_oracle_ledger.py --check

`--check` recomputes the whole ledger in memory and compares it byte-for-byte with the
committed file. It never writes, and exits non-zero on any disagreement.

Scope: the fix-oracle study (`PREREGISTRATION_FIX_ORACLE.md`), three runs executed
2026-08-29, four oracle configurations evaluated in each of three states. This is separate
accounting from MUTANT_LEDGER.json (nine mutant repetitions) and RESULT_LEDGER.json
(thirteen historical runs); neither is touched by this file.

Three determinism rules, all load-bearing for `--check`:

1. **No filesystem metadata.** Every timestamp emitted here is read from the run record.
2. **No implicit coercion of a verdict.** `strict_bool` refuses anything that is not an
   actual JSON boolean. `bool({"success": False})` is `True`, which is precisely the
   mistake this refuses to make.
3. **`predictions_matched` is computed from the grid**, not asserted. The prediction table
   is transcribed once, from PREREGISTRATION_FIX_ORACLE.md §3, and compared cell by cell.

The three records are attested and are never modified by this script. It reads only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

EXP = Path(__file__).resolve().parent
REPO = EXP.parent
SREGYM = REPO.parent / "SREGym"
OUT = EXP / "FIX_ORACLE_LEDGER.json"

PROTOCOL = "sremut-fix-oracle-v1"
RUN_IDS = ("fix-m01-r01", "fix-m02-r01", "fix-m03-r01")
STATES = ("healthy", "faulted", "restored")
CONTROL_STATES = ("healthy", "restored")
CONFIGS = ("O1", "O2", "O3", "O4")
INVARIANTS = ("MS-I1", "MS-I2", "MS-I3", "MS-I4", "MS-I5", "MS-I6")
EXPECTED_PORT = 9090

INSTRUMENT_KEYS = ("driver_sha256", "mutant_run_sha256",
                   "contract_check_sha256", "three_state_run_sha256")
INSTRUMENT_FILES = {
    "driver_sha256": EXP / "fix_oracle_run.py",
    "mutant_run_sha256": EXP / "mutant_run.py",
    "contract_check_sha256": EXP / "contract_check.py",
    "three_state_run_sha256": EXP / "three_state_run.py",
}
PINNED_MODULES = ("service_endpoint_mitigation.py", "compound.py",
                  "mitigation.py", "missing_service.py")

# PREREGISTRATION_FIX_ORACLE.md section 3, transcribed once. Keyed
# (config, state) for the controls and (config, mutant) for the faulted column.
PREDICTED_CONTROL = {"O1": True, "O2": False, "O3": True, "O4": True}
PREDICTED_FAULTED = {"O1": True, "O2": False, "O3": False, "O4": False}


class FixOracleLedgerError(RuntimeError):
    """Raised on any malformed or missing input. Never silently tolerated."""


def strict_bool(value, where: str) -> bool:
    """Return `value` only if it is an actual JSON boolean.

    Rejects "true", "false", 1, 0 and null. `isinstance(True, int)` is True in Python,
    so identity is the only correct test: `1 is True` is False.
    """
    if value is True or value is False:
        return value
    raise FixOracleLedgerError(
        f"{where}: expected a JSON boolean; got {value!r} ({type(value).__name__}). "
        f"Strings, 0/1 and null are refused, never coerced.")


def _require(doc: dict, key: str, where: str):
    if key not in doc:
        raise FixOracleLedgerError(f"{where}: missing required key {key!r}")
    return doc[key]


def _sha256_file(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _rate(non2xx: int, requests: int):
    if requests <= 0:
        return None
    return round(100.0 * non2xx / requests, 4)


def _cell(rec: dict, config: str, where: str) -> dict:
    out = {
        "verdict": strict_bool(_require(rec, "verdict", where), f"{where}.verdict"),
        "attribute_before_call": rec.get("attribute_before_call"),
        "attribute_after_cleanup": rec.get("attribute_after_cleanup"),
        "elapsed_seconds": rec.get("elapsed_seconds"),
    }
    barrier = rec.get("probe_pod_barrier") or {}
    out["barrier"] = {
        "cleared": strict_bool(barrier.get("cleared"), f"{where}.barrier.cleared"),
        "waited_seconds": barrier.get("waited_seconds"),
        "pods_seen": list(barrier.get("pods_seen") or []),
    }
    diag = rec.get("direct_probe_call")
    out["diagnostic_exception_type"] = (diag or {}).get("exception_type")
    if diag is not None:
        out["diagnostic_returned"] = diag.get("returned")

    if config in ("O1", "O4"):
        out["baseline_source"] = rec.get("baseline_source")
        out["baseline_deployment_count"] = rec.get("baseline_deployment_count")

    if config == "O4":
        raw = _require(rec, "returned_object", where)
        children = {c["name"]: strict_bool(c["success"], f"{where}.child.{c['name']}")
                    for c in raw.get("oracles") or []}
        out["children"] = dict(sorted(children.items()))
        out["accuracy"] = raw.get("accuracy")
    return out


def build_record(run_id: str) -> dict:
    path = EXP / run_id / "fix-oracle-run.json"
    if not path.is_file():
        raise FixOracleLedgerError(f"{run_id}: no fix-oracle-run.json at {path}")
    doc = json.loads(path.read_text())
    where = run_id

    protocol = _require(doc, "protocol", where)
    if protocol != PROTOCOL:
        raise FixOracleLedgerError(
            f"{where}: protocol is {protocol!r}, expected {PROTOCOL!r}")

    grid: dict[str, dict] = {}
    for state in STATES:
        block = _require(doc, f"oracles_{state}", where)
        missing = [c for c in CONFIGS if c not in block]
        if missing:
            raise FixOracleLedgerError(f"{where}.oracles_{state}: missing {missing}")
        grid[state] = {c: _cell(block[c], c, f"{where}.{state}.{c}") for c in CONFIGS}

    contract = {}
    for state in STATES:
        c = _require(doc, f"contract_{state}", where)
        inv = _require(c, "invariants", f"{where}.contract_{state}")
        contract[state] = {
            "verdict": c["verdict"],
            "violated": list(c["violated"]),
            "invariants": {i: inv[i]["result"] for i in INVARIANTS},
        }

    workload = {}
    for state in STATES:
        w = _require(doc, f"workload_{state}", where)
        detail = w.get("detail") or []
        req = sum(r["requests"] or 0 for r in detail)
        bad = sum(r["non2xx"] for r in detail)
        if req != w["total_requests"] or bad != w["total_non2xx"]:
            raise FixOracleLedgerError(
                f"{where}.workload_{state}: stored totals disagree with per-round detail")
        workload[state] = {
            "rounds": w["rounds"],
            "total_requests": w["total_requests"],
            "total_non2xx": w["total_non2xx"],
            "non2xx_rate_percent": _rate(w["total_non2xx"], w["total_requests"]),
            "timed_out": strict_bool(w["timed_out"], f"{where}.workload_{state}"),
        }

    mutant = doc.get("mutant") or {}
    activation = doc.get("activation_after_apply") or {}
    return {
        "run_id": _require(doc, "run_id", where),
        "mutant_id": _require(doc, "mutant_id", where),
        "status": _require(doc, "status", where),
        "protocol": protocol,
        "single_provenance": strict_bool(
            _require(doc, "single_provenance", where), f"{where}.single_provenance"),
        "expected_service_port_used": doc.get("expected_service_port_used"),
        "runtime": doc.get("runtime"),
        "total_seconds": doc.get("total_seconds"),
        "phase_seconds": {
            "deployment": doc.get("deployment_seconds"),
            "injection": doc.get("injection_seconds"),
            "recovery": doc.get("recovery_seconds"),
        },
        "r1_interval_seconds_achieved": doc.get("r1_interval_seconds_achieved"),
        "instrument_sha256": {k: doc.get(k) for k in INSTRUMENT_KEYS},
        "pinned_module_sha256": {m: (doc.get("pinned_module_sha256") or {}).get(m)
                                 for m in PINNED_MODULES},
        "mutant_application": {
            "action": mutant.get("action"),
            "body_sha256": mutant.get("body_sha256"),
            "kubectl_exit": mutant.get("exit"),
        },
        "activation": {
            "activated": strict_bool(activation.get("activated"),
                                     f"{where}.activation.activated"),
            "attempts": activation.get("attempts"),
            "elapsed_seconds": activation.get("elapsed_seconds"),
        },
        "grid": grid,
        "contract": contract,
        "workload": workload,
    }


def build_records() -> list[dict]:
    return [build_record(r) for r in RUN_IDS]


def build_ledger(records: list[dict]) -> dict:
    by_status: dict[str, int] = {}
    for r in records:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1

    # 36-cell grid collapsed across runs.
    collapsed: dict[str, dict] = {}
    consistent = True
    for state in STATES:
        collapsed[state] = {}
        for cfg in CONFIGS:
            vals = [r["grid"][state][cfg]["verdict"] for r in records]
            same = len(set(vals)) == 1
            consistent = consistent and same
            collapsed[state][cfg] = {
                "per_run": {r["run_id"]: r["grid"][state][cfg]["verdict"]
                            for r in records},
                "agree": same,
                "value": vals[0] if same else None,
            }

    # predictions_matched, computed from the grid rather than asserted.
    mismatches = []
    for r in records:
        for state in STATES:
            table = PREDICTED_FAULTED if state == "faulted" else PREDICTED_CONTROL
            for cfg in CONFIGS:
                got = r["grid"][state][cfg]["verdict"]
                want = table[cfg]
                if got is not want:
                    mismatches.append({"run_id": r["run_id"], "state": state,
                                       "config": cfg, "predicted": want,
                                       "observed": got})

    barriers = [r["grid"][s][c]["barrier"] for r in records
                for s in STATES for c in CONFIGS]

    instrument = {}
    for key in INSTRUMENT_KEYS:
        seen = sorted({r["instrument_sha256"][key] for r in records})
        instrument[key] = {
            "value": seen[0] if len(seen) == 1 else None,
            "identical_across_runs": len(seen) == 1,
            "matches_committed_file": (
                len(seen) == 1 and seen[0] == _sha256_file(INSTRUMENT_FILES[key])),
        }

    o2_exc = [r["grid"][s]["O2"]["diagnostic_exception_type"]
              for r in records for s in STATES]
    o4_equals = all(
        r["grid"][s]["O4"]["verdict"]
        is (r["grid"][s]["O1"]["verdict"] and r["grid"][s]["O3"]["verdict"])
        for r in records for s in STATES)

    return {
        "GENERATED_BY": "experiments/build_fix_oracle_ledger.py",
        "DO_NOT_HAND_EDIT": True,
        "protocol": PROTOCOL,
        "protocol_doc": "PREREGISTRATION_FIX_ORACLE.md",
        "adjudication_doc": "ADJUDICATION_FIX_ORACLE.md",
        "deviations_doc": "DEVIATIONS_AND_LIMITS.md",
        "n_runs": len(records),
        "runs_by_status": dict(sorted(by_status.items())),
        "all_runs_complete": all(r["status"] == "COMPLETE" for r in records),
        "predicted_table": {"controls": dict(sorted(PREDICTED_CONTROL.items())),
                            "faulted": dict(sorted(PREDICTED_FAULTED.items()))},
        "collapsed_grid": collapsed,
        "grid_internally_consistent": consistent,
        "prediction_mismatches": mismatches,
        "predictions_matched": not mismatches,
        "cells_compared": len(records) * len(STATES) * len(CONFIGS),
        "o4_equals_o1_and_o3": o4_equals,
        "o2_diagnostic_exception_types": dict(sorted(
            {e: o2_exc.count(e) for e in set(o2_exc)}.items(),
            key=lambda kv: str(kv[0]))),
        "barriers": {
            "total": len(barriers),
            "cleared": sum(1 for b in barriers if b["cleared"]),
            "all_cleared": all(b["cleared"] for b in barriers),
            "max_waited_seconds": max(b["waited_seconds"] for b in barriers),
            "min_waited_seconds": min(b["waited_seconds"] for b in barriers),
            "invocations_that_saw_a_pod": sum(1 for b in barriers if b["pods_seen"]),
        },
        "diagnostic_calls": {
            # O2/O3/O4 only: the stock oracle has no connectivity probe.
            "recorded": sum(1 for r in records for s in STATES for c in CONFIGS
                            if "diagnostic_returned" in r["grid"][s][c]),
            "o1_has_none": all("diagnostic_returned" not in r["grid"][s]["O1"]
                               for r in records for s in STATES),
        },
        "instrument_sha256": instrument,
        "contract_faulted_violated": {
            r["mutant_id"]: r["contract"]["faulted"]["violated"] for r in records},
        "runs": records,
    }


def render(led: dict) -> str:
    return json.dumps(led, indent=1, sort_keys=True) + "\n"


def _summarise(led: dict) -> None:
    print(f"runs: {led['n_runs']}  status: "
          f"{json.dumps(led['runs_by_status'], sort_keys=True)}")
    print(f"cells compared: {led['cells_compared']}  "
          f"predictions_matched: {led['predictions_matched']}  "
          f"mismatches: {len(led['prediction_mismatches'])}")
    print(f"grid internally consistent: {led['grid_internally_consistent']}  "
          f"O4 == O1 and O3: {led['o4_equals_o1_and_o3']}")
    print(f"O2 diagnostic exceptions: "
          f"{json.dumps(led['o2_diagnostic_exception_types'], sort_keys=True)}")
    b = led["barriers"]
    print(f"barriers: {b['cleared']}/{b['total']} cleared, "
          f"waited {b['min_waited_seconds']}-{b['max_waited_seconds']}s, "
          f"saw a pod {b['invocations_that_saw_a_pod']} times")
    for state, row in led["collapsed_grid"].items():
        print(f"  {state:9} " + "  ".join(
            f"{c}={row[c]['value']}" for c in ("O1", "O2", "O3", "O4")))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Build (or verify) experiments/FIX_ORACLE_LEDGER.json from the "
                    "three fix-oracle run records on disk.")
    ap.add_argument("--check", action="store_true",
                    help="Recompute the ledger in memory and compare it byte-for-byte "
                         "with the committed FIX_ORACLE_LEDGER.json. Writes nothing.")
    args = ap.parse_args(argv)

    led = build_ledger(build_records())
    text = render(led)

    if args.check:
        if not OUT.exists():
            print(f"FAIL: {OUT} does not exist", file=sys.stderr)
            return 1
        committed = OUT.read_bytes()
        recomputed = text.encode("utf-8")
        if committed != recomputed:
            print(f"FAIL: {OUT} does not match the recomputed ledger", file=sys.stderr)
            print(f"  committed  sha256 = {hashlib.sha256(committed).hexdigest()}",
                  file=sys.stderr)
            print(f"  recomputed sha256 = {hashlib.sha256(recomputed).hexdigest()}",
                  file=sys.stderr)
            print("  regenerate with: python3 experiments/build_fix_oracle_ledger.py",
                  file=sys.stderr)
            return 1
        print(f"OK: {OUT} matches the recomputed ledger "
              f"({hashlib.sha256(committed).hexdigest()})")
        _summarise(led)
        return 0

    OUT.write_text(text)
    print(f"wrote {OUT}  runs={led['n_runs']}")
    _summarise(led)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Generate experiments/MUTANT_LEDGER.json from the nine mutant run records.

GENERATED — do not hand-edit.

  regenerate :  python3 experiments/build_mutant_ledger.py
  verify     :  python3 experiments/build_mutant_ledger.py --check

`--check` recomputes the whole ledger in memory and compares it byte-for-byte with the
committed file. It never writes, and exits non-zero on any disagreement, so a stale
MUTANT_LEDGER.json cannot pass unnoticed.

Scope: the pre-registered MS-M01/MS-M02/MS-M03 MS-I1..MS-I5 study, nine repetitions
executed 2026-08-29 (`PREREGISTRATION_MS_MUTANTS.md`). This is a separate accounting
from RESULT_LEDGER.json, which covers the thirteen historical null-agent runs and is
not touched by this file.

Three determinism rules, all load-bearing for `--check`:

1. **No filesystem metadata.** Every timestamp emitted here is read from the run record
   itself. `st_mtime` is a property of this working copy, not of the experiment.
2. **No implicit coercion of a verdict.** `strict_bool` refuses anything that is not an
   actual JSON boolean — `"true"`, `1`, `0` and `null` are errors, never coerced. A
   `bool(...)` wrapper would map `bool({"success": false})` onto `True`.
3. **Derived quantities are computed here, not asserted anywhere.** Two are emitted.

   `faulted_rate_excluding_first_round` is recomputed from the committed per-round
   detail. Its motivation is in `DEVIATIONS_AND_LIMITS.md` item 4: `wait_for_rounds`
   selects rounds by `report_ts`, the END of an ~11 s round, so in all nine runs the
   first selected round began before the mutation was applied and may contain
   pre-mutation requests. Excluding it changes no categorical result and is the figure
   to prefer for any quantitative claim.

   `successful_response_volume_retained_percent` is the write-up's headline metric:

       (faulted.total_requests - faulted.total_non2xx)
       ----------------------------------------------  x 100
       (healthy.total_requests - healthy.total_non2xx)

   It counts successful HTTP responses in the faulted window against successful HTTP
   responses in the SAME run's healthy window, over equal ten-round windows of about
   99 s each. **It is a response count, not a measure of application work.** A system
   returning fewer responses is doing less of what the workload asked for, but nothing
   here weighs a response by cost, latency or usefulness, and the name must not be read
   as if it did. The caveat lives here, in the register, so that it travels with the
   number rather than only with the paragraph in the paper that cites it.

   `successful_response_volume_retained_excluding_first_round_percent` is the same
   quantity over the **fully post-mutation** faulted window:

       mean successful responses per faulted round, first round dropped
       ---------------------------------------------------------------  x 100
       mean successful responses per healthy round

   Normalising per round is required, not cosmetic: dropping the straddling round leaves
   the faulted window with nine rounds against the healthy window's ten, so a bare sum
   ratio would understate retention by a tenth for reasons that have nothing to do with
   the mutant.

   **The write-up reports this second figure**, because every run's first faulted round
   began before the mutation was applied (`DEVIATIONS_AND_LIMITS.md` item 4) and that
   document already directs that the fully post-mutation figures be preferred for any
   quantitative claim. The full-window figure is retained beside it as the sensitivity
   value, so a reader can see how much the choice moves the result: for MS-M01 and MS-M03
   it moves it by about a twentieth of a percentage point, for MS-M02 by about four.

The nine records are attested and are never modified by this script. It reads only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

EXP = Path(__file__).resolve().parent
REPO = EXP.parent
OUT = EXP / "MUTANT_LEDGER.json"

PROTOCOL = "sremut-preregistered-mutant-v1"
MUTANTS = ("MS-M01", "MS-M02", "MS-M03")
REPETITIONS = ("r01", "r02", "r03")
STATES = ("healthy", "faulted", "restored")
CONTROL_STATES = ("healthy", "restored")
INVARIANTS = ("MS-I1", "MS-I2", "MS-I3", "MS-I4", "MS-I5", "MS-I6")

# Run directories, in the frozen round-robin order of
# profiles/missing_service_social_network/pilot-v1.yaml.
RUN_IDS = tuple(f"ms-m{m}-{r}" for r in REPETITIONS for m in ("01", "02", "03"))

# The four instrument files whose hashes each record pins.
INSTRUMENT_KEYS = ("driver_sha256", "contract_check_sha256",
                   "three_state_run_sha256", "worker_sha256")
INSTRUMENT_FILES = {
    "driver_sha256": EXP / "mutant_run.py",
    "contract_check_sha256": EXP / "contract_check.py",
    "three_state_run_sha256": EXP / "three_state_run.py",
    "worker_sha256": REPO / "src" / "sremut" / "original_oracle_worker.py",
}


class MutantLedgerError(RuntimeError):
    """Raised on any malformed or missing input. Never silently tolerated."""


def strict_bool(value, where: str) -> bool:
    """Return `value` only if it is an actual JSON boolean.

    Rejects "true", "false", 1, 0 and null. `isinstance(True, int)` is True in Python,
    so identity is the only correct test: `1 is True` is False.
    """
    if value is True or value is False:
        return value
    raise MutantLedgerError(
        f"{where}: expected a JSON boolean; got {value!r} ({type(value).__name__}). "
        f"Strings, 0/1 and null are refused, never coerced.")


def _require(doc: dict, key: str, where: str):
    if key not in doc:
        raise MutantLedgerError(f"{where}: missing required key {key!r}")
    return doc[key]


def _sha256_file(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _rate(non2xx: int, requests: int):
    """Failure rate as a percentage, or None when the denominator is zero.

    Rounded to 4 decimal places so the emitted bytes are stable across platforms;
    binary float repr is not something a ledger should depend on.
    """
    if requests <= 0:
        return None
    return round(100.0 * non2xx / requests, 4)


def _workload(block: dict, where: str) -> dict:
    detail = _require(block, "detail", where)
    if not isinstance(detail, list) or not detail:
        raise MutantLedgerError(f"{where}: 'detail' must be a non-empty list")

    rounds = _require(block, "rounds", where)
    requests = _require(block, "total_requests", where)
    non2xx = _require(block, "total_non2xx", where)

    # Recompute the totals from the per-round detail rather than trusting them.
    recomputed_requests = sum(r["requests"] or 0 for r in detail)
    recomputed_non2xx = sum(r["non2xx"] for r in detail)
    if len(detail) != rounds:
        raise MutantLedgerError(
            f"{where}: rounds={rounds} but detail has {len(detail)} entries")
    if recomputed_requests != requests or recomputed_non2xx != non2xx:
        raise MutantLedgerError(
            f"{where}: stored totals ({requests}/{non2xx}) disagree with the per-round "
            f"detail ({recomputed_requests}/{recomputed_non2xx})")

    return {
        "rounds": rounds,
        "total_requests": requests,
        "total_non2xx": non2xx,
        "non2xx_rate_percent": _rate(non2xx, requests),
        "timed_out": strict_bool(_require(block, "timed_out", where),
                                 f"{where}.timed_out"),
        "first_round_utc": block.get("first_round_utc"),
        "last_round_utc": block.get("last_round_utc"),
        "per_round_requests": [r["requests"] for r in detail],
        "per_round_non2xx": [r["non2xx"] for r in detail],
    }


def _excluding_first_round(block: dict) -> dict:
    """The fully-post-mutation faulted window. See DEVIATIONS_AND_LIMITS.md item 4."""
    rest = block["detail"][1:]
    requests = sum(r["requests"] or 0 for r in rest)
    non2xx = sum(r["non2xx"] for r in rest)
    return {
        "rounds": len(rest),
        "total_requests": requests,
        "total_non2xx": non2xx,
        "rate_percent": _rate(non2xx, requests),
        "dropped_round_report_ts": block["detail"][0]["report_ts"],
        "dropped_round_requests": block["detail"][0]["requests"],
        "dropped_round_non2xx": block["detail"][0]["non2xx"],
    }


def _oracle_readings(doc: dict, state: str, where: str) -> dict:
    """The two provenance categories for one state, as strict Booleans."""
    ip = _require(doc, f"{state}_in_process", where)
    wk = _require(doc, f"{state}_worker", where)
    raw = _require(ip, "raw_verdict", f"{where}.{state}_in_process")
    if set(raw) != {"success"}:
        raise MutantLedgerError(
            f"{where}.{state}_in_process.raw_verdict: expected exactly one key "
            f"'success'; got {sorted(raw)}")
    return {
        "in_process": {
            "verdict": strict_bool(raw["success"],
                                   f"{where}.{state}_in_process.raw_verdict.success"),
            "elapsed_seconds": ip.get("elapsed_seconds"),
        },
        "worker": {
            "verdict": strict_bool(wk.get("returned_boolean"),
                                   f"{where}.{state}_worker.returned_boolean"),
            "outcome": wk.get("outcome"),
            "raw_result_sha256": wk.get("raw_result_sha256"),
            "exit_code": wk.get("exit_code"),
            "elapsed_seconds": wk.get("elapsed_seconds"),
        },
    }


def _contract(doc: dict, state: str, where: str) -> dict:
    c = _require(doc, f"contract_{state}", where)
    inv = _require(c, "invariants", f"{where}.contract_{state}")
    missing = [i for i in INVARIANTS if i not in inv]
    if missing:
        raise MutantLedgerError(
            f"{where}.contract_{state}: missing invariants {missing}")
    return {
        "verdict": _require(c, "verdict", f"{where}.contract_{state}"),
        "violated": list(_require(c, "violated", f"{where}.contract_{state}")),
        "invariants": {i: inv[i]["result"] for i in INVARIANTS},
        "probe_pod_gone": (c.get("probe_pod") or {}).get("delete", {}).get("gone"),
    }


def _activation(doc: dict, where: str) -> dict:
    aa = _require(doc, "activation_after_apply", where)
    ab = _require(doc, "activation_before_oracle", where)
    return {
        "after_apply": {
            "activated": strict_bool(_require(aa, "activated", f"{where}.after_apply"),
                                     f"{where}.activation_after_apply.activated"),
            "attempts": aa.get("attempts"),
            "elapsed_seconds": aa.get("elapsed_seconds"),
            "criteria": {k: strict_bool(v["ok"], f"{where}.after_apply.{k}")
                         for k, v in sorted(aa.get("criteria", {}).items())},
        },
        "before_oracle": {
            "activated": strict_bool(_require(ab, "activated", f"{where}.before_oracle"),
                                     f"{where}.activation_before_oracle.activated"),
            "criteria": {k: strict_bool(v["ok"], f"{where}.before_oracle.{k}")
                         for k, v in sorted(ab.get("criteria", {}).items())},
        },
        "drift": strict_bool(_require(doc, "activation_drift", where),
                             f"{where}.activation_drift"),
    }


def build_record(run_id: str) -> dict:
    path = EXP / run_id / "mutant-run.json"
    if not path.is_file():
        raise MutantLedgerError(f"{run_id}: no mutant-run.json at {path}")
    doc = json.loads(path.read_text())
    where = run_id

    protocol = _require(doc, "protocol", where)
    if protocol != PROTOCOL:
        raise MutantLedgerError(
            f"{where}: protocol is {protocol!r}, expected {PROTOCOL!r}")

    assertions = doc.get("pre_oracle_assertions") or []
    mutant = doc.get("mutant") or {}
    faulted_block = _require(doc, "workload_faulted", where)

    workload = {s: _workload(_require(doc, f"workload_{s}", where),
                             f"{where}.workload_{s}") for s in STATES}
    healthy_ok = workload["healthy"]["total_requests"] - workload["healthy"]["total_non2xx"]
    faulted_ok = workload["faulted"]["total_requests"] - workload["faulted"]["total_non2xx"]

    # Fully post-mutation: drop the straddling first faulted round, then compare MEAN
    # successful responses per round, because the two windows no longer have equal
    # round counts.
    post = faulted_block["detail"][1:]
    post_ok = sum((r["requests"] or 0) - r["non2xx"] for r in post)
    healthy_rounds = workload["healthy"]["rounds"]
    retained_excl = (
        _rate(post_ok / len(post), healthy_ok / healthy_rounds)
        if post and healthy_rounds and healthy_ok else None)

    rec = {
        "run_id": _require(doc, "run_id", where),
        "mutant_id": _require(doc, "mutant_id", where),
        "problem_id": doc.get("problem_id"),
        "namespace": doc.get("namespace"),
        "status": _require(doc, "status", where),
        "protocol": protocol,
        "started_at": doc.get("started_at"),
        "finished_at": doc.get("finished_at"),
        "total_seconds": doc.get("total_seconds"),
        "phase_seconds": {
            "deployment": doc.get("deployment_seconds"),
            "stabilization": doc.get("stabilization_seconds"),
            "injection": doc.get("injection_seconds"),
            "recovery": doc.get("recovery_seconds"),
        },
        "r1_interval_seconds_achieved": doc.get("r1_interval_seconds_achieved"),
        "instrument_sha256": {k: doc.get(k) for k in INSTRUMENT_KEYS},
        "mutant_application": {
            "action": mutant.get("action"),
            "body_sha256": mutant.get("body_sha256"),
            "kubectl_exit": mutant.get("exit"),
            "applied_utc": doc.get("mutant_applied_utc"),
        },
        "activation": _activation(doc, where),
        "captured_replica_baseline_count": doc.get("captured_replica_baseline_count"),
        "captured_replica_baseline_sha256": doc.get("captured_replica_baseline_sha256"),
        "captured_service_sha256": doc.get("captured_service_sha256"),
        "oracle_readings": {s: _oracle_readings(doc, s, where) for s in STATES},
        "contract": {s: _contract(doc, s, where) for s in STATES},
        "workload": workload,
        "faulted_rate_excluding_first_round": _excluding_first_round(faulted_block),
        "successful_responses": {
            "healthy": healthy_ok,
            "faulted": faulted_ok,
            "faulted_excluding_first_round": post_ok,
            "faulted_rounds_excluding_first": len(post),
            "healthy_rounds": healthy_rounds,
        },
        "successful_response_volume_retained_percent": _rate(faulted_ok, healthy_ok),
        "successful_response_volume_retained_excluding_first_round_percent": retained_excl,
        "pre_oracle_assertions": {
            "count": len(assertions),
            "all_no_probe_pod": all(strict_bool(a["ok"], f"{where}.assertion.ok")
                                    for a in assertions),
            "all_namespace_fully_running": all(
                strict_bool(a["namespace_fully_running"],
                            f"{where}.assertion.namespace_fully_running")
                for a in assertions),
            "labels": [a["label"] for a in assertions],
        },
        "any_non_running_pod_at_an_oracle_call": strict_bool(
            _require(doc, "any_non_running_pod_at_an_oracle_call", where),
            f"{where}.any_non_running_pod_at_an_oracle_call"),
        "preflight_passed": strict_bool(doc["preflight"]["passed"],
                                        f"{where}.preflight.passed"),
        "restoration_attempted": strict_bool(
            _require(doc, "restoration_attempted", where),
            f"{where}.restoration_attempted"),
        "restoration_outcome": doc.get("restoration_outcome"),
    }
    return rec


def build_records() -> list[dict]:
    return [build_record(r) for r in RUN_IDS]


def build_ledger(records: list[dict]) -> dict:
    by_status: dict[str, int] = {}
    for r in records:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1

    readings, faulted_readings = [], []
    for r in records:
        for state in STATES:
            for cat in ("in_process", "worker"):
                v = r["oracle_readings"][state][cat]["verdict"]
                readings.append(v)
                if state == "faulted":
                    faulted_readings.append(v)

    control_verdicts = [r["contract"][s]["verdict"]
                        for r in records for s in CONTROL_STATES]
    faulted_verdicts = [r["contract"]["faulted"]["verdict"] for r in records]

    per_mutant = {}
    for m in MUTANTS:
        rs = [r for r in records if r["mutant_id"] == m]
        retained = [r["successful_response_volume_retained_percent"] for r in rs]
        retained_x = [
            r["successful_response_volume_retained_excluding_first_round_percent"]
            for r in rs]
        violated = [tuple(r["contract"]["faulted"]["violated"]) for r in rs]
        bodies = sorted({r["mutant_application"]["body_sha256"] for r in rs})
        per_mutant[m] = {
            "n_runs": len(rs),
            "run_ids": [r["run_id"] for r in rs],
            "violated_invariants": sorted(violated[0]) if violated else [],
            "violated_identical_across_repetitions": len(set(violated)) == 1,
            "body_sha256": bodies[0] if len(bodies) == 1 else None,
            "body_sha256_identical_across_repetitions": len(bodies) == 1,
            "faulted_rate_percent": [
                r["workload"]["faulted"]["non2xx_rate_percent"] for r in rs],
            "faulted_rate_excluding_first_round_percent": [
                r["faulted_rate_excluding_first_round"]["rate_percent"] for r in rs],
            "successful_response_volume_retained_percent": retained,
            "successful_response_volume_retained_mean_percent": (
                round(sum(retained) / len(retained), 4) if retained else None),
            "successful_response_volume_retained_excluding_first_round_percent": retained_x,
            "successful_response_volume_retained_excluding_first_round_mean_percent": (
                round(sum(retained_x) / len(retained_x), 4) if retained_x else None),
            "faulted_oracle_readings": 2 * len(rs),
        }

    instrument = {}
    for key in INSTRUMENT_KEYS:
        seen = sorted({r["instrument_sha256"][key] for r in records})
        instrument[key] = {
            "value": seen[0] if len(seen) == 1 else None,
            "identical_across_runs": len(seen) == 1,
            "matches_committed_file": (
                len(seen) == 1 and seen[0] == _sha256_file(INSTRUMENT_FILES[key])),
        }

    return {
        "GENERATED_BY": "experiments/build_mutant_ledger.py",
        "DO_NOT_HAND_EDIT": True,
        "protocol": PROTOCOL,
        "protocol_doc": "PREREGISTRATION_MS_MUTANTS.md",
        "deviations_doc": "DEVIATIONS_AND_LIMITS.md",
        "scope": ("Pre-registered MS-M01/M02/M03 study, invariants MS-I1..MS-I5. "
                  "MS-I6 was declared out of scope in advance by section 6 of the "
                  "pre-registration and is recorded as NOT_EVALUATED throughout."),
        "n_runs": len(records),
        "runs_by_status": dict(sorted(by_status.items())),
        "all_runs_complete": all(r["status"] == "COMPLETE" for r in records),
        "oracle_readings": {
            "total": len(readings),
            "faulted": len(faulted_readings),
            "controls": len(readings) - len(faulted_readings),
            "every_reading_true": all(v is True for v in readings),
            "every_faulted_reading_true": all(v is True for v in faulted_readings),
        },
        "contract_verdicts": {
            "faulted": dict(sorted(
                {v: faulted_verdicts.count(v) for v in set(faulted_verdicts)}.items())),
            "controls": dict(sorted(
                {v: control_verdicts.count(v) for v in set(control_verdicts)}.items())),
            "every_faulted_reject": all(v == "REJECT" for v in faulted_verdicts),
            "every_control_pass": all(v == "PASS" for v in control_verdicts),
        },
        "instrument_sha256": instrument,
        "per_mutant": per_mutant,
        "runs": records,
    }


def render(led: dict) -> str:
    return json.dumps(led, indent=1, sort_keys=True) + "\n"


def _summarise(led: dict) -> None:
    print(f"runs: {led['n_runs']}")
    print("runs by status:", json.dumps(led["runs_by_status"], sort_keys=True))
    o = led["oracle_readings"]
    print(f"oracle readings: total={o['total']} faulted={o['faulted']} "
          f"controls={o['controls']}")
    print("every faulted reading is success=true :", o["every_faulted_reading_true"])
    print("contract faulted verdicts:",
          json.dumps(led["contract_verdicts"]["faulted"], sort_keys=True))
    print("contract control verdicts:",
          json.dumps(led["contract_verdicts"]["controls"], sort_keys=True))
    for m, v in sorted(led["per_mutant"].items()):
        print(f"  {m}: violated={v['violated_invariants']} "
              f"rate={v['faulted_rate_percent']} "
              f"excl_first={v['faulted_rate_excluding_first_round_percent']}")
        print(f"       retained volume        {v['successful_response_volume_retained_percent']} "
              f"mean {v['successful_response_volume_retained_mean_percent']}%")
        print(f"       retained excl 1st round "
              f"{v['successful_response_volume_retained_excluding_first_round_percent']} "
              f"mean {v['successful_response_volume_retained_excluding_first_round_mean_percent']}% "
              f"<- reported")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Build (or verify) experiments/MUTANT_LEDGER.json from the nine "
                    "mutant run records on disk.")
    ap.add_argument("--check", action="store_true",
                    help="Recompute the ledger in memory and compare it byte-for-byte "
                         "with the committed MUTANT_LEDGER.json. Writes nothing; exits "
                         "non-zero on any disagreement.")
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
            print("  regenerate with: python3 experiments/build_mutant_ledger.py",
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

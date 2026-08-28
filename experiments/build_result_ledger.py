#!/usr/bin/env python3
"""Generate experiments/RESULT_LEDGER.json from the run records on disk.

GENERATED — do not hand-edit.

  regenerate :  python3 experiments/build_result_ledger.py
  verify     :  python3 experiments/build_result_ledger.py --check

`--check` recomputes the whole ledger in memory and compares it byte-for-byte with the
committed file. It never writes. It exits non-zero on any disagreement, so a stale
RESULT_LEDGER.json cannot pass unnoticed.

One record per run directory under experiments/. Four on-disk record schemas are
normalised into one shape:
  three-state.json    driver (ii) minimal driver, three-state runs
  run-result.json     g02 exploratory null-agent run
  w3-result.json      w3 partial run
  conductor-path.json g3 real-Conductor in-process run

Every field is read from the run's own JSON. Nothing is carried forward from prose.

Two determinism rules, both load-bearing for `--check`:

1. **No filesystem metadata.** A timestamp is emitted only when the artifact itself
   records one. `st_mtime` is a property of this working copy, not of the experiment —
   a fresh clone reproduces the bytes and destroys the mtimes — so an artifact with no
   internal timestamp gets `"utc": null` and
   `"utc_source": "not_recorded_in_artifact"` rather than an invented one.
2. **No implicit coercion of a verdict.** `success` must be an actual JSON boolean in
   the artifact. `"true"`, `"false"`, `1`, `0` and `null` are refused, loudly, at the
   point of read. The previous `bool(...)` wrapper would have silently turned every one
   of those into a verdict — including `bool({"success": false})`, which is `True`.
"""
from __future__ import annotations
import argparse, csv as _csv, hashlib, json, sys
from pathlib import Path

EXP = Path(__file__).resolve().parent
OUT = EXP / "RESULT_LEDGER.json"
MAIN = ("three-state.json", "run-result.json", "w3-result.json", "conductor-path.json")

APP_BY_PID = {
    "missing_service_social_network": "social-network",
    "missing_service_hotel_reservation": "hotel-reservation",
    "missing_service_astronomy_shop": "astronomy-shop",
}

# --- historical classification vocabulary ---------------------------------------
# These runs are the project's *historical* experiment record: they predate the
# authenticated MS-M01/MS-M02/MS-M03 matrix and were not produced by the sealed
# orchestrator. The word "official" is deliberately not used for any of them; it is
# reserved, together with OFFICIAL_FROZEN_ATTEMPT, for that future matrix.
HISTORICAL_INCLUDED = "HISTORICAL_INCLUDED"
HISTORICAL_EXCLUDED_INERT = "HISTORICAL_EXCLUDED_INERT"
HISTORICAL_EXCLUDED_ABANDONED = "HISTORICAL_EXCLUDED_ABANDONED"
HISTORICAL_EXCLUDED_SUPERSEDED = "HISTORICAL_EXCLUDED_SUPERSEDED"
RESERVED_STATUS = "OFFICIAL_FROZEN_ATTEMPT"

STATUS_VOCABULARY = {
    HISTORICAL_INCLUDED:
        "Historical run whose measurements are included in the reported totals.",
    HISTORICAL_EXCLUDED_INERT:
        "Historical run excluded because the condition it was meant to establish was "
        "never actually applied.",
    HISTORICAL_EXCLUDED_ABANDONED:
        "Historical run excluded because it stopped before the faulted state and "
        "produced no faulted-state measurement.",
    HISTORICAL_EXCLUDED_SUPERSEDED:
        "Historical run excluded because a later run repeated the same measurement "
        "with the instrument defects fixed.",
}

# Status is a classification, not a measurement, and it is an analysis-layer property:
# it is asserted here, never written back into a raw run record. Each entry carries its
# reason so the judgement is visible and challengeable. Keyed by run directory.
STATUS = {
 "g02-run-01":  (HISTORICAL_INCLUDED, None,
   "Complete end-to-end null-agent run under analysis/G02_null_agent_plan.md."),
 "g1-run-01":   (HISTORICAL_INCLUDED, None,
   "Complete G1 repetition under experiments/PROTOCOL_G1.md."),
 "g1-run-02":   (HISTORICAL_INCLUDED, None,
   "Complete G1 repetition under experiments/PROTOCOL_G1.md."),
 "g1-run-03":   (HISTORICAL_INCLUDED, None,
   "Complete G1 repetition under experiments/PROTOCOL_G1.md."),
 "g3-run-01":   (HISTORICAL_INCLUDED, None,
   "Complete real-Conductor run. Verdict instrument sound; the faulted-state WORKLOAD "
   "window is void (jsonpath literal-newline defect in the driver), so this run "
   "contributes a verdict but no faulted workload measurement."),
 "w1-delay0-01":(HISTORICAL_INCLUDED, None,
   "Complete H2 delay=0 run under experiments/PROTOCOL_W1.md."),
 "w1-delay0-02":(HISTORICAL_INCLUDED, None,
   "Complete H2 delay=0 run under experiments/PROTOCOL_W1.md."),
 "w2-noise-01": (HISTORICAL_INCLUDED, None,
   "Complete H3 noise run under experiments/PROTOCOL_W2.md."),
 "w2-noise-02": (HISTORICAL_INCLUDED, None,
   "Complete H3 noise run under experiments/PROTOCOL_W2.md."),
 "w4-hotel-01": (HISTORICAL_INCLUDED, None,
   "Complete hotel-reservation run under experiments/PROTOCOL_W4.md, with both "
   "instrument defects from w3 fixed."),
 "w2-noise-00-INERT-noise-never-started": (HISTORICAL_EXCLUDED_INERT,
   "enable_noise=True had no effect: the driver never called start_problem(), where "
   "NoiseManager.start() lives (conductor.py:451). No noise was ever injected.",
   "Preserved for provenance. Excluded from every noise claim."),
 "w2-hotel-01": (HISTORICAL_EXCLUDED_ABANDONED,
   "Run stopped at the healthy gate; the faulted state was never reached.",
   "No faulted-state measurement exists in this run."),
 "w3-hotel-01": (HISTORICAL_EXCLUDED_SUPERSEDED,
   "Two instrument defects: the state sampler was hardcoded to namespace "
   "'social-network' so its samples describe the wrong namespace, and the supervising "
   "loop hit a 10-minute tool timeout and killed the driver during the faulted window.",
   "Superseded by w4-hotel-01, which re-ran the same measurement with both defects fixed."),
}


class LedgerDataError(ValueError):
    """A run record violates the ledger's type contract."""


def strict_bool(value, where: str) -> bool:
    """Return `value` only if it is an actual JSON boolean.

    Rejects "true", "false", 1, 0 and null. `isinstance(True, int)` is True in Python,
    so identity is the only correct test here: `1 is True` is False, `True is True` is
    not.
    """
    if value is True or value is False:
        return value
    raise LedgerDataError(
        f"{where}: 'success' must be a JSON boolean; got {value!r} "
        f"({type(value).__name__}). Strings, 0/1 and null are refused, never coerced.")


def read_main(d: Path):
    for name in MAIN:
        p = d / name
        if p.exists():
            return name, json.loads(p.read_text())
    return None, None


def workload_summary(w):
    """Normalise a workload block to rounds / requests / failures / failure_rate."""
    if not isinstance(w, dict):
        return None
    det = w.get("detail")
    if not isinstance(det, list) or not det:
        return None
    rounds = len(det)
    req = sum(int(r.get("requests", 0) or 0) for r in det)
    fail = sum(int(r.get("non2xx", 0) or 0) for r in det)
    return {"rounds": rounds, "requests": req, "failures": fail,
            "failure_rate_pct": round(100.0 * fail / req, 4) if req else None,
            "first_report_ts": det[0].get("report_ts"),
            "last_report_ts": det[-1].get("report_ts")}


def verdict_of(block, where: str):
    """Extract (bool_verdict, utc, sha256) from an instrument block."""
    if not isinstance(block, dict):
        return None
    utc = block.get("finished_utc")
    raw = block.get("raw_verdict")
    if isinstance(raw, dict) and "success" in raw:
        return {"success": strict_bool(raw["success"], f"{where}.raw_verdict"),
                "utc": utc,
                "raw_result_sha256": block.get("raw_result_sha256")}
    res = block.get("result")
    if isinstance(res, dict):
        rr = res.get("raw_result")
        if isinstance(rr, dict) and "success" in rr:
            return {"success": strict_bool(rr["success"],
                                           f"{where}.result.raw_result"),
                    "utc": utc,
                    "outcome": res.get("outcome"),
                    "raw_result_sha256": block.get("raw_result_sha256")}
    return None


def build_record(d: Path):
    name, j = read_main(d)
    if j is None:
        return None
    pid = j.get("problem_id")
    rec = {
        "run_id": j.get("run_id", d.name),
        "run_dir": d.name,
        "record_file": name,
        "record_sha256": hashlib.sha256((d / name).read_bytes()).hexdigest(),
        "schema_version": j.get("schema_version"),
        "problem_id": pid,
        "application": APP_BY_PID.get(pid),
        "namespace": j.get("namespace"),
        "driver": j.get("driver"),
        "protocol": j.get("protocol"),
        "protocol_doc": j.get("protocol_doc"),
        "mitigation_oracle_class": j.get("mitigation_oracle_class"),
        "started_at": j.get("started_at"),
        "finished_at": j.get("finished_at"),
        "record_status_field": j.get("status"),
        "r1_null_agent_episode_seconds": j.get("null_agent_episode_seconds",
                                               j.get("r1_interval_seconds")),
        "r1_sleep_started_utc": j.get("null_agent_sleep_started_utc"),
        "r1_sleep_finished_utc": j.get("null_agent_sleep_finished_utc"),
        "r2_min_rounds_per_state": j.get("min_rounds_per_state"),
        "r3_classification": j.get("r3_classification"),
        "enable_noise": j.get("enable_noise"),
        "single_instrument": j.get("single_instrument"),
        "agent_action_between_injection_and_evaluation":
            j.get("agent_action_between_injection_and_evaluation"),
        "verdicts": {},
        "workload": {},
        "instruments": [],
    }

    # --- instruments -------------------------------------------------------
    for state in ("healthy", "faulted", "restored"):
        for inst, key in (("in_process", f"{state}_in_process"),
                          ("worker",     f"{state}_worker")):
            v = verdict_of(j.get(key), f"{d.name}/{name}:{key}")
            if v:
                rec["verdicts"].setdefault(state, {})[inst] = v
    # g02 schema: healthy_control / treatment carry raw_verdict directly (in-process)
    for state, key in (("healthy", "healthy_control"), ("faulted", "treatment")):
        blk = j.get(key)
        if isinstance(blk, dict) and "raw_verdict" in blk:
            rec["verdicts"].setdefault(state, {})["in_process"] = {
                "success": strict_bool(blk["raw_verdict"].get("success"),
                                       f"{d.name}/{name}:{key}.raw_verdict"),
                "utc": blk.get("finished_utc"), "phase": blk.get("phase")}
    # w3 schema: flat in_process_* keys inside healthy / faulted / restored_state
    for state, key in (("healthy", "healthy"), ("faulted", "faulted"),
                       ("restored", "restored_state")):
        blk = j.get(key)
        if not isinstance(blk, dict):
            continue
        if isinstance(blk.get("in_process_raw_verdict"), dict):
            rec["verdicts"].setdefault(state, {})["in_process"] = {
                "success": strict_bool(
                    blk["in_process_raw_verdict"].get("success"),
                    f"{d.name}/{name}:{key}.in_process_raw_verdict"),
                "utc": blk.get("in_process_finished_utc")}
        if isinstance(blk.get("worker"), str):
            rec.setdefault("instrument_notes", {})[f"{state}/worker"] = blk["worker"]
        ph = blk.get("workload_captured_post_hoc")
        if isinstance(ph, dict):
            rec["workload"][state] = {
                "rounds": ph.get("rounds"), "requests": ph.get("total_requests"),
                "failures": ph.get("total_non2xx"),
                "failure_rate_pct": round(100.0 * ph["non2xx_rate"], 4)
                                    if ph.get("non2xx_rate") is not None else None,
                "first_report_ts": (ph.get("window") or [None, None])[0],
                "last_report_ts": (ph.get("window") or [None, None])[-1],
                "capture": "post_hoc", "note": ph.get("note")}
        if isinstance(blk.get("live_cluster_check"), dict):
            rec.setdefault("live_cluster_check", {})[state] = {
                "values": blk["live_cluster_check"],
                "utc": blk.get("live_cluster_check_utc"),
                "caveat": "Recorded verbatim from the run record. The 'user_service' "
                          "field is meaningless for hotel-reservation: no Service named "
                          "user-service exists there; the deleted Service is mongodb-rate "
                          "(registry.py:177). Do not cite this field as fault evidence."}
    # g3 schema: real Conductor. results_Mitigation is the raw oracle dict, so the
    # verdict is its 'success' member — never the truthiness of the dict itself.
    if "results_Mitigation" in j:
        m = j["results_Mitigation"]
        where = f"{d.name}/{name}:results_Mitigation"
        if isinstance(m, dict):
            if "success" not in m:
                raise LedgerDataError(f"{where}: dict carries no 'success' member.")
            succ = strict_bool(m["success"], f"{where}.success")
        else:
            succ = strict_bool(m, where)
        rec["verdicts"].setdefault("faulted", {})["conductor"] = {
            "success": succ,
            "utc": j.get("finish_problem_reached_utc") or j.get("finished_at"),
            "results_TTM": j.get("results_TTM"), "results_TTL": j.get("results_TTL")}

    # --- workload ----------------------------------------------------------
    for state in ("healthy", "faulted", "restored"):
        if state in rec["workload"]:
            continue
        ws = workload_summary(j.get(f"workload_{state}"))
        if ws:
            rec["workload"][state] = ws

    # --- auxiliary sidecar instrument artifacts -----------------------------
    # Some runs recorded further verdicts outside the main record, as standalone
    # worker/in-process artifacts. The worker artifacts carry NO internal timestamp,
    # so no UTC is emitted for them: file mtime describes this checkout, not the
    # experiment, and would make the ledger non-reproducible from the bytes alone.
    for sub, state in (("post/worker/result.json", "faulted"),
                       ("recovery/worker/result.json", "restored")):
        f = d / sub
        if f.exists():
            w = json.loads(f.read_text())
            rr = w.get("raw_result") or {}
            if "success" in rr:
                rec["verdicts"].setdefault(state, {})["worker"] = {
                    "success": strict_bool(rr["success"],
                                           f"{d.name}/{sub}:raw_result"),
                    "outcome": w.get("outcome"),
                    "raw_result_sha256": w.get("raw_result_sha256"),
                    "utc": None, "utc_source": "not_recorded_in_artifact",
                    "artifact": sub}
    f = d / "recovery/in-process-oracle.json"
    if f.exists():
        o = json.loads(f.read_text())
        if isinstance(o.get("raw_verdict"), dict):
            state = "restored" if "RESTORED" in str(o.get("phase", "")).upper() else "faulted"
            rec["verdicts"].setdefault(state, {})["in_process"] = {
                "success": strict_bool(
                    o["raw_verdict"].get("success"),
                    f"{d.name}/recovery/in-process-oracle.json:raw_verdict"),
                "utc": o.get("finished_utc"),
                "phase": o.get("phase"), "artifact": "recovery/in-process-oracle.json"}

    # sidecar per-round workload CSV (g02 G0.3 harvest of the faulted state)
    csvf = d / "post/workload-rounds.csv"
    if csvf.exists() and "faulted" not in rec["workload"]:
        with csvf.open() as fh:
            rows = [r for r in _csv.DictReader(fh)
                    if r.get("requests") and r.get("aborted") == "0"]
        if rows:
            req = sum(int(r["requests"]) for r in rows)
            fail = sum(int(r["non2xx"] or 0) for r in rows)
            rec["workload"]["faulted"] = {
                "rounds": len(rows), "requests": req, "failures": fail,
                "failure_rate_pct": round(100.0 * fail / req, 4) if req else None,
                "first_report_ts": rows[0]["end_utc"], "last_report_ts": rows[-1]["end_utc"],
                "capture": "sidecar_csv", "source": "post/workload-rounds.csv",
                "note": "Completed rounds only (requests present, aborted=0), from the "
                        "G0.3 harvest spanning the whole faulted period."}

    rec["instruments"] = sorted({i for st in rec["verdicts"].values() for i in st})
    wf = rec["workload"].get("faulted")
    rec["r2_satisfied_faulted"] = (wf["rounds"] >= 10) if wf and wf.get("rounds") else None

    st, excl, reason = STATUS.get(d.name, ("UNCLASSIFIED", None, "No classification rule."))
    rec["status"] = st
    rec["exclusion_reason"] = excl
    rec["status_reason"] = reason
    rec["faulted_verdicts"] = rec["verdicts"].get("faulted", {})
    rec["n_faulted_measurements"] = len(rec["faulted_verdicts"])
    return rec


def build_records() -> list[dict]:
    recs = []
    for d in sorted(EXP.iterdir()):
        if not d.is_dir() or d.name == "__pycache__":
            continue
        r = build_record(d)
        if r:
            recs.append(r)
    return recs


def build_ledger(recs: list[dict]) -> dict:
    per_inst, per_inst_included = {}, {}
    for r in recs:
        for inst in r["faulted_verdicts"]:
            per_inst[inst] = per_inst.get(inst, 0) + 1
            if r["status"] == HISTORICAL_INCLUDED:
                per_inst_included[inst] = per_inst_included.get(inst, 0) + 1

    by_status = {}
    for r in recs:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1

    # Every faulted verdict is already a validated JSON boolean by construction; this
    # re-asserts it over the assembled ledger rather than trusting the walk above.
    for r in recs:
        for inst, v in r["faulted_verdicts"].items():
            strict_bool(v["success"], f"{r['run_dir']}/faulted/{inst}")

    return {
        "generated_by": "experiments/build_result_ledger.py",
        "hand_edited": False,
        "n_runs": len(recs),
        "run_status_vocabulary": {
            "values": STATUS_VOCABULARY,
            "reserved": {
                RESERVED_STATUS:
                    "Reserved for the future authenticated MS-M01/MS-M02/MS-M03 "
                    "experiment matrix. No run in this ledger carries it, and the word "
                    "'official' is not used for any historical run.",
            },
            "note": "Status is an analysis-layer classification asserted by "
                    "experiments/build_result_ledger.py. It is never written back into "
                    "a raw run record.",
        },
        "runs_by_status": by_status,
        "faulted_state_measurements": {
            "per_instrument_all_runs": per_inst,
            "total_all_runs": sum(per_inst.values()),
            "per_instrument_historical_included_only": per_inst_included,
            "total_historical_included_only": sum(per_inst_included.values()),
            "note": "An 'instrument reading' is one faulted-state mitigation verdict "
                    "produced by one instrument in one run. Readings from the same run "
                    "are NOT independent experimental repetitions: they share one "
                    "deployment, one injection and one cluster state. The run is the "
                    "experimental unit. Recomputed from the run records; not carried "
                    "forward from any earlier figure.",
        },
        "faulted_verdicts_all_true": all(
            v["success"] for r in recs for v in r["faulted_verdicts"].values()),
        "runs": recs,
    }


def render(led: dict) -> str:
    return json.dumps(led, indent=1, sort_keys=True) + "\n"


def _summarise(led: dict) -> None:
    fsm = led["faulted_state_measurements"]
    print("runs:", led["n_runs"])
    print("runs by status:", json.dumps(led["runs_by_status"], sort_keys=True))
    print("faulted-state instrument readings, per instrument (all runs)          :",
          json.dumps(fsm["per_instrument_all_runs"], sort_keys=True),
          "total =", fsm["total_all_runs"])
    print("faulted-state instrument readings, per instrument (included historical):",
          json.dumps(fsm["per_instrument_historical_included_only"], sort_keys=True),
          "total =", fsm["total_historical_included_only"])
    print("every faulted verdict is success=true :", led["faulted_verdicts_all_true"])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Build (or verify) experiments/RESULT_LEDGER.json from the run "
                    "records on disk.")
    ap.add_argument("--check", action="store_true",
                    help="Recompute the ledger in memory and compare it byte-for-byte "
                         "with the committed RESULT_LEDGER.json. Writes nothing; exits "
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
            print("  regenerate with: python3 experiments/build_result_ledger.py",
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

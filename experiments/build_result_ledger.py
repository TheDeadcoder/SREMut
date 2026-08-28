#!/usr/bin/env python3
"""Generate experiments/RESULT_LEDGER.json from the run records on disk.

GENERATED — do not hand-edit. Re-run:  python3 experiments/build_result_ledger.py

One record per run directory under experiments/. Three on-disk record schemas are
normalised into one shape:
  three-state.json    driver (ii) minimal driver, three-state runs
  run-result.json     g02 exploratory null-agent run
  w3-result.json      w3 partial run
  conductor-path.json g3 real-Conductor in-process run

Every field is read from the run's own JSON. Nothing is carried forward from prose.
"""
from __future__ import annotations
import json, os, glob, hashlib
from pathlib import Path

EXP = Path(__file__).resolve().parent
MAIN = ("three-state.json", "run-result.json", "w3-result.json", "conductor-path.json")

APP_BY_PID = {
    "missing_service_social_network": "social-network",
    "missing_service_hotel_reservation": "hotel-reservation",
    "missing_service_astronomy_shop": "astronomy-shop",
}

# Status is a classification, not a measurement. Each entry carries its reason so the
# judgement is visible and challengeable. Keyed by run directory.
STATUS = {
 "g02-run-01":  ("official", None,
   "Complete end-to-end null-agent run under analysis/G02_null_agent_plan.md."),
 "g1-run-01":   ("official", None, "Complete G1 repetition under experiments/PROTOCOL_G1.md."),
 "g1-run-02":   ("official", None, "Complete G1 repetition under experiments/PROTOCOL_G1.md."),
 "g1-run-03":   ("official", None, "Complete G1 repetition under experiments/PROTOCOL_G1.md."),
 "g3-run-01":   ("official", None,
   "Complete real-Conductor run. Verdict instrument sound; the faulted-state WORKLOAD "
   "window is void (jsonpath literal-newline defect in the driver), so this run "
   "contributes a verdict but no faulted workload measurement."),
 "w1-delay0-01":("official", None, "Complete H2 delay=0 run under experiments/PROTOCOL_W1.md."),
 "w1-delay0-02":("official", None, "Complete H2 delay=0 run under experiments/PROTOCOL_W1.md."),
 "w2-noise-01": ("official", None, "Complete H3 noise run under experiments/PROTOCOL_W2.md."),
 "w2-noise-02": ("official", None, "Complete H3 noise run under experiments/PROTOCOL_W2.md."),
 "w4-hotel-01": ("official", None,
   "Complete hotel-reservation run under experiments/PROTOCOL_W4.md, with both "
   "instrument defects from w3 fixed."),
 "w2-noise-00-INERT-noise-never-started": ("inert",
   "enable_noise=True had no effect: the driver never called start_problem(), where "
   "NoiseManager.start() lives (conductor.py:451). No noise was ever injected.",
   "Preserved for provenance. Excluded from every noise claim."),
 "w2-hotel-01": ("abandoned",
   "Run stopped at the healthy gate; the faulted state was never reached.",
   "No faulted-state measurement exists in this run."),
 "w3-hotel-01": ("superseded",
   "Two instrument defects: the state sampler was hardcoded to namespace "
   "'social-network' so its samples describe the wrong namespace, and the supervising "
   "loop hit a 10-minute tool timeout and killed the driver during the faulted window.",
   "Superseded by w4-hotel-01, which re-ran the same measurement with both defects fixed."),
}

def _mtime_utc(p: Path) -> str:
    import datetime
    return datetime.datetime.fromtimestamp(
        p.stat().st_mtime, datetime.timezone.utc).isoformat().replace("+00:00", "Z")

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

def verdict_of(block):
    """Extract (bool_verdict, utc, sha256) from an instrument block."""
    if not isinstance(block, dict):
        return None
    utc = block.get("finished_utc")
    raw = block.get("raw_verdict")
    if isinstance(raw, dict) and "success" in raw:
        return {"success": bool(raw["success"]), "utc": utc,
                "raw_result_sha256": block.get("raw_result_sha256")}
    res = block.get("result")
    if isinstance(res, dict):
        rr = res.get("raw_result")
        if isinstance(rr, dict) and "success" in rr:
            return {"success": bool(rr["success"]), "utc": utc,
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
            v = verdict_of(j.get(key))
            if v:
                rec["verdicts"].setdefault(state, {})[inst] = v
    # g02 schema: healthy_control / treatment carry raw_verdict directly (in-process)
    for state, key in (("healthy", "healthy_control"), ("faulted", "treatment")):
        blk = j.get(key)
        if isinstance(blk, dict) and "raw_verdict" in blk:
            rec["verdicts"].setdefault(state, {})["in_process"] = {
                "success": bool(blk["raw_verdict"]["success"]),
                "utc": blk.get("finished_utc"), "phase": blk.get("phase")}
    # w3 schema: flat in_process_* keys inside healthy / faulted / restored_state
    for state, key in (("healthy", "healthy"), ("faulted", "faulted"),
                       ("restored", "restored_state")):
        blk = j.get(key)
        if not isinstance(blk, dict):
            continue
        if isinstance(blk.get("in_process_raw_verdict"), dict):
            rec["verdicts"].setdefault(state, {})["in_process"] = {
                "success": bool(blk["in_process_raw_verdict"]["success"]),
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
    # g3 schema: real Conductor
    if "results_Mitigation" in j:
        rec["verdicts"].setdefault("faulted", {})["conductor"] = {
            "success": bool(j["results_Mitigation"]),
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
    # worker/in-process artifacts. These carry no internal timestamp, so the UTC is
    # the file mtime and is labelled as such.
    for sub, state in (("post/worker/result.json", "faulted"),
                       ("recovery/worker/result.json", "restored")):
        f = d / sub
        if f.exists():
            w = json.loads(f.read_text())
            rr = w.get("raw_result") or {}
            if "success" in rr:
                rec["verdicts"].setdefault(state, {})["worker"] = {
                    "success": bool(rr["success"]), "outcome": w.get("outcome"),
                    "raw_result_sha256": w.get("raw_result_sha256"),
                    "utc": _mtime_utc(f), "utc_source": "file_mtime",
                    "artifact": sub}
    f = d / "recovery/in-process-oracle.json"
    if f.exists():
        o = json.loads(f.read_text())
        if isinstance(o.get("raw_verdict"), dict):
            state = "restored" if "RESTORED" in str(o.get("phase", "")).upper() else "faulted"
            rec["verdicts"].setdefault(state, {})["in_process"] = {
                "success": bool(o["raw_verdict"]["success"]), "utc": o.get("finished_utc"),
                "phase": o.get("phase"), "artifact": "recovery/in-process-oracle.json"}

    # sidecar per-round workload CSV (g02 G0.3 harvest of the faulted state)
    csvf = d / "post/workload-rounds.csv"
    if csvf.exists() and "faulted" not in rec["workload"]:
        import csv as _csv
        rows = [r for r in _csv.DictReader(csvf.open())
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

    st, excl, reason = STATUS.get(d.name, ("unclassified", None, "No classification rule."))
    rec["status"] = st
    rec["exclusion_reason"] = excl
    rec["status_reason"] = reason
    rec["faulted_verdicts"] = rec["verdicts"].get("faulted", {})
    rec["n_faulted_measurements"] = len(rec["faulted_verdicts"])
    return rec

def main() -> int:
    recs = []
    for d in sorted(EXP.iterdir()):
        if not d.is_dir() or d.name == "__pycache__":
            continue
        r = build_record(d)
        if r:
            recs.append(r)

    per_inst, per_inst_official = {}, {}
    for r in recs:
        for inst in r["faulted_verdicts"]:
            per_inst[inst] = per_inst.get(inst, 0) + 1
            if r["status"] == "official":
                per_inst_official[inst] = per_inst_official.get(inst, 0) + 1

    by_status = {}
    for r in recs:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1

    led = {
        "generated_by": "experiments/build_result_ledger.py",
        "hand_edited": False,
        "n_runs": len(recs),
        "runs_by_status": by_status,
        "faulted_state_measurements": {
            "per_instrument_all_runs": per_inst,
            "total_all_runs": sum(per_inst.values()),
            "per_instrument_official_only": per_inst_official,
            "total_official_only": sum(per_inst_official.values()),
            "note": "A 'measurement' is one faulted-state mitigation verdict produced by "
                    "one instrument in one run. Recomputed from the run records; not "
                    "carried forward from any earlier figure.",
        },
        "faulted_verdicts_all_true": all(
            v["success"] for r in recs for v in r["faulted_verdicts"].values()),
        "runs": recs,
    }
    out = EXP / "RESULT_LEDGER.json"
    out.write_text(json.dumps(led, indent=1, sort_keys=True) + "\n")
    print(f"wrote {out}  runs={len(recs)}")
    print("runs by status:", json.dumps(by_status, sort_keys=True))
    print("faulted-state measurements, per instrument (all runs) :",
          json.dumps(per_inst, sort_keys=True), "total =", sum(per_inst.values()))
    print("faulted-state measurements, per instrument (official) :",
          json.dumps(per_inst_official, sort_keys=True), "total =",
          sum(per_inst_official.values()))
    print("every faulted verdict is success=true :", led["faulted_verdicts_all_true"])
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

"""RESULT_LEDGER determinism and verdict type safety.

Two defects motivated this file, both of which would have produced a plausible ledger
rather than an error:

1. **Timestamps from `st_mtime`.** The sidecar worker artifacts carry no internal
   timestamp, so the builder used the file's modification time. That is a property of
   one working copy, not of the experiment: a fresh clone reproduces every byte and
   destroys every mtime, so the ledger was not reproducible from the committed data.

2. **`bool(...)` around a verdict.** Every `success` read went through `bool()`, which
   maps `"false"`, `1`, `0` and `{"success": false}` onto a verdict without complaint.
   The g3 path was the sharp case: `results_Mitigation` is a dict, and
   `bool({"success": False})` is `True`.

Neither defect changes the numbers in the committed ledger. Both are the class of thing
that stays invisible until it silently reverses a result, so they are tested, not
merely fixed.

Runnable two ways, because the repository interpreter has no pytest:

    python3 tests/test_result_ledger.py     # self-contained runner
    pytest tests/test_result_ledger.py      # under any interpreter that has pytest
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BUILDER = REPO / "experiments" / "build_result_ledger.py"
LEDGER = REPO / "experiments" / "RESULT_LEDGER.json"


def _load_builder():
    """Import build_result_ledger.py by path; it is a script, not a package module."""
    spec = importlib.util.spec_from_file_location("build_result_ledger", BUILDER)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


BR = _load_builder()


# --------------------------------------------------------------------------
# strict boolean validation
# --------------------------------------------------------------------------

REJECTED = ["true", "false", "True", 1, 0, None, 1.0, [], {}, "1"]


def test_strict_bool_accepts_only_real_booleans():
    assert BR.strict_bool(True, "t") is True
    assert BR.strict_bool(False, "t") is False


def test_strict_bool_rejects_everything_else():
    for bad in REJECTED:
        try:
            BR.strict_bool(bad, "unit")
        except BR.LedgerDataError:
            continue
        raise AssertionError(f"strict_bool accepted {bad!r}; it must refuse it")


def test_strict_bool_message_names_the_location():
    try:
        BR.strict_bool("true", "some-run/three-state.json:faulted_in_process")
    except BR.LedgerDataError as exc:
        assert "some-run/three-state.json:faulted_in_process" in str(exc)
    else:
        raise AssertionError("strict_bool accepted the string 'true'")


def test_verdict_of_rejects_a_stringly_typed_success():
    """The three-state / g1 path."""
    block = {"finished_utc": "2026-08-26T00:00:00Z", "raw_verdict": {"success": "true"}}
    try:
        BR.verdict_of(block, "unit:faulted_in_process")
    except BR.LedgerDataError:
        return
    raise AssertionError("verdict_of coerced a string verdict")


def test_verdict_of_rejects_a_stringly_typed_worker_result():
    """The worker path, which reads through result.raw_result rather than raw_verdict."""
    block = {"finished_utc": "2026-08-26T00:00:00Z",
             "result": {"outcome": "RETURNED_TRUE", "raw_result": {"success": 1}}}
    try:
        BR.verdict_of(block, "unit:faulted_worker")
    except BR.LedgerDataError:
        return
    raise AssertionError("verdict_of coerced an integer verdict")


def test_verdict_of_accepts_a_real_boolean_on_both_paths():
    a = BR.verdict_of({"raw_verdict": {"success": True}}, "unit")
    b = BR.verdict_of({"result": {"raw_result": {"success": False}}}, "unit")
    assert a["success"] is True
    assert b["success"] is False


def test_every_success_in_the_built_ledger_is_a_real_boolean():
    """Whole-ledger sweep: no coerced verdict may survive anywhere in the output."""
    led = BR.build_ledger(BR.build_records())
    seen = 0
    for run in led["runs"]:
        for state, insts in run["verdicts"].items():
            for inst, v in insts.items():
                assert v["success"] is True or v["success"] is False, (
                    f"{run['run_dir']}/{state}/{inst} success is not a JSON boolean")
                seen += 1
    assert seen > 0, "the sweep found no verdicts at all; it would pass vacuously"
    assert led["faulted_verdicts_all_true"] is True


def test_g3_conductor_verdict_reads_the_success_member_not_the_dict():
    """`bool({"success": False})` is True. The builder must not be able to do that."""
    led = BR.build_ledger(BR.build_records())
    g3 = [r for r in led["runs"] if r["run_dir"] == "g3-run-01"]
    assert len(g3) == 1
    assert g3[0]["faulted_verdicts"]["conductor"]["success"] is True

    raw = json.loads((REPO / "experiments" / "g3-run-01" / "conductor-path.json").read_text())
    assert isinstance(raw["results_Mitigation"], dict), (
        "premise of this test: results_Mitigation is a dict on disk")
    assert raw["results_Mitigation"]["success"] is True


# --------------------------------------------------------------------------
# no filesystem-derived timestamps
# --------------------------------------------------------------------------

def test_no_verdict_carries_a_file_mtime_timestamp():
    led = BR.build_ledger(BR.build_records())
    for run in led["runs"]:
        for state, insts in run["verdicts"].items():
            for inst, v in insts.items():
                assert v.get("utc_source") != "file_mtime", (
                    f"{run['run_dir']}/{state}/{inst} still dates from st_mtime")


def test_artifacts_without_an_internal_timestamp_report_it_as_such():
    led = BR.build_ledger(BR.build_records())
    sidecars = [(r["run_dir"], state, inst, v)
                for r in led["runs"]
                for state, insts in r["verdicts"].items()
                for inst, v in insts.items()
                if v.get("artifact", "").endswith("worker/result.json")]
    assert sidecars, "no sidecar worker artifacts found; this test would pass vacuously"
    for run_dir, state, inst, v in sidecars:
        assert v["utc"] is None, f"{run_dir}/{state}/{inst} invented a timestamp"
        assert v["utc_source"] == "not_recorded_in_artifact"


def test_builder_code_never_reads_st_mtime():
    """AST, not grep: the module docstring names `st_mtime` to explain why it is gone."""
    import ast

    tree = ast.parse(BUILDER.read_text())
    banned = {"st_mtime", "getmtime", "st_ctime", "getctime"}
    hits = [n.attr for n in ast.walk(tree)
            if isinstance(n, ast.Attribute) and n.attr in banned]
    hits += [n.id for n in ast.walk(tree)
             if isinstance(n, ast.Name) and n.id in banned]
    assert not hits, f"builder still reads filesystem metadata: {sorted(set(hits))}"


# --------------------------------------------------------------------------
# historical classification
# --------------------------------------------------------------------------

EXPECTED_STATUS_COUNTS = {
    "HISTORICAL_INCLUDED": 10,
    "HISTORICAL_EXCLUDED_INERT": 1,
    "HISTORICAL_EXCLUDED_ABANDONED": 1,
    "HISTORICAL_EXCLUDED_SUPERSEDED": 1,
}


def test_historical_status_counts():
    led = BR.build_ledger(BR.build_records())
    assert led["n_runs"] == 13
    assert led["runs_by_status"] == EXPECTED_STATUS_COUNTS


def test_no_run_is_called_official():
    """`official` / OFFICIAL_FROZEN_ATTEMPT are reserved for the future frozen matrix."""
    led = BR.build_ledger(BR.build_records())
    for run in led["runs"]:
        assert run["status"] in EXPECTED_STATUS_COUNTS, run["status"]
    reserved = led["run_status_vocabulary"]["reserved"]
    assert "OFFICIAL_FROZEN_ATTEMPT" in reserved
    assert "OFFICIAL_FROZEN_ATTEMPT" not in led["runs_by_status"]


def test_measurement_counts_are_preserved():
    led = BR.build_ledger(BR.build_records())
    fsm = led["faulted_state_measurements"]
    assert fsm["per_instrument_historical_included_only"] == {
        "in_process": 9, "worker": 8, "conductor": 1}
    assert fsm["total_historical_included_only"] == 18
    assert fsm["per_instrument_all_runs"] == {
        "in_process": 11, "worker": 9, "conductor": 1}
    assert fsm["total_all_runs"] == 21


def test_generated_json_uses_the_historical_included_field_names():
    fsm = json.loads(LEDGER.read_text())["faulted_state_measurements"]
    assert "per_instrument_historical_included_only" in fsm
    assert "total_historical_included_only" in fsm
    assert not any("official_only" in k for k in fsm)


def test_included_runs_split_nine_social_network_one_hotel_reservation():
    led = BR.build_ledger(BR.build_records())
    apps = {}
    for r in led["runs"]:
        if r["status"] == "HISTORICAL_INCLUDED":
            apps[r["application"]] = apps.get(r["application"], 0) + 1
    assert apps == {"social-network": 9, "hotel-reservation": 1}


# --------------------------------------------------------------------------
# determinism and --check
# --------------------------------------------------------------------------

def test_two_renders_from_unchanged_inputs_are_byte_identical():
    a = BR.render(BR.build_ledger(BR.build_records())).encode("utf-8")
    b = BR.render(BR.build_ledger(BR.build_records())).encode("utf-8")
    assert hashlib.sha256(a).hexdigest() == hashlib.sha256(b).hexdigest()
    assert a == b


def test_committed_ledger_matches_the_recomputed_one():
    recomputed = BR.render(BR.build_ledger(BR.build_records())).encode("utf-8")
    assert LEDGER.read_bytes() == recomputed, (
        "RESULT_LEDGER.json is stale; regenerate it with "
        "python3 experiments/build_result_ledger.py")


def test_check_flag_succeeds_against_the_committed_ledger():
    r = subprocess.run([sys.executable, str(BUILDER), "--check"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_check_flag_does_not_rewrite_the_file():
    before = LEDGER.read_bytes()
    subprocess.run([sys.executable, str(BUILDER), "--check"],
                   capture_output=True, text=True)
    assert LEDGER.read_bytes() == before


def test_check_flag_fails_on_a_mutated_ledger(tmp_path=None):
    """Verify --check actually discriminates, then restore the file byte-for-byte."""
    original = LEDGER.read_bytes()
    try:
        led = json.loads(original)
        led["n_runs"] = 999
        LEDGER.write_text(json.dumps(led, indent=1, sort_keys=True) + "\n")
        r = subprocess.run([sys.executable, str(BUILDER), "--check"],
                           capture_output=True, text=True)
        assert r.returncode != 0, "--check passed a ledger that disagrees with the runs"
    finally:
        LEDGER.write_bytes(original)
    assert LEDGER.read_bytes() == original


# --------------------------------------------------------------------------
# self-contained runner, for interpreters without pytest
# --------------------------------------------------------------------------

def _main() -> int:
    tests = [(n, o) for n, o in sorted(globals().items())
             if n.startswith("test_") and callable(o)]
    failed = []
    for name, fn in tests:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - a test runner reports every failure
            failed.append((name, exc))
            print(f"FAIL {name}: {type(exc).__name__}: {exc}")
        else:
            print(f"ok   {name}")
    print(f"\n{len(tests) - len(failed)} passed, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_main())

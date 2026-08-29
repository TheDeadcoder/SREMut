"""MUTANT_LEDGER determinism, verdict type safety, and independent recomputation.

Every assertion here is recomputed from the nine `mutant-run.json` records rather than
read back out of the ledger the builder just produced. A test that only re-reads the
builder's own output tests nothing: it would pass just as happily against a builder that
had silently inverted a verdict.

The two determinism hazards inherited from RESULT_LEDGER are tested again because they
are the class of defect that produces a plausible ledger rather than an error:

1. **Coercion of a verdict.** `bool({"success": False})` is `True`. `strict_bool` must
   refuse anything that is not an actual JSON boolean.
2. **Filesystem metadata.** A ledger that reads `st_mtime` is not reproducible from the
   committed data: a fresh clone reproduces every byte and destroys every mtime.

Runnable two ways, because the repository interpreter has no pytest:

    python3 tests/test_mutant_ledger.py     # self-contained runner
    pytest tests/test_mutant_ledger.py      # under any interpreter that has pytest
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
EXP = REPO / "experiments"
BUILDER = EXP / "build_mutant_ledger.py"
LEDGER = EXP / "MUTANT_LEDGER.json"

RUN_IDS = tuple(f"ms-m{m}-{r}" for r in ("r01", "r02", "r03")
                for m in ("01", "02", "03"))
MUTANTS = ("MS-M01", "MS-M02", "MS-M03")
STATES = ("healthy", "faulted", "restored")


def _load_builder():
    spec = importlib.util.spec_from_file_location("build_mutant_ledger", BUILDER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _records() -> dict[str, dict]:
    """The nine raw run records, read straight from disk."""
    out = {}
    for rid in RUN_IDS:
        out[rid] = json.loads((EXP / rid / "mutant-run.json").read_text())
    return out


# --------------------------------------------------------------------------
# the nine records, recomputed independently of the builder
# --------------------------------------------------------------------------

def test_there_are_exactly_nine_records_all_complete():
    recs = _records()
    assert len(recs) == 9, f"expected 9 records, found {len(recs)}"
    bad = {r: d["status"] for r, d in recs.items() if d["status"] != "COMPLETE"}
    assert not bad, f"runs not COMPLETE: {bad}"


def test_fifty_four_oracle_readings_all_strictly_true():
    recs = _records()
    readings = []
    for rid, d in recs.items():
        for state in STATES:
            raw = d[f"{state}_in_process"]["raw_verdict"]
            assert set(raw) == {"success"}, f"{rid}/{state}: unexpected keys {sorted(raw)}"
            readings.append((f"{rid}/{state}/in_process", raw["success"]))
            readings.append((f"{rid}/{state}/worker",
                             d[f"{state}_worker"]["returned_boolean"]))
    assert len(readings) == 54, f"expected 54 readings, got {len(readings)}"
    not_true = [w for w, v in readings if v is not True]
    assert not not_true, f"readings that are not the boolean True: {not_true}"


def test_eighteen_of_the_readings_are_faulted():
    recs = _records()
    faulted = [(rid, cat) for rid in recs for cat in ("in_process", "worker")]
    assert len(faulted) == 18, f"expected 18 faulted readings, got {len(faulted)}"
    for rid, d in recs.items():
        assert d["faulted_in_process"]["raw_verdict"]["success"] is True, rid
        assert d["faulted_worker"]["returned_boolean"] is True, rid


def test_nine_contract_rejects_and_eighteen_control_passes():
    recs = _records()
    faulted = [d["contract_faulted"]["verdict"] for d in recs.values()]
    controls = [d[f"contract_{s}"]["verdict"]
                for d in recs.values() for s in ("healthy", "restored")]
    assert faulted == ["REJECT"] * 9, faulted
    assert controls == ["PASS"] * 18, controls


def test_violated_invariant_sets_identical_across_each_mutants_repetitions():
    recs = _records()
    for m in MUTANTS:
        sets = {rid: tuple(d["contract_faulted"]["violated"])
                for rid, d in recs.items() if d["mutant_id"] == m}
        assert len(sets) == 3, f"{m}: expected 3 repetitions, got {len(sets)}"
        distinct = set(sets.values())
        assert len(distinct) == 1, f"{m}: violated sets differ across repetitions: {sets}"


def test_ms_i6_is_not_evaluated_everywhere():
    """Declared out of scope in advance by section 6 of the pre-registration."""
    recs = _records()
    for rid, d in recs.items():
        for s in STATES:
            got = d[f"contract_{s}"]["invariants"]["MS-I6"]["result"]
            assert got == "NOT_EVALUATED", f"{rid}/{s}: MS-I6 is {got!r}"


def test_instrument_hashes_identical_across_runs_and_match_committed_files():
    mod = _load_builder()
    recs = _records()
    for key, path in mod.INSTRUMENT_FILES.items():
        seen = {d[key] for d in recs.values()}
        assert len(seen) == 1, f"{key}: differs across runs: {sorted(seen)}"
        recorded = seen.pop()
        on_disk = hashlib.sha256(path.read_bytes()).hexdigest()
        assert recorded == on_disk, (
            f"{key}: records pin {recorded}, but {path} hashes to {on_disk}. "
            f"The nine runs came from one attested instrument; a mismatch means the "
            f"file changed after the runs.")


def test_each_mutant_body_sha256_identical_across_its_three_runs():
    recs = _records()
    for m in MUTANTS:
        seen = {d["mutant"]["body_sha256"]
                for d in recs.values() if d["mutant_id"] == m}
        assert len(seen) == 1, f"{m}: body_sha256 differs across repetitions: {seen}"


def test_ms_m01_is_an_absence_mutant_and_the_others_create_a_body():
    """DEVIATIONS_AND_LIMITS.md item 2: MS-M01 is not a one-field mutation."""
    recs = _records()
    for rid, d in recs.items():
        m = d["mutant"]
        if d["mutant_id"] == "MS-M01":
            assert m["action"] == "NONE", f"{rid}: {m['action']!r}"
            assert m["body_sha256"] is None, f"{rid}: {m['body_sha256']!r}"
        else:
            assert m["action"] == "CREATE", f"{rid}: {m['action']!r}"
            assert isinstance(m["body_sha256"], str) and len(m["body_sha256"]) == 64
            assert m["exit"] == 0, f"{rid}: kubectl exit {m['exit']}"


def test_r1_interval_is_sixty_seconds_in_all_nine():
    recs = _records()
    bad = {rid: d["r1_interval_seconds_achieved"] for rid, d in recs.items()
           if d["r1_interval_seconds_achieved"] != 60.0}
    assert not bad, f"R1 not exactly 60.0s: {bad}"


def test_six_clean_pre_oracle_assertions_per_run():
    recs = _records()
    for rid, d in recs.items():
        a = d["pre_oracle_assertions"]
        assert len(a) == 6, f"{rid}: {len(a)} assertions, expected 6"
        assert all(x["ok"] is True for x in a), f"{rid}: a probe pod was present"
        assert all(x["namespace_fully_running"] is True for x in a), rid
        assert d["any_non_running_pod_at_an_oracle_call"] is False, rid


def test_activation_verified_twice_with_no_drift():
    recs = _records()
    for rid, d in recs.items():
        assert d["activation_after_apply"]["activated"] is True, rid
        assert d["activation_before_oracle"]["activated"] is True, rid
        assert d["activation_drift"] is False, rid


def test_workload_totals_agree_with_per_round_detail():
    recs = _records()
    for rid, d in recs.items():
        for s in STATES:
            w = d[f"workload_{s}"]
            req = sum(r["requests"] or 0 for r in w["detail"])
            bad = sum(r["non2xx"] for r in w["detail"])
            assert len(w["detail"]) == w["rounds"], f"{rid}/{s}"
            assert req == w["total_requests"], f"{rid}/{s}: {req} != {w['total_requests']}"
            assert bad == w["total_non2xx"], f"{rid}/{s}: {bad} != {w['total_non2xx']}"


def test_controls_have_zero_non2xx_and_faulted_has_some():
    recs = _records()
    for rid, d in recs.items():
        for s in ("healthy", "restored"):
            assert d[f"workload_{s}"]["total_non2xx"] == 0, f"{rid}/{s}"
        assert d["workload_faulted"]["total_non2xx"] > 0, rid


def test_excluding_first_round_recomputed_independently():
    """DEVIATIONS_AND_LIMITS.md item 4, recomputed here rather than trusted."""
    mod = _load_builder()
    led = json.loads(LEDGER.read_text())
    by_id = {r["run_id"]: r for r in led["runs"]}
    for rid, d in _records().items():
        rest = d["workload_faulted"]["detail"][1:]
        req = sum(r["requests"] or 0 for r in rest)
        bad = sum(r["non2xx"] for r in rest)
        expect = round(100.0 * bad / req, 4)
        got = by_id[rid]["faulted_rate_excluding_first_round"]["rate_percent"]
        assert got == expect, f"{rid}: ledger {got} != recomputed {expect}"
        assert len(rest) == 9, f"{rid}: expected 9 rounds after dropping the first"
    assert mod  # builder imported cleanly


def test_ms_i4_would_still_fail_excluding_the_first_round():
    """Excluding the straddling round changes no categorical result."""
    for rid, d in _records().items():
        rest = d["workload_faulted"]["detail"][1:]
        assert sum(r["non2xx"] for r in rest) > 0, (
            f"{rid}: MS-I4 must still FAIL with the first round excluded")
        assert d["contract_faulted"]["invariants"]["MS-I4"]["result"] == "FAIL", rid


# --------------------------------------------------------------------------
# type safety
# --------------------------------------------------------------------------

def test_strict_bool_accepts_only_real_booleans():
    mod = _load_builder()
    assert mod.strict_bool(True, "w") is True
    assert mod.strict_bool(False, "w") is False


def test_strict_bool_rejects_everything_else():
    mod = _load_builder()
    for bad in ("true", "false", 1, 0, None, {}, [], 1.0, {"success": False}):
        try:
            mod.strict_bool(bad, "where")
        except mod.MutantLedgerError:
            continue
        raise AssertionError(f"strict_bool accepted {bad!r}; it must refuse it")


def test_strict_bool_message_names_the_location():
    mod = _load_builder()
    try:
        mod.strict_bool("true", "ms-m01-r01.faulted_worker.returned_boolean")
    except mod.MutantLedgerError as exc:
        assert "ms-m01-r01.faulted_worker.returned_boolean" in str(exc)
    else:
        raise AssertionError("expected MutantLedgerError")


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
# determinism
# --------------------------------------------------------------------------

def test_two_renders_from_unchanged_inputs_are_byte_identical():
    mod = _load_builder()
    a = mod.render(mod.build_ledger(mod.build_records()))
    b = mod.render(mod.build_ledger(mod.build_records()))
    assert a == b


def test_committed_ledger_matches_the_recomputed_one():
    mod = _load_builder()
    recomputed = mod.render(mod.build_ledger(mod.build_records())).encode("utf-8")
    assert LEDGER.read_bytes() == recomputed, (
        "MUTANT_LEDGER.json is stale; regenerate with "
        "python3 experiments/build_mutant_ledger.py")


def test_check_flag_succeeds_against_the_committed_ledger():
    p = subprocess.run([sys.executable, str(BUILDER), "--check"],
                       capture_output=True, text=True)
    assert p.returncode == 0, f"--check failed:\n{p.stdout}\n{p.stderr}"


def test_check_flag_does_not_rewrite_the_file():
    original = LEDGER.read_bytes()
    subprocess.run([sys.executable, str(BUILDER), "--check"],
                   capture_output=True, text=True)
    assert LEDGER.read_bytes() == original


def test_check_flag_fails_on_a_mutated_ledger():
    original = LEDGER.read_bytes()
    doc = json.loads(original)
    doc["n_runs"] = 8
    try:
        LEDGER.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
        p = subprocess.run([sys.executable, str(BUILDER), "--check"],
                           capture_output=True, text=True)
        assert p.returncode != 0, "--check passed a mutated ledger"
    finally:
        LEDGER.write_bytes(original)
    assert LEDGER.read_bytes() == original


def test_ledger_top_level_summary_agrees_with_the_records():
    led = json.loads(LEDGER.read_text())
    assert led["n_runs"] == 9
    assert led["runs_by_status"] == {"COMPLETE": 9}
    assert led["all_runs_complete"] is True
    o = led["oracle_readings"]
    assert (o["total"], o["faulted"], o["controls"]) == (54, 18, 36)
    assert o["every_reading_true"] is True
    assert o["every_faulted_reading_true"] is True
    c = led["contract_verdicts"]
    assert c["faulted"] == {"REJECT": 9}
    assert c["controls"] == {"PASS": 18}
    assert c["every_faulted_reject"] is True
    assert c["every_control_pass"] is True
    for key, info in led["instrument_sha256"].items():
        assert info["identical_across_runs"] is True, key
        assert info["matches_committed_file"] is True, key
    for m, info in led["per_mutant"].items():
        assert info["n_runs"] == 3, m
        assert info["violated_identical_across_repetitions"] is True, m
        assert info["body_sha256_identical_across_repetitions"] is True, m


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

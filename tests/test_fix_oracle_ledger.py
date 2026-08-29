"""FIX_ORACLE_LEDGER determinism, verdict type safety, and independent recomputation.

Every assertion recomputes from the three `fix-oracle-run.json` records rather than
reading back the ledger the builder just produced. A test that only re-reads the builder's
own output would pass just as happily against a builder that had inverted a verdict.

The study these records carry is a falsification test, so the load-bearing guarantee is
that a verdict is a real JSON boolean read from `evaluate()` — never coerced.
`bool({"success": False})` is `True`, and `service_endpoint_mitigation.py:178` already
turns an `AttributeError` into `{"success": False}`, so a coercion here would be invisible
and fatal.

    python3 tests/test_fix_oracle_ledger.py     # self-contained runner
    pytest tests/test_fix_oracle_ledger.py      # under any interpreter with pytest
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
EXP = REPO / "experiments"
BUILDER = EXP / "build_fix_oracle_ledger.py"
LEDGER = EXP / "FIX_ORACLE_LEDGER.json"

RUN_IDS = ("fix-m01-r01", "fix-m02-r01", "fix-m03-r01")
STATES = ("healthy", "faulted", "restored")
CONTROLS = ("healthy", "restored")
CONFIGS = ("O1", "O2", "O3", "O4")


def _load_builder():
    spec = importlib.util.spec_from_file_location("build_fix_oracle_ledger", BUILDER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _records() -> dict[str, dict]:
    return {rid: json.loads((EXP / rid / "fix-oracle-run.json").read_text())
            for rid in RUN_IDS}


def _verdict(d: dict, state: str, cfg: str):
    return d[f"oracles_{state}"][cfg]["verdict"]


# --------------------------------------------------------------------------
# recomputed independently of the builder
# --------------------------------------------------------------------------

def test_three_records_all_complete():
    recs = _records()
    assert len(recs) == 3
    bad = {r: d["status"] for r, d in recs.items() if d["status"] != "COMPLETE"}
    assert not bad, f"runs not COMPLETE: {bad}"


def test_thirty_six_verdicts_all_real_json_booleans():
    recs = _records()
    n = 0
    for rid, d in recs.items():
        for state in STATES:
            for cfg in CONFIGS:
                v = _verdict(d, state, cfg)
                assert v is True or v is False, (
                    f"{rid}/{state}/{cfg}: verdict is {v!r} ({type(v).__name__})")
                n += 1
    assert n == 36, f"expected 36 verdicts, got {n}"


def test_o1_true_in_all_nine_states():
    for rid, d in _records().items():
        for state in STATES:
            assert _verdict(d, state, "O1") is True, f"{rid}/{state}/O1"


def test_o2_false_in_all_nine_states():
    for rid, d in _records().items():
        for state in STATES:
            assert _verdict(d, state, "O2") is False, f"{rid}/{state}/O2"


def test_o3_and_o4_true_on_controls_false_on_faulted():
    for rid, d in _records().items():
        for cfg in ("O3", "O4"):
            for state in CONTROLS:
                assert _verdict(d, state, cfg) is True, f"{rid}/{state}/{cfg}"
            assert _verdict(d, "faulted", cfg) is False, f"{rid}/faulted/{cfg}"


def test_o4_equals_o1_and_o3_in_all_nine_states():
    """compound.py:40-41 ANDs its children; this checks the composition behaves so."""
    for rid, d in _records().items():
        for state in STATES:
            o1, o3, o4 = (_verdict(d, state, c) for c in ("O1", "O3", "O4"))
            assert o4 is (o1 and o3), f"{rid}/{state}: O4={o4} but O1={o1} O3={o3}"


def test_o4_children_are_recorded_and_agree_with_the_composite():
    for rid, d in _records().items():
        for state in STATES:
            raw = d[f"oracles_{state}"]["O4"]["returned_object"]
            children = {c["name"]: c["success"] for c in raw["oracles"]}
            assert set(children) == {"0-MitigationOracle",
                                     "1-ServiceEndpointMitigationOracle"}, children
            for name, ok in children.items():
                assert ok is True or ok is False, f"{rid}/{state}/{name}: {ok!r}"
            assert raw["success"] is all(children.values()), f"{rid}/{state}"


def test_all_nine_o2_diagnostics_raised_attributeerror():
    seen = set()
    for rid, d in _records().items():
        for state in STATES:
            dp = d[f"oracles_{state}"]["O2"]["direct_probe_call"]
            assert dp["exception_type"] == "AttributeError", (
                f"{rid}/{state}/O2: {dp['exception_type']!r}")
            assert dp["returned"] is None, f"{rid}/{state}/O2"
            seen.add(dp["exception_message"])
    assert len(seen) == 1, f"expected one distinct message, got {seen}"
    assert "expected_service_port" in seen.pop()


def test_all_thirty_six_barriers_cleared():
    for rid, d in _records().items():
        for state in STATES:
            for cfg in CONFIGS:
                b = d[f"oracles_{state}"][cfg]["probe_pod_barrier"]
                assert b["cleared"] is True, f"{rid}/{state}/{cfg}: barrier not cleared"


def test_attribute_absent_for_o1_o2_set_for_o3_o4_and_cleared_after_all():
    for rid, d in _records().items():
        for state in STATES:
            for cfg in CONFIGS:
                rec = d[f"oracles_{state}"][cfg]
                before = rec["attribute_before_call"]
                if cfg in ("O1", "O2"):
                    assert before == "<ABSENT>", f"{rid}/{state}/{cfg}: {before!r}"
                else:
                    assert before == 9090, f"{rid}/{state}/{cfg}: {before!r}"
                assert rec["attribute_after_cleanup"] == "<ABSENT>", (
                    f"{rid}/{state}/{cfg}: attribute leaked")


def test_instrument_hashes_identical_across_runs_and_match_committed_files():
    mod = _load_builder()
    recs = _records()
    for key, path in mod.INSTRUMENT_FILES.items():
        seen = {d[key] for d in recs.values()}
        assert len(seen) == 1, f"{key}: differs across runs: {sorted(seen)}"
        recorded = seen.pop()
        on_disk = hashlib.sha256(path.read_bytes()).hexdigest()
        assert recorded == on_disk, (
            f"{key}: records pin {recorded}, {path} hashes to {on_disk}. All three runs "
            f"came from one instrument; a mismatch means the file changed after them.")


def test_predictions_matched_is_computed_not_asserted():
    """The ledger's own flag must be reproducible from the records by hand."""
    predicted_control = {"O1": True, "O2": False, "O3": True, "O4": True}
    predicted_faulted = {"O1": True, "O2": False, "O3": False, "O4": False}
    mismatches = []
    for rid, d in _records().items():
        for state in STATES:
            table = predicted_faulted if state == "faulted" else predicted_control
            for cfg in CONFIGS:
                if _verdict(d, state, cfg) is not table[cfg]:
                    mismatches.append((rid, state, cfg))
    led = json.loads(LEDGER.read_text())
    assert led["predictions_matched"] is (not mismatches)
    assert led["prediction_mismatches"] == [] if not mismatches else True
    assert led["cells_compared"] == 36


# --------------------------------------------------------------------------
# type safety and determinism
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
        except mod.FixOracleLedgerError:
            continue
        raise AssertionError(f"strict_bool accepted {bad!r}")


def test_builder_code_never_reads_st_mtime():
    """AST, not grep: the docstring may legitimately name the thing it forbids."""
    tree = ast.parse(BUILDER.read_text())
    banned = {"st_mtime", "getmtime", "st_ctime", "getctime"}
    hits = [n.attr for n in ast.walk(tree)
            if isinstance(n, ast.Attribute) and n.attr in banned]
    hits += [n.id for n in ast.walk(tree)
             if isinstance(n, ast.Name) and n.id in banned]
    assert not hits, f"builder reads filesystem metadata: {sorted(set(hits))}"


def test_two_renders_from_unchanged_inputs_are_byte_identical():
    mod = _load_builder()
    a = mod.render(mod.build_ledger(mod.build_records()))
    b = mod.render(mod.build_ledger(mod.build_records()))
    assert a == b


def test_committed_ledger_matches_the_recomputed_one():
    mod = _load_builder()
    recomputed = mod.render(mod.build_ledger(mod.build_records())).encode("utf-8")
    assert LEDGER.read_bytes() == recomputed, (
        "FIX_ORACLE_LEDGER.json is stale; regenerate with "
        "python3 experiments/build_fix_oracle_ledger.py")


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
    doc["predictions_matched"] = False
    try:
        LEDGER.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
        p = subprocess.run([sys.executable, str(BUILDER), "--check"],
                           capture_output=True, text=True)
        assert p.returncode != 0, "--check passed a mutated ledger"
    finally:
        LEDGER.write_bytes(original)
    assert LEDGER.read_bytes() == original


# --------------------------------------------------------------------------
def _main() -> int:
    tests = [(n, o) for n, o in sorted(globals().items())
             if n.startswith("test_") and callable(o)]
    failed = []
    for name, fn in tests:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            failed.append((name, exc))
            print(f"FAIL {name}: {type(exc).__name__}: {exc}")
        else:
            print(f"ok   {name}")
    print(f"\n{len(tests) - len(failed)} passed, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_main())

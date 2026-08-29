"""Shape and type safety for fix-oracle-run.json records.

The study these records carry is a falsification test: `analysis/PR_PLAN.md` predicts
that the un-patched `ServiceEndpointMitigationOracle` rejects correct repairs. A record
whose verdicts were coerced rather than read would make that test meaningless — an
`AttributeError` swallowed at `service_endpoint_mitigation.py:178` already produces
`{"success": False}`, so the one thing this file must guarantee is that every recorded
verdict is a real JSON boolean read from `evaluate()`, and that the diagnostic direct
call is kept strictly separate from it.

Skips cleanly when no run has been executed yet, so it is committable alongside the
driver and before the runs.

    python3 tests/test_fix_oracle_record.py     # self-contained runner
    pytest tests/test_fix_oracle_record.py      # under any interpreter with pytest
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
EXP = REPO / "experiments"
DRIVER = EXP / "fix_oracle_run.py"
RUN_IDS = ("fix-m01-r01", "fix-m02-r01", "fix-m03-r01")
CONFIGS = ("O1", "O2", "O3", "O4")
STATES = ("healthy", "faulted", "restored")


def _records() -> dict[str, dict]:
    out = {}
    for rid in RUN_IDS:
        p = EXP / rid / "fix-oracle-run.json"
        if p.is_file():
            out[rid] = json.loads(p.read_text())
    return out


# --------------------------------------------------------------------------
# driver-level invariants, checkable before any run exists
# --------------------------------------------------------------------------

def test_driver_parses_and_declares_the_four_configurations():
    mod = ast.parse(DRIVER.read_text())
    consts = {t.id: n.value for n in mod.body if isinstance(n, ast.Assign)
              for t in n.targets if isinstance(t, ast.Name)}
    assert "CONFIGS" in consts, "driver must declare CONFIGS"
    assert [e.value for e in consts["CONFIGS"].elts] == list(CONFIGS)
    assert [e.value for e in consts["STATES"].elts] == list(STATES)
    assert consts["EXPECTED_SERVICE_PORT"].value == 9090
    assert consts["PROTOCOL"].value == "sremut-fix-oracle-v1"


def test_driver_never_coerces_a_verdict_with_bool():
    """`bool({"success": False})` is True. A verdict must never pass through bool()."""
    src = DRIVER.read_text()
    tree = ast.parse(src)
    bad = [n.lineno for n in ast.walk(tree)
           if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
           and n.func.id == "bool"
           and not (n.args and isinstance(n.args[0], ast.BoolOp))]
    assert not bad, f"bool() applied near a verdict at lines {bad}"


def test_driver_does_not_write_into_the_sregym_submodule():
    src = DRIVER.read_text()
    for forbidden in ("write_text", "write_bytes", "open("):
        for line in src.splitlines():
            if forbidden in line and "SREGYM_ROOT" in line:
                raise AssertionError(f"driver may write into SREGym/: {line.strip()}")


# --------------------------------------------------------------------------
# record-level invariants, once runs exist
# --------------------------------------------------------------------------

def test_every_recorded_verdict_is_a_real_json_boolean():
    recs = _records()
    if not recs:
        print("   (skipped: no fix-oracle records yet)")
        return
    for rid, d in recs.items():
        for state in STATES:
            block = d.get(f"oracles_{state}")
            if block is None:
                continue
            for cfg in CONFIGS:
                v = block[cfg]["verdict"]
                assert v is True or v is False, (
                    f"{rid}/{state}/{cfg}: verdict is {v!r} ({type(v).__name__}); "
                    f"it must be an actual JSON boolean")


def test_returned_object_carries_a_success_key():
    recs = _records()
    if not recs:
        print("   (skipped: no fix-oracle records yet)")
        return
    for rid, d in recs.items():
        for state in STATES:
            block = d.get(f"oracles_{state}")
            if block is None:
                continue
            for cfg in CONFIGS:
                raw = block[cfg]["returned_object"]
                assert isinstance(raw, dict) and "success" in raw, (
                    f"{rid}/{state}/{cfg}: returned_object = {raw!r}")
                assert raw["success"] is block[cfg]["verdict"], (
                    f"{rid}/{state}/{cfg}: verdict disagrees with returned_object")


def test_attribute_observed_before_every_call_and_cleared_after():
    """The O2/O3 distinction must be evidenced, not asserted."""
    recs = _records()
    if not recs:
        print("   (skipped: no fix-oracle records yet)")
        return
    for rid, d in recs.items():
        for state in STATES:
            block = d.get(f"oracles_{state}")
            if block is None:
                continue
            for cfg in CONFIGS:
                rec = block[cfg]
                assert "attribute_before_call" in rec, f"{rid}/{state}/{cfg}"
                assert rec["attribute_after_cleanup"] == "<ABSENT>", (
                    f"{rid}/{state}/{cfg}: attribute leaked after the call: "
                    f"{rec['attribute_after_cleanup']!r}")
                if cfg in ("O1", "O2"):
                    assert rec["attribute_before_call"] == "<ABSENT>", (
                        f"{rid}/{state}/{cfg}: expected the attribute absent")
                else:
                    assert rec["attribute_before_call"] == 9090, (
                        f"{rid}/{state}/{cfg}: expected the attribute set to 9090")
                if cfg == "O2":
                    assert rec["absence_asserted"] is True, f"{rid}/{state}/O2"


def test_diagnostic_probe_is_separate_from_the_verdict():
    recs = _records()
    if not recs:
        print("   (skipped: no fix-oracle records yet)")
        return
    for rid, d in recs.items():
        for state in STATES:
            block = d.get(f"oracles_{state}")
            if block is None:
                continue
            assert "direct_probe_call" not in block["O1"], (
                f"{rid}/{state}/O1: the stock oracle has no connectivity probe")
            for cfg in ("O2", "O3", "O4"):
                dp = block[cfg]["direct_probe_call"]
                assert dp["reads_attribute_at"] == "service_endpoint_mitigation.py:65"
                # diagnostic only: it must not be the verdict
                assert dp["returned"] is not block[cfg] , "sanity"
                assert set(dp) >= {"returned", "exception_type", "exception_message",
                                   "attribute_before_call"}, f"{rid}/{state}/{cfg}"


def test_o1_and_o4_use_the_healthy_baseline_not_a_self_capture():
    """A baseline captured at evaluate time compares the graded state against itself."""
    recs = _records()
    if not recs:
        print("   (skipped: no fix-oracle records yet)")
        return
    for rid, d in recs.items():
        expected = d.get("captured_replica_baseline_count")
        for state in STATES:
            block = d.get(f"oracles_{state}")
            if block is None:
                continue
            for cfg in ("O1", "O4"):
                rec = block[cfg]
                assert rec["baseline_source"] == "healthy capture at STEP 4", (
                    f"{rid}/{state}/{cfg}: {rec.get('baseline_source')!r}")
                assert rec["baseline_deployment_count"] == expected, (
                    f"{rid}/{state}/{cfg}: baseline has "
                    f"{rec['baseline_deployment_count']} deployments, healthy capture "
                    f"had {expected}")


def test_driver_never_calls_capture_baseline_inside_an_evaluation():
    """O1/O4 must inherit the healthy baseline, never re-capture in the graded state."""
    tree = ast.parse(DRIVER.read_text())
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_evaluate_one")
    bad = [n.lineno for n in ast.walk(fn)
           if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
           and n.func.attr == "capture_baseline"]
    assert not bad, f"capture_baseline() called inside _evaluate_one at lines {bad}"


def test_pod_census_recorded_before_every_configuration():
    """SREGym's own probe pod carries labels our assertion cannot see."""
    recs = _records()
    if not recs:
        print("   (skipped: no fix-oracle records yet)")
        return
    for rid, d in recs.items():
        for state in STATES:
            block = d.get(f"oracles_{state}")
            if block is None:
                continue
            for cfg in CONFIGS:
                pods = block[cfg]["pods_before_call"]
                assert isinstance(pods.get("total"), int), f"{rid}/{state}/{cfg}"
                assert isinstance(pods.get("not_running"), list), f"{rid}/{state}/{cfg}"
                assert "sregym_connectivity_probe_pods" in pods, f"{rid}/{state}/{cfg}"


def test_pinned_module_hashes_recorded_and_match_disk():
    import hashlib
    recs = _records()
    if not recs:
        print("   (skipped: no fix-oracle records yet)")
        return
    sregym = REPO.parent / "SREGym"
    paths = {
        "service_endpoint_mitigation.py":
            sregym / "sregym/conductor/oracles/service_endpoint_mitigation.py",
        "compound.py": sregym / "sregym/conductor/oracles/compound.py",
        "mitigation.py": sregym / "sregym/conductor/oracles/mitigation.py",
        "missing_service.py": sregym / "sregym/conductor/problems/missing_service.py",
    }
    for rid, d in recs.items():
        pinned = d["pinned_module_sha256"]
        for name, path in paths.items():
            on_disk = hashlib.sha256(path.read_bytes()).hexdigest()
            assert pinned[name] == on_disk, (
                f"{rid}: {name} recorded {pinned[name]}, disk has {on_disk} — "
                f"SREGym changed after the run")


def test_single_provenance_is_declared():
    recs = _records()
    if not recs:
        print("   (skipped: no fix-oracle records yet)")
        return
    for rid, d in recs.items():
        assert d["single_provenance"] is True, rid
        assert d["protocol"] == "sremut-fix-oracle-v1", rid
        assert d["expected_service_port_used"] == 9090, rid


def test_all_twelve_cells_present_when_a_run_completes():
    recs = _records()
    if not recs:
        print("   (skipped: no fix-oracle records yet)")
        return
    for rid, d in recs.items():
        if d.get("status") != "COMPLETE":
            continue
        for state in STATES:
            block = d[f"oracles_{state}"]
            assert set(block) == set(CONFIGS), f"{rid}/{state}: {sorted(block)}"


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

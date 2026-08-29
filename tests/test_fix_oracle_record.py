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


def test_probe_pod_barrier_cleared_before_every_configuration():
    """SREGym's probe pod is deleted fire-and-forget; no verdict may be taken while
    one is still present (service_endpoint_mitigation.py:94, :111-115)."""
    recs = _records()
    if not recs:
        print("   (skipped: no fix-oracle records yet)")
        return
    for rid, d in recs.items():
        for state in STATES:
            block = d.get(f"oracles_{state}")
            if block is None:
                continue
            for cfg, rec in block.items():
                b = rec.get("probe_pod_barrier")
                assert b is not None, f"{rid}/{state}/{cfg}: no probe_pod_barrier"
                assert b["label_selector"] == "app=service-connectivity-check"
                assert isinstance(b["waited_seconds"], (int, float))
                assert isinstance(b["pods_seen"], list)
                # A recorded verdict implies the barrier cleared; an uncleared barrier
                # must have aborted the run instead.
                if "verdict" in rec:
                    assert b["cleared"] is True, (
                        f"{rid}/{state}/{cfg}: a verdict was recorded while the "
                        f"barrier reported cleared=False")


def test_driver_barrier_never_deletes_a_pod():
    """The barrier surfaces a residual pod; it must not paper over it by deleting."""
    tree = ast.parse(DRIVER.read_text())
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_probe_pod_barrier")
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            args = [a.value for a in node.args if isinstance(a, ast.Constant)]
            assert "delete" not in args, (
                f"_probe_pod_barrier issues a delete at line {node.lineno}; it must "
                f"only observe")


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
    # SREGym is a sibling checkout, not part of this repository. A clone without it
    # must skip rather than raise FileNotFoundError: the hashes are still recorded in
    # every run record, they simply cannot be re-verified against disk here.
    absent = [str(p) for p in paths.values() if not p.is_file()]
    if absent:
        print(f"   (skipped: SREGym checkout not present: {absent[0]})")
        return
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
# the barrier's blocking path, exercised directly
# --------------------------------------------------------------------------

def _load_driver():
    """Import fix_oracle_run with the SREGym imports stubbed.

    The driver imports `sregym.*` for the oracle classes, which live in a sibling
    checkout under its own venv. None of that is needed to exercise
    `_probe_pod_barrier`, which touches only the module-level `k` and `_kjson`, so the
    import surface is stubbed and the test stays hermetic: no cluster, no subprocess,
    no venv.
    """
    import sys
    import types

    made = []
    for name, attrs in (
        ("sregym", ()), ("sregym.conductor", ()),
        ("sregym.conductor.conductor", ("Conductor", "ConductorConfig")),
        ("sregym.conductor.oracles", ()),
        ("sregym.conductor.oracles.compound", ("CompoundedOracle",)),
        ("sregym.conductor.oracles.mitigation", ("MitigationOracle",)),
        ("sregym.conductor.oracles.service_endpoint_mitigation",
         ("ServiceEndpointMitigationOracle",)),
    ):
        if name not in sys.modules:
            mod = types.ModuleType(name)
            for a in attrs:
                setattr(mod, a, type(a, (), {}))
            sys.modules[name] = mod
            made.append(name)
    exp = str(EXP)
    added = exp not in sys.path
    if added:
        sys.path.insert(0, exp)
    import importlib
    return importlib.import_module("fix_oracle_run")


class _Proc:
    """Minimal stand-in for subprocess.CompletedProcess."""

    def __init__(self, stdout="", returncode=0, stderr=""):
        self.stdout, self.returncode, self.stderr = stdout, returncode, stderr


def _run_barrier(pods_json, recheck_stdout, recheck_rc=0, **kw):
    """Drive `_probe_pod_barrier` with both cluster reads stubbed.

    Returns (record, calls) where `calls` is every argv the barrier passed to `k`.
    """
    mod = _load_driver()
    real_k, real_kjson = mod.k, mod._kjson
    calls = []

    def fake_k(*args):
        calls.append(args)
        if args and args[0] == "wait":
            return _Proc(returncode=1, stderr="timed out waiting for the condition")
        return _Proc(stdout=recheck_stdout, returncode=recheck_rc)

    def fake_kjson(*args):
        return pods_json, {"argv": list(args), "exit": 0}

    mod.k, mod._kjson = fake_k, fake_kjson
    try:
        return mod._probe_pod_barrier(**kw), calls
    finally:
        mod.k, mod._kjson = real_k, real_kjson


def test_barrier_blocks_while_a_probe_pod_is_present():
    present = {"items": [{"metadata": {"name": "stub-probe"},
                          "status": {"phase": "Succeeded"}}]}
    rec, calls = _run_barrier(present, "stub-probe\n",
                              deadline_seconds=2, poll_seconds=1)
    assert rec["cleared"] is False, "barrier cleared while a pod was present"
    assert "stub-probe" in rec["pods_seen"], rec["pods_seen"]
    assert rec["waited_seconds"] >= 2, rec["waited_seconds"]
    assert len(rec["attempts"]) > 1, f"only {len(rec['attempts'])} attempt(s)"
    assert not any(a and a[0] == "delete" for a in calls), calls


def test_barrier_clears_on_an_empty_namespace_via_the_independent_recheck():
    rec, calls = _run_barrier({"items": []}, "", deadline_seconds=2, poll_seconds=1)
    assert rec["cleared"] is True
    assert rec["pods_seen"] == []
    assert len(rec["attempts"]) == 1, f"{len(rec['attempts'])} attempts"
    only = rec["attempts"][0]
    assert only["recheck"] == {"exit": 0, "names": [], "stderr": ""}, only["recheck"]
    assert "wait" not in only, "clearance must come from the re-check, not from wait"
    assert not any(a and a[0] == "delete" for a in calls), calls


def test_barrier_does_not_treat_a_failed_read_as_clearance():
    """`_kjson` returns None both for genuine absence and for a failed read.

    The independent re-check is what distinguishes them. If the listing fails while a
    pod is in fact present, the barrier must not clear.
    """
    rec, calls = _run_barrier(None, "stub-probe\n",
                              deadline_seconds=2, poll_seconds=1)
    assert rec["cleared"] is False, (
        "barrier treated a failed listing as clearance while the re-check reported a "
        "pod present")
    assert not any(a and a[0] == "delete" for a in calls), calls


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

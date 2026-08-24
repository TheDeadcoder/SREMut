"""Offline regressions for the isolated stock MitigationOracle adapter."""

from __future__ import annotations

import ast
from collections import UserDict
import copy
import hashlib
import inspect
from pathlib import Path
import subprocess
import sys
import tempfile
from types import MappingProxyType, ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

from sremut.canonical_json import canonical_json_bytes, parse_canonical_json
from sremut.original_oracle_adapter import (
    EVIDENCE_ROLES,
    EXPECTED_DEPENDENCIES,
    EXPECTED_ORACLE_SHA256,
    EXPECTED_SREGYM_COMMIT,
    OriginalOracleAdapter,
    OriginalOracleAdapterError,
    OriginalOracleInput,
    _validate_worker_result,
    extract_returned_boolean,
    verify_stock_provenance,
)
from sremut import original_oracle_adapter as adapter_module
from sremut import original_oracle_worker as worker_module
from sremut.policy_runtime import POLICY_MANIFEST_SHA256, load_policy_bundle


REPOSITORY = Path(__file__).resolve().parents[1]


def load_policy():
    return load_policy_bundle(
        REPOSITORY / "policies/missing_service_social_network/evidence-capture-v1.1.yaml",
        REPOSITORY / "schemas/evidence-capture-policy-v1.1.schema.json",
        REPOSITORY / "EVIDENCE_CAPTURE_POLICY_V1_1_SHA256SUMS",
        expected_manifest_sha256=POLICY_MANIFEST_SHA256,
    )


def logical_input(**changes):
    value = {
        "kubernetes_context": "kind-kind",
        "namespace": "social-network",
        "captured_replica_baseline": {"user-service": 1, "home-timeline-service": 1},
        "evidence_paths": {
            "original_oracle_input": "oracle/input.json",
            "original_oracle_stdout": "oracle/stdout.bin",
            "original_oracle_stderr": "oracle/stderr.bin",
            "original_oracle_result": "oracle/result.json",
        },
    }
    value.update(changes)
    return value


def provenance():
    return MappingProxyType(
        {
            "dependency_versions": EXPECTED_DEPENDENCIES,
            "oracle_module_sha256": EXPECTED_ORACLE_SHA256,
            "python_executable_sha256": "0" * 64,
            "python_version": "3.12.3",
            "sregym_commit": EXPECTED_SREGYM_COMMIT,
        }
    )


def worker_result(success=True):
    raw = {"success": success}
    return {
        "outcome": "RETURNED_TRUE" if success else "RETURNED_FALSE",
        "exception_type": None,
        "raw_result": raw,
        "raw_result_sha256": hashlib.sha256(canonical_json_bytes(raw)).hexdigest(),
        "returned_boolean": success,
        "runtime": {
            "dependency_versions": dict(EXPECTED_DEPENDENCIES),
            "oracle_module_sha256": EXPECTED_ORACLE_SHA256,
            "python_version": "3.12.3",
            "sregym_commit": EXPECTED_SREGYM_COMMIT,
        },
        "schema_version": 1,
    }


class DictSubclass(dict):
    pass


class Truthy:
    def __bool__(self):
        return True


class StrictReturnTests(unittest.TestCase):
    def test_exact_builtin_dict_true_and_false(self):
        self.assertIs(extract_returned_boolean({"success": True}), True)
        self.assertIs(extract_returned_boolean({"success": False}), False)
        self.assertIs(worker_module.extract_returned_boolean({"success": True}), True)
        self.assertIs(worker_module.extract_returned_boolean({"success": False}), False)

    def test_wrong_container_shapes_reject(self):
        values = [True, False, {}, {"other": True}, {"success": True, "extra": 1}]
        for value in values:
            with self.subTest(value=value):
                with self.assertRaisesRegex(OriginalOracleAdapterError, "ORIGINAL_ORACLE_RETURN_SHAPE_INVALID"):
                    extract_returned_boolean(value)

    def test_truthy_and_non_boolean_success_values_reject(self):
        for value in (1, 0, "true", "false", None, Truthy()):
            with self.subTest(value=type(value).__name__):
                with self.assertRaisesRegex(OriginalOracleAdapterError, "ORIGINAL_ORACLE_RETURN_SHAPE_INVALID"):
                    extract_returned_boolean({"success": value})

    def test_dict_subclass_and_arbitrary_mapping_reject(self):
        for value in (DictSubclass(success=True), UserDict(success=True)):
            with self.assertRaisesRegex(OriginalOracleAdapterError, "ORIGINAL_ORACLE_RETURN_SHAPE_INVALID"):
                extract_returned_boolean(value)

    def test_parent_worker_agree_and_raw_identity_is_exact(self):
        for success in (True, False):
            value = worker_result(success)
            outcome, returned, raw = _validate_worker_result(value, provenance())
            self.assertEqual(outcome, value["outcome"])
            self.assertIs(returned, success)
            self.assertEqual(canonical_json_bytes(raw), canonical_json_bytes({"success": success}))

    def test_no_truthiness_normalization_and_mutation_is_detected(self):
        for function in (extract_returned_boolean, worker_module.extract_returned_boolean):
            source = inspect.getsource(function)
            tree = ast.parse(source)
            self.assertFalse(
                any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "bool" for node in ast.walk(tree))
            )
        mutated = lambda raw: bool(raw.get("success"))
        self.assertTrue(mutated({"success": "false"}))
        with self.assertRaises(OriginalOracleAdapterError):
            extract_returned_boolean({"success": "false"})


class InputBoundaryTests(unittest.TestCase):
    def test_exact_canonical_input_and_immutability(self):
        source = logical_input()
        parsed = OriginalOracleInput.from_mapping(source)
        encoded = canonical_json_bytes(parsed.as_dict())
        self.assertEqual(parse_canonical_json(encoded), source)
        source["captured_replica_baseline"]["user-service"] = 9
        self.assertEqual(parsed.captured_replica_baseline["user-service"], 1)
        with self.assertRaises(TypeError):
            parsed.captured_replica_baseline["x"] = 1

    def test_missing_extra_context_and_namespace_reject(self):
        values = []
        missing = logical_input(); missing.pop("namespace"); values.append(missing)
        extra = logical_input(extra=True); values.append(extra)
        values.append(logical_input(kubernetes_context="other"))
        values.append(logical_input(namespace="default"))
        for value in values:
            with self.subTest(keys=sorted(value)):
                with self.assertRaisesRegex(OriginalOracleAdapterError, "ORIGINAL_ORACLE_INPUT_INVALID"):
                    OriginalOracleInput.from_mapping(value)

    def test_malformed_baselines_reject(self):
        baselines = [{}, {"User_Service": 1}, {"user-service": -1}, {"user-service": True}, {1: 1}]
        for baseline in baselines:
            with self.subTest(baseline=baseline):
                with self.assertRaises(OriginalOracleAdapterError):
                    OriginalOracleInput.from_mapping(logical_input(captured_replica_baseline=baseline))

    def test_unsafe_duplicate_and_malformed_evidence_paths_reject(self):
        unsafe = ["/tmp/out", "../out", "a//out", "a/./out", "a\\out"]
        for path in unsafe:
            paths = dict(logical_input()["evidence_paths"])
            paths["original_oracle_result"] = path
            with self.subTest(path=path):
                with self.assertRaises(OriginalOracleAdapterError):
                    OriginalOracleInput.from_mapping(logical_input(evidence_paths=paths))
        duplicate = dict(logical_input()["evidence_paths"])
        duplicate["original_oracle_result"] = duplicate["original_oracle_input"]
        with self.assertRaises(OriginalOracleAdapterError):
            OriginalOracleInput.from_mapping(logical_input(evidence_paths=duplicate))

    def test_worker_revalidates_exact_canonical_input(self):
        value = logical_input()
        self.assertEqual(worker_module.validate_input(copy.deepcopy(value)), value)
        for mutation in ({**value, "extra": 1}, {**value, "namespace": "default"}):
            with self.assertRaises(worker_module.WorkerFailure):
                worker_module.validate_input(mutation)


class FakePopen:
    calls = []
    value = worker_result(True)
    stdout_value = b"stock stdout"
    stderr_value = b""
    exit_value = 0
    communicate_failure = None

    def __init__(self, argv, **kwargs):
        type(self).calls.append((list(argv), dict(kwargs)))
        self.argv = argv
        self.pid = 987654
        self.returncode = type(self).exit_value
        self._communicated = False

    def communicate(self, timeout=None):
        if type(self).communicate_failure is not None and not self._communicated:
            self._communicated = True
            raise type(self).communicate_failure
        if type(self).value is not None:
            Path(self.argv[-1]).write_bytes(canonical_json_bytes(type(self).value))
        return type(self).stdout_value, type(self).stderr_value

    def poll(self):
        return self.returncode

    def wait(self):
        return self.returncode


class AdapterInvocationTests(unittest.TestCase):
    def setUp(self):
        FakePopen.calls = []
        FakePopen.value = worker_result(True)
        FakePopen.stdout_value = b"stock stdout"
        FakePopen.stderr_value = b""
        FakePopen.exit_value = 0
        FakePopen.communicate_failure = None
        self.policy = load_policy()

    def invoke(self, root):
        with patch.object(adapter_module, "verify_stock_provenance", return_value=provenance()), patch.object(
            adapter_module.subprocess, "Popen", FakePopen
        ):
            return OriginalOracleAdapter(self.policy, timeout_seconds=90).invoke(logical_input(), attempt_root=root)

    def test_true_false_and_exactly_one_launch(self):
        for success in (True, False):
            FakePopen.calls = []
            FakePopen.value = worker_result(success)
            with tempfile.TemporaryDirectory() as directory:
                result = self.invoke(Path(directory))
                self.assertIs(result.returned_boolean, success)
                self.assertEqual(dict(result.raw_result), {"success": success})
                self.assertEqual(result.outcome, "RETURNED_TRUE" if success else "RETURNED_FALSE")
                self.assertEqual(len(FakePopen.calls), 1)
                argv, kwargs = FakePopen.calls[0]
                self.assertEqual(argv[:3], [str(adapter_module.STOCK_PYTHON), "-I", "-B"])
                self.assertIs(kwargs["shell"], False)

    def test_exception_is_distinct_from_false(self):
        value = worker_result(True)
        value.update(outcome="ORACLE_EXCEPTION", exception_type="RuntimeError", raw_result=None, raw_result_sha256=None, returned_boolean=None)
        FakePopen.value = value
        with tempfile.TemporaryDirectory() as directory:
            result = self.invoke(Path(directory))
        self.assertEqual(result.outcome, "ORACLE_EXCEPTION")
        self.assertIsNone(result.returned_boolean)

    def test_invalid_worker_output_and_nonzero_exit_are_distinct(self):
        FakePopen.value = {"bad": True}
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(self.invoke(Path(directory)).outcome, "INVALID_WORKER_OUTPUT")
        FakePopen.calls = []
        FakePopen.value = None
        FakePopen.exit_value = 9
        with tempfile.TemporaryDirectory() as directory:
            result = self.invoke(Path(directory))
        self.assertEqual(result.outcome, "NONZERO_EXIT")
        self.assertEqual(result.exit_status, 9)
        self.assertEqual(len(FakePopen.calls), 1)

    def test_timeout_is_reaped_without_retry(self):
        FakePopen.value = None
        FakePopen.communicate_failure = subprocess.TimeoutExpired("worker", 1)
        with tempfile.TemporaryDirectory() as directory, patch.object(adapter_module.os, "killpg"):
            result = self.invoke(Path(directory))
        self.assertEqual(result.outcome, "TIMEOUT")
        self.assertIsNone(result.returned_boolean)
        self.assertEqual(len(FakePopen.calls), 1)

    def test_output_reuse_rejects_before_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "oracle").mkdir()
            (root / "oracle/input.json").write_bytes(b"occupied")
            with patch.object(adapter_module, "verify_stock_provenance", return_value=provenance()), patch.object(
                adapter_module.subprocess, "Popen", FakePopen
            ):
                with self.assertRaisesRegex(OriginalOracleAdapterError, "ORIGINAL_ORACLE_OUTPUT_REUSE_FORBIDDEN"):
                    OriginalOracleAdapter(self.policy, timeout_seconds=90).invoke(logical_input(), attempt_root=root)
        self.assertEqual(FakePopen.calls, [])

    def test_sensitive_and_oversized_output_reject_without_file_capture(self):
        for stdout in (b"Authorization: Bearer not-preserved\n", b"x" * 65537):
            FakePopen.calls = []
            FakePopen.stdout_value = stdout
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                with self.assertRaisesRegex(OriginalOracleAdapterError, "ORIGINAL_ORACLE_CAPTURE_REJECTED"):
                    self.invoke(root)
                self.assertFalse((root / "oracle/stdout.bin").exists())

    def test_evidence_roles_media_storage_and_zero_byte_semantics(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.invoke(Path(directory))
        self.assertEqual(tuple(item.role for item in result.candidates), EVIDENCE_ROLES)
        for candidate in result.candidates:
            contract = self.policy.role(candidate.role)
            self.assertEqual(candidate.producer, contract.producer)
            self.assertEqual(candidate.source_kind, contract.source_kind)
            self.assertEqual(candidate.storage_class, contract.storage_class)
            self.assertEqual(candidate.media_type, contract.media_type)
        self.assertEqual(result.candidates[2].payload, b"")

    def test_symlink_root_and_path_reject(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            real = base / "real"; real.mkdir()
            link = base / "link"; link.symlink_to(real, target_is_directory=True)
            with self.assertRaisesRegex(OriginalOracleAdapterError, "ORIGINAL_ORACLE_EVIDENCE_PATH_INVALID"):
                self.invoke(link)
            inner = real / "oracle"; inner.symlink_to(base, target_is_directory=True)
            with self.assertRaisesRegex(OriginalOracleAdapterError, "ORIGINAL_ORACLE_EVIDENCE_PATH_INVALID"):
                self.invoke(real)


class WorkerIsolationTests(unittest.TestCase):
    def test_clean_submodule_status_prefix_is_preserved(self):
        with patch.object(adapter_module, "_run_checked", return_value=b" deadbeef path (heads/main)\n"):
            self.assertEqual(
                adapter_module._git(["submodule", "status"], Path("/tmp")),
                " deadbeef path (heads/main)",
            )

    def test_exactly_one_stock_evaluation_and_no_capture_baseline(self):
        calls = []

        class Oracle:
            def __init__(self, problem):
                self.problem = problem
                self.replica_count = {}

            def capture_baseline(self):
                calls.append("capture")

            def evaluate(self):
                calls.append(("evaluate", dict(self.replica_count)))
                return {"success": True}

        kubernetes = ModuleType("kubernetes")
        kubernetes.config = SimpleNamespace(load_kube_config=lambda **kwargs: calls.append(("config", kwargs["context"], kwargs["persist_config"])))
        kubernetes.client = SimpleNamespace(AppsV1Api=lambda: object(), CoreV1Api=lambda: object())
        mitigation = ModuleType("sregym.conductor.oracles.mitigation")
        mitigation.MitigationOracle = Oracle
        modules = {
            "kubernetes": kubernetes,
            "sregym": ModuleType("sregym"),
            "sregym.conductor": ModuleType("sregym.conductor"),
            "sregym.conductor.oracles": ModuleType("sregym.conductor.oracles"),
            "sregym.conductor.oracles.mitigation": mitigation,
        }
        candidate = worker_module.validate_input(logical_input())
        with patch.dict(sys.modules, modules), patch.object(worker_module, "_runtime_identity", return_value={"verified": True}):
            result = worker_module.evaluate_once(candidate)
        self.assertEqual(calls.count(("evaluate", dict(candidate["captured_replica_baseline"]))), 1)
        self.assertNotIn("capture", calls)
        self.assertEqual(result["raw_result"], {"success": True})

    def test_parent_never_imports_stock_and_worker_is_self_contained(self):
        parent_source = inspect.getsource(adapter_module)
        worker_source = inspect.getsource(worker_module)
        self.assertNotIn("sregym.conductor", parent_source)
        self.assertNotIn("from sremut", worker_source)
        self.assertNotIn("import sremut", worker_source)
        self.assertNotIn("Conductor", worker_source)

    def test_worker_surface_has_only_read_calls(self):
        source = inspect.getsource(worker_module._ReadOnlyKubectl)
        self.assertIn("list_namespaced_deployment", source)
        self.assertIn("list_namespaced_pod", source)
        for forbidden in ("create_", "patch_", "replace_", "delete_", "read_namespaced_service", "endpoint_slice"):
            self.assertNotIn(forbidden, source)

    def test_real_provenance_matches_pins(self):
        value = verify_stock_provenance()
        self.assertEqual(value["sregym_commit"], EXPECTED_SREGYM_COMMIT)
        self.assertEqual(value["oracle_module_sha256"], EXPECTED_ORACLE_SHA256)
        self.assertEqual(dict(value["dependency_versions"]), dict(EXPECTED_DEPENDENCIES))


if __name__ == "__main__":
    unittest.main()

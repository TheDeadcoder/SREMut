"""Isolated worker for the pinned stock SREGym MitigationOracle.

This module is intentionally self-contained: it must run under the frozen
SREGym interpreter without importing the SREMut package.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import sys
from typing import Any, NoReturn


SREGYM_ROOT = Path("/home/sakibbuet2k19/sremut/SREGym")
KUBECONFIG_PATH = Path("/home/sakibbuet2k19/.kube/config")
EXPECTED_CONTEXT = "kind-kind"
EXPECTED_NAMESPACE = "social-network"
EXPECTED_COMMIT = "ba07faf1a322f9b6d4a279643bb796aa2f36f64b"
ORACLE_RELATIVE_PATH = "sregym/conductor/oracles/mitigation.py"
EXPECTED_ORACLE_SHA256 = "a087fd38399cfca4c2de764dbbab837d6100350ab9f71b89552cafe2c91fca8b"
EXPECTED_PYTHON = "3.12.3"
EXPECTED_DEPENDENCIES = {
    "PyYAML": "6.0.2",
    "jsonschema": "4.23.0",
    "kubernetes": "30.1.0",
}
INPUT_FIELDS = frozenset(
    {"kubernetes_context", "namespace", "captured_replica_baseline", "evidence_paths"}
)
EVIDENCE_PATH_FIELDS = frozenset(
    {
        "original_oracle_input",
        "original_oracle_stdout",
        "original_oracle_stderr",
        "original_oracle_result",
    }
)
_NAME = re.compile(r"[a-z0-9](?:[-a-z0-9.]*[a-z0-9])?")
_REQUEST_TIMEOUT_SECONDS = 30


class WorkerFailure(Exception):
    """Stable worker failure without attacker-controlled detail."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _fail(code: str) -> NoReturn:
    raise WorkerFailure(code) from None


def _validate_value(value: Any) -> None:
    if value is None or type(value) in (str, bool, int):
        if type(value) is str:
            try:
                value.encode("utf-8", errors="strict")
            except UnicodeEncodeError:
                _fail("ORIGINAL_ORACLE_CANONICAL_INPUT_INVALID")
        return
    if type(value) is list:
        for child in value:
            _validate_value(child)
        return
    if type(value) is dict:
        for key, child in value.items():
            if type(key) is not str:
                _fail("ORIGINAL_ORACLE_CANONICAL_INPUT_INVALID")
            _validate_value(child)
        return
    _fail("ORIGINAL_ORACLE_CANONICAL_INPUT_INVALID")


def canonical_json_bytes(value: Any) -> bytes:
    _validate_value(value)
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8", errors="strict")
    except (TypeError, ValueError, UnicodeEncodeError):
        _fail("ORIGINAL_ORACLE_CANONICAL_INPUT_INVALID")


def _unique_mapping(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("ORIGINAL_ORACLE_CANONICAL_INPUT_INVALID")
        result[key] = value
    return result


def parse_canonical_json(data: bytes) -> Any:
    try:
        text = data.decode("utf-8", errors="strict")
        value = json.loads(
            text,
            object_pairs_hook=_unique_mapping,
            parse_float=lambda _value: _fail("ORIGINAL_ORACLE_CANONICAL_INPUT_INVALID"),
            parse_constant=lambda _value: _fail("ORIGINAL_ORACLE_CANONICAL_INPUT_INVALID"),
        )
    except WorkerFailure:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail("ORIGINAL_ORACLE_CANONICAL_INPUT_INVALID")
    _validate_value(value)
    if canonical_json_bytes(value) != data:
        _fail("ORIGINAL_ORACLE_CANONICAL_INPUT_INVALID")
    return value


def _safe_relative_path(value: Any) -> bool:
    if type(value) is not str or not value or value.startswith("/") or "\\" in value:
        return False
    parts = value.split("/")
    return all(part not in ("", ".", "..") for part in parts)


def validate_input(value: Any) -> dict[str, Any]:
    if type(value) is not dict or set(value) != INPUT_FIELDS:
        _fail("ORIGINAL_ORACLE_INPUT_INVALID")
    if value["kubernetes_context"] != EXPECTED_CONTEXT:
        _fail("ORIGINAL_ORACLE_INPUT_INVALID")
    if value["namespace"] != EXPECTED_NAMESPACE:
        _fail("ORIGINAL_ORACLE_INPUT_INVALID")
    baseline = value["captured_replica_baseline"]
    if type(baseline) is not dict or not baseline:
        _fail("ORIGINAL_ORACLE_INPUT_INVALID")
    for name, count in baseline.items():
        if (
            type(name) is not str
            or not _NAME.fullmatch(name)
            or len(name) > 253
            or type(count) is not int
            or count < 0
        ):
            _fail("ORIGINAL_ORACLE_INPUT_INVALID")
    evidence_paths = value["evidence_paths"]
    if type(evidence_paths) is not dict or set(evidence_paths) != EVIDENCE_PATH_FIELDS:
        _fail("ORIGINAL_ORACLE_INPUT_INVALID")
    if len(set(evidence_paths.values())) != len(EVIDENCE_PATH_FIELDS):
        _fail("ORIGINAL_ORACLE_INPUT_INVALID")
    if any(not _safe_relative_path(path) for path in evidence_paths.values()):
        _fail("ORIGINAL_ORACLE_INPUT_INVALID")
    return value


def extract_returned_boolean(raw_result: Any) -> bool:
    """Apply the frozen strict stock-return extraction without coercion."""

    if type(raw_result) is not dict:
        _fail("ORIGINAL_ORACLE_RETURN_SHAPE_INVALID")
    if set(raw_result) != {"success"}:
        _fail("ORIGINAL_ORACLE_RETURN_SHAPE_INVALID")
    if type(raw_result["success"]) is not bool:
        _fail("ORIGINAL_ORACLE_RETURN_SHAPE_INVALID")
    return raw_result["success"]


def _runtime_identity() -> dict[str, Any]:
    versions = {name: importlib.metadata.version(name) for name in EXPECTED_DEPENDENCIES}
    if platform.python_version() != EXPECTED_PYTHON or versions != EXPECTED_DEPENDENCIES:
        _fail("ORIGINAL_ORACLE_WORKER_PROVENANCE_INVALID")
    module_sha = hashlib.sha256((SREGYM_ROOT / ORACLE_RELATIVE_PATH).read_bytes()).hexdigest()
    if module_sha != EXPECTED_ORACLE_SHA256:
        _fail("ORIGINAL_ORACLE_WORKER_PROVENANCE_INVALID")
    return {
        "dependency_versions": versions,
        "oracle_module_sha256": module_sha,
        "python_version": platform.python_version(),
        "sregym_commit": EXPECTED_COMMIT,
    }


class _ReadOnlyKubectl:
    __slots__ = ("_apps", "_core")

    def __init__(self, apps: Any, core: Any):
        self._apps = apps
        self._core = core

    def list_deployments(self, namespace: str) -> Any:
        if namespace != EXPECTED_NAMESPACE:
            _fail("ORIGINAL_ORACLE_WORKER_OPERATION_FORBIDDEN")
        return self._apps.list_namespaced_deployment(
            namespace,
            watch=False,
            _request_timeout=_REQUEST_TIMEOUT_SECONDS,
        )

    def list_pods(self, namespace: str) -> Any:
        if namespace != EXPECTED_NAMESPACE:
            _fail("ORIGINAL_ORACLE_WORKER_OPERATION_FORBIDDEN")
        return self._core.list_namespaced_pod(
            namespace,
            watch=False,
            _request_timeout=_REQUEST_TIMEOUT_SECONDS,
        )


class _Problem:
    __slots__ = ("kubectl", "namespace")

    def __init__(self, kubectl: _ReadOnlyKubectl, namespace: str):
        self.kubectl = kubectl
        self.namespace = namespace


def _atomic_write(path: Path, data: bytes) -> None:
    if path.exists() or path.is_symlink():
        _fail("ORIGINAL_ORACLE_OUTPUT_REUSE_FORBIDDEN")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                _fail("ORIGINAL_ORACLE_OUTPUT_WRITE_FAILED")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary, path)
    parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)


def evaluate_once(candidate: dict[str, Any]) -> dict[str, Any]:
    runtime = _runtime_identity()
    sys.path.insert(0, str(SREGYM_ROOT))
    from kubernetes import client, config  # lazy, isolated worker import
    from sregym.conductor.oracles.mitigation import MitigationOracle

    config.load_kube_config(
        config_file=str(KUBECONFIG_PATH),
        context=candidate["kubernetes_context"],
        persist_config=False,
    )
    problem = _Problem(_ReadOnlyKubectl(client.AppsV1Api(), client.CoreV1Api()), candidate["namespace"])
    oracle = MitigationOracle(problem)
    oracle.replica_count = dict(candidate["captured_replica_baseline"])
    try:
        raw_result = oracle.evaluate()
    except Exception as error:
        return {
            "outcome": "ORACLE_EXCEPTION",
            "exception_type": type(error).__name__,
            "raw_result": None,
            "raw_result_sha256": None,
            "returned_boolean": None,
            "runtime": runtime,
            "schema_version": 1,
        }
    try:
        returned = extract_returned_boolean(raw_result)
    except WorkerFailure:
        return {
            "outcome": "ORIGINAL_ORACLE_RETURN_SHAPE_INVALID",
            "exception_type": None,
            "raw_result": None,
            "raw_result_sha256": None,
            "returned_boolean": None,
            "runtime": runtime,
            "schema_version": 1,
        }
    raw_bytes = canonical_json_bytes(raw_result)
    return {
        "outcome": "RETURNED_TRUE" if returned is True else "RETURNED_FALSE",
        "exception_type": None,
        "raw_result": raw_result,
        "raw_result_sha256": hashlib.sha256(raw_bytes).hexdigest(),
        "returned_boolean": returned,
        "runtime": runtime,
        "schema_version": 1,
    }


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) != 2:
        return 64
    input_path = Path(arguments[0])
    output_path = Path(arguments[1])
    try:
        candidate = validate_input(parse_canonical_json(input_path.read_bytes()))
        result = evaluate_once(candidate)
        _atomic_write(output_path, canonical_json_bytes(result))
    except WorkerFailure as error:
        os.write(2, (error.code + "\n").encode("ascii"))
        return 65
    except Exception as error:
        code = f"ORIGINAL_ORACLE_WORKER_FAILURE:{type(error).__name__}\n"
        os.write(2, code.encode("ascii", errors="strict"))
        return 70
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

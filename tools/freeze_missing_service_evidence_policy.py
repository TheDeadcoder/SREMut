#!/usr/bin/env python3
"""Generate and verify the frozen missing-service evidence-capture policy."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import os
import re
import secrets
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import yaml
from jsonschema import Draft202012Validator, ValidationError


GENERATOR_PATH = Path(__file__).resolve()
ROOT = GENERATOR_PATH.parents[1]
POLICY_PATH = ROOT / "policies/missing_service_social_network/evidence-capture-v1.yaml"
SCHEMA_PATH = ROOT / "schemas/evidence-capture-policy-v1.schema.json"
MANIFEST_PATH = ROOT / "EVIDENCE_CAPTURE_POLICY_V1_SHA256SUMS"
JOURNAL_PATH = ROOT / ".evidence-capture-policy-v1.transaction.json"
CONTRACT_PATH = ROOT / "contracts/missing_service_social_network.yaml"
REGISTRY_PATH = ROOT / "mutants/missing_service_social_network/registry.yaml"
PROFILE_PATH = ROOT / "profiles/missing_service_social_network/pilot-v1.yaml"
PROFILE_SCHEMA_PATH = ROOT / "schemas/missing-service-execution-profile-v1.schema.json"
PYPROJECT_PATH = ROOT / "pyproject.toml"
UV_LOCK_PATH = ROOT / "uv.lock"

SREGYM_ROOT = Path("/home/sakibbuet2k19/sremut/SREGym")
APPLICATIONS_ROOT = SREGYM_ROOT / "SREGym-applications"
BASELINE_ROOT = Path("/home/sakibbuet2k19/sremut/baselines/missing_service_social_network")

POLICY_REL = POLICY_PATH.relative_to(ROOT).as_posix()
SCHEMA_REL = SCHEMA_PATH.relative_to(ROOT).as_posix()
MANIFEST_REL = MANIFEST_PATH.relative_to(ROOT).as_posix()
GENERATOR_REL = GENERATOR_PATH.relative_to(ROOT).as_posix()
JOURNAL_REL = JOURNAL_PATH.relative_to(ROOT).as_posix()
ARTIFACT_RELS = (GENERATOR_REL, POLICY_REL, SCHEMA_REL)
EXPECTED_UNTRACKED = frozenset({GENERATOR_REL, POLICY_REL, SCHEMA_REL, MANIFEST_REL})

INITIAL_FROZEN_AT = "2026-08-20T10:53:22.794810+00:00"
SCAFFOLD_COMMIT = "b2e263bdcbfcf49779268df6797c60669d863cf1"
SUBPROCESS_TIMEOUT_SECONDS = 15
PYPROJECT_SHA256 = "93e3c59d74450ab9df1e09128a929fd50804ddbf693a2bc4d005e5620579e0f7"
UV_LOCK_SHA256 = "700c432b80e151da281f8052451be13ef28b8bf61cfdff21c8c215367db79f01"

CONTRACT_TAG = "sremut-missing-service-contract-v1"
CONTRACT_TAG_OBJECT = "378e9e9180438910611e7642402220e76bb302ca"
CONTRACT_COMMIT = "abed58d67e3f91e61f3ad666a47f0101cc680b93"
CONTRACT_TREE = "2648659b56a7d472bcedb38b8ffb6bc085aea650"
CONTRACT_SHA256 = "bda78e1b07b5eb0628954bcafd8fae3fc1bb2f3b22770046584bd873b72a2488"
REGISTRY_SHA256 = "688411986f75cb83c25ac6913e2868a075eb90fd755a1c0acd553a466ed87c72"

PROFILE_TAG = "sremut-missing-service-execution-profile-v1"
PROFILE_TAG_OBJECT = "7c6493eb7dce68370fd0d5be572edd968654a1d6"
PROFILE_COMMIT = "35fcaeecd6cca02aaec6ebed63455f662bc28176"
PROFILE_TREE = "c55ac0748275b9b3ea1ec133700cb7eeec54325b"
PROFILE_SHA256 = "79cb2d45298e6221d78fcc3aea82df52c71f60f0ab4ba872e1f0579a908703c7"
PROFILE_SCHEMA_SHA256 = "dbf715f7df6ff2f06ab27d19064d83c0d1e9c9beb21389daa27e89015fc0be2b"
PROFILE_GENERATOR_SHA256 = "3122ba3c8048f49cfc67e62d134ffc5580dbd7bbadf272d12bca97f5304a16c3"

SREGYM_COMMIT = "ba07faf1a322f9b6d4a279643bb796aa2f36f64b"
APPLICATIONS_COMMIT = "2b2f9c6c2e97c44abbfcc44af1cf2f994bbb04f8"
APPLICATION_SUBMODULES = {
    "FleetCast": "a2b9c5e5a14cc7892c347e0dd944a4e2ab09a8fb",
    "astronomy-shop": "7d7b074714345a0c282b0be75af7a2c504b44c95",
    "flight-ticket": "77fe227f7df911ccdbe2f2b514e5090b51a8a169",
    "train-ticket": "c9537c1533514bb6ba9bd664b9312c2b9cee413c",
}
SREGYM_SUBMODULES = {
    "SREGym-applications": APPLICATIONS_COMMIT,
    **{f"SREGym-applications/{name}": commit for name, commit in APPLICATION_SUBMODULES.items()},
}

SOURCE_HASHES = {
    "sregym/conductor/problems/missing_service.py": "adf9a93aee8e016ca70d3aeb6270c94f5cc3bcf41aa36f00d538f6d6d9c8405f",
    "sregym/conductor/oracles/mitigation.py": "a087fd38399cfca4c2de764dbbab837d6100350ab9f71b89552cafe2c91fca8b",
    "sregym/conductor/oracles/workload.py": "d1bb10c9230ab1b2bba541df16552013323b0160a928d9854fad41210a356468",
    "sregym/generators/workload/base.py": "dbe8538e835dc8603b9ecabe33b66f7c8a5fe1e4c334f3d2ae85488c787b9e2c",
    "sregym/generators/workload/stream.py": "5a6c317c4261572087d4d64a6ac1cfda4db0b1e029ce2717167689f577c0d06d",
    "sregym/generators/workload/wrk2.py": "ef5ffa18815c55e781a1a162eae744b47e4e91689e29ddf693ac66f4451e268e",
    "sregym/generators/fault/inject_virtual.py": "1c381fec5f47c9c724396e6b8b0c0598cbf6cb7dce98cd2f94e0a8823104934f",
    "sregym/service/apps/social_network.py": "246b3d060d02d4665bc6b2e366495ab212da9ba3b4da1ab2a6a1a6bd7cc3d607",
}

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SHA1_RE = re.compile(r"^[0-9a-f]{40}$")
EVIDENCE_ID_RE = re.compile(r"^ev-[0-9a-f]{32}$")
RUN_ID_RE = re.compile(r"^sremut-ms-(m01|m02|m03)-r0[1-3]-a0[1-2]-[0-9a-f]{12}$")
ATTEMPT_ID_RE = re.compile(r"^a0[1-2]$")
UTC_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{9}Z$")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
BINARY64_RE = re.compile(r"^[0-9a-f]{16}$")
ALIAS_RE = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")
TRANSACTION_ID_RE = re.compile(r"^[0-9a-f]{32}$")
TEMP_TOKEN_RE = re.compile(r"^[0-9a-f]{16}$")
ZERO_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
GENESIS_SHA256 = "0" * 64

ROLE_NAMES = (
    "run_identity",
    "healthy_prestate",
    "kubernetes_request_identity",
    "kubernetes_object_projection",
    "kubernetes_api_status",
    "kubernetes_api_error_body",
    "original_oracle_input",
    "original_oracle_stdout",
    "original_oracle_stderr",
    "original_oracle_result",
    "challenge_invocation",
    "challenge_stdout",
    "challenge_stderr",
    "challenge_result",
    "workload_log_bytes",
    "workload_boundary",
    "workload_parse_result",
    "mutation_intent",
    "mutation_receipt",
    "adjudication",
    "terminal_manifest",
)

PRODUCERS = (
    "RUNNER_IDENTITY_RECORDER",
    "RUNNER_PREFLIGHT_RECORDER",
    "RUNNER_KUBERNETES_CLIENT",
    "ORIGINAL_ORACLE_ADAPTER",
    "CHALLENGE_EXEC_ADAPTER",
    "WORKLOAD_EVIDENCE_ADAPTER",
    "MUTATION_CONTROLLER",
    "ADJUDICATOR",
    "TERMINALIZER",
)
CONSUMERS = (
    "LIVE_PREFLIGHT",
    "ORIGINAL_ORACLE",
    "CONTRACT_EVALUATOR",
    "MUTATION_CONTROLLER",
    "RESTORATION_CONTROLLER",
    "ADJUDICATOR",
    "TERMINALIZER",
    "EXTERNAL_AGGREGATOR",
)
MEDIA_TYPES = ("application/json", "application/octet-stream", "text/plain; charset=utf-8")
STORAGE_CLASSES = ("PAYLOAD_WITH_DESCRIPTOR", "DESCRIPTOR_ONLY", "TERMINAL_MANIFEST")
TERMINAL_OUTCOMES = ("FINALIZED", "ABORTED_SAFE", "RESTORATION_BLOCKED")
WORKLOAD_WINDOWS = {
    "INITIAL_MUTANT_CHALLENGE": 1,
    "POST_REPLACEMENT_PERSISTENCE": 2,
    "RESTORATION_POSITIVE_CONTROL": 3,
}
RUNTIME_VALIDATOR_HOOKS = (
    "VALIDATE_CANONICAL_NO_FLOATS_V1",
    "VALIDATE_EVIDENCE_REF_HASH_PATH_ID_V1",
    "VALIDATE_DESCRIPTOR_CONTENT_IDENTITY_V1",
    "VALIDATE_ATTEMPT_PHASES_AND_FINALITY_V1",
    "VALIDATE_WORKLOAD_CARDINALITY_V1",
    "VALIDATE_WORKLOAD_WINDOW_CONSISTENCY_V1",
    "VALIDATE_ADJUDICATION_RAW_BACKING_V1",
    "VALIDATE_JOURNAL_HASH_CHAIN_V1",
    "VALIDATE_KUBERNETES_REQUEST_V1",
    "VALIDATE_KUBERNETES_RESPONSE_V1",
    "VALIDATE_SERVICE_RESTORATION_BODY_V1",
    "VALIDATE_SENSITIVE_CAPTURE_V1",
)

STRUCTURAL_VALIDATION_LEVEL = "STRUCTURAL_SCHEMA_VALIDATION"
FULL_VALIDATION_LEVEL = "FULL_ADMISSIBILITY_VALIDATION"
FULL_ADMISSIBILITY_DISPATCHER_ID = "SREMUT_FULL_ADMISSIBILITY_DISPATCHER_V1"
FULL_ADMISSIBILITY_RESULT_DOCUMENT = "FULL_ADMISSIBILITY_VALIDATION_RESULT_V1"
RESOLVED_CONTEXT_DOCUMENT = "RESOLVED_EVIDENCE_CONTEXT_V1"
AUTHENTICATED_POLICY_INPUT_DOCUMENT = "AUTHENTICATED_EVIDENCE_POLICY_INPUT_V1"
OFFLINE_SEAL_INPUT_DOCUMENT = "OFFLINE_SEALED_REVALIDATION_INPUT_V1"
CAPTURE_TIME_VALIDATION_LEVEL = "CAPTURE_TIME_FULL_ADMISSIBILITY"
OFFLINE_VALIDATION_LEVEL = "OFFLINE_SEALED_REVALIDATION"

PROJECTION_CLASSES = (
    "KUBERNETES_OBJECT_PROJECTION_V1",
    "SERVICE_RESTORATION_SOURCE_V1",
    "SERVICE_RESTORATION_BODY_V1",
    "POD_IDENTITY_CAPTURE_V1",
    "REPLICASET_OWNER_CAPTURE_V1",
    "DEPLOYMENT_OWNER_CAPTURE_V1",
)

WORKLOAD_PARSE_ALGORITHM = "SREMUT_PINNED_WORKLOAD_ENTRY_JSONL_V1"
WORKLOAD_PARSER_MODULE = "sregym/conductor/oracles/workload.py"
WORKLOAD_PARSER_SOURCE_SHA256 = SOURCE_HASHES[WORKLOAD_PARSER_MODULE]

VALIDATION_FAILURE_CODES = (
    "STRUCTURAL_SCHEMA_INVALID",
    "MISSING_RESOLVED_CONTEXT",
    "RESOLVED_CONTEXT_INVALID",
    "UNKNOWN_DOCUMENT_ROLE",
    "HOOK_CONTEXT_MISSING",
    "CANONICAL_FLOAT_FORBIDDEN",
    "EVIDENCE_REFERENCE_INVALID",
    "EVIDENCE_REFERENCE_UNRESOLVED",
    "DESCRIPTOR_BYTES_MISSING",
    "DESCRIPTOR_HASH_MISMATCH",
    "DESCRIPTOR_CANONICAL_MISMATCH",
    "DESCRIPTOR_REFERENCE_MISMATCH",
    "PAYLOAD_BYTES_MISSING",
    "PAYLOAD_HASH_MISMATCH",
    "PAYLOAD_SIZE_MISMATCH",
    "PUBLICATION_RECORD_MISSING",
    "PUBLICATION_ORDER_INVALID",
    "RUN_ATTEMPT_MISMATCH",
    "ATTEMPT_FINALITY_INVALID",
    "WORKLOAD_CARDINALITY_INVALID",
    "WORKLOAD_WINDOW_MISMATCH",
    "WORKLOAD_RAW_PREFIX_MISMATCH",
    "WORKLOAD_TIMESTAMP_ORDER_INVALID",
    "WORKLOAD_ENTRY_ORDER_INVALID",
    "WORKLOAD_POD_IDENTITY_MISMATCH",
    "WORKLOAD_RESTART_COUNT_MISMATCH",
    "WORKLOAD_EVALUATION_CONTEXT_MISMATCH",
    "ADJUDICATION_RAW_REFERENCE_REQUIRED",
    "ADJUDICATION_RAW_ROLE_INVALID",
    "ADJUDICATION_DEADLINE_MISMATCH",
    "ADJUDICATION_EVALUATION_PHASE_MISMATCH",
    "JOURNAL_CHAIN_INVALID",
    "KUBERNETES_REQUEST_RULE_INVALID",
    "KUBERNETES_CAPTURE_UNRESOLVED",
    "KUBERNETES_CAPTURE_IDENTITY_MISMATCH",
    "KUBERNETES_CHALLENGE_IDENTITY_MISMATCH",
    "KUBERNETES_CHALLENGE_BODY_INVALID",
    "KUBERNETES_PROJECTION_FIELD_FORBIDDEN",
    "KUBERNETES_PROJECTION_REQUEST_MISMATCH",
    "KUBERNETES_LIST_INVALID",
    "RESTORATION_INTENT_UNRESOLVED",
    "RESTORATION_INTENT_ROLE_INVALID",
    "RESTORATION_OPERATION_MISMATCH",
    "RESTORATION_TARGET_MISMATCH",
    "RESTORATION_UID_MISMATCH",
    "RESTORATION_REFERENCE_MISSING",
    "RESTORATION_BODY_UNRESOLVED",
    "RESTORATION_BODY_HASH_MISMATCH",
    "RESTORATION_SERVICE_MISMATCH",
    "RESTORATION_PREPUBLICATION_FAILURE",
    "STATE_OPERATION_FORBIDDEN",
    "POST_TERMINAL_OPERATION",
    "SERVICE_RESTORATION_BODY_INVALID",
    "SENSITIVE_CAPTURE_REJECTED",
    "VALIDATOR_EXECUTION_FAILURE",
    "POLICY_BINDING_MISSING",
    "POLICY_MANIFEST_HASH_MISMATCH",
    "POLICY_MANIFEST_INVALID",
    "POLICY_HASH_MISMATCH",
    "POLICY_SCHEMA_HASH_MISMATCH",
    "POLICY_PARSED_CONTENT_MISMATCH",
    "HOOK_MATRIX_MISMATCH",
    "HOOK_ORDER_MISMATCH",
    "JOURNAL_BYTES_MISSING",
    "JOURNAL_CANONICALIZATION_INVALID",
    "JOURNAL_STATE_DERIVATION_FAILED",
    "JOURNAL_CONTEXT_MISMATCH",
    "JOURNAL_EVALUATION_MARKER_MISSING",
    "JOURNAL_OPERATION_MARKER_MISSING",
    "RESTORATION_SOURCE_REFERENCE_MISSING",
    "RESTORATION_SOURCE_UNRESOLVED",
    "RESTORATION_SOURCE_CLASS_INVALID",
    "RESTORATION_DERIVATION_MISMATCH",
    "MUTATION_RECEIPT_IDENTITY_INVALID",
    "MUTATION_RECEIPT_REQUEST_MISMATCH",
    "MUTATION_RECEIPT_PUBLICATION_INVALID",
    "KUBERNETES_CAPTURE_CLASS_INVALID",
    "KUBERNETES_CAPTURE_STATE_INVALID",
    "KUBERNETES_CAPTURE_OWNER_INVALID",
    "KUBERNETES_CAPTURE_SELECTION_INVALID",
    "KUBERNETES_REQUEST_IDENTITY_MISMATCH",
    "KUBERNETES_SOURCE_PROJECTION_MISMATCH",
    "KUBERNETES_LIST_RESOURCE_VERSION_MISMATCH",
    "WORKLOAD_PARSE_SCHEMA_INVALID",
    "WORKLOAD_PARSE_RECOMPUTATION_MISMATCH",
    "WORKLOAD_PARSE_REFERENCE_MISMATCH",
    "WORKLOAD_PARSER_IDENTITY_MISMATCH",
    "EXTERNAL_SEAL_MISSING",
    "EXTERNAL_MANIFEST_HASH_MISMATCH",
    "EXTERNAL_MANIFEST_INVALID",
    "EXTERNAL_MANIFEST_COVERAGE_MISMATCH",
    "VALIDATION_MODE_INVALID",
)

EVALUATION_CONTEXTS = {
    "INITIAL_INVARIANT_EVALUATION": {
        "phase": "INITIAL_MUTANT_CHALLENGE",
        "deadline_identity": "INITIAL_FRESH_WORKLOAD_60S",
        "allowed_raw_roles": [
            "workload_log_bytes", "workload_parse_result", "kubernetes_object_projection",
            "challenge_stdout", "challenge_stderr", "kubernetes_api_error_body",
        ],
        "allowed_states": ["ORIGINAL_ORACLE_EVALUATED", "CONTRACT_EVALUATED"],
    },
    "REPLACEMENT_PERSISTENCE_EVALUATION": {
        "phase": "POST_REPLACEMENT_PERSISTENCE",
        "deadline_identity": "POST_REPLACEMENT_FRESH_WORKLOAD_60S",
        "allowed_raw_roles": [
            "workload_log_bytes", "workload_parse_result", "kubernetes_object_projection",
            "challenge_stdout", "challenge_stderr", "kubernetes_api_error_body",
        ],
        "allowed_states": ["CONTRACT_EVALUATED"],
    },
    "RESTORATION_POSITIVE_CONTROL": {
        "phase": "RESTORATION_POSITIVE_CONTROL",
        "deadline_identity": "RESTORATION_FRESH_WORKLOAD_60S",
        "allowed_raw_roles": [
            "workload_log_bytes", "workload_parse_result", "kubernetes_object_projection",
            "challenge_stdout", "challenge_stderr", "kubernetes_api_error_body",
        ],
        "allowed_states": ["RESTORE_STARTED", "RESTORE_VERIFIED"],
    },
}

SERVICE_DELETE_OPERATION_KINDS = ("INITIAL_USER_SERVICE_DELETION", "MUTANT_SERVICE_DELETION")
SERVICE_CREATE_OPERATION_KINDS = ("MUTANT_SERVICE_CREATION", "RESTORED_SERVICE_CREATION")


class ValidationFailure(RuntimeError):
    """Stable, non-sensitive full-admissibility rejection."""

    def __init__(self, failure_code: str, hook_id: str | None = None):
        if failure_code not in VALIDATION_FAILURE_CODES:
            raise ValueError("Unknown validation failure code")
        super().__init__(failure_code)
        self.failure_code = failure_code
        self.hook_id = hook_id

ROLE_SOURCE_KINDS = {
    "run_identity": "LOCAL_IDENTITY",
    "healthy_prestate": "GENERATED_DESCRIPTOR",
    "kubernetes_request_identity": "KUBERNETES",
    "kubernetes_object_projection": "KUBERNETES",
    "kubernetes_api_status": "KUBERNETES",
    "kubernetes_api_error_body": "KUBERNETES",
    "original_oracle_input": "SUBPROCESS",
    "original_oracle_stdout": "SUBPROCESS",
    "original_oracle_stderr": "SUBPROCESS",
    "original_oracle_result": "SUBPROCESS",
    "challenge_invocation": "KUBERNETES",
    "challenge_stdout": "KUBERNETES",
    "challenge_stderr": "KUBERNETES",
    "challenge_result": "KUBERNETES",
    "workload_log_bytes": "IN_PROCESS_WORKLOAD",
    "workload_boundary": "IN_PROCESS_WORKLOAD",
    "workload_parse_result": "IN_PROCESS_WORKLOAD",
    "mutation_intent": "GENERATED_DESCRIPTOR",
    "mutation_receipt": "GENERATED_DESCRIPTOR",
    "adjudication": "GENERATED_DESCRIPTOR",
    "terminal_manifest": "JOURNAL",
}

SOURCE_ALIASES = {
    "SREMUT_REPOSITORY": {"kind": "GIT_REPOSITORY", "published_resolution": "repository-relative-paths-only"},
    "SREGYM_REPOSITORY": {"kind": "GIT_REPOSITORY", "published_resolution": "source-alias-and-relative-path-only"},
    "SREGYM_APPLICATIONS_REPOSITORY": {"kind": "GIT_REPOSITORY", "published_resolution": "source-alias-and-relative-path-only"},
    "EXECUTION_PROFILE": {"kind": "IMMUTABLE_REPOSITORY_FILE", "published_resolution": "EXECUTION_PROFILE"},
    "CONTRACT": {"kind": "IMMUTABLE_REPOSITORY_FILE", "published_resolution": "CONTRACT"},
    "EXECUTION_PROFILE_SCHEMA": {"kind": "IMMUTABLE_REPOSITORY_FILE", "published_resolution": "EXECUTION_PROFILE_SCHEMA"},
    "EVIDENCE_POLICY_SCHEMA": {"kind": "IMMUTABLE_REPOSITORY_FILE", "published_resolution": "EVIDENCE_POLICY_SCHEMA"},
    "PYPROJECT": {"kind": "IMMUTABLE_REPOSITORY_FILE", "published_resolution": "PYPROJECT"},
    "UV_LOCK": {"kind": "IMMUTABLE_REPOSITORY_FILE", "published_resolution": "UV_LOCK"},
    "ORIGINAL_ORACLE_EXECUTABLE": {"kind": "LOCAL_EXECUTABLE", "published_resolution": "alias-plus-hash-only"},
    "ATTEMPT_ROOT": {"kind": "RUN_LOCAL_DIRECTORY", "published_resolution": "attempt-relative-paths-only"},
    "WORKLOAD_HISTORY": {"kind": "IN_PROCESS_SOURCE", "published_resolution": "alias-only"},
    "BOOT_ID_SOURCE": {"kind": "LOCAL_IDENTITY_SOURCE", "published_resolution": "value-only"},
    "KUBECONFIG_HASH_SOURCE": {"kind": "LOCAL_HASH_SOURCE", "published_resolution": "content-sha256-only"},
    "KUBECTL_CACHE_SNAPSHOT": {"kind": "LOCAL_TREE_HASH_SOURCE", "published_resolution": "tree-sha256-only"},
}

CHALLENGE_LABEL_KEYS = (
    "app.kubernetes.io/name",
    "app.kubernetes.io/managed-by",
    "sremut-run-id",
    "sremut-mutant",
    "sremut-attempt",
)

COMMON_METADATA = (
    "schema_version",
    "evidence_id",
    "run_id",
    "attempt_id",
    "producer",
    "source_kind",
    "created_utc",
    "monotonic_ns",
    "boot_identity",
    "redaction_status",
)


class RecoveryBlocked(RuntimeError):
    """Raised when interrupted state cannot be safely reconciled."""


class InjectedCrash(RuntimeError):
    """Self-test-only transaction failure."""


@dataclass(frozen=True)
class Layout:
    root: Path
    generator: Path
    policy: Path
    schema: Path
    manifest: Path
    journal: Path


def default_layout() -> Layout:
    return Layout(ROOT, GENERATOR_PATH, POLICY_PATH, SCHEMA_PATH, MANIFEST_PATH, JOURNAL_PATH)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256(path: Path) -> str:
    return sha256_bytes(read_regular_bytes(path))


def path_lexists(path: Path) -> bool:
    return os.path.lexists(path)


def read_regular_bytes(path: Path) -> bytes:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise RuntimeError(f"Required regular file is not regular: {path}")
    return path.read_bytes()


def run_local(
    argv: list[str],
    *,
    check: bool,
    text: bool = True,
    stdout: Any = subprocess.PIPE,
    stderr: Any = subprocess.PIPE,
    timeout_seconds: float = SUBPROCESS_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess[Any]:
    """Run one bounded local command; shell execution is never permitted."""
    try:
        return subprocess.run(
            argv,
            check=check,
            capture_output=stdout == subprocess.PIPE and stderr == subprocess.PIPE,
            stdout=None if stdout == subprocess.PIPE and stderr == subprocess.PIPE else stdout,
            stderr=None if stdout == subprocess.PIPE and stderr == subprocess.PIPE else stderr,
            text=text,
            encoding="utf-8" if text else None,
            errors="strict" if text else None,
            timeout=timeout_seconds,
            shell=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, UnicodeDecodeError) as error:
        raise RuntimeError(f"Local command execution failed: {argv!r}: {error}") from error


def git(repo: Path, *args: str) -> str:
    return run_local(["git", "-C", str(repo), *args], check=True).stdout.strip()


def git_bytes(repo: Path, *args: str) -> bytes:
    return run_local(["git", "-C", str(repo), *args], check=True, text=False).stdout


def git_raw(repo: Path, *args: str) -> str:
    return run_local(["git", "-C", str(repo), *args], check=True).stdout


def git_returncode(repo: Path, *args: str) -> int:
    return run_local(
        ["git", "-C", str(repo), *args],
        check=False,
        text=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode


def git_boolean(repo: Path, *args: str) -> bool:
    """Interpret only Git's documented 0/1 predicate statuses."""
    status = git_returncode(repo, *args)
    if status == 0:
        return True
    if status == 1:
        return False
    raise RuntimeError(f"Git predicate returned unexpected status {status}: {args!r}")


def ensure_no_floats(value: Any, location: str = "$") -> None:
    if isinstance(value, float):
        raise ValueError(f"JSON floating-point value forbidden at {location}")
    if isinstance(value, dict):
        for key, child in value.items():
            ensure_no_floats(child, f"{location}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            ensure_no_floats(child, f"{location}[{index}]")


def canonical_json_bytes(value: Any) -> bytes:
    ensure_no_floats(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def ieee754_binary64_hex(value: float) -> str:
    if not isinstance(value, float) or not math.isfinite(value):
        raise ValueError("WorkloadEntry.time must be a finite Python float")
    return struct.pack(">d", value).hex()


def validate_field_path(path: Any) -> None:
    if not isinstance(path, list) or not path:
        raise ValueError("Kubernetes field path must be a nonempty token array")
    for index, token in enumerate(path):
        if not isinstance(token, str) or not token:
            raise ValueError("Kubernetes path tokens must be nonempty strings")
        if token in (".", "..") or "\\" in token:
            raise ValueError("Forbidden Kubernetes path token")
        if token.startswith("[") and token != "[]":
            raise ValueError("[] is the only traversal token")
        if token == "[]" and (index == 0 or path[index - 1] == "[]"):
            raise ValueError("Malformed [] traversal")


def validate_relative_path(value: str, allowed_prefixes: tuple[str, ...]) -> None:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError("Invalid relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise ValueError("Absolute or traversal path forbidden")
    if not any(value.startswith(prefix) for prefix in allowed_prefixes):
        raise ValueError("Relative path prefix is not allowed")


def validate_publish_path(root: Path, value: str, allowed_prefixes: tuple[str, ...]) -> None:
    validate_relative_path(value, allowed_prefixes)
    current = root
    for component in PurePosixPath(value).parts:
        current = current / component
        if path_lexists(current) and current.is_symlink():
            raise ValueError("Symlink path component is forbidden")


def load_yaml(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(read_regular_bytes(path))
    if not isinstance(value, dict):
        raise RuntimeError(f"Expected YAML mapping: {path}")
    return value


def verify_manifest(path: Path, expected_count: int, expected_paths: tuple[str, ...] | None = None) -> None:
    lines = read_regular_bytes(path).decode("utf-8", errors="strict").splitlines()
    if len(lines) != expected_count:
        raise RuntimeError(f"Unexpected checksum count in {path}: {len(lines)}")
    observed_paths: list[str] = []
    for line in lines:
        match = re.fullmatch(r"([0-9a-f]{64})  ([^\n]+)", line)
        if match is None:
            raise RuntimeError(f"Malformed checksum line in {path}: {line!r}")
        digest, relative = match.groups()
        validate_relative_path(relative, ("README.md", "tools/", "contracts/", "mutants/", "profiles/", "schemas/", "policies/", "pyproject.toml", "uv.lock"))
        target = ROOT / relative
        if sha256(target) != digest:
            raise RuntimeError(f"Checksum mismatch for {relative}")
        observed_paths.append(relative)
    if expected_paths is not None and tuple(observed_paths) != expected_paths:
        raise RuntimeError(f"Checksum path set/order mismatch in {path}")


def verify_tag(name: str, object_id: str, commit: str, tree: str) -> None:
    if git(ROOT, "cat-file", "-t", f"refs/tags/{name}") != "tag":
        raise RuntimeError(f"Tag is not annotated: {name}")
    checks = (
        (git(ROOT, "rev-parse", f"refs/tags/{name}"), object_id, "object"),
        (git(ROOT, "rev-parse", f"{name}^{{}}"), commit, "commit"),
        (git(ROOT, "rev-parse", f"{name}^{{}}^{{tree}}"), tree, "tree"),
    )
    for observed, expected, label in checks:
        if observed != expected:
            raise RuntimeError(f"{name} {label} mismatch: {observed} != {expected}")


def verify_blob(commit: str, relative: str, expected_sha: str) -> None:
    blob = git_bytes(ROOT, "show", f"{commit}:{relative}")
    if sha256_bytes(blob) != expected_sha:
        raise RuntimeError(f"Immutable blob mismatch: {commit}:{relative}")
    if sha256(ROOT / relative) != expected_sha:
        raise RuntimeError(f"Worktree differs from immutable blob: {relative}")


def parse_submodules(raw: str, expected: dict[str, str], label: str) -> None:
    observed: dict[str, str] = {}
    for line in raw.splitlines():
        if not line:
            continue
        prefix = line[0]
        if prefix not in (" ", "-"):
            raise RuntimeError(f"{label} dirty or conflicting submodule: {line}")
        parts = line[1:].split()
        if len(parts) < 2:
            raise RuntimeError(f"Malformed submodule status: {line}")
        commit = parts[0].removesuffix("-dirty")
        if prefix != " " or not SHA1_RE.fullmatch(commit) or parts[0].endswith("-dirty"):
            raise RuntimeError(f"{label} submodule not clean/initialized: {line}")
        observed[parts[1]] = commit
    if observed != expected:
        raise RuntimeError(f"{label} submodule identities differ: {observed!r}")


def verify_clean_repo(repo: Path, commit: str, submodules: dict[str, str], label: str) -> None:
    if git(repo, "rev-parse", "HEAD") != commit:
        raise RuntimeError(f"{label} HEAD mismatch")
    status = git(repo, "status", "--porcelain=v1", "--untracked-files=all", "--ignore-submodules=none")
    if status:
        raise RuntimeError(f"{label} is not clean:\n{status}")
    if not git_boolean(repo, "diff", "--quiet", "--ignore-submodules=none", "--"):
        raise RuntimeError(f"{label} has unstaged changes")
    if not git_boolean(repo, "diff", "--cached", "--quiet", "--ignore-submodules=none", "--"):
        raise RuntimeError(f"{label} has staged changes")
    parse_submodules(git_raw(repo, "submodule", "status", "--recursive"), submodules, label)


def verify_baseline_hashes(contract: dict[str, Any]) -> None:
    evidence = contract["baseline_evidence"]
    checks: list[tuple[Path, str]] = [
        (BASELINE_ROOT / "baseline-reproducibility.json", evidence["reproducibility_report_sha256"])
    ]
    names = (
        ("result_sha256", "baseline-result.json"),
        ("summary_sha256", "baseline-summary.json"),
        ("service_sha256", "user-service.json"),
        ("endpoints_sha256", "user-service-endpoints.json"),
        ("deployments_sha256", "deployments.json"),
    )
    for index, run in enumerate(evidence["healthy_runs"], 1):
        for field, filename in names:
            checks.append((BASELINE_ROOT / f"run-{index:02d}" / filename, run[field]))
    if len(checks) != 16:
        raise RuntimeError("Expected exactly 16 embedded baseline hashes")
    for path, expected in checks:
        if sha256(path) != expected:
            raise RuntimeError(f"Embedded baseline hash mismatch: {path}")


def verify_sremut_worktree(allowed_transaction: frozenset[str] = frozenset()) -> None:
    verify_descendant_head(ROOT, SCAFFOLD_COMMIT)
    if git(ROOT, "diff", "--cached", "--name-only", "--"):
        raise RuntimeError("SREMut has staged changes")
    if git(ROOT, "diff", "--name-only", "--"):
        raise RuntimeError("SREMut has tracked unstaged changes")
    untracked = set(filter(None, git(ROOT, "ls-files", "--others", "--exclude-standard").splitlines()))
    allowed = set(EXPECTED_UNTRACKED) | set(allowed_transaction)
    if not untracked.issubset(allowed):
        raise RuntimeError("Unexpected SREMut untracked paths: " + repr(sorted(untracked - allowed)))


def verify_descendant_head(repo: Path, anchor: str) -> None:
    if not git_boolean(repo, "merge-base", "--is-ancestor", anchor, "HEAD"):
        raise RuntimeError("Runner scaffold commit is not an ancestor of HEAD")


def verify_provenance(allowed_transaction: frozenset[str] = frozenset()) -> tuple[dict[str, Any], dict[str, Any]]:
    verify_sremut_worktree(allowed_transaction)
    verify_tag(CONTRACT_TAG, CONTRACT_TAG_OBJECT, CONTRACT_COMMIT, CONTRACT_TREE)
    verify_tag(PROFILE_TAG, PROFILE_TAG_OBJECT, PROFILE_COMMIT, PROFILE_TREE)
    verify_blob(CONTRACT_COMMIT, "contracts/missing_service_social_network.yaml", CONTRACT_SHA256)
    verify_blob(CONTRACT_COMMIT, "mutants/missing_service_social_network/registry.yaml", REGISTRY_SHA256)
    verify_blob(PROFILE_COMMIT, "profiles/missing_service_social_network/pilot-v1.yaml", PROFILE_SHA256)
    verify_blob(PROFILE_COMMIT, "schemas/missing-service-execution-profile-v1.schema.json", PROFILE_SCHEMA_SHA256)
    verify_blob(PROFILE_COMMIT, "tools/freeze_missing_service_execution_profile.py", PROFILE_GENERATOR_SHA256)
    if sha256(PYPROJECT_PATH) != PYPROJECT_SHA256 or sha256(UV_LOCK_PATH) != UV_LOCK_SHA256:
        raise RuntimeError("Locked project identity mismatch")
    verify_manifest(ROOT / "CONTRACT_FREEZE_SHA256SUMS", 4)
    verify_manifest(
        ROOT / "EXECUTION_PROFILE_V1_SHA256SUMS",
        3,
        (
            "tools/freeze_missing_service_execution_profile.py",
            "profiles/missing_service_social_network/pilot-v1.yaml",
            "schemas/missing-service-execution-profile-v1.schema.json",
        ),
    )
    verify_clean_repo(SREGYM_ROOT, SREGYM_COMMIT, SREGYM_SUBMODULES, "SREGym")
    verify_clean_repo(APPLICATIONS_ROOT, APPLICATIONS_COMMIT, APPLICATION_SUBMODULES, "SREGym-applications")
    for relative, expected in SOURCE_HASHES.items():
        if sha256(SREGYM_ROOT / relative) != expected:
            raise RuntimeError(f"Pinned SREGym source mismatch: {relative}")
    contract = load_yaml(CONTRACT_PATH)
    profile = load_yaml(PROFILE_PATH)
    verify_baseline_hashes(contract)
    return contract, profile


def role_specs(intent: list[str], receipt: list[str], adjudication: list[str]) -> dict[str, Any]:
    all_terminal = list(TERMINAL_OUTCOMES)
    rows = (
        ("run_identity", "DESCRIPTOR_ONLY", "RUNNER_IDENTITY_RECORDER", ["LIVE_PREFLIGHT", "ADJUDICATOR", "TERMINALIZER", "EXTERNAL_AGGREGATOR"], "application/json", False, 32768, "DERIVED", list(COMMON_METADATA) + ["phase"]),
        ("healthy_prestate", "PAYLOAD_WITH_DESCRIPTOR", "RUNNER_PREFLIGHT_RECORDER", ["MUTATION_CONTROLLER", "CONTRACT_EVALUATOR", "RESTORATION_CONTROLLER", "ADJUDICATOR"], "application/json", False, 32768, "RAW_PROJECTION", list(COMMON_METADATA) + ["captured_replica_baseline", "captured_service_reference"]),
        ("kubernetes_request_identity", "DESCRIPTOR_ONLY", "RUNNER_KUBERNETES_CLIENT", ["CONTRACT_EVALUATOR", "MUTATION_CONTROLLER", "RESTORATION_CONTROLLER", "ADJUDICATOR"], "application/json", False, 8192, "DERIVED", list(COMMON_METADATA) + ["request_rule_id", "operation", "resource", "subresource", "namespace", "name_rule", "selector", "projection_paths"]),
        ("kubernetes_object_projection", "PAYLOAD_WITH_DESCRIPTOR", "RUNNER_KUBERNETES_CLIENT", ["ORIGINAL_ORACLE", "CONTRACT_EVALUATOR", "MUTATION_CONTROLLER", "RESTORATION_CONTROLLER", "ADJUDICATOR"], "application/json", False, 131072, "RAW_PROJECTION", list(COMMON_METADATA) + ["request_identity_reference", "object_count", "projection_class"]),
        ("kubernetes_api_status", "PAYLOAD_WITH_DESCRIPTOR", "RUNNER_KUBERNETES_CLIENT", ["MUTATION_CONTROLLER", "RESTORATION_CONTROLLER", "ADJUDICATOR"], "application/json", False, 16384, "RAW_PROJECTION", list(COMMON_METADATA) + ["request_identity_reference", "http_status"]),
        ("kubernetes_api_error_body", "PAYLOAD_WITH_DESCRIPTOR", "RUNNER_KUBERNETES_CLIENT", ["MUTATION_CONTROLLER", "RESTORATION_CONTROLLER", "ADJUDICATOR"], "application/octet-stream", True, 65536, "RAW", list(COMMON_METADATA) + ["request_identity_reference", "http_status"]),
        ("original_oracle_input", "PAYLOAD_WITH_DESCRIPTOR", "ORIGINAL_ORACLE_ADAPTER", ["ORIGINAL_ORACLE", "ADJUDICATOR", "TERMINALIZER"], "application/json", False, 32768, "DERIVED", list(COMMON_METADATA) + ["invocation_ordinal"]),
        ("original_oracle_stdout", "PAYLOAD_WITH_DESCRIPTOR", "ORIGINAL_ORACLE_ADAPTER", ["ADJUDICATOR", "TERMINALIZER"], "application/octet-stream", True, 65536, "RAW", list(COMMON_METADATA) + ["invocation_reference"]),
        ("original_oracle_stderr", "PAYLOAD_WITH_DESCRIPTOR", "ORIGINAL_ORACLE_ADAPTER", ["ADJUDICATOR", "TERMINALIZER"], "application/octet-stream", True, 65536, "RAW", list(COMMON_METADATA) + ["invocation_reference"]),
        ("original_oracle_result", "PAYLOAD_WITH_DESCRIPTOR", "ORIGINAL_ORACLE_ADAPTER", ["ADJUDICATOR", "CONTRACT_EVALUATOR", "TERMINALIZER"], "application/json", False, 32768, "DERIVED", list(COMMON_METADATA) + ["input_reference", "stdout_reference", "stderr_reference", "exit_status", "returned_boolean_or_exception"]),
        ("challenge_invocation", "DESCRIPTOR_ONLY", "CHALLENGE_EXEC_ADAPTER", ["CONTRACT_EVALUATOR", "ADJUDICATOR", "TERMINALIZER"], "application/json", False, 8192, "DERIVED", list(COMMON_METADATA) + ["template_id", "parameters", "pod_name", "pod_uid"]),
        ("challenge_stdout", "PAYLOAD_WITH_DESCRIPTOR", "CHALLENGE_EXEC_ADAPTER", ["CONTRACT_EVALUATOR", "ADJUDICATOR", "TERMINALIZER"], "application/octet-stream", True, 16384, "RAW_SERIALIZED_TEXT", list(COMMON_METADATA) + ["invocation_reference", "channel"]),
        ("challenge_stderr", "PAYLOAD_WITH_DESCRIPTOR", "CHALLENGE_EXEC_ADAPTER", ["CONTRACT_EVALUATOR", "ADJUDICATOR", "TERMINALIZER"], "application/octet-stream", True, 16384, "RAW_SERIALIZED_TEXT", list(COMMON_METADATA) + ["invocation_reference", "channel"]),
        ("challenge_result", "DESCRIPTOR_ONLY", "CHALLENGE_EXEC_ADAPTER", ["CONTRACT_EVALUATOR", "ADJUDICATOR", "TERMINALIZER"], "application/json", False, 8192, "DERIVED", list(COMMON_METADATA) + ["invocation_reference", "stdout_reference", "stderr_reference", "exit_status"]),
        ("workload_log_bytes", "PAYLOAD_WITH_DESCRIPTOR", "WORKLOAD_EVIDENCE_ADAPTER", ["CONTRACT_EVALUATOR", "ADJUDICATOR", "TERMINALIZER"], "application/octet-stream", True, 262144, "RAW_SERIALIZATION", list(COMMON_METADATA) + ["complete_entry_count", "entry_time_ieee754_binary64_hex", "workload_window"]),
        ("workload_boundary", "DESCRIPTOR_ONLY", "WORKLOAD_EVIDENCE_ADAPTER", ["CONTRACT_EVALUATOR", "ADJUDICATOR", "TERMINALIZER"], "application/json", False, 8192, "DERIVED", list(COMMON_METADATA) + ["workload_pod_projection_reference", "pod_name", "pod_uid", "container_restart_count", "raw_log_reference", "raw_log_byte_length", "raw_log_sha256", "complete_entry_count", "request_count", "entry_time_ieee754_binary64_hex", "workload_window"]),
        ("workload_parse_result", "PAYLOAD_WITH_DESCRIPTOR", "WORKLOAD_EVIDENCE_ADAPTER", ["CONTRACT_EVALUATOR", "ADJUDICATOR", "TERMINALIZER"], "application/json", False, 65536, "DERIVED", list(COMMON_METADATA) + ["boundary_reference", "raw_log_reference", "fresh_request_count", "failure_marker_count", "workload_window"]),
        ("mutation_intent", "DESCRIPTOR_ONLY", "MUTATION_CONTROLLER", ["MUTATION_CONTROLLER", "RESTORATION_CONTROLLER", "ADJUDICATOR", "TERMINALIZER"], "application/json", False, 32768, "DERIVED", intent),
        ("mutation_receipt", "DESCRIPTOR_ONLY", "MUTATION_CONTROLLER", ["MUTATION_CONTROLLER", "RESTORATION_CONTROLLER", "ADJUDICATOR", "TERMINALIZER"], "application/json", False, 65536, "DERIVED", receipt),
        ("adjudication", "DESCRIPTOR_ONLY", "ADJUDICATOR", ["TERMINALIZER", "EXTERNAL_AGGREGATOR"], "application/json", False, 131072, "DERIVED", adjudication),
        ("terminal_manifest", "TERMINAL_MANIFEST", "TERMINALIZER", ["EXTERNAL_AGGREGATOR"], "text/plain; charset=utf-8", False, 262144, "SEAL", []),
    )
    result: dict[str, Any] = {}
    for ordinal, row in enumerate(rows, 1):
        name, storage, producer, consumers, media, zero, maximum, classification, metadata = row
        result[name] = {
            "ordinal": ordinal,
            "storage_class": storage,
            "producer": producer,
            "source_kind": ROLE_SOURCE_KINDS[name],
            "consumers": consumers,
            "media_type": media,
            "zero_byte_payload_allowed": zero,
            "redaction_status": "NOT_REDACTED",
            "maximum_bytes": maximum,
            "raw_or_derived": classification,
            "required_metadata": metadata,
            "terminal_outcomes_allowed": all_terminal,
            "retention": "IMMUTABLE_UNTIL_EXTERNAL_AGGREGATION_AND_ARCHIVAL",
            "canonical_json_required": media == "application/json",
            "strict_utf8_required": name in ("challenge_stdout", "challenge_stderr", "terminal_manifest", "workload_log_bytes"),
        }
        if name == "mutation_intent":
            result[name]["conditional_required_metadata"] = ["service_restoration_body_reference", "service_restoration_body_sha256"]
        elif name == "mutation_receipt":
            result[name]["conditional_required_metadata"] = ["service_restoration_body_sha256", "post_create_observation_reference"]
        elif name == "adjudication":
            result[name]["conditional_required_metadata"] = ["workload_window_adjudication_identity"]
    return result


def path(*tokens: str) -> list[str]:
    return list(tokens)


def _legacy_kubernetes_surface(deployment_names: list[str]) -> dict[str, Any]:
    identity = [
        path("apiVersion"), path("kind"), path("metadata", "name"), path("metadata", "namespace"),
        path("metadata", "uid"), path("metadata", "resourceVersion"), path("metadata", "generation"),
        path("metadata", "deletionTimestamp"),
    ]
    owner = [
        path("metadata", "ownerReferences", "[]", "apiVersion"),
        path("metadata", "ownerReferences", "[]", "kind"),
        path("metadata", "ownerReferences", "[]", "name"),
        path("metadata", "ownerReferences", "[]", "uid"),
        path("metadata", "ownerReferences", "[]", "controller"),
    ]
    deployment_fields = identity + [
        path("spec", "replicas"), path("status", "replicas"), path("status", "updatedReplicas"),
        path("status", "readyReplicas"), path("status", "availableReplicas"), path("status", "unavailableReplicas"),
        path("status", "conditions", "[]", "type"), path("status", "conditions", "[]", "status"),
        path("status", "conditions", "[]", "reason"), path("status", "conditions", "[]", "lastTransitionTime"),
    ]
    pod_fields = identity + owner + [
        path("metadata", "labels", "service"), path("metadata", "labels", "job-name"),
        path("metadata", "labels", "sremut-mutant-backend"),
        *[path("metadata", "labels", key) for key in CHALLENGE_LABEL_KEYS],
        path("status", "phase"), path("status", "podIP"),
        path("status", "conditions", "[]", "type"), path("status", "conditions", "[]", "status"),
        path("status", "conditions", "[]", "reason"), path("status", "conditions", "[]", "lastTransitionTime"),
        path("status", "containerStatuses", "[]", "name"), path("status", "containerStatuses", "[]", "ready"),
        path("status", "containerStatuses", "[]", "restartCount"),
        path("status", "containerStatuses", "[]", "state", "waiting", "reason"),
        path("status", "containerStatuses", "[]", "state", "terminated", "reason"),
    ]
    service_fields = identity + [
        path("metadata", "labels", "app.kubernetes.io/managed-by"),
        path("metadata", "annotations", "meta.helm.sh/release-name"),
        path("metadata", "annotations", "meta.helm.sh/release-namespace"),
        path("spec", "type"), path("spec", "clusterIP"), path("spec", "clusterIPs", "[]"),
        path("spec", "ipFamilies", "[]"), path("spec", "ipFamilyPolicy"), path("spec", "internalTrafficPolicy"),
        path("spec", "sessionAffinity"), path("spec", "selector", "service"),
        path("spec", "selector", "sremut-mutant-backend"), path("spec", "ports", "[]", "name"),
        path("spec", "ports", "[]", "protocol"), path("spec", "ports", "[]", "port"),
        path("spec", "ports", "[]", "targetPort"), path("spec", "ports", "[]", "nodePort"),
        path("spec", "healthCheckNodePort"), path("spec", "publishNotReadyAddresses"),
        path("spec", "allocateLoadBalancerNodePorts"),
    ]
    endpoint_slice_fields = identity + [
        path("metadata", "labels", "kubernetes.io/service-name"), path("addressType"),
        path("ports", "[]", "name"), path("ports", "[]", "protocol"), path("ports", "[]", "port"),
        path("endpoints", "[]", "addresses", "[]"), path("endpoints", "[]", "conditions", "ready"),
        path("endpoints", "[]", "conditions", "terminating"),
        path("endpoints", "[]", "targetRef", "apiVersion"), path("endpoints", "[]", "targetRef", "kind"),
        path("endpoints", "[]", "targetRef", "namespace"), path("endpoints", "[]", "targetRef", "name"),
        path("endpoints", "[]", "targetRef", "uid"),
    ]
    status_fields = [
        path("apiVersion"), path("kind"), path("status"),
        path("reason"), path("details", "name"), path("details", "group"), path("details", "kind"),
        path("details", "uid"), path("details", "causes", "[]", "reason"),
        path("details", "causes", "[]", "field"), path("code"),
    ]
    return {
        "core_api_group_representation": "",
        "response_kind_semantics": "SINGULAR_KIND_OF_EACH_PROJECTED_RESOURCE; LIST_ENVELOPE_IS_NOT_PUBLISHED",
        "list_response_envelope_publication": "FORBIDDEN",
        "field_path_encoding": {
            "representation": "TOKEN_ARRAY_V1",
            "list_traversal_token": "[]",
            "map_keys_are_single_tokens": True,
            "dotted_path_strings_forbidden": True,
            "jsonpath_jq_json_pointer_interpretation": False,
        },
        "resource_rules": [
            {"id": "DEPLOYMENTS_SOCIAL_READ", "api_group": "apps", "api_version": "v1", "resource": "deployments", "response_kind": "Deployment", "subresource": "", "operations": ["GET", "LIST"], "namespace": "social-network", "names": deployment_names, "name_rule": "EXACT_BASELINE_DEPLOYMENT_OR_LIST", "selectors": [""], "projection_paths": deployment_fields},
            {"id": "COREDNS_DEPLOYMENT_READ", "api_group": "apps", "api_version": "v1", "resource": "deployments", "response_kind": "Deployment", "subresource": "", "operations": ["GET"], "namespace": "kube-system", "names": ["coredns"], "name_rule": "EXACT_NAME", "selectors": [], "projection_paths": deployment_fields},
            {"id": "REPLICASET_OWNER_READ", "api_group": "apps", "api_version": "v1", "resource": "replicasets", "response_kind": "ReplicaSet", "subresource": "", "operations": ["GET"], "namespace": "social-network", "names": [], "name_rule": "EXACT_CONTROLLER_OWNER_REFERENCE_FROM_CAPTURED_POD", "selectors": [], "projection_paths": identity + owner},
            {"id": "PODS_SOCIAL_READ_AND_GUARDED_MUTATION", "api_group": "", "api_version": "v1", "resource": "pods", "response_kind": "Pod", "subresource": "", "operations": ["GET", "LIST", "CREATE", "DELETE"], "namespace": "social-network", "names": [], "name_rule": "CAPTURED_UID_PRECONDITIONED_POD_OR_EXACT_DERIVED_CHALLENGE_NAME", "selectors": ["", "service=user-service", "job-name=wrk2-job", "sremut-mutant-backend=ms-m02"], "projection_paths": pod_fields},
            {"id": "COREDNS_PODS_READ", "api_group": "", "api_version": "v1", "resource": "pods", "response_kind": "Pod", "subresource": "", "operations": ["LIST"], "namespace": "kube-system", "names": [], "name_rule": "LIST_ONLY", "selectors": ["k8s-app=kube-dns"], "projection_paths": pod_fields},
            {"id": "USER_SERVICE_READ_AND_GUARDED_MUTATION", "api_group": "", "api_version": "v1", "resource": "services", "response_kind": "Service", "subresource": "", "operations": ["GET", "CREATE", "DELETE"], "namespace": "social-network", "names": ["user-service"], "name_rule": "EXACT_NAME", "selectors": [], "projection_paths": service_fields},
            {"id": "USER_SERVICE_ENDPOINT_SLICES_READ", "api_group": "discovery.k8s.io", "api_version": "v1", "resource": "endpointslices", "response_kind": "EndpointSlice", "subresource": "", "operations": ["LIST"], "namespace": "social-network", "names": [], "name_rule": "LIST_ONLY", "selectors": ["kubernetes.io/service-name=user-service"], "projection_paths": endpoint_slice_fields},
        ],
        "subresource_rules": [
            {"id": "CHALLENGE_POD_EXEC", "api_group": "", "api_version": "v1", "resource": "pods", "response_kind": "Pod", "subresource": "exec", "operations": ["CONNECT_GET"], "namespace": "social-network", "name_rule": "EXACT_DERIVED_CHALLENGE_POD_WITH_UID_CHECK_BEFORE_AND_AFTER", "python_client_method": "CoreV1Api.connect_get_namespaced_pod_exec", "projection_paths": []},
        ],
        "typed_api_status_projection": {"is_resource_rule": False, "projection_paths": status_fields},
        "projection_value_semantics": {
            "missing_field": "ABSENT",
            "explicit_json_null": "NULL",
            "list_order": "KUBERNETES_RESPONSE_ORDER_UNLESS_RULE_STATES_SORTING",
            "target_port_allowed_scalar_types": ["integer", "string"],
            "complete_metadata_maps_forbidden": True,
        },
        "explicitly_forbidden_resources": [
            "secrets", "configmaps", "serviceaccounts", "roles", "rolebindings", "clusterroles",
            "clusterrolebindings", "persistentvolumes", "persistentvolumeclaims", "events",
            "mutatingwebhookconfigurations", "validatingwebhookconfigurations", "customresources",
        ],
        "pod_log_evidence_capture": "FORBIDDEN_AS_DIRECT_EVIDENCE_SOURCE",
        "stock_workload_internal_calls_documented_not_runner_capture_authority": [
            "CoreV1Api.list_namespaced_pod(job-name=wrk2-job)",
            "CoreV1Api.connect_get_namespaced_pod_exec(command=date -Ins)",
            "CoreV1Api.read_namespaced_pod_log(timestamps=true)",
        ],
        "stock_original_oracle_internal_deserialization_not_restricted_by_publication_allowlist": True,
    }


def kubernetes_surface(deployment_names: list[str]) -> dict[str, Any]:
    """Freeze executable request predicates without expanding mutation authority."""
    surface = _legacy_kubernetes_surface(deployment_names)
    maxima = {
        "DEPLOYMENTS_SOCIAL_READ": 64,
        "PODS_SOCIAL_READ_AND_GUARDED_MUTATION": 256,
        "COREDNS_PODS_READ": 16,
        "USER_SERVICE_ENDPOINT_SLICES_READ": 64,
    }
    predicates: dict[str, dict[str, list[dict[str, Any]]]] = {
        "DEPLOYMENTS_SOCIAL_READ": {
            "GET": [{"type": "EXACT_NAME", "allowed_names": deployment_names}],
            "LIST": [{"type": "LIST_NO_NAME"}],
        },
        "COREDNS_DEPLOYMENT_READ": {"GET": [{"type": "EXACT_NAME", "allowed_names": ["coredns"]}]},
        "REPLICASET_OWNER_READ": {"GET": [{"type": "CAPTURED_OBJECT_NAME", "capture_role": "kubernetes_object_projection", "captured_kind": "ReplicaSet"}]},
        "PODS_SOCIAL_READ_AND_GUARDED_MUTATION": {
            "GET": [{"type": "CAPTURED_OBJECT_NAME", "capture_role": "kubernetes_object_projection", "captured_kind": "Pod"}],
            "LIST": [{"type": "LIST_NO_NAME"}],
            "CREATE": [{"type": "DETERMINISTIC_CHALLENGE_NAME", "prefix": "sremut-challenge-", "required_identity_labels": list(CHALLENGE_LABEL_KEYS)}],
            "DELETE": [
                {"type": "CAPTURED_REPLACEMENT_POD_NAME", "capture_role": "kubernetes_object_projection", "captured_kind": "Pod", "uid_precondition_required": True},
                {"type": "DETERMINISTIC_CHALLENGE_NAME", "prefix": "sremut-challenge-", "required_identity_labels": list(CHALLENGE_LABEL_KEYS), "uid_precondition_required": True},
            ],
        },
        "COREDNS_PODS_READ": {"LIST": [{"type": "LIST_NO_NAME"}]},
        "USER_SERVICE_READ_AND_GUARDED_MUTATION": {
            "GET": [{"type": "EXACT_NAME", "allowed_names": ["user-service"]}],
            "CREATE": [{"type": "EXACT_NAME", "allowed_names": ["user-service"]}],
            "DELETE": [{"type": "EXACT_NAME", "allowed_names": ["user-service"], "uid_precondition_required": True}],
        },
        "USER_SERVICE_ENDPOINT_SLICES_READ": {"LIST": [{"type": "LIST_NO_NAME"}]},
    }
    consumer_by_rule = {
        "DEPLOYMENTS_SOCIAL_READ": ["LIVE_PREFLIGHT", "CONTRACT_EVALUATOR", "RESTORATION_CONTROLLER"],
        "COREDNS_DEPLOYMENT_READ": ["LIVE_PREFLIGHT", "ADJUDICATOR"],
        "REPLICASET_OWNER_READ": ["CONTRACT_EVALUATOR", "ADJUDICATOR"],
        "PODS_SOCIAL_READ_AND_GUARDED_MUTATION": ["LIVE_PREFLIGHT", "MUTATION_CONTROLLER", "RESTORATION_CONTROLLER", "CONTRACT_EVALUATOR"],
        "COREDNS_PODS_READ": ["LIVE_PREFLIGHT", "ADJUDICATOR"],
        "USER_SERVICE_READ_AND_GUARDED_MUTATION": ["LIVE_PREFLIGHT", "MUTATION_CONTROLLER", "RESTORATION_CONTROLLER", "CONTRACT_EVALUATOR"],
        "USER_SERVICE_ENDPOINT_SLICES_READ": ["CONTRACT_EVALUATOR", "ADJUDICATOR"],
    }
    list_item_predicates = {
        "DEPLOYMENTS_SOCIAL_READ": {"type": "EXACT_NAME", "allowed_names": deployment_names},
        "PODS_SOCIAL_READ_AND_GUARDED_MUTATION": {"type": "DNS_SUBDOMAIN_MATCHING_REQUEST_SELECTOR"},
        "COREDNS_PODS_READ": {"type": "DNS_SUBDOMAIN_MATCHING_REQUEST_SELECTOR"},
        "USER_SERVICE_ENDPOINT_SLICES_READ": {"type": "DNS_SUBDOMAIN_MATCHING_REQUEST_SELECTOR"},
    }
    operation_kinds = {
        "PODS_SOCIAL_READ_AND_GUARDED_MUTATION": {
            "CREATE": ["CHALLENGE_POD_CREATION"],
            "DELETE": ["INITIAL_CAPTURED_POD_RECYCLE_DELETE", "REPLACEMENT_POD_DELETION", "CHALLENGE_POD_DELETION", "RECOVERY_POD_RECYCLE_DELETE"],
        },
        "USER_SERVICE_READ_AND_GUARDED_MUTATION": {
            "CREATE": ["MUTANT_SERVICE_CREATION", "RESTORED_SERVICE_CREATION"],
            "DELETE": ["INITIAL_USER_SERVICE_DELETION", "MUTANT_SERVICE_DELETION"],
        },
    }
    for rule in surface["resource_rules"]:
        rule["context"] = "kind-kind"
        rule["name_predicates_by_operation"] = predicates[rule["id"]]
        selectors = rule.pop("selectors")
        rule["label_selectors"] = selectors if selectors else [""]
        rule["field_selectors"] = [""]
        rule["request_options"] = {"dry_run": False, "pretty": False, "watch": False}
        rule["purpose"] = "CAPTURE_ADMISSIBLE_EVIDENCE_OR_PROFILE_AUTHORIZED_OPERATION"
        rule["consumers"] = consumer_by_rule[rule["id"]]
        rule["projection_class"] = "KUBERNETES_OBJECT_PROJECTION_V1"
        rule["maximum_list_items"] = maxima.get(rule["id"], 0)
        rule["list_item_name_predicate"] = list_item_predicates.get(rule["id"], {"type": "NOT_A_LIST_RULE"})
        rule["list_metadata_projection"] = [path("resourceVersion"), path("continue"), path("remainingItemCount")]
        rule["evidence_policy_grants_mutation_authority"] = False
        rule["mutation_requires_frozen_profile_operation_kind"] = any(operation in ("CREATE", "DELETE") for operation in rule["operations"])
        rule["mutation_requires_intent_evidence_reference"] = rule["mutation_requires_frozen_profile_operation_kind"]
        rule["profile_operation_kinds_by_operation"] = operation_kinds.get(rule["id"], {})
    for rule in surface["subresource_rules"]:
        rule.update({
            "context": "kind-kind",
            "name_predicates_by_operation": {
                "CONNECT_GET": [{"type": "DETERMINISTIC_CHALLENGE_NAME", "prefix": "sremut-challenge-", "required_identity_labels": list(CHALLENGE_LABEL_KEYS)}],
            },
            "label_selectors": [""],
            "field_selectors": [""],
            "request_options": {"stdin": False, "tty": False, "preload_content": False},
            "purpose": "FIXED_CHALLENGE_EXECUTION",
            "consumers": ["CONTRACT_EVALUATOR", "ADJUDICATOR"],
            "projection_class": "KUBERNETES_EXEC_RESULT_V1",
            "maximum_list_items": 0,
            "evidence_policy_grants_mutation_authority": False,
            "mutation_requires_frozen_profile_operation_kind": False,
            "mutation_requires_intent_evidence_reference": False,
        })
    surface["request_validation"] = {
        "document_type": "KUBERNETES_REQUEST_V1",
        "arbitrary_empty_name_is_never_accepted": True,
        "pod_delete_requires_captured_or_deterministic_name": True,
        "pod_delete_requires_captured_uid_and_uid_precondition": True,
        "pod_delete_requires_mutation_intent_evidence_reference": True,
        "unrelated_pod_delete_rejected": True,
    }
    surface["list_response_validation"] = {
        "document_type": "KUBERNETES_LIST_RESPONSE_V1",
        "metadata_validated_separately": True,
        "every_item_validated": True,
        "mixed_kinds_rejected": True,
        "unapproved_items_rejected": True,
        "excess_items_rejected": True,
    }
    return surface


def responsibility_map(roles: dict[str, Any]) -> list[dict[str, Any]]:
    rows = (
        ("SREMUT_RUNNER", ["RUNNER_IDENTITY_RECORDER", "RUNNER_PREFLIGHT_RECORDER"], "RESPONSIBILITY_SPLIT", ["record immutable run identities", "capture healthy prestate"], ["run_identity", "healthy_prestate"], False, False, False, True, False),
        ("KUBERNETES_API_PROJECTOR", ["RUNNER_KUBERNETES_CLIENT"], "NAME_SPECIALIZATION", ["issue allowlisted read requests", "publish closed Kubernetes projections"], ["kubernetes_request_identity", "kubernetes_object_projection", "kubernetes_api_status", "kubernetes_api_error_body"], False, False, True, True, False),
        ("CHALLENGE_EXECUTOR", ["CHALLENGE_EXEC_ADAPTER"], "NAME_SPECIALIZATION", ["record fixed challenge invocations and streams"], ["challenge_invocation", "challenge_stdout", "challenge_stderr", "challenge_result"], False, False, True, True, False),
        ("WORKLOAD_HISTORY_SERIALIZER", ["WORKLOAD_EVIDENCE_ADAPTER"], "RESPONSIBILITY_MERGE", ["serialize complete workload history and boundaries"], ["workload_log_bytes", "workload_boundary"], False, False, False, True, False),
        ("WORKLOAD_RESULT_PARSER", ["WORKLOAD_EVIDENCE_ADAPTER"], "RESPONSIBILITY_MERGE", ["parse frozen workload windows"], ["workload_parse_result"], False, False, False, True, False),
        ("MUTATION_COORDINATOR", ["MUTATION_CONTROLLER"], "NAME_SPECIALIZATION", ["seal mutation intent", "dispatch profile-authorized mutation", "seal receipt", "perform profile-authorized restoration"], ["mutation_intent", "mutation_receipt"], True, True, True, True, False),
        ("TERMINAL_SEALER", ["TERMINALIZER"], "NAME_SPECIALIZATION", ["install terminal identity and final manifest"], ["terminal_manifest"], False, False, False, True, True),
        ("ORIGINAL_ORACLE_ADAPTER", ["ORIGINAL_ORACLE_ADAPTER"], "NAME_SPECIALIZATION", ["execute isolated stock original-oracle adapter read-only"], ["original_oracle_input", "original_oracle_stdout", "original_oracle_stderr", "original_oracle_result"], False, False, False, True, False),
        ("ADJUDICATOR", ["ADJUDICATOR"], "NAME_SPECIALIZATION", ["classify outcomes from cited immutable evidence"], ["adjudication"], False, False, False, True, False),
    )
    result: list[dict[str, Any]] = []
    for focused, generated, classification, responsibilities, evidence_roles, mutation, restoration, kubernetes, raw, terminal in rows:
        consumers = sorted({consumer for role in evidence_roles for consumer in roles[role]["consumers"]})
        result.append({
            "focused_identity": focused,
            "generated_identities": generated,
            "classification": classification,
            "exact_responsibilities": responsibilities,
            "evidence_roles_produced": evidence_roles,
            "consumers_served": consumers,
            "mutation_authority": mutation,
            "restoration_authority": restoration,
            "kubernetes_request_authority": kubernetes,
            "raw_evidence_access": raw,
            "terminalization_authority": terminal,
        })
    return result


def restoration_projection(profile: dict[str, Any]) -> dict[str, Any]:
    stripped = deepcopy(profile["service_normalization"]["stripped_fields"])
    return {
        "projection_class": "SERVICE_RESTORATION_BODY_V1",
        "role": "kubernetes_object_projection",
        "source": "captured healthy Service/user-service",
        "derivation_algorithm": "KUBERNETES_SERVICE_CREATE_BODY_NORMALIZATION_V1",
        "sensitive_screening_before_publication_and_mutation": True,
        "source_service_identity_fields": ["uid", "resourceVersion"],
        "exact_stripped_field_token_paths": [
            [token.replace("*", "[]") for token in item.split("/") if token]
            for item in stripped
        ],
        "wildcard_token_normalization": "* becomes []",
        "preserve_every_other_field_and_value": True,
        "required_metadata": [
            "projection_class", "derivation_algorithm", "source_service_uid",
            "source_service_resource_version", "exact_stripped_field_token_paths",
            "normalized_body_sha256", "canonical_json_identity", "capture_timestamp",
            "source_request_reference",
        ],
        "healthy_prestate_reference_must_match_projection_class": True,
        "mutation_intent_must_cite_before_service_deletion": True,
        "restoration_uses_only_sealed_body": True,
        "restoration_receipt_cites_same_body_hash_and_post_create_observation": True,
        "capture_rejection_forbids_mutation": True,
        "dynamic_cluster_ip_rule": deepcopy(profile["service_normalization"]["dynamic_cluster_ip_change_allowed"]),
        "canonical_round_trip_required": True,
    }


def build_policy(frozen_at: str, contract: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    intent = deepcopy(profile["mutation_operation_protocol"]["intent"]["required_fields"])
    receipt = deepcopy(profile["mutation_operation_protocol"]["receipt"]["required_fields"])
    adjudication = deepcopy(profile["adjudication_evidence_protocol"]["required_fields"])
    deployment_names = sorted(contract["baseline_evidence"]["baseline_deployment_replica_floor"])
    roles = role_specs(intent, receipt, adjudication)
    policy = {
        "document_type": "EVIDENCE_POLICY_DOCUMENT_V1",
        "schema_version": 1,
        "policy_id": "sremut/missing-service-social-network/evidence-capture-v1",
        "status": "FROZEN_BEFORE_EVIDENCE_IMPLEMENTATION",
        "frozen_at": frozen_at,
        "future_annotated_tag": "sremut-missing-service-evidence-policy-v1",
        "bindings": {
            "contract": {"tag": CONTRACT_TAG, "tag_object": CONTRACT_TAG_OBJECT, "commit": CONTRACT_COMMIT, "tree": CONTRACT_TREE, "path": "contracts/missing_service_social_network.yaml", "sha256": CONTRACT_SHA256, "registry_path": "mutants/missing_service_social_network/registry.yaml", "registry_sha256": REGISTRY_SHA256},
            "execution_profile": {"tag": PROFILE_TAG, "tag_object": PROFILE_TAG_OBJECT, "commit": PROFILE_COMMIT, "tree": PROFILE_TREE, "path": "profiles/missing_service_social_network/pilot-v1.yaml", "sha256": PROFILE_SHA256, "schema_path": "schemas/missing-service-execution-profile-v1.schema.json", "schema_sha256": PROFILE_SCHEMA_SHA256},
            "project": {"pyproject_sha256": PYPROJECT_SHA256, "uv_lock_sha256": UV_LOCK_SHA256},
            "sregym": {"commit": SREGYM_COMMIT, "applications_commit": APPLICATIONS_COMMIT, "source_sha256": deepcopy(SOURCE_HASHES)},
        },
        "runner_release_binding": {
            "binding_mode": "SEPARATELY_FROZEN_EXECUTION_RELEASE",
            "canonical_input": "sorted_relative_path_two_spaces_sha256_newline_manifest",
            "required_before_non_mutating_live_preflight": True,
            "required_before_mutant_execution": True,
            "policy_update_on_binding_forbidden": True,
            "required_release_artifact": "RUNNER_BUNDLE_SHA256SUMS",
            "required_release_identity_fields": ["manifest_sha256", "bundle_sha256", "git_commit", "git_tree", "annotated_tag_name", "annotated_tag_object", "pyproject_sha256", "uv_lock_sha256"],
            "future_run_identity_must_cite_release_artifact_and_annotated_tag": True,
        },
        "canonicalization": {
            "descriptor_encoding": "CANONICAL_COMPACT_SORTED_UTF8_JSON_V1",
            "json_separators": [",", ":"],
            "ensure_ascii": False,
            "floating_point_values_forbidden_recursively": True,
            "descriptor_sha256_domain": "canonical descriptor bytes excluding evidence_id and descriptor_sha256",
            "descriptor_sha256_stored_inside_descriptor": False,
        },
        "runtime_validation": {
            "schema_dialect": "https://json-schema.org/draft/2020-12/schema",
            "public_entrypoint_discriminated_one_of": True,
            "named_validator_hooks": list(RUNTIME_VALIDATOR_HOOKS),
            "json_schema_cross_document_comparison_claimed": False,
        },
        "common_metadata": {
            "required_fields": list(COMMON_METADATA),
            "schema_version": 1,
            "evidence_id_grammar": "^ev-[0-9a-f]{32}$",
            "evidence_id_derivation": "ev- plus first32 lowercase hex of descriptor_sha256",
            "run_id_grammar": "^sremut-ms-(m01|m02|m03)-r0[1-3]-a0[1-2]-[0-9a-f]{12}$",
            "attempt_id_grammar": "^a0[1-2]$",
            "attempt_id_nullable": False,
            "created_utc_grammar": "^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\\.[0-9]{9}Z$",
            "monotonic_ns_type": "NONNEGATIVE_INTEGER",
            "boot_identity_grammar": "RFC4122_LOWERCASE_UUID",
            "producer_enum": list(PRODUCERS),
            "source_kind_enum": ["KUBERNETES", "SUBPROCESS", "IN_PROCESS_WORKLOAD", "GENERATED_DESCRIPTOR", "JOURNAL", "LOCAL_IDENTITY"],
            "redaction_status_const": "NOT_REDACTED",
        },
        "evidence_reference": {
            "variants": ["PAYLOAD_BACKED", "DESCRIPTOR_ONLY"],
            "common_fields": ["schema_version", "evidence_id", "role", "media_type", "storage_class", "descriptor_sha256", "descriptor_relative_path", "redaction_status"],
            "payload_backed_additional_fields": ["payload_sha256", "payload_size_bytes", "payload_relative_path"],
            "descriptor_cross_check": "descriptor role, media_type, storage_class and payload identity must equal EvidenceRef",
            "payload_relative_path_grammar": "objects/sha256/{sha256[0:2]}/{sha256}",
            "descriptor_relative_path_grammar": "descriptors/sha256/{sha256[0:2]}/{sha256}.json",
            "absolute_path_forbidden": True,
            "filesystem_relative_path_security": {
                "attempt_root_preopened_directory_descriptor_required": True,
                "absolute_paths_forbidden": True,
                "dot_and_dotdot_components_forbidden": True,
                "backslash_forbidden": True,
                "symlink_components_forbidden": True,
                "resolution_requirement": "OPENAT_DIRECTORY_FD_COMPONENT_WALK_WITH_O_NOFOLLOW",
                "resolved_target_must_remain_beneath_attempt_root": True,
            },
            "zero_byte_payload": {"present_is_not_absent": True, "size_bytes": 0, "sha256": ZERO_SHA256},
        },
        "storage_model": {
            "payload": {"content_addressed": True, "sha256_domain": "exact supplied payload bytes", "contains_self_hash_metadata": False},
            "descriptor": {"content_addressed": True, "sha256_domain": "canonical descriptor bytes excluding descriptor_sha256", "descriptor_sha256_location": "EvidenceRef and journal only"},
            "journal": {
                "authority": "APPEND_ONLY_HASH_CHAIN",
                "record_hash_domain": "canonical compact sorted UTF-8 JSON excluding canonical_current_entry_sha256",
                "required_fields": ["schema_version", "document_type", "journal_record_type", "sequence_number", "previous_entry_sha256", "canonical_current_entry_sha256", "run_id", "attempt_id", "transition", "referenced_intent_receipt_and_adjudication_sha256", "utc_time", "monotonic_ns", "boot_identity"],
                "supplementary_closed_fields": ["referenced_descriptor_sha256", "referenced_payload_sha256"],
                "genesis_previous_entry_sha256": GENESIS_SHA256,
                "sequence_increments_by_one": True,
                "capture_rejection_is_journal_only": True,
                "capture_rejection_required_fields": ["schema_version", "document_type", "journal_record_type", "sequence_number", "previous_entry_sha256", "canonical_current_entry_sha256", "run_id", "attempt_id", "intended_role", "rejection_code", "detector_id", "source_kind", "observed_size", "created_utc", "monotonic_ns", "boot_identity"],
                "capture_rejection_forbidden_fields": ["rejected_payload", "rejected_descriptor", "rejected_content_sha256", "matched_bytes", "surrounding_text", "secret_values", "resolved_absolute_paths"],
            },
            "terminal_manifest": {
                "storage_class": "TERMINAL_MANIFEST",
                "sha256sums_line_grammar": "lowercase_sha256 + two ASCII spaces + attempt-relative-path + LF",
                "lines_sorted_by_relative_path_utf8_bytes": True,
                "covers": ["published payload objects", "published descriptors", "append-only journal files", "terminal journal record", "global stop marker only for RESTORATION_BLOCKED"],
                "excludes": ["terminal manifest itself", "state/current.json", "transaction temporary files"],
                "contains_manifest_sha256": False,
                "contains_descriptor_sha256": False,
                "has_descriptor": False,
                "has_self_evidence_reference": False,
                "hash_size_line_count_recomputed_from_sealed_file": True,
                "terminal_state_established_by": "terminal journal record and selected terminal manifest path",
                "last_durable_attempt_artifact": True,
                "sealed_attempt_modification_after_install_forbidden": True,
            },
            "publication_atomicity_claim": "INDIVIDUALLY_ATOMIC_SAME_FILESYSTEM_REPLACEMENTS_ONLY",
        },
        "roles": roles,
        "responsibility_provenance": responsibility_map(roles),
        "service_restoration_projection": restoration_projection(profile),
        "run_identity_protocol": {
            "role": "run_identity",
            "instances_per_attempt": 2,
            "phases_in_order": ["START", "TERMINAL"],
            "exactly_once_for_terminal_outcomes": list(TERMINAL_OUTCOMES),
            "start_required_fields": ["runtime_identity", "contract_binding", "execution_profile_binding", "runner_release_binding", "pyproject_sha256", "uv_lock_sha256", "source_alias_sha256", "kubeconfig_content_sha256", "kubectl_default_cache_before_sha256"],
            "start_forbidden_fields": ["kubectl_default_cache_after_sha256", "terminal_outcome"],
            "terminal_required_fields": ["start_identity_reference", "terminal_release_identity", "terminal_source_alias_sha256", "kubectl_default_cache_before_sha256", "kubectl_default_cache_after_sha256", "terminal_outcome"],
            "terminal_start_reference_must_be_descriptor_only_run_identity_start": True,
            "kubeconfig_contents_forbidden": True,
            "no_kubernetes_or_kubectl_operation_after_terminal_after_cache_snapshot": True,
        },
        "workload_evidence_protocol": {
            "source_alias": "WORKLOAD_HISTORY",
            "source": "sregym.generators.workload.stream.StreamWorkloadManager.log_history",
            "source_type": "class-level list[WorkloadEntry] isolated per evaluation",
            "complete_entry_fields": {"time": "finite Python float", "number": "integer request count", "log": "Python str", "ok": "boolean"},
            "raw_log_bytes_serialization": "b'\\n'.join(entry.log.encode('utf-8', errors='strict') for entry in log_history)",
            "entry_time_identity": "struct.pack('>d', entry.time).hex()",
            "entry_time_ieee754_binary64_hex_grammar": "^[0-9a-f]{16}$",
            "nonfinite_time_forbidden": True,
            "time_identity_array_length_equals_complete_entry_count": True,
            "ordering": "log_history order",
            "boundary_sequence": ["isolate class-level log_history", "collect one request using stock WorkloadOracle semantics", "seal prefix raw bytes and exact complete-entry count", "collect at least fifty fresh requests", "require later bytes have exact prefix", "parse only complete appended entries"],
            "boundary_raw_log_is_evidence_reference_not_duplicate_text": True,
            "new_direct_pod_log_capture_for_evidence_forbidden": True,
            "minimum_fresh_requests": 50,
            "maximum_non_2xx_or_3xx_responses": 0,
            "fresh_workload_deadline_seconds": 60,
            "strict_utf8_required": True,
            "window_identity": {
                "phase_ordinal_enum": deepcopy(WORKLOAD_WINDOWS),
                "required_fields": ["phase", "ordinal", "run_id", "attempt_id", "mutant_id", "repetition", "boundary_reference", "raw_log_reference", "parse_result_reference"],
                "references_must_share_exact_identity": True,
                "cross_window_substitution_forbidden": True,
            },
        },
        "challenge_stream_protocol": {
            "mechanism": "PERSISTENT_HARDENED_CHALLENGE_POD_THEN_PODS_EXEC",
            "pod_namespace": "social-network",
            "exec_python_client_method": "CoreV1Api.connect_get_namespaced_pod_exec",
            "exec_http_operation": "CONNECT_GET",
            "pod_uid_check": "immediately before and after every exec",
            "stdout_serialization": "PYTHON_STRICT_UTF8_OF_KUBERNETES_WS_CHANNEL_TEXT_V1",
            "stderr_serialization": "PYTHON_STRICT_UTF8_OF_KUBERNETES_WS_CHANNEL_TEXT_V1",
            "serialization_definition": ["collect Kubernetes client channel-1 or channel-2 text in observed order", "preserve every Python string character without newline normalization", "encode final Python string as UTF-8 with errors=strict"],
            "raw_websocket_frame_bytes_claimed": False,
            "exit_status_source": "Kubernetes WebSocket status channel as interpreted by kubernetes-client stream API",
            "pod_log_subresource_used": False,
            "approved_templates": {
                "CHALLENGE_DNS_V1": {"argv": ["sh", "-c", "nslookup user-service.social-network.svc.cluster.local"], "parameters": []},
                "CHALLENGE_TCP_V1": {"argv": ["sh", "-c", "nc -z -w 3 user-service.social-network.svc.cluster.local 9090"], "parameters": []},
                "WORKLOAD_CLOCK_V1": {"argv": ["date", "-Ins"], "parameters": []},
                "ORIGINAL_ORACLE_ADAPTER_V1": {"argv_tokens": ["ORIGINAL_ORACLE_EXECUTABLE", "-B", "SEPARATELY_FROZEN_ADAPTER", "--input", "ATTEMPT_RELATIVE_INPUT", "--output", "ATTEMPT_RELATIVE_OUTPUT"], "parameters": ["ATTEMPT_RELATIVE_INPUT", "ATTEMPT_RELATIVE_OUTPUT"]},
            },
            "arbitrary_argv_forbidden": True,
            "template_id_and_non_sensitive_parameters_reconstruct_exact_argv": True,
            "sensitive_parameter_rejects_invocation_evidence": True,
            "redacted_invocation_cannot_satisfy_reference": True,
        },
        "challenge_pod_template": {
            "image": profile["challenge_pod"]["image"],
            "image_pull_policy": "IfNotPresent",
            "command": ["sh", "-c", "trap 'exit 0' TERM INT; sleep 600 & wait"],
            "automount_service_account_token": False,
            "restart_policy": "Never",
            "active_deadline_seconds": 600,
            "termination_grace_period_seconds": 5,
            "label_keys": list(CHALLENGE_LABEL_KEYS),
            "security_context": deepcopy(profile["challenge_pod"]["security_context"]),
            "resources": deepcopy(profile["challenge_pod"]["resources"]),
        },
        "kubernetes_evidence_surface": kubernetes_surface(deployment_names),
        "local_source_aliases": deepcopy(SOURCE_ALIASES),
        "sensitive_capture_policy": {
            "accepted_redaction_status": "NOT_REDACTED",
            "rejected_capture_publication_forbidden": True,
            "detectors": {
                "SENSITIVE_AUTHORIZATION_HEADER": {"kind": "BYTE_REGEX", "grammar": "(?im)^Authorization:[ \\t]*[^\\r\\n]+(?=\\r?$)"},
                "SENSITIVE_BEARER_CREDENTIAL": {"kind": "BYTE_REGEX", "grammar": "(?i)\\bBearer[ \\t]+[A-Za-z0-9._~+/=-]{16,4096}\\b"},
                "SENSITIVE_PEM_PRIVATE_KEY": {"kind": "BYTE_MARKERS", "begin_markers": ["-----BEGIN PRIVATE KEY-----", "-----BEGIN RSA PRIVATE KEY-----", "-----BEGIN EC PRIVATE KEY-----", "-----BEGIN OPENSSH PRIVATE KEY-----"]},
                "SENSITIVE_STRUCTURED_SECRET": {"kind": "CONTEXTUAL_STRUCTURED_KEYS", "token_keys": ["access_token", "api_key", "bearer_token", "client_secret", "id_token", "refresh_token", "token"], "password_keys": ["passphrase", "password"], "private_key_keys": ["client_key_data", "private_key", "private_key_data", "ssh_private_key"], "nonempty_values_only": True, "recursive_mappings_and_lists": True},
                "SENSITIVE_KUBECONFIG_STRUCTURE": {"kind": "RECOGNIZED_KUBECONFIG_CONTEXT_ONLY", "recognition": {"apiVersion": "v1", "kind": "Config", "required_root_keys": ["clusters", "contexts", "users"]}, "credential_keys": ["client-key-data", "client-key", "client-certificate-data", "client-certificate", "token", "token-file", "username", "password", "auth-provider", "exec"], "ordinary_json_username_or_exec_does_not_trigger": True},
                "SENSITIVE_CREDENTIAL_URL": {"kind": "PARSED_URL_USERINFO", "schemes": ["http", "https"], "require_nonempty_username_and_password": True},
                "SENSITIVE_KUBERNETES_SECRET_OBJECT": {"kind": "STRUCTURED_KUBERNETES_OBJECT", "apiVersion": "v1", "kind_value": "Secret"},
                "SENSITIVE_ENVIRONMENT_ASSIGNMENT": {"kind": "ANCHORED_LINE", "grammar": "^[A-Za-z_][A-Za-z0-9_]{0,127}=.*$", "sensitive_normalized_keys": ["API_KEY", "AUTH_TOKEN", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AZURE_CLIENT_SECRET", "GITHUB_TOKEN", "GOOGLE_APPLICATION_CREDENTIALS", "KUBECONFIG", "PASSWORD", "PRIVATE_KEY", "SECRET", "TOKEN"], "nonempty_value_required": True},
                "SENSITIVE_DENSE_ENVIRONMENT_DUMP": {"kind": "CONSECUTIVE_LINE_THRESHOLD", "line_grammar": "^[A-Za-z_][A-Za-z0-9_]{0,127}=.*$", "minimum_consecutive_lines": 4},
            },
            "generic_cloud_or_api_credential_detector": False,
            "generic_high_entropy_detector": False,
            "safe_rejection_codes": ["CAPTURE_REJECTED_AUTHORIZATION", "CAPTURE_REJECTED_BEARER", "CAPTURE_REJECTED_PRIVATE_KEY", "CAPTURE_REJECTED_STRUCTURED_SECRET", "CAPTURE_REJECTED_KUBECONFIG", "CAPTURE_REJECTED_CREDENTIAL_URL", "CAPTURE_REJECTED_KUBERNETES_SECRET", "CAPTURE_REJECTED_ENVIRONMENT", "CAPTURE_REJECTED_SIZE", "CAPTURE_REJECTED_INVALID_ENCODING", "CAPTURE_REJECTED_UNAUTHORIZED_SOURCE"],
            "journal_safe_fields": ["schema_version", "document_type", "journal_record_type", "sequence_number", "previous_entry_sha256", "canonical_current_entry_sha256", "run_id", "attempt_id", "intended_role", "rejection_code", "detector_id", "source_kind", "observed_size", "created_utc", "monotonic_ns", "boot_identity"],
            "false_positive_controls": ["token_count", "password_policy_text", "automountServiceAccountToken=false", "public certificate fingerprints", "unanchored source-code text mentioning Authorization", "ordinary username and exec keys outside kubeconfig scope"],
        },
        "expanded_frozen_metadata": {
            "source_execution_profile_sha256": PROFILE_SHA256,
            "mutation_intent_required_metadata": intent,
            "mutation_receipt_required_metadata": receipt,
            "adjudication_required_metadata": adjudication,
            "runtime_source_pointer_required": False,
            "exact_order_required": True,
        },
        "adjudication_vocabularies": {
            "execution_status_values": deepcopy(profile["adjudication"]["execution_status_values"]),
            "original_oracle_verdict_values": deepcopy(profile["adjudication"]["original_oracle_verdict_values"]),
            "contract_verdict_values": deepcopy(profile["adjudication"]["contract_verdict_values"]),
            "classifications": deepcopy(profile["adjudication"]["classifications"]),
            "target_predicate_deadline_failure": profile["adjudication"]["target_predicate_deadline_failure"],
            "target_timeout_never_implies_infrastructure_failure": profile["adjudication"]["target_timeout_never_implies_infrastructure_failure"],
        },
        "adjudication_raw_evidence_rule": {
            "scope": "EVERY_BOOLEAN_OR_CATEGORICAL_RESULT",
            "minimum_payload_backed_raw_evidence_references": 1,
            "descriptor_only_reference_alone_sufficient": False,
            "redacted_or_rejected_reference_sufficient": False,
        },
        "terminalization": {
            "required_run_identity_phases": ["START", "TERMINAL"],
            "required_phase_count": {"START": 1, "TERMINAL": 1},
            "outcomes": {"FINALIZED": "COMPLETE", "ABORTED_SAFE": "COMPLETE", "RESTORATION_BLOCKED": "SEALED_PARTIAL_WITH_GLOBAL_STOP"},
            "manifest_is_last_durable_write": True,
            "nothing_in_sealed_attempt_modified_after_manifest": True,
        },
        "semantic_non_interference": {
            "governs_evidence_admissibility_only": True,
            "grants_mutation_authority": False,
            "contract_invariants_unchanged": ["MS-I1", "MS-I2", "MS-I3", "MS-I4", "MS-I5", "MS-I6"],
            "mutants_unchanged": ["MS-M01", "MS-M02", "MS-M03"],
            "original_oracle_verdict_hypotheses_unchanged": True,
            "expected_contract_verdicts_unchanged": True,
            "repetition_schedule_retry_state_machine_timeouts_restoration_unchanged": True,
            "repetition_count_and_order_unchanged": True,
            "retry_policy_unchanged": True,
            "state_machine_unchanged": True,
            "timeouts_unchanged": True,
            "no_agent_policy_unchanged": True,
            "challenge_semantics_unchanged": True,
            "restoration_semantics_unchanged": True,
            "separately_frozen_runner_release_rule_unchanged": True,
            "stock_original_oracle_source_unchanged": True,
        },
    }
    validate_policy_semantics(policy, profile)
    return policy


def contains_key(value: Any, forbidden: str) -> bool:
    if isinstance(value, dict):
        return forbidden in value or any(contains_key(child, forbidden) for child in value.values())
    if isinstance(value, list):
        return any(contains_key(child, forbidden) for child in value)
    return False


def validate_policy_semantics(policy: dict[str, Any], profile: dict[str, Any]) -> None:
    ensure_no_floats(policy)
    if tuple(policy["roles"]) != ROLE_NAMES or len(policy["roles"]) != 21:
        raise ValueError("Policy must contain exactly the ordered 21-role vocabulary")
    for name, spec in policy["roles"].items():
        if spec["producer"] not in PRODUCERS or not spec["consumers"]:
            raise ValueError(f"Invalid role producer/consumer: {name}")
        if any(consumer not in CONSUMERS for consumer in spec["consumers"]):
            raise ValueError(f"Unknown consumer in {name}")
        if spec["media_type"] not in MEDIA_TYPES or spec["storage_class"] not in STORAGE_CLASSES:
            raise ValueError(f"Invalid media/storage in {name}")
        if spec["redaction_status"] != "NOT_REDACTED":
            raise ValueError("Redacted evidence is never admissible")
        if not isinstance(spec["maximum_bytes"], int) or spec["maximum_bytes"] <= 0:
            raise ValueError("Role maximum must be a positive integer")
        if spec["source_kind"] != ROLE_SOURCE_KINDS[name]:
            raise ValueError("Role source kind differs")
    if contains_key(policy, "current_head") or contains_key(policy, "current_tree") or "runner_scaffold" in policy["bindings"]:
        raise ValueError("Transaction-time Git identity must not be serialized")
    if policy["runtime_validation"]["named_validator_hooks"] != list(RUNTIME_VALIDATOR_HOOKS):
        raise ValueError("Runtime validator hook vocabulary differs")
    if not policy["roles"]["workload_log_bytes"]["strict_utf8_required"]:
        raise ValueError("Workload raw logs must be strict UTF-8")
    if contains_key(policy, "required_metadata_source"):
        raise ValueError("Runtime metadata source pointers are forbidden")
    expanded = policy["expanded_frozen_metadata"]
    profile_receipt = profile["mutation_operation_protocol"]["receipt"]["required_fields"]
    augmented_receipt = list(dict.fromkeys(list(COMMON_METADATA) + profile_receipt))
    receipt_expected = augmented_receipt if all(field in expanded["mutation_receipt_required_metadata"] for field in COMMON_METADATA) else profile_receipt
    exact = (
        ("mutation_intent_required_metadata", profile["mutation_operation_protocol"]["intent"]["required_fields"]),
        ("mutation_receipt_required_metadata", receipt_expected),
        ("adjudication_required_metadata", profile["adjudication_evidence_protocol"]["required_fields"]),
    )
    for key, expected in exact:
        if expanded[key] != expected:
            raise ValueError(f"Frozen metadata expansion differs: {key}")
    if policy["roles"]["mutation_intent"]["required_metadata"] != exact[0][1]:
        raise ValueError("Intent role metadata differs from expansion")
    if policy["roles"]["mutation_receipt"]["required_metadata"] != exact[1][1]:
        raise ValueError("Receipt role metadata differs from expansion")
    if policy["roles"]["adjudication"]["required_metadata"] != exact[2][1]:
        raise ValueError("Adjudication role metadata differs from expansion")
    if policy["runner_release_binding"]["binding_mode"] != "SEPARATELY_FROZEN_EXECUTION_RELEASE":
        raise ValueError("Runner release binding mode differs")
    if contains_key(policy, "runner_bundle_sha256"):
        raise ValueError("Future runner-bundle hash field is forbidden")
    manifest = policy["storage_model"]["terminal_manifest"]
    if any(manifest[key] for key in ("contains_manifest_sha256", "contains_descriptor_sha256", "has_descriptor", "has_self_evidence_reference")):
        raise ValueError("Terminal manifest self-reference is forbidden")
    if policy["run_identity_protocol"]["instances_per_attempt"] != 2:
        raise ValueError("run_identity must have exactly two phases")
    if "kubectl_default_cache_after_sha256" not in policy["run_identity_protocol"]["start_forbidden_fields"]:
        raise ValueError("START must forbid cache-after")
    if policy["workload_evidence_protocol"]["entry_time_identity"] != "struct.pack('>d', entry.time).hex()":
        raise ValueError("Workload time identity differs")
    if policy["workload_evidence_protocol"]["window_identity"]["phase_ordinal_enum"] != WORKLOAD_WINDOWS:
        raise ValueError("Workload window enumeration differs")
    path_security = policy["evidence_reference"]["filesystem_relative_path_security"]
    if not path_security["symlink_components_forbidden"] or not path_security["resolved_target_must_remain_beneath_attempt_root"]:
        raise ValueError("Evidence path security differs")
    surface = policy["kubernetes_evidence_surface"]
    if surface["list_response_envelope_publication"] != "FORBIDDEN":
        raise ValueError("Kubernetes list envelope publication differs")
    detectors = policy["sensitive_capture_policy"]["detectors"]
    dense = detectors["SENSITIVE_DENSE_ENVIRONMENT_DUMP"]
    if dense != {"kind": "CONSECUTIVE_LINE_THRESHOLD", "line_grammar": "^[A-Za-z_][A-Za-z0-9_]{0,127}=.*$", "minimum_consecutive_lines": 4}:
        raise ValueError("Dense environment detector differs")
    kubeconfig = detectors["SENSITIVE_KUBECONFIG_STRUCTURE"]
    if not kubeconfig["ordinary_json_username_or_exec_does_not_trigger"]:
        raise ValueError("Kubeconfig detector must be contextual")
    if not detectors["SENSITIVE_STRUCTURED_SECRET"]["recursive_mappings_and_lists"]:
        raise ValueError("Structured secret detection must recurse")
    if policy["local_source_aliases"] != SOURCE_ALIASES or any(not ALIAS_RE.fullmatch(alias) for alias in SOURCE_ALIASES):
        raise ValueError("Source aliases differ")
    for rule in policy["kubernetes_evidence_surface"]["resource_rules"]:
        if rule["resource"] == "secrets":
            raise ValueError("Secret rule forbidden")
        for field_path in rule["projection_paths"]:
            validate_field_path(field_path)
        if rule["context"] != "kind-kind" or not rule["name_predicates_by_operation"]:
            raise ValueError("Kubernetes request predicates are not executable")
        if "LIST" in rule["operations"] and rule["maximum_list_items"] <= 0:
            raise ValueError("Every Kubernetes LIST rule requires a positive maximum")
        if rule["evidence_policy_grants_mutation_authority"] is not False:
            raise ValueError("Evidence policy cannot grant mutation authority")
    for field_path in policy["kubernetes_evidence_surface"]["typed_api_status_projection"]["projection_paths"]:
        validate_field_path(field_path)
    projection = policy["service_restoration_projection"]
    if projection["projection_class"] != "SERVICE_RESTORATION_BODY_V1" or not projection["preserve_every_other_field_and_value"]:
        raise ValueError("Service restoration projection differs")
    expected_stripped = [[token.replace("*", "[]") for token in value.split("/") if token] for value in profile["service_normalization"]["stripped_fields"]]
    if projection["exact_stripped_field_token_paths"] != expected_stripped:
        raise ValueError("Service stripped-field tokens differ from the execution profile")
    mappings = policy["responsibility_provenance"]
    focused_expected = {"SREMUT_RUNNER", "KUBERNETES_API_PROJECTOR", "CHALLENGE_EXECUTOR", "WORKLOAD_HISTORY_SERIALIZER", "WORKLOAD_RESULT_PARSER", "MUTATION_COORDINATOR", "TERMINAL_SEALER", "ORIGINAL_ORACLE_ADAPTER", "ADJUDICATOR"}
    if {mapping["focused_identity"] for mapping in mappings} != focused_expected:
        raise ValueError("Focused producer mapping is incomplete")
    generated = {identity for mapping in mappings for identity in mapping["generated_identities"]}
    if generated != set(PRODUCERS):
        raise ValueError("Every generated producer must have a focused source mapping")
    produced = [role for mapping in mappings for role in mapping["evidence_roles_produced"]]
    if sorted(produced) != sorted(ROLE_NAMES) or len(produced) != len(set(produced)):
        raise ValueError("Every evidence role must be assigned exactly once")
    for mapping in mappings:
        for role in mapping["evidence_roles_produced"]:
            if policy["roles"][role]["producer"] not in mapping["generated_identities"]:
                raise ValueError("Responsibility mapping assigns a role to the wrong producer")
    covered_consumers = {consumer for mapping in mappings for consumer in mapping["consumers_served"]}
    if covered_consumers != set(CONSUMERS):
        raise ValueError("Responsibility mapping does not cover every consumer")
    if sum(mapping["terminalization_authority"] for mapping in mappings) != 1 or next(mapping for mapping in mappings if mapping["terminalization_authority"])["generated_identities"] != ["TERMINALIZER"]:
        raise ValueError("TERMINALIZER must hold the sole terminalization authority")
    projector = next(mapping for mapping in mappings if mapping["focused_identity"] == "KUBERNETES_API_PROJECTOR")
    oracle = next(mapping for mapping in mappings if mapping["focused_identity"] == "ORIGINAL_ORACLE_ADAPTER")
    if projector["mutation_authority"] or oracle["mutation_authority"] or oracle["kubernetes_request_authority"]:
        raise ValueError("Evidence production must not merge mutation authority")


def exact_instance_schema(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return {
            "type": "object",
            "additionalProperties": False,
            "required": list(value),
            "properties": {key: exact_instance_schema(child) for key, child in value.items()},
        }
    if isinstance(value, list):
        schema = {
            "type": "array",
            "minItems": len(value),
            "maxItems": len(value),
            "items": False,
        }
        if value:
            schema["prefixItems"] = [exact_instance_schema(child) for child in value]
        return schema
    if isinstance(value, bool):
        return {"type": "boolean", "const": value}
    if isinstance(value, int):
        return {"type": "integer", "const": value}
    if value is None:
        return {"type": "null"}
    return {"type": "string", "const": value}


def object_schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "additionalProperties": False, "required": list(properties) if required is None else required, "properties": properties}


def evidence_ref_schema(policy: dict[str, Any], payload: bool, role: str) -> dict[str, Any]:
    spec = policy["roles"][role]
    props: dict[str, Any] = {
        "document_type": {"type": "string", "const": "PAYLOAD_EVIDENCE_REF_V1" if payload else "DESCRIPTOR_EVIDENCE_REF_V1"},
        "schema_version": {"type": "integer", "const": 1},
        "evidence_id": {"type": "string", "pattern": EVIDENCE_ID_RE.pattern},
        "role": {"type": "string", "const": role},
        "producer": {"type": "string", "const": spec["producer"]},
        "source_kind": {"type": "string", "const": spec["source_kind"]},
        "media_type": {"type": "string", "const": spec["media_type"]},
        "storage_class": {"type": "string", "const": spec["storage_class"]},
        "descriptor_sha256": {"type": "string", "pattern": SHA256_RE.pattern},
        "descriptor_size_bytes": {"type": "integer", "minimum": 1, "maximum": spec["maximum_bytes"]},
        "descriptor_relative_path": {"type": "string", "pattern": r"^descriptors/sha256/[0-9a-f]{2}/[0-9a-f]{64}\.json$"},
        "redaction_status": {"type": "string", "const": "NOT_REDACTED"},
    }
    if payload:
        props.update({
            "payload_sha256": {"type": "string", "pattern": SHA256_RE.pattern},
            "payload_size_bytes": {"type": "integer", "minimum": 0, "maximum": spec["maximum_bytes"]},
            "payload_relative_path": {"type": "string", "pattern": r"^objects/sha256/[0-9a-f]{2}/[0-9a-f]{64}$"},
        })
    if role == "kubernetes_object_projection":
        props["projection_class"] = {"type": "string", "enum": list(PROJECTION_CLASSES)}
    schema = object_schema(props)
    if payload:
        zero_constraint = {
            "if": {"properties": {"payload_size_bytes": {"const": 0}}, "required": ["payload_size_bytes"]},
            "then": {"properties": {"payload_sha256": {"const": ZERO_SHA256}}},
            "else": {"properties": {"payload_sha256": {"not": {"const": ZERO_SHA256}}}},
        }
        schema["allOf"] = [zero_constraint]
        if not spec["zero_byte_payload_allowed"]:
            schema["properties"]["payload_size_bytes"]["minimum"] = 1
    return schema


def canonical_value_schema() -> dict[str, Any]:
    return {
        "oneOf": [
            {"type": "null"}, {"type": "boolean"}, {"type": "integer"},
            {"type": "string", "maxLength": 262144},
            {"type": "array", "maxItems": 4096, "items": {"$ref": "#/$defs/canonical_value"}},
            {
                "type": "object", "propertyNames": {"type": "string", "minLength": 1, "maxLength": 253},
                "maxProperties": 4096, "additionalProperties": {"$ref": "#/$defs/canonical_value"},
                "x-bounded-dynamic-map": True,
            },
        ]
    }


def metadata_value_schema(name: str) -> dict[str, Any]:
    if name == "schema_version":
        return {"type": "integer", "const": 1}
    if name == "evidence_id":
        return {"type": "string", "pattern": EVIDENCE_ID_RE.pattern}
    if name == "run_id":
        return {"type": "string", "pattern": RUN_ID_RE.pattern}
    if name == "attempt_id":
        return {"type": "string", "pattern": ATTEMPT_ID_RE.pattern}
    if name == "producer":
        return {"type": "string", "enum": list(PRODUCERS)}
    if name == "source_kind":
        return {"type": "string", "enum": sorted(set(ROLE_SOURCE_KINDS.values()))}
    if name in ("created_utc", "capture_timestamp", "dispatch_start_utc", "dispatch_finish_utc", "utc_wall_time"):
        return {"type": "string", "pattern": UTC_RE.pattern}
    if name in ("monotonic_ns", "monotonic_time", "observed_size", "object_count", "complete_entry_count", "request_count", "fresh_request_count", "failure_marker_count", "raw_log_byte_length", "container_restart_count", "operation_ordinal", "repetition"):
        return {"type": "integer", "minimum": 0}
    if name == "boot_identity":
        return {"type": "string", "pattern": UUID_RE.pattern}
    if name == "redaction_status":
        return {"type": "string", "const": "NOT_REDACTED"}
    if name == "projection_class":
        return {"type": "string", "enum": list(PROJECTION_CLASSES)}
    if name == "entry_time_ieee754_binary64_hex":
        return {"type": "array", "maxItems": 4096, "items": {"type": "string", "pattern": BINARY64_RE.pattern}}
    if name == "workload_window":
        return {"$ref": "#/$defs/workload_window_descriptor_identity"}
    if name == "workload_window_adjudication_identity":
        return {"$ref": "#/$defs/workload_window_identity"}
    if name.endswith("_sha256") or name.endswith("_evidence_sha256"):
        return {"type": "string", "pattern": SHA256_RE.pattern}
    if name.endswith("_reference") or name.endswith("_references"):
        return {"$ref": "#/$defs/evidence_reference_union"}
    return {"$ref": "#/$defs/canonical_value"}


def descriptor_schema(policy: dict[str, Any], role: str) -> dict[str, Any]:
    spec = policy["roles"][role]
    payload = spec["storage_class"] == "PAYLOAD_WITH_DESCRIPTOR"
    base_metadata = list(dict.fromkeys(spec["required_metadata"]))
    conditional_metadata = spec.get("conditional_required_metadata", [])
    metadata = list(dict.fromkeys(base_metadata + conditional_metadata))
    props = {name: metadata_value_schema(name) for name in metadata}
    props.update({
        "document_type": {"type": "string", "const": "PAYLOAD_EVIDENCE_DESCRIPTOR_V1" if payload else "DESCRIPTOR_EVIDENCE_DESCRIPTOR_V1"},
        "role": {"type": "string", "const": role},
        "producer": {"type": "string", "const": spec["producer"]},
        "source_kind": {"type": "string", "const": spec["source_kind"]},
        "media_type": {"type": "string", "const": spec["media_type"]},
        "storage_class": {"type": "string", "const": spec["storage_class"]},
    })
    if payload:
        props.update({
            "payload_sha256": {"type": "string", "pattern": SHA256_RE.pattern},
            "payload_size_bytes": {"type": "integer", "minimum": 0, "maximum": spec["maximum_bytes"]},
            "payload_relative_path": {"type": "string", "pattern": r"^objects/sha256/[0-9a-f]{2}/[0-9a-f]{64}$"},
        })
    required = list(props)
    if role in ("mutation_intent", "mutation_receipt", "kubernetes_object_projection"):
        required = [name for name in required if name not in conditional_metadata]
    result = object_schema(props, required)
    if payload:
        result["allOf"] = [{
            "if": {"properties": {"payload_size_bytes": {"const": 0}}, "required": ["payload_size_bytes"]},
            "then": {"properties": {"payload_sha256": {"const": ZERO_SHA256}}},
            "else": {"properties": {"payload_sha256": {"not": {"const": ZERO_SHA256}}}},
        }]
        if not spec["zero_byte_payload_allowed"]:
            result["properties"]["payload_size_bytes"]["minimum"] = 1
    return result


def workload_window_schema() -> dict[str, Any]:
    return object_schema({
        "phase": {"type": "string", "enum": list(WORKLOAD_WINDOWS)},
        "ordinal": {"type": "integer", "enum": list(WORKLOAD_WINDOWS.values())},
        "run_id": {"type": "string", "pattern": RUN_ID_RE.pattern},
        "attempt_id": {"type": "string", "pattern": ATTEMPT_ID_RE.pattern},
        "mutant_id": {"type": "string", "enum": ["MS-M01", "MS-M02", "MS-M03"]},
        "repetition": {"type": "integer", "minimum": 1, "maximum": 3},
        "boundary_reference": {"$ref": "#/$defs/descriptor_ref_workload_boundary"},
        "raw_log_reference": {"$ref": "#/$defs/payload_ref_workload_log_bytes"},
        "parse_result_reference": {"oneOf": [{"type": "null"}, {"$ref": "#/$defs/payload_ref_workload_parse_result"}]},
    })


def service_create_body_schema() -> dict[str, Any]:
    string_map = {
        "type": "object", "maxProperties": 256,
        "propertyNames": {"type": "string", "pattern": r"^(?:[A-Za-z0-9](?:[-A-Za-z0-9_.]{0,251}[A-Za-z0-9])?/)?[A-Za-z0-9](?:[-A-Za-z0-9_.]{0,61}[A-Za-z0-9])?$"},
        "additionalProperties": {"type": "string", "maxLength": 262144},
        "x-bounded-dynamic-map": True,
    }
    owner = object_schema({
        "apiVersion": {"type": "string", "minLength": 1, "maxLength": 253},
        "kind": {"type": "string", "minLength": 1, "maxLength": 63},
        "name": {"type": "string", "minLength": 1, "maxLength": 253},
        "uid": {"type": "string", "minLength": 1, "maxLength": 128},
        "controller": {"type": "boolean"},
        "blockOwnerDeletion": {"type": "boolean"},
    }, ["apiVersion", "kind", "name", "uid"])
    metadata = object_schema({
        "name": {"type": "string", "const": "user-service"},
        "namespace": {"type": "string", "const": "social-network"},
        "generateName": {"type": "string", "maxLength": 253},
        "labels": deepcopy(string_map), "annotations": deepcopy(string_map),
        "finalizers": {"type": "array", "maxItems": 64, "uniqueItems": True, "items": {"type": "string", "maxLength": 253}},
        "ownerReferences": {"type": "array", "maxItems": 64, "items": owner},
    }, ["name", "namespace"])
    port = object_schema({
        "name": {"type": "string", "maxLength": 63}, "protocol": {"type": "string", "enum": ["TCP", "UDP", "SCTP"]},
        "appProtocol": {"type": "string", "maxLength": 253}, "port": {"type": "integer", "minimum": 1, "maximum": 65535},
        "targetPort": {"oneOf": [{"type": "integer", "minimum": 1, "maximum": 65535}, {"type": "string", "minLength": 1, "maxLength": 63}]},
        "nodePort": {"type": "integer", "minimum": 1, "maximum": 65535},
    }, ["port"])
    client_ip = object_schema({"timeoutSeconds": {"type": "integer", "minimum": 1, "maximum": 86400}}, ["timeoutSeconds"])
    session_config = object_schema({"clientIP": client_ip}, ["clientIP"])
    spec = object_schema({
        "allocateLoadBalancerNodePorts": {"type": "boolean"},
        "clusterIP": {"type": "string", "maxLength": 253},
        "clusterIPs": {"type": "array", "maxItems": 2, "items": {"type": "string", "maxLength": 253}},
        "externalIPs": {"type": "array", "maxItems": 64, "items": {"type": "string", "maxLength": 253}},
        "externalName": {"type": "string", "maxLength": 253},
        "externalTrafficPolicy": {"type": "string", "enum": ["Cluster", "Local"]},
        "healthCheckNodePort": {"type": "integer", "minimum": 1, "maximum": 65535},
        "internalTrafficPolicy": {"type": "string", "enum": ["Cluster", "Local"]},
        "ipFamilies": {"type": "array", "maxItems": 2, "items": {"type": "string", "enum": ["IPv4", "IPv6"]}},
        "ipFamilyPolicy": {"type": "string", "enum": ["SingleStack", "PreferDualStack", "RequireDualStack"]},
        "loadBalancerClass": {"type": "string", "maxLength": 253},
        "loadBalancerIP": {"type": "string", "maxLength": 253},
        "loadBalancerSourceRanges": {"type": "array", "maxItems": 256, "items": {"type": "string", "maxLength": 64}},
        "ports": {"type": "array", "minItems": 1, "maxItems": 64, "items": port},
        "publishNotReadyAddresses": {"type": "boolean"},
        "selector": deepcopy(string_map),
        "sessionAffinity": {"type": "string", "enum": ["None", "ClientIP"]},
        "sessionAffinityConfig": session_config,
        "trafficDistribution": {"type": "string", "enum": ["PreferClose", "PreferSameZone", "PreferSameNode"]},
        "type": {"type": "string", "enum": ["ClusterIP", "ExternalName", "LoadBalancer", "NodePort"]},
    }, ["ports"])
    return object_schema({
        "apiVersion": {"type": "string", "const": "v1"},
        "kind": {"type": "string", "const": "Service"},
        "metadata": metadata,
        "spec": spec,
    })


def runtime_schemas(policy: dict[str, Any]) -> dict[str, Any]:
    hash_schema = {"type": "string", "pattern": SHA256_RE.pattern}
    common = {
        "document_type": {"type": "string", "const": "DESCRIPTOR_EVIDENCE_DESCRIPTOR_V1"},
        "schema_version": {"type": "integer", "const": 1},
        "evidence_id": {"type": "string", "pattern": EVIDENCE_ID_RE.pattern},
        "role": {"type": "string", "const": "run_identity"},
        "producer": {"type": "string", "const": "RUNNER_IDENTITY_RECORDER"},
        "source_kind": {"type": "string", "const": "LOCAL_IDENTITY"},
        "media_type": {"type": "string", "const": "application/json"},
        "storage_class": {"type": "string", "const": "DESCRIPTOR_ONLY"},
        "run_id": {"type": "string", "pattern": RUN_ID_RE.pattern},
        "attempt_id": {"type": "string", "pattern": ATTEMPT_ID_RE.pattern},
        "created_utc": {"type": "string", "pattern": UTC_RE.pattern},
        "monotonic_ns": {"type": "integer", "minimum": 0},
        "boot_identity": {"type": "string", "pattern": UUID_RE.pattern},
        "redaction_status": {"type": "string", "const": "NOT_REDACTED"},
    }
    source_hashes = object_schema({alias: deepcopy(hash_schema) for alias in SOURCE_ALIASES})
    release = object_schema({
        "binding_mode": {"type": "string", "const": "SEPARATELY_FROZEN_EXECUTION_RELEASE"},
        "release_artifact": {"type": "string", "const": "RUNNER_BUNDLE_SHA256SUMS"},
        "manifest_sha256": deepcopy(hash_schema), "bundle_sha256": deepcopy(hash_schema),
        "git_commit": {"type": "string", "pattern": SHA1_RE.pattern}, "git_tree": {"type": "string", "pattern": SHA1_RE.pattern},
        "annotated_tag_name": {"type": "string", "minLength": 1, "maxLength": 128}, "annotated_tag_object": {"type": "string", "pattern": SHA1_RE.pattern},
        "pyproject_sha256": deepcopy(hash_schema), "uv_lock_sha256": deepcopy(hash_schema),
    })
    start = object_schema({
        **deepcopy(common), "phase": {"type": "string", "const": "START"},
        "runtime_identity": object_schema({"python_version": {"type": "string", "const": "3.12.3"}, "pyyaml_version": {"type": "string", "const": "6.0.2"}, "kubernetes_version": {"type": "string", "const": "32.0.1"}, "jsonschema_version": {"type": "string", "const": "4.23.0"}, "uv_version": {"type": "string", "const": "0.12.5"}, "kubectl_version": {"type": "string", "const": "v1.32.0"}}),
        "contract_binding": object_schema({"tag_object": {"const": CONTRACT_TAG_OBJECT}, "commit": {"const": CONTRACT_COMMIT}, "tree": {"const": CONTRACT_TREE}, "sha256": {"const": CONTRACT_SHA256}}),
        "execution_profile_binding": object_schema({"tag_object": {"const": PROFILE_TAG_OBJECT}, "commit": {"const": PROFILE_COMMIT}, "tree": {"const": PROFILE_TREE}, "sha256": {"const": PROFILE_SHA256}}),
        "runner_release_binding": release,
        "pyproject_sha256": {"const": PYPROJECT_SHA256}, "uv_lock_sha256": {"const": UV_LOCK_SHA256},
        "source_alias_sha256": source_hashes, "kubeconfig_content_sha256": deepcopy(hash_schema),
        "kubectl_default_cache_before_sha256": deepcopy(hash_schema),
    })
    start_ref = {"$ref": "#/$defs/descriptor_ref_run_identity"}
    terminal = object_schema({
        **deepcopy(common), "phase": {"type": "string", "const": "TERMINAL"},
        "start_identity_reference": start_ref, "terminal_release_identity": release,
        "terminal_source_alias_sha256": source_hashes,
        "kubectl_default_cache_before_sha256": deepcopy(hash_schema), "kubectl_default_cache_after_sha256": deepcopy(hash_schema),
        "terminal_outcome": {"type": "string", "enum": list(TERMINAL_OUTCOMES)},
    })
    time_item = {"type": "string", "pattern": BINARY64_RE.pattern}
    workload = object_schema({
        "schema_version": {"type": "integer", "const": 1}, "complete_entry_count": {"type": "integer", "minimum": 0},
        "entry_time_ieee754_binary64_hex": {"type": "array", "items": time_item, "maxItems": 4096},
        "raw_log_reference": {"$ref": "#/$defs/payload_ref_workload_log_bytes"},
    })
    journal = object_schema({
        "document_type": {"type": "string", "const": "JOURNAL_RECORD_V1"}, "schema_version": {"type": "integer", "const": 1},
        "journal_record_type": {"type": "string", "const": "STATE_TRANSITION"},
        "sequence_number": {"type": "integer", "minimum": 0}, "previous_entry_sha256": deepcopy(hash_schema),
        "canonical_current_entry_sha256": deepcopy(hash_schema), "run_id": {"type": "string", "pattern": RUN_ID_RE.pattern},
        "attempt_id": {"type": "string", "pattern": ATTEMPT_ID_RE.pattern}, "transition": {"type": "string", "minLength": 1, "maxLength": 16384},
        "referenced_intent_receipt_and_adjudication_sha256": {"type": "array", "items": deepcopy(hash_schema), "maxItems": 4096},
        "referenced_descriptor_sha256": {"type": "array", "items": deepcopy(hash_schema), "maxItems": 4096},
        "referenced_payload_sha256": {"type": "array", "items": deepcopy(hash_schema), "maxItems": 4096},
        "utc_time": {"type": "string", "pattern": UTC_RE.pattern}, "monotonic_ns": {"type": "integer", "minimum": 0},
        "boot_identity": {"type": "string", "pattern": UUID_RE.pattern},
    }, ["document_type", "schema_version", "journal_record_type", "sequence_number", "previous_entry_sha256", "canonical_current_entry_sha256", "run_id", "attempt_id", "transition", "referenced_intent_receipt_and_adjudication_sha256", "utc_time", "monotonic_ns", "boot_identity"])
    rejection = object_schema({
        "document_type": {"type": "string", "const": "JOURNAL_RECORD_V1"}, "schema_version": {"type": "integer", "const": 1},
        "journal_record_type": {"type": "string", "const": "CAPTURE_REJECTED"},
        "sequence_number": {"type": "integer", "minimum": 0}, "previous_entry_sha256": deepcopy(hash_schema),
        "canonical_current_entry_sha256": deepcopy(hash_schema), "run_id": {"type": "string", "pattern": RUN_ID_RE.pattern},
        "attempt_id": {"type": "string", "pattern": ATTEMPT_ID_RE.pattern}, "intended_role": {"type": "string", "enum": list(ROLE_NAMES)},
        "rejection_code": {"type": "string", "pattern": r"^CAPTURE_REJECTED_[A-Z_]+$"}, "detector_id": {"type": "string", "pattern": r"^SENSITIVE_[A-Z_]+$"},
        "source_kind": {"type": "string", "enum": sorted(set(ROLE_SOURCE_KINDS.values()))}, "observed_size": {"type": "integer", "minimum": 0},
        "created_utc": {"type": "string", "pattern": UTC_RE.pattern}, "monotonic_ns": {"type": "integer", "minimum": 0},
        "boot_identity": {"type": "string", "pattern": UUID_RE.pattern},
    })
    defs: dict[str, Any] = {
        "canonical_value": canonical_value_schema(),
        "workload_window_identity": workload_window_schema(),
        "run_identity_start": start,
        "run_identity_terminal": terminal,
        "journal_state_transition": journal,
        "journal_capture_rejected": rejection,
        "journal_record": {"oneOf": [{"$ref": "#/$defs/journal_state_transition"}, {"$ref": "#/$defs/journal_capture_rejected"}]},
    }
    payload_descriptor_refs = []
    descriptor_descriptor_refs = []
    payload_ref_refs = []
    descriptor_ref_refs = []
    for role in ROLE_NAMES[:-1]:
        spec = policy["roles"][role]
        payload = spec["storage_class"] == "PAYLOAD_WITH_DESCRIPTOR"
        descriptor_name = ("payload_descriptor_" if payload else "descriptor_descriptor_") + role
        reference_name = ("payload_ref_" if payload else "descriptor_ref_") + role
        if role == "run_identity":
            defs[descriptor_name] = {"oneOf": [{"$ref": "#/$defs/run_identity_start"}, {"$ref": "#/$defs/run_identity_terminal"}]}
        else:
            defs[descriptor_name] = descriptor_schema(policy, role)
        defs[reference_name] = evidence_ref_schema(policy, payload, role)
        (payload_descriptor_refs if payload else descriptor_descriptor_refs).append({"$ref": f"#/$defs/{descriptor_name}"})
        (payload_ref_refs if payload else descriptor_ref_refs).append({"$ref": f"#/$defs/{reference_name}"})
    defs["payload_evidence_descriptor"] = {"oneOf": payload_descriptor_refs}
    defs["descriptor_evidence_descriptor"] = {"oneOf": descriptor_descriptor_refs}
    defs["payload_evidence_ref"] = {"oneOf": payload_ref_refs}
    defs["descriptor_evidence_ref"] = {"oneOf": descriptor_ref_refs}
    defs["evidence_reference_union"] = {"oneOf": [{"$ref": "#/$defs/payload_evidence_ref"}, {"$ref": "#/$defs/descriptor_evidence_ref"}]}
    terminal_manifest = object_schema({
        "document_type": {"type": "string", "const": "TERMINAL_MANIFEST_V1"}, "schema_version": {"type": "integer", "const": 1},
        "run_id": {"type": "string", "pattern": RUN_ID_RE.pattern}, "attempt_id": {"type": "string", "pattern": ATTEMPT_ID_RE.pattern},
        "terminal_outcome": {"type": "string", "enum": list(TERMINAL_OUTCOMES)},
        "terminal_cache_snapshot_monotonic_ns": {"type": "integer", "minimum": 0}, "installed_monotonic_ns": {"type": "integer", "minimum": 0},
        "manifest_relative_path": {"type": "string", "pattern": r"^manifests/[A-Za-z0-9._/-]+$"},
    })
    defs["terminal_manifest_record"] = terminal_manifest
    operation = object_schema({
        "operation_id": {"type": "string", "minLength": 1, "maxLength": 128},
        "created_monotonic_ns": {"type": "integer", "minimum": 0},
        "intent_reference": {"$ref": "#/$defs/descriptor_ref_mutation_intent"},
        "receipt_reference": {"$ref": "#/$defs/descriptor_ref_mutation_receipt"},
    })
    defs["attempt_validation_envelope"] = object_schema({
        "document_type": {"type": "string", "const": "ATTEMPT_VALIDATION_ENVELOPE_V1"}, "schema_version": {"type": "integer", "const": 1},
        "run_id": {"type": "string", "pattern": RUN_ID_RE.pattern}, "attempt_id": {"type": "string", "pattern": ATTEMPT_ID_RE.pattern},
        "terminal_outcome": {"type": "string", "enum": list(TERMINAL_OUTCOMES)},
        "run_identities": {"type": "array", "minItems": 2, "maxItems": 2, "prefixItems": [{"$ref": "#/$defs/run_identity_start"}, {"$ref": "#/$defs/run_identity_terminal"}], "items": False},
        "operations": {"type": "array", "maxItems": 4096, "items": operation},
        "journal_records": {"type": "array", "minItems": 1, "maxItems": 16384, "items": {"$ref": "#/$defs/journal_record"}},
        "adjudication_references": {"type": "array", "minItems": 1, "maxItems": 4096, "items": {"$ref": "#/$defs/descriptor_ref_adjudication"}},
        "raw_evidence_references": {"type": "array", "minItems": 1, "maxItems": 4096, "items": {"$ref": "#/$defs/payload_evidence_ref"}},
        "terminal_manifest": {"$ref": "#/$defs/terminal_manifest_record"},
    })
    defs["terminal_global_stop"] = object_schema({
        "document_type": {"type": "string", "const": "TERMINAL_GLOBAL_STOP_V1"}, "schema_version": {"type": "integer", "const": 1},
        "run_id": {"type": "string", "pattern": RUN_ID_RE.pattern}, "attempt_id": {"type": "string", "pattern": ATTEMPT_ID_RE.pattern},
        "terminal_outcome": {"type": "string", "const": "RESTORATION_BLOCKED"}, "reason_code": {"type": "string", "minLength": 1, "maxLength": 128},
        "adjudication_reference": {"$ref": "#/$defs/descriptor_ref_adjudication"}, "created_utc": {"type": "string", "pattern": UTC_RE.pattern},
        "monotonic_ns": {"type": "integer", "minimum": 0}, "boot_identity": {"type": "string", "pattern": UUID_RE.pattern},
    })
    defs["service_create_body"] = service_create_body_schema()
    defs["service_restoration_body"] = object_schema({
        "document_type": {"type": "string", "const": "SERVICE_RESTORATION_BODY_V1"}, "schema_version": {"type": "integer", "const": 1},
        "projection_class": {"type": "string", "const": "SERVICE_RESTORATION_BODY_V1"},
        "derivation_algorithm": {"type": "string", "const": "KUBERNETES_SERVICE_CREATE_BODY_NORMALIZATION_V1"},
        "source_service_uid": {"type": "string", "minLength": 1, "maxLength": 128}, "source_service_resource_version": {"type": "string", "minLength": 1, "maxLength": 128},
        "exact_stripped_field_token_paths": exact_instance_schema(policy["service_restoration_projection"]["exact_stripped_field_token_paths"]),
        "normalized_body_sha256": deepcopy(hash_schema), "canonical_json_identity": deepcopy(hash_schema),
        "capture_timestamp": {"type": "string", "pattern": UTC_RE.pattern}, "source_request_reference": {"$ref": "#/$defs/descriptor_ref_kubernetes_request_identity"},
        "source_service_reference": {"$ref": "#/$defs/payload_ref_kubernetes_object_projection"},
        "normalized_service_create_body": {"$ref": "#/$defs/service_create_body"},
    })
    request_options = {
        "type": "object", "maxProperties": 32, "propertyNames": {"type": "string", "pattern": r"^[a-z][a-z0-9_]{0,63}$"},
        "additionalProperties": {"oneOf": [{"type": "null"}, {"type": "boolean"}, {"type": "integer"}, {"type": "string", "maxLength": 253}]},
        "x-bounded-dynamic-map": True,
    }
    defs["kubernetes_request"] = object_schema({
        "document_type": {"type": "string", "const": "KUBERNETES_REQUEST_V1"}, "schema_version": {"type": "integer", "const": 1},
        "request_rule_id": {"type": "string", "minLength": 1, "maxLength": 128}, "context": {"type": "string", "const": "kind-kind"},
        "api_group": {"type": "string", "maxLength": 253}, "api_version": {"type": "string", "minLength": 1, "maxLength": 63},
        "resource": {"type": "string", "minLength": 1, "maxLength": 63}, "response_kind": {"type": "string", "minLength": 1, "maxLength": 63},
        "subresource": {"type": "string", "maxLength": 63}, "operation": {"type": "string", "enum": ["GET", "LIST", "CREATE", "DELETE", "CONNECT_GET"]},
        "namespace": {"type": "string", "minLength": 1, "maxLength": 253}, "name": {"type": "string", "maxLength": 253},
        "label_selector": {"type": "string", "maxLength": 1024}, "field_selector": {"type": "string", "maxLength": 1024},
        "request_options": request_options, "purpose": {"type": "string", "minLength": 1, "maxLength": 128},
        "consumer": {"type": "string", "enum": list(CONSUMERS)}, "projection_class": {"type": "string", "minLength": 1, "maxLength": 128},
        "captured_object_name": {"oneOf": [{"type": "null"}, {"type": "string", "minLength": 1, "maxLength": 253}]},
        "captured_object_uid": {"oneOf": [{"type": "null"}, {"type": "string", "minLength": 1, "maxLength": 128}]},
        "captured_object_resource_version": {"oneOf": [{"type": "null"}, {"type": "string", "minLength": 1, "maxLength": 128}]},
        "captured_object_reference": {"oneOf": [{"type": "null"}, {"$ref": "#/$defs/payload_ref_kubernetes_object_projection"}]},
        "uid_precondition": {"oneOf": [{"type": "null"}, {"type": "string", "minLength": 1, "maxLength": 128}]},
        "authorized_operation_kind": {"oneOf": [{"type": "null"}, {"type": "string", "enum": ["INITIAL_USER_SERVICE_DELETION", "INITIAL_CAPTURED_POD_RECYCLE_DELETE", "MUTANT_SERVICE_CREATION", "CHALLENGE_POD_CREATION", "REPLACEMENT_POD_DELETION", "CHALLENGE_POD_DELETION", "MUTANT_SERVICE_DELETION", "RESTORED_SERVICE_CREATION", "RECOVERY_POD_RECYCLE_DELETE"]}]},
        "intent_reference": {"oneOf": [{"type": "null"}, {"$ref": "#/$defs/descriptor_ref_mutation_intent"}]},
    })
    list_metadata = object_schema({
        "resourceVersion": {"type": "string", "maxLength": 128}, "continue": {"type": "string", "maxLength": 4096},
        "remainingItemCount": {"type": "integer", "minimum": 0},
    }, [])
    projection_item = object_schema({
        "apiVersion": {"type": "string", "minLength": 1, "maxLength": 253}, "kind": {"type": "string", "minLength": 1, "maxLength": 63},
        "namespace": {"type": "string", "minLength": 1, "maxLength": 253}, "name": {"type": "string", "minLength": 1, "maxLength": 253},
        "projection_class": {"type": "string", "const": "KUBERNETES_OBJECT_PROJECTION_V1"}, "projected_fields": {"$ref": "#/$defs/canonical_value"},
    })
    defs["kubernetes_projection_item"] = projection_item
    defs["kubernetes_list_response"] = object_schema({
        "document_type": {"type": "string", "const": "KUBERNETES_LIST_RESPONSE_V1"}, "schema_version": {"type": "integer", "const": 1},
        "request": {"$ref": "#/$defs/kubernetes_request"}, "list_metadata": list_metadata,
        "items": {"type": "array", "maxItems": 256, "items": {"$ref": "#/$defs/kubernetes_projection_item"}},
    })
    return defs


def build_schema(policy: dict[str, Any]) -> dict[str, Any]:
    runtime = runtime_schemas(policy)
    runtime["evidence_policy_document"] = exact_instance_schema(policy)
    roots = (
        "evidence_policy_document", "payload_evidence_descriptor", "descriptor_evidence_descriptor",
        "payload_evidence_ref", "descriptor_evidence_ref", "journal_record",
        "attempt_validation_envelope", "terminal_global_stop", "service_restoration_body",
        "kubernetes_request", "kubernetes_list_response",
    )
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://sremut.local/schemas/evidence-capture-policy-v1.schema.json",
        "title": "SREMut missing-service evidence-capture policy v1",
        "x-runtime-validator-hooks": list(RUNTIME_VALIDATOR_HOOKS),
        "oneOf": [{"$ref": f"#/$defs/{name}"} for name in roots],
        "$defs": runtime,
    }
    assert_closed_object_schemas(schema)
    Draft202012Validator.check_schema(schema)
    assert_all_defs_reachable(schema)
    return schema


def assert_all_defs_reachable(schema: dict[str, Any]) -> None:
    definitions = schema["$defs"]
    pending: list[Any] = [schema["oneOf"]]
    reached: set[str] = set()
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            reference = value.get("$ref")
            if isinstance(reference, str) and reference.startswith("#/$defs/"):
                name = reference.removeprefix("#/$defs/")
                if name not in definitions:
                    raise ValueError(f"Unknown local definition reference: {name}")
                if name not in reached:
                    reached.add(name)
                    pending.append(definitions[name])
            pending.extend(child for key, child in value.items() if key != "$ref")
        elif isinstance(value, list):
            pending.extend(value)
    unreachable = set(definitions) - reached
    if unreachable:
        raise ValueError("Unreachable runtime schema definitions: " + repr(sorted(unreachable)))


def assert_closed_object_schemas(value: Any, location: str = "$") -> None:
    if isinstance(value, dict):
        if value.get("type") == "object" and value.get("additionalProperties") is not False and value.get("x-bounded-dynamic-map") is not True:
            raise ValueError(f"Open object schema at {location}")
        for key, child in value.items():
            assert_closed_object_schemas(child, f"{location}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            assert_closed_object_schemas(child, f"{location}[{index}]")


def render_policy(policy: dict[str, Any]) -> bytes:
    return yaml.safe_dump(policy, sort_keys=False, allow_unicode=True, width=100).encode("utf-8")


def render_schema(schema: dict[str, Any]) -> bytes:
    return (json.dumps(schema, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def render_manifest(layout: Layout, policy_bytes: bytes, schema_bytes: bytes) -> bytes:
    generator_hash = sha256(layout.generator)
    rows = (
        (generator_hash, GENERATOR_REL),
        (sha256_bytes(policy_bytes), POLICY_REL),
        (sha256_bytes(schema_bytes), SCHEMA_REL),
    )
    return "".join(f"{digest}  {relative}\n" for digest, relative in rows).encode("utf-8")


def expected_artifacts(layout: Layout, frozen_at: str, contract: dict[str, Any], profile: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[Path, bytes]]:
    policy = build_policy(frozen_at, contract, profile)
    schema = build_schema(policy)
    policy_bytes = render_policy(policy)
    schema_bytes = render_schema(schema)
    manifest_bytes = render_manifest(layout, policy_bytes, schema_bytes)
    return policy, schema, {layout.policy: policy_bytes, layout.schema: schema_bytes, layout.manifest: manifest_bytes}


def relative_components(layout: Layout, path: Path) -> tuple[str, ...]:
    try:
        relative = path.relative_to(layout.root)
    except ValueError as error:
        raise RuntimeError(f"Path is outside transaction root: {path}") from error
    parts = relative.parts
    if not parts or any(part in ("", ".", "..") or "/" in part or "\\" in part for part in parts):
        raise RuntimeError(f"Unsafe transaction path: {path}")
    return parts


def open_verified_parent(layout: Layout, path: Path, *, create: bool) -> tuple[int, str]:
    """Open every parent component relative to a pre-opened no-follow root."""
    parts = relative_components(layout, path)
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    root_fd = os.open(layout.root, flags)
    current = os.dup(root_fd)
    os.close(root_fd)
    try:
        for component in parts[:-1]:
            try:
                child = os.open(component, flags, dir_fd=current)
            except FileNotFoundError:
                if not create:
                    raise
                os.mkdir(component, mode=0o755, dir_fd=current)
                os.fsync(current)
                child = os.open(component, flags, dir_fd=current)
            os.close(current)
            current = child
        return current, parts[-1]
    except Exception:
        os.close(current)
        raise


def safe_stat(layout: Layout, path: Path) -> os.stat_result | None:
    try:
        parent_fd, name = open_verified_parent(layout, path, create=False)
    except FileNotFoundError:
        return None
    try:
        try:
            return os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return None
    finally:
        os.close(parent_fd)


def safe_read_transaction_file(layout: Layout, path: Path) -> bytes:
    parent_fd, name = open_verified_parent(layout, path, create=False)
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(name, flags, dir_fd=parent_fd)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode):
                raise RuntimeError(f"Transaction path is not a regular file: {path}")
            chunks: list[bytes] = []
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                chunks.append(chunk)
            return b"".join(chunks)
        finally:
            os.close(descriptor)
    finally:
        os.close(parent_fd)


def safe_identity(layout: Layout, path: Path) -> str:
    info = safe_stat(layout, path)
    if info is None:
        return "ABSENT"
    if not stat.S_ISREG(info.st_mode):
        raise RuntimeError(f"Transaction target is not a regular file: {path}")
    return sha256_bytes(safe_read_transaction_file(layout, path))


def safe_write_temp(layout: Layout, destination: Path, data: bytes, transaction_id: str, role: str) -> Path:
    parent_fd, destination_name = open_verified_parent(layout, destination, create=True)
    token = secrets.token_hex(8)
    temporary_name = f".{destination_name}.{role}.{transaction_id}.{token}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(temporary_name, flags, 0o600, dir_fd=parent_fd)
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.fchmod(descriptor, 0o644)
        os.fsync(descriptor)
    finally:
        if "descriptor" in locals():
            os.close(descriptor)
        os.fsync(parent_fd)
        os.close(parent_fd)
    return destination.parent / temporary_name


def safe_replace(layout: Layout, source: Path, destination: Path) -> None:
    source_fd, source_name = open_verified_parent(layout, source, create=False)
    destination_fd, destination_name = open_verified_parent(layout, destination, create=True)
    try:
        source_info = os.stat(source_name, dir_fd=source_fd, follow_symlinks=False)
        if not stat.S_ISREG(source_info.st_mode):
            raise RuntimeError(f"Replacement source is not regular: {source}")
        try:
            target_info = os.stat(destination_name, dir_fd=destination_fd, follow_symlinks=False)
        except FileNotFoundError:
            target_info = None
        if target_info is not None and not stat.S_ISREG(target_info.st_mode):
            raise RuntimeError(f"Replacement target is not regular: {destination}")
        os.replace(source_name, destination_name, src_dir_fd=source_fd, dst_dir_fd=destination_fd)
        os.fsync(destination_fd)
    finally:
        os.close(source_fd)
        os.close(destination_fd)


def safe_unlink(layout: Layout, path: Path) -> None:
    parent_fd, name = open_verified_parent(layout, path, create=False)
    try:
        info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if not stat.S_ISREG(info.st_mode):
            raise RuntimeError(f"Refusing to unlink non-regular transaction path: {path}")
        os.unlink(name, dir_fd=parent_fd)
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)


def atomic_write_journal(layout: Layout, journal: dict[str, Any]) -> None:
    existing = safe_stat(layout, layout.journal)
    if existing is not None and not stat.S_ISREG(existing.st_mode):
        raise RecoveryBlocked("Transaction journal is a symlink")
    data = canonical_json_bytes(journal) + b"\n"
    temporary = safe_write_temp(layout, layout.journal, data, journal["transaction_id"], "journal")
    safe_replace(layout, temporary, layout.journal)


def update_phase(layout: Layout, journal: dict[str, Any], phase: str) -> None:
    journal["phase"] = phase
    atomic_write_journal(layout, journal)


def current_git_identity(root: Path) -> tuple[str, str]:
    return git(root, "rev-parse", "HEAD"), git(root, "rev-parse", "HEAD^{tree}")


PHASES = ("PREPARED", "POLICY_INSTALLED", "SCHEMA_INSTALLED", "MANIFEST_INSTALLED", "VERIFIED")


def install_transaction(layout: Layout, artifacts: dict[Path, bytes], mode: str, fail_after: str | None = None) -> None:
    if safe_stat(layout, layout.journal) is not None:
        raise RuntimeError("Unresolved evidence-policy transaction; explicit recovery required")
    for destination in artifacts:
        info = safe_stat(layout, destination)
        if info is not None and not stat.S_ISREG(info.st_mode):
            raise RuntimeError(f"Artifact target is not a regular file: {destination}")
    transaction_id = secrets.token_hex(16)
    head, tree = current_git_identity(layout.root)
    old = {destination: safe_identity(layout, destination) for destination in artifacts}
    temps: dict[Path, Path] = {}
    try:
        for destination, data in artifacts.items():
            role = re.sub(r"[^a-z0-9-]", "-", destination.stem.lower().replace(".", "-"))
            temps[destination] = safe_write_temp(layout, destination, data, transaction_id, role)
        journal = {
            "schema_version": 1,
            "transaction_id": transaction_id,
            "mode": mode,
            "phase": "PREPARED",
            "transaction_head": head,
            "transaction_tree": tree,
            "artifacts": [
                {
                    "path": destination.relative_to(layout.root).as_posix(),
                    "old_sha256_or_absent": old[destination],
                    "new_sha256": sha256_bytes(artifacts[destination]),
                    "temporary_path": temps[destination].relative_to(layout.root).as_posix(),
                }
                for destination in artifacts
            ],
            "individually_atomic_replacements_only": True,
        }
        atomic_write_journal(layout, journal)
        if fail_after == "PREPARED":
            raise InjectedCrash("PREPARED")
        for destination, phase in (
            (layout.policy, "POLICY_INSTALLED"),
            (layout.schema, "SCHEMA_INSTALLED"),
            (layout.manifest, "MANIFEST_INSTALLED"),
        ):
            safe_replace(layout, temps[destination], destination)
            update_phase(layout, journal, phase)
            if fail_after == phase:
                raise InjectedCrash(phase)
        for destination, expected in artifacts.items():
            if safe_read_transaction_file(layout, destination) != expected:
                raise RuntimeError(f"Installed artifact differs: {destination}")
        update_phase(layout, journal, "VERIFIED")
        if fail_after == "VERIFIED":
            raise InjectedCrash("VERIFIED")
        safe_unlink(layout, layout.journal)
    except InjectedCrash:
        raise
    except Exception:
        # Preserve journal and temporary state for explicit forensic recovery.
        raise


def parse_journal(layout: Layout) -> dict[str, Any]:
    raw = safe_read_transaction_file(layout, layout.journal)
    journal = json.loads(raw.decode("utf-8"))
    expected_keys = {
        "schema_version", "transaction_id", "mode", "phase", "transaction_head", "transaction_tree",
        "artifacts", "individually_atomic_replacements_only",
    }
    if not isinstance(journal, dict) or set(journal) != expected_keys:
        raise RecoveryBlocked("Transaction journal shape is not recognized")
    if journal["schema_version"] != 1 or not TRANSACTION_ID_RE.fullmatch(journal["transaction_id"]):
        raise RecoveryBlocked("Transaction journal identity is invalid")
    if journal["phase"] not in PHASES or journal["individually_atomic_replacements_only"] is not True:
        raise RecoveryBlocked("Transaction journal phase/claim is invalid")
    return journal


def validate_transaction_paths(layout: Layout, journal: dict[str, Any]) -> list[tuple[Path, Path, str, str]]:
    expected = [layout.policy, layout.schema, layout.manifest]
    rows = journal["artifacts"]
    if not isinstance(rows, list) or len(rows) != 3:
        raise RecoveryBlocked("Transaction artifact list differs")
    result: list[tuple[Path, Path, str, str]] = []
    for row, destination in zip(rows, expected, strict=True):
        if set(row) != {"path", "old_sha256_or_absent", "new_sha256", "temporary_path"}:
            raise RecoveryBlocked("Transaction artifact record differs")
        if row["path"] != destination.relative_to(layout.root).as_posix():
            raise RecoveryBlocked("Transaction artifact order/path differs")
        old = row["old_sha256_or_absent"]
        new = row["new_sha256"]
        if old != "ABSENT" and not SHA256_RE.fullmatch(old):
            raise RecoveryBlocked("Invalid old artifact identity")
        if not SHA256_RE.fullmatch(new):
            raise RecoveryBlocked("Invalid new artifact identity")
        temporary = layout.root / row["temporary_path"]
        if temporary.parent != destination.parent:
            raise RecoveryBlocked("Unsafe transaction temporary path")
        pattern = re.compile(rf"^\.{re.escape(destination.name)}\.[a-z0-9-]+\.{journal['transaction_id']}\.([0-9a-f]{{16}})\.tmp$")
        match = pattern.fullmatch(temporary.name)
        if match is None or not TEMP_TOKEN_RE.fullmatch(match.group(1)):
            raise RecoveryBlocked("Transaction temporary name differs")
        info = safe_stat(layout, temporary)
        if info is not None and not stat.S_ISREG(info.st_mode):
            raise RecoveryBlocked("Unsafe transaction temporary target")
        result.append((destination, temporary, old, new))
    return result


def recover_transaction(layout: Layout, artifacts: dict[Path, bytes]) -> str:
    if not path_lexists(layout.journal):
        raise RecoveryBlocked("No interrupted evidence-policy transaction exists")
    journal = parse_journal(layout)
    if current_git_identity(layout.root) != (journal["transaction_head"], journal["transaction_tree"]):
        raise RecoveryBlocked("HEAD/tree changed since transaction preparation")
    rows = validate_transaction_paths(layout, journal)
    for destination, temporary, old, new in rows:
        if sha256_bytes(artifacts[destination]) != new:
            raise RecoveryBlocked("Recorded transaction does not match canonical artifacts")
        observed = safe_identity(layout, destination)
        if observed not in (old, new):
            raise RecoveryBlocked(f"Forensic conflict at {destination}")
        if observed == old:
            if safe_identity(layout, temporary) != new:
                raise RecoveryBlocked(f"Missing/corrupt recovery temporary: {temporary}")
        elif safe_identity(layout, temporary) not in ("ABSENT", new):
            raise RecoveryBlocked(f"Unexpected recovery temporary content: {temporary}")
    # No mutation occurs until every target and temporary has passed validation.
    phases = ("POLICY_INSTALLED", "SCHEMA_INSTALLED", "MANIFEST_INSTALLED")
    for (destination, temporary, _old, new), phase in zip(rows, phases, strict=True):
        if safe_identity(layout, destination) != new:
            safe_replace(layout, temporary, destination)
        elif safe_identity(layout, temporary) != "ABSENT":
            safe_unlink(layout, temporary)
        update_phase(layout, journal, phase)
    for destination, expected in artifacts.items():
        if safe_read_transaction_file(layout, destination) != expected:
            raise RecoveryBlocked(f"Recovered artifact is not canonical: {destination}")
    update_phase(layout, journal, "VERIFIED")
    safe_unlink(layout, layout.journal)
    return "recovered exact canonical policy/schema/manifest set"


@contextmanager
def repository_lock(layout: Layout):
    descriptor = os.open(layout.root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0))
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def parse_installed_policy(layout: Layout) -> dict[str, Any]:
    policy = yaml.safe_load(safe_read_transaction_file(layout, layout.policy))
    if not isinstance(policy, dict):
        raise RuntimeError("Installed policy is not a mapping")
    return policy


def verify_exact_installed(layout: Layout, contract: dict[str, Any], profile: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    if safe_stat(layout, layout.journal) is not None:
        raise RuntimeError("Unresolved evidence-policy transaction; explicit recovery required")
    policy = parse_installed_policy(layout)
    frozen_at = policy.get("frozen_at")
    if not isinstance(frozen_at, str):
        raise RuntimeError("Installed policy lacks frozen_at")
    expected_policy, expected_schema, artifacts = expected_artifacts(layout, frozen_at, contract, profile)
    for destination, expected in artifacts.items():
        if safe_read_transaction_file(layout, destination) != expected:
            raise RuntimeError(f"Exact-byte check failed: {destination}")
    Draft202012Validator(expected_schema).validate(expected_policy)
    verify_manifest(layout.manifest, 3, ARTIFACT_RELS)
    return expected_policy, expected_schema


def valid_ref(payload: bool, role: str, zero: bool = False, descriptor_digit: str = "1", projection_class: str = "KUBERNETES_OBJECT_PROJECTION_V1") -> dict[str, Any]:
    spec = role_specs([], [], [])[role]
    if payload != (spec["storage_class"] == "PAYLOAD_WITH_DESCRIPTOR"):
        raise ValueError("Fixture payload mode differs from role storage class")
    descriptor = descriptor_digit * 64
    result: dict[str, Any] = {
        "document_type": "PAYLOAD_EVIDENCE_REF_V1" if payload else "DESCRIPTOR_EVIDENCE_REF_V1",
        "schema_version": 1,
        "evidence_id": "ev-" + descriptor[:32],
        "role": role,
        "producer": spec["producer"],
        "source_kind": spec["source_kind"],
        "media_type": spec["media_type"],
        "storage_class": spec["storage_class"],
        "descriptor_sha256": descriptor,
        "descriptor_size_bytes": 1,
        "descriptor_relative_path": f"descriptors/sha256/{descriptor[:2]}/{descriptor}.json",
        "redaction_status": "NOT_REDACTED",
    }
    if payload:
        payload_hash = ZERO_SHA256 if zero else "2" * 64
        result.update({"payload_sha256": payload_hash, "payload_size_bytes": 0 if zero else 1, "payload_relative_path": f"objects/sha256/{payload_hash[:2]}/{payload_hash}"})
    if role == "kubernetes_object_projection":
        result["projection_class"] = projection_class
    return result


def validate_evidence_ref(reference: dict[str, Any], policy: dict[str, Any] | None = None, payload_bytes: bytes | None = None, descriptor_bytes: bytes | None = None) -> None:
    if policy is None:
        policy = {"roles": role_specs([], [], [])}
    role = reference.get("role")
    if role not in policy["roles"] or role == "terminal_manifest":
        raise ValueError("EvidenceRef role is not admissible")
    spec = policy["roles"][role]
    expected_document = "PAYLOAD_EVIDENCE_REF_V1" if spec["storage_class"] == "PAYLOAD_WITH_DESCRIPTOR" else "DESCRIPTOR_EVIDENCE_REF_V1"
    exact = {
        "document_type": expected_document,
        "producer": spec["producer"],
        "source_kind": spec["source_kind"],
        "media_type": spec["media_type"],
        "storage_class": spec["storage_class"],
    }
    if any(reference.get(key) != value for key, value in exact.items()):
        raise ValueError("EvidenceRef role/producer/source/media/storage combination differs")
    if role == "kubernetes_object_projection" and reference.get("projection_class") not in PROJECTION_CLASSES:
        raise ValueError("Kubernetes projection reference lacks a closed projection class")
    descriptor = reference.get("descriptor_sha256", "")
    if not isinstance(descriptor, str) or not SHA256_RE.fullmatch(descriptor):
        raise ValueError("Invalid descriptor identity")
    if reference.get("evidence_id") != "ev-" + descriptor[:32]:
        raise ValueError("Evidence ID does not derive from descriptor SHA-256")
    expected_descriptor_path = "descriptors/sha256/" + descriptor[:2] + "/" + descriptor + ".json"
    if reference.get("descriptor_relative_path") != expected_descriptor_path:
        raise ValueError("Descriptor path does not derive from descriptor SHA-256")
    validate_relative_path(reference["descriptor_relative_path"], ("descriptors/",))
    descriptor_size = reference.get("descriptor_size_bytes")
    if not isinstance(descriptor_size, int) or isinstance(descriptor_size, bool) or not 1 <= descriptor_size <= spec["maximum_bytes"]:
        raise ValueError("Descriptor size exceeds role bounds")
    if descriptor_bytes is not None and (len(descriptor_bytes) != descriptor_size or sha256_bytes(descriptor_bytes) != descriptor):
        raise ValueError("Descriptor bytes do not match EvidenceRef")
    if reference.get("redaction_status") != "NOT_REDACTED":
        raise ValueError("Only unredacted evidence is admissible")
    if reference.get("storage_class") == "PAYLOAD_WITH_DESCRIPTOR":
        payload = reference.get("payload_sha256", "")
        size = reference.get("payload_size_bytes")
        if not isinstance(payload, str) or not SHA256_RE.fullmatch(payload) or not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise ValueError("Invalid payload identity")
        expected_payload_path = "objects/sha256/" + payload[:2] + "/" + payload
        if reference.get("payload_relative_path") != expected_payload_path:
            raise ValueError("Payload path does not derive from payload SHA-256")
        validate_relative_path(reference["payload_relative_path"], ("objects/",))
        if size == 0 and payload != ZERO_SHA256:
            raise ValueError("Zero-byte payload identity differs")
        if size != 0 and payload == ZERO_SHA256:
            raise ValueError("Empty-payload hash cannot identify nonempty bytes")
        if size == 0 and not spec["zero_byte_payload_allowed"]:
            raise ValueError("Role forbids zero-byte payloads")
        if size > spec["maximum_bytes"]:
            raise ValueError("Payload exceeds role maximum")
        if payload_bytes is not None and (len(payload_bytes) != size or sha256_bytes(payload_bytes) != payload):
            raise ValueError("Payload bytes do not match EvidenceRef")
    elif reference.get("storage_class") != "DESCRIPTOR_ONLY":
        raise ValueError("Unknown EvidenceRef storage class")


def validate_adjudication_references(policy: dict[str, Any], references: list[dict[str, Any]]) -> None:
    for reference in references:
        validate_evidence_ref(reference, policy)
    payload_raw = [reference for reference in references if reference.get("storage_class") == "PAYLOAD_WITH_DESCRIPTOR" and policy["roles"].get(reference.get("role"), {}).get("raw_or_derived", "").startswith("RAW")]
    if not payload_raw:
        raise ValueError("Adjudication requires payload-backed raw evidence")


def valid_release() -> dict[str, Any]:
    return {
        "binding_mode": "SEPARATELY_FROZEN_EXECUTION_RELEASE", "release_artifact": "RUNNER_BUNDLE_SHA256SUMS",
        "manifest_sha256": "3" * 64, "bundle_sha256": "4" * 64, "git_commit": "5" * 40,
        "git_tree": "6" * 40, "annotated_tag_name": "sremut-runner-v1", "annotated_tag_object": "7" * 40,
        "pyproject_sha256": PYPROJECT_SHA256, "uv_lock_sha256": UV_LOCK_SHA256,
    }


def valid_run_common() -> dict[str, Any]:
    return {
        "document_type": "DESCRIPTOR_EVIDENCE_DESCRIPTOR_V1", "schema_version": 1,
        "evidence_id": "ev-" + "1" * 32, "role": "run_identity", "producer": "RUNNER_IDENTITY_RECORDER",
        "source_kind": "LOCAL_IDENTITY", "media_type": "application/json", "storage_class": "DESCRIPTOR_ONLY",
        "run_id": "sremut-ms-m01-r01-a01-abcdef123456",
        "attempt_id": "a01", "created_utc": "2026-08-20T10:00:00.123456789Z", "monotonic_ns": 1,
        "boot_identity": "123e4567-e89b-12d3-a456-426614174000", "redaction_status": "NOT_REDACTED",
    }


def valid_start() -> dict[str, Any]:
    return {
        **valid_run_common(), "phase": "START",
        "runtime_identity": {"python_version": "3.12.3", "pyyaml_version": "6.0.2", "kubernetes_version": "32.0.1", "jsonschema_version": "4.23.0", "uv_version": "0.12.5", "kubectl_version": "v1.32.0"},
        "contract_binding": {"tag_object": CONTRACT_TAG_OBJECT, "commit": CONTRACT_COMMIT, "tree": CONTRACT_TREE, "sha256": CONTRACT_SHA256},
        "execution_profile_binding": {"tag_object": PROFILE_TAG_OBJECT, "commit": PROFILE_COMMIT, "tree": PROFILE_TREE, "sha256": PROFILE_SHA256},
        "runner_release_binding": valid_release(), "pyproject_sha256": PYPROJECT_SHA256, "uv_lock_sha256": UV_LOCK_SHA256,
        "source_alias_sha256": {alias: "8" * 64 for alias in SOURCE_ALIASES},
        "kubeconfig_content_sha256": "9" * 64, "kubectl_default_cache_before_sha256": "a" * 64,
    }


def valid_terminal() -> dict[str, Any]:
    return {
        **valid_run_common(), "phase": "TERMINAL", "start_identity_reference": valid_ref(False, "run_identity", descriptor_digit="2"),
        "terminal_release_identity": valid_release(), "terminal_source_alias_sha256": {alias: "8" * 64 for alias in SOURCE_ALIASES},
        "kubectl_default_cache_before_sha256": "a" * 64, "kubectl_default_cache_after_sha256": "b" * 64,
        "terminal_outcome": "FINALIZED",
    }


def validate_workload_fixture(value: dict[str, Any], schema: dict[str, Any]) -> None:
    ensure_no_floats(value)
    Draft202012Validator(schema).validate(value)
    if len(value["entry_time_ieee754_binary64_hex"]) != value["complete_entry_count"]:
        raise ValueError("binary64 identity array length mismatch")


def structured_items(value: Any):
    if isinstance(value, dict):
        for key, child in value.items():
            if isinstance(key, str):
                yield key, child
            yield from structured_items(child)
    elif isinstance(value, list):
        for child in value:
            yield from structured_items(child)


def detect_sensitive(data: bytes, structured: Any | None = None) -> str | None:
    text = data.decode("latin-1")
    if re.search(r"(?im)^Authorization:[ \t]*[^\r\n]+(?=\r?$)", text):
        return "CAPTURE_REJECTED_AUTHORIZATION"
    if re.search(r"(?i)\bBearer[ \t]+[A-Za-z0-9._~+/=-]{16,4096}\b", text):
        return "CAPTURE_REJECTED_BEARER"
    if any(marker in text for marker in ("-----BEGIN PRIVATE KEY-----", "-----BEGIN RSA PRIVATE KEY-----", "-----BEGIN EC PRIVATE KEY-----", "-----BEGIN OPENSSH PRIVATE KEY-----")):
        return "CAPTURE_REJECTED_PRIVATE_KEY"
    for candidate in re.findall(r"[A-Za-z][A-Za-z0-9+.-]*://[^\s<>\"']+", text):
        try:
            parsed = urlsplit(candidate)
            userinfo = parsed.netloc.rsplit("@", 1)[0] if "@" in parsed.netloc else ""
            password = parsed.password
            username = parsed.username
        except ValueError:
            continue
        if parsed.scheme.lower() in ("http", "https") and ":" in userinfo and username not in (None, "") and password not in (None, ""):
            return "CAPTURE_REJECTED_CREDENTIAL_URL"
    assignment = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}=.*$")
    sensitive_keys = set(build_sensitive_keys())
    consecutive = 0
    for line in text.splitlines():
        if assignment.fullmatch(line):
            consecutive += 1
            key, value = line.split("=", 1)
            if key.upper() in sensitive_keys and value:
                return "CAPTURE_REJECTED_ENVIRONMENT"
            if consecutive >= 4:
                return "CAPTURE_REJECTED_ENVIRONMENT"
        else:
            consecutive = 0
    if isinstance(structured, (dict, list)):
        root = structured if isinstance(structured, dict) else None
        if root is not None and root.get("apiVersion") == "v1" and root.get("kind") == "Secret":
            return "CAPTURE_REJECTED_KUBERNETES_SECRET"
        if root is not None and root.get("apiVersion") == "v1" and root.get("kind") == "Config" and all(key in root for key in ("clusters", "contexts", "users")):
            kube_keys = {"client-key-data", "client-key", "client-certificate-data", "client-certificate", "token", "token-file", "username", "password", "auth-provider", "exec"}
            for user in root.get("users", []):
                if isinstance(user, dict) and isinstance(user.get("user"), dict) and any(key in user["user"] for key in kube_keys):
                    return "CAPTURE_REJECTED_KUBECONFIG"
        structured_keys = {"access_token", "api_key", "bearer_token", "client_secret", "id_token", "refresh_token", "token", "passphrase", "password", "client_key_data", "private_key", "private_key_data", "ssh_private_key"}
        for key, value in structured_items(structured):
            normalized = key.lower().replace("-", "_")
            if normalized in structured_keys and value not in ("", None, False, [], {}):
                return "CAPTURE_REJECTED_STRUCTURED_SECRET"
    return None


def build_sensitive_keys() -> list[str]:
    return ["API_KEY", "AUTH_TOKEN", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AZURE_CLIENT_SECRET", "GITHUB_TOKEN", "GOOGLE_APPLICATION_CREDENTIALS", "KUBECONFIG", "PASSWORD", "PRIVATE_KEY", "SECRET", "TOKEN"]


def journal_record_sha256(record: dict[str, Any]) -> str:
    material = deepcopy(record)
    material.pop("canonical_current_entry_sha256", None)
    return sha256_bytes(canonical_json_bytes(material))


def capture_rejection(role: str, code: str, detector_id: str = "SENSITIVE_STRUCTURED_SECRET", observed_size: int = 0) -> dict[str, Any]:
    record = {
        "document_type": "JOURNAL_RECORD_V1", "schema_version": 1,
        "journal_record_type": "CAPTURE_REJECTED", "sequence_number": 0,
        "previous_entry_sha256": GENESIS_SHA256, "canonical_current_entry_sha256": GENESIS_SHA256,
        "run_id": "sremut-ms-m01-r01-a01-abcdef123456", "attempt_id": "a01",
        "intended_role": role, "rejection_code": code, "detector_id": detector_id,
        "source_kind": ROLE_SOURCE_KINDS[role], "observed_size": observed_size,
        "created_utc": "2026-08-20T10:00:00.123456789Z", "monotonic_ns": 1,
        "boot_identity": "123e4567-e89b-12d3-a456-426614174000",
    }
    record["canonical_current_entry_sha256"] = journal_record_sha256(record)
    return record


def validate_capture_payload(policy: dict[str, Any], role: str, data: bytes, structured: Any | None = None) -> None:
    spec = policy["roles"].get(role)
    if spec is None or spec["storage_class"] != "PAYLOAD_WITH_DESCRIPTOR":
        raise ValueError("Role cannot publish a payload")
    if len(data) > spec["maximum_bytes"] or (not data and not spec["zero_byte_payload_allowed"]):
        raise ValueError("Payload size is outside the role bound")
    if spec["strict_utf8_required"]:
        try:
            data.decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise ValueError("Strict UTF-8 payload is required") from error
    detector = detect_sensitive(data, structured)
    if detector is not None:
        raise ValueError(f"Sensitive capture rejected by {detector}")


def validate_kubernetes_request(policy: dict[str, Any], request: dict[str, Any]) -> None:
    if request.get("document_type") != "KUBERNETES_REQUEST_V1" or request.get("context") != "kind-kind":
        raise ValueError("Kubernetes request identity/context differs")
    if request.get("resource") == "secrets":
        raise ValueError("Secret evidence is forbidden")
    candidates = policy["kubernetes_evidence_surface"]["resource_rules"] + policy["kubernetes_evidence_surface"]["subresource_rules"]
    for candidate in candidates:
        keys = ("api_group", "api_version", "resource", "response_kind", "subresource", "namespace", "context")
        operation = request.get("operation")
        if request.get("request_rule_id") != candidate["id"] or not all(request.get(key) == candidate[key] for key in keys) or operation not in candidate["operations"]:
            continue
        if request.get("label_selector") not in candidate["label_selectors"] or request.get("field_selector") not in candidate["field_selectors"]:
            raise ValueError("Kubernetes selector is not allowlisted")
        if request.get("request_options") != candidate["request_options"]:
            raise ValueError("Kubernetes request options differ")
        if request.get("purpose") != candidate["purpose"] or request.get("consumer") not in candidate["consumers"]:
            raise ValueError("Kubernetes purpose/consumer differs")
        if request.get("projection_class") != candidate["projection_class"]:
            raise ValueError("Kubernetes projection class differs")
        predicates = candidate["name_predicates_by_operation"][operation]
        name = request.get("name")
        matched = False
        for predicate in predicates:
            kind = predicate["type"]
            if kind == "LIST_NO_NAME":
                matched = name == ""
            elif kind == "EXACT_NAME":
                matched = name in predicate["allowed_names"]
            elif kind in ("CAPTURED_OBJECT_NAME", "CAPTURED_REPLACEMENT_POD_NAME"):
                matched = isinstance(name, str) and bool(name) and name == request.get("captured_object_name")
            elif kind == "DETERMINISTIC_CHALLENGE_NAME":
                matched = isinstance(name, str) and re.fullmatch(re.escape(predicate["prefix"]) + r"[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?", name) is not None
                if operation == "DELETE":
                    matched = matched and name == request.get("captured_object_name")
            if matched:
                break
        if not matched:
            raise ValueError("Kubernetes name does not satisfy a closed predicate")
        if operation in ("CREATE", "DELETE"):
            if candidate["evidence_policy_grants_mutation_authority"] is not False or not candidate["mutation_requires_frozen_profile_operation_kind"]:
                raise ValueError("Mutation rule is not subordinate to the frozen profile")
            validate_evidence_ref(request.get("intent_reference") or {}, policy)
            if request["intent_reference"].get("role") != "mutation_intent":
                raise ValueError("Mutation request lacks a mutation-intent reference")
            allowed_operation_kinds = candidate["profile_operation_kinds_by_operation"].get(operation, [])
            if request.get("authorized_operation_kind") not in allowed_operation_kinds:
                raise ValueError("Mutation request is not bound to an allowed frozen operation kind")
        if operation == "DELETE":
            uid = request.get("captured_object_uid")
            if not isinstance(uid, str) or not uid or request.get("uid_precondition") != uid:
                raise ValueError("DELETE requires the exact captured UID precondition")
            captured_reference = request.get("captured_object_reference")
            validate_evidence_ref(captured_reference or {}, policy)
            if captured_reference.get("role") != "kubernetes_object_projection":
                raise ValueError("DELETE requires captured-object projection evidence")
        return
    raise ValueError("Kubernetes request does not match a frozen rule")


def _selector_matches(item: dict[str, Any], selector: str) -> bool:
    if not selector:
        return True
    fields = item.get("projected_fields")
    if not isinstance(fields, dict):
        return False
    labels = fields.get("metadata", {}).get("labels", {}) if isinstance(fields.get("metadata"), dict) else {}
    if not isinstance(labels, dict) or "=" not in selector:
        return False
    key, expected = selector.split("=", 1)
    return labels.get(key) == expected


def validate_kubernetes_response(policy: dict[str, Any], response: dict[str, Any]) -> None:
    request = response.get("request")
    if not isinstance(request, dict) or request.get("operation") != "LIST":
        raise ValueError("LIST response must cite a validated LIST request")
    validate_kubernetes_request(policy, request)
    rule = next(rule for rule in policy["kubernetes_evidence_surface"]["resource_rules"] if rule["id"] == request["request_rule_id"])
    items = response.get("items")
    if not isinstance(items, list) or len(items) > rule["maximum_list_items"]:
        raise ValueError("LIST response exceeds the rule maximum")
    expected = (rule["api_version"], rule["response_kind"], rule["namespace"], rule["projection_class"])
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("LIST item is not an object")
        observed = (item.get("apiVersion"), item.get("kind"), item.get("namespace"), item.get("projection_class"))
        if observed != expected:
            raise ValueError("LIST item kind/namespace/projection differs")
        if not isinstance(item.get("name"), str) or not item["name"]:
            raise ValueError("LIST item lacks an object name")
        item_predicate = rule["list_item_name_predicate"]
        if item_predicate["type"] == "EXACT_NAME" and item["name"] not in item_predicate["allowed_names"]:
            raise ValueError("LIST item name is not approved")
        if item_predicate["type"] == "DNS_SUBDOMAIN_MATCHING_REQUEST_SELECTOR" and re.fullmatch(r"[a-z0-9](?:[-a-z0-9.]{0,251}[a-z0-9])?", item["name"]) is None:
            raise ValueError("LIST item name is not a Kubernetes DNS subdomain")
        if not _selector_matches(item, request["label_selector"]):
            raise ValueError("LIST item does not satisfy the frozen selector")
    if not isinstance(response.get("list_metadata"), dict):
        raise ValueError("LIST metadata must be validated separately")


def _remove_token_path(value: Any, tokens: list[str]) -> None:
    if not tokens:
        return
    token = tokens[0]
    if token == "[]":
        if isinstance(value, list):
            for item in value:
                _remove_token_path(item, tokens[1:])
        return
    if not isinstance(value, dict) or token not in value:
        return
    if len(tokens) == 1:
        del value[token]
    else:
        _remove_token_path(value[token], tokens[1:])


def _token_path_present(value: Any, tokens: list[str]) -> bool:
    if not tokens:
        return True
    token = tokens[0]
    if token == "[]":
        return isinstance(value, list) and any(_token_path_present(item, tokens[1:]) for item in value)
    return isinstance(value, dict) and token in value and _token_path_present(value[token], tokens[1:])


def derive_service_restoration_body(policy: dict[str, Any], schema: dict[str, Any], source: dict[str, Any], source_request_reference: dict[str, Any], capture_timestamp: str, source_service_reference: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(source, dict) or source.get("apiVersion") != "v1" or source.get("kind") != "Service":
        raise ValueError("Restoration source is not a core/v1 Service")
    metadata = source.get("metadata")
    if not isinstance(metadata, dict) or metadata.get("name") != "user-service" or metadata.get("namespace") != "social-network":
        raise ValueError("Restoration source is not Service/social-network/user-service")
    uid = metadata.get("uid")
    resource_version = metadata.get("resourceVersion")
    if not isinstance(uid, str) or not uid or not isinstance(resource_version, str) or not resource_version:
        raise ValueError("Restoration source lacks UID/resourceVersion")
    source_bytes = canonical_json_bytes(source)
    if detect_sensitive(source_bytes, source) is not None:
        raise ValueError("Sensitive Service capture cannot be published or authorize mutation")
    validate_evidence_ref(source_request_reference, policy)
    if source_request_reference.get("role") != "kubernetes_request_identity":
        raise ValueError("Restoration source request has the wrong evidence role")
    validate_evidence_ref(source_service_reference, policy)
    if source_service_reference.get("role") != "kubernetes_object_projection" or source_service_reference.get("projection_class") != "SERVICE_RESTORATION_SOURCE_V1":
        raise ValueError("Restoration source has the wrong projection class")
    normalized = deepcopy(source)
    stripped = policy["service_restoration_projection"]["exact_stripped_field_token_paths"]
    for token_path in stripped:
        _remove_token_path(normalized, token_path)
    Draft202012Validator(schema["$defs"]["service_create_body"]).validate(normalized)
    serialized = canonical_json_bytes(normalized)
    restored = json.loads(serialized.decode("utf-8"))
    if canonical_json_bytes(restored) != serialized:
        raise ValueError("Normalized Service does not survive canonical round trip")
    body_hash = sha256_bytes(serialized)
    document = {
        "document_type": "SERVICE_RESTORATION_BODY_V1", "schema_version": 1,
        "projection_class": "SERVICE_RESTORATION_BODY_V1",
        "derivation_algorithm": "KUBERNETES_SERVICE_CREATE_BODY_NORMALIZATION_V1",
        "source_service_uid": uid, "source_service_resource_version": resource_version,
        "exact_stripped_field_token_paths": deepcopy(stripped),
        "normalized_body_sha256": body_hash, "canonical_json_identity": body_hash,
        "capture_timestamp": capture_timestamp, "source_request_reference": deepcopy(source_request_reference),
        "source_service_reference": deepcopy(source_service_reference),
        "normalized_service_create_body": restored,
    }
    validate_service_restoration_body(policy, schema, document, source)
    return document


def validate_service_restoration_body(policy: dict[str, Any], schema: dict[str, Any], document: dict[str, Any], source: dict[str, Any] | None = None) -> None:
    Draft202012Validator(schema).validate(document)
    normalized = document["normalized_service_create_body"]
    body_bytes = canonical_json_bytes(normalized)
    body_hash = sha256_bytes(body_bytes)
    if document["normalized_body_sha256"] != body_hash or document["canonical_json_identity"] != body_hash:
        raise ValueError("Restoration-body identity does not match canonical bytes")
    for token_path in document["exact_stripped_field_token_paths"]:
        if _token_path_present(normalized, token_path):
            raise ValueError("A frozen stripped Service field remains")
    if detect_sensitive(body_bytes, normalized) is not None:
        raise ValueError("Normalized Service contains sensitive material")
    validate_evidence_ref(document["source_request_reference"], policy)
    validate_evidence_ref(document["source_service_reference"], policy)
    if document["source_service_reference"].get("role") != "kubernetes_object_projection" or document["source_service_reference"].get("projection_class") != "SERVICE_RESTORATION_SOURCE_V1":
        raise ValueError("Restoration source reference has the wrong projection class")
    if source is not None:
        expected = deepcopy(source)
        for token_path in document["exact_stripped_field_token_paths"]:
            _remove_token_path(expected, token_path)
        if canonical_json_bytes(expected) != body_bytes:
            raise ValueError("An unstripped Service field changed during normalization")


def descriptor_content_sha256(descriptor: dict[str, Any]) -> str:
    material = deepcopy(descriptor)
    material.pop("descriptor_sha256", None)
    material.pop("evidence_id", None)
    return sha256_bytes(canonical_json_bytes(material))


def validate_descriptor_content_identity(descriptor: dict[str, Any], reference: dict[str, Any], policy: dict[str, Any]) -> None:
    validate_evidence_ref(reference, policy)
    digest = descriptor_content_sha256(descriptor)
    if reference["descriptor_sha256"] != digest or reference["evidence_id"] != "ev-" + digest[:32]:
        raise ValueError("Descriptor canonical identity differs from its EvidenceRef")
    for field in ("role", "producer", "source_kind", "media_type", "storage_class"):
        if descriptor.get(field) != reference.get(field):
            raise ValueError("Descriptor and EvidenceRef identity fields differ")
    if reference["storage_class"] == "PAYLOAD_WITH_DESCRIPTOR":
        for field in ("payload_sha256", "payload_size_bytes", "payload_relative_path"):
            if descriptor.get(field) != reference.get(field):
                raise ValueError("Descriptor and EvidenceRef payload identities differ")


def validate_workload_window_consistency(documents: list[dict[str, Any]]) -> None:
    if not documents:
        raise ValueError("At least one workload-window document is required")
    identities = [document.get("workload_window") for document in documents]
    if any(not isinstance(identity, dict) for identity in identities):
        raise ValueError("Workload evidence lacks a closed window identity")
    core_fields = ("phase", "ordinal", "run_id", "attempt_id", "mutant_id", "repetition")
    core = tuple(identities[0][field] for field in core_fields)
    if WORKLOAD_WINDOWS.get(core[0]) != core[1]:
        raise ValueError("Workload phase and deterministic ordinal differ")
    for identity in identities[1:]:
        if tuple(identity[field] for field in core_fields) != core or canonical_json_bytes(identity) != canonical_json_bytes(identities[0]):
            raise ValueError("Cross-window evidence substitution detected")
    log = next((document for document in documents if document.get("role") == "workload_log_bytes"), None)
    boundary = next((document for document in documents if document.get("role") == "workload_boundary"), None)
    parsed = next((document for document in documents if document.get("role") == "workload_parse_result"), None)
    if log is None or boundary is None or parsed is None:
        raise ValueError("Workload adjudication requires raw log, boundary, and parse result")
    if len(log.get("entry_time_ieee754_binary64_hex", [])) != log.get("complete_entry_count"):
        raise ValueError("Workload log entry/time cardinality differs")
    if parsed.get("fresh_request_count", 0) < 50 or parsed.get("failure_marker_count") != 0:
        raise ValueError("Fresh workload predicate does not pass")


def validate_journal_chain(records: list[dict[str, Any]]) -> None:
    previous = GENESIS_SHA256
    for sequence, record in enumerate(records):
        if record.get("sequence_number") != sequence or record.get("previous_entry_sha256") != previous:
            raise ValueError("Journal sequence or previous hash differs")
        current = journal_record_sha256(record)
        if record.get("canonical_current_entry_sha256") != current:
            raise ValueError("Journal current-entry hash differs")
        previous = current


def validate_attempt_envelope(envelope: dict[str, Any], policy: dict[str, Any]) -> None:
    identities = envelope.get("run_identities")
    if not isinstance(identities, list) or len(identities) != 2:
        raise ValueError("Attempt requires exactly START and TERMINAL identities")
    start, terminal = identities
    if start.get("phase") != "START" or terminal.get("phase") != "TERMINAL":
        raise ValueError("Attempt identity phases are absent, duplicated, or out of order")
    if start.get("run_id") != envelope.get("run_id") or terminal.get("run_id") != envelope.get("run_id") or start.get("attempt_id") != envelope.get("attempt_id") or terminal.get("attempt_id") != envelope.get("attempt_id"):
        raise ValueError("Attempt identity scope differs")
    start_reference = terminal.get("start_identity_reference")
    validate_evidence_ref(start_reference, policy)
    if start_reference.get("evidence_id") in (start.get("evidence_id"), terminal.get("evidence_id")):
        raise ValueError("Run identity self-citation is forbidden")
    manifest = envelope.get("terminal_manifest", {})
    terminal_time = manifest.get("terminal_cache_snapshot_monotonic_ns")
    installed_time = manifest.get("installed_monotonic_ns")
    if not isinstance(terminal_time, int) or not isinstance(installed_time, int) or terminal_time > installed_time:
        raise ValueError("Terminal cache snapshot/manifest ordering differs")
    for operation in envelope.get("operations", []):
        if operation.get("created_monotonic_ns", installed_time) >= installed_time:
            raise ValueError("Post-terminal operation detected")
    if terminal.get("terminal_outcome") != envelope.get("terminal_outcome") or manifest.get("terminal_outcome") != envelope.get("terminal_outcome"):
        raise ValueError("Terminal outcome differs across attempt records")
    validate_journal_chain(envelope.get("journal_records", []))
    for reference in envelope.get("adjudication_references", []):
        validate_evidence_ref(reference, policy)
    validate_adjudication_references(policy, envelope.get("raw_evidence_references", []))


def validate_mutation_restoration_binding(document: dict[str, Any], policy: dict[str, Any]) -> None:
    role = document.get("role")
    if role == "mutation_intent":
        service_operations = {
            "INITIAL_USER_SERVICE_DELETION", "MUTANT_SERVICE_CREATION",
            "MUTANT_SERVICE_DELETION", "RESTORED_SERVICE_CREATION",
        }
        if document.get("operation_kind") in service_operations:
            reference = document.get("service_restoration_body_reference")
            validate_evidence_ref(reference or {}, policy)
            if reference.get("role") != "kubernetes_object_projection" or reference.get("projection_class") != "SERVICE_RESTORATION_BODY_V1":
                raise ValueError("Service mutation intent must cite the sealed restoration projection")
            if document.get("service_restoration_body_sha256") != reference.get("payload_sha256"):
                raise ValueError("Service mutation intent restoration-body hash differs")
    elif role == "mutation_receipt":
        body_hash = document.get("service_restoration_body_sha256")
        observation = document.get("post_create_observation_reference")
        if body_hash is not None or observation is not None:
            if not isinstance(body_hash, str) or not SHA256_RE.fullmatch(body_hash):
                raise ValueError("Restoration receipt body hash is invalid")
            validate_evidence_ref(observation or {}, policy)
            if observation.get("role") != "kubernetes_object_projection":
                raise ValueError("Restoration receipt lacks a post-create Kubernetes observation")


def validate_runtime_document(document: dict[str, Any], schema: dict[str, Any], policy: dict[str, Any]) -> None:
    ensure_no_floats(document)
    validate_structural_schema_branch(document, schema)
    kind = document.get("document_type")
    if kind in ("PAYLOAD_EVIDENCE_REF_V1", "DESCRIPTOR_EVIDENCE_REF_V1"):
        validate_evidence_ref(document, policy)
    elif kind == "ATTEMPT_VALIDATION_ENVELOPE_V1":
        validate_attempt_envelope(document, policy)
    elif kind == "JOURNAL_RECORD_V1":
        if document.get("canonical_current_entry_sha256") != journal_record_sha256(document):
            raise ValueError("Standalone journal-record hash differs")
    elif kind == "KUBERNETES_REQUEST_V1":
        validate_kubernetes_request(policy, document)
    elif kind == "KUBERNETES_LIST_RESPONSE_V1":
        validate_kubernetes_response(policy, document)
    elif kind == "SERVICE_RESTORATION_BODY_V1":
        validate_service_restoration_body(policy, schema, document)
    elif kind == "PAYLOAD_EVIDENCE_DESCRIPTOR_V1" and document.get("role") == "healthy_prestate":
        reference = document.get("captured_service_reference")
        validate_evidence_ref(reference, policy)
        if reference.get("role") != "kubernetes_object_projection" or reference.get("projection_class") != "SERVICE_RESTORATION_SOURCE_V1":
            raise ValueError("Healthy prestate must cite the sealed captured Service source projection")
    elif kind == "DESCRIPTOR_EVIDENCE_DESCRIPTOR_V1" and document.get("role") in ("mutation_intent", "mutation_receipt"):
        validate_mutation_restoration_binding(document, policy)


def validate_command(policy: dict[str, Any], template_id: str, argv: list[str]) -> None:
    template = policy["challenge_stream_protocol"]["approved_templates"].get(template_id)
    if template is None or "argv" not in template or argv != template["argv"]:
        raise ValueError("Command is outside approved exact templates")


def expect_failure(function, *args, **kwargs) -> None:
    try:
        function(*args, **kwargs)
    except Exception:
        return
    raise AssertionError("Expected failure did not occur")


def mutate(policy: dict[str, Any], operation) -> dict[str, Any]:
    changed = deepcopy(policy)
    operation(changed)
    return changed


def object_paths(value: Any, prefix: tuple[str | int, ...] = ()):
    if isinstance(value, dict):
        yield prefix
        for key, child in value.items():
            yield from object_paths(child, prefix + (key,))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from object_paths(child, prefix + (index,))


def run_schema_tests(policy: dict[str, Any], schema: dict[str, Any], profile: dict[str, Any]) -> tuple[int, int]:
    positives = [policy, yaml.safe_load(render_policy(policy)), json.loads(json.dumps(policy))]
    for fixture in positives:
        validate_structural_schema_branch(fixture, schema)
    negatives: list[dict[str, Any]] = []
    operations = [
        lambda p: p.update({"unknown": True}),
        lambda p: p["roles"].pop("challenge_stderr"),
        lambda p: p["roles"].update({"twenty_second_role": deepcopy(p["roles"]["challenge_result"])}),
        lambda p: p["roles"]["run_identity"].update({"unknown": True}),
        lambda p: p["roles"]["run_identity"].update({"maximum_bytes": 1}),
        lambda p: p["roles"]["run_identity"].update({"producer": "UNKNOWN"}),
        lambda p: p["roles"]["run_identity"].update({"consumers": ["UNKNOWN"]}),
        lambda p: p["roles"]["run_identity"].update({"media_type": "text/csv"}),
        lambda p: p["roles"]["run_identity"].update({"storage_class": "OTHER"}),
        lambda p: p["roles"]["run_identity"].update({"redaction_status": "REDACTED"}),
        lambda p: p["terminalization"].update({"unknown": True}),
        lambda p: p["bindings"]["contract"].update({"sha256": "0" * 64}),
        lambda p: p["run_identity_protocol"]["start_required_fields"].append("kubectl_default_cache_after_sha256"),
        lambda p: p["storage_model"]["terminal_manifest"].update({"manifest_sha256": "0" * 64}),
        lambda p: p["storage_model"]["terminal_manifest"].update({"has_descriptor": True}),
        lambda p: p.update({"required_metadata_source": "profile"}),
        lambda p: p["expanded_frozen_metadata"]["mutation_intent_required_metadata"].pop(),
        lambda p: p["expanded_frozen_metadata"]["mutation_receipt_required_metadata"].reverse(),
        lambda p: p["expanded_frozen_metadata"]["adjudication_required_metadata"].append("extra"),
        lambda p: p["sensitive_capture_policy"]["detectors"]["SENSITIVE_KUBECONFIG_STRUCTURE"].update({"ordinary_json_username_or_exec_does_not_trigger": False}),
        lambda p: p["sensitive_capture_policy"]["detectors"]["SENSITIVE_DENSE_ENVIRONMENT_DUMP"].update({"minimum_consecutive_lines": 3}),
        lambda p: p["sensitive_capture_policy"]["detectors"]["SENSITIVE_DENSE_ENVIRONMENT_DUMP"].update({"line_grammar": "^.*=.*$"}),
        lambda p: p["runner_release_binding"].update({"bundle_sha256": None}),
        lambda p: p["runner_release_binding"].update({"binding_mode": "INLINE"}),
        lambda p: p["kubernetes_evidence_surface"]["resource_rules"][0]["projection_paths"].append("metadata.name"),
        lambda p: p["kubernetes_evidence_surface"]["resource_rules"].append(deepcopy(p["kubernetes_evidence_surface"]["resource_rules"][0])),
        lambda p: p["workload_evidence_protocol"].update({"entry_time_identity": "decimal"}),
        lambda p: p["challenge_stream_protocol"].update({"raw_websocket_frame_bytes_claimed": True}),
        lambda p: p["adjudication_raw_evidence_rule"].update({"minimum_payload_backed_raw_evidence_references": 0}),
        lambda p: p["evidence_reference"]["zero_byte_payload"].update({"sha256": "0" * 64}),
        lambda p: p["local_source_aliases"].update({"UNKNOWN_ALIAS": deepcopy(p["local_source_aliases"]["CONTRACT"])}),
        lambda p: p["workload_evidence_protocol"].update({"source_alias": "bad.alias"}),
    ]
    for operation in operations:
        negatives.append(mutate(policy, operation))
    for object_path in object_paths(policy):
        fixture = deepcopy(policy)
        cursor: Any = fixture
        for component in object_path:
            cursor = cursor[component]
        cursor["__unexpected_field__"] = True
        negatives.append(fixture)
    for fixture in negatives:
        try:
            validate_structural_schema_branch(fixture, schema)
        except ValidationError:
            continue
        raise AssertionError("Negative policy schema fixture was accepted")
    return len(positives), len(negatives)


def valid_kubernetes_request(policy: dict[str, Any], rule_id: str, operation: str, name: str, selector: str = "") -> dict[str, Any]:
    rules = policy["kubernetes_evidence_surface"]["resource_rules"] + policy["kubernetes_evidence_surface"]["subresource_rules"]
    rule = next(candidate for candidate in rules if candidate["id"] == rule_id)
    captured_name = name if operation == "DELETE" else None
    captured_uid = "123e4567-e89b-12d3-a456-426614174000" if operation == "DELETE" else None
    operation_kind = None
    if operation in ("CREATE", "DELETE"):
        operation_kind = rule["profile_operation_kinds_by_operation"][operation][0]
    return {
        "document_type": "KUBERNETES_REQUEST_V1", "schema_version": 1, "request_rule_id": rule_id,
        "context": rule["context"], "api_group": rule["api_group"], "api_version": rule["api_version"],
        "resource": rule["resource"], "response_kind": rule["response_kind"], "subresource": rule["subresource"],
        "operation": operation, "namespace": rule["namespace"], "name": name,
        "label_selector": selector, "field_selector": "", "request_options": deepcopy(rule["request_options"]),
        "purpose": rule["purpose"], "consumer": rule["consumers"][0], "projection_class": rule["projection_class"],
        "captured_object_name": captured_name, "captured_object_uid": captured_uid,
        "captured_object_resource_version": "1" if operation == "DELETE" else None,
        "captured_object_reference": valid_ref(True, "kubernetes_object_projection") if operation == "DELETE" else None,
        "uid_precondition": captured_uid, "authorized_operation_kind": operation_kind,
        "intent_reference": valid_ref(False, "mutation_intent") if operation in ("CREATE", "DELETE") else None,
    }


def valid_attempt_envelope() -> dict[str, Any]:
    journal = capture_rejection("challenge_stdout", "CAPTURE_REJECTED_STRUCTURED_SECRET")
    return {
        "document_type": "ATTEMPT_VALIDATION_ENVELOPE_V1", "schema_version": 1,
        "run_id": "sremut-ms-m01-r01-a01-abcdef123456", "attempt_id": "a01", "terminal_outcome": "FINALIZED",
        "run_identities": [valid_start(), valid_terminal()], "operations": [], "journal_records": [journal],
        "adjudication_references": [valid_ref(False, "adjudication")],
        "raw_evidence_references": [valid_ref(True, "workload_log_bytes")],
        "terminal_manifest": {
            "document_type": "TERMINAL_MANIFEST_V1", "schema_version": 1,
            "run_id": "sremut-ms-m01-r01-a01-abcdef123456", "attempt_id": "a01", "terminal_outcome": "FINALIZED",
            "terminal_cache_snapshot_monotonic_ns": 8, "installed_monotonic_ns": 9,
            "manifest_relative_path": "manifests/terminal.sha256",
        },
    }


def valid_service_source() -> dict[str, Any]:
    return {
        "apiVersion": "v1", "kind": "Service",
        "metadata": {
            "name": "user-service", "namespace": "social-network", "uid": "service-uid", "resourceVersion": "123",
            "creationTimestamp": "2026-08-20T00:00:00Z", "generation": 4, "managedFields": [{"manager": "test"}],
            "labels": {"service": "user-service", "app.kubernetes.io/managed-by": "Helm"},
            "annotations": {"meta.helm.sh/release-name": "social-network"}, "finalizers": ["example.invalid/finalizer"],
            "ownerReferences": [{"apiVersion": "apps/v1", "kind": "Deployment", "name": "owner", "uid": "owner-uid", "controller": True}],
        },
        "spec": {
            "type": "ClusterIP", "clusterIP": "10.96.0.50", "clusterIPs": ["10.96.0.50"], "ipFamilies": ["IPv4"],
            "ipFamilyPolicy": "SingleStack", "internalTrafficPolicy": "Cluster", "sessionAffinity": "ClientIP",
            "sessionAffinityConfig": {"clientIP": {"timeoutSeconds": 10800}}, "selector": {"service": "user-service"},
            "ports": [{"name": "grpc", "protocol": "TCP", "appProtocol": "grpc", "port": 9090, "targetPort": 9090, "nodePort": 30090}],
            "publishNotReadyAddresses": False, "trafficDistribution": "PreferClose",
        },
        "status": {"loadBalancer": {}},
    }


def valid_workload_documents() -> list[dict[str, Any]]:
    raw_ref = valid_ref(True, "workload_log_bytes")
    boundary_ref = valid_ref(False, "workload_boundary", descriptor_digit="3")
    parse_ref = valid_ref(True, "workload_parse_result", descriptor_digit="4")
    window = {
        "phase": "INITIAL_MUTANT_CHALLENGE", "ordinal": 1,
        "run_id": "sremut-ms-m01-r01-a01-abcdef123456", "attempt_id": "a01", "mutant_id": "MS-M01", "repetition": 1,
        "boundary_reference": boundary_ref, "raw_log_reference": raw_ref, "parse_result_reference": parse_ref,
    }
    return [
        {"role": "workload_log_bytes", "complete_entry_count": 2, "entry_time_ieee754_binary64_hex": [ieee754_binary64_hex(1.0), ieee754_binary64_hex(2.0)], "workload_window": deepcopy(window)},
        {"role": "workload_boundary", "workload_window": deepcopy(window)},
        {"role": "workload_parse_result", "fresh_request_count": 50, "failure_marker_count": 0, "workload_window": deepcopy(window)},
    ]


def runtime_negative_tests(policy: dict[str, Any], schema: dict[str, Any]) -> tuple[int, int]:
    public = Draft202012Validator(schema)
    positive: list[dict[str, Any]] = [
        policy, valid_start(), valid_ref(True, "healthy_prestate"), valid_ref(False, "run_identity"),
        capture_rejection("challenge_stdout", "CAPTURE_REJECTED_STRUCTURED_SECRET"),
    ]
    attempt = valid_attempt_envelope()
    positive.append(attempt)
    for outcome in TERMINAL_OUTCOMES:
        outcome_attempt = deepcopy(attempt)
        outcome_attempt["terminal_outcome"] = outcome
        outcome_attempt["run_identities"][1]["terminal_outcome"] = outcome
        outcome_attempt["terminal_manifest"]["terminal_outcome"] = outcome
        validate_runtime_document(outcome_attempt, schema, policy)
    stop = {
        "document_type": "TERMINAL_GLOBAL_STOP_V1", "schema_version": 1,
        "run_id": attempt["run_id"], "attempt_id": attempt["attempt_id"], "terminal_outcome": "RESTORATION_BLOCKED",
        "reason_code": "RESTORATION_NOT_ESTABLISHED", "adjudication_reference": valid_ref(False, "adjudication"),
        "created_utc": "2026-08-20T10:00:00.123456789Z", "monotonic_ns": 10,
        "boot_identity": "123e4567-e89b-12d3-a456-426614174000",
    }
    positive.append(stop)
    service_request_ref = valid_ref(False, "kubernetes_request_identity", descriptor_digit="5")
    restoration = derive_service_restoration_body(policy, schema, valid_service_source(), service_request_ref, "2026-08-20T10:00:00.123456789Z", valid_ref(True, "kubernetes_object_projection", descriptor_digit="6", projection_class="SERVICE_RESTORATION_SOURCE_V1"))
    positive.append(restoration)
    service_get = valid_kubernetes_request(policy, "USER_SERVICE_READ_AND_GUARDED_MUTATION", "GET", "user-service")
    positive.append(service_get)
    pod_list = valid_kubernetes_request(policy, "PODS_SOCIAL_READ_AND_GUARDED_MUTATION", "LIST", "", "service=user-service")
    list_response = {
        "document_type": "KUBERNETES_LIST_RESPONSE_V1", "schema_version": 1, "request": pod_list,
        "list_metadata": {"resourceVersion": "1"},
        "items": [{"apiVersion": "v1", "kind": "Pod", "namespace": "social-network", "name": "user-service-abc", "projection_class": "KUBERNETES_OBJECT_PROJECTION_V1", "projected_fields": {"metadata": {"labels": {"service": "user-service"}}}}],
    }
    positive.append(list_response)
    # One payload descriptor and one descriptor-only descriptor exercise the two descriptor roots.
    healthy_descriptor = {
        "document_type": "PAYLOAD_EVIDENCE_DESCRIPTOR_V1", "schema_version": 1, "evidence_id": "ev-" + "1" * 32,
        "run_id": attempt["run_id"], "attempt_id": "a01", "producer": "RUNNER_PREFLIGHT_RECORDER", "source_kind": "GENERATED_DESCRIPTOR",
        "created_utc": "2026-08-20T10:00:00.123456789Z", "monotonic_ns": 1, "boot_identity": "123e4567-e89b-12d3-a456-426614174000",
        "redaction_status": "NOT_REDACTED", "role": "healthy_prestate", "media_type": "application/json", "storage_class": "PAYLOAD_WITH_DESCRIPTOR",
        "captured_replica_baseline": {}, "captured_service_reference": valid_ref(True, "kubernetes_object_projection", projection_class="SERVICE_RESTORATION_SOURCE_V1"),
        "payload_sha256": "2" * 64, "payload_size_bytes": 1, "payload_relative_path": "objects/sha256/22/" + "2" * 64,
    }
    positive.append(healthy_descriptor)
    for fixture in positive:
        validate_runtime_document(fixture, schema, policy)
    validate_workload_window_consistency(valid_workload_documents())
    validate_kubernetes_response(policy, list_response)
    validate_service_restoration_body(policy, schema, restoration, valid_service_source())
    restoration_ref = valid_ref(True, "kubernetes_object_projection", projection_class="SERVICE_RESTORATION_BODY_V1")
    service_intent_binding = {"role": "mutation_intent", "operation_kind": "INITIAL_USER_SERVICE_DELETION", "service_restoration_body_reference": restoration_ref, "service_restoration_body_sha256": restoration_ref["payload_sha256"]}
    restoration_receipt_binding = {"role": "mutation_receipt", "service_restoration_body_sha256": restoration_ref["payload_sha256"], "post_create_observation_reference": valid_ref(True, "kubernetes_object_projection")}
    validate_mutation_restoration_binding(service_intent_binding, policy)
    validate_mutation_restoration_binding(restoration_receipt_binding, policy)

    negatives: list[tuple[Any, tuple[Any, ...]]] = []
    add = negatives.append
    add((validate_runtime_document, (mutate(valid_start(), lambda x: x.update({"storage_class": "PAYLOAD_WITH_DESCRIPTOR", "payload_sha256": "2" * 64, "payload_size_bytes": 1, "payload_relative_path": "objects/sha256/22/" + "2" * 64})), schema, policy)))
    add((validate_runtime_document, (mutate(valid_start(), lambda x: x.update({"media_type": "application/octet-stream"})), schema, policy)))
    payload_ref = valid_ref(True, "workload_log_bytes")
    add((validate_runtime_document, (mutate(payload_ref, lambda x: x.update({"payload_relative_path": "objects/sha256/33/" + "3" * 64})), schema, policy)))
    add((validate_runtime_document, (mutate(payload_ref, lambda x: x.update({"payload_size_bytes": policy["roles"]["workload_log_bytes"]["maximum_bytes"] + 1})), schema, policy)))
    add((validate_runtime_document, (mutate(payload_ref, lambda x: x.update({"payload_sha256": ZERO_SHA256, "payload_size_bytes": 1, "payload_relative_path": "objects/sha256/e3/" + ZERO_SHA256})), schema, policy)))
    add((validate_runtime_document, (mutate(valid_start(), lambda x: x.pop("producer")), schema, policy)))
    add((validate_runtime_document, (mutate(valid_start(), lambda x: x.update({"unexpected": True})), schema, policy)))
    add((validate_runtime_document, (mutate(attempt, lambda x: x.update({"run_identities": [x["run_identities"][0]]})), schema, policy)))
    add((validate_runtime_document, (mutate(attempt, lambda x: x.update({"run_identities": [x["run_identities"][0], x["run_identities"][0]]})), schema, policy)))
    add((validate_runtime_document, (mutate(attempt, lambda x: x["run_identities"][1].update({"start_identity_reference": valid_ref(False, "run_identity")})), schema, policy)))
    post_terminal = mutate(attempt, lambda x: x.update({"operations": [{"operation_id": "late", "created_monotonic_ns": 9, "intent_reference": valid_ref(False, "mutation_intent"), "receipt_reference": valid_ref(False, "mutation_receipt")}] }))
    add((validate_runtime_document, (post_terminal, schema, policy)))
    add((validate_runtime_document, (mutate(attempt, lambda x: x.update({"raw_evidence_references": []})), schema, policy)))
    invalid_rejection = mutate(capture_rejection("challenge_stdout", "CAPTURE_REJECTED_STRUCTURED_SECRET"), lambda x: x.update({"matched_bytes": "secret"}))
    add((validate_runtime_document, (invalid_rejection, schema, policy)))
    unrelated_delete = valid_kubernetes_request(policy, "PODS_SOCIAL_READ_AND_GUARDED_MUTATION", "DELETE", "unrelated-workload-pod")
    unrelated_delete["captured_object_name"] = "approved-user-service-pod"
    add((validate_runtime_document, (unrelated_delete, schema, policy)))
    mixed = mutate(list_response, lambda x: x["items"][0].update({"kind": "Service"}))
    add((validate_runtime_document, (mixed, schema, policy)))
    oversized = mutate(list_response, lambda x: x.update({"items": x["items"] * 257}))
    add((validate_runtime_document, (oversized, schema, policy)))
    terminal_descriptor = deepcopy(healthy_descriptor); terminal_descriptor["role"] = "terminal_manifest"
    add((validate_runtime_document, (terminal_descriptor, schema, policy)))
    bad_window = valid_workload_documents(); bad_window[2]["workload_window"]["phase"] = "POST_REPLACEMENT_PERSISTENCE"; bad_window[2]["workload_window"]["ordinal"] = 2
    add((validate_workload_window_consistency, (bad_window,)))
    bad_count = valid_workload_documents(); bad_count[0]["complete_entry_count"] = 3
    add((validate_workload_window_consistency, (bad_count,)))
    bad_body = mutate(restoration, lambda x: x["normalized_service_create_body"]["spec"].update({"clusterIP": "10.96.0.99"}))
    add((validate_runtime_document, (bad_body, schema, policy)))
    add((validate_mutation_restoration_binding, (mutate(service_intent_binding, lambda x: x.update({"service_restoration_body_sha256": "9" * 64})), policy)))
    add((validate_mutation_restoration_binding, (mutate(restoration_receipt_binding, lambda x: x.pop("post_create_observation_reference")), policy)))
    for function, arguments in negatives:
        expect_failure(function, *arguments)
    validate_command(policy, "CHALLENGE_DNS_V1", ["sh", "-c", "nslookup user-service.social-network.svc.cluster.local"])
    expect_failure(validate_command, policy, "CHALLENGE_DNS_V1", ["sh", "-c", "env"])
    expect_failure(validate_command, policy, "UNKNOWN", ["true"])
    return len(positive), len(negatives) + 2


def sensitive_tests() -> tuple[int, int]:
    positives = [
        (b"Authorization: secret", None),
        (b"Bearer abcdefghijklmnop", None),
        (b"-----BEGIN PRIVATE KEY-----", None),
        (b"https://user:password@example.invalid/x", None),
        (b"HTTPS://user:password@example.invalid/", None),
        (b"Authorization: secret\r\n", None),
        (b"TOKEN=value", None),
        (b"A=1\nB=2\nC=3\nD=4", None),
        (b"{}", {"apiVersion": "v1", "kind": "Secret"}),
        (b"{}", {"apiVersion": "v1", "kind": "Config", "clusters": [], "contexts": [], "users": [{"user": {"exec": {"command": "x"}}}]}),
        (b"{}", {"token": "populated"}),
        (b"{}", {"nested": [{"client_secret": "populated"}]}),
        (b"[]", [{"token": "secret-value"}]),
    ]
    negatives = [
        (b"token_count=4", None),
        (b"password_policy_text=long", None),
        (b"automountServiceAccountToken=false", None),
        (b"public certificate fingerprint 00:11", None),
        (b'print("Authorization: example")', None),
        (b"{}", {"username": "ordinary", "exec": "ordinary"}),
        (b"{}", {"nested": [{"username": "ordinary", "exec": "ordinary"}]}),
        (b"A=1\nB=2\nC=3", None),
        (b"TOKEN=", None),
        (b"https://user:@example.invalid/", None),
    ]
    for data, structured in positives:
        code = detect_sensitive(data, structured)
        if code is None:
            raise AssertionError("Sensitive positive fixture was not detected")
        record = capture_rejection("challenge_stdout", code)
        forbidden = {"payload_sha256", "descriptor_sha256", "matched_bytes", "rejected_content_sha256", "absolute_path", "rejected_value"}
        if forbidden & set(record):
            raise AssertionError("Rejected content was hashed or persisted")
    for data, structured in negatives:
        if detect_sensitive(data, structured) is not None:
            raise AssertionError("Sensitive false-positive fixture was rejected")
    return len(positives), len(negatives)


def make_test_layout(base: Path) -> Layout:
    root = base / "repo"
    (root / "tools").mkdir(parents=True)
    generator = root / GENERATOR_REL
    shutil.copyfile(GENERATOR_PATH, generator)
    run_local(["git", "init", "-q", "-b", "main", str(root)], check=True)
    run_local(["git", "-C", str(root), "config", "user.name", "Evidence Policy Test"], check=True)
    run_local(["git", "-C", str(root), "config", "user.email", "evidence-policy-test@example.invalid"], check=True)
    run_local(["git", "-C", str(root), "add", GENERATOR_REL], check=True)
    run_local(["git", "-C", str(root), "commit", "-q", "-m", "test root"], check=True)
    return Layout(root, generator, root / POLICY_REL, root / SCHEMA_REL, root / MANIFEST_REL, root / JOURNAL_REL)


def _verify_local_provenance(repo: Path, anchor: str, file_hashes: dict[str, str], tags: dict[str, str]) -> None:
    verify_descendant_head(repo, anchor)
    if git(repo, "diff", "--name-only", "--") or git(repo, "diff", "--cached", "--name-only", "--"):
        raise RuntimeError("Fixture has tracked drift")
    for relative, digest in file_hashes.items():
        if sha256(repo / relative) != digest:
            raise RuntimeError("Fixture immutable artifact drift")
    for name, object_id in tags.items():
        if git(repo, "cat-file", "-t", f"refs/tags/{name}") != "tag" or git(repo, "rev-parse", f"refs/tags/{name}") != object_id:
            raise RuntimeError("Fixture frozen tag drift")


def descendant_provenance_tests(policy: dict[str, Any], schema: dict[str, Any]) -> dict[str, int]:
    accepted = rejected = 0
    with tempfile.TemporaryDirectory(prefix="sremut-evidence-descendant-", dir="/tmp") as directory:
        repo = Path(directory) / "repo"
        repo.mkdir()
        run_local(["git", "init", "-q", "-b", "main", str(repo)], check=True)
        run_local(["git", "-C", str(repo), "config", "user.name", "Evidence Policy Test"], check=True)
        run_local(["git", "-C", str(repo), "config", "user.email", "evidence-policy-test@example.invalid"], check=True)
        files = {"contract.yaml": b"contract-v1\n", "profile.yaml": b"profile-v1\n", "pyproject.toml": b"project-v1\n", "uv.lock": b"lock-v1\n"}
        for relative, data in files.items():
            (repo / relative).write_bytes(data)
        run_local(["git", "-C", str(repo), "add", "--", *files], check=True)
        run_local(["git", "-C", str(repo), "commit", "-q", "-m", "scaffold"], check=True)
        anchor = git(repo, "rev-parse", "HEAD")
        for name in ("contract-v1", "profile-v1"):
            run_local(["git", "-C", str(repo), "tag", "-a", name, "-m", name, anchor], check=True)
        file_hashes = {relative: sha256(repo / relative) for relative in files}
        tags = {name: git(repo, "rev-parse", f"refs/tags/{name}") for name in ("contract-v1", "profile-v1")}
        _verify_local_provenance(repo, anchor, file_hashes, tags); accepted += 1
        (repo / "evidence-policy.txt").write_bytes(b"corrected artifacts\n")
        run_local(["git", "-C", str(repo), "add", "evidence-policy.txt"], check=True)
        run_local(["git", "-C", str(repo), "commit", "-q", "-m", "evidence policy"], check=True)
        _verify_local_provenance(repo, anchor, file_hashes, tags); accepted += 1
        (repo / "unrelated.txt").write_bytes(b"implementation\n")
        run_local(["git", "-C", str(repo), "add", "unrelated.txt"], check=True)
        run_local(["git", "-C", str(repo), "commit", "-q", "-m", "unrelated descendant"], check=True)
        _verify_local_provenance(repo, anchor, file_hashes, tags); accepted += 1
        run_local(["git", "-C", str(repo), "tag", "-a", "future-evidence-policy-v1", "-m", "future", "HEAD"], check=True)
        if git(repo, "cat-file", "-t", "refs/tags/future-evidence-policy-v1") != "tag":
            raise AssertionError("Future evidence-policy tag is not annotated")
        _verify_local_provenance(repo, anchor, file_hashes, tags); accepted += 1
        non_descendant = git(repo, "commit-tree", git(repo, "rev-parse", f"{anchor}^{{tree}}"), "-m", "non descendant")
        if git_boolean(repo, "merge-base", "--is-ancestor", anchor, non_descendant):
            raise AssertionError("Non-descendant history was accepted")
        rejected += 1
        for relative in ("contract.yaml", "profile.yaml", "pyproject.toml", "uv.lock"):
            original = (repo / relative).read_bytes()
            (repo / relative).write_bytes(original + b"drift\n")
            expect_failure(_verify_local_provenance, repo, anchor, file_hashes, tags); rejected += 1
            (repo / relative).write_bytes(original)
        (repo / "contract.yaml").write_bytes(b"staged drift\n")
        run_local(["git", "-C", str(repo), "add", "contract.yaml"], check=True)
        expect_failure(_verify_local_provenance, repo, anchor, file_hashes, tags); rejected += 1
        run_local(["git", "-C", str(repo), "restore", "--staged", "--worktree", "contract.yaml"], check=True)
        for name in ("contract-v1", "profile-v1"):
            original_object = tags[name]
            run_local(["git", "-C", str(repo), "update-ref", "-d", f"refs/tags/{name}"], check=True)
            expect_failure(_verify_local_provenance, repo, anchor, file_hashes, tags); rejected += 1
            run_local(["git", "-C", str(repo), "update-ref", f"refs/tags/{name}", original_object], check=True)
            run_local(["git", "-C", str(repo), "update-ref", f"refs/tags/{name}", anchor], check=True)
            expect_failure(_verify_local_provenance, repo, anchor, file_hashes, tags); rejected += 1
            run_local(["git", "-C", str(repo), "update-ref", f"refs/tags/{name}", original_object], check=True)
            changed_name = "changed-" + name
            run_local(["git", "-C", str(repo), "tag", "-a", changed_name, "-m", changed_name, "HEAD"], check=True)
            run_local(["git", "-C", str(repo), "update-ref", f"refs/tags/{name}", git(repo, "rev-parse", f"refs/tags/{changed_name}")], check=True)
            expect_failure(_verify_local_provenance, repo, anchor, file_hashes, tags); rejected += 1
            run_local(["git", "-C", str(repo), "update-ref", f"refs/tags/{name}", original_object], check=True)
        expect_failure(git_boolean, repo, "merge-base", "--is-ancestor", "not-a-commit", "HEAD"); rejected += 1
        policy_bytes = render_policy(policy)
        schema_bytes = render_schema(schema)
        manifest_bytes = b"manifest without live identity\n"
        for artifact in (policy_bytes, schema_bytes, manifest_bytes):
            if b"current_head" in artifact or b"current_tree" in artifact or git(repo, "rev-parse", "HEAD").encode() in artifact:
                raise AssertionError("Transaction-time identity leaked into frozen output")
    return {"accepted": accepted, "rejected": rejected}


def subprocess_failure_tests() -> int:
    expect_failure(run_local, ["/bin/sleep", "1"], check=True, timeout_seconds=0.01)
    expect_failure(run_local, ["/definitely/missing/sremut-command"], check=True)
    expect_failure(run_local, [sys.executable, "-c", "import os; os.write(1, b'\\xff')"], check=True)
    with tempfile.TemporaryDirectory(prefix="sremut-git-status-", dir="/tmp") as directory:
        repo = Path(directory)
        run_local(["git", "init", "-q", "-b", "main", str(repo)], check=True)
        expect_failure(git_boolean, repo, "merge-base", "--is-ancestor", "missing-anchor", "HEAD")
    return 4


def crash_recovery_tests(policy: dict[str, Any], schema: dict[str, Any]) -> tuple[int, int, int]:
    passed = 0
    with tempfile.TemporaryDirectory(prefix="sremut-evidence-policy-crash-", dir="/tmp") as directory:
        parent = Path(directory)
        for phase in PHASES:
            layout = make_test_layout(parent / phase.lower())
            policy_bytes = render_policy(policy)
            schema_bytes = render_schema(schema)
            artifacts = {layout.policy: policy_bytes, layout.schema: schema_bytes, layout.manifest: render_manifest(layout, policy_bytes, schema_bytes)}
            try:
                install_transaction(layout, artifacts, "SELF_TEST", phase)
            except InjectedCrash:
                pass
            else:
                raise AssertionError(f"Crash injection did not fire: {phase}")
            if not path_lexists(layout.journal):
                raise AssertionError("Crash phase lacks durable journal")
            expect_failure(lambda: (_ for _ in ()).throw(RuntimeError("unresolved")) if path_lexists(layout.journal) else None)
            recover_transaction(layout, artifacts)
            if path_lexists(layout.journal) or any(read_regular_bytes(path) != data for path, data in artifacts.items()):
                raise AssertionError(f"Recovery failed at {phase}")
            passed += 1
        unsafe_layout = make_test_layout(parent / "unsafe")
        policy_bytes = render_policy(policy)
        schema_bytes = render_schema(schema)
        unsafe_artifacts = {unsafe_layout.policy: policy_bytes, unsafe_layout.schema: schema_bytes, unsafe_layout.manifest: render_manifest(unsafe_layout, policy_bytes, schema_bytes)}
        try:
            install_transaction(unsafe_layout, unsafe_artifacts, "SELF_TEST", "POLICY_INSTALLED")
        except InjectedCrash:
            pass
        unsafe_layout.schema.write_bytes(b"forensic-conflict\n")
        expect_failure(recover_transaction, unsafe_layout, unsafe_artifacts)
        if not path_lexists(unsafe_layout.journal) or read_regular_bytes(unsafe_layout.schema) != b"forensic-conflict\n":
            raise AssertionError("Unsafe recovery did not preserve forensic state")
        symlink_layout = make_test_layout(parent / "symlink")
        parent_fd, _name = open_verified_parent(symlink_layout, symlink_layout.policy, create=True)
        os.close(parent_fd)
        generator_before = sha256(symlink_layout.generator)
        os.symlink(symlink_layout.generator, symlink_layout.policy)
        symlink_artifacts = {symlink_layout.policy: policy_bytes, symlink_layout.schema: schema_bytes, symlink_layout.manifest: render_manifest(symlink_layout, policy_bytes, schema_bytes)}
        expect_failure(install_transaction, symlink_layout, symlink_artifacts, "SELF_TEST")
        if not symlink_layout.policy.is_symlink() or sha256(symlink_layout.generator) != generator_before or path_lexists(symlink_layout.journal):
            raise AssertionError("Symlink artifact target refusal changed protected state")
        # Every relevant parent component is opened with O_NOFOLLOW; outside state remains byte-identical.
        component_cases = ("policies", "nested-policy", "schemas", "journal-parent")
        for case in component_cases:
            layout = make_test_layout(parent / ("component-" + case))
            outside = parent / ("outside-" + case)
            outside.mkdir()
            sentinel = outside / "sentinel"
            sentinel.write_bytes(b"outside-unchanged\n")
            if case == "policies":
                os.symlink(outside, layout.root / "policies")
            elif case == "nested-policy":
                (layout.root / "policies").mkdir()
                os.symlink(outside, layout.root / "policies" / "missing_service_social_network")
            elif case == "schemas":
                os.symlink(outside, layout.root / "schemas")
            else:
                os.symlink(outside, layout.root / "transactions")
                layout = Layout(layout.root, layout.generator, layout.policy, layout.schema, layout.manifest, layout.root / "transactions" / "state.json")
            artifacts = {layout.policy: policy_bytes, layout.schema: schema_bytes, layout.manifest: render_manifest(layout, policy_bytes, schema_bytes)}
            expect_failure(install_transaction, layout, artifacts, "SELF_TEST")
            if sentinel.read_bytes() != b"outside-unchanged\n" or len(list(outside.iterdir())) != 1:
                raise AssertionError(f"Parent-symlink refusal changed outside state: {case}")
        recovery_layout = make_test_layout(parent / "recovery-parent-symlink")
        recovery_artifacts = {recovery_layout.policy: policy_bytes, recovery_layout.schema: schema_bytes, recovery_layout.manifest: render_manifest(recovery_layout, policy_bytes, schema_bytes)}
        try:
            install_transaction(recovery_layout, recovery_artifacts, "SELF_TEST", "PREPARED")
        except InjectedCrash:
            pass
        recovery_outside = parent / "outside-recovery"
        recovery_outside.mkdir()
        recovery_sentinel = recovery_outside / "sentinel"
        recovery_sentinel.write_bytes(b"outside-unchanged\n")
        os.rename(recovery_layout.root / "policies", recovery_layout.root / ".policies-forensic")
        os.symlink(recovery_outside, recovery_layout.root / "policies")
        expect_failure(recover_transaction, recovery_layout, recovery_artifacts)
        if recovery_sentinel.read_bytes() != b"outside-unchanged\n" or len(list(recovery_outside.iterdir())) != 1:
            raise AssertionError("Recovery parent-symlink refusal changed outside state")
    return passed, 1, 6



# Frozen full-admissibility model.  The public schema remains the structural level;
# only the dispatcher below can return an admissible result.
def frozen_hook_contracts() -> list[dict[str, Any]]:
    scopes = {
        "VALIDATE_CANONICAL_NO_FLOATS_V1": "DOCUMENT_LOCAL",
        "VALIDATE_EVIDENCE_REF_HASH_PATH_ID_V1": "CROSS_DOCUMENT",
        "VALIDATE_DESCRIPTOR_CONTENT_IDENTITY_V1": "BYTE_DEPENDENT",
        "VALIDATE_ATTEMPT_PHASES_AND_FINALITY_V1": "CROSS_RECORD",
        "VALIDATE_WORKLOAD_CARDINALITY_V1": "BYTE_DEPENDENT",
        "VALIDATE_WORKLOAD_WINDOW_CONSISTENCY_V1": "CROSS_DOCUMENT",
        "VALIDATE_ADJUDICATION_RAW_BACKING_V1": "BYTE_DEPENDENT",
        "VALIDATE_JOURNAL_HASH_CHAIN_V1": "CROSS_RECORD",
        "VALIDATE_KUBERNETES_REQUEST_V1": "CROSS_DOCUMENT",
        "VALIDATE_KUBERNETES_RESPONSE_V1": "BYTE_DEPENDENT",
        "VALIDATE_SERVICE_RESTORATION_BODY_V1": "BYTE_DEPENDENT",
        "VALIDATE_SENSITIVE_CAPTURE_V1": "BYTE_DEPENDENT",
    }
    failure_codes = {
        "VALIDATE_CANONICAL_NO_FLOATS_V1": ["CANONICAL_FLOAT_FORBIDDEN"],
        "VALIDATE_EVIDENCE_REF_HASH_PATH_ID_V1": ["EVIDENCE_REFERENCE_INVALID", "EVIDENCE_REFERENCE_UNRESOLVED"],
        "VALIDATE_DESCRIPTOR_CONTENT_IDENTITY_V1": ["DESCRIPTOR_BYTES_MISSING", "DESCRIPTOR_HASH_MISMATCH", "DESCRIPTOR_CANONICAL_MISMATCH", "DESCRIPTOR_REFERENCE_MISMATCH", "PAYLOAD_BYTES_MISSING", "PAYLOAD_HASH_MISMATCH", "PAYLOAD_SIZE_MISMATCH", "PUBLICATION_RECORD_MISSING", "PUBLICATION_ORDER_INVALID", "RUN_ATTEMPT_MISMATCH"],
        "VALIDATE_ATTEMPT_PHASES_AND_FINALITY_V1": ["ATTEMPT_FINALITY_INVALID", "POST_TERMINAL_OPERATION", "STATE_OPERATION_FORBIDDEN"],
        "VALIDATE_WORKLOAD_CARDINALITY_V1": ["WORKLOAD_CARDINALITY_INVALID", "WORKLOAD_TIMESTAMP_ORDER_INVALID", "WORKLOAD_ENTRY_ORDER_INVALID"],
        "VALIDATE_WORKLOAD_WINDOW_CONSISTENCY_V1": ["WORKLOAD_WINDOW_MISMATCH", "WORKLOAD_RAW_PREFIX_MISMATCH", "WORKLOAD_POD_IDENTITY_MISMATCH", "WORKLOAD_RESTART_COUNT_MISMATCH", "WORKLOAD_EVALUATION_CONTEXT_MISMATCH", "ADJUDICATION_DEADLINE_MISMATCH", "RUN_ATTEMPT_MISMATCH"],
        "VALIDATE_ADJUDICATION_RAW_BACKING_V1": ["ADJUDICATION_RAW_REFERENCE_REQUIRED", "EVIDENCE_REFERENCE_UNRESOLVED", "ADJUDICATION_RAW_ROLE_INVALID", "PAYLOAD_BYTES_MISSING", "PAYLOAD_HASH_MISMATCH", "PAYLOAD_SIZE_MISMATCH", "PUBLICATION_ORDER_INVALID", "RUN_ATTEMPT_MISMATCH", "ADJUDICATION_DEADLINE_MISMATCH", "ADJUDICATION_EVALUATION_PHASE_MISMATCH"],
        "VALIDATE_JOURNAL_HASH_CHAIN_V1": ["JOURNAL_CHAIN_INVALID", "PUBLICATION_RECORD_MISSING", "PUBLICATION_ORDER_INVALID"],
        "VALIDATE_KUBERNETES_REQUEST_V1": ["KUBERNETES_REQUEST_RULE_INVALID", "KUBERNETES_CAPTURE_UNRESOLVED", "KUBERNETES_CAPTURE_IDENTITY_MISMATCH", "KUBERNETES_CHALLENGE_IDENTITY_MISMATCH", "KUBERNETES_CHALLENGE_BODY_INVALID", "RESTORATION_INTENT_UNRESOLVED", "RESTORATION_INTENT_ROLE_INVALID", "RESTORATION_OPERATION_MISMATCH", "RESTORATION_TARGET_MISMATCH", "RESTORATION_UID_MISMATCH", "RESTORATION_REFERENCE_MISSING", "RESTORATION_BODY_UNRESOLVED", "RESTORATION_BODY_HASH_MISMATCH", "RESTORATION_SERVICE_MISMATCH", "RESTORATION_PREPUBLICATION_FAILURE", "STATE_OPERATION_FORBIDDEN", "POST_TERMINAL_OPERATION"],
        "VALIDATE_KUBERNETES_RESPONSE_V1": ["KUBERNETES_PROJECTION_FIELD_FORBIDDEN", "KUBERNETES_PROJECTION_REQUEST_MISMATCH", "KUBERNETES_LIST_INVALID", "RUN_ATTEMPT_MISMATCH"],
        "VALIDATE_SERVICE_RESTORATION_BODY_V1": ["SERVICE_RESTORATION_BODY_INVALID", "RESTORATION_BODY_HASH_MISMATCH", "RESTORATION_SERVICE_MISMATCH", "SENSITIVE_CAPTURE_REJECTED"],
        "VALIDATE_SENSITIVE_CAPTURE_V1": ["SENSITIVE_CAPTURE_REJECTED"],
    }
    candidate_fields = {
        "VALIDATE_CANONICAL_NO_FLOATS_V1": ["complete candidate document"],
        "VALIDATE_EVIDENCE_REF_HASH_PATH_ID_V1": ["every recursively contained EvidenceRef"],
        "VALIDATE_DESCRIPTOR_CONTENT_IDENTITY_V1": ["document_type", "role", "producer", "source_kind", "media_type", "storage_class"],
        "VALIDATE_ATTEMPT_PHASES_AND_FINALITY_V1": ["run_id", "attempt_id", "terminal state and operations when applicable"],
        "VALIDATE_WORKLOAD_CARDINALITY_V1": ["workload_window_adjudication_identity or workload_window"],
        "VALIDATE_WORKLOAD_WINDOW_CONSISTENCY_V1": ["workload_window_adjudication_identity or workload_window"],
        "VALIDATE_ADJUDICATION_RAW_BACKING_V1": ["predicate_oracle_or_classification_id", "result_type", "boolean_or_categorical_value", "applicable_deadline", "raw_evidence_references"],
        "VALIDATE_JOURNAL_HASH_CHAIN_V1": ["journal records or authoritative resolved attempt journal"],
        "VALIDATE_KUBERNETES_REQUEST_V1": ["request_rule_id", "operation", "resource", "namespace", "name", "captured_object_reference", "intent_reference"],
        "VALIDATE_KUBERNETES_RESPONSE_V1": ["request", "list_metadata", "items"],
        "VALIDATE_SERVICE_RESTORATION_BODY_V1": ["source_service_uid", "source_service_resource_version", "normalized_body_sha256", "normalized_service_create_body"],
        "VALIDATE_SENSITIVE_CAPTURE_V1": ["exact resolved descriptor and payload bytes when present"],
    }
    contracts = []
    for order, hook_id in enumerate(RUNTIME_VALIDATOR_HOOKS, 1):
        scope = scopes[hook_id]
        contracts.append({
            "hook_id": hook_id,
            "order": order,
            "version": 1,
            "applicable_document_kinds": [row["document_kind"] for row in frozen_hook_applicability_matrix() if hook_id in row["hooks"]],
            "applicable_roles": sorted({role for row in frozen_hook_applicability_matrix() if hook_id in row["hooks"] for role in row["roles"]}),
            "required_candidate_fields": candidate_fields[hook_id],
            "required_policy_sections": ["full_admissibility_validation", "runtime_validation"],
            "required_resolved_descriptors": ["every applicable EvidenceRef descriptor and exact descriptor bytes"] if scope in ("CROSS_DOCUMENT", "CROSS_RECORD", "BYTE_DEPENDENT") else [],
            "required_resolved_payload_bytes": ["every applicable payload-backed EvidenceRef exact payload bytes"] if scope == "BYTE_DEPENDENT" else [],
            "required_journal_state_context": ["journal publication records", "authoritative ordered attempt journal", "current verified attempt state"] if scope in ("CROSS_RECORD", "BYTE_DEPENDENT") or hook_id in ("VALIDATE_KUBERNETES_REQUEST_V1", "VALIDATE_ADJUDICATION_RAW_BACKING_V1") else [],
            "success_output": {"document_type": FULL_ADMISSIBILITY_RESULT_DOCUMENT, "schema_version": 1, "valid": True, "dispatcher_id": FULL_ADMISSIBILITY_DISPATCHER_ID, "hook_id": None, "failure_code": None, "subject_evidence_id": None},
            "failure_codes": failure_codes[hook_id],
            "missing_input_behavior": "FAIL_CLOSED_WITH_FIRST_APPLICABLE_STABLE_FAILURE_CODE",
            "validation_scope": scope,
            "authenticated_policy_required": True,
            "canonical_journal_bytes_required": scope in ("CROSS_RECORD", "BYTE_DEPENDENT") or hook_id in ("VALIDATE_KUBERNETES_REQUEST_V1", "VALIDATE_ADJUDICATION_RAW_BACKING_V1"),
            "validation_modes": [CAPTURE_TIME_VALIDATION_LEVEL, OFFLINE_VALIDATION_LEVEL],
        })
    additions = {
        "VALIDATE_ATTEMPT_PHASES_AND_FINALITY_V1": [
            "JOURNAL_BYTES_MISSING", "JOURNAL_CANONICALIZATION_INVALID", "JOURNAL_STATE_DERIVATION_FAILED",
            "JOURNAL_CONTEXT_MISMATCH", "JOURNAL_EVALUATION_MARKER_MISSING", "JOURNAL_OPERATION_MARKER_MISSING",
        ],
        "VALIDATE_WORKLOAD_CARDINALITY_V1": [
            "WORKLOAD_PARSE_SCHEMA_INVALID", "WORKLOAD_PARSE_RECOMPUTATION_MISMATCH",
            "WORKLOAD_PARSE_REFERENCE_MISMATCH", "WORKLOAD_PARSER_IDENTITY_MISMATCH",
        ],
        "VALIDATE_WORKLOAD_WINDOW_CONSISTENCY_V1": [
            "JOURNAL_EVALUATION_MARKER_MISSING", "JOURNAL_CONTEXT_MISMATCH",
            "WORKLOAD_PARSE_SCHEMA_INVALID", "WORKLOAD_PARSE_RECOMPUTATION_MISMATCH",
            "WORKLOAD_PARSE_REFERENCE_MISMATCH", "WORKLOAD_PARSER_IDENTITY_MISMATCH",
        ],
        "VALIDATE_ADJUDICATION_RAW_BACKING_V1": [
            "JOURNAL_EVALUATION_MARKER_MISSING", "JOURNAL_CONTEXT_MISMATCH",
        ],
        "VALIDATE_JOURNAL_HASH_CHAIN_V1": [
            "JOURNAL_BYTES_MISSING", "JOURNAL_CANONICALIZATION_INVALID",
            "JOURNAL_STATE_DERIVATION_FAILED", "JOURNAL_CONTEXT_MISMATCH",
        ],
        "VALIDATE_KUBERNETES_REQUEST_V1": [
            "JOURNAL_OPERATION_MARKER_MISSING", "JOURNAL_CONTEXT_MISMATCH",
            "KUBERNETES_CAPTURE_CLASS_INVALID", "KUBERNETES_CAPTURE_STATE_INVALID",
            "KUBERNETES_CAPTURE_OWNER_INVALID", "KUBERNETES_CAPTURE_SELECTION_INVALID",
            "KUBERNETES_REQUEST_IDENTITY_MISMATCH", "RESTORATION_SOURCE_REFERENCE_MISSING",
            "RESTORATION_SOURCE_UNRESOLVED", "RESTORATION_SOURCE_CLASS_INVALID",
            "RESTORATION_DERIVATION_MISMATCH",
        ],
        "VALIDATE_KUBERNETES_RESPONSE_V1": [
            "KUBERNETES_REQUEST_IDENTITY_MISMATCH", "KUBERNETES_SOURCE_PROJECTION_MISMATCH",
            "KUBERNETES_LIST_RESOURCE_VERSION_MISMATCH",
        ],
        "VALIDATE_SERVICE_RESTORATION_BODY_V1": [
            "RESTORATION_SOURCE_REFERENCE_MISSING", "RESTORATION_SOURCE_UNRESOLVED",
            "RESTORATION_SOURCE_CLASS_INVALID", "RESTORATION_DERIVATION_MISMATCH",
            "MUTATION_RECEIPT_IDENTITY_INVALID", "MUTATION_RECEIPT_REQUEST_MISMATCH",
            "MUTATION_RECEIPT_PUBLICATION_INVALID",
        ],
    }
    for contract in contracts:
        contract["failure_codes"] = list(dict.fromkeys(contract["failure_codes"] + additions.get(contract["hook_id"], [])))
    return contracts


def frozen_hook_applicability_matrix() -> list[dict[str, Any]]:
    storage = role_specs([], [], [])
    payload_roles = [role for role in ROLE_NAMES[:-1] if storage[role]["storage_class"] == "PAYLOAD_WITH_DESCRIPTOR"]
    descriptor_roles = [role for role in ROLE_NAMES[:-1] if storage[role]["storage_class"] == "DESCRIPTOR_ONLY"]
    base = ["VALIDATE_CANONICAL_NO_FLOATS_V1", "VALIDATE_EVIDENCE_REF_HASH_PATH_ID_V1", "VALIDATE_DESCRIPTOR_CONTENT_IDENTITY_V1"]
    rows = [
        {"document_kind": "EVIDENCE_POLICY_DOCUMENT_V1", "roles": ["NONE"], "hooks": ["VALIDATE_CANONICAL_NO_FLOATS_V1"]},
        {"document_kind": "PAYLOAD_EVIDENCE_REF_V1", "roles": payload_roles, "hooks": base[:2]},
        {"document_kind": "DESCRIPTOR_EVIDENCE_REF_V1", "roles": descriptor_roles, "hooks": base[:2]},
        {"document_kind": "PAYLOAD_EVIDENCE_DESCRIPTOR_V1", "roles": [r for r in payload_roles if r not in ("kubernetes_object_projection", "workload_log_bytes", "workload_parse_result")], "hooks": base + ["VALIDATE_SENSITIVE_CAPTURE_V1"]},
        {"document_kind": "PAYLOAD_EVIDENCE_DESCRIPTOR_V1", "roles": ["kubernetes_object_projection"], "hooks": base + ["VALIDATE_KUBERNETES_RESPONSE_V1", "VALIDATE_SERVICE_RESTORATION_BODY_V1", "VALIDATE_SENSITIVE_CAPTURE_V1"]},
        {"document_kind": "PAYLOAD_EVIDENCE_DESCRIPTOR_V1", "roles": ["workload_log_bytes", "workload_parse_result"], "hooks": base + ["VALIDATE_WORKLOAD_CARDINALITY_V1", "VALIDATE_WORKLOAD_WINDOW_CONSISTENCY_V1", "VALIDATE_SENSITIVE_CAPTURE_V1"]},
        {"document_kind": "DESCRIPTOR_EVIDENCE_DESCRIPTOR_V1", "roles": [r for r in descriptor_roles if r not in ("run_identity", "kubernetes_request_identity", "workload_boundary", "mutation_intent", "mutation_receipt", "adjudication")], "hooks": base},
        {"document_kind": "DESCRIPTOR_EVIDENCE_DESCRIPTOR_V1", "roles": ["run_identity"], "hooks": base + ["VALIDATE_ATTEMPT_PHASES_AND_FINALITY_V1"]},
        {"document_kind": "DESCRIPTOR_EVIDENCE_DESCRIPTOR_V1", "roles": ["kubernetes_request_identity"], "hooks": base + ["VALIDATE_KUBERNETES_REQUEST_V1"]},
        {"document_kind": "DESCRIPTOR_EVIDENCE_DESCRIPTOR_V1", "roles": ["workload_boundary"], "hooks": base + ["VALIDATE_WORKLOAD_CARDINALITY_V1", "VALIDATE_WORKLOAD_WINDOW_CONSISTENCY_V1"]},
        {"document_kind": "DESCRIPTOR_EVIDENCE_DESCRIPTOR_V1", "roles": ["mutation_intent", "mutation_receipt"], "hooks": base + ["VALIDATE_SERVICE_RESTORATION_BODY_V1"]},
        {"document_kind": "DESCRIPTOR_EVIDENCE_DESCRIPTOR_V1", "roles": ["adjudication"], "hooks": base + ["VALIDATE_WORKLOAD_CARDINALITY_V1", "VALIDATE_WORKLOAD_WINDOW_CONSISTENCY_V1", "VALIDATE_ADJUDICATION_RAW_BACKING_V1"]},
        {"document_kind": "ATTEMPT_VALIDATION_ENVELOPE_V1", "roles": ["NONE"], "hooks": ["VALIDATE_CANONICAL_NO_FLOATS_V1", "VALIDATE_EVIDENCE_REF_HASH_PATH_ID_V1", "VALIDATE_ATTEMPT_PHASES_AND_FINALITY_V1", "VALIDATE_ADJUDICATION_RAW_BACKING_V1", "VALIDATE_JOURNAL_HASH_CHAIN_V1"]},
        {"document_kind": "JOURNAL_RECORD_V1", "roles": ["NONE"], "hooks": ["VALIDATE_CANONICAL_NO_FLOATS_V1", "VALIDATE_JOURNAL_HASH_CHAIN_V1"]},
        {"document_kind": "KUBERNETES_REQUEST_V1", "roles": ["NONE"], "hooks": ["VALIDATE_CANONICAL_NO_FLOATS_V1", "VALIDATE_EVIDENCE_REF_HASH_PATH_ID_V1", "VALIDATE_KUBERNETES_REQUEST_V1", "VALIDATE_SERVICE_RESTORATION_BODY_V1"]},
        {"document_kind": "KUBERNETES_LIST_RESPONSE_V1", "roles": ["NONE"], "hooks": ["VALIDATE_CANONICAL_NO_FLOATS_V1", "VALIDATE_EVIDENCE_REF_HASH_PATH_ID_V1", "VALIDATE_KUBERNETES_RESPONSE_V1", "VALIDATE_SENSITIVE_CAPTURE_V1"]},
        {"document_kind": "SERVICE_RESTORATION_BODY_V1", "roles": ["NONE"], "hooks": ["VALIDATE_CANONICAL_NO_FLOATS_V1", "VALIDATE_EVIDENCE_REF_HASH_PATH_ID_V1", "VALIDATE_SERVICE_RESTORATION_BODY_V1", "VALIDATE_SENSITIVE_CAPTURE_V1"]},
        {"document_kind": "WORKLOAD_PARSE_RESULT_V1", "roles": ["NONE"], "hooks": ["VALIDATE_CANONICAL_NO_FLOATS_V1", "VALIDATE_EVIDENCE_REF_HASH_PATH_ID_V1", "VALIDATE_WORKLOAD_CARDINALITY_V1", "VALIDATE_WORKLOAD_WINDOW_CONSISTENCY_V1"]},
        {"document_kind": "TERMINAL_GLOBAL_STOP_V1", "roles": ["NONE"], "hooks": ["VALIDATE_CANONICAL_NO_FLOATS_V1", "VALIDATE_EVIDENCE_REF_HASH_PATH_ID_V1", "VALIDATE_ATTEMPT_PHASES_AND_FINALITY_V1"]},
    ]
    return rows


_legacy_build_policy = build_policy


def build_policy(frozen_at: str, contract: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    policy = _legacy_build_policy(frozen_at, contract, profile)
    if "entry_indexes" not in policy["roles"]["workload_log_bytes"]["required_metadata"]:
        policy["roles"]["workload_log_bytes"]["required_metadata"].append("entry_indexes")
    policy["workload_evidence_protocol"]["entry_index_identity"] = "zero-based integer index in authoritative log_history order"
    policy["workload_evidence_protocol"]["window_identity"]["descriptor_required_fields"] = ["phase", "ordinal", "run_id", "attempt_id", "mutant_id", "repetition"]
    policy["workload_evidence_protocol"]["window_identity"]["adjudication_required_fields"] = deepcopy(policy["workload_evidence_protocol"]["window_identity"]["required_fields"])
    policy["workload_evidence_protocol"]["window_identity"]["content_address_cycle_forbidden"] = True
    policy["challenge_request_binding"] = deepcopy(profile["challenge_pod"])
    policy["verified_attempt_state_machine"] = deepcopy(profile["state_machine"])
    for rule in policy["kubernetes_evidence_surface"]["resource_rules"]:
        for selector in rule["label_selectors"]:
            if selector and "=" in selector:
                selector_path = ["metadata", "labels", selector.split("=", 1)[0]]
                if selector_path not in rule["projection_paths"]:
                    rule["projection_paths"].append(selector_path)
    policy["runtime_validation"].update({
        "validation_levels": [STRUCTURAL_VALIDATION_LEVEL, FULL_VALIDATION_LEVEL],
        "structural_schema_validation_scope": "Draft 2020-12 document structure only; never sufficient for evidence admissibility",
        "structural_schema_validation_authorities": [],
        "full_admissibility_dispatcher_id": FULL_ADMISSIBILITY_DISPATCHER_ID,
        "full_admissibility_is_only_admissible_result": True,
        "silent_structural_downgrade_forbidden": True,
    })
    policy["full_admissibility_validation"] = {
        "schema_version": 1,
        "dispatcher_id": FULL_ADMISSIBILITY_DISPATCHER_ID,
        "candidate_count": 1,
        "inputs": ["one candidate runtime document", "exact frozen policy", "closed resolved-evidence context"],
        "algorithm": ["validate the public Draft 2020-12 schema branch", "validate and authenticate the complete resolved-evidence context", "select every applicable hook from the closed matrix", "invoke hooks in exact ascending order", "require every applicable hook to succeed", "return one closed validation result"],
        "missing_context_or_unresolved_evidence": "FAIL_CLOSED",
        "structural_only_result_is_never_admissible": True,
        "unknown_role_or_document_combination": "UNKNOWN_DOCUMENT_ROLE",
        "unexpected_validator_error": "VALIDATOR_EXECUTION_FAILURE_WITHOUT_REJECTED_CONTENT",
        "result_document": FULL_ADMISSIBILITY_RESULT_DOCUMENT,
        "resolved_context_document": RESOLVED_CONTEXT_DOCUMENT,
        "failure_codes": list(VALIDATION_FAILURE_CODES),
        "hook_contracts": frozen_hook_contracts(),
        "hook_applicability_matrix": frozen_hook_applicability_matrix(),
        "adjudication_predicate_raw_role_context_deadline_matrix": deepcopy(EVALUATION_CONTEXTS),
        "service_delete_operation_kinds": list(SERVICE_DELETE_OPERATION_KINDS),
        "service_create_operation_kinds": list(SERVICE_CREATE_OPERATION_KINDS),
        "caller_assertions_are_never_authoritative": ["name", "uid", "resourceVersion", "hash", "phase", "role", "evaluation context", "operation context"],
        "deterministic_challenge_name_seed_hash_domain": "canonical compact sorted JSON containing exact derived labels, metadata.namespace, and Pod spec; metadata.name excluded to prevent a content-address cycle",
        "profile_challenge_canonical_body_hash_domain": "canonical compact sorted JSON containing derived metadata.name, metadata.namespace, exact labels, and Pod spec, preserving execution-profile spec_hash semantics",
        "deterministic_challenge_name_derivation": "sremut-challenge- plus first16 lowercase SHA-256 hex of canonical compact sorted JSON containing exact run_id, mutant_id, repetition, attempt_id, and frozen_spec_sha256",
        "runner_conformance_requirement": "Future runner validation MUST be byte-for-byte behaviorally equivalent to this generator reference implementation and its stable failure-code semantics and MUST NOT import mutable external policy logic",
    }
    receipt_metadata = list(dict.fromkeys(list(COMMON_METADATA) + profile["mutation_operation_protocol"]["receipt"]["required_fields"]))
    policy["roles"]["mutation_receipt"]["required_metadata"] = receipt_metadata
    request_metadata = list(dict.fromkeys(
        list(COMMON_METADATA)
        + policy["roles"]["kubernetes_request_identity"]["required_metadata"]
        + ["canonical_request", "request_identity_sha256"]
    ))
    policy["roles"]["kubernetes_request_identity"]["required_metadata"] = request_metadata
    policy["roles"]["kubernetes_object_projection"]["conditional_required_metadata"] = [
        "projection_schema_id", "capture_state", "replicaset_projection_reference",
        "deployment_projection_reference", "selection_evidence_reference",
        "trusted_source_sha256", "source_list_resource_version",
    ]
    policy["roles"]["mutation_receipt"]["conditional_required_metadata"] = [
        "operation_request_reference", "service_restoration_body_reference",
        "service_restoration_body_sha256", "post_create_observation_reference",
    ]
    policy["expanded_frozen_metadata"]["mutation_receipt_required_metadata"] = receipt_metadata
    policy["threat_and_trust_model"] = {
        "model_id": "SREMUT_EVIDENCE_TRUST_MODEL_V1",
        "provided_guarantees": [
            "deterministic capture", "least-data evidence projection", "structural and semantic validation",
            "content-addressed integrity", "journal ordering and finality", "detection of accidental corruption",
            "detection of missing or mismatched references",
            "detection of post-publication tampering relative to an externally anchored terminal-manifest hash",
            "reproducible adjudication from retained evidence",
        ],
        "capture_time_trusted_computing_base": [
            "checksum-verified runner release", "frozen evidence policy and schema", "pinned runtime dependencies",
            "capture adapters", "host kernel and filesystem durable-publication primitives",
            "configured Kubernetes API and TLS connection", "pinned SREGym workload and oracle implementation",
        ],
        "explicitly_out_of_scope": [
            "malicious runner or compromised host fabricating an entirely self-consistent false evidence universe before sealing",
            "compromised Kubernetes API server returning false state",
            "attacker replacing the external terminal-manifest anchor and every release identity",
        ],
        "forbidden_claims": [
            "cryptographic attestation", "trusted execution", "Kubernetes API-server signatures",
            "malicious-runner resistance",
        ],
    }
    policy["validation_modes"] = {
        CAPTURE_TIME_VALIDATION_LEVEL: {
            "authenticated_inputs": "in-process source objects and exact bytes supplied by trusted frozen adapters",
            "requirements": [
                "validate source-to-projection derivation before publication",
                "validate sensitive-material policy",
                "publish immutable evidence and journal records",
            ],
        },
        OFFLINE_VALIDATION_LEVEL: {
            "authenticated_inputs": "sealed attempt tree plus externally recorded terminal-manifest SHA-256",
            "requirements": [
                "verify external manifest anchor", "verify every covered path and hash", "verify canonical journal",
                "verify descriptors, payloads, references, and adjudications",
            ],
            "unretained_live_cluster_truth_reconstruction_claimed": False,
        },
    }
    policy["authenticated_policy_input"] = {
        "document_type": AUTHENTICATED_POLICY_INPUT_DOCUMENT,
        "manifest_format": "three sorted sha256sum-compatible lines: generator, policy, schema",
        "expected_entry_paths": [GENERATOR_REL, POLICY_REL, SCHEMA_REL],
        "required_exact_bytes": [POLICY_REL, SCHEMA_REL, MANIFEST_REL],
        "expected_manifest_sha256_source": "frozen runner release or generator conformance oracle",
        "caller_policy_or_hook_override_forbidden": True,
        "authentication_algorithm": [
            "verify manifest SHA-256", "parse exactly three canonical manifest rows",
            "verify exact policy and schema bytes", "parse policy and schema",
            "validate policy through authenticated schema",
            "extract hook contracts and applicability only from authenticated policy bytes",
            "reject supplied parsed-content, hook-contract, matrix, or hook-order disagreement",
        ],
    }
    policy["runner_release_binding"]["required_evidence_policy_fields"] = [
        "evidence_policy_annotated_tag", "evidence_policy_checksum_manifest_sha256",
        "evidence_policy_sha256", "evidence_policy_schema_sha256", "dispatcher_id",
    ]
    policy["authoritative_journal_derivation"] = {
        "input": "exact canonical JSON-lines journal bytes",
        "algorithm": [
            "strict UTF-8 decode", "canonical JSON line verification", "closed record schema validation",
            "sequence and hash-chain verification", "run and attempt verification",
            "derive current and terminal state from verified state-transition records",
            "derive operation from OPERATION_AUTHORIZED canonical-JSON-hex marker",
            "derive evaluation predicate from EVALUATION_AUTHORIZED marker",
            "derive phase and deadline from authenticated policy mapping",
            "reject every caller convenience-context disagreement",
        ],
        "caller_verified_state_authoritative": False,
    }
    policy["service_restoration_source_binding"] = {
        "projection_class": "SERVICE_RESTORATION_SOURCE_V1",
        "source_role": "kubernetes_object_projection",
        "source_payload": "complete sensitive-screened allowlisted core/v1 Service capture including fields stripped from CREATE",
        "derivation_algorithm": "resolve exact source bytes; apply authenticated exact_stripped_field_token_paths; canonicalize; compare exact normalized bytes and SHA-256",
        "required_order": ["source capture", "normalized restoration body", "mutation intent", "destructive request"],
        "fabricated_source_and_body_by_malicious_runner": "OUT_OF_SCOPE",
    }
    policy["kubernetes_captured_identity_binding"] = {
        "rules": {
            "CAPTURED_OBJECT_NAME": {
                "allowed_projection_schema_ids": ["SERVICE_RESTORATION_SOURCE_V1", "POD_IDENTITY_CAPTURE_V1"],
                "allowed_capture_states": ["HEALTHY_STATE_CAPTURED", "MUTANT_STATE_VERIFIED", "ORIGINAL_ORACLE_EVALUATED", "CONTRACT_EVALUATED", "RESTORE_STARTED"],
                "uid_comparison_required": True, "resource_version_comparison_required": True,
            },
            "CAPTURED_REPLACEMENT_POD_NAME": {
                "allowed_projection_schema_ids": ["POD_IDENTITY_CAPTURE_V1"],
                "allowed_capture_states": ["HEALTHY_STATE_CAPTURED", "CONTRACT_EVALUATED", "RESTORE_STARTED"],
                "uid_comparison_required": True, "resource_version_comparison_required": True,
            },
            "DETERMINISTIC_CHALLENGE_NAME": {
                "allowed_projection_schema_ids": ["KUBERNETES_OBJECT_PROJECTION_V1"],
                "allowed_capture_states": ["ORIGINAL_ORACLE_EVALUATED", "CONTRACT_EVALUATED", "RESTORE_STARTED"],
                "uid_comparison_required": False, "resource_version_comparison_required": False,
            },
        },
        "pod_owner_chain": ["Pod", "ReplicaSet", "Deployment/user-service"],
        "pod_projection_classes": ["POD_IDENTITY_CAPTURE_V1", "REPLICASET_OWNER_CAPTURE_V1", "DEPLOYMENT_OWNER_CAPTURE_V1"],
        "required_pod_descriptor_bindings": [
            "replicaset_projection_reference", "deployment_projection_reference",
            "selection_evidence_reference", "capture_state",
        ],
        "caller_name_uid_state_authoritative": False,
    }
    policy["kubernetes_request_response_binding"] = {
        "request_identity_algorithm": "SHA-256 of canonical compact sorted JSON over every field of KUBERNETES_REQUEST_V1",
        "request_identity_fields": [
            "api_group", "api_version", "resource", "subresource", "operation", "context", "namespace",
            "name", "label_selector", "field_selector", "request_options", "purpose", "consumer",
            "projection_class", "captured_object_name", "captured_object_uid",
            "captured_object_resource_version", "captured_object_reference", "uid_precondition", "authorized_operation_kind", "intent_reference",
            "run_id", "attempt_id",
        ],
        "capture_time_source_required": True,
        "offline_source_truth_claimed": False,
        "list_resource_version_source_binding_required": True,
    }
    policy["workload_parse_result_protocol"] = {
        "document_type": "WORKLOAD_PARSE_RESULT_V1",
        "payload_schema": "closed canonical JSON defined by public schema",
        "parser_identity": {
            "sregym_commit": SREGYM_COMMIT, "module_path": WORKLOAD_PARSER_MODULE,
            "source_sha256": WORKLOAD_PARSER_SOURCE_SHA256, "python_runtime": "3.12.3",
            "algorithm": WORKLOAD_PARSE_ALGORITHM, "version": 1,
        },
        "raw_serialization": "one canonical compact JSON WorkloadEntry per UTF-8 line with index, time_binary64_hex, number, log, and ok",
        "recompute_fields": [
            "complete_entry_count", "entry_indexes", "entry_time_ieee754_binary64_hex",
            "fresh_request_count", "failure_marker_count", "prefix_byte_length",
            "prefix_sha256", "suffix_sha256",
        ],
        "parse_before_adjudication_required": True,
    }
    policy["offline_external_seal"] = {
        "document_type": OFFLINE_SEAL_INPUT_DOCUMENT,
        "manifest_format": "sorted sha256sum-compatible relative-path rows",
        "external_anchor": {
            "recorded_by": "future runner terminalizer into the immutable run index outside the attempt root",
            "cited_by": "external result aggregation using run_id, attempt_id, attempt-root identifier, manifest relative path, and terminal-manifest SHA-256",
            "replaceable_with_attempt_tree": False,
        },
        "post_seal_consistent_internal_reseal_with_original_anchor": "REJECT",
        "different_manifest_requires_different_external_anchor": True,
    }
    policy["full_admissibility_validation"]["inputs"] = [
        "one candidate runtime document", "authenticated policy input", "closed resolved-evidence context",
    ]
    policy["full_admissibility_validation"]["authenticated_policy_required_before_hook_selection"] = True
    policy["full_admissibility_validation"]["validation_modes"] = [CAPTURE_TIME_VALIDATION_LEVEL, OFFLINE_VALIDATION_LEVEL]
    policy["full_admissibility_validation"]["dispatcher_uses_caller_selected_hook_list"] = False
    policy["full_admissibility_validation"]["dispatcher_extracts_hooks_only_from_authenticated_policy_bytes"] = True
    return policy


_legacy_metadata_value_schema = metadata_value_schema


def metadata_value_schema(name: str) -> dict[str, Any]:
    if name == "raw_evidence_references":
        return {"type": "array", "minItems": 1, "maxItems": 4096, "items": {"$ref": "#/$defs/payload_evidence_ref"}}
    if name == "raw_evidence_sha256_per_reference":
        return {"type": "array", "minItems": 1, "maxItems": 4096, "items": {"type": "string", "pattern": SHA256_RE.pattern}}
    if name == "kubernetes_uid_and_resource_version_references_when_applicable":
        return {"type": "array", "maxItems": 4096, "items": {"$ref": "#/$defs/payload_evidence_ref"}}
    if name == "entry_indexes":
        return {"type": "array", "maxItems": 4096, "items": {"type": "integer", "minimum": 0}}
    if name == "canonical_request":
        return {"$ref": "#/$defs/kubernetes_request"}
    if name == "operation_request_reference":
        return {"$ref": "#/$defs/descriptor_ref_kubernetes_request_identity"}
    if name in ("replicaset_projection_reference", "deployment_projection_reference"):
        return {"$ref": "#/$defs/payload_ref_kubernetes_object_projection"}
    if name == "selection_evidence_reference":
        return {"$ref": "#/$defs/evidence_reference_union"}
    if name == "projection_schema_id":
        return {"type": "string", "enum": list(PROJECTION_CLASSES)}
    if name == "capture_state":
        return {"type": "string", "enum": [
            "CREATED", "PREFLIGHT_PASS", "HEALTHY_STATE_CAPTURED", "MUTANT_INJECTED",
            "MUTANT_STATE_VERIFIED", "ORIGINAL_ORACLE_STARTED", "ORIGINAL_ORACLE_EVALUATED",
            "CONTRACT_EVALUATED", "RESTORE_STARTED", "RESTORE_VERIFIED",
        ]}
    if name == "source_list_resource_version":
        return {"type": "string", "maxLength": 128}
    return _legacy_metadata_value_schema(name)


def _bounded_evidence_map(value_schema: dict[str, Any], maximum: int = 4096) -> dict[str, Any]:
    return {"type": "object", "maxProperties": maximum, "propertyNames": {"type": "string", "pattern": EVIDENCE_ID_RE.pattern}, "additionalProperties": value_schema, "x-bounded-dynamic-map": True}


def authenticated_policy_input_schema() -> dict[str, Any]:
    hex_bytes = {"type": "string", "pattern": r"^(?:[0-9a-f]{2})*$", "maxLength": 33554432}
    return object_schema({
        "document_type": {"type": "string", "const": AUTHENTICATED_POLICY_INPUT_DOCUMENT},
        "schema_version": {"type": "integer", "const": 1},
        "exact_policy_bytes_hex": deepcopy(hex_bytes),
        "exact_schema_bytes_hex": deepcopy(hex_bytes),
        "exact_checksum_manifest_bytes_hex": {"type": "string", "pattern": r"^(?:[0-9a-f]{2})*$", "maxLength": 16384},
        "expected_checksum_manifest_sha256": {"type": "string", "pattern": SHA256_RE.pattern},
        "expected_policy_relative_path": {"type": "string", "const": POLICY_REL},
        "expected_schema_relative_path": {"type": "string", "const": SCHEMA_REL},
        "expected_generator_relative_path": {"type": "string", "const": GENERATOR_REL},
        "supplied_parsed_policy": {"$ref": "#/$defs/canonical_value"},
        "supplied_parsed_schema": {"$ref": "#/$defs/canonical_value"},
        "caller_hook_contracts": {"type": "array", "maxItems": 12, "items": {"$ref": "#/$defs/canonical_value"}},
        "caller_hook_applicability_matrix": {"type": "array", "maxItems": 128, "items": {"$ref": "#/$defs/canonical_value"}},
        "caller_hook_order": {"type": "array", "maxItems": 12, "items": {"type": "string", "enum": list(RUNTIME_VALIDATOR_HOOKS)}},
    })


def workload_parse_result_payload_schema() -> dict[str, Any]:
    parser_identity = object_schema({
        "sregym_commit": {"type": "string", "const": SREGYM_COMMIT},
        "module_path": {"type": "string", "const": WORKLOAD_PARSER_MODULE},
        "source_sha256": {"type": "string", "const": WORKLOAD_PARSER_SOURCE_SHA256},
        "python_runtime": {"type": "string", "const": "3.12.3"},
        "algorithm": {"type": "string", "const": WORKLOAD_PARSE_ALGORITHM},
        "version": {"type": "integer", "const": 1},
    })
    return object_schema({
        "document_type": {"type": "string", "const": "WORKLOAD_PARSE_RESULT_V1"},
        "schema_version": {"type": "integer", "const": 1},
        "parser_identity": parser_identity,
        "boundary_reference": {"$ref": "#/$defs/descriptor_ref_workload_boundary"},
        "raw_log_reference": {"$ref": "#/$defs/payload_ref_workload_log_bytes"},
        "workload_window": {"$ref": "#/$defs/workload_window_descriptor_identity"},
        "complete_entry_count": {"type": "integer", "minimum": 0, "maximum": 4096},
        "entry_indexes": {"type": "array", "maxItems": 4096, "items": {"type": "integer", "minimum": 0}},
        "entry_time_ieee754_binary64_hex": {"type": "array", "maxItems": 4096, "items": {"type": "string", "pattern": BINARY64_RE.pattern}},
        "prefix_complete_entry_count": {"type": "integer", "minimum": 0, "maximum": 4096},
        "prefix_byte_length": {"type": "integer", "minimum": 0, "maximum": 262144},
        "prefix_sha256": {"type": "string", "pattern": SHA256_RE.pattern},
        "suffix_sha256": {"type": "string", "pattern": SHA256_RE.pattern},
        "fresh_request_count": {"type": "integer", "minimum": 0},
        "failure_marker_count": {"type": "integer", "minimum": 0},
    })


def offline_seal_input_schema(policy: dict[str, Any]) -> dict[str, Any]:
    hash_schema = {"type": "string", "pattern": SHA256_RE.pattern}
    anchor = object_schema({
        "recorded_by": {"type": "string", "const": "IMMUTABLE_RUN_INDEX_OUTSIDE_ATTEMPT_ROOT"},
        "cited_by_result_aggregation": {"type": "boolean", "const": True},
        "attempt_root_identifier": {"type": "string", "minLength": 1, "maxLength": 256},
        "manifest_relative_path": {"type": "string", "pattern": r"^manifests/[A-Za-z0-9._/-]+$"},
        "terminal_manifest_sha256": deepcopy(hash_schema),
    })
    release = object_schema({
        "evidence_policy_annotated_tag": {"type": "string", "const": "sremut-missing-service-evidence-policy-v1"},
        "evidence_policy_checksum_manifest_sha256": deepcopy(hash_schema),
        "evidence_policy_sha256": deepcopy(hash_schema),
        "evidence_policy_schema_sha256": deepcopy(hash_schema),
        "dispatcher_id": {"type": "string", "const": FULL_ADMISSIBILITY_DISPATCHER_ID},
    })
    return object_schema({
        "document_type": {"type": "string", "const": OFFLINE_SEAL_INPUT_DOCUMENT},
        "schema_version": {"type": "integer", "const": 1},
        "attempt_root_identifier": {"type": "string", "minLength": 1, "maxLength": 256},
        "run_id": {"type": "string", "pattern": RUN_ID_RE.pattern},
        "attempt_id": {"type": "string", "pattern": ATTEMPT_ID_RE.pattern},
        "terminal_state": {"type": "string", "enum": policy["verified_attempt_state_machine"]["terminal_states"]},
        "terminal_manifest_bytes_hex": {"type": "string", "pattern": r"^(?:[0-9a-f]{2})*$", "maxLength": 33554432},
        "externally_recorded_terminal_manifest_sha256": deepcopy(hash_schema),
        "expected_manifest_relative_path": {"type": "string", "pattern": r"^manifests/[A-Za-z0-9._/-]+$"},
        "sealed_journal_sha256": deepcopy(hash_schema),
        "runner_release_binding": release,
        "aggregation_anchor": anchor,
    })


def resolved_context_schema(policy: dict[str, Any]) -> dict[str, Any]:
    hash_schema = {"type": "string", "pattern": SHA256_RE.pattern}
    nullable_ref = {"oneOf": [{"type": "null"}, {"$ref": "#/$defs/evidence_reference_union"}]}
    state_names = policy["verified_attempt_state_machine"]["states"]
    publication = object_schema({"sequence_number": {"type": "integer", "minimum": 0}, "journal_record_sha256": deepcopy(hash_schema)})
    state = object_schema({"state": {"type": "string", "enum": state_names}, "sequence_number": {"type": "integer", "minimum": 0}, "terminal": {"type": "boolean"}})
    operation = object_schema({
        "operation_kind": {"oneOf": [{"type": "null"}, {"type": "string", "minLength": 1, "maxLength": 128}]},
        "request_rule_id": {"oneOf": [{"type": "null"}, {"type": "string", "minLength": 1, "maxLength": 128}]},
        "resource": {"oneOf": [{"type": "null"}, {"type": "string", "minLength": 1, "maxLength": 63}]},
        "namespace": {"oneOf": [{"type": "null"}, {"type": "string", "minLength": 1, "maxLength": 253}]},
        "name": {"oneOf": [{"type": "null"}, {"type": "string", "minLength": 1, "maxLength": 253}]},
        "request_reference": nullable_ref, "projection_reference": nullable_ref, "request_body_reference": nullable_ref,
        "request_dispatch_sequence": {"oneOf": [{"type": "null"}, {"type": "integer", "minimum": 0}]},
    })
    evaluation = object_schema({
        "predicate_id": {"type": "string", "enum": list(EVALUATION_CONTEXTS)},
        "phase": {"type": "string", "enum": list(WORKLOAD_WINDOWS)},
        "deadline_identity": {"type": "string", "enum": [row["deadline_identity"] for row in EVALUATION_CONTEXTS.values()]},
    })
    challenge = object_schema({
        "run_id": {"type": "string", "pattern": RUN_ID_RE.pattern}, "mutant_id": {"type": "string", "enum": ["MS-M01", "MS-M02", "MS-M03"]},
        "repetition": {"type": "integer", "minimum": 1, "maximum": 3}, "attempt_id": {"type": "string", "pattern": ATTEMPT_ID_RE.pattern},
        "frozen_spec_sha256": deepcopy(hash_schema), "canonical_request_body_sha256": deepcopy(hash_schema), "expected_name": {"type": "string", "minLength": 1, "maxLength": 63},
        "canonical_body_reference": {"$ref": "#/$defs/payload_ref_kubernetes_object_projection"},
    })
    return object_schema({
        "document_type": {"type": "string", "const": RESOLVED_CONTEXT_DOCUMENT}, "schema_version": {"type": "integer", "const": 1},
        "run_id": {"type": "string", "pattern": RUN_ID_RE.pattern}, "attempt_id": {"type": "string", "pattern": ATTEMPT_ID_RE.pattern},
        "mutant_id": {"type": "string", "enum": ["MS-M01", "MS-M02", "MS-M03"]}, "repetition": {"type": "integer", "minimum": 1, "maximum": 3},
        "evidence_refs": _bounded_evidence_map({"$ref": "#/$defs/evidence_reference_union"}),
        "exact_descriptor_bytes_hex": _bounded_evidence_map({"type": "string", "pattern": r"^(?:[0-9a-f]{2})*$", "maxLength": 8388608}),
        "parsed_canonical_descriptors": _bounded_evidence_map({"oneOf": [{"$ref": "#/$defs/payload_evidence_descriptor"}, {"$ref": "#/$defs/descriptor_evidence_descriptor"}]}),
        "exact_payload_bytes_hex": _bounded_evidence_map({"type": "string", "pattern": r"^(?:[0-9a-f]{2})*$", "maxLength": 33554432}),
        "journal_publication_records": _bounded_evidence_map(publication),
        "attempt_journal_records": {"type": "array", "maxItems": 16384, "items": {"$ref": "#/$defs/journal_record"}},
        "exact_authoritative_journal_bytes_hex": {"type": "string", "pattern": r"^(?:[0-9a-f]{2})*$", "maxLength": 33554432},
        "validation_mode": {"type": "string", "enum": [CAPTURE_TIME_VALIDATION_LEVEL, OFFLINE_VALIDATION_LEVEL]},
        "trusted_capture_source_bytes_hex": _bounded_evidence_map({"type": "string", "pattern": r"^(?:[0-9a-f]{2})*$", "maxLength": 33554432}),
        "offline_seal": {"oneOf": [{"type": "null"}, {"$ref": "#/$defs/offline_seal_input"}]},
        "current_verified_attempt_state": state,
        "frozen_contract_profile_identities": object_schema({"contract_sha256": {"const": CONTRACT_SHA256}, "execution_profile_sha256": {"const": PROFILE_SHA256}, "contract_tag_object": {"const": CONTRACT_TAG_OBJECT}, "execution_profile_tag_object": {"const": PROFILE_TAG_OBJECT}}),
        "expected_operation_context": {"oneOf": [{"type": "null"}, operation]},
        "expected_evaluation_context": {"oneOf": [{"type": "null"}, evaluation]},
        "challenge_identity": {"oneOf": [{"type": "null"}, challenge]},
    })


def full_validation_result_schema() -> dict[str, Any]:
    return object_schema({
        "document_type": {"type": "string", "const": FULL_ADMISSIBILITY_RESULT_DOCUMENT}, "schema_version": {"type": "integer", "const": 1},
        "valid": {"type": "boolean"}, "dispatcher_id": {"type": "string", "const": FULL_ADMISSIBILITY_DISPATCHER_ID},
        "hook_id": {"oneOf": [{"type": "null"}, {"type": "string", "enum": list(RUNTIME_VALIDATOR_HOOKS)}]},
        "failure_code": {"oneOf": [{"type": "null"}, {"type": "string", "enum": list(VALIDATION_FAILURE_CODES)}]},
        "subject_evidence_id": {"oneOf": [{"type": "null"}, {"type": "string", "pattern": EVIDENCE_ID_RE.pattern}]},
    })


_legacy_runtime_schemas = runtime_schemas


def runtime_schemas(policy: dict[str, Any]) -> dict[str, Any]:
    definitions = _legacy_runtime_schemas(policy)
    definitions["workload_window_descriptor_identity"] = object_schema({key: deepcopy(value) for key, value in workload_window_schema()["properties"].items() if key in ("phase", "ordinal", "run_id", "attempt_id", "mutant_id", "repetition")})
    return definitions


_legacy_build_schema = build_schema


def build_schema(policy: dict[str, Any]) -> dict[str, Any]:
    schema = _legacy_build_schema(policy)
    schema["$defs"]["authenticated_policy_input"] = authenticated_policy_input_schema()
    schema["$defs"]["workload_parse_result_payload"] = workload_parse_result_payload_schema()
    schema["$defs"]["offline_seal_input"] = offline_seal_input_schema(policy)
    schema["$defs"]["resolved_evidence_context"] = resolved_context_schema(policy)
    schema["$defs"]["full_admissibility_validation_result"] = full_validation_result_schema()
    schema["oneOf"].extend([
        {"$ref": "#/$defs/authenticated_policy_input"},
        {"$ref": "#/$defs/workload_parse_result_payload"},
        {"$ref": "#/$defs/resolved_evidence_context"},
        {"$ref": "#/$defs/full_admissibility_validation_result"},
    ])
    schema["x-validation-levels"] = [STRUCTURAL_VALIDATION_LEVEL, FULL_VALIDATION_LEVEL]
    schema["x-full-admissibility-dispatcher"] = FULL_ADMISSIBILITY_DISPATCHER_ID
    schema["x-structural-validation-is-not-admissibility"] = True
    assert_closed_object_schemas(schema)
    Draft202012Validator.check_schema(schema)
    assert_all_defs_reachable(schema)
    return schema



def structural_schema_branch_name(document: Any, schema: dict[str, Any]) -> str:
    if not isinstance(document, dict): raise ValidationError("runtime document is not an object")
    kind = document.get("document_type"); role = document.get("role")
    if kind == "PAYLOAD_EVIDENCE_DESCRIPTOR_V1" and role in ROLE_NAMES[:-1]: return "payload_descriptor_" + role
    if kind == "DESCRIPTOR_EVIDENCE_DESCRIPTOR_V1" and role in ROLE_NAMES[:-1]: return "descriptor_descriptor_" + role
    if kind == "PAYLOAD_EVIDENCE_REF_V1" and role in ROLE_NAMES[:-1]: return "payload_ref_" + role
    if kind == "DESCRIPTOR_EVIDENCE_REF_V1" and role in ROLE_NAMES[:-1]: return "descriptor_ref_" + role
    names = {
        "EVIDENCE_POLICY_DOCUMENT_V1": "evidence_policy_document", "JOURNAL_RECORD_V1": "journal_record",
        "ATTEMPT_VALIDATION_ENVELOPE_V1": "attempt_validation_envelope", "TERMINAL_GLOBAL_STOP_V1": "terminal_global_stop",
        "SERVICE_RESTORATION_BODY_V1": "service_restoration_body", "KUBERNETES_REQUEST_V1": "kubernetes_request",
        "KUBERNETES_LIST_RESPONSE_V1": "kubernetes_list_response",
        "WORKLOAD_PARSE_RESULT_V1": "workload_parse_result_payload",
        AUTHENTICATED_POLICY_INPUT_DOCUMENT: "authenticated_policy_input",
        RESOLVED_CONTEXT_DOCUMENT: "resolved_evidence_context",
        FULL_ADMISSIBILITY_RESULT_DOCUMENT: "full_admissibility_validation_result",
    }
    name = names.get(kind)
    if name is None or name not in schema["$defs"]: raise ValidationError("unknown public schema branch")
    return name


def validate_structural_schema_branch(document: Any, schema: dict[str, Any]) -> None:
    name = structural_schema_branch_name(document, schema)
    branch = {"$schema": schema["$schema"], "$ref": f"#/$defs/{name}", "$defs": schema["$defs"]}
    Draft202012Validator(branch).validate(document)


def _failure_result(code: str, hook_id: str | None, candidate: Any) -> dict[str, Any]:
    subject = candidate.get("evidence_id") if isinstance(candidate, dict) and isinstance(candidate.get("evidence_id"), str) and EVIDENCE_ID_RE.fullmatch(candidate["evidence_id"]) else None
    return {"document_type": FULL_ADMISSIBILITY_RESULT_DOCUMENT, "schema_version": 1, "valid": False, "dispatcher_id": FULL_ADMISSIBILITY_DISPATCHER_ID, "hook_id": hook_id, "failure_code": code, "subject_evidence_id": subject}


def _success_result(candidate: Any) -> dict[str, Any]:
    subject = candidate.get("evidence_id") if isinstance(candidate, dict) and isinstance(candidate.get("evidence_id"), str) and EVIDENCE_ID_RE.fullmatch(candidate["evidence_id"]) else None
    return {"document_type": FULL_ADMISSIBILITY_RESULT_DOCUMENT, "schema_version": 1, "valid": True, "dispatcher_id": FULL_ADMISSIBILITY_DISPATCHER_ID, "hook_id": None, "failure_code": None, "subject_evidence_id": subject}


def _raise(code: str, hook_id: str | None) -> None:
    raise ValidationFailure(code, hook_id)


def _parse_canonical_json_bytes(data: bytes, code: str, hook_id: str) -> Any:
    try:
        value = json.loads(data.decode("utf-8", errors="strict"))
        ensure_no_floats(value)
        if canonical_json_bytes(value) != data:
            _raise(code, hook_id)
        return value
    except ValidationFailure:
        raise
    except Exception:
        _raise(code, hook_id)


def _descriptor_reference_match(descriptor: dict[str, Any], reference: dict[str, Any], hook_id: str) -> None:
    digest = descriptor_content_sha256(descriptor)
    if digest != reference.get("descriptor_sha256") or reference.get("evidence_id") != "ev-" + digest[:32]:
        _raise("DESCRIPTOR_HASH_MISMATCH", hook_id)
    for field in ("role", "producer", "source_kind", "media_type", "storage_class"):
        if descriptor.get(field) != reference.get(field):
            _raise("DESCRIPTOR_REFERENCE_MISMATCH", hook_id)
    if "evidence_id" in descriptor and descriptor["evidence_id"] != reference["evidence_id"]:
        _raise("DESCRIPTOR_REFERENCE_MISMATCH", hook_id)
    if reference["storage_class"] == "PAYLOAD_WITH_DESCRIPTOR":
        for field in ("payload_sha256", "payload_size_bytes", "payload_relative_path"):
            if descriptor.get(field) != reference.get(field):
                _raise("DESCRIPTOR_REFERENCE_MISMATCH", hook_id)


def _parse_sha256_manifest_bytes(data: bytes, expected_paths: tuple[str, ...], code: str, hook_id: str | None) -> dict[str, str]:
    try:
        text = data.decode("utf-8", errors="strict")
    except Exception:
        _raise(code, hook_id)
    expected_text = ""
    rows: dict[str, str] = {}
    for line in text.splitlines(keepends=True):
        match = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9._/-]+)\n", line)
        if match is None or match.group(2) in rows:
            _raise(code, hook_id)
        rows[match.group(2)] = match.group(1)
        expected_text += line
    if expected_text.encode("utf-8") != data or tuple(rows) != expected_paths:
        _raise(code, hook_id)
    return rows


def authenticate_policy_input(value: Any) -> tuple[dict[str, Any], dict[str, Any], dict[str, str], bytes]:
    hook_id = None
    required = {
        "document_type", "schema_version", "exact_policy_bytes_hex", "exact_schema_bytes_hex",
        "exact_checksum_manifest_bytes_hex", "expected_checksum_manifest_sha256",
        "expected_policy_relative_path", "expected_schema_relative_path", "expected_generator_relative_path",
        "supplied_parsed_policy", "supplied_parsed_schema", "caller_hook_contracts",
        "caller_hook_applicability_matrix", "caller_hook_order",
    }
    if not isinstance(value, dict) or set(value) != required or value.get("document_type") != AUTHENTICATED_POLICY_INPUT_DOCUMENT or value.get("schema_version") != 1:
        _raise("POLICY_BINDING_MISSING", hook_id)
    if value.get("expected_policy_relative_path") != POLICY_REL or value.get("expected_schema_relative_path") != SCHEMA_REL or value.get("expected_generator_relative_path") != GENERATOR_REL:
        _raise("POLICY_MANIFEST_INVALID", hook_id)
    try:
        policy_bytes = bytes.fromhex(value["exact_policy_bytes_hex"])
        schema_bytes = bytes.fromhex(value["exact_schema_bytes_hex"])
        manifest_bytes = bytes.fromhex(value["exact_checksum_manifest_bytes_hex"])
    except Exception:
        _raise("POLICY_BINDING_MISSING", hook_id)
    if sha256_bytes(manifest_bytes) != value.get("expected_checksum_manifest_sha256"):
        _raise("POLICY_MANIFEST_HASH_MISMATCH", hook_id)
    rows = _parse_sha256_manifest_bytes(manifest_bytes, ARTIFACT_RELS, "POLICY_MANIFEST_INVALID", hook_id)
    if rows[POLICY_REL] != sha256_bytes(policy_bytes):
        _raise("POLICY_HASH_MISMATCH", hook_id)
    if rows[SCHEMA_REL] != sha256_bytes(schema_bytes):
        _raise("POLICY_SCHEMA_HASH_MISMATCH", hook_id)
    try:
        policy = yaml.safe_load(policy_bytes)
        schema = json.loads(schema_bytes.decode("utf-8", errors="strict"))
        ensure_no_floats(policy)
        ensure_no_floats(schema)
        if render_policy(policy) != policy_bytes or render_schema(schema) != schema_bytes:
            _raise("POLICY_PARSED_CONTENT_MISMATCH", hook_id)
        Draft202012Validator.check_schema(schema)
        validate_structural_schema_branch(policy, schema)
    except ValidationFailure:
        raise
    except Exception:
        _raise("POLICY_PARSED_CONTENT_MISMATCH", hook_id)
    if value["supplied_parsed_policy"] != policy or value["supplied_parsed_schema"] != schema:
        _raise("POLICY_PARSED_CONTENT_MISMATCH", hook_id)
    authenticated_contracts = policy.get("full_admissibility_validation", {}).get("hook_contracts")
    authenticated_matrix = policy.get("full_admissibility_validation", {}).get("hook_applicability_matrix")
    if authenticated_contracts != frozen_hook_contracts() or value["caller_hook_contracts"] != authenticated_contracts:
        _raise("HOOK_MATRIX_MISMATCH", hook_id)
    if authenticated_matrix != frozen_hook_applicability_matrix() or value["caller_hook_applicability_matrix"] != authenticated_matrix:
        _raise("HOOK_MATRIX_MISMATCH", hook_id)
    expected_order = list(RUNTIME_VALIDATOR_HOOKS)
    authenticated_order = [row.get("hook_id") for row in authenticated_contracts]
    if authenticated_order != expected_order or value["caller_hook_order"] != expected_order:
        _raise("HOOK_ORDER_MISMATCH", hook_id)
    return policy, schema, rows, manifest_bytes


def _canonical_journal_bytes(records: list[dict[str, Any]]) -> bytes:
    return b"".join(canonical_json_bytes(record) + b"\n" for record in records)


def _derive_journal_context(policy: dict[str, Any], schema: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    hook_id = "VALIDATE_JOURNAL_HASH_CHAIN_V1"
    encoded = context.get("exact_authoritative_journal_bytes_hex")
    if not isinstance(encoded, str):
        _raise("JOURNAL_BYTES_MISSING", hook_id)
    try:
        journal_bytes = bytes.fromhex(encoded)
        text = journal_bytes.decode("utf-8", errors="strict")
    except Exception:
        _raise("JOURNAL_CANONICALIZATION_INVALID", hook_id)
    records: list[dict[str, Any]] = []
    try:
        if text and not text.endswith("\n"):
            raise ValueError
        for line in text.splitlines(keepends=True):
            if not line.endswith("\n"):
                raise ValueError
            record = json.loads(line[:-1])
            ensure_no_floats(record)
            if canonical_json_bytes(record) + b"\n" != line.encode("utf-8"):
                raise ValueError
            validate_structural_schema_branch(record, schema)
            records.append(record)
    except Exception:
        _raise("JOURNAL_CANONICALIZATION_INVALID", hook_id)
    if records != context.get("attempt_journal_records"):
        _raise("JOURNAL_CONTEXT_MISMATCH", hook_id)
    try:
        validate_journal_chain(records)
    except Exception:
        _raise("JOURNAL_CHAIN_INVALID", hook_id)
    state_names = policy["verified_attempt_state_machine"]["states"]
    legal = set(policy["verified_attempt_state_machine"]["legal_transitions"])
    terminal_states = set(policy["verified_attempt_state_machine"]["terminal_states"])
    state = "CREATED"
    state_by_sequence: dict[int, str] = {}
    evaluation: dict[str, Any] | None = None
    operation: dict[str, Any] | None = None
    evaluation_sequence: int | None = None
    operation_sequence: int | None = None
    for sequence, record in enumerate(records):
        if record.get("sequence_number") != sequence or record.get("run_id") != context.get("run_id") or record.get("attempt_id") != context.get("attempt_id"):
            _raise("JOURNAL_CONTEXT_MISMATCH", hook_id)
        transition = record.get("transition", "")
        if transition.startswith("STATE_VERIFIED:"):
            proposed = transition.split(":", 1)[1]
            if proposed not in state_names:
                _raise("JOURNAL_STATE_DERIVATION_FAILED", hook_id)
            state = proposed
        elif transition in legal:
            source, target = transition.split("->", 1)
            if source != state:
                _raise("JOURNAL_STATE_DERIVATION_FAILED", hook_id)
            state = target
        elif transition.startswith("EVALUATION_AUTHORIZED:"):
            predicate = transition.split(":", 1)[1]
            frozen = EVALUATION_CONTEXTS.get(predicate)
            if frozen is None or state not in frozen["allowed_states"]:
                _raise("JOURNAL_STATE_DERIVATION_FAILED", hook_id)
            evaluation = {"predicate_id": predicate, "phase": frozen["phase"], "deadline_identity": frozen["deadline_identity"]}
            evaluation_sequence = sequence
        elif transition.startswith("OPERATION_AUTHORIZED:"):
            try:
                operation = json.loads(bytes.fromhex(transition.split(":", 1)[1]).decode("utf-8"))
                ensure_no_floats(operation)
                if canonical_json_bytes(operation).hex() != transition.split(":", 1)[1]:
                    raise ValueError
            except Exception:
                _raise("JOURNAL_STATE_DERIVATION_FAILED", hook_id)
            operation_sequence = sequence
        state_by_sequence[sequence] = state
    derived_state = {"state": state, "sequence_number": len(records) - 1, "terminal": state in terminal_states}
    if context.get("current_verified_attempt_state") != derived_state:
        _raise("JOURNAL_CONTEXT_MISMATCH", hook_id)
    supplied_evaluation = context.get("expected_evaluation_context")
    if supplied_evaluation is not None and evaluation is None:
        _raise("JOURNAL_EVALUATION_MARKER_MISSING", hook_id)
    if supplied_evaluation != evaluation:
        _raise("JOURNAL_CONTEXT_MISMATCH", hook_id)
    supplied_operation = deepcopy(context.get("expected_operation_context"))
    if supplied_operation is not None:
        supplied_operation.pop("request_dispatch_sequence", None)
        if operation is None:
            _raise("JOURNAL_OPERATION_MARKER_MISSING", hook_id)
        if supplied_operation != operation:
            _raise("JOURNAL_CONTEXT_MISMATCH", hook_id)
    elif operation is not None:
        _raise("JOURNAL_CONTEXT_MISMATCH", hook_id)
    return {
        "journal_bytes": journal_bytes, "records": records, "state": derived_state,
        "state_by_sequence": state_by_sequence, "evaluation": evaluation,
        "evaluation_sequence": evaluation_sequence, "operation": operation,
        "operation_sequence": operation_sequence,
    }


def authenticate_resolved_evidence_context(policy: dict[str, Any], schema: dict[str, Any], context: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    hook_id = "VALIDATE_DESCRIPTOR_CONTENT_IDENTITY_V1"
    if not isinstance(context, dict):
        _raise("MISSING_RESOLVED_CONTEXT", hook_id)
    try:
        validate_structural_schema_branch(context, schema)
        ensure_no_floats(context)
    except Exception:
        _raise("RESOLVED_CONTEXT_INVALID", hook_id)
    if context["frozen_contract_profile_identities"] != {
        "contract_sha256": CONTRACT_SHA256, "execution_profile_sha256": PROFILE_SHA256,
        "contract_tag_object": CONTRACT_TAG_OBJECT, "execution_profile_tag_object": PROFILE_TAG_OBJECT,
    }:
        _raise("RESOLVED_CONTEXT_INVALID", hook_id)
    if context.get("validation_mode") not in (CAPTURE_TIME_VALIDATION_LEVEL, OFFLINE_VALIDATION_LEVEL):
        _raise("VALIDATION_MODE_INVALID", hook_id)
    derived = _derive_journal_context(policy, schema, context)
    records = derived["records"]
    state = derived["state"]
    maps = (context["evidence_refs"], context["exact_descriptor_bytes_hex"], context["parsed_canonical_descriptors"], context["journal_publication_records"])
    if any(set(mapping) != set(context["evidence_refs"]) for mapping in maps):
        _raise("RESOLVED_CONTEXT_INVALID", hook_id)
    authenticated: dict[str, Any] = {}
    for evidence_id, reference in context["evidence_refs"].items():
        try:
            validate_evidence_ref(reference, policy)
        except Exception:
            _raise("EVIDENCE_REFERENCE_INVALID", "VALIDATE_EVIDENCE_REF_HASH_PATH_ID_V1")
        if reference["evidence_id"] != evidence_id:
            _raise("EVIDENCE_REFERENCE_INVALID", "VALIDATE_EVIDENCE_REF_HASH_PATH_ID_V1")
        try:
            descriptor_bytes = bytes.fromhex(context["exact_descriptor_bytes_hex"][evidence_id])
        except Exception:
            _raise("DESCRIPTOR_BYTES_MISSING", hook_id)
        if reference.get("descriptor_size_bytes") != len(descriptor_bytes):
            _raise("DESCRIPTOR_REFERENCE_MISMATCH", hook_id)
        parsed = _parse_canonical_json_bytes(descriptor_bytes, "DESCRIPTOR_CANONICAL_MISMATCH", hook_id)
        if parsed != context["parsed_canonical_descriptors"][evidence_id]:
            _raise("DESCRIPTOR_CANONICAL_MISMATCH", hook_id)
        _descriptor_reference_match(parsed, reference, hook_id)
        payload_bytes = None
        if reference["storage_class"] == "PAYLOAD_WITH_DESCRIPTOR":
            encoded_payload = context["exact_payload_bytes_hex"].get(evidence_id)
            if not isinstance(encoded_payload, str):
                _raise("PAYLOAD_BYTES_MISSING", hook_id)
            try:
                payload_bytes = bytes.fromhex(encoded_payload)
            except Exception:
                _raise("PAYLOAD_BYTES_MISSING", hook_id)
            if sha256_bytes(payload_bytes) != reference["payload_sha256"]:
                _raise("PAYLOAD_HASH_MISMATCH", hook_id)
            if len(payload_bytes) != reference["payload_size_bytes"]:
                _raise("PAYLOAD_SIZE_MISMATCH", hook_id)
        elif evidence_id in context["exact_payload_bytes_hex"]:
            _raise("RESOLVED_CONTEXT_INVALID", hook_id)
        publication = context["journal_publication_records"].get(evidence_id)
        if not isinstance(publication, dict) or publication["sequence_number"] >= len(records):
            _raise("PUBLICATION_RECORD_MISSING", hook_id)
        record = records[publication["sequence_number"]]
        if publication["journal_record_sha256"] != record.get("canonical_current_entry_sha256"):
            _raise("PUBLICATION_RECORD_MISSING", hook_id)
        if reference["descriptor_sha256"] not in record.get("referenced_descriptor_sha256", []):
            _raise("PUBLICATION_RECORD_MISSING", hook_id)
        if payload_bytes is not None and reference["payload_sha256"] not in record.get("referenced_payload_sha256", []):
            _raise("PUBLICATION_RECORD_MISSING", hook_id)
        if record.get("run_id") != context["run_id"] or record.get("attempt_id") != context["attempt_id"] or parsed.get("run_id") != context["run_id"] or parsed.get("attempt_id") != context["attempt_id"]:
            _raise("RUN_ATTEMPT_MISMATCH", hook_id)
        if publication["sequence_number"] > state["sequence_number"]:
            _raise("PUBLICATION_ORDER_INVALID", hook_id)
        authenticated[evidence_id] = {
            "reference": reference, "descriptor_bytes": descriptor_bytes, "descriptor": parsed,
            "payload_bytes": payload_bytes, "publication_sequence": publication["sequence_number"],
            "publication_state": derived["state_by_sequence"][publication["sequence_number"]],
        }
    if set(context["exact_payload_bytes_hex"]) != {evidence_id for evidence_id, row in authenticated.items() if row["payload_bytes"] is not None}:
        _raise("RESOLVED_CONTEXT_INVALID", hook_id)
    sources = context.get("trusted_capture_source_bytes_hex", {})
    if context["validation_mode"] == CAPTURE_TIME_VALIDATION_LEVEL:
        if context.get("offline_seal") is not None or not set(sources).issubset(authenticated):
            _raise("RESOLVED_CONTEXT_INVALID", hook_id)
    elif sources or context.get("offline_seal") is None:
        _raise("EXTERNAL_SEAL_MISSING", hook_id)
    return authenticated, derived


def _iter_evidence_refs(value: Any):
    if isinstance(value, dict):
        if value.get("document_type") in ("PAYLOAD_EVIDENCE_REF_V1", "DESCRIPTOR_EVIDENCE_REF_V1"):
            yield value
        for child in value.values():
            yield from _iter_evidence_refs(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_evidence_refs(child)


def _resolve_reference(authenticated: dict[str, Any], reference: Any, hook_id: str, *, role: str | None = None, payload: bool | None = None, unresolved_code: str = "EVIDENCE_REFERENCE_UNRESOLVED") -> dict[str, Any]:
    if not isinstance(reference, dict) or not isinstance(reference.get("evidence_id"), str):
        _raise(unresolved_code, hook_id)
    row = authenticated.get(reference["evidence_id"])
    if row is None or row["reference"] != reference:
        _raise(unresolved_code, hook_id)
    if role is not None and row["reference"]["role"] != role:
        _raise("ADJUDICATION_RAW_ROLE_INVALID" if hook_id == "VALIDATE_ADJUDICATION_RAW_BACKING_V1" else "DESCRIPTOR_REFERENCE_MISMATCH", hook_id)
    if payload is True and row["payload_bytes"] is None:
        _raise("PAYLOAD_BYTES_MISSING", hook_id)
    if payload is False and row["payload_bytes"] is not None:
        _raise("DESCRIPTOR_REFERENCE_MISMATCH", hook_id)
    return row


def _resolved_candidate_descriptor(candidate: dict[str, Any], authenticated: dict[str, Any], hook_id: str) -> dict[str, Any]:
    if isinstance(candidate.get("evidence_id"), str) and candidate["evidence_id"] in authenticated:
        row = authenticated[candidate["evidence_id"]]
        if row["descriptor"] != candidate:
            _raise("DESCRIPTOR_REFERENCE_MISMATCH", hook_id)
        return row
    matches = [row for row in authenticated.values() if row["descriptor"] == candidate]
    if len(matches) != 1:
        _raise("EVIDENCE_REFERENCE_UNRESOLVED", hook_id)
    return matches[0]


def _projection_object(payload_bytes: bytes, hook_id: str) -> dict[str, Any]:
    value = _parse_canonical_json_bytes(payload_bytes, "KUBERNETES_PROJECTION_FIELD_FORBIDDEN", hook_id)
    if isinstance(value, dict) and value.get("document_type") == "KUBERNETES_LIST_RESPONSE_V1" and len(value.get("items", [])) == 1:
        item = value["items"][0]
        fields = deepcopy(item.get("projected_fields", {}))
        fields.setdefault("apiVersion", item.get("apiVersion")); fields.setdefault("kind", item.get("kind"))
        fields.setdefault("metadata", {}).setdefault("name", item.get("name")); fields["metadata"].setdefault("namespace", item.get("namespace"))
        return fields
    if isinstance(value, dict) and "projected_fields" in value:
        fields = deepcopy(value["projected_fields"])
        fields.setdefault("apiVersion", value.get("apiVersion")); fields.setdefault("kind", value.get("kind"))
        fields.setdefault("metadata", {}).setdefault("name", value.get("name")); fields["metadata"].setdefault("namespace", value.get("namespace"))
        return fields
    if not isinstance(value, dict):
        _raise("KUBERNETES_PROJECTION_FIELD_FORBIDDEN", hook_id)
    return value


def _object_identity(value: dict[str, Any]) -> tuple[Any, Any, Any, Any]:
    metadata = value.get("metadata", {}) if isinstance(value.get("metadata"), dict) else {}
    return metadata.get("name"), metadata.get("namespace"), metadata.get("uid"), metadata.get("resourceVersion")


def _leaf_token_paths(value: Any, prefix: tuple[str, ...] = ()) -> set[tuple[str, ...]]:
    if isinstance(value, dict):
        result: set[tuple[str, ...]] = set()
        for key, child in value.items(): result |= _leaf_token_paths(child, prefix + (key,))
        return result
    if isinstance(value, list):
        result = set()
        for child in value: result |= _leaf_token_paths(child, prefix + ("[]",))
        return result
    return {prefix}


def _hook_canonical(candidate: dict[str, Any], **_: Any) -> None:
    try: ensure_no_floats(candidate)
    except Exception: _raise("CANONICAL_FLOAT_FORBIDDEN", "VALIDATE_CANONICAL_NO_FLOATS_V1")


def _hook_references(candidate: dict[str, Any], policy: dict[str, Any], authenticated: dict[str, Any], **_: Any) -> None:
    hook_id = "VALIDATE_EVIDENCE_REF_HASH_PATH_ID_V1"
    references = list(_iter_evidence_refs(candidate))
    for reference in references:
        try: validate_evidence_ref(reference, policy)
        except Exception: _raise("EVIDENCE_REFERENCE_INVALID", hook_id)
    if candidate.get("document_type") in ("PAYLOAD_EVIDENCE_REF_V1", "DESCRIPTOR_EVIDENCE_REF_V1"):
        _resolve_reference(authenticated, candidate, hook_id)


def _hook_descriptor(candidate: dict[str, Any], authenticated: dict[str, Any], **_: Any) -> None:
    if candidate.get("document_type") in ("PAYLOAD_EVIDENCE_DESCRIPTOR_V1", "DESCRIPTOR_EVIDENCE_DESCRIPTOR_V1"):
        _resolved_candidate_descriptor(candidate, authenticated, "VALIDATE_DESCRIPTOR_CONTENT_IDENTITY_V1")


def _hook_attempt(candidate: dict[str, Any], policy: dict[str, Any], context: dict[str, Any], **_: Any) -> None:
    hook_id = "VALIDATE_ATTEMPT_PHASES_AND_FINALITY_V1"
    if candidate.get("document_type") == "ATTEMPT_VALIDATION_ENVELOPE_V1":
        try: validate_attempt_envelope(candidate, policy)
        except Exception: _raise("ATTEMPT_FINALITY_INVALID", hook_id)
    state = context["_derived_journal_context"]["state"]
    if state["terminal"] and candidate.get("document_type") == "KUBERNETES_REQUEST_V1":
        _raise("POST_TERMINAL_OPERATION", hook_id)


def _expected_evaluation(candidate: dict[str, Any], context: dict[str, Any], hook_id: str) -> dict[str, Any]:
    derived = context.get("_derived_journal_context")
    if not isinstance(derived, dict) or not isinstance(derived.get("evaluation"), dict):
        _raise("JOURNAL_EVALUATION_MARKER_MISSING", hook_id)
    expected = derived["evaluation"]
    frozen = EVALUATION_CONTEXTS.get(expected.get("predicate_id"))
    if frozen is None or expected.get("phase") != frozen["phase"] or expected.get("deadline_identity") != frozen["deadline_identity"]:
        _raise("JOURNAL_CONTEXT_MISMATCH", hook_id)
    if derived["state"]["state"] not in frozen["allowed_states"]:
        _raise("WORKLOAD_EVALUATION_CONTEXT_MISMATCH", hook_id)
    predicate = candidate.get("predicate_oracle_or_classification_id")
    if predicate is not None and predicate != expected["predicate_id"]:
        _raise("ADJUDICATION_EVALUATION_PHASE_MISMATCH", hook_id)
    return expected


def _workload_parser_identity() -> dict[str, Any]:
    return {
        "sregym_commit": SREGYM_COMMIT, "module_path": WORKLOAD_PARSER_MODULE,
        "source_sha256": WORKLOAD_PARSER_SOURCE_SHA256, "python_runtime": "3.12.3",
        "algorithm": WORKLOAD_PARSE_ALGORITHM, "version": 1,
    }


def _parse_workload_entry_jsonl(data: bytes, hook_id: str) -> list[dict[str, Any]]:
    try:
        text = data.decode("utf-8", errors="strict")
        if text and not text.endswith("\n"):
            raise ValueError
        entries = []
        for raw_line in text.splitlines(keepends=True):
            if not raw_line.endswith("\n"):
                raise ValueError
            entry = json.loads(raw_line[:-1])
            ensure_no_floats(entry)
            if canonical_json_bytes(entry) + b"\n" != raw_line.encode("utf-8"):
                raise ValueError
            if set(entry) != {"index", "time_binary64_hex", "number", "log", "ok"}:
                raise ValueError
            if not isinstance(entry["index"], int) or entry["index"] < 0 or not isinstance(entry["number"], int) or entry["number"] < 0 or not isinstance(entry["log"], str) or not isinstance(entry["ok"], bool):
                raise ValueError
            if not isinstance(entry["time_binary64_hex"], str) or BINARY64_RE.fullmatch(entry["time_binary64_hex"]) is None:
                raise ValueError
            value = struct.unpack(">d", bytes.fromhex(entry["time_binary64_hex"]))[0]
            if not math.isfinite(value):
                raise ValueError
            entries.append(entry)
        if [entry["index"] for entry in entries] != list(range(len(entries))):
            _raise("WORKLOAD_ENTRY_ORDER_INVALID", "VALIDATE_WORKLOAD_CARDINALITY_V1")
        values = [struct.unpack(">d", bytes.fromhex(entry["time_binary64_hex"]))[0] for entry in entries]
        if any(later < earlier for earlier, later in zip(values, values[1:])):
            _raise("WORKLOAD_TIMESTAMP_ORDER_INVALID", "VALIDATE_WORKLOAD_CARDINALITY_V1")
        return entries
    except ValidationFailure:
        raise
    except Exception:
        _raise("WORKLOAD_PARSE_RECOMPUTATION_MISMATCH", hook_id)


def _workload_subject(candidate: dict[str, Any], authenticated: dict[str, Any], hook_id: str) -> dict[str, Any]:
    window = candidate.get("workload_window_adjudication_identity")
    if isinstance(window, dict) and all(key in window for key in ("boundary_reference", "raw_log_reference", "parse_result_reference")):
        return candidate
    core = candidate.get("workload_window")
    matches = [
        row["descriptor"] for row in authenticated.values()
        if row["reference"].get("role") == "adjudication"
        and isinstance(row["descriptor"].get("workload_window_adjudication_identity"), dict)
        and all(row["descriptor"]["workload_window_adjudication_identity"].get(key) == core.get(key) for key in ("phase", "ordinal", "run_id", "attempt_id", "mutant_id", "repetition"))
    ] if isinstance(core, dict) else []
    if len(matches) != 1:
        _raise("HOOK_CONTEXT_MISSING", hook_id)
    return matches[0]


def _validate_workload(candidate: dict[str, Any], authenticated: dict[str, Any], context: dict[str, Any], schema: dict[str, Any]) -> None:
    hook_id = "VALIDATE_WORKLOAD_WINDOW_CONSISTENCY_V1"
    subject = _workload_subject(candidate, authenticated, hook_id)
    window = subject.get("workload_window_adjudication_identity")
    if not isinstance(window, dict):
        _raise("WORKLOAD_WINDOW_MISMATCH", hook_id)
    expected = _expected_evaluation(subject, context, hook_id)
    if window.get("phase") != expected["phase"] or WORKLOAD_WINDOWS.get(window.get("phase")) != window.get("ordinal"):
        _raise("WORKLOAD_EVALUATION_CONTEXT_MISMATCH", hook_id)
    for field in ("run_id", "attempt_id", "mutant_id", "repetition"):
        if window.get(field) != context[field]:
            _raise("RUN_ATTEMPT_MISMATCH", hook_id)
    log = _resolve_reference(authenticated, window.get("raw_log_reference"), hook_id, role="workload_log_bytes", payload=True)
    boundary = _resolve_reference(authenticated, window.get("boundary_reference"), hook_id, role="workload_boundary", payload=False)
    parsed = _resolve_reference(authenticated, window.get("parse_result_reference"), hook_id, role="workload_parse_result", payload=True)
    boundary_descriptor = boundary["descriptor"]
    prefix = _resolve_reference(authenticated, boundary_descriptor.get("raw_log_reference"), hook_id, role="workload_log_bytes", payload=True)
    pod = _resolve_reference(authenticated, boundary_descriptor.get("workload_pod_projection_reference"), hook_id, role="kubernetes_object_projection", payload=True)
    if parsed["descriptor"].get("raw_log_reference") != log["reference"] or parsed["descriptor"].get("boundary_reference") != boundary["reference"]:
        _raise("WORKLOAD_PARSE_REFERENCE_MISMATCH", hook_id)
    descriptors = (log["descriptor"], boundary_descriptor, parsed["descriptor"])
    for descriptor in descriptors:
        identity = descriptor.get("workload_window")
        if not isinstance(identity, dict):
            _raise("WORKLOAD_WINDOW_MISMATCH", hook_id)
        for field in ("phase", "ordinal", "run_id", "attempt_id", "mutant_id", "repetition"):
            if identity.get(field) != window.get(field):
                _raise("WORKLOAD_WINDOW_MISMATCH", hook_id)
    if boundary_descriptor.get("raw_log_sha256") != sha256_bytes(prefix["payload_bytes"]):
        _raise("PAYLOAD_HASH_MISMATCH", hook_id)
    if boundary_descriptor.get("raw_log_byte_length") != len(prefix["payload_bytes"]):
        _raise("PAYLOAD_SIZE_MISMATCH", hook_id)
    if not log["payload_bytes"].startswith(prefix["payload_bytes"]):
        _raise("WORKLOAD_RAW_PREFIX_MISMATCH", hook_id)
    prefix_entries = _parse_workload_entry_jsonl(prefix["payload_bytes"], hook_id)
    entries = _parse_workload_entry_jsonl(log["payload_bytes"], hook_id)
    if entries[:len(prefix_entries)] != prefix_entries:
        _raise("WORKLOAD_RAW_PREFIX_MISMATCH", hook_id)
    indexes = [entry["index"] for entry in entries]
    times = [entry["time_binary64_hex"] for entry in entries]
    if log["descriptor"].get("complete_entry_count") != len(entries) or log["descriptor"].get("entry_indexes") != indexes or log["descriptor"].get("entry_time_ieee754_binary64_hex") != times:
        _raise("WORKLOAD_PARSE_RECOMPUTATION_MISMATCH", hook_id)
    if boundary_descriptor.get("complete_entry_count") != len(prefix_entries) or boundary_descriptor.get("entry_time_ieee754_binary64_hex") != [entry["time_binary64_hex"] for entry in prefix_entries] or boundary_descriptor.get("request_count") != sum(entry["number"] for entry in prefix_entries):
        _raise("WORKLOAD_PARSE_RECOMPUTATION_MISMATCH", hook_id)
    fresh = entries[len(prefix_entries):]
    expected_parse = {
        "document_type": "WORKLOAD_PARSE_RESULT_V1", "schema_version": 1,
        "parser_identity": _workload_parser_identity(),
        "boundary_reference": boundary["reference"], "raw_log_reference": log["reference"],
        "workload_window": {key: window[key] for key in ("phase", "ordinal", "run_id", "attempt_id", "mutant_id", "repetition")},
        "complete_entry_count": len(entries), "entry_indexes": indexes,
        "entry_time_ieee754_binary64_hex": times,
        "prefix_complete_entry_count": len(prefix_entries),
        "prefix_byte_length": len(prefix["payload_bytes"]),
        "prefix_sha256": sha256_bytes(prefix["payload_bytes"]),
        "suffix_sha256": sha256_bytes(log["payload_bytes"][len(prefix["payload_bytes"]):]),
        "fresh_request_count": sum(entry["number"] for entry in fresh),
        "failure_marker_count": sum(1 for entry in fresh if not entry["ok"]),
    }
    parsed_payload = _parse_canonical_json_bytes(parsed["payload_bytes"], "WORKLOAD_PARSE_SCHEMA_INVALID", hook_id)
    try:
        Draft202012Validator({"$schema": schema["$schema"], "$ref": "#/$defs/workload_parse_result_payload", "$defs": schema["$defs"]}).validate(parsed_payload)
    except Exception:
        _raise("WORKLOAD_PARSE_SCHEMA_INVALID", hook_id)
    if parsed_payload.get("parser_identity") != _workload_parser_identity():
        _raise("WORKLOAD_PARSER_IDENTITY_MISMATCH", hook_id)
    if parsed_payload != expected_parse:
        _raise("WORKLOAD_PARSE_RECOMPUTATION_MISMATCH", hook_id)
    if parsed["descriptor"].get("fresh_request_count") != expected_parse["fresh_request_count"] or parsed["descriptor"].get("failure_marker_count") != expected_parse["failure_marker_count"]:
        _raise("WORKLOAD_PARSE_RECOMPUTATION_MISMATCH", hook_id)
    if expected_parse["fresh_request_count"] < 50 or expected_parse["failure_marker_count"] != 0:
        _raise("WORKLOAD_CARDINALITY_INVALID", "VALIDATE_WORKLOAD_CARDINALITY_V1")
    pod_object = _projection_object(pod["payload_bytes"], hook_id)
    pod_name, _namespace, pod_uid, _resource_version = _object_identity(pod_object)
    statuses = pod_object.get("status", {}).get("containerStatuses", []) if isinstance(pod_object.get("status"), dict) else []
    restart_count = sum(item.get("restartCount", 0) for item in statuses if isinstance(item, dict))
    if boundary_descriptor.get("pod_name") != pod_name or boundary_descriptor.get("pod_uid") != pod_uid:
        _raise("WORKLOAD_POD_IDENTITY_MISMATCH", hook_id)
    if boundary_descriptor.get("container_restart_count") != restart_count:
        _raise("WORKLOAD_RESTART_COUNT_MISMATCH", hook_id)
    adjudication_row = _resolved_candidate_descriptor(subject, authenticated, hook_id)
    if not (pod["publication_sequence"] <= prefix["publication_sequence"] < boundary["publication_sequence"] <= log["publication_sequence"] <= parsed["publication_sequence"] < adjudication_row["publication_sequence"]):
        _raise("PUBLICATION_ORDER_INVALID", hook_id)
    if context["validation_mode"] == CAPTURE_TIME_VALIDATION_LEVEL:
        source_hex = context["trusted_capture_source_bytes_hex"].get(log["reference"]["evidence_id"])
        if not isinstance(source_hex, str) or bytes.fromhex(source_hex) != log["payload_bytes"]:
            _raise("WORKLOAD_PARSE_RECOMPUTATION_MISMATCH", hook_id)


def _hook_workload_cardinality(candidate: dict[str, Any], authenticated: dict[str, Any], context: dict[str, Any], schema: dict[str, Any], **_: Any) -> None:
    _validate_workload(candidate, authenticated, context, schema)


def _hook_workload_window(candidate: dict[str, Any], authenticated: dict[str, Any], context: dict[str, Any], schema: dict[str, Any], **_: Any) -> None:
    _validate_workload(candidate, authenticated, context, schema)


def _hook_adjudication(candidate: dict[str, Any], authenticated: dict[str, Any], context: dict[str, Any], **_: Any) -> None:
    if candidate.get("role") != "adjudication" and candidate.get("document_type") != "ATTEMPT_VALIDATION_ENVELOPE_V1": return
    hook_id = "VALIDATE_ADJUDICATION_RAW_BACKING_V1"
    if candidate.get("role") != "adjudication":
        for reference in candidate.get("raw_evidence_references", []): _resolve_reference(authenticated, reference, hook_id, payload=True)
        return
    expected = _expected_evaluation(candidate, context, hook_id)
    frozen = EVALUATION_CONTEXTS[expected["predicate_id"]]
    if candidate.get("applicable_deadline") != expected["deadline_identity"]:
        _raise("ADJUDICATION_DEADLINE_MISMATCH", hook_id)
    raw_refs = candidate.get("raw_evidence_references")
    if not isinstance(raw_refs, list) or not raw_refs: _raise("ADJUDICATION_RAW_REFERENCE_REQUIRED", hook_id)
    candidate_row = _resolved_candidate_descriptor(candidate, authenticated, hook_id)
    evaluation_sequence = context["_derived_journal_context"].get("evaluation_sequence")
    if not isinstance(evaluation_sequence, int) or evaluation_sequence >= candidate_row["publication_sequence"]:
        _raise("JOURNAL_EVALUATION_MARKER_MISSING", hook_id)
    hashes = candidate.get("raw_evidence_sha256_per_reference")
    if not isinstance(hashes, list) or len(hashes) != len(raw_refs): _raise("ADJUDICATION_RAW_REFERENCE_REQUIRED", hook_id)
    for index, reference in enumerate(raw_refs):
        row = _resolve_reference(authenticated, reference, hook_id, payload=True)
        if row["reference"]["role"] not in frozen["allowed_raw_roles"]: _raise("ADJUDICATION_RAW_ROLE_INVALID", hook_id)
        if hashes[index] != row["reference"]["payload_sha256"]: _raise("PAYLOAD_HASH_MISMATCH", hook_id)
        if not (evaluation_sequence < row["publication_sequence"] < candidate_row["publication_sequence"]):
            _raise("PUBLICATION_ORDER_INVALID", hook_id)


def _hook_journal(candidate: dict[str, Any], context: dict[str, Any], **_: Any) -> None:
    hook_id = "VALIDATE_JOURNAL_HASH_CHAIN_V1"
    try:
        if candidate.get("document_type") == "JOURNAL_RECORD_V1" and candidate.get("canonical_current_entry_sha256") != journal_record_sha256(candidate): raise ValueError
        validate_journal_chain(context["attempt_journal_records"])
    except Exception: _raise("JOURNAL_CHAIN_INVALID", hook_id)


def _matching_request_rule(policy: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    for rule in policy["kubernetes_evidence_surface"]["resource_rules"] + policy["kubernetes_evidence_surface"]["subresource_rules"]:
        if rule["id"] == request.get("request_rule_id"): return rule
    _raise("KUBERNETES_REQUEST_RULE_INVALID", "VALIDATE_KUBERNETES_REQUEST_V1")


def _matched_name_predicate(rule: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    for predicate in rule["name_predicates_by_operation"].get(request.get("operation"), []):
        kind = predicate["type"]; name = request.get("name")
        if kind == "LIST_NO_NAME" and name == "": return predicate
        if kind == "EXACT_NAME" and name in predicate["allowed_names"]: return predicate
        if kind in ("CAPTURED_OBJECT_NAME", "CAPTURED_REPLACEMENT_POD_NAME") and isinstance(name, str) and name: return predicate
        if kind == "DETERMINISTIC_CHALLENGE_NAME" and isinstance(name, str) and name.startswith(predicate["prefix"]): return predicate
    _raise("KUBERNETES_REQUEST_RULE_INVALID", "VALIDATE_KUBERNETES_REQUEST_V1")


def _validate_challenge(policy: dict[str, Any], request: dict[str, Any], authenticated: dict[str, Any], context: dict[str, Any]) -> None:
    hook_id = "VALIDATE_KUBERNETES_REQUEST_V1"; identity = context.get("challenge_identity")
    if not isinstance(identity, dict): _raise("HOOK_CONTEXT_MISSING", hook_id)
    for field in ("run_id", "mutant_id", "repetition", "attempt_id"):
        if identity[field] != context[field]: _raise("KUBERNETES_CHALLENGE_IDENTITY_MISMATCH", hook_id)
    body_row = _resolve_reference(authenticated, identity["canonical_body_reference"], hook_id, role="kubernetes_object_projection", payload=True, unresolved_code="KUBERNETES_CAPTURE_UNRESOLVED")
    body = _parse_canonical_json_bytes(body_row["payload_bytes"], "KUBERNETES_CHALLENGE_BODY_INVALID", hook_id)
    spec_hash = sha256_bytes(canonical_json_bytes({"metadata": {"namespace": body.get("metadata", {}).get("namespace"), "labels": body.get("metadata", {}).get("labels")}, "spec": body.get("spec")}))
    if spec_hash != identity["frozen_spec_sha256"]: _raise("KUBERNETES_CHALLENGE_BODY_INVALID", hook_id)
    material = {"run_id": identity["run_id"], "mutant_id": identity["mutant_id"], "repetition": identity["repetition"], "attempt_id": identity["attempt_id"], "frozen_spec_sha256": spec_hash}
    expected_name = "sremut-challenge-" + sha256_bytes(canonical_json_bytes(material))[:16]
    if identity["expected_name"] != expected_name or request.get("name") != expected_name: _raise("KUBERNETES_CHALLENGE_IDENTITY_MISMATCH", hook_id)
    canonical_body_hash = sha256_bytes(canonical_json_bytes({"metadata": {"name": body.get("metadata", {}).get("name"), "namespace": body.get("metadata", {}).get("namespace"), "labels": body.get("metadata", {}).get("labels")}, "spec": body.get("spec")}))
    if identity["canonical_request_body_sha256"] != canonical_body_hash: _raise("KUBERNETES_CHALLENGE_BODY_INVALID", hook_id)
    profile = policy["challenge_request_binding"]; metadata = body.get("metadata", {}); spec = body.get("spec", {}); labels = metadata.get("labels", {})
    expected_labels = {"app.kubernetes.io/name": "sremut-challenge", "app.kubernetes.io/managed-by": "sremut", "sremut-run-id": identity["run_id"], "sremut-mutant": identity["mutant_id"], "sremut-attempt": identity["attempt_id"]}
    containers = spec.get("containers", [])
    if metadata.get("name") != expected_name or metadata.get("namespace") != profile["namespace"] or labels != expected_labels or len(containers) != 1:
        _raise("KUBERNETES_CHALLENGE_BODY_INVALID", hook_id)
    container = containers[0]; security = profile["security_context"]
    expected_security = {"runAsNonRoot": security["run_as_non_root"], "runAsUser": security["run_as_user"], "runAsGroup": security["run_as_group"], "readOnlyRootFilesystem": security["read_only_root_filesystem"], "allowPrivilegeEscalation": security["allow_privilege_escalation"], "capabilities": {"drop": security["capabilities_drop"]}, "seccompProfile": {"type": security["seccomp_profile"]}}
    expected_resources = profile["resources"]
    if container.get("image") != profile["image"] or container.get("imagePullPolicy") != profile["image_pull_policy"] or container.get("command") != profile["container_command"] or container.get("securityContext") != expected_security or container.get("resources") != expected_resources:
        _raise("KUBERNETES_CHALLENGE_BODY_INVALID", hook_id)
    if spec.get("automountServiceAccountToken") != profile["automount_service_account_token"] or spec.get("restartPolicy") != profile["restart_policy"] or spec.get("activeDeadlineSeconds") != profile["active_deadline_seconds"] or spec.get("terminationGracePeriodSeconds") != profile["termination_grace_period_seconds"]:
        _raise("KUBERNETES_CHALLENGE_BODY_INVALID", hook_id)


def _request_identity_material(request: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    return {"request": deepcopy(request), "run_id": context["run_id"], "attempt_id": context["attempt_id"]}


def _resolve_request_identity(request: dict[str, Any], authenticated: dict[str, Any], context: dict[str, Any], hook_id: str) -> dict[str, Any]:
    derived = context.get("_derived_journal_context")
    operation = derived.get("operation") if isinstance(derived, dict) else None
    if not isinstance(operation, dict):
        _raise("JOURNAL_OPERATION_MARKER_MISSING", hook_id)
    row = _resolve_reference(
        authenticated, operation.get("request_reference"), hook_id,
        role="kubernetes_request_identity", payload=False,
        unresolved_code="KUBERNETES_REQUEST_IDENTITY_MISMATCH",
    )
    descriptor = row["descriptor"]
    material = _request_identity_material(request, context)
    if descriptor.get("canonical_request") != request or descriptor.get("request_identity_sha256") != sha256_bytes(canonical_json_bytes(material)):
        _raise("KUBERNETES_REQUEST_IDENTITY_MISMATCH", hook_id)
    for field in ("request_rule_id", "operation", "resource", "subresource", "namespace"):
        if descriptor.get(field) != request.get(field):
            _raise("KUBERNETES_REQUEST_IDENTITY_MISMATCH", hook_id)
    if descriptor.get("selector") != request.get("label_selector") or descriptor.get("name_rule") != request.get("name"):
        _raise("KUBERNETES_REQUEST_IDENTITY_MISMATCH", hook_id)
    marker_sequence = derived.get("operation_sequence")
    if not isinstance(marker_sequence, int) or row["publication_sequence"] >= marker_sequence:
        _raise("JOURNAL_OPERATION_MARKER_MISSING", hook_id)
    return row


def _controller_reference(value: dict[str, Any], kind: str) -> dict[str, Any] | None:
    metadata = value.get("metadata") if isinstance(value.get("metadata"), dict) else {}
    owners = metadata.get("ownerReferences") if isinstance(metadata.get("ownerReferences"), list) else []
    matches = [owner for owner in owners if isinstance(owner, dict) and owner.get("controller") is True and owner.get("kind") == kind]
    return matches[0] if len(matches) == 1 else None


def _validate_pod_capture(row: dict[str, Any], authenticated: dict[str, Any], context: dict[str, Any], predicate_type: str, request_marker_sequence: int) -> tuple[str, str, str, str]:
    hook_id = "VALIDATE_KUBERNETES_REQUEST_V1"
    descriptor = row["descriptor"]
    reference = row["reference"]
    if reference.get("projection_class") != "POD_IDENTITY_CAPTURE_V1" or descriptor.get("projection_schema_id") != "POD_IDENTITY_CAPTURE_V1":
        _raise("KUBERNETES_CAPTURE_CLASS_INVALID", hook_id)
    rules = context["_authenticated_policy"]["kubernetes_captured_identity_binding"]["rules"][predicate_type]
    capture_state = descriptor.get("capture_state")
    if capture_state not in rules["allowed_capture_states"] or row["publication_state"] != capture_state:
        _raise("KUBERNETES_CAPTURE_STATE_INVALID", hook_id)
    pod = _projection_object(row["payload_bytes"], hook_id)
    name, namespace, uid, resource_version = _object_identity(pod)
    metadata = pod.get("metadata", {}) if isinstance(pod.get("metadata"), dict) else {}
    status = pod.get("status", {}) if isinstance(pod.get("status"), dict) else {}
    ready = any(
        isinstance(condition, dict) and condition.get("type") == "Ready" and condition.get("status") == "True"
        for condition in status.get("conditions", [])
    )
    if (
        pod.get("apiVersion") != "v1" or pod.get("kind") != "Pod" or namespace != "social-network"
        or metadata.get("labels", {}).get("service") != "user-service"
        or metadata.get("deletionTimestamp") is not None or not ready
        or re.fullmatch(r"(?:[0-9]{1,3}\.){3}[0-9]{1,3}", str(status.get("podIP"))) is None
    ):
        _raise("KUBERNETES_CAPTURE_IDENTITY_MISMATCH", hook_id)
    rs_ref = _controller_reference(pod, "ReplicaSet")
    rs_row = _resolve_reference(authenticated, descriptor.get("replicaset_projection_reference"), hook_id, role="kubernetes_object_projection", payload=True, unresolved_code="KUBERNETES_CAPTURE_UNRESOLVED")
    dep_row = _resolve_reference(authenticated, descriptor.get("deployment_projection_reference"), hook_id, role="kubernetes_object_projection", payload=True, unresolved_code="KUBERNETES_CAPTURE_UNRESOLVED")
    if rs_row["reference"].get("projection_class") != "REPLICASET_OWNER_CAPTURE_V1" or dep_row["reference"].get("projection_class") != "DEPLOYMENT_OWNER_CAPTURE_V1":
        _raise("KUBERNETES_CAPTURE_CLASS_INVALID", hook_id)
    rs = _projection_object(rs_row["payload_bytes"], hook_id)
    dep = _projection_object(dep_row["payload_bytes"], hook_id)
    rs_name, rs_namespace, rs_uid, _rs_rv = _object_identity(rs)
    dep_name, dep_namespace, dep_uid, _dep_rv = _object_identity(dep)
    dep_ref = _controller_reference(rs, "Deployment")
    if rs_ref is None or dep_ref is None or rs_ref.get("name") != rs_name or rs_ref.get("uid") != rs_uid or dep_ref.get("name") != dep_name or dep_ref.get("uid") != dep_uid or rs_namespace != "social-network" or dep_namespace != "social-network" or dep_name != "user-service":
        _raise("KUBERNETES_CAPTURE_OWNER_INVALID", hook_id)
    selection = _resolve_reference(authenticated, descriptor.get("selection_evidence_reference"), hook_id, role="healthy_prestate", payload=True, unresolved_code="KUBERNETES_CAPTURE_SELECTION_INVALID")
    selected = selection["descriptor"].get("captured_replica_baseline", {})
    if selected.get("selection_rule_id") != "LEXICOGRAPHIC_READY_USER_SERVICE_POD_V1" or selected.get("selected_pod_name") != name or selected.get("selected_pod_uid") != uid:
        _raise("KUBERNETES_CAPTURE_SELECTION_INVALID", hook_id)
    if not (selection["publication_sequence"] < row["publication_sequence"] < request_marker_sequence):
        _raise("KUBERNETES_CAPTURE_SELECTION_INVALID", hook_id)
    if rs_row["publication_sequence"] >= row["publication_sequence"] or dep_row["publication_sequence"] >= row["publication_sequence"]:
        _raise("KUBERNETES_CAPTURE_OWNER_INVALID", hook_id)
    return name, namespace, uid, resource_version


def _validate_restoration_request(policy: dict[str, Any], schema: dict[str, Any], request: dict[str, Any], authenticated: dict[str, Any], context: dict[str, Any], capture_row: dict[str, Any] | None) -> None:
    hook_id = "VALIDATE_KUBERNETES_REQUEST_V1"
    intent = _resolve_reference(authenticated, request.get("intent_reference"), hook_id, payload=False, unresolved_code="RESTORATION_INTENT_UNRESOLVED")
    if intent["reference"]["role"] != "mutation_intent":
        _raise("RESTORATION_INTENT_ROLE_INVALID", hook_id)
    descriptor = intent["descriptor"]
    operation = request.get("authorized_operation_kind")
    if descriptor.get("operation_kind") != operation:
        _raise("RESTORATION_OPERATION_MISMATCH", hook_id)
    if descriptor.get("resource_kind") != "Service" or descriptor.get("namespace") != "social-network" or descriptor.get("object_name") != "user-service":
        _raise("RESTORATION_TARGET_MISMATCH", hook_id)
    restoration_ref = descriptor.get("service_restoration_body_reference")
    if not isinstance(restoration_ref, dict):
        _raise("RESTORATION_REFERENCE_MISSING", hook_id)
    restoration = _resolve_reference(authenticated, restoration_ref, hook_id, role="kubernetes_object_projection", payload=True, unresolved_code="RESTORATION_BODY_UNRESOLVED")
    if restoration["reference"].get("projection_class") != "SERVICE_RESTORATION_BODY_V1":
        _raise("RESTORATION_BODY_UNRESOLVED", hook_id)
    if descriptor.get("service_restoration_body_sha256") != restoration["reference"].get("payload_sha256"):
        _raise("RESTORATION_BODY_HASH_MISMATCH", hook_id)
    restoration_doc = _parse_canonical_json_bytes(restoration["payload_bytes"], "RESTORATION_BODY_HASH_MISMATCH", hook_id)
    source_ref = restoration_doc.get("source_service_reference")
    if not isinstance(source_ref, dict):
        _raise("RESTORATION_SOURCE_REFERENCE_MISSING", hook_id)
    source_row = _resolve_reference(authenticated, source_ref, hook_id, role="kubernetes_object_projection", payload=True, unresolved_code="RESTORATION_SOURCE_UNRESOLVED")
    if source_row["reference"].get("projection_class") != "SERVICE_RESTORATION_SOURCE_V1" or source_row["descriptor"].get("projection_schema_id") != "SERVICE_RESTORATION_SOURCE_V1":
        _raise("RESTORATION_SOURCE_CLASS_INVALID", hook_id)
    source = _projection_object(source_row["payload_bytes"], hook_id)
    try:
        validate_service_restoration_body(policy, schema, restoration_doc, source)
    except Exception:
        _raise("RESTORATION_DERIVATION_MISMATCH", hook_id)
    source_name, source_namespace, uid, resource_version = _object_identity(source)
    if source.get("apiVersion") != "v1" or source.get("kind") != "Service" or source_name != "user-service" or source_namespace != "social-network":
        _raise("RESTORATION_SERVICE_MISMATCH", hook_id)
    expected_doc = derive_service_restoration_body(
        policy, schema, source, restoration_doc["source_request_reference"],
        restoration_doc["capture_timestamp"], source_row["reference"],
    )
    if canonical_json_bytes(expected_doc) != restoration["payload_bytes"]:
        _raise("RESTORATION_DERIVATION_MISMATCH", hook_id)
    if capture_row is None or capture_row["reference"] != source_row["reference"]:
        _raise("RESTORATION_SOURCE_UNRESOLVED", hook_id)
    if descriptor.get("expected_uid_when_existing") != uid or request.get("captured_object_uid") != uid or request.get("uid_precondition") != uid:
        _raise("RESTORATION_UID_MISMATCH", hook_id)
    if descriptor.get("expected_resource_version_when_applicable") != resource_version or request.get("captured_object_resource_version") != resource_version:
        _raise("RESTORATION_UID_MISMATCH", hook_id)
    if restoration_doc.get("source_service_uid") != uid or restoration_doc.get("source_service_resource_version") != resource_version:
        _raise("RESTORATION_SERVICE_MISMATCH", hook_id)
    derived = context["_derived_journal_context"]
    dispatch_sequence = derived.get("operation_sequence")
    if not isinstance(dispatch_sequence, int):
        _raise("JOURNAL_OPERATION_MARKER_MISSING", hook_id)
    if not (source_row["publication_sequence"] < restoration["publication_sequence"] < intent["publication_sequence"] < dispatch_sequence):
        _raise("RESTORATION_PREPUBLICATION_FAILURE", hook_id)
    state = derived["state"]
    if state["terminal"]:
        _raise("POST_TERMINAL_OPERATION", hook_id)
    allowed = {
        "INITIAL_USER_SERVICE_DELETION": ["HEALTHY_STATE_CAPTURED"],
        "MUTANT_SERVICE_DELETION": ["CONTRACT_EVALUATED", "RESTORE_STARTED"],
        "MUTANT_SERVICE_CREATION": ["HEALTHY_STATE_CAPTURED"],
        "RESTORED_SERVICE_CREATION": ["RESTORE_STARTED"],
    }.get(operation, [])
    if state["state"] not in allowed:
        _raise("STATE_OPERATION_FORBIDDEN", hook_id)
    if request.get("operation") == "CREATE":
        body = _resolve_reference(authenticated, derived["operation"].get("request_body_reference"), hook_id, role="kubernetes_object_projection", payload=True, unresolved_code="RESTORATION_BODY_UNRESOLVED")
        if body["reference"] != restoration["reference"] or body["payload_bytes"] != restoration["payload_bytes"]:
            _raise("RESTORATION_BODY_HASH_MISMATCH", hook_id)
    if context["validation_mode"] == CAPTURE_TIME_VALIDATION_LEVEL:
        source_hex = context["trusted_capture_source_bytes_hex"].get(source_row["reference"]["evidence_id"])
        if not isinstance(source_hex, str) or bytes.fromhex(source_hex) != source_row["payload_bytes"]:
            _raise("KUBERNETES_SOURCE_PROJECTION_MISMATCH", hook_id)


def _hook_kubernetes_request(candidate: dict[str, Any], policy: dict[str, Any], schema: dict[str, Any], authenticated: dict[str, Any], context: dict[str, Any], **_: Any) -> None:
    if candidate.get("document_type") != "KUBERNETES_REQUEST_V1":
        return
    hook_id = "VALIDATE_KUBERNETES_REQUEST_V1"
    request_row = _resolve_request_identity(candidate, authenticated, context, hook_id)
    rule = _matching_request_rule(policy, candidate)
    predicate = _matched_name_predicate(rule, candidate)
    derived = context["_derived_journal_context"]
    marker_sequence = derived.get("operation_sequence")
    if not isinstance(marker_sequence, int):
        _raise("JOURNAL_OPERATION_MARKER_MISSING", hook_id)
    operation_context = derived["operation"]
    for field in ("operation_kind", "request_rule_id", "resource", "namespace", "name"):
        candidate_value = candidate.get("authorized_operation_kind") if field == "operation_kind" else candidate.get(field)
        if operation_context.get(field) != candidate_value:
            _raise("JOURNAL_CONTEXT_MISMATCH", hook_id)
    if operation_context.get("request_reference") != request_row["reference"]:
        _raise("KUBERNETES_REQUEST_IDENTITY_MISMATCH", hook_id)
    if derived["state"]["terminal"]:
        _raise("POST_TERMINAL_OPERATION", hook_id)
    intent = None
    if candidate.get("operation") in ("CREATE", "DELETE"):
        intent = _resolve_reference(authenticated, candidate.get("intent_reference"), hook_id, role="mutation_intent", payload=False, unresolved_code="RESTORATION_INTENT_UNRESOLVED")
        if intent["publication_sequence"] >= marker_sequence:
            _raise("RESTORATION_PREPUBLICATION_FAILURE", hook_id)
    capture_row = None
    if predicate["type"] in ("CAPTURED_OBJECT_NAME", "CAPTURED_REPLACEMENT_POD_NAME") or candidate.get("operation") == "DELETE":
        capture_row = _resolve_reference(
            authenticated, candidate.get("captured_object_reference"), hook_id,
            role="kubernetes_object_projection", payload=True,
            unresolved_code="RESTORATION_SOURCE_UNRESOLVED" if candidate.get("resource") == "services" else "KUBERNETES_CAPTURE_UNRESOLVED",
        )
        if candidate.get("resource") == "pods":
            name, namespace, uid, resource_version = _validate_pod_capture(capture_row, authenticated, context, predicate["type"], marker_sequence)
        elif candidate.get("resource") == "services":
            if capture_row["reference"].get("projection_class") != "SERVICE_RESTORATION_SOURCE_V1" or capture_row["descriptor"].get("projection_schema_id") != "SERVICE_RESTORATION_SOURCE_V1":
                _raise("KUBERNETES_CAPTURE_CLASS_INVALID", hook_id)
            name, namespace, uid, resource_version = _object_identity(_projection_object(capture_row["payload_bytes"], hook_id))
            if capture_row["descriptor"].get("capture_state") != capture_row["publication_state"] or capture_row["publication_state"] not in policy["kubernetes_captured_identity_binding"]["rules"]["CAPTURED_OBJECT_NAME"]["allowed_capture_states"]:
                _raise("KUBERNETES_CAPTURE_STATE_INVALID", hook_id)
        else:
            _raise("KUBERNETES_CAPTURE_CLASS_INVALID", hook_id)
        if candidate.get("name") != name or candidate.get("namespace") != namespace or candidate.get("captured_object_name") != name or candidate.get("captured_object_uid") != uid or candidate.get("captured_object_resource_version") != resource_version:
            _raise("KUBERNETES_CAPTURE_IDENTITY_MISMATCH", hook_id)
        if candidate.get("operation") == "DELETE" and candidate.get("uid_precondition") != uid:
            _raise("KUBERNETES_CAPTURE_IDENTITY_MISMATCH", hook_id)
        if intent is not None and not (capture_row["publication_sequence"] < intent["publication_sequence"]):
            _raise("KUBERNETES_CAPTURE_SELECTION_INVALID", hook_id)
    if predicate["type"] == "DETERMINISTIC_CHALLENGE_NAME":
        _validate_challenge(policy, candidate, authenticated, context)
    try:
        validate_kubernetes_request(policy, candidate)
    except Exception:
        _raise("KUBERNETES_REQUEST_RULE_INVALID", hook_id)
    if candidate.get("resource") == "services" and candidate.get("name") == "user-service" and candidate.get("operation") in ("CREATE", "DELETE"):
        _validate_restoration_request(policy, schema, candidate, authenticated, context, capture_row)


def _hook_kubernetes_response(candidate: dict[str, Any], policy: dict[str, Any], authenticated: dict[str, Any], context: dict[str, Any], **_: Any) -> None:
    if candidate.get("document_type") != "KUBERNETES_LIST_RESPONSE_V1":
        return
    hook_id = "VALIDATE_KUBERNETES_RESPONSE_V1"
    request = candidate["request"]
    request_row = _resolve_request_identity(request, authenticated, context, hook_id)
    try:
        validate_kubernetes_response(policy, candidate)
    except Exception:
        _raise("KUBERNETES_LIST_INVALID", hook_id)
    operation = context["_derived_journal_context"]["operation"]
    projection_row = _resolve_reference(authenticated, operation.get("projection_reference"), hook_id, role="kubernetes_object_projection", payload=True, unresolved_code="KUBERNETES_PROJECTION_REQUEST_MISMATCH")
    if _parse_canonical_json_bytes(projection_row["payload_bytes"], "KUBERNETES_PROJECTION_FIELD_FORBIDDEN", hook_id) != candidate:
        _raise("KUBERNETES_PROJECTION_REQUEST_MISMATCH", hook_id)
    if projection_row["descriptor"].get("request_identity_reference") != request_row["reference"]:
        _raise("KUBERNETES_PROJECTION_REQUEST_MISMATCH", hook_id)
    if projection_row["descriptor"].get("projection_schema_id") not in (None, "KUBERNETES_OBJECT_PROJECTION_V1"):
        _raise("KUBERNETES_CAPTURE_CLASS_INVALID", hook_id)
    rule = _matching_request_rule(policy, request)
    allowed = {tuple(path) for path in rule["projection_paths"]}
    if len(candidate["items"]) > rule["maximum_list_items"]:
        _raise("KUBERNETES_LIST_INVALID", hook_id)
    for item in candidate["items"]:
        leaves = _leaf_token_paths(item["projected_fields"])
        if any(path not in allowed for path in leaves):
            _raise("KUBERNETES_PROJECTION_FIELD_FORBIDDEN", hook_id)
        if item["apiVersion"] != rule["api_version"] or item["kind"] != rule["response_kind"] or item["namespace"] != rule["namespace"]:
            _raise("KUBERNETES_LIST_INVALID", hook_id)
    if projection_row["publication_sequence"] <= request_row["publication_sequence"]:
        _raise("PUBLICATION_ORDER_INVALID", hook_id)
    if context["validation_mode"] == CAPTURE_TIME_VALIDATION_LEVEL:
        source_hex = context["trusted_capture_source_bytes_hex"].get(projection_row["reference"]["evidence_id"])
        if not isinstance(source_hex, str):
            _raise("KUBERNETES_SOURCE_PROJECTION_MISMATCH", hook_id)
        source = _parse_canonical_json_bytes(bytes.fromhex(source_hex), "KUBERNETES_SOURCE_PROJECTION_MISMATCH", hook_id)
        source_rv = source.get("list_metadata", {}).get("resourceVersion") if isinstance(source, dict) else None
        projected_rv = candidate.get("list_metadata", {}).get("resourceVersion")
        if source_rv != projected_rv or projection_row["descriptor"].get("source_list_resource_version") != source_rv:
            _raise("KUBERNETES_LIST_RESOURCE_VERSION_MISMATCH", hook_id)
        if source != candidate:
            _raise("KUBERNETES_SOURCE_PROJECTION_MISMATCH", hook_id)


def _hook_restoration(candidate: dict[str, Any], policy: dict[str, Any], schema: dict[str, Any], authenticated: dict[str, Any], context: dict[str, Any], **_: Any) -> None:
    hook_id = "VALIDATE_SERVICE_RESTORATION_BODY_V1"
    if candidate.get("document_type") == "SERVICE_RESTORATION_BODY_V1":
        matches = [
            row for row in authenticated.values()
            if row["reference"].get("projection_class") == "SERVICE_RESTORATION_BODY_V1"
            and row["payload_bytes"] is not None
            and _parse_canonical_json_bytes(row["payload_bytes"], "RESTORATION_BODY_HASH_MISMATCH", hook_id) == candidate
        ]
        if len(matches) != 1:
            _raise("RESTORATION_BODY_UNRESOLVED", hook_id)
        source_ref = candidate.get("source_service_reference")
        if not isinstance(source_ref, dict):
            _raise("RESTORATION_SOURCE_REFERENCE_MISSING", hook_id)
        source_row = _resolve_reference(authenticated, source_ref, hook_id, role="kubernetes_object_projection", payload=True, unresolved_code="RESTORATION_SOURCE_UNRESOLVED")
        if source_row["reference"].get("projection_class") != "SERVICE_RESTORATION_SOURCE_V1":
            _raise("RESTORATION_SOURCE_CLASS_INVALID", hook_id)
        source = _projection_object(source_row["payload_bytes"], hook_id)
        try:
            validate_service_restoration_body(policy, schema, candidate, source)
            expected = derive_service_restoration_body(policy, schema, source, candidate["source_request_reference"], candidate["capture_timestamp"], source_row["reference"])
        except Exception:
            _raise("RESTORATION_DERIVATION_MISMATCH", hook_id)
        if canonical_json_bytes(expected) != matches[0]["payload_bytes"]:
            _raise("RESTORATION_DERIVATION_MISMATCH", hook_id)
        if source_row["publication_sequence"] >= matches[0]["publication_sequence"]:
            _raise("RESTORATION_PREPUBLICATION_FAILURE", hook_id)
    elif candidate.get("role") == "mutation_receipt":
        receipt = _resolved_candidate_descriptor(candidate, authenticated, hook_id)
        for field in COMMON_METADATA:
            if field not in candidate:
                _raise("MUTATION_RECEIPT_IDENTITY_INVALID", hook_id)
        if receipt["publication_state"] in policy["verified_attempt_state_machine"]["terminal_states"]:
            _raise("MUTATION_RECEIPT_PUBLICATION_INVALID", hook_id)
        if candidate.get("service_restoration_body_sha256") is not None:
            body = _resolve_reference(authenticated, candidate.get("service_restoration_body_reference"), hook_id, role="kubernetes_object_projection", payload=True, unresolved_code="RESTORATION_BODY_UNRESOLVED")
            request = _resolve_reference(authenticated, candidate.get("operation_request_reference"), hook_id, role="kubernetes_request_identity", payload=False, unresolved_code="MUTATION_RECEIPT_REQUEST_MISMATCH")
            observation = _resolve_reference(authenticated, candidate.get("post_create_observation_reference"), hook_id, role="kubernetes_object_projection", payload=True, unresolved_code="RESTORATION_BODY_UNRESOLVED")
            if body["reference"].get("projection_class") != "SERVICE_RESTORATION_BODY_V1" or body["reference"].get("payload_sha256") != candidate["service_restoration_body_sha256"]:
                _raise("RESTORATION_BODY_HASH_MISMATCH", hook_id)
            request_doc = request["descriptor"].get("canonical_request")
            if not isinstance(request_doc, dict) or request_doc.get("operation") != "CREATE" or request_doc.get("resource") != "services" or request_doc.get("name") != "user-service":
                _raise("MUTATION_RECEIPT_REQUEST_MISMATCH", hook_id)
            marker = context["_derived_journal_context"].get("operation_sequence")
            if not isinstance(marker, int) or marker >= receipt["publication_sequence"]:
                _raise("MUTATION_RECEIPT_PUBLICATION_INVALID", hook_id)
            if body["publication_sequence"] >= marker or observation["publication_sequence"] >= receipt["publication_sequence"]:
                _raise("MUTATION_RECEIPT_PUBLICATION_INVALID", hook_id)


def _hook_sensitive(candidate: dict[str, Any], authenticated: dict[str, Any], **_: Any) -> None:
    hook_id = "VALIDATE_SENSITIVE_CAPTURE_V1"
    rows: list[dict[str, Any]] = []
    if candidate.get("document_type") in ("PAYLOAD_EVIDENCE_DESCRIPTOR_V1", "DESCRIPTOR_EVIDENCE_DESCRIPTOR_V1"):
        rows.append(_resolved_candidate_descriptor(candidate, authenticated, hook_id))
    for reference in _iter_evidence_refs(candidate):
        row = authenticated.get(reference.get("evidence_id"));
        if row is not None and row not in rows: rows.append(row)
    for row in rows:
        if detect_sensitive(row["descriptor_bytes"], row["descriptor"]) is not None: _raise("SENSITIVE_CAPTURE_REJECTED", hook_id)
        if row["payload_bytes"] is not None:
            structured = None
            try: structured = json.loads(row["payload_bytes"].decode("utf-8"))
            except Exception: pass
            if detect_sensitive(row["payload_bytes"], structured) is not None: _raise("SENSITIVE_CAPTURE_REJECTED", hook_id)


_HOOK_IMPLEMENTATIONS = {
    "VALIDATE_CANONICAL_NO_FLOATS_V1": _hook_canonical,
    "VALIDATE_EVIDENCE_REF_HASH_PATH_ID_V1": _hook_references,
    "VALIDATE_DESCRIPTOR_CONTENT_IDENTITY_V1": _hook_descriptor,
    "VALIDATE_ATTEMPT_PHASES_AND_FINALITY_V1": _hook_attempt,
    "VALIDATE_WORKLOAD_CARDINALITY_V1": _hook_workload_cardinality,
    "VALIDATE_WORKLOAD_WINDOW_CONSISTENCY_V1": _hook_workload_window,
    "VALIDATE_ADJUDICATION_RAW_BACKING_V1": _hook_adjudication,
    "VALIDATE_JOURNAL_HASH_CHAIN_V1": _hook_journal,
    "VALIDATE_KUBERNETES_REQUEST_V1": _hook_kubernetes_request,
    "VALIDATE_KUBERNETES_RESPONSE_V1": _hook_kubernetes_response,
    "VALIDATE_SERVICE_RESTORATION_BODY_V1": _hook_restoration,
    "VALIDATE_SENSITIVE_CAPTURE_V1": _hook_sensitive,
}


def _applicable_hook_ids(policy: dict[str, Any], candidate: dict[str, Any]) -> list[str]:
    kind = candidate.get("document_type"); role = candidate.get("role", "NONE")
    matches = [row for row in policy["full_admissibility_validation"]["hook_applicability_matrix"] if row["document_kind"] == kind and role in row["roles"]]
    if len(matches) != 1: _raise("UNKNOWN_DOCUMENT_ROLE", None)
    selected = set(matches[0]["hooks"])
    return [hook for hook in RUNTIME_VALIDATOR_HOOKS if hook in selected]


def _expected_attempt_manifest(context: dict[str, Any], authenticated: dict[str, Any], journal_bytes: bytes) -> bytes:
    rows = [("journal/attempt.jsonl", sha256_bytes(journal_bytes))]
    for row in authenticated.values():
        rows.append((row["reference"]["descriptor_relative_path"], sha256_bytes(row["descriptor_bytes"])))
        if row["payload_bytes"] is not None:
            rows.append((row["reference"]["payload_relative_path"], sha256_bytes(row["payload_bytes"])))
    ordered = sorted(rows)
    if len({path for path, _digest in ordered}) != len(ordered):
        raise ValueError("duplicate sealed path")
    return "".join(f"{digest}  {path}\n" for path, digest in ordered).encode("utf-8")


def _verify_external_seal(context: dict[str, Any], authenticated: dict[str, Any], derived: dict[str, Any], policy_rows: dict[str, str], policy_manifest_bytes: bytes) -> None:
    hook_id = "VALIDATE_JOURNAL_HASH_CHAIN_V1"
    seal = context.get("offline_seal")
    if not isinstance(seal, dict):
        _raise("EXTERNAL_SEAL_MISSING", hook_id)
    try:
        manifest_bytes = bytes.fromhex(seal["terminal_manifest_bytes_hex"])
    except Exception:
        _raise("EXTERNAL_MANIFEST_INVALID", hook_id)
    external_hash = seal.get("externally_recorded_terminal_manifest_sha256")
    if sha256_bytes(manifest_bytes) != external_hash:
        _raise("EXTERNAL_MANIFEST_HASH_MISMATCH", hook_id)
    expected_manifest = _expected_attempt_manifest(context, authenticated, derived["journal_bytes"])
    if manifest_bytes != expected_manifest:
        _raise("EXTERNAL_MANIFEST_COVERAGE_MISMATCH", hook_id)
    expected_paths = tuple(path for path, _digest in sorted(
        [("journal/attempt.jsonl", sha256_bytes(derived["journal_bytes"]))]
        + [(row["reference"]["descriptor_relative_path"], sha256_bytes(row["descriptor_bytes"])) for row in authenticated.values()]
        + [(row["reference"]["payload_relative_path"], sha256_bytes(row["payload_bytes"])) for row in authenticated.values() if row["payload_bytes"] is not None]
    ))
    _parse_sha256_manifest_bytes(manifest_bytes, expected_paths, "EXTERNAL_MANIFEST_INVALID", hook_id)
    if seal.get("run_id") != context["run_id"] or seal.get("attempt_id") != context["attempt_id"] or seal.get("terminal_state") != derived["state"]["state"] or not derived["state"]["terminal"]:
        _raise("EXTERNAL_MANIFEST_INVALID", hook_id)
    if seal.get("sealed_journal_sha256") != sha256_bytes(derived["journal_bytes"]):
        _raise("EXTERNAL_MANIFEST_COVERAGE_MISMATCH", hook_id)
    anchor = seal.get("aggregation_anchor", {})
    if anchor.get("attempt_root_identifier") != seal.get("attempt_root_identifier") or anchor.get("manifest_relative_path") != seal.get("expected_manifest_relative_path") or anchor.get("terminal_manifest_sha256") != external_hash or anchor.get("recorded_by") != "IMMUTABLE_RUN_INDEX_OUTSIDE_ATTEMPT_ROOT" or anchor.get("cited_by_result_aggregation") is not True:
        _raise("EXTERNAL_MANIFEST_HASH_MISMATCH", hook_id)
    release = seal.get("runner_release_binding", {})
    if release != {
        "evidence_policy_annotated_tag": "sremut-missing-service-evidence-policy-v1",
        "evidence_policy_checksum_manifest_sha256": sha256_bytes(policy_manifest_bytes),
        "evidence_policy_sha256": policy_rows[POLICY_REL],
        "evidence_policy_schema_sha256": policy_rows[SCHEMA_REL],
        "dispatcher_id": FULL_ADMISSIBILITY_DISPATCHER_ID,
    }:
        _raise("EXTERNAL_MANIFEST_INVALID", hook_id)


def full_admissibility_dispatch(candidate: Any, authenticated_policy_input: Any, resolved_context: Any) -> dict[str, Any]:
    try:
        policy, schema, policy_rows, policy_manifest_bytes = authenticate_policy_input(authenticated_policy_input)
    except ValidationFailure as error:
        return _failure_result(error.failure_code, error.hook_id, candidate)
    except Exception:
        return _failure_result("VALIDATOR_EXECUTION_FAILURE", None, candidate)
    try:
        ensure_no_floats(candidate)
        validate_structural_schema_branch(candidate, schema)
    except Exception:
        return _failure_result("STRUCTURAL_SCHEMA_INVALID", None, candidate)
    try:
        authenticated, derived = authenticate_resolved_evidence_context(policy, schema, resolved_context)
        context = deepcopy(resolved_context)
        context["_derived_journal_context"] = derived
        context["_authenticated_policy"] = policy
        if context["validation_mode"] == OFFLINE_VALIDATION_LEVEL:
            _verify_external_seal(context, authenticated, derived, policy_rows, policy_manifest_bytes)
        hooks = _applicable_hook_ids(policy, candidate)
        if not hooks:
            _raise("UNKNOWN_DOCUMENT_ROLE", None)
        expected_order = [row["hook_id"] for row in policy["full_admissibility_validation"]["hook_contracts"]]
        if expected_order != list(RUNTIME_VALIDATOR_HOOKS) or hooks != [hook for hook in expected_order if hook in hooks]:
            _raise("HOOK_ORDER_MISMATCH", None)
        for hook_id in hooks:
            _HOOK_IMPLEMENTATIONS[hook_id](
                candidate=candidate, policy=policy, schema=schema,
                authenticated=authenticated, context=context,
            )
        for reference in _iter_evidence_refs(candidate):
            _resolve_reference(authenticated, reference, "VALIDATE_EVIDENCE_REF_HASH_PATH_ID_V1")
        result = _success_result(candidate)
        Draft202012Validator(schema["$defs"]["full_admissibility_validation_result"]).validate(result)
        return result
    except ValidationFailure as error:
        return _failure_result(error.failure_code, error.hook_id, candidate)
    except Exception:
        return _failure_result("VALIDATOR_EXECUTION_FAILURE", None, candidate)



_FIXTURE_RUN_ID = "sremut-ms-m01-r01-a01-abcdef123456"
_FIXTURE_ATTEMPT_ID = "a01"
_FIXTURE_MUTANT_ID = "MS-M01"
_FIXTURE_REPETITION = 1
_FIXTURE_UTC = "2026-08-20T10:00:00.123456789Z"
_FIXTURE_BOOT = "123e4567-e89b-12d3-a456-426614174000"


def _fixture_default(name: str) -> Any:
    values: dict[str, Any] = {
        "schema_version": 1, "run_id": _FIXTURE_RUN_ID, "attempt_id": _FIXTURE_ATTEMPT_ID,
        "created_utc": _FIXTURE_UTC, "capture_timestamp": _FIXTURE_UTC, "dispatch_start_utc": _FIXTURE_UTC,
        "dispatch_finish_utc": _FIXTURE_UTC, "first_observation_utc": _FIXTURE_UTC, "last_observation_utc": _FIXTURE_UTC,
        "boot_identity": _FIXTURE_BOOT, "redaction_status": "NOT_REDACTED", "monotonic_ns": 1, "monotonic_time": 1,
        "operation_ordinal": 1, "repetition": 1, "object_count": 1, "complete_entry_count": 1,
        "entry_time_ieee754_binary64_hex": [ieee754_binary64_hex(1.0)], "entry_indexes": [0],
    }
    if name in values: return deepcopy(values[name])
    if name.endswith("_sha256") or name.endswith("_evidence_sha256"): return "a" * 64
    if name.endswith("_paths") or name.endswith("_postconditions") or name.startswith("exact_"): return []
    if name.endswith("_labels") or name in ("request_preconditions", "dependency_and_toolchain_identity", "captured_replica_baseline"): return {}
    if name.endswith("_count") or name.endswith("_time") or name in ("observation_count", "request_count", "fresh_request_count", "failure_marker_count", "raw_log_byte_length", "container_restart_count"): return 1
    return "FIXTURE"


def _seal_fixture(policy: dict[str, Any], schema: dict[str, Any], role: str, overrides: dict[str, Any], payload: bytes | None = None, projection_class: str = "KUBERNETES_OBJECT_PROJECTION_V1") -> dict[str, Any]:
    spec = policy["roles"][role]; payload_expected = spec["storage_class"] == "PAYLOAD_WITH_DESCRIPTOR"
    if payload_expected != (payload is not None): raise AssertionError("Fixture payload/storage mismatch")
    descriptor: dict[str, Any] = {}
    for name in spec["required_metadata"]:
        descriptor[name] = deepcopy(overrides[name]) if name in overrides else _fixture_default(name)
    descriptor.update({
        "document_type": "PAYLOAD_EVIDENCE_DESCRIPTOR_V1" if payload_expected else "DESCRIPTOR_EVIDENCE_DESCRIPTOR_V1",
        "role": role, "producer": spec["producer"], "source_kind": spec["source_kind"], "media_type": spec["media_type"], "storage_class": spec["storage_class"],
    })
    for name, value in overrides.items(): descriptor[name] = deepcopy(value)
    if payload is not None:
        payload_hash = sha256_bytes(payload)
        descriptor.update({"payload_sha256": payload_hash, "payload_size_bytes": len(payload), "payload_relative_path": f"objects/sha256/{payload_hash[:2]}/{payload_hash}"})
    provisional = descriptor_content_sha256(descriptor)
    if "evidence_id" in spec["required_metadata"]: descriptor["evidence_id"] = "ev-" + provisional[:32]
    descriptor_hash = descriptor_content_sha256(descriptor)
    if "evidence_id" in descriptor: descriptor["evidence_id"] = "ev-" + descriptor_hash[:32]
    descriptor_bytes = canonical_json_bytes(descriptor)
    reference: dict[str, Any] = {
        "document_type": "PAYLOAD_EVIDENCE_REF_V1" if payload_expected else "DESCRIPTOR_EVIDENCE_REF_V1", "schema_version": 1,
        "evidence_id": "ev-" + descriptor_hash[:32], "role": role, "producer": spec["producer"], "source_kind": spec["source_kind"], "media_type": spec["media_type"], "storage_class": spec["storage_class"],
        "descriptor_sha256": descriptor_hash, "descriptor_size_bytes": len(descriptor_bytes), "descriptor_relative_path": f"descriptors/sha256/{descriptor_hash[:2]}/{descriptor_hash}.json", "redaction_status": "NOT_REDACTED",
    }
    if payload is not None: reference.update({"payload_sha256": descriptor["payload_sha256"], "payload_size_bytes": len(payload), "payload_relative_path": descriptor["payload_relative_path"]})
    if role == "kubernetes_object_projection": reference["projection_class"] = projection_class
    validate_structural_schema_branch(descriptor, schema); validate_structural_schema_branch(reference, schema)
    return {"descriptor": descriptor, "descriptor_bytes": descriptor_bytes, "reference": reference, "payload_bytes": payload}


def _journal_record(sequence: int, previous: str, transition: str, descriptors: list[str], payloads: list[str]) -> dict[str, Any]:
    record = {
        "document_type": "JOURNAL_RECORD_V1", "schema_version": 1, "journal_record_type": "STATE_TRANSITION", "sequence_number": sequence,
        "previous_entry_sha256": previous, "canonical_current_entry_sha256": GENESIS_SHA256, "run_id": _FIXTURE_RUN_ID, "attempt_id": _FIXTURE_ATTEMPT_ID,
        "transition": transition, "referenced_intent_receipt_and_adjudication_sha256": [], "referenced_descriptor_sha256": descriptors, "referenced_payload_sha256": payloads,
        "utc_time": _FIXTURE_UTC, "monotonic_ns": sequence + 1, "boot_identity": _FIXTURE_BOOT,
    }
    record["canonical_current_entry_sha256"] = journal_record_sha256(record); return record


def _fixture_context(
    policy: dict[str, Any], rows: list[dict[str, Any]], state: str, *,
    expected_evaluation: str | None = None,
    expected_operation: dict[str, Any] | None = None,
    omit_ids: set[str] | None = None,
    post_marker_ids: set[str] | None = None,
    validation_mode: str = CAPTURE_TIME_VALIDATION_LEVEL,
) -> dict[str, Any]:
    omit_ids = omit_ids or set()
    post_marker_ids = post_marker_ids or set()
    retained = [row for row in rows if row["reference"]["evidence_id"] not in omit_ids]
    records: list[dict[str, Any]] = []
    publications: dict[str, Any] = {}
    previous = GENESIS_SHA256

    def append_record(transition: str, descriptors: list[str], payloads: list[str]) -> None:
        nonlocal previous
        record = _journal_record(len(records), previous, transition, descriptors, payloads)
        records.append(record)
        previous = record["canonical_current_entry_sha256"]

    def publish(row: dict[str, Any]) -> None:
        reference = row["reference"]
        append_record(
            "EVIDENCE_PUBLISHED:" + reference["evidence_id"],
            [reference["descriptor_sha256"]],
            [reference["payload_sha256"]] if row["payload_bytes"] is not None else [],
        )
        record = records[-1]
        publications[reference["evidence_id"]] = {
            "sequence_number": record["sequence_number"],
            "journal_record_sha256": record["canonical_current_entry_sha256"],
        }

    append_record("STATE_VERIFIED:" + state, [], [])
    evaluation = None
    if expected_evaluation is not None:
        frozen = EVALUATION_CONTEXTS[expected_evaluation]
        evaluation = {
            "predicate_id": expected_evaluation,
            "phase": frozen["phase"],
            "deadline_identity": frozen["deadline_identity"],
        }
        append_record("EVALUATION_AUTHORIZED:" + expected_evaluation, [], [])
    for row in retained:
        if row["reference"]["evidence_id"] not in post_marker_ids:
            publish(row)
    operation = deepcopy(expected_operation)
    if operation is not None:
        material = {key: value for key, value in operation.items() if key != "request_dispatch_sequence"}
        append_record("OPERATION_AUTHORIZED:" + canonical_json_bytes(material).hex(), [], [])
        operation["request_dispatch_sequence"] = records[-1]["sequence_number"]
    for row in retained:
        if row["reference"]["evidence_id"] in post_marker_ids:
            publish(row)
    context = {
        "document_type": RESOLVED_CONTEXT_DOCUMENT, "schema_version": 1,
        "run_id": _FIXTURE_RUN_ID, "attempt_id": _FIXTURE_ATTEMPT_ID,
        "mutant_id": _FIXTURE_MUTANT_ID, "repetition": _FIXTURE_REPETITION,
        "evidence_refs": {row["reference"]["evidence_id"]: deepcopy(row["reference"]) for row in retained},
        "exact_descriptor_bytes_hex": {row["reference"]["evidence_id"]: row["descriptor_bytes"].hex() for row in retained},
        "parsed_canonical_descriptors": {row["reference"]["evidence_id"]: deepcopy(row["descriptor"]) for row in retained},
        "exact_payload_bytes_hex": {row["reference"]["evidence_id"]: row["payload_bytes"].hex() for row in retained if row["payload_bytes"] is not None},
        "journal_publication_records": publications,
        "attempt_journal_records": records,
        "exact_authoritative_journal_bytes_hex": _canonical_journal_bytes(records).hex(),
        "validation_mode": validation_mode,
        "trusted_capture_source_bytes_hex": {
            row["reference"]["evidence_id"]: row["payload_bytes"].hex()
            for row in retained if row["payload_bytes"] is not None
        } if validation_mode == CAPTURE_TIME_VALIDATION_LEVEL else {},
        "offline_seal": None,
        "current_verified_attempt_state": {
            "state": state, "sequence_number": records[-1]["sequence_number"],
            "terminal": state in policy["verified_attempt_state_machine"]["terminal_states"],
        },
        "frozen_contract_profile_identities": {
            "contract_sha256": CONTRACT_SHA256, "execution_profile_sha256": PROFILE_SHA256,
            "contract_tag_object": CONTRACT_TAG_OBJECT, "execution_profile_tag_object": PROFILE_TAG_OBJECT,
        },
        "expected_operation_context": operation,
        "expected_evaluation_context": evaluation,
        "challenge_identity": None,
    }
    return context


def _authenticated_policy_fixture(policy: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    policy_bytes = render_policy(policy)
    schema_bytes = render_schema(schema)
    manifest_bytes = render_manifest(default_layout(), policy_bytes, schema_bytes)
    return {
        "document_type": AUTHENTICATED_POLICY_INPUT_DOCUMENT, "schema_version": 1,
        "exact_policy_bytes_hex": policy_bytes.hex(),
        "exact_schema_bytes_hex": schema_bytes.hex(),
        "exact_checksum_manifest_bytes_hex": manifest_bytes.hex(),
        "expected_checksum_manifest_sha256": sha256_bytes(manifest_bytes),
        "expected_policy_relative_path": POLICY_REL,
        "expected_schema_relative_path": SCHEMA_REL,
        "expected_generator_relative_path": GENERATOR_REL,
        "supplied_parsed_policy": deepcopy(policy),
        "supplied_parsed_schema": deepcopy(schema),
        "caller_hook_contracts": deepcopy(policy["full_admissibility_validation"]["hook_contracts"]),
        "caller_hook_applicability_matrix": deepcopy(policy["full_admissibility_validation"]["hook_applicability_matrix"]),
        "caller_hook_order": list(RUNTIME_VALIDATOR_HOOKS),
    }


def _request_identity_fixture(policy: dict[str, Any], schema: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    material = {"request": deepcopy(request), "run_id": _FIXTURE_RUN_ID, "attempt_id": _FIXTURE_ATTEMPT_ID}
    return _seal_fixture(policy, schema, "kubernetes_request_identity", {
        "request_rule_id": request["request_rule_id"], "operation": request["operation"],
        "resource": request["resource"], "subresource": request["subresource"],
        "namespace": request["namespace"], "name_rule": request["name"],
        "selector": request["label_selector"],
        "projection_paths": next(
            rule["projection_paths"]
            for rule in policy["kubernetes_evidence_surface"]["resource_rules"] + policy["kubernetes_evidence_surface"]["subresource_rules"]
            if rule["id"] == request["request_rule_id"]
        ),
        "canonical_request": deepcopy(request),
        "request_identity_sha256": sha256_bytes(canonical_json_bytes(material)),
    })


def _projection_fixture(
    policy: dict[str, Any], schema: dict[str, Any], payload_value: dict[str, Any],
    request_row: dict[str, Any], projection_class: str = "KUBERNETES_OBJECT_PROJECTION_V1",
    *, capture_state: str = "HEALTHY_STATE_CAPTURED", extra_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = canonical_json_bytes(payload_value)
    metadata = {
        "request_identity_reference": request_row["reference"],
        "object_count": len(payload_value.get("items", [])) if isinstance(payload_value.get("items"), list) else 1,
        "projection_class": projection_class,
        "projection_schema_id": projection_class,
        "capture_state": capture_state,
        "trusted_source_sha256": sha256_bytes(payload),
    }
    if isinstance(payload_value.get("list_metadata"), dict) and isinstance(payload_value["list_metadata"].get("resourceVersion"), str):
        metadata["source_list_resource_version"] = payload_value["list_metadata"]["resourceVersion"]
    if extra_metadata:
        metadata.update(deepcopy(extra_metadata))
    return _seal_fixture(policy, schema, "kubernetes_object_projection", metadata, payload, projection_class)


def _workload_entry_bytes(entries: list[dict[str, Any]]) -> bytes:
    return b"".join(canonical_json_bytes(entry) + b"\n" for entry in entries)


def _workload_bundle(
    policy: dict[str, Any], schema: dict[str, Any], fault: str | None = None,
    expected_predicate: str = "INITIAL_INVARIANT_EVALUATION",
    window_phase: str = "INITIAL_MUTANT_CHALLENGE",
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    ordinal = WORKLOAD_WINDOWS[window_phase]
    repetition = 2 if fault == "wrong_repetition" else _FIXTURE_REPETITION
    core = {
        "phase": window_phase, "ordinal": ordinal, "run_id": _FIXTURE_RUN_ID,
        "attempt_id": _FIXTURE_ATTEMPT_ID, "mutant_id": _FIXTURE_MUTANT_ID,
        "repetition": repetition,
    }
    request = valid_kubernetes_request(
        policy, "PODS_SOCIAL_READ_AND_GUARDED_MUTATION", "LIST", "", "service=user-service",
    )
    request_row = _request_identity_fixture(policy, schema, request)
    pod_value = {
        "apiVersion": "v1", "kind": "Pod",
        "metadata": {
            "name": "user-service-abc", "namespace": "social-network",
            "uid": "pod-uid", "resourceVersion": "10",
        },
        "status": {"containerStatuses": [{"name": "user-service", "restartCount": 0}]},
    }
    state = "RESTORE_STARTED" if expected_predicate == "RESTORATION_POSITIVE_CONTROL" else "CONTRACT_EVALUATED"
    pod_row = _projection_fixture(policy, schema, pod_value, request_row, capture_state=state)
    prefix_entries = [{
        "index": 0, "time_binary64_hex": ieee754_binary64_hex(1.0),
        "number": 1, "log": "prefix", "ok": True,
    }]
    entries = deepcopy(prefix_entries)
    for index in range(1, 51):
        entries.append({
            "index": index, "time_binary64_hex": ieee754_binary64_hex(float(index + 1)),
            "number": 1, "log": "fresh", "ok": False if fault == "failure_count" and index == 1 else True,
        })
    if fault == "reversed_timestamps":
        entries[-1]["time_binary64_hex"] = ieee754_binary64_hex(0.5)
    prefix_bytes = _workload_entry_bytes(prefix_entries)
    raw_bytes = _workload_entry_bytes(entries)
    prefix_row = _seal_fixture(policy, schema, "workload_log_bytes", {
        "complete_entry_count": len(prefix_entries),
        "entry_time_ieee754_binary64_hex": [entry["time_binary64_hex"] for entry in prefix_entries],
        "entry_indexes": [entry["index"] for entry in prefix_entries],
        "workload_window": core,
    }, prefix_bytes)
    boundary_row = _seal_fixture(policy, schema, "workload_boundary", {
        "workload_pod_projection_reference": pod_row["reference"],
        "pod_name": "wrong-pod" if fault == "wrong_pod_uid" else "user-service-abc",
        "pod_uid": "wrong-uid" if fault == "wrong_pod_uid" else "pod-uid",
        "container_restart_count": 1 if fault == "restart_count" else 0,
        "raw_log_reference": prefix_row["reference"],
        "raw_log_byte_length": 999 if fault == "raw_length" else len(prefix_bytes),
        "raw_log_sha256": "f" * 64 if fault == "raw_hash" else sha256_bytes(prefix_bytes),
        "complete_entry_count": len(prefix_entries), "request_count": 1,
        "entry_time_ieee754_binary64_hex": [entry["time_binary64_hex"] for entry in prefix_entries],
        "workload_window": core,
    })
    log_attempt = "a02" if fault == "wrong_attempt" else _FIXTURE_ATTEMPT_ID
    log_row = _seal_fixture(policy, schema, "workload_log_bytes", {
        "attempt_id": log_attempt, "complete_entry_count": len(entries),
        "entry_time_ieee754_binary64_hex": [entry["time_binary64_hex"] for entry in entries],
        "entry_indexes": [entry["index"] for entry in entries], "workload_window": core,
    }, raw_bytes)
    parse_raw_ref = prefix_row["reference"] if fault == "raw_reference_mismatch" else log_row["reference"]
    parse_payload = {
        "document_type": "WORKLOAD_PARSE_RESULT_V1", "schema_version": 1,
        "parser_identity": _workload_parser_identity(),
        "boundary_reference": boundary_row["reference"], "raw_log_reference": parse_raw_ref,
        "workload_window": core, "complete_entry_count": len(entries),
        "entry_indexes": [entry["index"] for entry in entries],
        "entry_time_ieee754_binary64_hex": [entry["time_binary64_hex"] for entry in entries],
        "prefix_complete_entry_count": len(prefix_entries), "prefix_byte_length": len(prefix_bytes),
        "prefix_sha256": sha256_bytes(prefix_bytes),
        "suffix_sha256": sha256_bytes(raw_bytes[len(prefix_bytes):]),
        "fresh_request_count": 50,
        "failure_marker_count": 1 if fault == "failure_count" else 0,
    }
    if fault == "arbitrary_parse_bytes":
        parse_payload["fresh_request_count"] = 500
    elif fault == "failure_marker_mismatch":
        parse_payload["failure_marker_count"] = 1
    elif fault == "changed_count":
        parse_payload["fresh_request_count"] = 49
    elif fault == "changed_timestamp":
        parse_payload["entry_time_ieee754_binary64_hex"][-1] = ieee754_binary64_hex(999.0)
    elif fault == "changed_index":
        parse_payload["entry_indexes"][-1] = 999
    elif fault == "parser_identity":
        parse_payload["parser_identity"]["source_sha256"] = "f" * 64
    parse_row = _seal_fixture(policy, schema, "workload_parse_result", {
        "boundary_reference": boundary_row["reference"], "raw_log_reference": parse_raw_ref,
        "fresh_request_count": parse_payload["fresh_request_count"],
        "failure_marker_count": parse_payload["failure_marker_count"], "workload_window": core,
    }, canonical_json_bytes(parse_payload))
    window = {
        **core, "boundary_reference": boundary_row["reference"],
        "raw_log_reference": log_row["reference"], "parse_result_reference": parse_row["reference"],
    }
    deadline = "WRONG_DEADLINE" if fault == "wrong_deadline" else EVALUATION_CONTEXTS[expected_predicate]["deadline_identity"]
    raw_refs = [log_row["reference"], parse_row["reference"], pod_row["reference"]]
    adjudication_row = _seal_fixture(policy, schema, "adjudication", {
        "adjudication_id": "adj-1",
        "predicate_oracle_or_classification_id": expected_predicate,
        "result_type": "BOOLEAN", "boolean_or_categorical_value": True,
        "reason": "frozen predicate passed", "first_observation_utc": _FIXTURE_UTC,
        "last_observation_utc": _FIXTURE_UTC, "monotonic_elapsed_time": 1,
        "observation_count": 50, "applicable_deadline": deadline,
        "evaluator_source_or_runner_bundle_sha256": "a" * 64,
        "raw_evidence_references": raw_refs,
        "raw_evidence_sha256_per_reference": [ref["payload_sha256"] for ref in raw_refs],
        "kubernetes_uid_and_resource_version_references_when_applicable": [pod_row["reference"]],
        "dependency_and_toolchain_identity": {}, "attempt_id": _FIXTURE_ATTEMPT_ID,
        "run_id": _FIXTURE_RUN_ID, "workload_window_adjudication_identity": window,
    })
    rows = [request_row, pod_row, prefix_row, boundary_row, log_row, parse_row, adjudication_row]
    if fault == "parse_after_adjudication":
        rows = [request_row, pod_row, prefix_row, boundary_row, log_row, adjudication_row, parse_row]
    omit = set()
    if fault == "unresolved_payload":
        omit.add(log_row["reference"]["evidence_id"])
    elif fault == "unresolved_parse_payload":
        omit.add(parse_row["reference"]["evidence_id"])
    context = _fixture_context(
        policy, rows, state, expected_evaluation=expected_predicate, omit_ids=omit,
    )
    return adjudication_row["descriptor"], rows, context


def _service_delete_bundle(policy: dict[str, Any], schema: dict[str, Any], fault: str | None = None) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    source_request = valid_kubernetes_request(policy, "USER_SERVICE_READ_AND_GUARDED_MUTATION", "GET", "user-service")
    source_request_row = _request_identity_fixture(policy, schema, source_request)
    source = valid_service_source()
    source_row = _projection_fixture(
        policy, schema, source, source_request_row, "SERVICE_RESTORATION_SOURCE_V1",
        capture_state="HEALTHY_STATE_CAPTURED",
    )
    restoration_doc = derive_service_restoration_body(
        policy, schema, source, source_request_row["reference"], _FIXTURE_UTC, source_row["reference"],
    )
    if fault in ("resealed_reconstructed_body", "one_field_body_change"):
        restoration_doc["normalized_service_create_body"]["metadata"]["annotations"]["research.example/change"] = "resealed"
        normalized = canonical_json_bytes(restoration_doc["normalized_service_create_body"])
        restoration_doc["normalized_body_sha256"] = sha256_bytes(normalized)
        restoration_doc["canonical_json_identity"] = sha256_bytes(normalized)
    elif fault == "body_other_service":
        foreign = deepcopy(source)
        foreign["metadata"]["uid"] = "foreign-service-uid"
        foreign["metadata"]["resourceVersion"] = "999"
        foreign_row = _projection_fixture(
            policy, schema, foreign, source_request_row, "SERVICE_RESTORATION_SOURCE_V1",
            capture_state="HEALTHY_STATE_CAPTURED",
        )
        restoration_doc["source_service_reference"] = foreign_row["reference"]
        source_row_for_rows = foreign_row
    elif fault == "missing_source_reference":
        restoration_doc.pop("source_service_reference")
    elif fault == "stripped_fields_changed":
        restoration_doc["exact_stripped_field_token_paths"] = restoration_doc["exact_stripped_field_token_paths"][:-1]
    source_row_for_rows = locals().get("source_row_for_rows", source_row)
    restoration_row = _projection_fixture(
        policy, schema, restoration_doc, source_request_row, "SERVICE_RESTORATION_BODY_V1",
        capture_state="HEALTHY_STATE_CAPTURED",
    )
    intent_overrides = {
        "operation_id": "service-delete-1", "operation_ordinal": 1,
        "operation_kind": "INITIAL_USER_SERVICE_DELETION", "namespace": "social-network",
        "resource_kind": "Service", "object_name": "user-service",
        "expected_uid_when_existing": source["metadata"]["uid"],
        "expected_resource_version_when_applicable": source["metadata"]["resourceVersion"],
        "desired_canonical_body_sha256_when_creation": None,
        "captured_prestate_evidence_path": "descriptors/prestate",
        "captured_prestate_evidence_sha256": source_row["reference"]["descriptor_sha256"],
        "exact_idempotency_and_adoption_labels": {},
        "request_preconditions": {"uid": source["metadata"]["uid"]},
        "permitted_postconditions": ["Service absent"],
        "forbidden_postconditions": ["unrelated deletion"],
        "created_utc": _FIXTURE_UTC, "monotonic_time": 10,
        "boot_identity": _FIXTURE_BOOT, "evaluator_or_runner_bundle_sha256": "a" * 64,
        "status": "SEALED",
    }
    if fault != "missing_restoration_reference":
        intent_overrides.update({
            "service_restoration_body_reference": restoration_row["reference"],
            "service_restoration_body_sha256": "f" * 64 if fault == "mismatched_body" else restoration_row["reference"]["payload_sha256"],
        })
    if fault == "wrong_uid":
        intent_overrides["expected_uid_when_existing"] = "wrong-uid"
    intent_row = _seal_fixture(policy, schema, "mutation_intent", intent_overrides)
    request = valid_kubernetes_request(policy, "USER_SERVICE_READ_AND_GUARDED_MUTATION", "DELETE", "user-service")
    request.update({
        "captured_object_name": "user-service",
        "captured_object_uid": source["metadata"]["uid"],
        "captured_object_resource_version": source["metadata"]["resourceVersion"],
        "captured_object_reference": source_row["reference"],
        "uid_precondition": "wrong-uid" if fault == "wrong_uid" else source["metadata"]["uid"],
        "intent_reference": intent_row["reference"],
        "authorized_operation_kind": "INITIAL_USER_SERVICE_DELETION",
    })
    request_row = _request_identity_fixture(policy, schema, request)
    rows = [source_request_row, source_row_for_rows, restoration_row, intent_row, request_row]
    if source_row_for_rows is not source_row:
        rows.insert(1, source_row)
    if fault == "source_after_body":
        rows.remove(source_row)
        rows.insert(rows.index(restoration_row) + 1, source_row)
    omit = set()
    if fault == "unresolved_intent":
        omit.add(intent_row["reference"]["evidence_id"])
    if fault == "source_unresolved":
        omit.add(source_row["reference"]["evidence_id"])
    state = "CONTRACT_EVALUATED" if fault == "wrong_state" else "HEALTHY_STATE_CAPTURED"
    op = {
        "operation_kind": "INITIAL_USER_SERVICE_DELETION",
        "request_rule_id": request["request_rule_id"], "resource": "services",
        "namespace": "social-network", "name": "user-service",
        "request_reference": request_row["reference"], "projection_reference": None,
        "request_body_reference": None, "request_dispatch_sequence": None,
    }
    post = {intent_row["reference"]["evidence_id"]} if fault == "intent_after_request" else set()
    context = _fixture_context(
        policy, rows, state, expected_operation=op, omit_ids=omit, post_marker_ids=post,
    )
    return request, rows, context


def _pod_delete_bundle(policy: dict[str, Any], schema: dict[str, Any], fault: str | None = None) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    list_request = valid_kubernetes_request(
        policy, "PODS_SOCIAL_READ_AND_GUARDED_MUTATION", "LIST", "", "service=user-service",
    )
    capture_request_row = _request_identity_fixture(policy, schema, list_request)
    service_row = _projection_fixture(
        policy, schema, valid_service_source(), capture_request_row,
        "SERVICE_RESTORATION_SOURCE_V1", capture_state="HEALTHY_STATE_CAPTURED",
    )
    selection_row = _seal_fixture(policy, schema, "healthy_prestate", {
        "captured_replica_baseline": {
            "selection_rule_id": "LEXICOGRAPHIC_READY_USER_SERVICE_POD_V1",
            "selected_pod_name": "user-service-abc", "selected_pod_uid": "pod-uid",
        },
        "captured_service_reference": service_row["reference"],
    }, canonical_json_bytes({"selection_rule_id": "LEXICOGRAPHIC_READY_USER_SERVICE_POD_V1", "selected_pod_name": "user-service-abc", "selected_pod_uid": "pod-uid"}))
    deployment_name = "other-deployment" if fault in ("wrong_owner", "unrelated_pod") else "user-service"
    deployment = {
        "apiVersion": "apps/v1", "kind": "Deployment",
        "metadata": {
            "name": deployment_name, "namespace": "social-network",
            "uid": "deployment-uid", "resourceVersion": "20",
        },
    }
    deployment_row = _projection_fixture(
        policy, schema, deployment, capture_request_row,
        "DEPLOYMENT_OWNER_CAPTURE_V1", capture_state="HEALTHY_STATE_CAPTURED",
    )
    replicaset = {
        "apiVersion": "apps/v1", "kind": "ReplicaSet",
        "metadata": {
            "name": "user-service-rs", "namespace": "social-network",
            "uid": "replicaset-uid", "resourceVersion": "15",
            "ownerReferences": [{
                "apiVersion": "apps/v1", "kind": "Deployment", "name": deployment_name,
                "uid": "deployment-uid", "controller": True,
            }],
        },
    }
    replicaset_row = _projection_fixture(
        policy, schema, replicaset, capture_request_row,
        "REPLICASET_OWNER_CAPTURE_V1", capture_state="HEALTHY_STATE_CAPTURED",
    )
    pod_name = "unrelated-workload-pod" if fault == "unrelated_pod" else "user-service-abc"
    owners = [] if fault == "no_controller_owner" else [{
        "apiVersion": "apps/v1", "kind": "ReplicaSet", "name": "user-service-rs",
        "uid": "replicaset-uid", "controller": True,
    }]
    pod = {
        "apiVersion": "v1", "kind": "Pod",
        "metadata": {
            "name": pod_name, "namespace": "social-network", "uid": "pod-uid",
            "resourceVersion": "10", "labels": {"service": "user-service"},
            "ownerReferences": owners,
        },
        "spec": {"nodeName": "kind-worker"},
        "status": {
            "podIP": "10.244.1.10",
            "conditions": [{"type": "Ready", "status": "True"}],
            "containerStatuses": [{"name": "user-service", "restartCount": 0}],
        },
    }
    projection_class = "SERVICE_RESTORATION_BODY_V1" if fault == "wrong_projection_class" else "POD_IDENTITY_CAPTURE_V1"
    capture_state = "CREATED" if fault == "created_state" else "HEALTHY_STATE_CAPTURED"
    pod_row = _projection_fixture(
        policy, schema, pod, capture_request_row, projection_class,
        capture_state=capture_state,
        extra_metadata={
            "replicaset_projection_reference": replicaset_row["reference"],
            "deployment_projection_reference": deployment_row["reference"],
            "selection_evidence_reference": selection_row["reference"],
        },
    )
    intent = _seal_fixture(policy, schema, "mutation_intent", {
        "operation_id": "pod-delete", "operation_ordinal": 1,
        "operation_kind": "INITIAL_CAPTURED_POD_RECYCLE_DELETE",
        "namespace": "social-network", "resource_kind": "Pod", "object_name": pod_name,
        "expected_uid_when_existing": "pod-uid",
        "expected_resource_version_when_applicable": "10",
        "desired_canonical_body_sha256_when_creation": None,
        "captured_prestate_evidence_path": "pod",
        "captured_prestate_evidence_sha256": pod_row["reference"]["descriptor_sha256"],
        "exact_idempotency_and_adoption_labels": {}, "request_preconditions": {"uid": "pod-uid"},
        "permitted_postconditions": [], "forbidden_postconditions": [],
        "created_utc": _FIXTURE_UTC, "monotonic_time": 2, "boot_identity": _FIXTURE_BOOT,
        "evaluator_or_runner_bundle_sha256": "a" * 64, "status": "SEALED",
    })
    request = valid_kubernetes_request(
        policy, "PODS_SOCIAL_READ_AND_GUARDED_MUTATION", "DELETE", pod_name,
    )
    request.update({
        "captured_object_name": pod_name,
        "captured_object_uid": "wrong-uid" if fault == "wrong_captured_uid" else "pod-uid",
        "captured_object_resource_version": "10",
        "captured_object_reference": pod_row["reference"], "uid_precondition": "pod-uid",
        "intent_reference": intent["reference"],
        "authorized_operation_kind": "INITIAL_CAPTURED_POD_RECYCLE_DELETE",
    })
    request_row = _request_identity_fixture(policy, schema, request)
    rows = [
        capture_request_row, service_row, selection_row, deployment_row,
        replicaset_row, pod_row, intent, request_row,
    ]
    if fault == "wrong_attempt":
        bad_pod_row = _seal_fixture(
            policy, schema, "kubernetes_object_projection",
            {"attempt_id": "a02", "request_identity_reference": capture_request_row["reference"],
             "object_count": 1, "projection_class": "POD_IDENTITY_CAPTURE_V1",
             "projection_schema_id": "POD_IDENTITY_CAPTURE_V1", "capture_state": "HEALTHY_STATE_CAPTURED",
             "replicaset_projection_reference": replicaset_row["reference"],
             "deployment_projection_reference": deployment_row["reference"],
             "selection_evidence_reference": selection_row["reference"],
             "trusted_source_sha256": sha256_bytes(canonical_json_bytes(pod))},
            canonical_json_bytes(pod), "POD_IDENTITY_CAPTURE_V1",
        )
        request["captured_object_reference"] = bad_pod_row["reference"]
        request_row = _request_identity_fixture(policy, schema, request)
        rows = rows[:5] + [bad_pod_row, intent, request_row]
    op = {
        "operation_kind": request["authorized_operation_kind"],
        "request_rule_id": request["request_rule_id"], "resource": "pods",
        "namespace": "social-network", "name": request["name"],
        "request_reference": request_row["reference"], "projection_reference": None,
        "request_body_reference": None, "request_dispatch_sequence": None,
    }
    post = {pod_row["reference"]["evidence_id"]} if fault == "late_capture" else set()
    context = _fixture_context(
        policy, rows, "HEALTHY_STATE_CAPTURED", expected_operation=op,
        post_marker_ids=post,
    )
    return request, rows, context


def _list_bundle(policy: dict[str, Any], schema: dict[str, Any], rule_id: str) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    rule = next(row for row in policy["kubernetes_evidence_surface"]["resource_rules"] if row["id"] == rule_id); selector = rule["label_selectors"][0]
    request = valid_kubernetes_request(policy, rule_id, "LIST", "", selector); request_row = _request_identity_fixture(policy, schema, request)
    names = {"DEPLOYMENTS_SOCIAL_READ": "user-service", "PODS_SOCIAL_READ_AND_GUARDED_MUTATION": "user-service-abc", "COREDNS_PODS_READ": "coredns-abc", "USER_SERVICE_ENDPOINT_SLICES_READ": "user-service-abc"}
    fields: dict[str, Any] = {"metadata": {"name": names[rule_id], "namespace": rule["namespace"]}}
    if selector:
        key, value = selector.split("=", 1); fields["metadata"]["labels"] = {key: value}
    response = {"document_type": "KUBERNETES_LIST_RESPONSE_V1", "schema_version": 1, "request": request, "list_metadata": {"resourceVersion": "1"}, "items": [{"apiVersion": rule["api_version"], "kind": rule["response_kind"], "namespace": rule["namespace"], "name": names[rule_id], "projection_class": "KUBERNETES_OBJECT_PROJECTION_V1", "projected_fields": fields}]}
    projection_row = _projection_fixture(policy, schema, response, request_row); rows = [request_row, projection_row]
    op = {"operation_kind": None, "request_rule_id": rule_id, "resource": rule["resource"], "namespace": rule["namespace"], "name": None, "request_reference": request_row["reference"], "projection_reference": projection_row["reference"], "request_body_reference": None, "request_dispatch_sequence": None}
    return response, rows, _fixture_context(
        policy, rows, "HEALTHY_STATE_CAPTURED", expected_operation=op,
        post_marker_ids={projection_row["reference"]["evidence_id"]},
    )



def _reseal_descriptor(policy: dict[str, Any], schema: dict[str, Any], descriptor: dict[str, Any]) -> dict[str, Any]:
    excluded = {"document_type", "role", "producer", "source_kind", "media_type", "storage_class", "payload_sha256", "payload_size_bytes", "payload_relative_path", "evidence_id"}
    return _seal_fixture(policy, schema, descriptor["role"], {key: deepcopy(value) for key, value in descriptor.items() if key not in excluded})


def _challenge_bundle(policy: dict[str, Any], schema: dict[str, Any], fault: str | None = None) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    profile = policy["challenge_request_binding"]
    labels = {"app.kubernetes.io/name": "sremut-challenge", "app.kubernetes.io/managed-by": "sremut", "sremut-run-id": _FIXTURE_RUN_ID, "sremut-mutant": _FIXTURE_MUTANT_ID, "sremut-attempt": _FIXTURE_ATTEMPT_ID}
    security = profile["security_context"]
    security_context = {"runAsNonRoot": security["run_as_non_root"], "runAsUser": security["run_as_user"], "runAsGroup": security["run_as_group"], "readOnlyRootFilesystem": security["read_only_root_filesystem"], "allowPrivilegeEscalation": security["allow_privilege_escalation"], "capabilities": {"drop": security["capabilities_drop"]}, "seccompProfile": {"type": security["seccomp_profile"]}}
    spec = {"automountServiceAccountToken": profile["automount_service_account_token"], "restartPolicy": profile["restart_policy"], "activeDeadlineSeconds": profile["active_deadline_seconds"], "terminationGracePeriodSeconds": profile["termination_grace_period_seconds"], "containers": [{"name": "challenge", "image": profile["image"], "imagePullPolicy": profile["image_pull_policy"], "command": profile["container_command"], "securityContext": security_context, "resources": deepcopy(profile["resources"])}]}
    hash_labels = deepcopy(labels)
    spec_hash = sha256_bytes(canonical_json_bytes({"metadata": {"namespace": profile["namespace"], "labels": hash_labels}, "spec": spec}))
    material = {"run_id": _FIXTURE_RUN_ID, "mutant_id": _FIXTURE_MUTANT_ID, "repetition": _FIXTURE_REPETITION, "attempt_id": _FIXTURE_ATTEMPT_ID, "frozen_spec_sha256": spec_hash}
    expected_name = "sremut-challenge-" + sha256_bytes(canonical_json_bytes(material))[:16]
    if fault == "missing_labels": labels.pop("sremut-attempt")
    body = {"apiVersion": "v1", "kind": "Pod", "metadata": {"name": expected_name, "namespace": profile["namespace"], "labels": labels}, "spec": spec}
    provisional_request = valid_kubernetes_request(policy, "PODS_SOCIAL_READ_AND_GUARDED_MUTATION", "CREATE", expected_name)
    source_request_row = _request_identity_fixture(policy, schema, provisional_request)
    body_row = _projection_fixture(policy, schema, body, source_request_row, capture_state="ORIGINAL_ORACLE_EVALUATED")
    intent = _seal_fixture(policy, schema, "mutation_intent", {"operation_id": "challenge-create", "operation_ordinal": 1, "operation_kind": "CHALLENGE_POD_CREATION", "namespace": "social-network", "resource_kind": "Pod", "object_name": expected_name, "expected_uid_when_existing": None, "expected_resource_version_when_applicable": None, "desired_canonical_body_sha256_when_creation": body_row["reference"]["payload_sha256"], "captured_prestate_evidence_path": "challenge", "captured_prestate_evidence_sha256": body_row["reference"]["descriptor_sha256"], "exact_idempotency_and_adoption_labels": labels, "request_preconditions": {}, "permitted_postconditions": [], "forbidden_postconditions": [], "created_utc": _FIXTURE_UTC, "monotonic_time": 2, "boot_identity": _FIXTURE_BOOT, "evaluator_or_runner_bundle_sha256": "a" * 64, "status": "SEALED"})
    request = provisional_request; request["intent_reference"] = intent["reference"]; request["authorized_operation_kind"] = "CHALLENGE_POD_CREATION"
    request_row = _request_identity_fixture(policy, schema, request)
    rows = [source_request_row, body_row, intent, request_row]
    op = {"operation_kind": "CHALLENGE_POD_CREATION", "request_rule_id": request["request_rule_id"], "resource": "pods", "namespace": "social-network", "name": expected_name, "request_reference": request_row["reference"], "projection_reference": None, "request_body_reference": body_row["reference"], "request_dispatch_sequence": None}
    context = _fixture_context(policy, rows, "ORIGINAL_ORACLE_EVALUATED", expected_operation=op)
    canonical_body_hash = sha256_bytes(canonical_json_bytes({"metadata": {"name": body["metadata"]["name"], "namespace": body["metadata"]["namespace"], "labels": body["metadata"]["labels"]}, "spec": body["spec"]}))
    context["challenge_identity"] = {"run_id": _FIXTURE_RUN_ID, "mutant_id": _FIXTURE_MUTANT_ID, "repetition": _FIXTURE_REPETITION, "attempt_id": _FIXTURE_ATTEMPT_ID, "frozen_spec_sha256": "f" * 64 if fault == "wrong_spec_hash" else spec_hash, "canonical_request_body_sha256": canonical_body_hash, "expected_name": expected_name, "canonical_body_reference": body_row["reference"]}
    return request, rows, context


def _receipt_bundle(policy: dict[str, Any], schema: dict[str, Any], fault: str | None = None) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    source_request = valid_kubernetes_request(policy, "USER_SERVICE_READ_AND_GUARDED_MUTATION", "GET", "user-service")
    source_request_row = _request_identity_fixture(policy, schema, source_request)
    source = valid_service_source()
    source_row = _projection_fixture(policy, schema, source, source_request_row, "SERVICE_RESTORATION_SOURCE_V1", capture_state="RESTORE_STARTED")
    body_doc = derive_service_restoration_body(policy, schema, source, source_request_row["reference"], _FIXTURE_UTC, source_row["reference"])
    body_row = _projection_fixture(policy, schema, body_doc, source_request_row, "SERVICE_RESTORATION_BODY_V1", capture_state="RESTORE_STARTED")
    intent = _seal_fixture(policy, schema, "mutation_intent", {
        "operation_id": "restore-create", "operation_ordinal": 2,
        "operation_kind": "RESTORED_SERVICE_CREATION", "namespace": "social-network",
        "resource_kind": "Service", "object_name": "user-service",
        "expected_uid_when_existing": source["metadata"]["uid"],
        "expected_resource_version_when_applicable": source["metadata"]["resourceVersion"],
        "desired_canonical_body_sha256_when_creation": body_row["reference"]["payload_sha256"],
        "captured_prestate_evidence_path": "service", "captured_prestate_evidence_sha256": source_row["reference"]["descriptor_sha256"],
        "exact_idempotency_and_adoption_labels": {}, "request_preconditions": {},
        "permitted_postconditions": [], "forbidden_postconditions": [],
        "created_utc": _FIXTURE_UTC, "monotonic_time": 2, "boot_identity": _FIXTURE_BOOT,
        "evaluator_or_runner_bundle_sha256": "a" * 64, "status": "SEALED",
        "service_restoration_body_reference": body_row["reference"],
        "service_restoration_body_sha256": body_row["reference"]["payload_sha256"],
    })
    request = valid_kubernetes_request(policy, "USER_SERVICE_READ_AND_GUARDED_MUTATION", "CREATE", "user-service")
    request.update({
        "captured_object_name": None, "captured_object_uid": None,
        "captured_object_resource_version": None, "captured_object_reference": None,
        "uid_precondition": None, "intent_reference": intent["reference"],
        "authorized_operation_kind": "RESTORED_SERVICE_CREATION",
    })
    request_row = _request_identity_fixture(policy, schema, request)
    observation = _projection_fixture(policy, schema, source, source_request_row, "SERVICE_RESTORATION_SOURCE_V1", capture_state="RESTORE_STARTED", extra_metadata={"monotonic_ns": 99})
    receipt_overrides = {
        "operation_id": "restore-create", "dispatch_start_utc": _FIXTURE_UTC,
        "dispatch_finish_utc": _FIXTURE_UTC, "api_method": "CREATE",
        "fixed_target": "Service/social-network/user-service", "http_status_or_typed_client_exception": 201,
        "returned_uid_when_present": "service-uid", "returned_resource_version_when_present": "124",
        "raw_response_evidence_path": "objects/receipt", "raw_response_evidence_sha256": observation["reference"]["payload_sha256"],
        "post_operation_get_or_list_evidence_paths": ["objects/post-create"],
        "post_operation_get_or_list_evidence_sha256": observation["reference"]["payload_sha256"],
        "observed_effect_classification": "CREATED", "effect_directly_acknowledged_or_recovered_from_observation": True,
        "status": "SEALED", "operation_request_reference": request_row["reference"],
        "service_restoration_body_reference": body_row["reference"],
        "service_restoration_body_sha256": body_row["reference"]["payload_sha256"],
        "post_create_observation_reference": observation["reference"],
    }
    if fault == "wrong_body":
        receipt_overrides["service_restoration_body_sha256"] = "f" * 64
    if fault == "wrong_request":
        receipt_overrides["operation_request_reference"] = source_request_row["reference"]
    if fault == "wrong_run":
        receipt_overrides["run_id"] = "sremut-ms-m01-r01-a01-000000000000"
    if fault == "wrong_attempt":
        receipt_overrides["attempt_id"] = "a02"
    receipt = _seal_fixture(policy, schema, "mutation_receipt", receipt_overrides)
    rows = [source_request_row, source_row, body_row, intent, request_row, observation, receipt]
    state = "FINALIZED" if fault == "post_terminal" else "RESTORE_STARTED"
    op = {
        "operation_kind": "RESTORED_SERVICE_CREATION", "request_rule_id": request["request_rule_id"],
        "resource": "services", "namespace": "social-network", "name": "user-service",
        "request_reference": request_row["reference"], "projection_reference": observation["reference"],
        "request_body_reference": body_row["reference"], "request_dispatch_sequence": None,
    }
    post = {observation["reference"]["evidence_id"], receipt["reference"]["evidence_id"]}
    if fault == "before_operation":
        post.remove(receipt["reference"]["evidence_id"])
    context = _fixture_context(policy, rows, state, expected_operation=op, post_marker_ids=post)
    candidate = deepcopy(receipt["descriptor"])
    if fault == "missing_evidence_id":
        candidate.pop("evidence_id")
    return candidate, rows, context


def _seal_offline_context(policy: dict[str, Any], schema: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    sealed = deepcopy(context)
    sealed["validation_mode"] = OFFLINE_VALIDATION_LEVEL
    sealed["trusted_capture_source_bytes_hex"] = {}
    rows = []
    for evidence_id, reference in sealed["evidence_refs"].items():
        rows.append((reference["descriptor_relative_path"], sha256_bytes(bytes.fromhex(sealed["exact_descriptor_bytes_hex"][evidence_id]))))
        if reference["storage_class"] == "PAYLOAD_WITH_DESCRIPTOR":
            rows.append((reference["payload_relative_path"], sha256_bytes(bytes.fromhex(sealed["exact_payload_bytes_hex"][evidence_id]))))
    rows.append(("journal/attempt.jsonl", sha256_bytes(bytes.fromhex(sealed["exact_authoritative_journal_bytes_hex"]))))
    manifest_bytes = "".join(f"{digest}  {path}\n" for path, digest in sorted(rows)).encode("utf-8")
    policy_bytes = render_policy(policy); schema_bytes = render_schema(schema)
    policy_manifest = render_manifest(default_layout(), policy_bytes, schema_bytes)
    manifest_hash = sha256_bytes(manifest_bytes)
    sealed["offline_seal"] = {
        "document_type": OFFLINE_SEAL_INPUT_DOCUMENT, "schema_version": 1,
        "attempt_root_identifier": "attempts/" + sealed["run_id"] + "/" + sealed["attempt_id"],
        "run_id": sealed["run_id"], "attempt_id": sealed["attempt_id"],
        "terminal_state": sealed["current_verified_attempt_state"]["state"],
        "terminal_manifest_bytes_hex": manifest_bytes.hex(),
        "externally_recorded_terminal_manifest_sha256": manifest_hash,
        "expected_manifest_relative_path": "manifests/terminal.sha256",
        "sealed_journal_sha256": sha256_bytes(bytes.fromhex(sealed["exact_authoritative_journal_bytes_hex"])),
        "runner_release_binding": {
            "evidence_policy_annotated_tag": "sremut-missing-service-evidence-policy-v1",
            "evidence_policy_checksum_manifest_sha256": sha256_bytes(policy_manifest),
            "evidence_policy_sha256": sha256_bytes(policy_bytes),
            "evidence_policy_schema_sha256": sha256_bytes(schema_bytes),
            "dispatcher_id": FULL_ADMISSIBILITY_DISPATCHER_ID,
        },
        "aggregation_anchor": {
            "recorded_by": "IMMUTABLE_RUN_INDEX_OUTSIDE_ATTEMPT_ROOT",
            "cited_by_result_aggregation": True,
            "attempt_root_identifier": "attempts/" + sealed["run_id"] + "/" + sealed["attempt_id"],
            "manifest_relative_path": "manifests/terminal.sha256",
            "terminal_manifest_sha256": manifest_hash,
        },
    }
    return sealed


def _rehash_context_journal(context: dict[str, Any]) -> None:
    previous = GENESIS_SHA256
    for sequence, record in enumerate(context["attempt_journal_records"]):
        record["sequence_number"] = sequence
        record["previous_entry_sha256"] = previous
        record["canonical_current_entry_sha256"] = journal_record_sha256(record)
        previous = record["canonical_current_entry_sha256"]
    for evidence_id, publication in context["journal_publication_records"].items():
        descriptor_hash = context["evidence_refs"][evidence_id]["descriptor_sha256"]
        matches = [record for record in context["attempt_journal_records"] if descriptor_hash in record.get("referenced_descriptor_sha256", [])]
        if len(matches) == 1:
            publication["sequence_number"] = matches[0]["sequence_number"]
            publication["journal_record_sha256"] = matches[0]["canonical_current_entry_sha256"]
    context["current_verified_attempt_state"]["sequence_number"] = len(context["attempt_journal_records"]) - 1
    context["exact_authoritative_journal_bytes_hex"] = _canonical_journal_bytes(context["attempt_journal_records"]).hex()


def _assert_dispatch(candidate: dict[str, Any], policy: dict[str, Any], schema: dict[str, Any], context: dict[str, Any], valid: bool, codes: set[str] | None = None, policy_input: dict[str, Any] | None = None) -> str | None:
    authenticated_input = deepcopy(policy_input) if policy_input is not None else _authenticated_policy_fixture(policy, schema)
    result = full_admissibility_dispatch(candidate, authenticated_input, context)
    Draft202012Validator(schema["$defs"]["full_admissibility_validation_result"]).validate(result)
    if result["valid"] is not valid: raise AssertionError("Full dispatcher validity differed: " + repr(result))
    if not valid and codes is not None and result["failure_code"] not in codes: raise AssertionError("Full dispatcher failure code differed: " + repr(result))
    return result["failure_code"]


def full_admissibility_regression_tests(policy: dict[str, Any], schema: dict[str, Any]) -> dict[str, int]:
    counts = {
        "structural_vs_full": 0, "adjudication": 0, "restoration": 0,
        "kubernetes": 0, "workload": 0, "hook_contracts": 0, "applicability": 0,
    }
    policy_input = _authenticated_policy_fixture(policy, schema)

    empty_context = _fixture_context(policy, [], "CREATED")
    structural_candidate = valid_ref(True, "workload_log_bytes")
    validate_structural_schema_branch(structural_candidate, schema)
    _assert_dispatch(structural_candidate, policy, schema, empty_context, False, {"EVIDENCE_REFERENCE_UNRESOLVED"})
    counts["structural_vs_full"] += 1

    candidate, rows, context = _workload_bundle(policy, schema)
    _assert_dispatch(candidate, policy, schema, context, True)
    counts["adjudication"] += 1; counts["workload"] += 1

    policy_cases: list[tuple[dict[str, Any], set[str]]] = []
    removed = deepcopy(policy_input)
    removed["caller_hook_applicability_matrix"][11]["hooks"].remove("VALIDATE_ADJUDICATION_RAW_BACKING_V1")
    policy_cases.append((removed, {"HOOK_MATRIX_MISMATCH"}))
    reordered = deepcopy(policy_input)
    reordered["caller_hook_order"][0], reordered["caller_hook_order"][1] = reordered["caller_hook_order"][1], reordered["caller_hook_order"][0]
    policy_cases.append((reordered, {"HOOK_ORDER_MISMATCH"}))
    added = deepcopy(policy_input)
    added["caller_hook_applicability_matrix"][11]["hooks"].append("VALIDATE_SENSITIVE_CAPTURE_V1")
    policy_cases.append((added, {"HOOK_MATRIX_MISMATCH"}))
    applicability = deepcopy(policy_input)
    applicability["caller_hook_applicability_matrix"][11]["roles"] = ["mutation_receipt"]
    policy_cases.append((applicability, {"HOOK_MATRIX_MISMATCH"}))
    failure_codes = deepcopy(policy_input)
    failure_codes["caller_hook_contracts"][0]["failure_codes"] = ["VALIDATOR_EXECUTION_FAILURE"]
    policy_cases.append((failure_codes, {"HOOK_MATRIX_MISMATCH"}))
    changed_policy_bytes = deepcopy(policy_input)
    tampered_policy = bytes.fromhex(changed_policy_bytes["exact_policy_bytes_hex"]).replace(
        b"FROZEN_BEFORE_EVIDENCE_IMPLEMENTATION", b"FROZEN_BEFORE_EVIDENCE_IMPLEMENTATIOX", 1,
    )
    changed_policy_bytes["exact_policy_bytes_hex"] = tampered_policy.hex()
    policy_cases.append((changed_policy_bytes, {"POLICY_HASH_MISMATCH"}))
    parsed_disagreement = deepcopy(policy_input)
    parsed_disagreement["supplied_parsed_policy"]["status"] = "CALLER_OVERRIDE"
    policy_cases.append((parsed_disagreement, {"POLICY_PARSED_CONTENT_MISMATCH"}))
    manifest_hash = deepcopy(policy_input)
    manifest_hash["expected_checksum_manifest_sha256"] = "f" * 64
    policy_cases.append((manifest_hash, {"POLICY_MANIFEST_HASH_MISMATCH"}))
    for bad_input, codes in policy_cases:
        _assert_dispatch(candidate, policy, schema, context, False, codes, bad_input)
        counts["structural_vs_full"] += 1

    contracts = policy["full_admissibility_validation"]["hook_contracts"]
    if len(contracts) != 12 or [row["hook_id"] for row in contracts] != list(RUNTIME_VALIDATOR_HOOKS):
        raise AssertionError("Hook contracts are incomplete")
    required_contract_keys = {
        "hook_id", "order", "version", "applicable_document_kinds", "applicable_roles",
        "required_candidate_fields", "required_policy_sections", "required_resolved_descriptors",
        "required_resolved_payload_bytes", "required_journal_state_context", "success_output",
        "failure_codes", "missing_input_behavior", "validation_scope",
        "authenticated_policy_required", "canonical_journal_bytes_required", "validation_modes",
    }
    if any(set(contract) != required_contract_keys or not contract["failure_codes"] for contract in contracts):
        raise AssertionError("A hook contract is not closed")
    counts["hook_contracts"] = 12

    covered = {
        (row["document_kind"], role)
        for row in policy["full_admissibility_validation"]["hook_applicability_matrix"]
        for role in row["roles"]
    }
    for role in ROLE_NAMES[:-1]:
        kind = "PAYLOAD_EVIDENCE_DESCRIPTOR_V1" if policy["roles"][role]["storage_class"] == "PAYLOAD_WITH_DESCRIPTOR" else "DESCRIPTOR_EVIDENCE_DESCRIPTOR_V1"
        if (kind, role) not in covered:
            raise AssertionError("Role bypasses applicability matrix: " + role)
    counts["applicability"] = len(ROLE_NAMES) - 1

    descriptor_only = deepcopy(candidate)
    descriptor_only["raw_evidence_references"] = [rows[3]["reference"]]
    descriptor_only["raw_evidence_sha256_per_reference"] = [rows[3]["reference"]["descriptor_sha256"]]
    _assert_dispatch(descriptor_only, policy, schema, context, False, {"STRUCTURAL_SCHEMA_INVALID"})
    counts["adjudication"] += 1
    base_candidate, base_rows, _ = _workload_bundle(policy, schema)
    wrong_role_row = _seal_fixture(
        policy, schema, "healthy_prestate",
        {"captured_replica_baseline": {}, "captured_service_reference": base_rows[1]["reference"]},
        b"ordinary healthy prestate",
    )
    wrong_role_descriptor = deepcopy(base_candidate)
    wrong_role_descriptor["raw_evidence_references"] = [wrong_role_row["reference"]]
    wrong_role_descriptor["raw_evidence_sha256_per_reference"] = [wrong_role_row["reference"]["payload_sha256"]]
    wrong_role_adjudication = _reseal_descriptor(policy, schema, wrong_role_descriptor)
    wrong_role_context = _fixture_context(
        policy, base_rows[:-1] + [wrong_role_row, wrong_role_adjudication],
        "CONTRACT_EVALUATED", expected_evaluation="INITIAL_INVARIANT_EVALUATION",
    )
    _assert_dispatch(wrong_role_adjudication["descriptor"], policy, schema, wrong_role_context, False, {"ADJUDICATION_RAW_ROLE_INVALID"})
    counts["adjudication"] += 1

    for fault, codes in (
        ("unresolved_payload", {"EVIDENCE_REFERENCE_UNRESOLVED"}),
        ("wrong_deadline", {"ADJUDICATION_DEADLINE_MISMATCH"}),
        ("wrong_attempt", {"RUN_ATTEMPT_MISMATCH"}),
        ("wrong_repetition", {"RUN_ATTEMPT_MISMATCH", "WORKLOAD_EVALUATION_CONTEXT_MISMATCH"}),
    ):
        bad, _bad_rows, bad_context = _workload_bundle(policy, schema, fault)
        _assert_dispatch(bad, policy, schema, bad_context, False, codes)
        counts["adjudication"] += 1; counts["workload"] += 1

    caller_phase = deepcopy(context)
    caller_phase["expected_evaluation_context"]["phase"] = "POST_REPLACEMENT_PERSISTENCE"
    _assert_dispatch(candidate, policy, schema, caller_phase, False, {"JOURNAL_CONTEXT_MISMATCH"})
    counts["adjudication"] += 1
    caller_state = deepcopy(context)
    caller_state["current_verified_attempt_state"]["state"] = "ORIGINAL_ORACLE_EVALUATED"
    _assert_dispatch(candidate, policy, schema, caller_state, False, {"JOURNAL_CONTEXT_MISMATCH"})
    counts["adjudication"] += 1
    missing_marker = deepcopy(context)
    marker = next(record for record in missing_marker["attempt_journal_records"] if record["transition"].startswith("EVALUATION_AUTHORIZED:"))
    marker["transition"] = "AUDIT_MARKER:NO_EVALUATION"
    _rehash_context_journal(missing_marker)
    _assert_dispatch(candidate, policy, schema, missing_marker, False, {"JOURNAL_EVALUATION_MARKER_MISSING"})
    counts["adjudication"] += 1
    broken_chain = deepcopy(context)
    broken_chain["attempt_journal_records"][2]["previous_entry_sha256"] = "f" * 64
    broken_chain["attempt_journal_records"][2]["canonical_current_entry_sha256"] = journal_record_sha256(broken_chain["attempt_journal_records"][2])
    broken_chain["exact_authoritative_journal_bytes_hex"] = _canonical_journal_bytes(broken_chain["attempt_journal_records"]).hex()
    _assert_dispatch(candidate, policy, schema, broken_chain, False, {"JOURNAL_CHAIN_INVALID"})
    counts["adjudication"] += 1
    contradicted, _cr, contradicted_context = _workload_bundle(
        policy, schema, expected_predicate="REPLACEMENT_PERSISTENCE_EVALUATION",
        window_phase="POST_REPLACEMENT_PERSISTENCE",
    )
    contradicted_context["attempt_journal_records"][1]["transition"] = "EVALUATION_AUTHORIZED:INITIAL_INVARIANT_EVALUATION"
    _rehash_context_journal(contradicted_context)
    _assert_dispatch(contradicted, policy, schema, contradicted_context, False, {"JOURNAL_CONTEXT_MISMATCH", "ADJUDICATION_EVALUATION_PHASE_MISMATCH"})
    counts["adjudication"] += 1

    service, service_rows, service_context = _service_delete_bundle(policy, schema)
    _assert_dispatch(service, policy, schema, service_context, True)
    counts["restoration"] += 1
    body_row = next(row for row in service_rows if row["reference"].get("projection_class") == "SERVICE_RESTORATION_BODY_V1")
    body_doc = json.loads(body_row["payload_bytes"])
    _assert_dispatch(body_doc, policy, schema, service_context, True)
    counts["restoration"] += 1
    for fault, codes in (
        ("unresolved_intent", {"RESTORATION_INTENT_UNRESOLVED"}),
        ("missing_restoration_reference", {"RESTORATION_REFERENCE_MISSING"}),
        ("mismatched_body", {"RESTORATION_BODY_HASH_MISMATCH"}),
        ("resealed_reconstructed_body", {"RESTORATION_DERIVATION_MISMATCH"}),
        ("one_field_body_change", {"RESTORATION_DERIVATION_MISMATCH"}),
        ("body_other_service", {"RESTORATION_DERIVATION_MISMATCH", "RESTORATION_SOURCE_UNRESOLVED"}),
        ("missing_source_reference", {"RESTORATION_SOURCE_REFERENCE_MISSING"}),
        ("source_unresolved", {"RESTORATION_SOURCE_UNRESOLVED"}),
        ("stripped_fields_changed", {"RESTORATION_DERIVATION_MISMATCH"}),
        ("source_after_body", {"RESTORATION_PREPUBLICATION_FAILURE"}),
        ("intent_after_request", {"RESTORATION_PREPUBLICATION_FAILURE"}),
        ("wrong_uid", {"KUBERNETES_CAPTURE_IDENTITY_MISMATCH", "RESTORATION_UID_MISMATCH"}),
        ("wrong_state", {"KUBERNETES_CAPTURE_STATE_INVALID", "STATE_OPERATION_FORBIDDEN"}),
    ):
        bad, _bad_rows, bad_context = _service_delete_bundle(policy, schema, fault)
        _assert_dispatch(bad, policy, schema, bad_context, False, codes)
        counts["restoration"] += 1

    receipt, _receipt_rows, receipt_context = _receipt_bundle(policy, schema)
    _assert_dispatch(receipt, policy, schema, receipt_context, True)
    counts["restoration"] += 1
    for fault, codes in (
        ("wrong_body", {"RESTORATION_BODY_HASH_MISMATCH"}),
        ("wrong_request", {"MUTATION_RECEIPT_REQUEST_MISMATCH"}),
        ("missing_evidence_id", {"STRUCTURAL_SCHEMA_INVALID"}),
        ("wrong_run", {"RUN_ATTEMPT_MISMATCH"}),
        ("wrong_attempt", {"RUN_ATTEMPT_MISMATCH"}),
        ("before_operation", {"MUTATION_RECEIPT_PUBLICATION_INVALID"}),
        ("post_terminal", {"MUTATION_RECEIPT_PUBLICATION_INVALID"}),
    ):
        bad, _bad_rows, bad_context = _receipt_bundle(policy, schema, fault)
        _assert_dispatch(bad, policy, schema, bad_context, False, codes)
        counts["restoration"] += 1

    pod, pod_rows, pod_context = _pod_delete_bundle(policy, schema)
    _assert_dispatch(pod, policy, schema, pod_context, True)
    counts["kubernetes"] += 1
    for fault, codes in (
        ("wrong_captured_uid", {"KUBERNETES_CAPTURE_IDENTITY_MISMATCH"}),
        ("wrong_projection_class", {"KUBERNETES_CAPTURE_CLASS_INVALID"}),
        ("created_state", {"KUBERNETES_CAPTURE_STATE_INVALID"}),
        ("late_capture", {"KUBERNETES_CAPTURE_SELECTION_INVALID"}),
        ("unrelated_pod", {"KUBERNETES_CAPTURE_OWNER_INVALID", "KUBERNETES_CAPTURE_SELECTION_INVALID"}),
        ("wrong_owner", {"KUBERNETES_CAPTURE_OWNER_INVALID"}),
        ("no_controller_owner", {"KUBERNETES_CAPTURE_OWNER_INVALID"}),
        ("wrong_attempt", {"RUN_ATTEMPT_MISMATCH"}),
    ):
        bad, _bad_rows, bad_context = _pod_delete_bundle(policy, schema, fault)
        _assert_dispatch(bad, policy, schema, bad_context, False, codes)
        counts["kubernetes"] += 1

    pod_capture = next(row for row in pod_rows if row["reference"].get("projection_class") == "POD_IDENTITY_CAPTURE_V1")
    pre_rows = pod_rows[:pod_rows.index(pod_capture) + 1]
    get_request = valid_kubernetes_request(policy, "PODS_SOCIAL_READ_AND_GUARDED_MUTATION", "GET", "user-service-abc")
    get_request.update({
        "captured_object_name": "user-service-abc", "captured_object_uid": "pod-uid",
        "captured_object_resource_version": "10", "captured_object_reference": pod_capture["reference"],
    })
    get_row = _request_identity_fixture(policy, schema, get_request)
    get_op = {
        "operation_kind": None, "request_rule_id": get_request["request_rule_id"], "resource": "pods",
        "namespace": "social-network", "name": "user-service-abc", "request_reference": get_row["reference"],
        "projection_reference": None, "request_body_reference": None, "request_dispatch_sequence": None,
    }
    get_context = _fixture_context(policy, pre_rows + [get_row], "HEALTHY_STATE_CAPTURED", expected_operation=get_op)
    _assert_dispatch(get_request, policy, schema, get_context, True)
    counts["kubernetes"] += 1
    bad_get = deepcopy(get_request); bad_get["captured_object_uid"] = "wrong-uid"
    bad_get_row = _request_identity_fixture(policy, schema, bad_get)
    bad_get_op = deepcopy(get_op); bad_get_op["request_reference"] = bad_get_row["reference"]
    bad_get_context = _fixture_context(policy, pre_rows + [bad_get_row], "HEALTHY_STATE_CAPTURED", expected_operation=bad_get_op)
    _assert_dispatch(bad_get, policy, schema, bad_get_context, False, {"KUBERNETES_CAPTURE_IDENTITY_MISMATCH"})
    counts["kubernetes"] += 1

    list_rule_ids = (
        "DEPLOYMENTS_SOCIAL_READ", "PODS_SOCIAL_READ_AND_GUARDED_MUTATION",
        "COREDNS_PODS_READ", "USER_SERVICE_ENDPOINT_SLICES_READ",
    )
    for rule_id in list_rule_ids:
        response, response_rows, response_context = _list_bundle(policy, schema, rule_id)
        _assert_dispatch(response, policy, schema, response_context, True)
        counts["kubernetes"] += 1
        bad = deepcopy(response); bad["items"][0]["projected_fields"]["unapproved"] = True
        bad_projection = _projection_fixture(policy, schema, bad, response_rows[0])
        bad_op = deepcopy(response_context["expected_operation_context"])
        bad_op["projection_reference"] = bad_projection["reference"]
        bad_op["request_dispatch_sequence"] = None
        bad_context = _fixture_context(
            policy, [response_rows[0], bad_projection], "HEALTHY_STATE_CAPTURED",
            expected_operation=bad_op, post_marker_ids={bad_projection["reference"]["evidence_id"]},
        )
        _assert_dispatch(bad, policy, schema, bad_context, False, {"KUBERNETES_PROJECTION_FIELD_FORBIDDEN", "KUBERNETES_LIST_INVALID"})
        counts["kubernetes"] += 1

    response, response_rows, response_context = _list_bundle(policy, schema, "DEPLOYMENTS_SOCIAL_READ")
    selector_changed = deepcopy(response); selector_changed["request"]["label_selector"] = "service=other"
    _assert_dispatch(selector_changed, policy, schema, response_context, False, {"KUBERNETES_REQUEST_IDENTITY_MISMATCH"})
    counts["kubernetes"] += 1
    options_changed = deepcopy(response); options_changed["request"]["request_options"]["limit"] = 1
    _assert_dispatch(options_changed, policy, schema, response_context, False, {"KUBERNETES_REQUEST_IDENTITY_MISMATCH", "STRUCTURAL_SCHEMA_INVALID"})
    counts["kubernetes"] += 1
    wrong_rv_context = deepcopy(response_context)
    projection_id = response_rows[1]["reference"]["evidence_id"]
    source_response = deepcopy(response); source_response["list_metadata"]["resourceVersion"] = "999"
    wrong_rv_context["trusted_capture_source_bytes_hex"][projection_id] = canonical_json_bytes(source_response).hex()
    _assert_dispatch(response, policy, schema, wrong_rv_context, False, {"KUBERNETES_LIST_RESOURCE_VERSION_MISMATCH"})
    counts["kubernetes"] += 1
    wrong_attempt_projection = _seal_fixture(
        policy, schema, "kubernetes_object_projection",
        {"attempt_id": "a02", "request_identity_reference": response_rows[0]["reference"],
         "object_count": 1, "projection_class": "KUBERNETES_OBJECT_PROJECTION_V1",
         "projection_schema_id": "KUBERNETES_OBJECT_PROJECTION_V1", "capture_state": "HEALTHY_STATE_CAPTURED",
         "trusted_source_sha256": sha256_bytes(canonical_json_bytes(response)),
         "source_list_resource_version": "1"},
        canonical_json_bytes(response),
    )
    wrong_attempt_op = deepcopy(response_context["expected_operation_context"])
    wrong_attempt_op["projection_reference"] = wrong_attempt_projection["reference"]
    wrong_attempt_op["request_dispatch_sequence"] = None
    wrong_attempt_context = _fixture_context(
        policy, [response_rows[0], wrong_attempt_projection], "HEALTHY_STATE_CAPTURED",
        expected_operation=wrong_attempt_op, post_marker_ids={wrong_attempt_projection["reference"]["evidence_id"]},
    )
    _assert_dispatch(response, policy, schema, wrong_attempt_context, False, {"RUN_ATTEMPT_MISMATCH"})
    counts["kubernetes"] += 1

    challenge, _challenge_rows, challenge_context = _challenge_bundle(policy, schema)
    _assert_dispatch(challenge, policy, schema, challenge_context, True)
    counts["kubernetes"] += 1
    for fault in ("missing_labels", "wrong_spec_hash"):
        bad, _bad_rows, bad_context = _challenge_bundle(policy, schema, fault)
        _assert_dispatch(bad, policy, schema, bad_context, False, {"KUBERNETES_CHALLENGE_BODY_INVALID", "KUBERNETES_CHALLENGE_IDENTITY_MISMATCH"})
        counts["kubernetes"] += 1

    offline_context = _fixture_context(
        policy, response_rows, "FINALIZED",
        expected_operation={**response_context["expected_operation_context"], "request_dispatch_sequence": None},
        post_marker_ids={response_rows[1]["reference"]["evidence_id"]},
        validation_mode=OFFLINE_VALIDATION_LEVEL,
    )
    offline_context = _seal_offline_context(policy, schema, offline_context)
    _assert_dispatch(response, policy, schema, offline_context, True)
    counts["kubernetes"] += 1
    changed_response = deepcopy(response); changed_response["list_metadata"]["resourceVersion"] = "2"
    changed_projection = _projection_fixture(policy, schema, changed_response, response_rows[0])
    changed_op = deepcopy(response_context["expected_operation_context"])
    changed_op["projection_reference"] = changed_projection["reference"]; changed_op["request_dispatch_sequence"] = None
    changed_context = _fixture_context(
        policy, [response_rows[0], changed_projection], "FINALIZED",
        expected_operation=changed_op, post_marker_ids={changed_projection["reference"]["evidence_id"]},
        validation_mode=OFFLINE_VALIDATION_LEVEL,
    )
    changed_context["offline_seal"] = deepcopy(offline_context["offline_seal"])
    _assert_dispatch(changed_response, policy, schema, changed_context, False, {"EXTERNAL_MANIFEST_COVERAGE_MISMATCH", "EXTERNAL_MANIFEST_HASH_MISMATCH"})
    counts["kubernetes"] += 1
    replaced_manifest = deepcopy(offline_context)
    seal_bytes = bytes.fromhex(replaced_manifest["offline_seal"]["terminal_manifest_bytes_hex"]) + b"0" * 64 + b"  extra\n"
    replaced_manifest["offline_seal"]["terminal_manifest_bytes_hex"] = seal_bytes.hex()
    _assert_dispatch(response, policy, schema, replaced_manifest, False, {"EXTERNAL_MANIFEST_HASH_MISMATCH"})
    counts["kubernetes"] += 1

    for fault, codes in (
        ("reversed_timestamps", {"WORKLOAD_TIMESTAMP_ORDER_INVALID"}),
        ("raw_hash", {"PAYLOAD_HASH_MISMATCH"}), ("raw_length", {"PAYLOAD_SIZE_MISMATCH"}),
        ("wrong_pod_uid", {"WORKLOAD_POD_IDENTITY_MISMATCH"}),
        ("restart_count", {"WORKLOAD_RESTART_COUNT_MISMATCH"}),
        ("wrong_deadline", {"ADJUDICATION_DEADLINE_MISMATCH"}),
        ("raw_reference_mismatch", {"WORKLOAD_PARSE_REFERENCE_MISMATCH"}),
        ("arbitrary_parse_bytes", {"WORKLOAD_PARSE_RECOMPUTATION_MISMATCH"}),
        ("changed_count", {"WORKLOAD_PARSE_RECOMPUTATION_MISMATCH"}),
        ("failure_marker_mismatch", {"WORKLOAD_PARSE_RECOMPUTATION_MISMATCH"}),
        ("changed_timestamp", {"WORKLOAD_PARSE_RECOMPUTATION_MISMATCH"}),
        ("changed_index", {"WORKLOAD_PARSE_RECOMPUTATION_MISMATCH"}),
        ("parse_after_adjudication", {"PUBLICATION_ORDER_INVALID"}),
        ("unresolved_parse_payload", {"EVIDENCE_REFERENCE_UNRESOLVED"}),
        ("parser_identity", {"WORKLOAD_PARSE_SCHEMA_INVALID", "WORKLOAD_PARSER_IDENTITY_MISMATCH"}),
    ):
        bad, _bad_rows, bad_context = _workload_bundle(policy, schema, fault)
        _assert_dispatch(bad, policy, schema, bad_context, False, codes)
        counts["workload"] += 1
    for expected, phase in (
        ("REPLACEMENT_PERSISTENCE_EVALUATION", "INITIAL_MUTANT_CHALLENGE"),
        ("RESTORATION_POSITIVE_CONTROL", "POST_REPLACEMENT_PERSISTENCE"),
        ("INITIAL_INVARIANT_EVALUATION", "RESTORATION_POSITIVE_CONTROL"),
    ):
        bad, _bad_rows, bad_context = _workload_bundle(
            policy, schema, expected_predicate=expected, window_phase=phase,
        )
        _assert_dispatch(bad, policy, schema, bad_context, False, {"WORKLOAD_EVALUATION_CONTEXT_MISMATCH"})
        counts["workload"] += 1
    return counts


def full_admissibility_subprocess_tests() -> dict[str, int]:
    completed = run_local([sys.executable, str(GENERATOR_PATH), "--full-admissibility-self-test"], check=True, timeout_seconds=60)
    try:
        result = json.loads(completed.stdout.strip())
    except Exception as error:
        raise RuntimeError("Full-admissibility subprocess returned invalid output") from error
    expected = {"structural_vs_full", "adjudication", "restoration", "kubernetes", "workload", "hook_contracts", "applicability"}
    if set(result) != expected or any(not isinstance(value, int) or value < 1 for value in result.values()):
        raise RuntimeError("Full-admissibility subprocess returned invalid counts")
    return result


def core_self_test_counts(policy: dict[str, Any], schema: dict[str, Any], profile: dict[str, Any]) -> dict[str, int]:
    validate_policy_semantics(policy, profile)
    positive_schema, negative_schema = run_schema_tests(policy, schema, profile)
    runtime_positive, runtime_negatives = runtime_negative_tests(policy, schema)
    sensitive_positive, sensitive_negative = sensitive_tests()
    crash_passed, unsafe_preserved, symlink_refused = crash_recovery_tests(policy, schema)
    descendant = descendant_provenance_tests(policy, schema)
    subprocess_failures = subprocess_failure_tests()
    validate_capture_payload(policy, "workload_log_bytes", b"valid utf-8 workload")
    expect_failure(validate_capture_payload, policy, "workload_log_bytes", b"\xff")
    expect_failure(validate_capture_payload, policy, "healthy_prestate", b"")
    return {
        "schema_positive": positive_schema, "schema_negative": negative_schema,
        "runtime_positive": runtime_positive, "runtime_negative": runtime_negatives,
        "sensitive_positive": sensitive_positive, "sensitive_false_positive": sensitive_negative,
        "crash_recovery_phases": crash_passed, "unsafe_recovery_preserved": unsafe_preserved,
        "symlink_target_refused": symlink_refused, "descendant_accepted": descendant["accepted"],
        "descendant_rejected": descendant["rejected"], "subprocess_failure": subprocess_failures,
        "capture_admission": 3,
    }


def core_subprocess_tests() -> dict[str, int]:
    completed = run_local([sys.executable, str(GENERATOR_PATH), "--core-self-test"], check=True, timeout_seconds=90)
    try: result = json.loads(completed.stdout.strip())
    except Exception as error: raise RuntimeError("Core self-test subprocess returned invalid output") from error
    expected = {"schema_positive", "schema_negative", "runtime_positive", "runtime_negative", "sensitive_positive", "sensitive_false_positive", "crash_recovery_phases", "unsafe_recovery_preserved", "symlink_target_refused", "descendant_accepted", "descendant_rejected", "subprocess_failure", "capture_admission"}
    if set(result) != expected or any(not isinstance(value, int) or value < 1 for value in result.values()): raise RuntimeError("Core self-test subprocess returned invalid counts")
    return result


def run_self_tests(policy: dict[str, Any], schema: dict[str, Any], profile: dict[str, Any]) -> dict[str, int]:
    del policy, schema, profile
    full = full_admissibility_subprocess_tests()
    core = core_subprocess_tests()
    result = {**core, **{"full_" + key: value for key, value in full.items()}}
    result["total"] = sum(core.values()) + sum(full.values())
    return result


def initial_generation(layout: Layout) -> None:
    if any(path_lexists(path) for path in (layout.policy, layout.schema, layout.manifest)):
        raise FileExistsError("Policy/schema/manifest already exists; use --check or --regenerate-policy")
    contract, profile = verify_provenance()
    _policy, _schema, artifacts = expected_artifacts(layout, INITIAL_FROZEN_AT, contract, profile)
    install_transaction(layout, artifacts, "INITIAL_GENERATION")
    verify_exact_installed(layout, contract, profile)
    print(f"Generated frozen evidence policy: {layout.policy}")


def check_generation(layout: Layout) -> tuple[dict[str, Any], dict[str, Any]]:
    contract, profile = verify_provenance()
    policy, schema = verify_exact_installed(layout, contract, profile)
    print(f"OK: {layout.policy}")
    print(f"Policy SHA256: {sha256(layout.policy)}")
    print(f"Schema SHA256: {sha256(layout.schema)}")
    print(f"Manifest SHA256: {sha256(layout.manifest)}")
    return policy, schema


def regenerate(layout: Layout) -> None:
    if safe_stat(layout, layout.journal) is not None:
        raise RuntimeError("Unresolved evidence-policy transaction; explicit recovery required")
    contract, profile = verify_provenance()
    existing = parse_installed_policy(layout)
    frozen_at = existing.get("frozen_at")
    if not isinstance(frozen_at, str):
        raise RuntimeError("Existing frozen_at is missing")
    _policy, _schema, artifacts = expected_artifacts(layout, frozen_at, contract, profile)
    if all(safe_stat(layout, path) is not None and safe_read_transaction_file(layout, path) == data for path, data in artifacts.items()):
        verify_exact_installed(layout, contract, profile)
        print("Policy/schema/manifest already canonical")
        return
    install_transaction(layout, artifacts, "REGENERATION")
    verify_exact_installed(layout, contract, profile)
    print("Regenerated exact canonical policy/schema/manifest set")


def recover(layout: Layout) -> None:
    journal = parse_journal(layout)
    allowed = {JOURNAL_REL}
    for row in journal["artifacts"]:
        allowed.add(row["temporary_path"])
    contract, profile = verify_provenance(frozenset(allowed))
    frozen_at: str | None = None
    candidates = [layout.policy]
    for row in journal["artifacts"]:
        if row["path"] == POLICY_REL:
            candidates.append(layout.root / row["temporary_path"])
    for candidate in candidates:
        info = safe_stat(layout, candidate)
        if info is not None and stat.S_ISREG(info.st_mode):
            try:
                parsed = yaml.safe_load(safe_read_transaction_file(layout, candidate))
            except Exception:
                continue
            if isinstance(parsed, dict) and isinstance(parsed.get("frozen_at"), str):
                frozen_at = parsed["frozen_at"]
                break
    if frozen_at is None:
        raise RecoveryBlocked("Cannot establish frozen_at from forensic transaction state")
    _policy, _schema, artifacts = expected_artifacts(layout, frozen_at, contract, profile)
    print(recover_transaction(layout, artifacts))


def _print_self_test_counts(counts: dict[str, int]) -> None:
    print("SELF_TESTS_PASS=" + str(counts["total"]))
    print("SCHEMA_POSITIVE_PASS=" + str(counts["schema_positive"]))
    print("SCHEMA_NEGATIVE_REJECTED=" + str(counts["schema_negative"]))
    print("RUNTIME_POSITIVE_PASS=" + str(counts["runtime_positive"]))
    print("RUNTIME_NEGATIVE_REJECTED=" + str(counts["runtime_negative"]))
    print("SENSITIVE_POSITIVE_PASS=" + str(counts["sensitive_positive"]))
    print("SENSITIVE_FALSE_POSITIVE_PASS=" + str(counts["sensitive_false_positive"]))
    print(f"CRASH_RECOVERY_MATRIX={counts['crash_recovery_phases']}/{len(PHASES)}")
    print("UNSAFE_RECOVERY_FORENSIC_PRESERVATION=PASS")
    print("SYMLINK_TARGET_REFUSAL=PASS")
    print(f"DESCENDANT_MATRIX_ACCEPTED={counts['descendant_accepted']}")
    print(f"DESCENDANT_MATRIX_REJECTED={counts['descendant_rejected']}")
    print(f"SUBPROCESS_FAILURE_MATRIX={counts['subprocess_failure']}/4")
    print(f"CAPTURE_ADMISSION_MATRIX={counts['capture_admission']}/3")
    print(f"FULL_STRUCTURAL_VS_ADMISSIBILITY={counts['full_structural_vs_full']}")
    print(f"FULL_ADJUDICATION_MATRIX={counts['full_adjudication']}")
    print(f"FULL_RESTORATION_MATRIX={counts['full_restoration']}")
    print(f"FULL_KUBERNETES_MATRIX={counts['full_kubernetes']}")
    print(f"FULL_WORKLOAD_MATRIX={counts['full_workload']}")
    print(f"FULL_HOOK_CONTRACTS={counts['full_hook_contracts']}/12")
    print(f"FULL_ROLE_APPLICABILITY={counts['full_applicability']}/{len(ROLE_NAMES)-1}")


def self_test_mode(layout: Layout) -> None:
    del layout
    sys.stdout.flush()
    os.execv(sys.executable, [sys.executable, str(GENERATOR_PATH), "--self-test-full-phase"])


def self_test_full_phase_mode() -> None:
    contract, profile = verify_provenance(EXPECTED_UNTRACKED)
    policy = build_policy(INITIAL_FROZEN_AT, contract, profile); schema = build_schema(policy)
    full = full_admissibility_regression_tests(policy, schema)
    sys.stdout.flush()
    os.execv(sys.executable, [sys.executable, str(GENERATOR_PATH), "--self-test-core-phase", json.dumps(full, sort_keys=True, separators=(",", ":"))])


def self_test_core_phase_mode(full_json: str) -> None:
    try: full = json.loads(full_json)
    except Exception as error: raise RuntimeError("Invalid full-phase handoff") from error
    expected = {"structural_vs_full", "adjudication", "restoration", "kubernetes", "workload", "hook_contracts", "applicability"}
    if set(full) != expected or any(not isinstance(value, int) or value < 1 for value in full.values()): raise RuntimeError("Invalid full-phase counts")
    contract, profile = verify_provenance(EXPECTED_UNTRACKED)
    policy = build_policy(INITIAL_FROZEN_AT, contract, profile); schema = build_schema(policy)
    core = core_self_test_counts(policy, schema, profile)
    counts = {**core, **{"full_" + key: value for key, value in full.items()}}
    counts["total"] = sum(core.values()) + sum(full.values())
    _print_self_test_counts(counts)


def core_self_test_mode() -> None:
    contract, profile = verify_provenance(EXPECTED_UNTRACKED)
    policy = build_policy(INITIAL_FROZEN_AT, contract, profile)
    schema = build_schema(policy)
    print(json.dumps(core_self_test_counts(policy, schema, profile), sort_keys=True, separators=(",", ":")))


def full_admissibility_self_test_mode() -> None:
    contract, profile = verify_provenance(EXPECTED_UNTRACKED)
    policy = build_policy(INITIAL_FROZEN_AT, contract, profile)
    schema = build_schema(policy)
    print(json.dumps(full_admissibility_regression_tests(policy, schema), sort_keys=True, separators=(",", ":")))


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--regenerate-policy", action="store_true")
    mode.add_argument("--self-test", action="store_true")
    mode.add_argument("--recover-interrupted-generation", action="store_true")
    mode.add_argument("--full-admissibility-self-test", action="store_true", help=argparse.SUPPRESS)
    mode.add_argument("--core-self-test", action="store_true", help=argparse.SUPPRESS)
    mode.add_argument("--self-test-full-phase", action="store_true", help=argparse.SUPPRESS)
    mode.add_argument("--self-test-core-phase", help=argparse.SUPPRESS)
    arguments = parser.parse_args()
    layout = default_layout()
    with repository_lock(layout):
        if arguments.self_test_core_phase is not None:
            self_test_core_phase_mode(arguments.self_test_core_phase)
        elif arguments.self_test_full_phase:
            self_test_full_phase_mode()
        elif arguments.full_admissibility_self_test:
            full_admissibility_self_test_mode()
        elif arguments.core_self_test:
            core_self_test_mode()
        elif arguments.recover_interrupted_generation:
            recover(layout)
        elif arguments.check:
            check_generation(layout)
        elif arguments.regenerate_policy:
            regenerate(layout)
        elif arguments.self_test:
            self_test_mode(layout)
        else:
            initial_generation(layout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

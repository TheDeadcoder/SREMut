#!/usr/bin/env python3
"""Generate and verify the frozen missing-service pilot execution profile."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import platform
import re
import secrets
import stat
import subprocess
import sys
from contextlib import contextmanager
from copy import deepcopy
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path, PurePosixPath

import yaml
from jsonschema import Draft202012Validator


GENERATOR_PATH = Path(__file__).resolve()
ROOT = GENERATOR_PATH.parents[1]
PROFILE_PATH = (
    ROOT / "profiles" / "missing_service_social_network" / "pilot-v1.yaml"
)
SCHEMA_PATH = ROOT / "schemas" / "missing-service-execution-profile-v1.schema.json"
MANIFEST_PATH = ROOT / "EXECUTION_PROFILE_V1_SHA256SUMS"
JOURNAL_PATH = ROOT / ".execution-profile-v1.transaction.json"
CONTRACT_PATH = ROOT / "contracts" / "missing_service_social_network.yaml"
REGISTRY_PATH = (
    ROOT / "mutants" / "missing_service_social_network" / "registry.yaml"
)

SREGYM_ROOT = Path("/home/sakibbuet2k19/sremut/SREGym")
APPLICATIONS_ROOT = SREGYM_ROOT / "SREGym-applications"
SREGYM_PYTHON = SREGYM_ROOT / ".venv" / "bin" / "python"
MITIGATION_ORACLE_SOURCE = (
    SREGYM_ROOT / "sregym" / "conductor" / "oracles" / "mitigation.py"
)
KUBECTL_PATH = Path(
    "/home/sakibbuet2k19/.local/lib/sremut/kubectl/v1.32.0/kubectl"
)

TAG_NAME = "sremut-missing-service-contract-v1"
TAG_OBJECT = "378e9e9180438910611e7642402220e76bb302ca"
CONTRACT_COMMIT = "abed58d67e3f91e61f3ad666a47f0101cc680b93"
CONTRACT_TREE = "2648659b56a7d472bcedb38b8ffb6bc085aea650"
CONTRACT_SHA256 = "bda78e1b07b5eb0628954bcafd8fae3fc1bb2f3b22770046584bd873b72a2488"
REGISTRY_SHA256 = "688411986f75cb83c25ac6913e2868a075eb90fd755a1c0acd553a466ed87c72"
SREGYM_COMMIT = "ba07faf1a322f9b6d4a279643bb796aa2f36f64b"
APPLICATIONS_COMMIT = "2b2f9c6c2e97c44abbfcc44af1cf2f994bbb04f8"
EXPECTED_APPLICATIONS_RECURSIVE_SUBMODULES = {
    "FleetCast": "a2b9c5e5a14cc7892c347e0dd944a4e2ab09a8fb",
    "astronomy-shop": "7d7b074714345a0c282b0be75af7a2c504b44c95",
    "flight-ticket": "77fe227f7df911ccdbe2f2b514e5090b51a8a169",
    "train-ticket": "c9537c1533514bb6ba9bd664b9312c2b9cee413c",
}
EXPECTED_SREGYM_RECURSIVE_SUBMODULES = {
    "SREGym-applications": APPLICATIONS_COMMIT,
    **{
        f"SREGym-applications/{path}": commit
        for path, commit in EXPECTED_APPLICATIONS_RECURSIVE_SUBMODULES.items()
    },
}
KUBECTL_SHA256 = "646d58f6d98ee670a71d9cdffbf6625aeea2849d567f214bc43a35f8ccb7bf70"
CHALLENGE_INDEX_SHA256 = "73aaf090f3d85aa34ee199857f03fa3a95c8ede2ffd4cc2cdb5b94e566b11662"
CHALLENGE_AMD64_SHA256 = "b7f3d86d6e84fc17718c48bcde1450807faa2d56704205c697b4bd5df7b9e29f"
MITIGATION_ORACLE_SOURCE_SHA256 = (
    "a087fd38399cfca4c2de764dbbab837d6100350ab9f71b89552cafe2c91fca8b"
)
LEGACY_SCHEMA_SHA256 = "f4f1b648f9cffaa251aef4d7ea72ee8b9469dca19f84900b6f25fca8ea65cc85"
PREVIOUS_PROFILE_SHA256 = "2a986f307701452018246336f7f66c790061113418a5c9014a2fd85ba5d067d1"
PREVIOUS_SCHEMA_SHA256 = "82a32fe9fed216ce95bdc5a9929d184bdb355ea01ac509108c5c9e63aa76e0e9"
FROZEN_AT = "2026-08-16T19:02:00.748953+00:00"
ABSENT = "ABSENT"
IMMUTABLE_CONTRACT_PATHS = (
    "CONTRACT_FREEZE_SHA256SUMS",
    "README.md",
    "tools/freeze_missing_service_contract.py",
    "contracts/missing_service_social_network.yaml",
    "mutants/missing_service_social_network/registry.yaml",
)

GENERATOR_RELATIVE_PATH = GENERATOR_PATH.relative_to(ROOT).as_posix()
PROFILE_RELATIVE_PATH = PROFILE_PATH.relative_to(ROOT).as_posix()
SCHEMA_RELATIVE_PATH = SCHEMA_PATH.relative_to(ROOT).as_posix()
MANIFEST_RELATIVE_PATH = MANIFEST_PATH.relative_to(ROOT).as_posix()
JOURNAL_RELATIVE_PATH = JOURNAL_PATH.relative_to(ROOT).as_posix()
MANIFEST_PATHS = (
    GENERATOR_RELATIVE_PATH,
    PROFILE_RELATIVE_PATH,
    SCHEMA_RELATIVE_PATH,
)
EXPECTED_EXECUTION_PROFILE_ARTIFACTS = frozenset(
    {
        GENERATOR_RELATIVE_PATH,
        PROFILE_RELATIVE_PATH,
        SCHEMA_RELATIVE_PATH,
        MANIFEST_RELATIVE_PATH,
    }
)
SELF_VALIDATING_TRACKED_PATHS = frozenset(
    {GENERATOR_RELATIVE_PATH, MANIFEST_RELATIVE_PATH}
)
JOURNAL_SCHEMA_VERSION = 1
JOURNAL_PHASES = (
    "PREPARED",
    "PROFILE_INSTALLED",
    "MANIFEST_INSTALLED",
    "VERIFIED",
)
ALLOWED_RECOVERY_ACTIONS = (
    "REMOVE_PREINSTALLATION_ARTIFACTS",
    "INSTALL_RECORDED_MANIFEST",
    "VERIFY_AND_FINALIZE_NEW_PAIR",
    "COMPLETE_RECORDED_INITIAL_CREATION",
    "BLOCK_ON_ANY_OTHER_STATE",
)
FAILURE_INJECTION_POINTS = (
    "TEMP_PROFILE_FSYNC",
    "TEMP_MANIFEST_FSYNC",
    "PREPARED",
    "PROFILE_INSTALL",
    "PROFILE_INSTALLED",
    "MANIFEST_INSTALL",
    "MANIFEST_INSTALLED",
    "FINAL_VERIFICATION",
)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SHA1_PATTERN = re.compile(r"^[0-9a-f]{40}$")
TRANSACTION_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
SAFE_TEMP_TOKEN_PATTERN = re.compile(r"^[0-9a-f]{16}$")
UTC_TIMESTAMP_PATTERN = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}\+00:00$"
)

EXPECTED_ORIGINAL_ORACLE_ENVIRONMENT = {
    "python_executable": str(SREGYM_PYTHON),
    "python_implementation": "CPython",
    "python_version": "3.12.3",
    "pyyaml_version": "6.0.2",
    "kubernetes_client_version": "30.1.0",
    "jsonschema_version": "4.23.0",
    "mitigation_oracle_source_sha256": MITIGATION_ORACLE_SOURCE_SHA256,
}

LEGACY_IMPLEMENTATION_ENVIRONMENT = {
    "python": {
        "implementation": "CPython",
        "version": "3.12.3",
        "executable_at_freeze": str(SREGYM_PYTHON),
    },
    "dependencies": {
        "PyYAML": "6.0.2",
        "kubernetes": "30.1.0",
        "jsonschema": "4.23.0",
    },
    "uv_lock": {
        "required_before_runner_execution": True,
        "repository_path": "uv.lock",
        "present_at_profile_freeze": False,
        "implicit_dependency_resolution_allowed": False,
        "frozen_execution_required": True,
    },
    "runner_bundle": {
        "required_before_runner_execution": True,
        "algorithm": "sha256",
        "canonical_input": "sorted_relative_path_two_spaces_sha256_newline_manifest",
    },
}

MUTATION_OPERATION_PROTOCOL = {
    "schema_version": 1,
    "scope": {
        "applies_to_every_kubernetes_mutation": True,
        "allowed_operation_kinds": [
            "INITIAL_USER_SERVICE_DELETION",
            "INITIAL_CAPTURED_POD_RECYCLE_DELETE",
            "MUTANT_SERVICE_CREATION",
            "CHALLENGE_POD_CREATION",
            "REPLACEMENT_POD_DELETION",
            "CHALLENGE_POD_DELETION",
            "MUTANT_SERVICE_DELETION",
            "RESTORED_SERVICE_CREATION",
            "RECOVERY_POD_RECYCLE_DELETE",
        ],
        "unlisted_future_mutation_forbidden": True,
    },
    "intent": {
        "immutable": True,
        "status": "INTENT_DURABLE",
        "required_fields": [
            "operation_id",
            "run_id",
            "attempt_id",
            "operation_ordinal",
            "operation_kind",
            "namespace",
            "resource_kind",
            "object_name",
            "expected_uid_when_existing",
            "expected_resource_version_when_applicable",
            "desired_canonical_body_sha256_when_creation",
            "captured_prestate_evidence_path",
            "captured_prestate_evidence_sha256",
            "exact_idempotency_and_adoption_labels",
            "request_preconditions",
            "permitted_postconditions",
            "forbidden_postconditions",
            "created_utc",
            "monotonic_time",
            "boot_identity",
            "evaluator_or_runner_bundle_sha256",
            "status",
        ],
        "durability_sequence_before_dispatch": [
            "write unique immutable intent file",
            "fsync intent file",
            "rename without overwrite",
            "fsync intent parent directory",
        ],
        "request_dispatch_before_intent_durable_forbidden": True,
    },
    "receipt": {
        "immutable": True,
        "status": "RECEIPT_DURABLE",
        "required_fields": [
            "operation_id",
            "dispatch_start_utc",
            "dispatch_finish_utc",
            "api_method",
            "fixed_target",
            "http_status_or_typed_client_exception",
            "returned_uid_when_present",
            "returned_resource_version_when_present",
            "raw_response_evidence_path",
            "raw_response_evidence_sha256",
            "post_operation_get_or_list_evidence_paths",
            "post_operation_get_or_list_evidence_sha256",
            "observed_effect_classification",
            "effect_directly_acknowledged_or_recovered_from_observation",
            "status",
        ],
        "post_operation_observation_required": True,
        "durability_sequence_before_state_advance": [
            "write unique immutable receipt file",
            "fsync receipt file",
            "rename without overwrite",
            "fsync receipt parent directory",
        ],
        "state_advance_before_receipt_durable_forbidden": True,
        "api_return_without_durable_post_observation_cannot_advance": True,
    },
    "observed_effect_classifications": [
        "NOT_DISPATCHED",
        "ACKNOWLEDGED_APPLIED",
        "ACKNOWLEDGED_REJECTED",
        "OUTCOME_UNKNOWN",
        "OBSERVED_APPLIED_AFTER_RECOVERY",
        "OBSERVED_NOT_APPLIED_AFTER_RECOVERY",
        "CONFLICTING_STATE",
    ],
    "crash_reconciliation": {
        "intent_without_receipt_requires_observation": True,
        "delete_existing_uid": {
            "exact_uid_still_exists_unchanged": (
                "same guarded delete may be retried"
            ),
            "name_absent": (
                "record OBSERVED_APPLIED_AFTER_RECOVERY and never repeat"
            ),
            "same_name_different_uid": (
                "record OBSERVED_APPLIED_AFTER_RECOVERY; never act destructively "
                "against replacement UID; continue only under operation-specific protocol"
            ),
            "inconsistent_or_unavailable_beyond_infrastructure_deadline": (
                "record OUTCOME_UNKNOWN and stop safely"
            ),
            "delete_replacement_uid_to_reproduce_request_forbidden": True,
        },
        "runner_owned_create": {
            "absent": "creation may be retried",
            "adoption_requires_exact_deterministic_name": True,
            "adoption_requires_exact_runner_ownership_labels": True,
            "adoption_requires_exact_attempt_identity": True,
            "adoption_requires_exact_canonical_spec_hash": True,
            "exact_match": "record OBSERVED_APPLIED_AFTER_RECOVERY and adopt",
            "ownership_or_spec_mismatch": (
                "record CONFLICTING_STATE and classify PROTOCOL_VIOLATION"
            ),
            "automatic_overwrite_or_delete_of_conflict_forbidden": True,
        },
        "service_restoration": {
            "captured_semantic_service_present_and_verified": "adopt",
            "service_absent": (
                "create only from durable captured restoration body"
            ),
            "unexpected_service_present": "do not overwrite",
            "safe_restored_state_not_established": "RESTORATION_BLOCKED",
        },
        "replacement_pod_deletion": {
            "selected_old_uid_remains": "same guarded delete may be retried",
            "selected_old_uid_absent": "never select or delete a second Pod",
            "wait_condition": "different Ready Pod UID",
            "new_target_within_same_challenge_forbidden": True,
        },
        "namespace_pod_recycle": {
            "each_captured_pod_uid_is_independent_operation": True,
            "intent_and_receipt_required_per_uid": True,
            "reconcile_each_uid_separately": True,
            "one_delete_never_implies_another": True,
            "broad_delete_forbidden": True,
        },
    },
}

ADJUDICATION_EVIDENCE_PROTOCOL = {
    "schema_version": 1,
    "scope": "every Boolean or categorical adjudication",
    "result_types": ["BOOLEAN", "CATEGORICAL"],
    "required_fields": [
        "adjudication_id",
        "predicate_oracle_or_classification_id",
        "result_type",
        "boolean_or_categorical_value",
        "reason",
        "first_observation_utc",
        "last_observation_utc",
        "monotonic_elapsed_time",
        "observation_count",
        "applicable_deadline",
        "evaluator_source_or_runner_bundle_sha256",
        "raw_evidence_references",
        "raw_evidence_sha256_per_reference",
        "kubernetes_uid_and_resource_version_references_when_applicable",
        "dependency_and_toolchain_identity",
        "attempt_id",
        "run_id",
    ],
    "summary_only_boolean_forbidden": True,
    "summary_result_must_reference_full_adjudication_object": True,
    "minimum_immutable_raw_evidence_objects": 1,
    "sha256_required_for_every_raw_evidence_reference": True,
    "raw_evidence_sealed_before_adjudication": True,
    "missing_raw_evidence_outcome": "PROTOCOL_VIOLATION",
    "checksum_mismatch_outcome": "PROTOCOL_VIOLATION",
    "infrastructure_classification_requires_target_independent_evidence": True,
    "contract_deadline_rejection_requires_complete_observation_series": True,
}

STATE_AUTHORITY_PROTOCOL = {
    "schema_version": 1,
    "authoritative_sources": [
        "append-only journal entries",
        "immutable mutation intent records",
        "immutable mutation receipt records",
        "immutable state-transition entries",
    ],
    "mutable_current_pointer": {
        "path": "state/current.json or equivalent",
        "authority": "convenience cache only",
        "authorize_mutation_or_resume_alone_forbidden": True,
        "missing_or_stale_pointer_may_be_rebuilt": True,
        "unsupported_claim_outcome": "PROTOCOL_VIOLATION",
        "completed_evidence_rewrite_to_repair_pointer_forbidden": True,
    },
    "startup": {
        "reconstruct_from_append_only_journal_required": True,
        "verify_journal_hash_chain_required": True,
        "compare_reconstructed_state_with_mutable_pointer_required": True,
    },
    "journal_hash_chain": {
        "required_fields": [
            "sequence_number",
            "previous_entry_sha256",
            "canonical_current_entry_sha256",
            "run_id",
            "attempt_id",
            "transition",
            "referenced_intent_receipt_and_adjudication_sha256",
            "utc_time",
            "monotonic_time",
            "boot_identity",
        ],
        "genesis_previous_entry_sha256": "0" * 64,
        "canonical_hash_algorithm": "sha256",
        "sequence_strictly_increases_by_one": True,
        "previous_hash_must_match_prior_canonical_entry": True,
    },
}

SENSITIVE_MATERIAL_POLICY = {
    "schema_version": 1,
    "control_model": "explicit capture allowlists and prohibited operations",
    "perfect_arbitrary_output_secret_scanning_claimed": False,
    "kubeconfig": {
        "contents_capture_allowed": False,
        "recordable_fields": ["path", "sha256"],
    },
    "prohibited_material": [
        "client certificates",
        "client private keys",
        "bearer tokens",
        "ServiceAccount tokens",
        "cloud credentials",
        "API keys",
        "SSH keys",
        "environment secrets",
        "Kubernetes Secret values",
        "raw HTTP Authorization headers",
        "TLS private material",
    ],
    "process_environment_wholesale_dump_allowed": False,
    "kubernetes_secret_objects_list_or_fetch_allowed": False,
    "command_recording": {
        "fixed_argv_required": True,
        "sensitive_values_must_be_redacted": True,
    },
    "evidence_capture_requires_explicit_kubernetes_kind_and_field_allowlist": True,
    "violation_outcome": "PROTOCOL_VIOLATION",
    "evidence_finalization_after_violation_allowed": False,
    "explicitly_allowable_nonsecret_records": [
        "hashes",
        "non-secret identifiers",
        "public certificate fingerprints",
    ],
}


class InjectedFailure(RuntimeError):
    """Test-only cooperative failure at a durable transaction boundary."""


class RecoveryBlocked(RuntimeError):
    """Recovery cannot safely choose an allowed journal-defined action."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_regular_bytes(path: Path) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise RuntimeError(f"Expected regular file: {path}")
        chunks = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
    finally:
        os.close(descriptor)


def sha256(path: Path) -> str:
    return sha256_bytes(read_regular_bytes(path))


def path_lexists(path: Path) -> bool:
    return os.path.lexists(path)


def current_hash_or_absent(path: Path) -> str:
    if not path_lexists(path):
        return ABSENT
    return sha256(path)


def require_hash_or_absent(value: object, field: str) -> str:
    if value == ABSENT:
        return ABSENT
    if not isinstance(value, str) or SHA256_PATTERN.fullmatch(value) is None:
        raise RecoveryBlocked(f"Invalid {field}: {value!r}")
    return value


def require_utc_timestamp(value: object, field: str) -> str:
    if not isinstance(value, str) or UTC_TIMESTAMP_PATTERN.fullmatch(value) is None:
        raise RecoveryBlocked(f"Invalid UTC timestamp field: {field}")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise RecoveryBlocked(f"Malformed UTC timestamp field: {field}") from exc
    if parsed.tzinfo != UTC:
        raise RecoveryBlocked(f"Timestamp is not UTC: {field}")
    return value


def fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def require_regular_destination_state(path: Path, expected_hash: str) -> None:
    observed = current_hash_or_absent(path)
    if observed != expected_hash:
        raise RuntimeError(
            f"Destination changed unexpectedly: {path}: "
            f"expected={expected_hash}, observed={observed}"
        )


def write_exclusive_file(path: Path, data: bytes, mode: int) -> None:
    if path_lexists(path):
        raise FileExistsError(f"Refusing to reuse transaction path: {path}")
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open(path, flags, 0o600)
    try:
        os.fchmod(descriptor, mode)
        offset = 0
        while offset < len(data):
            written = os.write(descriptor, data[offset:])
            if written <= 0:
                raise OSError(f"Short write to {path}")
            offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def install_prepared_file(
    temporary_path: Path,
    destination: Path,
    expected_old_hash: str,
    expected_new_hash: str,
) -> None:
    if sha256(temporary_path) != expected_new_hash:
        raise RuntimeError(f"Prepared bytes changed: {temporary_path}")
    require_regular_destination_state(destination, expected_old_hash)
    os.replace(temporary_path, destination)
    fsync_directory(destination.parent)


def canonical_json_bytes(value: dict) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def safe_relative_path(value: object) -> PurePosixPath:
    if not isinstance(value, str):
        raise RecoveryBlocked(f"Journal path is not a string: {value!r}")
    pure = PurePosixPath(value)
    if (
        pure.is_absolute()
        or not pure.parts
        or any(part in ("", ".", "..") or part.startswith("-") for part in pure.parts)
    ):
        raise RecoveryBlocked(f"Unsafe journal path: {value!r}")
    return pure


def resolve_recorded_temp_path(
    value: object,
    destination: Path,
    role: str,
    transaction_id: str,
) -> Path:
    pure = safe_relative_path(value)
    expected_parent = destination.parent.relative_to(ROOT).as_posix()
    observed_parent = pure.parent.as_posix()
    if observed_parent != expected_parent:
        raise RecoveryBlocked(
            f"Journal temporary path has wrong parent for {role}: {value!r}"
        )
    prefix = f".{destination.name}.{role}.{transaction_id}."
    if not pure.name.startswith(prefix) or not pure.name.endswith(".tmp"):
        raise RecoveryBlocked(f"Invalid transaction-owned {role} path: {value!r}")
    token = pure.name[len(prefix) : -len(".tmp")]
    if SAFE_TEMP_TOKEN_PATTERN.fullmatch(token) is None:
        raise RecoveryBlocked(f"Invalid transaction suffix for {role}: {value!r}")
    return ROOT.joinpath(*pure.parts)


def allocate_temp_path(
    destination: Path,
    role: str,
    transaction_id: str,
) -> Path:
    token = secrets.token_hex(8)
    path = destination.parent / (
        f".{destination.name}.{role}.{transaction_id}.{token}.tmp"
    )
    if path_lexists(path):
        raise RuntimeError(f"Unexpected temporary-path collision: {path}")
    return path


@contextmanager
def repository_lock():
    descriptor = os.open(ROOT, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def ensure_no_transaction_journal() -> None:
    if path_lexists(JOURNAL_PATH):
        raise RuntimeError(
            "Interrupted execution-profile transaction detected; run "
            "--recover-interrupted-regeneration"
        )


def parse_manifest_bytes(data: bytes) -> dict[str, str]:
    try:
        text = data.decode("ascii")
    except UnicodeDecodeError as exc:
        raise RuntimeError("Manifest must be ASCII") from exc
    if not text.endswith("\n") or "\r" in text or "\0" in text:
        raise RuntimeError("Manifest must use exact LF-terminated canonical lines")
    lines = text.splitlines()
    if len(lines) != len(MANIFEST_PATHS):
        raise RuntimeError("Manifest must contain exactly three entries")

    parsed = {}
    line_pattern = re.compile(r"^([0-9a-f]{64})  ([A-Za-z0-9._/-]+)$")
    for index, (line, expected_path) in enumerate(
        zip(lines, MANIFEST_PATHS, strict=True),
        start=1,
    ):
        match = line_pattern.fullmatch(line)
        if match is None:
            raise RuntimeError(f"Malformed manifest line {index}")
        digest, relative = match.groups()
        pure = PurePosixPath(relative)
        if (
            pure.is_absolute()
            or relative.startswith("-")
            or any(
                part in ("", ".", "..") or part.startswith("-")
                for part in pure.parts
            )
        ):
            raise RuntimeError(f"Unsafe manifest path on line {index}")
        if relative != expected_path:
            raise RuntimeError(
                f"Noncanonical manifest order/path on line {index}: {relative}"
            )
        if relative in parsed:
            raise RuntimeError(f"Duplicate manifest path: {relative}")
        parsed[relative] = digest
    return parsed


def render_manifest_bytes(profile_bytes: bytes) -> bytes:
    entries = (
        (GENERATOR_RELATIVE_PATH, sha256(GENERATOR_PATH)),
        (PROFILE_RELATIVE_PATH, sha256_bytes(profile_bytes)),
        (SCHEMA_RELATIVE_PATH, sha256(SCHEMA_PATH)),
    )
    return "".join(
        f"{digest}  {relative}\n" for relative, digest in entries
    ).encode("ascii")


def verify_manifest_bytes(
    manifest_bytes: bytes,
    profile_bytes: bytes,
) -> dict[str, str]:
    parsed = parse_manifest_bytes(manifest_bytes)
    calculated = {
        GENERATOR_RELATIVE_PATH: sha256(GENERATOR_PATH),
        PROFILE_RELATIVE_PATH: sha256_bytes(profile_bytes),
        SCHEMA_RELATIVE_PATH: sha256(SCHEMA_PATH),
    }
    if parsed != calculated:
        raise RuntimeError(
            f"Manifest digest mismatch: expected={calculated!r}, observed={parsed!r}"
        )
    expected_bytes = render_manifest_bytes(profile_bytes)
    if manifest_bytes != expected_bytes:
        raise RuntimeError("Manifest bytes are not canonical")
    return parsed


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def git_returncode(repo: Path, *args: str) -> int:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        capture_output=True,
        text=True,
    ).returncode


def git_raw(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
    )
    try:
        return result.stdout.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RuntimeError("Git output is not valid UTF-8") from exc


def git_bytes(repo: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Git byte command failed with status {result.returncode}: {args!r}"
        )
    return result.stdout


def require_git_ancestor(repo: Path, ancestor: str, descendant: str) -> None:
    result = subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "merge-base",
            "--is-ancestor",
            ancestor,
            descendant,
        ],
        check=False,
        capture_output=True,
    )
    if result.returncode == 0:
        return
    if result.returncode == 1:
        raise RuntimeError(
            f"Frozen contract commit {ancestor} is not an ancestor of {descendant}"
        )
    raise RuntimeError(
        "Git ancestry validation failed with execution status "
        f"{result.returncode}"
    )


def verify_contract_provenance(
    repo: Path = ROOT,
    working_root: Path = ROOT,
    tag_name: str = TAG_NAME,
    expected_tag_object: str = TAG_OBJECT,
    expected_commit: str = CONTRACT_COMMIT,
    expected_tree: str = CONTRACT_TREE,
    immutable_paths: tuple[str, ...] = IMMUTABLE_CONTRACT_PATHS,
) -> None:
    tag_ref = f"refs/tags/{tag_name}"
    try:
        observed_tag_object = git(repo, "rev-parse", "--verify", tag_ref)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"Frozen contract tag is missing: {tag_name}") from exc
    if observed_tag_object != expected_tag_object:
        raise RuntimeError(
            "Frozen contract tag object differs: "
            f"expected={expected_tag_object}, observed={observed_tag_object}"
        )
    if git(repo, "cat-file", "-t", observed_tag_object) != "tag":
        raise RuntimeError("Frozen contract tag is not an annotated tag")
    peeled_commit = git(repo, "rev-parse", f"{observed_tag_object}^{{}}")
    if peeled_commit != expected_commit:
        raise RuntimeError(
            "Frozen contract tag peels to a different commit: "
            f"expected={expected_commit}, observed={peeled_commit}"
        )
    if git(repo, "cat-file", "-t", peeled_commit) != "commit":
        raise RuntimeError("Frozen contract tag does not peel to a commit")
    peeled_tree = git(repo, "rev-parse", f"{peeled_commit}^{{tree}}")
    if peeled_tree != expected_tree:
        raise RuntimeError(
            "Frozen contract commit tree differs: "
            f"expected={expected_tree}, observed={peeled_tree}"
        )
    require_git_ancestor(repo, expected_commit, "HEAD")

    for relative in immutable_paths:
        pure = safe_relative_path(relative)
        if pure.as_posix() != relative:
            raise RuntimeError(f"Noncanonical immutable contract path: {relative!r}")
        frozen_bytes = git_bytes(repo, "show", f"{expected_commit}:{relative}")
        current_bytes = read_regular_bytes(working_root.joinpath(*pure.parts))
        if current_bytes != frozen_bytes:
            raise RuntimeError(
                f"Immutable contract artifact differs from frozen tag: {relative}"
            )


def parse_submodule_status(
    output: str,
    expected_submodules: dict[str, str],
) -> dict[str, str]:
    if "\0" in output:
        raise RuntimeError("Submodule status contains NUL")
    normalized = output.replace("\r\n", "\n")
    if "\r" in normalized:
        raise RuntimeError("Submodule status has a malformed line ending")
    if normalized.endswith("\n"):
        normalized = normalized[:-1]
    lines = [] if normalized == "" else normalized.split("\n")
    if any(line == "" for line in lines):
        raise RuntimeError("Submodule status contains an empty line")

    for path, commit in expected_submodules.items():
        safe_relative_path(path)
        if SHA1_PATTERN.fullmatch(commit) is None:
            raise RuntimeError(f"Invalid expected submodule commit: {path}")

    observed: dict[str, str] = {}
    line_pattern = re.compile(
        r"^([0-9a-f]{40}) ([^()\s]+)(?: \([^()\r\n]*\))?$"
    )
    for line_number, line in enumerate(lines, start=1):
        prefix = line[0]
        if prefix not in (" ", "-", "+", "U"):
            if prefix in "0123456789abcdef":
                raise RuntimeError(
                    f"Malformed submodule status line {line_number}: "
                    "missing status prefix"
                )
            raise RuntimeError(
                f"Unknown submodule status prefix on line {line_number}: "
                f"{prefix!r}"
            )
        if prefix != " ":
            classification = {
                "-": "uninitialized",
                "+": "commit mismatch",
                "U": "merge conflict",
            }[prefix]
            raise RuntimeError(
                f"Non-clean submodule status on line {line_number}: "
                f"{classification}"
            )
        match = line_pattern.fullmatch(line[1:])
        if match is None:
            raise RuntimeError(f"Malformed clean submodule line {line_number}")
        commit, path = match.groups()
        pure = safe_relative_path(path)
        canonical_path = pure.as_posix()
        if canonical_path != path:
            raise RuntimeError(f"Noncanonical submodule path: {path!r}")
        if path in observed:
            raise RuntimeError(f"Duplicate submodule path: {path}")
        observed[path] = commit

    unexpected = sorted(set(observed) - set(expected_submodules))
    missing = sorted(set(expected_submodules) - set(observed))
    if unexpected:
        raise RuntimeError(
            "Unexpected submodule paths:\n" + "\n".join(unexpected)
        )
    if missing:
        raise RuntimeError("Missing submodule paths:\n" + "\n".join(missing))
    mismatches = [
        f"{path}: expected={expected_submodules[path]}, observed={observed[path]}"
        for path in sorted(expected_submodules)
        if observed[path] != expected_submodules[path]
    ]
    if mismatches:
        raise RuntimeError(
            "Submodule commit mismatches:\n" + "\n".join(mismatches)
        )
    return observed


def verify_clean_pinned_repository(
    repo: Path,
    expected_head: str,
    expected_submodules: dict[str, str],
    label: str,
) -> None:
    observed_head = git(repo, "rev-parse", "HEAD")
    if observed_head != expected_head:
        raise RuntimeError(
            f"{label} HEAD differs: expected={expected_head}, "
            f"observed={observed_head}"
        )
    status = git(
        repo,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
        "--ignore-submodules=none",
    )
    if status:
        raise RuntimeError(f"{label} worktree is not clean:\n{status}")
    if git_returncode(
        repo,
        "diff",
        "--quiet",
        "--ignore-submodules=none",
        "--",
    ) != 0:
        raise RuntimeError(f"{label} has unstaged changes")
    if git_returncode(
        repo,
        "diff",
        "--cached",
        "--quiet",
        "--ignore-submodules=none",
        "--",
    ) != 0:
        raise RuntimeError(f"{label} has staged changes")
    submodules = git_raw(repo, "submodule", "status", "--recursive")
    try:
        parse_submodule_status(submodules, expected_submodules)
    except RuntimeError as exc:
        raise RuntimeError(
            f"{label} has non-clean submodule identity: {exc}"
        ) from exc


def verify_sremut_worktree(
    allowed_transaction_paths: set[str] | frozenset[str] = frozenset(),
) -> None:
    staged = git(ROOT, "diff", "--cached", "--name-only", "--")
    if staged:
        raise RuntimeError("SREMut has staged changes")

    unstaged_text = git(ROOT, "diff", "--name-only", "--")
    unstaged = set(filter(None, unstaged_text.splitlines()))
    unexpected_tracked = sorted(unstaged - SELF_VALIDATING_TRACKED_PATHS)
    if unexpected_tracked:
        raise RuntimeError(
            "Unexpected SREMut tracked changes:\n"
            + "\n".join(unexpected_tracked)
        )

    untracked_text = git(
        ROOT,
        "ls-files",
        "--others",
        "--exclude-standard",
    )
    untracked = set(filter(None, untracked_text.splitlines()))
    allowed = set(EXPECTED_EXECUTION_PROFILE_ARTIFACTS)
    allowed.update(allowed_transaction_paths)
    unexpected = sorted(untracked - allowed)
    if unexpected:
        raise RuntimeError(
            "Unexpected SREMut untracked paths:\n" + "\n".join(unexpected)
        )


def installed_environment() -> dict[str, str]:
    return {
        "python_executable": str(Path(sys.executable).absolute()),
        "python_implementation": platform.python_implementation(),
        "python_version": platform.python_version(),
        "pyyaml_version": metadata.version("PyYAML"),
        "kubernetes_client_version": metadata.version("kubernetes"),
        "jsonschema_version": metadata.version("jsonschema"),
        "mitigation_oracle_source_sha256": sha256(MITIGATION_ORACLE_SOURCE),
    }


def verify_environment() -> None:
    observed = installed_environment()
    if observed != EXPECTED_ORIGINAL_ORACLE_ENVIRONMENT:
        raise RuntimeError(
            "Observed SREGym original-oracle runtime differs from the frozen "
            "baseline: "
            f"expected={EXPECTED_ORIGINAL_ORACLE_ENVIRONMENT!r}, "
            f"observed={observed!r}"
        )


def verify_bindings(
    allowed_transaction_paths: set[str] | frozenset[str] = frozenset(),
) -> None:
    verify_contract_provenance()
    checks = {
        "contract hash": (sha256(CONTRACT_PATH), CONTRACT_SHA256),
        "registry hash": (sha256(REGISTRY_PATH), REGISTRY_SHA256),
        "SREGym commit": (git(SREGYM_ROOT, "rev-parse", "HEAD"), SREGYM_COMMIT),
        "applications commit": (
            git(APPLICATIONS_ROOT, "rev-parse", "HEAD"),
            APPLICATIONS_COMMIT,
        ),
        "kubectl hash": (sha256(KUBECTL_PATH), KUBECTL_SHA256),
    }
    failed = [
        f"{name}: expected {expected!r}, observed {observed!r}"
        for name, (observed, expected) in checks.items()
        if observed != expected
    ]
    if failed:
        raise RuntimeError("Frozen binding verification failed:\n" + "\n".join(failed))

    version = json.loads(
        subprocess.run(
            [str(KUBECTL_PATH), "version", "--client=true", "-o", "json"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    )["clientVersion"]
    if version["gitVersion"] != "v1.32.0" or version["platform"] != "linux/amd64":
        raise RuntimeError(f"Unexpected pinned kubectl identity: {version!r}")

    verify_clean_pinned_repository(
        SREGYM_ROOT,
        SREGYM_COMMIT,
        EXPECTED_SREGYM_RECURSIVE_SUBMODULES,
        "SREGym",
    )
    verify_clean_pinned_repository(
        APPLICATIONS_ROOT,
        APPLICATIONS_COMMIT,
        EXPECTED_APPLICATIONS_RECURSIVE_SUBMODULES,
        "SREGym-applications",
    )
    verify_sremut_worktree(allowed_transaction_paths)


def build_profile(frozen_at: str) -> dict:
    profile = {
        "schema_version": 1,
        "profile_id": "sremut/missing-service-social-network/pilot-execution-v1",
        "status": "FROZEN_BEFORE_MUTANT_EXECUTION",
        "frozen_at": frozen_at,
        "schema": {
            "path": "schemas/missing-service-execution-profile-v1.schema.json",
            "draft": "https://json-schema.org/draft/2020-12/schema",
            "sha256": sha256(SCHEMA_PATH),
        },
        "frozen_bindings": {
            "contract": {
                "tag": TAG_NAME,
                "tag_object": TAG_OBJECT,
                "commit": CONTRACT_COMMIT,
                "tree": CONTRACT_TREE,
                "contract_path": "contracts/missing_service_social_network.yaml",
                "contract_sha256": CONTRACT_SHA256,
                "registry_path": "mutants/missing_service_social_network/registry.yaml",
                "registry_sha256": REGISTRY_SHA256,
            },
            "sregym": {
                "repository": str(SREGYM_ROOT),
                "commit": SREGYM_COMMIT,
                "applications_repository": str(APPLICATIONS_ROOT),
                "applications_commit": APPLICATIONS_COMMIT,
            },
            "kubectl": {
                "path": str(KUBECTL_PATH),
                "version": "v1.32.0",
                "platform": "linux/amd64",
                "sha256": KUBECTL_SHA256,
            },
            "kubernetes_context": "kind-kind",
            "namespace": "social-network",
            "target_service": "user-service",
        },
        "original_oracle_runtime": {
            "purpose": "preserve the original benchmark implementation and its baselined behavior",
            "virtual_environment": {
                "path": str(SREGYM_ROOT / ".venv"),
                "python": {
                    "implementation": "CPython",
                    "version": "3.12.3",
                    "executable": str(SREGYM_PYTHON),
                },
            },
            "dependencies": {
                "PyYAML": "6.0.2",
                "kubernetes": "30.1.0",
                "jsonschema": "4.23.0",
            },
            "compatibility": {
                "generated_for_kubernetes": "1.30",
                "target_server": "1.32.0",
                "compatibility": "COMMON_APIS_ONLY",
            },
            "allowed_role": "read-only stock original-oracle invocation",
            "prohibited_roles": [
                "runner mutation",
                "runner restoration",
                "active challenges",
            ],
            "source_identity": {
                "repository_commit": SREGYM_COMMIT,
                "module_path": "sregym/conductor/oracles/mitigation.py",
                "module_sha256": MITIGATION_ORACLE_SOURCE_SHA256,
            },
            "execution": {
                "stock_implementation_unchanged_required": True,
                "isolated_subprocess_required": True,
                "mutable_python_module_state_shared_with_runner": False,
                "run_before_any_active_challenge": True,
                "upgrade_to_match_runner_environment_forbidden": True,
                "source_hashes_and_dependency_versions_required_in_evidence": True,
            },
            "adapter": {
                "kind": "narrow original-oracle subprocess adapter",
                "runner_import_and_in_process_execution_forbidden": True,
                "communication": "immutable JSON input/output artifacts",
                "permitted_input_fields": [
                    "kubernetes_context",
                    "namespace",
                    "captured_replica_baseline",
                    "evidence_paths",
                ],
                "additional_input_fields_forbidden": True,
                "preserved_outputs": [
                    "stdout",
                    "stderr",
                    "exit_status",
                    "source_hashes",
                    "dependency_versions",
                    "returned_boolean",
                ],
                "challenge_pod_creation_forbidden": True,
                "mutation_forbidden": True,
                "restoration_forbidden": True,
                "interrupted_invocation_policy": "frozen one-shot policy",
                "oracle_exception_outcome": "preserve exception; never reinterpret as Boolean rejection",
            },
        },
        "sremut_runner_runtime": {
            "purpose": "execute the future SREMut runner with exact Kubernetes 1.32 API parity",
            "python": {
                "implementation": "CPython",
                "version": "3.12.3",
            },
            "dependencies": {
                "PyYAML": "6.0.2",
                "kubernetes": "32.0.1",
                "jsonschema": "4.23.0",
            },
            "tooling": {
                "uv": "0.12.5",
                "exact_version_required_for_lock_generation": True,
                "exact_version_required_for_frozen_execution": True,
            },
            "compatibility": {
                "generated_for_kubernetes": "1.32",
                "target_server": "1.32.0",
                "compatibility": "EXACT_API_PARITY",
            },
            "allowed_role": "SREMut runner orchestration and active execution",
            "prohibited_role": "stock original-oracle in-process execution",
            "responsibilities": [
                "typed UID-preconditioned Kubernetes mutations",
                "guarded Service creation and restoration",
                "evidence recording",
                "contract evaluation",
                "orchestration and state transitions",
            ],
            "execution_prerequisites": {
                "runner_execution_allowed": False,
                "pyproject": {
                    "repository_path": "pyproject.toml",
                    "required_before_runner_execution": True,
                    "present_at_profile_freeze": False,
                },
                "uv_lock": {
                    "repository_path": "uv.lock",
                    "required_before_runner_execution": True,
                    "present_at_profile_freeze": False,
                    "dependency_resolution_during_experiment_execution_allowed": False,
                    "frozen_execution_required": True,
                    "sha256_required_in_run_identity": True,
                },
                "runner_bundle": {
                    "required_before_runner_execution": True,
                    "algorithm": "sha256",
                    "canonical_input": (
                        "sorted_relative_path_two_spaces_sha256_newline_manifest"
                    ),
                    "sha256_required_in_run_identity": True,
                },
            },
        },
        "pilot_scope": {
            "mutants": ["MS-M01", "MS-M02", "MS-M03"],
            "planned_repetitions_per_mutant": 3,
            "schedule": [
                {"mutant": "MS-M01", "repetition": 1},
                {"mutant": "MS-M02", "repetition": 1},
                {"mutant": "MS-M03", "repetition": 1},
                {"mutant": "MS-M01", "repetition": 2},
                {"mutant": "MS-M02", "repetition": 2},
                {"mutant": "MS-M03", "repetition": 2},
                {"mutant": "MS-M01", "repetition": 3},
                {"mutant": "MS-M02", "repetition": 3},
                {"mutant": "MS-M03", "repetition": 3},
            ],
            "agent_launched": False,
            "maximum_active_mutation_runs": 1,
        },
        "reset_model": {
            "healthy_semantic_state_required_before_every_attempt": True,
            "full_redeployment_required_between_attempts": False,
            "capture_new_healthy_prestate_before_every_attempt": True,
            "fresh_fault_injection_before_every_attempt": True,
            "next_attempt_requires_restore_verified": True,
            "restoration_blocked_stops_schedule": True,
        },
        "fault_setup": {
            "capture_before_injection": [
                "Service/user-service raw object",
                "Service/user-service UID and resourceVersion",
                "MitigationOracle replica_count baseline",
            ],
            "service_deletion": {
                "namespace": "social-network",
                "name": "user-service",
                "uid_precondition": "captured metadata.uid",
                "resource_version_precondition": "captured metadata.resourceVersion",
            },
            "namespace_pod_recycle": {
                "capture_every_current_pod_name_uid_and_resource_version": True,
                "delete_each_captured_uid_once": True,
                "uid_precondition_required": True,
                "resource_version_precondition_required": True,
                "namespace_wide_unguarded_deletion_forbidden": True,
                "newly_observed_pods_must_not_be_added_to_delete_set": True,
            },
            "stabilization": {
                "deadline_seconds": 180,
                "poll_interval_seconds": 2,
                "pod_phase": "Running",
                "pod_ready_condition": True,
                "pod_deletion_timestamp": "absent",
                "all_container_statuses_present_and_ready": True,
                "baseline_deployment_capacity_floor_required": True,
                "workload_pod_running_and_ready": True,
            },
            "apply_mutant_after_fault_setup": True,
        },
        "service_normalization": {
            "source": "captured Service/user-service raw object",
            "derive_and_seal_during_healthy_state_capture": True,
            "stripped_fields": [
                "/metadata/creationTimestamp",
                "/metadata/deletionGracePeriodSeconds",
                "/metadata/deletionTimestamp",
                "/metadata/generation",
                "/metadata/managedFields",
                "/metadata/resourceVersion",
                "/metadata/selfLink",
                "/metadata/uid",
                "/status",
                "/spec/clusterIP",
                "/spec/clusterIPs",
                "/spec/healthCheckNodePort",
                "/spec/ports/*/nodePort",
            ],
            "preserve_all_other_fields": True,
            "helm_or_registry_reconstruction_forbidden": True,
            "dynamic_cluster_ip_change_allowed": True,
        },
        "mutants": {
            "MS-M01": {
                "action": "leave Service/user-service absent",
                "activation": {
                    "service_absent": True,
                    "strict_stabilization_required": True,
                },
                "expected_original_oracle": "PASS_HYPOTHESIS_NOT_A_RESULT",
                "expected_contract": "REJECT",
            },
            "MS-M02": {
                "action": "create captured-normalized Service with exact nonmatching selector",
                "selector": {"sremut-mutant-backend": "ms-m02"},
                "preflight_zero_matching_pods_required": True,
                "preserve_service_port": 9090,
                "preserve_target_port": 9090,
                "activation": {
                    "exact_selector_required": True,
                    "zero_matching_pods_required": True,
                    "zero_eligible_endpoints_required": True,
                },
                "expected_original_oracle": "PASS_HYPOTHESIS_NOT_A_RESULT",
                "expected_contract": "REJECT",
            },
            "MS-M03": {
                "action": "create captured-normalized Service with wrong targetPort",
                "selector": {"service": "user-service"},
                "service_port": 9090,
                "target_port": 65535,
                "activation": {
                    "exact_selector_required": True,
                    "exact_target_port_required": True,
                    "eligible_user_service_backend_required": True,
                },
                "expected_original_oracle": "PASS_HYPOTHESIS_NOT_A_RESULT",
                "expected_contract": "REJECT",
            },
            "common": {
                "preserve_unmodified_normalized_service_fields": True,
                "activation_verified_before_original_oracle": True,
                "activation_uses_structural_observation_only": True,
            },
        },
        "oracle_protocol": {
            "order": [
                "verify_mutant_activation",
                "start_original_stock_MitigationOracle",
                "seal_original_oracle_evidence_and_result",
                "create_active_challenge_resources",
                "evaluate_all_contract_predicates",
                "continue_safe_diagnostic_predicates_after_first_reject",
                "restore_application",
            ],
            "original_oracle": {
                "class": "sregym.conductor.oracles.mitigation.MitigationOracle",
                "source_commit": SREGYM_COMMIT,
                "invocations_per_attempt": 1,
                "resume_or_repeat_after_start_forbidden": True,
                "interruption_before_sealed_result": "HARNESS_TIMING_FAILURE",
                "active_challenge_before_sealed_result_forbidden": True,
            },
        },
        "retry_policy": {
            "maximum_attempts_per_planned_repetition": 2,
            "retry_eligible_classifications": [
                "HARNESS_TIMING_FAILURE",
                "INFRASTRUCTURE_FAILURE",
            ],
            "retry_forbidden_classifications": [
                "MUTANT_ACTIVATION_FAILURE",
                "PROTOCOL_VIOLATION",
                "INVALID_OR_EQUIVALENT_MUTANT",
                "RESTORATION_FAILURE",
            ],
            "completed_research_verdict_retry_forbidden": True,
            "every_attempt_preserved": True,
            "first_eligible_completed_attempt_counts": True,
            "retry_requires_restore_verified": True,
        },
        "workload_protocol": {
            "deadline_seconds": 60,
            "overrides_stock_collector_timeout_seconds": 90,
            "parser": "sregym.generators.workload.wrk2.Wrk2WorkloadManager._parse_log",
            "preserve_workload_oracle_success_semantics": True,
            "boundary": {
                "record_before_observation": True,
                "required_fields": [
                    "pod_name",
                    "pod_uid",
                    "container_restart_count",
                    "raw_log",
                    "raw_log_byte_length",
                    "raw_log_sha256",
                    "utc_wall_time",
                    "monotonic_time",
                    "boot_identity",
                ],
                "later_log_requires_exact_boundary_byte_prefix": True,
                "parse_only_complete_rounds_wholly_in_appended_suffix": True,
            },
            "minimum_fresh_requests": 50,
            "maximum_non_2xx_or_3xx_responses": 0,
            "non_2xx_or_3xx_marker_presence_rejects": True,
            "class_level_log_history_isolated_per_evaluation": True,
            "unexpected_evidence_change": {
                "log_truncation": "deterministic_non_contract_unless_target_causal",
                "pod_uid_change": "deterministic_non_contract_unless_target_causal",
                "container_restart": "deterministic_non_contract_unless_target_causal",
                "target_causal_outcome": "CONTRACT_REJECT",
                "independent_harness_outcome": "HARNESS_TIMING_FAILURE",
                "independent_infrastructure_outcome": "INFRASTRUCTURE_FAILURE",
            },
        },
        "replacement_challenge": {
            "selection": "lexicographically_smallest_eligible_ready_user-service_pod",
            "record_fields": ["metadata.name", "metadata.uid"],
            "delete_exact_recorded_uid_once": True,
            "second_deletion_target_forbidden": True,
            "replacement_uid_must_differ": True,
            "recheck_predicates": [
                "dns_identity",
                "eligible_backend_set",
                "routing_predicate",
                "fresh_workload_predicate",
            ],
            "final_capacity_MS_I5_recheck_required": True,
        },
        "restoration": {
            "restoration_body_source": "sealed captured-normalized Service",
            "delete_only_recorded_mutant_service_uid": True,
            "mutant_service_resource_version_precondition_required": True,
            "recreate_captured_semantic_service": True,
            "dynamic_cluster_ip_change_allowed": True,
            "capture_current_namespace_pods_before_recovery_recycle": True,
            "recycle_only_captured_recovery_pod_uids_once": True,
            "delete_only_recorded_challenge_pod_uid": True,
            "all_six_invariants_required": True,
            "fresh_workload_window_required": True,
            "restored_state_contract_pass_is_positive_control": True,
            "unexpected_target_object_change_outcome": "PROTOCOL_VIOLATION",
        },
        "state_machine": {
            "states": [
                "CREATED",
                "PREFLIGHT_PASS",
                "HEALTHY_STATE_CAPTURED",
                "MUTANT_INJECTED",
                "MUTANT_STATE_VERIFIED",
                "ORIGINAL_ORACLE_STARTED",
                "ORIGINAL_ORACLE_EVALUATED",
                "CONTRACT_EVALUATED",
                "RESTORE_STARTED",
                "RESTORE_VERIFIED",
                "FINALIZED",
                "ABORTED_SAFE",
                "RESTORATION_BLOCKED",
            ],
            "legal_transitions": [
                "CREATED->PREFLIGHT_PASS",
                "CREATED->ABORTED_SAFE",
                "PREFLIGHT_PASS->HEALTHY_STATE_CAPTURED",
                "PREFLIGHT_PASS->ABORTED_SAFE",
                "HEALTHY_STATE_CAPTURED->MUTANT_INJECTED",
                "HEALTHY_STATE_CAPTURED->RESTORE_STARTED",
                "HEALTHY_STATE_CAPTURED->ABORTED_SAFE",
                "MUTANT_INJECTED->MUTANT_STATE_VERIFIED",
                "MUTANT_INJECTED->RESTORE_STARTED",
                "MUTANT_STATE_VERIFIED->ORIGINAL_ORACLE_STARTED",
                "MUTANT_STATE_VERIFIED->RESTORE_STARTED",
                "ORIGINAL_ORACLE_STARTED->ORIGINAL_ORACLE_EVALUATED",
                "ORIGINAL_ORACLE_STARTED->RESTORE_STARTED",
                "ORIGINAL_ORACLE_EVALUATED->CONTRACT_EVALUATED",
                "ORIGINAL_ORACLE_EVALUATED->RESTORE_STARTED",
                "CONTRACT_EVALUATED->RESTORE_STARTED",
                "RESTORE_STARTED->RESTORE_VERIFIED",
                "RESTORE_STARTED->RESTORATION_BLOCKED",
                "RESTORE_VERIFIED->FINALIZED",
            ],
            "terminal_states": [
                "FINALIZED",
                "ABORTED_SAFE",
                "RESTORATION_BLOCKED",
            ],
            "backward_transitions_forbidden": True,
            "challenge_creation_minimum_state": "ORIGINAL_ORACLE_EVALUATED",
            "later_run_after_restoration_blocked_forbidden": True,
        },
        "adjudication": {
            "execution_status_values": [
                "RUNNING",
                "COMPLETED",
                "ABORTED_SAFE",
                "RESTORATION_BLOCKED",
            ],
            "original_oracle_verdict_values": [
                "NOT_EVALUATED",
                "PASS",
                "REJECT",
                "INTERRUPTED",
            ],
            "contract_verdict_values": [
                "NOT_EVALUATED",
                "PASS",
                "REJECT",
                "INCOMPLETE",
            ],
            "classifications": [
                "CONTRACT_REJECT",
                "ORIGINAL_ORACLE_REJECT",
                "MUTANT_ACTIVATION_FAILURE",
                "HARNESS_TIMING_FAILURE",
                "INFRASTRUCTURE_FAILURE",
                "RESTORATION_FAILURE",
                "PROTOCOL_VIOLATION",
                "INVALID_OR_EQUIVALENT_MUTANT",
                "COMPLETED",
            ],
            "target_predicate_deadline_failure": "CONTRACT_REJECT",
            "target_timeout_never_implies_infrastructure_failure": True,
            "execution_status_separate_from_research_verdicts": True,
        },
        "failure_evidence": {
            "FINALIZED_manifest": "complete_SHA256SUMS",
            "ABORTED_SAFE_manifest": "complete_SHA256SUMS",
            "RESTORATION_BLOCKED_manifest": "sealed_partial_SHA256SUMS",
            "RESTORATION_BLOCKED_global_stop_marker": True,
            "incomplete_stage_attempts_append_only": True,
            "overwrite_forbidden": True,
        },
        "challenge_pod": {
            "namespace": "social-network",
            "name_derivation": (
                "sremut-ms-{mutant_token}-r{repetition:02d}-a{attempt:02d}-"
                "{run_identity_sha256_first12}"
            ),
            "mutant_tokens": {
                "MS-M01": "m01",
                "MS-M02": "m02",
                "MS-M03": "m03",
            },
            "image": (
                "docker.io/library/busybox@sha256:"
                + CHALLENGE_AMD64_SHA256
            ),
            "image_platform": "linux/amd64",
            "image_pull_policy": "IfNotPresent",
            "oci_evidence": {
                "tag_lookup": "docker.io/library/busybox:1.36.1",
                "index_digest": "sha256:" + CHALLENGE_INDEX_SHA256,
                "index_media_type": "application/vnd.oci.image.index.v1+json",
                "platform_manifest_digest": "sha256:" + CHALLENGE_AMD64_SHA256,
                "platform_manifest_media_type": (
                    "application/vnd.oci.image.manifest.v1+json"
                ),
                "local_baseline_evidence": (
                    "/home/sakibbuet2k19/sremut/baselines/"
                    "missing_service_social_network/teardown-after-run-02/"
                    "nodes-after.json"
                ),
                "local_source_probe_evidence": [
                    "sregym.conductor.oracles.dns_resolution_mitigation",
                    "sregym.conductor.oracles.service_endpoint_mitigation",
                ],
            },
            "container_command": [
                "sh",
                "-c",
                "trap 'exit 0' TERM INT; sleep 600 & wait",
            ],
            "dns_probe_command": [
                "sh",
                "-c",
                "nslookup user-service.social-network.svc.cluster.local",
            ],
            "dns_result_parser": (
                "parse IPv4 addresses and require Service.spec.clusterIP membership"
            ),
            "tcp_probe_command": [
                "sh",
                "-c",
                "nc -z -w 3 user-service.social-network.svc.cluster.local 9090",
            ],
            "automount_service_account_token": False,
            "restart_policy": "Never",
            "active_deadline_seconds": 600,
            "termination_grace_period_seconds": 5,
            "security_context": {
                "run_as_non_root": True,
                "run_as_user": 65532,
                "run_as_group": 65532,
                "read_only_root_filesystem": True,
                "allow_privilege_escalation": False,
                "capabilities_drop": ["ALL"],
                "seccomp_profile": "RuntimeDefault",
            },
            "resources": {
                "requests": {"cpu": "5m", "memory": "8Mi"},
                "limits": {"cpu": "50m", "memory": "32Mi"},
            },
            "labels": {
                "app.kubernetes.io/name": "sremut-challenge",
                "app.kubernetes.io/managed-by": "sremut",
                "sremut-run-id": "derived run identity",
                "sremut-mutant": "derived frozen mutant ID",
                "sremut-attempt": "derived attempt number",
            },
            "spec_hash": {
                "algorithm": "sha256",
                "canonical_input": (
                    "sorted compact JSON of derived metadata.name, metadata.namespace, "
                    "labels and Pod spec"
                ),
                "required_before_creation": True,
            },
            "exec_uid_verification": "immediately_before_and_after_every_exec",
            "deletion": {
                "record_uid_before_delete": True,
                "uid_precondition_required": True,
                "delete_exact_uid_once": True,
            },
        },
        "cache_isolation": {
            "kubectl_path": str(KUBECTL_PATH),
            "kubeconfig_path": "/home/sakibbuet2k19/.kube/config",
            "context": "kind-kind",
            "per_run_cache_dir_required": True,
            "kubectl_global_flag": "--cache-dir=<run>/harness/kubectl-cache",
            "default_cache_path": "/home/sakibbuet2k19/.kube/cache",
            "default_cache_tree_hash_before_and_after_required": True,
            "default_cache_must_remain_unchanged": True,
            "per_run_cache_retained": True,
            "per_run_cache_separate_harness_manifest": True,
            "caches_are_research_evidence": False,
            "home_or_shell_configuration_change_forbidden": True,
            "live_preflight_must_prove_discovery_and_http_cache_redirection": True,
        },
        "evidence_safety": {
            "subprocess_argv_arrays_required": True,
            "subprocess_shell": False,
            "typed_kubernetes_api_mutations_required": True,
            "uid_and_resource_version_safeguards_required": True,
            "atomic_write_sequence": [
                "write unique temporary file",
                "fsync file",
                "rename without overwrite",
                "fsync parent directory",
            ],
            "append_only_journal": True,
            "global_host_lock": "fcntl exclusive lock",
            "forbidden_operations": [
                "namespace deletion",
                "Helm mutation",
                "rollout",
                "broad label deletion",
                "cluster-scoped mutation",
                "automatic cluster reconciliation",
            ],
            "destructive_scope": (
                "only captured target object UIDs and exact runner-owned challenge Pod UID"
            ),
        },
        "mutation_operation_protocol": deepcopy(MUTATION_OPERATION_PROTOCOL),
        "adjudication_evidence_protocol": deepcopy(
            ADJUDICATION_EVIDENCE_PROTOCOL
        ),
        "state_authority_protocol": deepcopy(STATE_AUTHORITY_PROTOCOL),
        "sensitive_material_policy": deepcopy(SENSITIVE_MATERIAL_POLICY),
        "freeze_readiness": {
            "profile_semantics_complete": True,
            "runner_implemented": False,
            "runner_execution_allowed": False,
            "execution_prerequisites": [
                "committed implementation",
                "repository uv.lock",
                "canonical runner-bundle SHA-256",
                "passing unit and non-mutating live preflight tests",
            ],
            "unresolved_profile_freeze_blockers": [],
        },
    }
    return profile


def semantic_without_frozen_at(profile: dict) -> dict:
    normalized = deepcopy(profile)
    normalized.pop("frozen_at", None)
    return normalized


def profile_before_durability_protocols(expected: dict) -> dict:
    previous = deepcopy(expected)
    previous["schema"]["sha256"] = PREVIOUS_SCHEMA_SHA256
    previous.pop("mutation_operation_protocol")
    previous.pop("adjudication_evidence_protocol")
    previous.pop("state_authority_protocol")
    previous.pop("sensitive_material_policy")
    return previous


def legacy_profile_before_runtime_separation(expected: dict) -> dict:
    legacy = profile_before_durability_protocols(expected)
    legacy["schema"]["sha256"] = LEGACY_SCHEMA_SHA256
    legacy.pop("original_oracle_runtime")
    legacy.pop("sremut_runner_runtime")
    legacy["implementation_environment"] = deepcopy(
        LEGACY_IMPLEMENTATION_ENVIRONMENT
    )
    return legacy


def validate_profile(profile: dict) -> None:
    schema_bytes = read_regular_bytes(SCHEMA_PATH)
    schema = json.loads(schema_bytes.decode("utf-8"))
    Draft202012Validator.check_schema(schema)
    errors = sorted(
        Draft202012Validator(schema).iter_errors(profile),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        rendered = "\n".join(
            f"{list(error.absolute_path)!r}: {error.message}" for error in errors
        )
        raise RuntimeError("Profile schema validation failed:\n" + rendered)

    expected = build_profile(profile["frozen_at"])
    if profile != expected:
        raise RuntimeError("Profile differs from generator-defined frozen semantics")


def render(profile: dict) -> str:
    return yaml.safe_dump(
        profile,
        sort_keys=False,
        allow_unicode=True,
        width=88,
    )


def render_profile_bytes(profile: dict) -> bytes:
    rendered = render(profile)
    if not rendered.endswith("\n"):
        raise RuntimeError("Canonical profile rendering lacks final newline")
    return rendered.encode("utf-8")


def parse_profile_bytes(profile_bytes: bytes) -> dict:
    try:
        text = profile_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RuntimeError("Profile is not UTF-8") from exc
    profile = yaml.safe_load(text)
    if not isinstance(profile, dict):
        raise RuntimeError("Generated profile is not a mapping")
    return profile


def verify_profile_bytes(profile_bytes: bytes) -> dict:
    profile = parse_profile_bytes(profile_bytes)
    validate_profile(profile)
    expected_bytes = render_profile_bytes(profile)
    if profile_bytes != expected_bytes:
        raise RuntimeError("Profile bytes are not exact canonical YAML")
    return profile


def verify_installed_pair() -> dict:
    profile_bytes = read_regular_bytes(PROFILE_PATH)
    manifest_bytes = read_regular_bytes(MANIFEST_PATH)
    profile = verify_profile_bytes(profile_bytes)
    verify_manifest_bytes(manifest_bytes, profile_bytes)
    if profile["schema"]["sha256"] != sha256(SCHEMA_PATH):
        raise RuntimeError("Profile does not bind the exact installed schema bytes")
    return profile


def destination_mode(path: Path) -> int:
    if not path_lexists(path):
        return 0o664
    metadata = os.lstat(path)
    if not stat.S_ISREG(metadata.st_mode):
        raise RuntimeError(f"Destination is not a regular file: {path}")
    return stat.S_IMODE(metadata.st_mode)


def journal_relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def build_transaction_journal(
    operation_type: str,
    profile: dict,
    profile_bytes: bytes,
    manifest_bytes: bytes,
) -> tuple[dict, dict[str, Path]]:
    if operation_type not in ("INITIAL_CREATION", "REGENERATION"):
        raise RuntimeError(f"Unsupported operation type: {operation_type}")
    transaction_id = secrets.token_hex(16)
    paths = {
        "profile": allocate_temp_path(
            PROFILE_PATH,
            "profile",
            transaction_id,
        ),
        "manifest": allocate_temp_path(
            MANIFEST_PATH,
            "manifest",
            transaction_id,
        ),
        "journal_update": allocate_temp_path(
            JOURNAL_PATH,
            "journal",
            transaction_id,
        ),
    }
    journal = {
        "schema_version": JOURNAL_SCHEMA_VERSION,
        "transaction_id": transaction_id,
        "repository_identity": {
            "root": str(ROOT),
            "head": git(ROOT, "rev-parse", "HEAD"),
            "tree": git(ROOT, "rev-parse", "HEAD^{tree}"),
        },
        "operation_type": operation_type,
        "frozen_at": profile["frozen_at"],
        "old_profile_sha256": current_hash_or_absent(PROFILE_PATH),
        "old_manifest_sha256": current_hash_or_absent(MANIFEST_PATH),
        "new_profile_sha256": sha256_bytes(profile_bytes),
        "new_manifest_sha256": sha256_bytes(manifest_bytes),
        "expected_generator_sha256": sha256(GENERATOR_PATH),
        "expected_schema_sha256": sha256(SCHEMA_PATH),
        "temporary_paths": {
            name: journal_relative(path)
            for name, path in paths.items()
        },
        "current_durable_phase": "PREPARED",
        "created_utc": datetime.now(UTC).isoformat(timespec="microseconds"),
        "allowed_recovery_actions": list(ALLOWED_RECOVERY_ACTIONS),
    }
    return journal, paths


def validate_journal(journal: object) -> tuple[dict, dict[str, Path]]:
    if not isinstance(journal, dict):
        raise RecoveryBlocked("Transaction journal is not an object")
    expected_keys = {
        "schema_version",
        "transaction_id",
        "repository_identity",
        "operation_type",
        "frozen_at",
        "old_profile_sha256",
        "old_manifest_sha256",
        "new_profile_sha256",
        "new_manifest_sha256",
        "expected_generator_sha256",
        "expected_schema_sha256",
        "temporary_paths",
        "current_durable_phase",
        "created_utc",
        "allowed_recovery_actions",
    }
    if set(journal) != expected_keys:
        raise RecoveryBlocked("Transaction journal fields are not exact")
    if journal["schema_version"] != JOURNAL_SCHEMA_VERSION:
        raise RecoveryBlocked("Unknown transaction journal schema version")
    transaction_id = journal["transaction_id"]
    if (
        not isinstance(transaction_id, str)
        or TRANSACTION_ID_PATTERN.fullmatch(transaction_id) is None
    ):
        raise RecoveryBlocked("Invalid transaction ID")
    if journal["operation_type"] not in ("INITIAL_CREATION", "REGENERATION"):
        raise RecoveryBlocked("Unknown transaction operation")
    require_utc_timestamp(journal["frozen_at"], "frozen_at")
    if journal["current_durable_phase"] not in JOURNAL_PHASES:
        raise RecoveryBlocked("Unknown transaction phase")
    if journal["allowed_recovery_actions"] != list(ALLOWED_RECOVERY_ACTIONS):
        raise RecoveryBlocked("Unexpected recovery-action set")

    require_utc_timestamp(journal["created_utc"], "created_utc")

    repository_identity = journal["repository_identity"]
    expected_repository_identity = {
        "root": str(ROOT),
        "head": git(ROOT, "rev-parse", "HEAD"),
        "tree": git(ROOT, "rev-parse", "HEAD^{tree}"),
    }
    if repository_identity != expected_repository_identity:
        raise RecoveryBlocked("Transaction repository identity mismatch")

    for field in (
        "old_profile_sha256",
        "old_manifest_sha256",
    ):
        require_hash_or_absent(journal[field], field)
    for field in (
        "new_profile_sha256",
        "new_manifest_sha256",
        "expected_generator_sha256",
        "expected_schema_sha256",
    ):
        value = journal[field]
        if not isinstance(value, str) or SHA256_PATTERN.fullmatch(value) is None:
            raise RecoveryBlocked(f"Invalid transaction hash field: {field}")

    old_pair = (
        journal["old_profile_sha256"],
        journal["old_manifest_sha256"],
    )
    new_pair = (
        journal["new_profile_sha256"],
        journal["new_manifest_sha256"],
    )
    if journal["operation_type"] == "INITIAL_CREATION":
        if old_pair != (ABSENT, ABSENT):
            raise RecoveryBlocked("Initial-creation journal has a non-absent old pair")
    elif ABSENT in old_pair:
        raise RecoveryBlocked("Regeneration journal has an absent old artifact")
    if old_pair == new_pair:
        raise RecoveryBlocked("Transaction old and new pairs are identical")

    temporary_paths = journal["temporary_paths"]
    if not isinstance(temporary_paths, dict) or set(temporary_paths) != {
        "profile",
        "manifest",
        "journal_update",
    }:
        raise RecoveryBlocked("Transaction temporary path fields are not exact")
    paths = {
        "profile": resolve_recorded_temp_path(
            temporary_paths["profile"],
            PROFILE_PATH,
            "profile",
            transaction_id,
        ),
        "manifest": resolve_recorded_temp_path(
            temporary_paths["manifest"],
            MANIFEST_PATH,
            "manifest",
            transaction_id,
        ),
        "journal_update": resolve_recorded_temp_path(
            temporary_paths["journal_update"],
            JOURNAL_PATH,
            "journal",
            transaction_id,
        ),
    }
    return journal, paths


def read_validated_journal() -> tuple[dict, dict[str, Path]]:
    if not path_lexists(JOURNAL_PATH):
        raise RecoveryBlocked("No interrupted transaction journal exists")
    try:
        journal = json.loads(read_regular_bytes(JOURNAL_PATH).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RecoveryBlocked("Malformed transaction journal") from exc
    return validate_journal(journal)


def write_journal(journal: dict, paths: dict[str, Path]) -> None:
    if path_lexists(JOURNAL_PATH):
        read_regular_bytes(JOURNAL_PATH)
    journal_temp = paths["journal_update"]
    write_exclusive_file(journal_temp, canonical_json_bytes(journal), 0o600)
    os.replace(journal_temp, JOURNAL_PATH)
    fsync_directory(ROOT)


def update_journal_phase(
    journal: dict,
    paths: dict[str, Path],
    phase: str,
) -> None:
    if phase not in JOURNAL_PHASES:
        raise RuntimeError(f"Unknown transaction phase: {phase}")
    journal["current_durable_phase"] = phase
    write_journal(journal, paths)


def remove_owned_file_if_present(path: Path) -> None:
    if not path_lexists(path):
        return
    metadata = os.lstat(path)
    if not stat.S_ISREG(metadata.st_mode):
        raise RecoveryBlocked(f"Refusing to remove non-regular owned path: {path}")
    os.unlink(path)


def cleanup_transaction(journal: dict, paths: dict[str, Path]) -> None:
    validated_journal, validated_paths = validate_journal(journal)
    if validated_journal["transaction_id"] != journal["transaction_id"]:
        raise RecoveryBlocked("Transaction identity changed during cleanup")
    for name in ("profile", "manifest", "journal_update"):
        if validated_paths[name] != paths[name]:
            raise RecoveryBlocked("Transaction path changed during cleanup")
        remove_owned_file_if_present(paths[name])
    remove_owned_file_if_present(JOURNAL_PATH)
    fsync_directory(PROFILE_PATH.parent)
    fsync_directory(ROOT)


def maybe_inject_failure(
    requested: str | None,
    point: str,
) -> None:
    if requested is None:
        return
    if requested not in FAILURE_INJECTION_POINTS:
        raise RuntimeError(f"Unknown test failure point: {requested}")
    if requested == point:
        raise InjectedFailure(f"Injected failure after {point}")


def install_profile_transaction(
    profile: dict,
    operation_type: str,
    fail_after: str | None = None,
) -> None:
    ensure_no_transaction_journal()
    verify_environment()
    verify_bindings()

    validate_profile(profile)
    profile_bytes = render_profile_bytes(profile)
    verify_profile_bytes(profile_bytes)
    manifest_bytes = render_manifest_bytes(profile_bytes)
    verify_manifest_bytes(manifest_bytes, profile_bytes)

    journal, paths = build_transaction_journal(
        operation_type,
        profile,
        profile_bytes,
        manifest_bytes,
    )
    old_profile_hash = journal["old_profile_sha256"]
    old_manifest_hash = journal["old_manifest_sha256"]
    if operation_type == "INITIAL_CREATION":
        if old_profile_hash != ABSENT or old_manifest_hash != ABSENT:
            raise RuntimeError("Initial creation requires absent profile and manifest")
    elif old_profile_hash == ABSENT or old_manifest_hash == ABSENT:
        raise RuntimeError("Regeneration requires existing profile and manifest")

    write_exclusive_file(
        paths["profile"],
        profile_bytes,
        destination_mode(PROFILE_PATH),
    )
    write_exclusive_file(
        paths["manifest"],
        manifest_bytes,
        destination_mode(MANIFEST_PATH),
    )
    write_journal(journal, paths)

    # These cooperative hooks run only after PREPARED is durable so every
    # injected test interruption has journal-defined recovery.
    maybe_inject_failure(fail_after, "TEMP_PROFILE_FSYNC")
    maybe_inject_failure(fail_after, "TEMP_MANIFEST_FSYNC")
    maybe_inject_failure(fail_after, "PREPARED")

    install_prepared_file(
        paths["profile"],
        PROFILE_PATH,
        old_profile_hash,
        journal["new_profile_sha256"],
    )
    maybe_inject_failure(fail_after, "PROFILE_INSTALL")
    update_journal_phase(journal, paths, "PROFILE_INSTALLED")
    maybe_inject_failure(fail_after, "PROFILE_INSTALLED")

    install_prepared_file(
        paths["manifest"],
        MANIFEST_PATH,
        old_manifest_hash,
        journal["new_manifest_sha256"],
    )
    maybe_inject_failure(fail_after, "MANIFEST_INSTALL")
    update_journal_phase(journal, paths, "MANIFEST_INSTALLED")
    maybe_inject_failure(fail_after, "MANIFEST_INSTALLED")

    installed = verify_installed_pair()
    if installed != profile:
        raise RuntimeError("Installed profile changed during transaction")
    if sha256(PROFILE_PATH) != journal["new_profile_sha256"]:
        raise RuntimeError("Installed profile hash differs from journal")
    if sha256(MANIFEST_PATH) != journal["new_manifest_sha256"]:
        raise RuntimeError("Installed manifest hash differs from journal")
    maybe_inject_failure(fail_after, "FINAL_VERIFICATION")

    if journal["current_durable_phase"] != "VERIFIED":
        update_journal_phase(journal, paths, "VERIFIED")
    cleanup_transaction(journal, paths)


def require_recorded_temp(
    path: Path,
    expected_hash: str,
    label: str,
) -> None:
    if not path_lexists(path):
        raise RecoveryBlocked(f"Missing recorded {label} temporary file")
    observed = sha256(path)
    if observed != expected_hash:
        raise RecoveryBlocked(
            f"Recorded {label} temporary hash mismatch: "
            f"expected={expected_hash}, observed={observed}"
        )


def recover_interrupted_transaction_locked() -> str:
    journal, paths = read_validated_journal()
    allowed_extra = {
        JOURNAL_RELATIVE_PATH,
        *(journal_relative(path) for path in paths.values()),
    }
    verify_environment()
    verify_bindings(allowed_extra)

    if sha256(GENERATOR_PATH) != journal["expected_generator_sha256"]:
        raise RecoveryBlocked("Generator changed since transaction preparation")
    if sha256(SCHEMA_PATH) != journal["expected_schema_sha256"]:
        raise RecoveryBlocked("Schema changed since transaction preparation")

    expected_profile = build_profile(journal["frozen_at"])
    validate_profile(expected_profile)
    expected_profile_bytes = render_profile_bytes(expected_profile)
    expected_manifest_bytes = render_manifest_bytes(expected_profile_bytes)
    if sha256_bytes(expected_profile_bytes) != journal["new_profile_sha256"]:
        raise RecoveryBlocked("Recorded new profile is not reconstructible")
    if sha256_bytes(expected_manifest_bytes) != journal["new_manifest_sha256"]:
        raise RecoveryBlocked("Recorded new manifest is not reconstructible")

    current_profile_hash = current_hash_or_absent(PROFILE_PATH)
    current_manifest_hash = current_hash_or_absent(MANIFEST_PATH)
    old_pair = (
        journal["old_profile_sha256"],
        journal["old_manifest_sha256"],
    )
    new_pair = (
        journal["new_profile_sha256"],
        journal["new_manifest_sha256"],
    )
    current_pair = (current_profile_hash, current_manifest_hash)

    phase = journal["current_durable_phase"]
    if current_pair == old_pair:
        if phase != "PREPARED":
            raise RecoveryBlocked(
                f"BLOCKED: old/old pair is inconsistent with phase {phase}"
            )
        if journal["operation_type"] == "REGENERATION":
            cleanup_transaction(journal, paths)
            return "installation had not begun; removed transaction-owned files"
        require_recorded_temp(
            paths["profile"],
            journal["new_profile_sha256"],
            "profile",
        )
        require_recorded_temp(
            paths["manifest"],
            journal["new_manifest_sha256"],
            "manifest",
        )
        # A partially written journal-update temp is never authoritative, but
        # preserve it until every branch-specific safety check has passed.
        remove_owned_file_if_present(paths["journal_update"])
        install_prepared_file(
            paths["profile"],
            PROFILE_PATH,
            ABSENT,
            journal["new_profile_sha256"],
        )
        update_journal_phase(journal, paths, "PROFILE_INSTALLED")
        install_prepared_file(
            paths["manifest"],
            MANIFEST_PATH,
            ABSENT,
            journal["new_manifest_sha256"],
        )
        update_journal_phase(journal, paths, "MANIFEST_INSTALLED")
    elif current_pair == (new_pair[0], old_pair[1]):
        if phase not in ("PREPARED", "PROFILE_INSTALLED"):
            raise RecoveryBlocked(
                f"BLOCKED: new/old pair is inconsistent with phase {phase}"
            )
        verify_profile_bytes(read_regular_bytes(PROFILE_PATH))
        require_recorded_temp(
            paths["manifest"],
            journal["new_manifest_sha256"],
            "manifest",
        )
        remove_owned_file_if_present(paths["journal_update"])
        if phase == "PREPARED":
            update_journal_phase(journal, paths, "PROFILE_INSTALLED")
        install_prepared_file(
            paths["manifest"],
            MANIFEST_PATH,
            old_pair[1],
            journal["new_manifest_sha256"],
        )
        update_journal_phase(journal, paths, "MANIFEST_INSTALLED")
    elif current_pair == new_pair:
        if phase not in ("PROFILE_INSTALLED", "MANIFEST_INSTALLED", "VERIFIED"):
            raise RecoveryBlocked(
                f"BLOCKED: new/new pair is inconsistent with phase {phase}"
            )
        verify_installed_pair()
        remove_owned_file_if_present(paths["journal_update"])
        if phase == "PROFILE_INSTALLED":
            update_journal_phase(journal, paths, "MANIFEST_INSTALLED")
    else:
        raise RecoveryBlocked(
            "BLOCKED: current profile/manifest hashes are not an allowed "
            f"old/new combination: current={current_pair!r}, "
            f"old={old_pair!r}, new={new_pair!r}"
        )

    verify_installed_pair()
    if sha256(PROFILE_PATH) != journal["new_profile_sha256"]:
        raise RecoveryBlocked("Recovered profile hash mismatch")
    if sha256(MANIFEST_PATH) != journal["new_manifest_sha256"]:
        raise RecoveryBlocked("Recovered manifest hash mismatch")
    update_journal_phase(journal, paths, "VERIFIED")
    cleanup_transaction(journal, paths)
    return "recovered and verified exact canonical profile/manifest pair"


def exact_check_locked() -> dict:
    ensure_no_transaction_journal()
    verify_environment()
    verify_bindings()
    profile = verify_installed_pair()
    print(f"OK: {PROFILE_PATH}")
    print(f"Profile SHA256: {sha256(PROFILE_PATH)}")
    print(f"Schema SHA256: {sha256(SCHEMA_PATH)}")
    print(f"Manifest SHA256: {sha256(MANIFEST_PATH)}")
    return profile


def regenerate_locked() -> None:
    ensure_no_transaction_journal()
    verify_environment()
    verify_bindings()
    existing_bytes = read_regular_bytes(PROFILE_PATH)
    existing = parse_profile_bytes(existing_bytes)
    if existing.get("frozen_at") != FROZEN_AT:
        raise RuntimeError("Refusing to change the frozen profile timestamp")

    expected = build_profile(existing["frozen_at"])
    expected_semantics = semantic_without_frozen_at(expected)
    existing_semantics = semantic_without_frozen_at(existing)
    if existing_semantics == expected_semantics:
        pass
    else:
        previous = profile_before_durability_protocols(expected)
        legacy = legacy_profile_before_runtime_separation(expected)
        if existing_semantics == semantic_without_frozen_at(previous):
            if sha256_bytes(existing_bytes) != PREVIOUS_PROFILE_SHA256:
                raise RuntimeError(
                    "Immediately previous profile bytes do not match the "
                    "recorded migration source hash"
                )
        elif existing_semantics != semantic_without_frozen_at(legacy):
            raise RuntimeError(
                "Refusing regeneration because existing profile semantics "
                "differ beyond explicitly supported migrations"
            )

    validate_profile(expected)
    expected_profile_bytes = render_profile_bytes(expected)
    expected_manifest_bytes = render_manifest_bytes(expected_profile_bytes)
    if (
        path_lexists(MANIFEST_PATH)
        and existing_bytes == expected_profile_bytes
        and read_regular_bytes(MANIFEST_PATH) == expected_manifest_bytes
    ):
        verify_installed_pair()
        print(f"Profile and manifest already canonical: {PROFILE_PATH}")
        return

    install_profile_transaction(expected, "REGENERATION")
    print(f"Regenerated canonical profile and manifest: {PROFILE_PATH}")


def initial_creation_locked() -> None:
    ensure_no_transaction_journal()
    verify_environment()
    verify_bindings()
    if path_lexists(PROFILE_PATH) or path_lexists(MANIFEST_PATH):
        raise FileExistsError(
            "Profile or manifest already exists; use --check or "
            "--regenerate-profile"
        )
    frozen_at = datetime.now(UTC).isoformat(timespec="microseconds")
    profile = build_profile(frozen_at)
    install_profile_transaction(profile, "INITIAL_CREATION")
    print(f"Wrote canonical profile and manifest: {PROFILE_PATH}")


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--regenerate-profile", action="store_true")
    mode.add_argument(
        "--recover-interrupted-regeneration",
        action="store_true",
    )
    args = parser.parse_args()

    with repository_lock():
        if args.recover_interrupted_regeneration:
            result = recover_interrupted_transaction_locked()
            print(result)
            return 0
        ensure_no_transaction_journal()
        if args.check:
            exact_check_locked()
            return 0
        if args.regenerate_profile:
            regenerate_locked()
            return 0
        initial_creation_locked()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())

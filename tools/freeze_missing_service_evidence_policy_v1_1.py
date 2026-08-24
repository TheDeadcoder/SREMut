#!/usr/bin/env python3
"""Generate the provenance-locked evidence-capture policy v1.1 bundle.

Version 1.1 is a complete derivative of the immutable v1 tagged bundle.  Its
only semantic changes select canonical WorkloadEntry JSONL as the sole raw
workload encoding and bind workload evidence to a deterministic stream ID.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import stat
import struct
import subprocess
import sys
from typing import Any, Callable

from jsonschema import Draft202012Validator
import yaml


ROOT = Path(__file__).resolve().parents[1]
GENERATOR = Path(__file__).resolve()
POLICY = ROOT / "policies/missing_service_social_network/evidence-capture-v1.1.yaml"
SCHEMA = ROOT / "schemas/evidence-capture-policy-v1.1.schema.json"
MANIFEST = ROOT / "EVIDENCE_CAPTURE_POLICY_V1_1_SHA256SUMS"
BASE_GENERATOR = ROOT / "tools/freeze_missing_service_evidence_policy.py"
BASE_POLICY = ROOT / "policies/missing_service_social_network/evidence-capture-v1.yaml"
BASE_SCHEMA = ROOT / "schemas/evidence-capture-policy-v1.schema.json"
BASE_MANIFEST = ROOT / "EVIDENCE_CAPTURE_POLICY_V1_SHA256SUMS"

GENERATOR_REL = GENERATOR.relative_to(ROOT).as_posix()
POLICY_REL = POLICY.relative_to(ROOT).as_posix()
SCHEMA_REL = SCHEMA.relative_to(ROOT).as_posix()
MANIFEST_REL = MANIFEST.relative_to(ROOT).as_posix()
OWN_PATHS = frozenset({GENERATOR_REL, POLICY_REL, SCHEMA_REL, MANIFEST_REL})

BASE_TAG = "sremut-missing-service-evidence-policy-v1"
BASE_TAG_OBJECT = "40e50e4f6fcaff150a84137ed099a220441e4258"
BASE_COMMIT = "c2f500c9ec6ed46b4f62d989c6c7d2b6743d9471"
BASE_TREE = "11acd566368117d8a337641a949c14b86495a230"
BASE_MANIFEST_SHA256 = "7b99a435afbd5b8d692fa6654997a06baabd51209176b13103ecb68be21eee3d"
BASE_GENERATOR_SHA256 = "37b0cc9de07b476b0c5d16e3ec411c9c944e41b5192ff46af0a00311281d4809"
BASE_POLICY_SHA256 = "52d46f04c24e4b80e91c44acd9cf15c3e06453047f410cd549b7eec9acea0d09"
BASE_SCHEMA_SHA256 = "a0cfc61026a173089c3ebfc4aa3b4bc26fb0189852231deb7b2ec4f3f5c85244"
BASE_FROZEN_AT = "2026-08-20T10:53:22.794810+00:00"

POLICY_ID = "sremut/missing-service-social-network/evidence-capture-v1.1"
SEMANTIC_VERSION = "1.1"
FROZEN_AT = "2026-08-24T16:04:28.962775+00:00"
TAG_NAME = "sremut-missing-service-evidence-policy-v1.1"
EXECUTION_PROFILE_TAG_OBJECT = "7c6493eb7dce68370fd0d5be572edd968654a1d6"
STREAM_SOURCE = "StreamWorkloadManager.log_history"
MANAGER_INSTANCE_ORDINAL = 1
SHA256_PATTERN = r"^[0-9a-f]{64}$"
TIME_PATTERN = re.compile(r"^[0-9a-f]{16}$")
RUN_PATTERN = re.compile(r"^sremut-ms-(m01|m02|m03)-r0[1-3]-a0[1-2]-[0-9a-f]{12}$")
ATTEMPT_PATTERN = re.compile(r"^a0[1-2]$")

BASE_ARTIFACTS = {
    "EVIDENCE_CAPTURE_POLICY_V1_SHA256SUMS": BASE_MANIFEST_SHA256,
    "tools/freeze_missing_service_evidence_policy.py": BASE_GENERATOR_SHA256,
    "policies/missing_service_social_network/evidence-capture-v1.yaml": BASE_POLICY_SHA256,
    "schemas/evidence-capture-policy-v1.schema.json": BASE_SCHEMA_SHA256,
}
POLICY_ALLOWED_PREFIXES = (
    "document_type",
    "semantic_version",
    "policy_id",
    "status",
    "frozen_at",
    "future_annotated_tag",
    "base_policy_provenance",
    "supersession",
    "correction_reason",
    "semantic_diff_report",
    "authenticated_policy_input.expected_entry_paths",
    "authenticated_policy_input.required_exact_bytes",
    "roles.workload_log_bytes.required_metadata",
    "roles.workload_boundary.required_metadata",
    "roles.workload_parse_result.required_metadata",
    "workload_evidence_protocol.raw_log_bytes_serialization",
    "workload_evidence_protocol.authoritative_raw_representation",
    "workload_evidence_protocol.joined_entry_log_compatibility_mode",
    "workload_evidence_protocol.canonical_jsonl_record",
    "workload_evidence_protocol.window_identity.required_fields",
    "workload_evidence_protocol.window_identity.descriptor_required_fields",
    "workload_evidence_protocol.window_identity.adjudication_required_fields",
    "workload_parse_result_protocol.raw_serialization",
    "workload_parse_result_protocol.stream_identity_required",
    "workload_stream_identity_protocol",
    "full_admissibility_validation.failure_codes",
    "full_admissibility_validation.hook_contracts[4]",
    "full_admissibility_validation.hook_contracts[5]",
)
SCHEMA_ALLOWED_PREFIXES = (
    "$id",
    "title",
    "$defs.evidence_policy_document",
    "$defs.payload_descriptor_workload_log_bytes",
    "$defs.descriptor_descriptor_workload_boundary",
    "$defs.payload_descriptor_workload_parse_result",
    "$defs.descriptor_descriptor_adjudication",
    "$defs.workload_window_descriptor_identity",
    "$defs.workload_window_identity",
    "$defs.workload_parse_result_payload",
    "$defs.workload_entry_jsonl_record_v1_1",
    "$defs.authenticated_policy_input",
    "$defs.offline_seal_input",
    "oneOf",
    "x-evidence-policy-semantic-version",
)


class FreezeError(RuntimeError):
    pass


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_path(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def canonical_json_bytes(value: Any) -> bytes:
    reject_floats(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def reject_floats(value: Any) -> None:
    if isinstance(value, float):
        raise FreezeError("FLOAT_FORBIDDEN")
    if isinstance(value, dict):
        if any(type(key) is not str for key in value):
            raise FreezeError("NONSTRING_KEY")
        for child in value.values():
            reject_floats(child)
    elif isinstance(value, list):
        for child in value:
            reject_floats(child)


def run_git(*arguments: str, input_bytes: bytes | None = None) -> bytes:
    completed = subprocess.run(
        ["/usr/bin/git", "-C", str(ROOT), *arguments],
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        check=False,
        timeout=20,
        env={"LC_ALL": "C.UTF-8", "PATH": "/usr/bin:/bin"},
    )
    if completed.returncode != 0:
        raise FreezeError("GIT_PROVENANCE_FAILURE")
    return completed.stdout


def verify_repository_state() -> None:
    if run_git("rev-parse", f"refs/tags/{BASE_TAG}").strip().decode() != BASE_TAG_OBJECT:
        raise FreezeError("BASE_TAG_OBJECT_MISMATCH")
    if run_git("rev-parse", f"refs/tags/{BASE_TAG}^{{}}").strip().decode() != BASE_COMMIT:
        raise FreezeError("BASE_TAG_TARGET_MISMATCH")
    if run_git("rev-parse", f"refs/tags/{BASE_TAG}^{{}}^{{tree}}").strip().decode() != BASE_TREE:
        raise FreezeError("BASE_TAG_TREE_MISMATCH")
    if subprocess.run(
        ["/usr/bin/git", "-C", str(ROOT), "merge-base", "--is-ancestor", BASE_COMMIT, "HEAD"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        shell=False,
        timeout=20,
    ).returncode != 0:
        raise FreezeError("CURRENT_HEAD_NOT_BASE_DESCENDANT")
    for line in run_git("status", "--porcelain=v1", "--untracked-files=all").decode().splitlines():
        if len(line) < 4 or line[3:] not in OWN_PATHS:
            raise FreezeError("UNAUTHORIZED_DIRTY_PATH")


def verify_base_artifacts() -> None:
    for relative, expected in BASE_ARTIFACTS.items():
        path = ROOT / relative
        if path.is_symlink() or not path.is_file() or sha256_path(path) != expected:
            raise FreezeError("BASE_ARTIFACT_MISMATCH")
        tagged = run_git("show", f"{BASE_COMMIT}:{relative}")
        if tagged != path.read_bytes() or sha256_bytes(tagged) != expected:
            raise FreezeError("BASE_TAGGED_BLOB_MISMATCH")
    rows = parse_manifest(BASE_MANIFEST.read_bytes())
    expected_rows = {
        "tools/freeze_missing_service_evidence_policy.py": BASE_GENERATOR_SHA256,
        "policies/missing_service_social_network/evidence-capture-v1.yaml": BASE_POLICY_SHA256,
        "schemas/evidence-capture-policy-v1.schema.json": BASE_SCHEMA_SHA256,
    }
    if rows != expected_rows:
        raise FreezeError("BASE_MANIFEST_MISMATCH")


def parse_manifest(data: bytes) -> dict[str, str]:
    if not data or not data.endswith(b"\n"):
        raise FreezeError("MANIFEST_INVALID")
    rows: dict[str, str] = {}
    for line in data.decode("ascii").splitlines():
        parts = line.split("  ")
        if len(parts) != 2 or not re.fullmatch(SHA256_PATTERN, parts[0]):
            raise FreezeError("MANIFEST_INVALID")
        path = parts[1]
        if not path or path.startswith("/") or any(part in ("", ".", "..") for part in path.split("/")):
            raise FreezeError("MANIFEST_INVALID")
        if path in rows:
            raise FreezeError("MANIFEST_INVALID")
        rows[path] = parts[0]
    return rows


def load_base_module() -> Any:
    spec = importlib.util.spec_from_file_location("sremut_frozen_evidence_policy_v1", BASE_GENERATOR)
    if spec is None or spec.loader is None:
        raise FreezeError("BASE_GENERATOR_IMPORT_FAILED")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def reconstruct_v1() -> tuple[Any, dict[str, Any], dict[str, Any]]:
    base = load_base_module()
    contract = yaml.safe_load((ROOT / "contracts/missing_service_social_network.yaml").read_bytes())
    profile = yaml.safe_load((ROOT / "profiles/missing_service_social_network/pilot-v1.yaml").read_bytes())
    policy = base.build_policy(BASE_FROZEN_AT, contract, profile)
    schema = base.build_schema(policy)
    if base.render_policy(policy) != BASE_POLICY.read_bytes():
        raise FreezeError("BASE_POLICY_RECONSTRUCTION_MISMATCH")
    if base.render_schema(schema) != BASE_SCHEMA.read_bytes():
        raise FreezeError("BASE_SCHEMA_RECONSTRUCTION_MISMATCH")
    return base, policy, schema


def append_once(values: list[Any], value: Any) -> None:
    if value not in values:
        values.append(value)


def stream_identity(run_id: Any, attempt_id: Any, *, policy_id: str = POLICY_ID, source: str = STREAM_SOURCE, ordinal: Any = MANAGER_INSTANCE_ORDINAL) -> str:
    if type(run_id) is not str or RUN_PATTERN.fullmatch(run_id) is None:
        raise FreezeError("STREAM_IDENTITY_INPUT_INVALID")
    if type(attempt_id) is not str or ATTEMPT_PATTERN.fullmatch(attempt_id) is None:
        raise FreezeError("STREAM_IDENTITY_INPUT_INVALID")
    if policy_id != POLICY_ID or source != STREAM_SOURCE or type(ordinal) is not int or ordinal != 1:
        raise FreezeError("STREAM_IDENTITY_INPUT_INVALID")
    material = {
        "schema_version": 1,
        "execution_profile_tag_object": EXECUTION_PROFILE_TAG_OBJECT,
        "evidence_policy_id": POLICY_ID,
        "run_id": run_id,
        "attempt_id": attempt_id,
        "source": STREAM_SOURCE,
        "manager_instance_ordinal": 1,
    }
    return sha256_bytes(canonical_json_bytes(material))


def workload_record(index: Any, time_value: Any, number: Any, log: Any, ok: Any) -> dict[str, Any]:
    if type(index) is not int or index < 0 or type(number) is not int or number < 0:
        raise FreezeError("WORKLOAD_RECORD_INVALID")
    if type(time_value) is not float or not math.isfinite(time_value):
        raise FreezeError("WORKLOAD_RECORD_INVALID")
    if type(log) is not str or type(ok) is not bool:
        raise FreezeError("WORKLOAD_RECORD_INVALID")
    log.encode("utf-8", errors="strict")
    return {
        "index": index,
        "time_binary64_hex": struct.pack(">d", time_value).hex(),
        "number": number,
        "log": log,
        "ok": ok,
    }


def serialize_workload_records(records: list[dict[str, Any]]) -> bytes:
    return b"".join(canonical_json_bytes(record) + b"\n" for record in records)


def validate_workload_jsonl(data: Any) -> list[dict[str, Any]]:
    if type(data) is not bytes or not data or not data.endswith(b"\n"):
        raise FreezeError("WORKLOAD_JSONL_INVALID")
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeError as error:
        raise FreezeError("WORKLOAD_JSONL_INVALID") from error
    records: list[dict[str, Any]] = []
    for raw_line in text.splitlines(keepends=True):
        if raw_line == "\n" or not raw_line.endswith("\n"):
            raise FreezeError("WORKLOAD_JSONL_INVALID")
        try:
            value = json.loads(raw_line[:-1])
        except Exception as error:
            raise FreezeError("WORKLOAD_JSONL_INVALID") from error
        if type(value) is not dict or set(value) != {"index", "time_binary64_hex", "number", "log", "ok"}:
            raise FreezeError("WORKLOAD_JSONL_INVALID")
        if canonical_json_bytes(value) + b"\n" != raw_line.encode("utf-8"):
            raise FreezeError("WORKLOAD_JSONL_INVALID")
        if type(value["index"]) is not int or value["index"] < 0 or type(value["number"]) is not int or value["number"] < 0:
            raise FreezeError("WORKLOAD_JSONL_INVALID")
        if type(value["log"]) is not str or type(value["ok"]) is not bool:
            raise FreezeError("WORKLOAD_JSONL_INVALID")
        identity = value["time_binary64_hex"]
        if type(identity) is not str or TIME_PATTERN.fullmatch(identity) is None:
            raise FreezeError("WORKLOAD_JSONL_INVALID")
        if not math.isfinite(struct.unpack(">d", bytes.fromhex(identity))[0]):
            raise FreezeError("WORKLOAD_JSONL_INVALID")
        records.append(value)
    if [record["index"] for record in records] != list(range(len(records))):
        raise FreezeError("WORKLOAD_JSONL_INVALID")
    return records


def object_diff(left: Any, right: Any, path: str = "") -> list[str]:
    if type(left) is not type(right):
        return [path or "$"]
    if isinstance(left, dict):
        changed: list[str] = []
        for key in sorted(set(left) | set(right)):
            child = f"{path}.{key}" if path else key
            if key not in left or key not in right:
                changed.append(child)
            else:
                changed.extend(object_diff(left[key], right[key], child))
        return changed
    if isinstance(left, list):
        changed = []
        for index in range(max(len(left), len(right))):
            child = f"{path}[{index}]"
            if index >= len(left) or index >= len(right):
                changed.append(child)
            else:
                changed.extend(object_diff(left[index], right[index], child))
        return changed
    return [] if left == right else [path or "$"]


def path_allowed(path: str, prefixes: tuple[str, ...]) -> bool:
    return any(path == prefix or path.startswith(prefix + ".") or path.startswith(prefix + "[") for prefix in prefixes)


def assert_allowed_diff(left: Any, right: Any, prefixes: tuple[str, ...], label: str) -> list[str]:
    changed = object_diff(left, right)
    forbidden = [path for path in changed if not path_allowed(path, prefixes)]
    if forbidden:
        raise FreezeError(f"UNAUTHORIZED_{label}_SEMANTIC_DRIFT:{forbidden[0]}")
    return changed


def build_policy(base_policy: dict[str, Any]) -> dict[str, Any]:
    policy = deepcopy(base_policy)
    policy["document_type"] = "EVIDENCE_POLICY_DOCUMENT_V1_1"
    policy["semantic_version"] = SEMANTIC_VERSION
    policy["policy_id"] = POLICY_ID
    policy["status"] = "FROZEN_BEFORE_MUTANT_EXECUTION"
    policy["frozen_at"] = FROZEN_AT
    policy["future_annotated_tag"] = TAG_NAME
    policy["base_policy_provenance"] = {
        "tag": BASE_TAG,
        "tag_object": BASE_TAG_OBJECT,
        "peeled_commit": BASE_COMMIT,
        "tree": BASE_TREE,
        "manifest_sha256": BASE_MANIFEST_SHA256,
        "generator_sha256": BASE_GENERATOR_SHA256,
        "policy_sha256": BASE_POLICY_SHA256,
        "schema_sha256": BASE_SCHEMA_SHA256,
    }
    policy["supersession"] = {
        "supersedes": "sremut/missing-service-social-network/evidence-capture-v1",
        "scope": "ALL_FUTURE_MUTANT_EXECUTION",
        "base_v1_historical_scope": "PRE_EXPERIMENT_DESIGN_WORK_ONLY",
        "mutant_results_produced_under_v1": False,
    }
    policy["correction_reason"] = [
        "CONTRADICTORY_WORKLOAD_BYTE_ENCODINGS",
        "MISSING_AUTHENTICATED_STREAM_IDENTITY",
    ]
    policy["authenticated_policy_input"]["expected_entry_paths"] = [GENERATOR_REL, POLICY_REL, SCHEMA_REL]
    policy["authenticated_policy_input"]["required_exact_bytes"] = [POLICY_REL, SCHEMA_REL, MANIFEST_REL]

    workload = policy["workload_evidence_protocol"]
    workload["raw_log_bytes_serialization"] = (
        "b''.join(canonical_json_bytes({index,time_binary64_hex,number,log,ok}) + b'\\n' "
        "for each StreamWorkloadManager.log_history entry in exact order)"
    )
    workload["authoritative_raw_representation"] = "CANONICAL_WORKLOAD_ENTRY_JSONL_V1_1"
    workload["joined_entry_log_compatibility_mode"] = False
    workload["canonical_jsonl_record"] = {
        "fields_exactly": ["index", "time_binary64_hex", "number", "log", "ok"],
        "one_record_per_complete_log_history_entry": True,
        "canonical_compact_sorted_utf8_json": True,
        "exactly_one_final_lf_per_record": True,
        "blank_records_forbidden": True,
        "float_json_values_forbidden": True,
        "time_identity": "struct.pack('>d', entry.time).hex()",
        "finite_binary64_required": True,
        "embedded_log_newlines": "JSON_ESCAPED_AND_ROUND_TRIP_EXACT",
        "payload_sha256_domain": "EXACT_CANONICAL_JSONL_BYTES",
        "meaning": "canonical serialized snapshot of StreamWorkloadManager.log_history",
    }
    for field_name in ("required_fields", "descriptor_required_fields", "adjudication_required_fields"):
        append_once(workload["window_identity"][field_name], "stream_identity")
    for role_name in ("workload_log_bytes", "workload_boundary", "workload_parse_result"):
        append_once(policy["roles"][role_name]["required_metadata"], "stream_identity")
    policy["workload_parse_result_protocol"]["raw_serialization"] = (
        "sole authoritative workload_log_bytes encoding: one exact canonical compact JSON WorkloadEntry object plus LF per entry"
    )
    policy["workload_parse_result_protocol"]["stream_identity_required"] = True
    policy["workload_stream_identity_protocol"] = {
        "algorithm": "SHA-256_OF_CANONICAL_COMPACT_SORTED_UTF8_JSON_V1",
        "result_grammar": SHA256_PATTERN,
        "schema_version": 1,
        "execution_profile_tag_object": EXECUTION_PROFILE_TAG_OBJECT,
        "evidence_policy_id": POLICY_ID,
        "source": STREAM_SOURCE,
        "manager_instance_ordinal": 1,
        "manager_instance_ordinal_type": "EXACT_INTEGER_NOT_BOOLEAN",
        "exactly_one_manager_instance_per_attempt": True,
        "one_identity_for_all_three_windows_per_attempt": True,
        "recomputed_not_caller_trusted": True,
        "hash_material_fields_exactly": [
            "schema_version",
            "execution_profile_tag_object",
            "evidence_policy_id",
            "run_id",
            "attempt_id",
            "source",
            "manager_instance_ordinal",
        ],
        "substitution_rejections": ["CROSS_RUN", "CROSS_ATTEMPT", "CROSS_POLICY", "CROSS_SOURCE", "WRONG_MANAGER_ORDINAL"],
        "required_equal_locations": [
            "workload_log_bytes.stream_identity",
            "workload_boundary.stream_identity",
            "workload_parse_result.stream_identity",
            "workload_window.stream_identity",
            "workload_window_adjudication_identity.stream_identity",
        ],
        "threat_model_claim": "AUTHENTICATED_SEALED_EVIDENCE_IDENTIFIER_WITHIN_CAPTURE_TIME_TCB_NOT_MALICIOUS_RUNNER_RESISTANCE",
    }
    append_once(policy["full_admissibility_validation"]["failure_codes"], "WORKLOAD_STREAM_IDENTITY_MISMATCH")
    for hook in policy["full_admissibility_validation"]["hook_contracts"]:
        if hook["hook_id"] in ("VALIDATE_WORKLOAD_CARDINALITY_V1", "VALIDATE_WORKLOAD_WINDOW_CONSISTENCY_V1"):
            append_once(hook["required_candidate_fields"], "recomputed stream_identity")
            append_once(hook["required_policy_sections"], "workload_stream_identity_protocol")
            append_once(hook["failure_codes"], "WORKLOAD_STREAM_IDENTITY_MISMATCH")
    changed = assert_allowed_diff(base_policy, policy, POLICY_ALLOWED_PREFIXES, "POLICY")
    policy["semantic_diff_report"] = {
        "base_policy_id": base_policy["policy_id"],
        "target_policy_id": POLICY_ID,
        "allowlisted_policy_prefixes": list(POLICY_ALLOWED_PREFIXES),
        "allowlisted_schema_prefixes": list(SCHEMA_ALLOWED_PREFIXES),
        "observed_policy_leaf_paths_before_report": changed,
        "all_unlisted_policy_and_schema_values_must_equal_v1": True,
    }
    assert_allowed_diff(base_policy, policy, POLICY_ALLOWED_PREFIXES, "POLICY")
    return policy


def add_required_property(schema: dict[str, Any], definition: str, name: str, value_schema: dict[str, Any]) -> None:
    branch = schema["$defs"][definition]
    branch["properties"][name] = deepcopy(value_schema)
    append_once(branch["required"], name)


def build_schema(base: Any, policy: dict[str, Any], base_schema: dict[str, Any]) -> dict[str, Any]:
    schema = base.build_schema(policy)
    schema["$id"] = "https://sremut.local/schemas/evidence-capture-policy-v1.1.schema.json"
    schema["title"] = "SREMut Evidence Capture Policy v1.1"
    hash_schema = {"type": "string", "pattern": SHA256_PATTERN}
    for definition in (
        "payload_descriptor_workload_log_bytes",
        "descriptor_descriptor_workload_boundary",
        "payload_descriptor_workload_parse_result",
    ):
        add_required_property(schema, definition, "stream_identity", hash_schema)
    for definition in ("workload_window_descriptor_identity", "workload_window_identity"):
        add_required_property(schema, definition, "stream_identity", hash_schema)
    add_required_property(schema, "workload_parse_result_payload", "stream_identity", hash_schema)
    schema["$defs"]["workload_entry_jsonl_record_v1_1"] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "index": {"type": "integer", "minimum": 0},
            "time_binary64_hex": {"type": "string", "pattern": r"^[0-9a-f]{16}$"},
            "number": {"type": "integer", "minimum": 0},
            "log": {"type": "string"},
            "ok": {"type": "boolean"},
        },
        "required": ["index", "time_binary64_hex", "number", "log", "ok"],
    }
    schema["oneOf"].append({"$ref": "#/$defs/workload_entry_jsonl_record_v1_1"})
    auth = schema["$defs"]["authenticated_policy_input"]["properties"]
    auth["expected_policy_relative_path"] = {"type": "string", "const": POLICY_REL}
    auth["expected_schema_relative_path"] = {"type": "string", "const": SCHEMA_REL}
    auth["expected_generator_relative_path"] = {"type": "string", "const": GENERATOR_REL}
    offline = schema["$defs"]["offline_seal_input"]
    release = offline["properties"]["runner_release_binding"]["properties"]
    release["evidence_policy_annotated_tag"] = {"type": "string", "const": TAG_NAME}
    schema["x-evidence-policy-semantic-version"] = SEMANTIC_VERSION
    base.assert_closed_object_schemas(schema)
    Draft202012Validator.check_schema(schema)
    base.assert_all_defs_reachable(schema)
    assert_allowed_diff(base_schema, schema, SCHEMA_ALLOWED_PREFIXES, "SCHEMA")
    Draft202012Validator(schema).validate(policy)
    return schema


def validate_stream_bundle(bundle: dict[str, Any]) -> None:
    if set(bundle) != {"run_id", "attempt_id", "windows", "roles"}:
        raise FreezeError("STREAM_BUNDLE_INVALID")
    expected = stream_identity(bundle["run_id"], bundle["attempt_id"])
    if type(bundle["windows"]) is not list or len(bundle["windows"]) != 3:
        raise FreezeError("STREAM_BUNDLE_INVALID")
    expected_windows = [
        ("INITIAL_MUTANT_CHALLENGE", 1),
        ("POST_REPLACEMENT_PERSISTENCE", 2),
        ("RESTORATION_POSITIVE_CONTROL", 3),
    ]
    for row, identity in zip(bundle["windows"], expected_windows):
        if type(row) is not dict or set(row) != {"phase", "ordinal", "stream_identity"}:
            raise FreezeError("STREAM_BUNDLE_INVALID")
        if (row["phase"], row["ordinal"]) != identity or row["stream_identity"] != expected:
            raise FreezeError("WORKLOAD_STREAM_IDENTITY_MISMATCH")
    if type(bundle["roles"]) is not dict or set(bundle["roles"]) != {"workload_log_bytes", "workload_boundary", "workload_parse_result", "adjudication"}:
        raise FreezeError("STREAM_BUNDLE_INVALID")
    if any(type(value) is not str or not re.fullmatch(SHA256_PATTERN, value) or value != expected for value in bundle["roles"].values()):
        raise FreezeError("WORKLOAD_STREAM_IDENTITY_MISMATCH")


def expect_failure(function: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
    try:
        function(*args, **kwargs)
    except Exception:
        return
    raise FreezeError("NEGATIVE_TEST_ACCEPTED")


def run_self_tests(policy: dict[str, Any], schema: dict[str, Any], base_policy: dict[str, Any], base_schema: dict[str, Any]) -> tuple[int, int]:
    run_id = "sremut-ms-m01-r01-a01-abcdef123456"
    attempt_id = "a01"
    identity = stream_identity(run_id, attempt_id)
    first = workload_record(0, 1.0, 1, "line one\nline two", True)
    second = workload_record(1, 2.0, 50, "complete", True)
    raw = serialize_workload_records([first, second])
    records = validate_workload_jsonl(raw)
    positives = 0
    if records[0] == first:
        positives += 1
    if records[0]["log"] == "line one\nline two":
        positives += 1
    if [row["index"] for row in records] == [0, 1]:
        positives += 1
    bundle = {
        "run_id": run_id,
        "attempt_id": attempt_id,
        "windows": [
            {"phase": "INITIAL_MUTANT_CHALLENGE", "ordinal": 1, "stream_identity": identity},
            {"phase": "POST_REPLACEMENT_PERSISTENCE", "ordinal": 2, "stream_identity": identity},
            {"phase": "RESTORATION_POSITIVE_CONTROL", "ordinal": 3, "stream_identity": identity},
        ],
        "roles": {name: identity for name in ("workload_log_bytes", "workload_boundary", "workload_parse_result", "adjudication")},
    }
    validate_stream_bundle(bundle)
    positives += 1
    if len({row["stream_identity"] for row in bundle["windows"]}) == 1:
        positives += 1
    Draft202012Validator(schema).validate(policy)
    positives += 1

    negatives = 0
    cases: list[tuple[Callable[..., Any], tuple[Any, ...], dict[str, Any]]] = []
    cases.append((validate_workload_jsonl, (b"joined raw workload text\n",), {}))
    for mutation in (
        {key: value for key, value in first.items() if key != "ok"},
        {**first, "extra": 1},
        {**first, "time_binary64_hex": 1.0},
        {**first, "time_binary64_hex": "7ff8000000000000"},
        {**first, "time_binary64_hex": "7FF0000000000000"},
        {**first, "ok": 1},
        {**first, "index": True},
        {**first, "number": "1"},
    ):
        invalid_bytes = json.dumps(
            mutation, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8") + b"\n"
        cases.append((validate_workload_jsonl, (invalid_bytes,), {}))
    cases.extend(
        [
            (validate_workload_jsonl, (canonical_json_bytes(first),), {}),
            (validate_workload_jsonl, (canonical_json_bytes(first) + b"\n\n",), {}),
            (validate_workload_jsonl, (b"\xff\n",), {}),
            (stream_identity, (run_id, attempt_id), {"policy_id": "wrong"}),
            (stream_identity, (run_id, attempt_id), {"source": "wrong"}),
            (stream_identity, (run_id, attempt_id), {"ordinal": 2}),
            (stream_identity, (run_id, attempt_id), {"ordinal": True}),
        ]
    )
    for function, arguments, keywords in cases:
        expect_failure(function, *arguments, **keywords)
        negatives += 1
    for mutation in (
        lambda value: value["roles"].pop("workload_log_bytes"),
        lambda value: value["roles"].__setitem__("workload_boundary", "f" * 64),
        lambda value: value["windows"][0].__setitem__("stream_identity", "F" * 64),
        lambda value: value["windows"][1].__setitem__("ordinal", 1),
        lambda value: value.__setitem__("run_id", value["run_id"].replace("r01", "r02")),
        lambda value: value.__setitem__("attempt_id", "a02"),
    ):
        changed = deepcopy(bundle)
        mutation(changed)
        expect_failure(validate_stream_bundle, changed)
        negatives += 1
    changed_policy = deepcopy(policy)
    changed_policy["storage_model"]["terminal_manifest"]["unexpected"] = True
    expect_failure(assert_allowed_diff, base_policy, changed_policy, POLICY_ALLOWED_PREFIXES, "POLICY")
    negatives += 1
    assert_allowed_diff(base_policy, policy, POLICY_ALLOWED_PREFIXES, "POLICY")
    assert_allowed_diff(base_schema, schema, SCHEMA_ALLOWED_PREFIXES, "SCHEMA")
    return positives, negatives


def render_policy(policy: dict[str, Any]) -> bytes:
    return yaml.safe_dump(policy, sort_keys=False, allow_unicode=True, width=100).encode("utf-8")


def render_schema(schema: dict[str, Any]) -> bytes:
    return (json.dumps(schema, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def render_manifest(policy_bytes: bytes, schema_bytes: bytes) -> bytes:
    rows = (
        (sha256_path(GENERATOR), GENERATOR_REL),
        (sha256_bytes(policy_bytes), POLICY_REL),
        (sha256_bytes(schema_bytes), SCHEMA_REL),
    )
    return "".join(f"{digest}  {path}\n" for digest, path in rows).encode("ascii")


def expected_artifacts() -> tuple[dict[str, Any], dict[str, Any], dict[Path, bytes], tuple[int, int]]:
    verify_repository_state()
    verify_base_artifacts()
    base, base_policy, base_schema = reconstruct_v1()
    policy = build_policy(base_policy)
    schema = build_schema(base, policy, base_schema)
    counts = run_self_tests(policy, schema, base_policy, base_schema)
    policy_bytes = render_policy(policy)
    schema_bytes = render_schema(schema)
    return policy, schema, {POLICY: policy_bytes, SCHEMA: schema_bytes, MANIFEST: render_manifest(policy_bytes, schema_bytes)}, counts


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise FreezeError("SYMLINK_DESTINATION_FORBIDDEN")
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    if temporary.exists() or temporary.is_symlink():
        raise FreezeError("TEMPORARY_PATH_EXISTS")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise FreezeError("SHORT_WRITE")
            view = view[written:]
        os.fchmod(descriptor, 0o644)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary, path)
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def generate() -> None:
    if any(path.exists() or path.is_symlink() for path in (POLICY, SCHEMA, MANIFEST)):
        raise FreezeError("V1_1_ARTIFACT_ALREADY_EXISTS")
    _policy, _schema, artifacts, counts = expected_artifacts()
    for path in (POLICY, SCHEMA, MANIFEST):
        atomic_write(path, artifacts[path])
    print(f"V1_1_SCHEMA_POSITIVE={counts[0]}/6")
    print(f"V1_1_SCHEMA_NEGATIVE={counts[1]}/{counts[1]}")


def check() -> None:
    _policy, _schema, artifacts, counts = expected_artifacts()
    for path, expected in artifacts.items():
        if (
            path.is_symlink()
            or not path.is_file()
            or stat.S_IMODE(path.stat().st_mode) != 0o644
            or path.read_bytes() != expected
        ):
            raise FreezeError("V1_1_ARTIFACT_MISMATCH")
    rows = parse_manifest(MANIFEST.read_bytes())
    if rows != {
        GENERATOR_REL: sha256_path(GENERATOR),
        POLICY_REL: sha256_path(POLICY),
        SCHEMA_REL: sha256_path(SCHEMA),
    }:
        raise FreezeError("V1_1_MANIFEST_MISMATCH")
    print(f"V1_1_SCHEMA_POSITIVE={counts[0]}/6")
    print(f"V1_1_SCHEMA_NEGATIVE={counts[1]}/{counts[1]}")
    print("V1_1_CHECK=PASS")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    arguments = parser.parse_args()
    if arguments.check:
        check()
    else:
        generate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

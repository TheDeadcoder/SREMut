"""Authenticated, immutable runtime view of evidence-policy v1."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any, Mapping, NoReturn

from jsonschema import Draft202012Validator
import yaml

from sremut.canonical_json import canonical_json_bytes, validate_canonical_value


POLICY_MANIFEST_SHA256 = "7b99a435afbd5b8d692fa6654997a06baabd51209176b13103ecb68be21eee3d"
POLICY_RELATIVE_PATH = "policies/missing_service_social_network/evidence-capture-v1.yaml"
SCHEMA_RELATIVE_PATH = "schemas/evidence-capture-policy-v1.schema.json"
GENERATOR_RELATIVE_PATH = "tools/freeze_missing_service_evidence_policy.py"
EXPECTED_MANIFEST_PATHS = (
    GENERATOR_RELATIVE_PATH,
    POLICY_RELATIVE_PATH,
    SCHEMA_RELATIVE_PATH,
)
EXPECTED_HOOK_ORDER = (
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
EXPECTED_ROLE_SECTION_SHA256 = "e70e40d770b9c1aa6a8f207ba36b6e94f9e5d6e3a8daa9b71f37b872ea153370"
EXPECTED_HOOK_SECTION_SHA256 = "9ea177d3235971bf3bfb895abf2cbe86bcb5b2785acd34091a4cbcb80755a72d"
EXPECTED_MATRIX_SECTION_SHA256 = "1bb914a14a3d6fd2b63aa54d4ee429e368b23737d03caf7533920b0e3bacf014"
EXPECTED_STATE_SECTION_SHA256 = "c5d01548d46d416167a3adaff5cdd19a5e33e325464fd3597bf61d934b8e7487"
EXPECTED_SENSITIVE_SECTION_SHA256 = "0a909390e35eb5b2daefe6379f23b7a9e10874c0167266112d8f82cbd8198feb"

_MANIFEST_LINE = re.compile(rb"([0-9a-f]{64})  ([A-Za-z0-9._/-]+)\n")


@dataclass(slots=True)
class PolicyRuntimeError(ValueError):
    """Stable fail-closed policy-runtime failure."""

    code: str

    def __str__(self) -> str:
        return self.code


def _reject(code: str) -> NoReturn:
    raise PolicyRuntimeError(code) from None


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _section_sha256(value: Any) -> str:
    return _sha256(canonical_json_bytes(value))


def _safe_manifest_path(value: str) -> bool:
    if not value or value.startswith("/") or "\\" in value:
        return False
    parts = value.split("/")
    return all(part not in ("", ".", "..") for part in parts)


def _parse_manifest(data: bytes) -> Mapping[str, str]:
    if not data or not data.endswith(b"\n"):
        _reject("POLICY_MANIFEST_INVALID")
    rows: list[tuple[str, str]] = []
    offset = 0
    for line in data.splitlines(keepends=True):
        match = _MANIFEST_LINE.fullmatch(line)
        if match is None:
            _reject("POLICY_MANIFEST_INVALID")
        digest = match.group(1).decode("ascii")
        path = match.group(2).decode("ascii")
        if not _safe_manifest_path(path):
            _reject("POLICY_MANIFEST_INVALID")
        rows.append((path, digest))
        offset += len(line)
    if offset != len(data) or len(rows) != 3:
        _reject("POLICY_MANIFEST_INVALID")
    if tuple(path for path, _digest in rows) != EXPECTED_MANIFEST_PATHS:
        _reject("POLICY_MANIFEST_INVALID")
    if len({path for path, _digest in rows}) != len(rows):
        _reject("POLICY_MANIFEST_INVALID")
    return MappingProxyType(dict(rows))


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(child) for key, child in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(child) for child in value)
    return value


@dataclass(frozen=True, slots=True)
class RoleContract:
    name: str
    ordinal: int
    storage_class: str
    producer: str
    source_kind: str
    media_type: str
    maximum_bytes: int
    zero_byte_payload_allowed: bool
    canonical_json_required: bool
    strict_utf8_required: bool
    required_metadata: tuple[str, ...]
    conditional_required_metadata: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class HookContract:
    hook_id: str
    order: int
    version: int
    validation_scope: str
    failure_codes: tuple[str, ...]
    raw: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class ApplicabilityRule:
    document_kind: str
    roles: tuple[str, ...]
    hooks: tuple[str, ...]


@dataclass(frozen=True, slots=True, init=False)
class ValidationResult:
    valid: bool
    dispatcher_id: str
    hook_id: str | None
    failure_code: str | None
    subject_evidence_id: str | None
    document_type: str = "FULL_ADMISSIBILITY_VALIDATION_RESULT_V1"
    schema_version: int = 1


def _validation_result(
    valid: bool,
    dispatcher_id: str,
    hook_id: str | None,
    failure_code: str | None,
    subject_evidence_id: str | None,
) -> ValidationResult:
    result = object.__new__(ValidationResult)
    object.__setattr__(result, "valid", valid)
    object.__setattr__(result, "dispatcher_id", dispatcher_id)
    object.__setattr__(result, "hook_id", hook_id)
    object.__setattr__(result, "failure_code", failure_code)
    object.__setattr__(result, "subject_evidence_id", subject_evidence_id)
    object.__setattr__(result, "document_type", "FULL_ADMISSIBILITY_VALIDATION_RESULT_V1")
    object.__setattr__(result, "schema_version", 1)
    return result


@dataclass(frozen=True, slots=True)
class AuthenticatedPolicy:
    """Immutable policy and closed runtime contracts."""

    policy_bytes: bytes
    schema_bytes: bytes
    manifest_bytes: bytes
    manifest_sha256: str
    policy: Mapping[str, Any]
    schema: Mapping[str, Any]
    roles: Mapping[str, RoleContract]
    hooks: tuple[HookContract, ...]
    applicability: tuple[ApplicabilityRule, ...]
    dispatcher_id: str

    def role(self, name: str) -> RoleContract:
        try:
            return self.roles[name]
        except KeyError:
            _reject("EVIDENCE_ROLE_INVALID")

    def hook_plan(self, document_kind: str, role: str | None = None) -> tuple[HookContract, ...]:
        selected_role = "NONE" if role is None else role
        matches = [
            row
            for row in self.applicability
            if row.document_kind == document_kind and selected_role in row.roles
        ]
        if len(matches) != 1:
            _reject("UNKNOWN_DOCUMENT_ROLE")
        selected = set(matches[0].hooks)
        return tuple(hook for hook in self.hooks if hook.hook_id in selected)

    def structural_validate(self, candidate: Any) -> None:
        try:
            validate_canonical_value(candidate)
            schema = json.loads(self.schema_bytes.decode("utf-8", errors="strict"))
            Draft202012Validator(schema).validate(candidate)
        except Exception:
            _reject("STRUCTURAL_SCHEMA_INVALID")

    def full_admissibility(
        self,
        candidate: Any,
        resolved_context: Any | None = None,
    ) -> ValidationResult:
        """Fail closed until the frozen resolved-context authenticator is implemented."""

        try:
            self.structural_validate(candidate)
            if not isinstance(candidate, dict):
                _reject("STRUCTURAL_SCHEMA_INVALID")
            subject = candidate.get("evidence_id") if isinstance(candidate.get("evidence_id"), str) else None
            if resolved_context is None:
                return _validation_result(
                    False,
                    self.dispatcher_id,
                    "VALIDATE_DESCRIPTOR_CONTENT_IDENTITY_V1",
                    "MISSING_RESOLVED_CONTEXT",
                    subject,
                )
            plan = self.hook_plan(candidate.get("document_type", ""), candidate.get("role"))
            for hook in plan:
                if hook.hook_id == "VALIDATE_CANONICAL_NO_FLOATS_V1":
                    validate_canonical_value(candidate)
                    continue
                return _validation_result(
                    False,
                    self.dispatcher_id,
                    hook.hook_id,
                    "RUNNER_IMPLEMENTATION_INCOMPLETE",
                    subject,
                )
            return _validation_result(
                False,
                self.dispatcher_id,
                "VALIDATE_DESCRIPTOR_CONTENT_IDENTITY_V1",
                "RUNNER_IMPLEMENTATION_INCOMPLETE",
                subject,
            )
        except PolicyRuntimeError as error:
            return _validation_result(False, self.dispatcher_id, None, error.code, None)
        except Exception:
            return _validation_result(
                False,
                self.dispatcher_id,
                None,
                "VALIDATOR_EXECUTION_FAILURE",
                None,
            )


def load_policy_bundle(
    policy_path: Path,
    schema_path: Path,
    manifest_path: Path,
    *,
    expected_manifest_sha256: str,
    parsed_policy_override: Any | None = None,
    hook_contracts_override: Any | None = None,
    applicability_override: Any | None = None,
) -> AuthenticatedPolicy:
    """Authenticate exact v1 bytes and expose an immutable closed runtime view."""

    if any(value is not None for value in (parsed_policy_override, hook_contracts_override, applicability_override)):
        _reject("POLICY_PARSED_CONTENT_MISMATCH")
    if expected_manifest_sha256 != POLICY_MANIFEST_SHA256:
        _reject("POLICY_MANIFEST_HASH_MISMATCH")
    try:
        policy_bytes = Path(policy_path).read_bytes()
        schema_bytes = Path(schema_path).read_bytes()
        manifest_bytes = Path(manifest_path).read_bytes()
    except (OSError, TypeError):
        _reject("POLICY_BINDING_MISSING")
    if _sha256(manifest_bytes) != expected_manifest_sha256:
        _reject("POLICY_MANIFEST_HASH_MISMATCH")
    rows = _parse_manifest(manifest_bytes)
    if rows[POLICY_RELATIVE_PATH] != _sha256(policy_bytes):
        _reject("POLICY_HASH_MISMATCH")
    if rows[SCHEMA_RELATIVE_PATH] != _sha256(schema_bytes):
        _reject("POLICY_SCHEMA_HASH_MISMATCH")
    try:
        policy = yaml.safe_load(policy_bytes.decode("utf-8", errors="strict"))
        schema = json.loads(schema_bytes.decode("utf-8", errors="strict"))
        validate_canonical_value(policy)
        validate_canonical_value(schema)
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(policy)
    except Exception:
        _reject("POLICY_PARSED_CONTENT_MISMATCH")
    if not isinstance(policy, dict) or not isinstance(schema, dict):
        _reject("POLICY_PARSED_CONTENT_MISMATCH")
    full = policy.get("full_admissibility_validation")
    if not isinstance(full, dict):
        _reject("HOOK_MATRIX_MISMATCH")
    roles_raw = policy.get("roles")
    hooks_raw = full.get("hook_contracts")
    matrix_raw = full.get("hook_applicability_matrix")
    state_raw = policy.get("verified_attempt_state_machine")
    sensitive_raw = policy.get("sensitive_capture_policy")
    if not isinstance(roles_raw, dict) or len(roles_raw) != 21:
        _reject("EVIDENCE_ROLE_INVALID")
    if _section_sha256(roles_raw) != EXPECTED_ROLE_SECTION_SHA256:
        _reject("EVIDENCE_ROLE_INVALID")
    if _section_sha256(hooks_raw) != EXPECTED_HOOK_SECTION_SHA256:
        _reject("HOOK_MATRIX_MISMATCH")
    if _section_sha256(matrix_raw) != EXPECTED_MATRIX_SECTION_SHA256:
        _reject("HOOK_MATRIX_MISMATCH")
    if _section_sha256(state_raw) != EXPECTED_STATE_SECTION_SHA256:
        _reject("POLICY_PARSED_CONTENT_MISMATCH")
    if _section_sha256(sensitive_raw) != EXPECTED_SENSITIVE_SECTION_SHA256:
        _reject("POLICY_PARSED_CONTENT_MISMATCH")
    if not isinstance(hooks_raw, list) or len(hooks_raw) != 12:
        _reject("HOOK_ORDER_MISMATCH")
    order = tuple(row.get("hook_id") for row in hooks_raw if isinstance(row, dict))
    ordinals = tuple(row.get("order") for row in hooks_raw if isinstance(row, dict))
    if order != EXPECTED_HOOK_ORDER or ordinals != tuple(range(1, 13)) or len(set(order)) != 12:
        _reject("HOOK_ORDER_MISMATCH")
    if not isinstance(matrix_raw, list) or len(matrix_raw) != 19:
        _reject("HOOK_MATRIX_MISMATCH")

    roles = {
        name: RoleContract(
            name=name,
            ordinal=row["ordinal"],
            storage_class=row["storage_class"],
            producer=row["producer"],
            source_kind=row["source_kind"],
            media_type=row["media_type"],
            maximum_bytes=row["maximum_bytes"],
            zero_byte_payload_allowed=row["zero_byte_payload_allowed"],
            canonical_json_required=row["canonical_json_required"],
            strict_utf8_required=row["strict_utf8_required"],
            required_metadata=tuple(row["required_metadata"]),
            conditional_required_metadata=tuple(row.get("conditional_required_metadata", ())),
        )
        for name, row in roles_raw.items()
    }
    hooks = tuple(
        HookContract(
            hook_id=row["hook_id"],
            order=row["order"],
            version=row["version"],
            validation_scope=row["validation_scope"],
            failure_codes=tuple(row["failure_codes"]),
            raw=_freeze(row),
        )
        for row in hooks_raw
    )
    applicability = tuple(
        ApplicabilityRule(row["document_kind"], tuple(row["roles"]), tuple(row["hooks"]))
        for row in matrix_raw
    )
    dispatcher_id = full.get("dispatcher_id")
    if dispatcher_id != "SREMUT_FULL_ADMISSIBILITY_DISPATCHER_V1":
        _reject("HOOK_MATRIX_MISMATCH")
    return AuthenticatedPolicy(
        policy_bytes=policy_bytes,
        schema_bytes=schema_bytes,
        manifest_bytes=manifest_bytes,
        manifest_sha256=expected_manifest_sha256,
        policy=_freeze(policy),
        schema=_freeze(schema),
        roles=MappingProxyType(roles),
        hooks=hooks,
        applicability=applicability,
        dispatcher_id=dispatcher_id,
    )

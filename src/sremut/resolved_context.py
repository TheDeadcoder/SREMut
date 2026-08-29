"""Authenticated immutable resolution of one sealed SREMut attempt."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any, Mapping, NoReturn

import yaml

from sremut.canonical_json import canonical_json_bytes, parse_canonical_json, sha256_hex
from sremut.evidence import (
    EvidenceRef,
    ExternalAnchor,
    _parse_manifest,
    descriptor_content_sha256,
    validate_evidence_ref,
)
from sremut.journal import Journal, JournalError, JournalState, SafeRoot
from sremut.policy_runtime import (
    AuthenticatedPolicy,
    EXPECTED_HOOK_ORDER,
    POLICY_V1_2_SEMANTIC_VERSION,
    policy_binding,
    _parse_manifest as _parse_policy_manifest,
)
from sremut.sensitive import SensitiveCaptureError, detect_sensitive, validate_payload


CONNECTED_HOOKS = frozenset(
    {
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
    }
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RUN_ID = re.compile(r"^sremut-ms-(m01|m02|m03)-r0([1-3])-a0([1-2])-[0-9a-f]{12}$")
# Named by the frozen v1.2 schema's offline-seal release binding.  Emitting the
# constant the schema requires is not the same as creating the tag: no v1.2 tag
# exists in this repository and none is created here.
V1_2_EVIDENCE_POLICY_TAG_NAME = "sremut-missing-service-evidence-policy-v1.2"
_RESOLVER_TOKEN = object()
_MAXIMUM_MANIFEST_BYTES = 4 * 1024 * 1024
_MAXIMUM_JOURNAL_BYTES = 16 * 1024 * 1024
_AUTHENTICATED_POLICY_CACHE: dict[int, tuple[AuthenticatedPolicy, PolicyIdentity]]


@dataclass(slots=True)
class ResolvedContextError(ValueError):
    """Stable failure without attacker-controlled material."""

    code: str

    def __str__(self) -> str:
        return self.code


def _reject(code: str) -> NoReturn:
    raise ResolvedContextError(code) from None


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(child) for key, child in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(child) for child in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [_thaw(child) for child in value]
    return value


@dataclass(frozen=True, slots=True)
class PolicyIdentity:
    manifest_sha256: str
    policy_sha256: str
    schema_sha256: str
    dispatcher_id: str
    contract_sha256: str
    contract_tag_object: str
    execution_profile_sha256: str
    execution_profile_tag_object: str


_AUTHENTICATED_POLICY_CACHE = {}


@dataclass(frozen=True, slots=True)
class PublicationRecord:
    sequence_number: int
    journal_record_sha256: str


@dataclass(frozen=True, slots=True, repr=False)
class ResolvedEvidence:
    reference: EvidenceRef
    descriptor_bytes: bytes = field(repr=False)
    descriptor: Mapping[str, Any] = field(repr=False)
    payload_bytes: bytes | None = field(repr=False)
    publication: PublicationRecord

    def __repr__(self) -> str:
        payload = "present" if self.payload_bytes is not None else "absent"
        return (
            "ResolvedEvidence("
            f"evidence_id={self.reference.evidence_id!r}, "
            f"role={self.reference.role!r}, payload={payload})"
        )


@dataclass(frozen=True, slots=True, init=False, repr=False)
class ResolvedEvidenceContext:
    """Closed byte-authenticated view of a sealed attempt.

    Instances can only be created by :func:`resolve_evidence_context`; direct
    construction cannot produce the private authentication marker checked by
    the dispatcher.
    """

    policy_identity: PolicyIdentity
    run_id: str
    attempt_id: str
    attempt_root_identifier: str
    manifest_relative_path: str
    terminal_manifest_sha256: str
    terminal_manifest_bytes: bytes = field(repr=False)
    manifest_rows: tuple[tuple[str, str], ...]
    evidence: Mapping[str, ResolvedEvidence] = field(repr=False)
    journal_bytes: bytes = field(repr=False)
    journal_records: tuple[Mapping[str, Any], ...] = field(repr=False)
    journal_state: JournalState = field(repr=False)
    terminal_outcome: str
    classification: str
    terminal_global_stop_bytes: bytes | None = field(repr=False)
    terminal_global_stop: Mapping[str, Any] | None = field(repr=False)
    expected_operation_context: Mapping[str, Any] | None = field(repr=False)
    evaluation_authorization_contexts: Mapping[str, Mapping[str, Any]] = field(repr=False)
    _authentication_marker: object = field(repr=False, compare=False)
    _policy: AuthenticatedPolicy = field(repr=False, compare=False)

    def __new__(cls, *args: Any, **kwargs: Any) -> ResolvedEvidenceContext:
        if kwargs.pop("_resolver_token", None) is not _RESOLVER_TOKEN or args or kwargs:
            raise TypeError("RESOLVED_CONTEXT_DIRECT_CONSTRUCTION_FORBIDDEN")
        return object.__new__(cls)

    def __repr__(self) -> str:
        return (
            "ResolvedEvidenceContext("
            f"run_id={self.run_id!r}, attempt_id={self.attempt_id!r}, "
            f"terminal_outcome={self.terminal_outcome!r}, "
            f"evidence_count={len(self.evidence)})"
        )

    def authenticates(self, policy: AuthenticatedPolicy) -> bool:
        try:
            return self._authentication_marker is _RESOLVER_TOKEN and (
                policy is self._policy
                or self.policy_identity == _policy_identity(policy)
            )
        except Exception:
            return False

    def resolve_reference(self, reference: EvidenceRef | Mapping[str, Any]) -> ResolvedEvidence:
        try:
            validated = validate_evidence_ref(
                self._policy,
                reference,
                expected_run_id=self.run_id,
                expected_attempt_id=self.attempt_id,
            )
        except Exception:
            _reject("EVIDENCE_REFERENCE_INVALID")
        row = self.evidence.get(validated.evidence_id)
        if row is None or row.reference.as_dict() != validated.as_dict():
            _reject("EVIDENCE_REFERENCE_UNRESOLVED")
        return row

    def as_v1_2_document(self) -> dict[str, Any]:
        """Serialize this authenticated context as a `RESOLVED_EVIDENCE_CONTEXT_V1_2`.

        Every field is derived from bytes this context already authenticated --
        retained descriptor and payload bytes, the authoritative journal, the
        terminal manifest and the external anchor.  Nothing is caller supplied.

        Only a v1.2-bound context can produce one: the v1.1 schema defines no
        such document, so the result would have nothing to validate against.
        """
        binding = policy_binding(self._policy)
        if binding.semantic_version != POLICY_V1_2_SEMANTIC_VERSION:
            _reject("POLICY_BINDING_MISSING")
        match = _RUN_ID.fullmatch(self.run_id)
        if match is None:
            _reject("RUN_ATTEMPT_MISMATCH")
        identity = self.policy_identity
        evidence_refs: dict[str, Any] = {}
        descriptor_hex: dict[str, str] = {}
        parsed: dict[str, Any] = {}
        payload_hex: dict[str, str] = {}
        publications: dict[str, Any] = {}
        for evidence_id, row in self.evidence.items():
            evidence_refs[evidence_id] = row.reference.as_dict()
            descriptor_hex[evidence_id] = row.descriptor_bytes.hex()
            parsed[evidence_id] = _thaw(row.descriptor)
            if row.payload_bytes is not None:
                payload_hex[evidence_id] = row.payload_bytes.hex()
            publications[evidence_id] = {
                "sequence_number": row.publication.sequence_number,
                "journal_record_sha256": row.publication.journal_record_sha256,
            }
        return {
            "document_type": "RESOLVED_EVIDENCE_CONTEXT_V1_2",
            "schema_version": 1,
            "run_id": self.run_id,
            "attempt_id": self.attempt_id,
            # Both are read out of the authenticated run id, not supplied.
            "mutant_id": f"MS-M{match.group(1)[1:]}",
            "repetition": int(match.group(2)),
            "evidence_refs": evidence_refs,
            "exact_descriptor_bytes_hex": descriptor_hex,
            "parsed_canonical_descriptors": parsed,
            "exact_payload_bytes_hex": payload_hex,
            "journal_publication_records": publications,
            "attempt_journal_records": [_thaw(record) for record in self.journal_records],
            "exact_authoritative_journal_bytes_hex": self.journal_bytes.hex(),
            # This context was rebuilt from a sealed attempt root under an
            # external anchor; there is no capture-time trusted source here.
            "validation_mode": "OFFLINE_SEALED_REVALIDATION",
            "trusted_capture_source_bytes_hex": {},
            "offline_seal": {
                "document_type": "OFFLINE_SEALED_REVALIDATION_INPUT_V1",
                "schema_version": 1,
                "attempt_root_identifier": self.attempt_root_identifier,
                "run_id": self.run_id,
                "attempt_id": self.attempt_id,
                "terminal_state": self.terminal_outcome,
                "terminal_manifest_bytes_hex": self.terminal_manifest_bytes.hex(),
                "externally_recorded_terminal_manifest_sha256": self.terminal_manifest_sha256,
                "expected_manifest_relative_path": self.manifest_relative_path,
                "sealed_journal_sha256": hashlib.sha256(self.journal_bytes).hexdigest(),
                "runner_release_binding": {
                    "evidence_policy_annotated_tag": V1_2_EVIDENCE_POLICY_TAG_NAME,
                    "evidence_policy_checksum_manifest_sha256": identity.manifest_sha256,
                    "evidence_policy_sha256": identity.policy_sha256,
                    "evidence_policy_schema_sha256": identity.schema_sha256,
                    "dispatcher_id": identity.dispatcher_id,
                },
                "aggregation_anchor": {
                    "recorded_by": "IMMUTABLE_RUN_INDEX_OUTSIDE_ATTEMPT_ROOT",
                    "cited_by_result_aggregation": True,
                    "attempt_root_identifier": self.attempt_root_identifier,
                    "manifest_relative_path": self.manifest_relative_path,
                    "terminal_manifest_sha256": self.terminal_manifest_sha256,
                },
            },
            "current_verified_attempt_state": {
                "state": self.journal_state.state,
                "sequence_number": self.journal_state.sequence_number,
                "terminal": self.journal_state.terminal,
            },
            "frozen_contract_profile_identities": {
                "contract_sha256": identity.contract_sha256,
                "execution_profile_sha256": identity.execution_profile_sha256,
                "contract_tag_object": identity.contract_tag_object,
                "execution_profile_tag_object": identity.execution_profile_tag_object,
            },
            "expected_operation_context": (
                None
                if self.expected_operation_context is None
                else _thaw(self.expected_operation_context)
            ),
            "challenge_identity": None,
            "evaluation_authorization_contexts": _thaw(
                self.evaluation_authorization_contexts
            ),
        }

    def validate_hook(self, hook_id: str, candidate: Mapping[str, Any]) -> None:
        handlers = {
            "VALIDATE_EVIDENCE_REF_HASH_PATH_ID_V1": self._validate_references,
            "VALIDATE_DESCRIPTOR_CONTENT_IDENTITY_V1": self._validate_descriptor,
            "VALIDATE_ATTEMPT_PHASES_AND_FINALITY_V1": self._validate_attempt,
            "VALIDATE_WORKLOAD_CARDINALITY_V1": self._validate_workload_cardinality,
            "VALIDATE_WORKLOAD_WINDOW_CONSISTENCY_V1": self._validate_workload_window,
            "VALIDATE_ADJUDICATION_RAW_BACKING_V1": self._validate_adjudication,
            "VALIDATE_JOURNAL_HASH_CHAIN_V1": self._validate_journal,
            "VALIDATE_KUBERNETES_REQUEST_V1": self._validate_kubernetes_request,
            "VALIDATE_KUBERNETES_RESPONSE_V1": self._validate_kubernetes_response,
            "VALIDATE_SERVICE_RESTORATION_BODY_V1": self._validate_service_restoration,
            "VALIDATE_SENSITIVE_CAPTURE_V1": self._validate_sensitive,
        }
        handler = handlers.get(hook_id)
        if handler is None:
            _reject("RUNNER_IMPLEMENTATION_INCOMPLETE")
        handler(candidate)

    def _validate_references(self, candidate: Mapping[str, Any]) -> None:
        references = tuple(_iter_evidence_refs(candidate))
        for reference in references:
            self.resolve_reference(reference)
        if candidate.get("document_type") in (
            "PAYLOAD_EVIDENCE_REF_V1",
            "DESCRIPTOR_EVIDENCE_REF_V1",
        ):
            self.resolve_reference(candidate)

    def _candidate_evidence(self, candidate: Mapping[str, Any]) -> ResolvedEvidence:
        evidence_id = candidate.get("evidence_id")
        if isinstance(evidence_id, str):
            row = self.evidence.get(evidence_id)
            if row is not None and _thaw(row.descriptor) == candidate:
                return row
        matches = tuple(
            row for row in self.evidence.values() if _thaw(row.descriptor) == candidate
        )
        if len(matches) != 1:
            _reject("EVIDENCE_REFERENCE_UNRESOLVED")
        return matches[0]

    def _validate_descriptor(self, candidate: Mapping[str, Any]) -> None:
        if candidate.get("document_type") not in (
            "PAYLOAD_EVIDENCE_DESCRIPTOR_V1",
            "DESCRIPTOR_EVIDENCE_DESCRIPTOR_V1",
        ):
            return
        row = self._candidate_evidence(candidate)
        try:
            parsed = parse_canonical_json(row.descriptor_bytes)
        except Exception:
            _reject("DESCRIPTOR_CANONICAL_MISMATCH")
        if parsed != candidate:
            _reject("DESCRIPTOR_REFERENCE_MISMATCH")
        if descriptor_content_sha256(parsed) != row.reference.descriptor_sha256:
            _reject("DESCRIPTOR_HASH_MISMATCH")
        try:
            self._policy.structural_validate(parsed)
            validate_evidence_ref(
                self._policy,
                row.reference,
                descriptor_bytes=row.descriptor_bytes,
                payload_bytes=row.payload_bytes,
                expected_run_id=self.run_id,
                expected_attempt_id=self.attempt_id,
            )
        except ResolvedContextError:
            raise
        except Exception as error:
            code = str(error)
            allowed = {
                "DESCRIPTOR_HASH_MISMATCH",
                "DESCRIPTOR_CANONICAL_MISMATCH",
                "DESCRIPTOR_REFERENCE_MISMATCH",
                "PAYLOAD_HASH_MISMATCH",
                "PAYLOAD_SIZE_MISMATCH",
                "RUN_ATTEMPT_MISMATCH",
            }
            _reject(code if code in allowed else "DESCRIPTOR_REFERENCE_MISMATCH")
        if row.publication.sequence_number > self.journal_state.sequence_number:
            _reject("PUBLICATION_ORDER_INVALID")

    def _validate_attempt(self, candidate: Mapping[str, Any]) -> None:
        if candidate.get("run_id") != self.run_id or candidate.get("attempt_id") != self.attempt_id:
            _reject("ATTEMPT_FINALITY_INVALID")
        kind = candidate.get("document_type")
        if kind == "TERMINAL_GLOBAL_STOP_V1":
            if (
                self.terminal_outcome != "RESTORATION_BLOCKED"
                or self.terminal_global_stop is None
                or self.terminal_global_stop != candidate
            ):
                _reject("ATTEMPT_FINALITY_INVALID")
        elif kind == "DESCRIPTOR_EVIDENCE_DESCRIPTOR_V1" and candidate.get("role") == "run_identity":
            self._validate_terminal_run_identities()
        elif kind == "ATTEMPT_VALIDATION_ENVELOPE_V1":
            self._validate_attempt_envelope(candidate)
        if not self.journal_state.terminal or self.journal_state.state != self.terminal_outcome:
            _reject("ATTEMPT_FINALITY_INVALID")

    def _validate_terminal_run_identities(self) -> None:
        identities = sorted(
            (
                row.descriptor
                for row in self.evidence.values()
                if row.reference.role == "run_identity"
            ),
            key=lambda item: item.get("monotonic_ns", -1),
        )
        if len(identities) != 2 or tuple(item.get("phase") for item in identities) != (
            "START",
            "TERMINAL",
        ):
            _reject("ATTEMPT_FINALITY_INVALID")
        if any(
            item.get("run_id") != self.run_id or item.get("attempt_id") != self.attempt_id
            for item in identities
        ):
            _reject("ATTEMPT_FINALITY_INVALID")
        if identities[1].get("terminal_outcome") != self.terminal_outcome:
            _reject("ATTEMPT_FINALITY_INVALID")

    def _validate_attempt_envelope(self, candidate: Mapping[str, Any]) -> None:
        identities = candidate.get("run_identities")
        if not isinstance(identities, (list, tuple)) or len(identities) != 2:
            _reject("ATTEMPT_FINALITY_INVALID")
        start, terminal = identities
        if start.get("phase") != "START" or terminal.get("phase") != "TERMINAL":
            _reject("ATTEMPT_FINALITY_INVALID")
        if candidate.get("terminal_outcome") != self.terminal_outcome:
            _reject("ATTEMPT_FINALITY_INVALID")
        if tuple(candidate.get("journal_records", ())) != tuple(
            _thaw(record) for record in self.journal_records
        ):
            _reject("ATTEMPT_FINALITY_INVALID")
        manifest = candidate.get("terminal_manifest", {})
        if (
            manifest.get("manifest_relative_path") != self.manifest_relative_path
            or manifest.get("terminal_outcome") != self.terminal_outcome
        ):
            _reject("ATTEMPT_FINALITY_INVALID")
        installed = manifest.get("installed_monotonic_ns")
        snapshot = manifest.get("terminal_cache_snapshot_monotonic_ns")
        if (
            not isinstance(installed, int)
            or isinstance(installed, bool)
            or not isinstance(snapshot, int)
            or isinstance(snapshot, bool)
            or snapshot > installed
        ):
            _reject("ATTEMPT_FINALITY_INVALID")
        if any(operation.get("created_monotonic_ns", installed) >= installed for operation in candidate.get("operations", ())):
            _reject("POST_TERMINAL_OPERATION")

    def _validate_journal(self, candidate: Mapping[str, Any]) -> None:
        try:
            rebuilt = Journal.reconstruct_retained(
                self._policy,
                self.run_id,
                self.attempt_id,
                self.journal_bytes,
            )
        except Exception as error:
            code = str(error)
            allowed = {
                "JOURNAL_CHAIN_INVALID",
                "JOURNAL_CANONICALIZATION_INVALID",
                "JOURNAL_STATE_DERIVATION_FAILED",
                "JOURNAL_CONTEXT_MISMATCH",
                "POST_TERMINAL_OPERATION",
            }
            _reject(code if code in allowed else "JOURNAL_CHAIN_INVALID")
        if (
            rebuilt.state != self.journal_state.state
            or rebuilt.sequence_number != self.journal_state.sequence_number
            or rebuilt.terminal != self.journal_state.terminal
            or _thaw(rebuilt.evaluation_authorization_contexts)
            != _thaw(self.journal_state.evaluation_authorization_contexts)
            or _thaw(rebuilt.operation) != _thaw(self.journal_state.operation)
        ):
            _reject("JOURNAL_CHAIN_INVALID")
        if candidate.get("document_type") == "JOURNAL_RECORD_V1":
            try:
                candidate_bytes = canonical_json_bytes(dict(candidate)) + b"\n"
            except Exception:
                _reject("JOURNAL_CANONICALIZATION_INVALID")
            if candidate_bytes not in self.journal_bytes.splitlines(keepends=True):
                _reject("JOURNAL_CHAIN_INVALID")
        manifest_digest = dict(self.manifest_rows).get("journal/attempt.jsonl")
        if manifest_digest != sha256_hex(self.journal_bytes):
            _reject("JOURNAL_CHAIN_INVALID")

    def _validate_workload_cardinality(self, candidate: Mapping[str, Any]) -> None:
        from sremut.workload_evidence import (
            WorkloadEvidenceError,
            validate_resolved_workload_cardinality,
        )

        try:
            validate_resolved_workload_cardinality(self, candidate)
        except WorkloadEvidenceError as error:
            _reject(error.code)

    def _validate_workload_window(self, candidate: Mapping[str, Any]) -> None:
        from sremut.workload_evidence import (
            WorkloadEvidenceError,
            validate_resolved_workload_window,
        )

        try:
            validate_resolved_workload_window(self, candidate)
        except WorkloadEvidenceError as error:
            _reject(error.code)

    def _validate_adjudication(self, candidate: Mapping[str, Any]) -> None:
        from sremut.adjudication import AdjudicationError, validate_resolved_adjudication

        try:
            validate_resolved_adjudication(self, candidate)
        except AdjudicationError as error:
            _reject(error.code)

    def _validate_kubernetes_request(self, candidate: Mapping[str, Any]) -> None:
        from sremut.kubernetes_readonly import (
            KubernetesReadOnlyError,
            validate_resolved_kubernetes_request,
        )

        try:
            validate_resolved_kubernetes_request(self, candidate)
        except KubernetesReadOnlyError as error:
            _reject(error.code)

    def _validate_kubernetes_response(self, candidate: Mapping[str, Any]) -> None:
        from sremut.kubernetes_readonly import (
            KubernetesReadOnlyError,
            validate_resolved_kubernetes_response,
        )

        try:
            validate_resolved_kubernetes_response(self, candidate)
        except KubernetesReadOnlyError as error:
            _reject(error.code)

    def _validate_service_restoration(self, candidate: Mapping[str, Any]) -> None:
        from sremut.service_restoration import (
            ServiceRestorationError,
            validate_resolved_service_restoration,
        )

        try:
            validate_resolved_service_restoration(self, candidate)
        except ServiceRestorationError as error:
            _reject(error.code)

    def _validate_sensitive(self, candidate: Mapping[str, Any]) -> None:
        rows: list[ResolvedEvidence] = []
        if candidate.get("document_type") in (
            "PAYLOAD_EVIDENCE_DESCRIPTOR_V1",
            "DESCRIPTOR_EVIDENCE_DESCRIPTOR_V1",
        ):
            rows.append(self._candidate_evidence(candidate))
        for reference in _iter_evidence_refs(candidate):
            row = self.resolve_reference(reference)
            if row not in rows:
                rows.append(row)
        for row in rows:
            try:
                finding = detect_sensitive(row.descriptor_bytes, structured_format="json")
                if finding is not None:
                    _reject("SENSITIVE_CAPTURE_REJECTED")
                if row.payload_bytes is not None:
                    validate_payload(
                        self._policy,
                        row.reference.role,
                        row.payload_bytes,
                        media_type=row.reference.media_type,
                    )
            except ResolvedContextError:
                raise
            except SensitiveCaptureError:
                _reject("SENSITIVE_CAPTURE_REJECTED")
            except Exception:
                _reject("SENSITIVE_CAPTURE_REJECTED")


def _policy_identity(policy: AuthenticatedPolicy) -> PolicyIdentity:
    if not isinstance(policy, AuthenticatedPolicy):
        _reject("POLICY_BINDING_MISSING")
    cached = _AUTHENTICATED_POLICY_CACHE.get(id(policy))
    if cached is not None and cached[0] is policy:
        return cached[1]
    # The version binding travels with the authenticated policy.  There is no
    # hardcoded v1.1 pin here any more, and no second copy of the manifest
    # pins: `policy_binding` is the single closed authority, and it accepts
    # only the two explicitly pinned bundles.
    binding = policy_binding(policy)
    if hashlib.sha256(policy.manifest_bytes).hexdigest() != binding.manifest_sha256:
        _reject("POLICY_MANIFEST_HASH_MISMATCH")
    try:
        rows = _parse_policy_manifest(policy.manifest_bytes, binding.manifest_paths)
    except Exception:
        _reject("POLICY_MANIFEST_INVALID")
    if rows[binding.policy_relative_path] != hashlib.sha256(policy.policy_bytes).hexdigest():
        _reject("POLICY_HASH_MISMATCH")
    if rows[binding.schema_relative_path] != hashlib.sha256(policy.schema_bytes).hexdigest():
        _reject("POLICY_SCHEMA_HASH_MISMATCH")
    if rows.get(binding.generator_relative_path) != binding.generator_sha256:
        _reject("POLICY_MANIFEST_INVALID")
    try:
        parsed_policy = yaml.safe_load(policy.policy_bytes.decode("utf-8", errors="strict"))
    except Exception:
        _reject("POLICY_PARSED_CONTENT_MISMATCH")
    if _thaw(policy.policy) != parsed_policy:
        _reject("POLICY_PARSED_CONTENT_MISMATCH")
    hooks = parsed_policy["full_admissibility_validation"]["hook_contracts"]
    matrix = parsed_policy["full_admissibility_validation"]["hook_applicability_matrix"]
    if (
        tuple(hook.hook_id for hook in policy.hooks) != EXPECTED_HOOK_ORDER
        or [_thaw(hook.raw) for hook in policy.hooks] != hooks
        or [
            {
                "document_kind": row.document_kind,
                "roles": list(row.roles),
                "hooks": list(row.hooks),
            }
            for row in policy.applicability
        ]
        != matrix
        or policy.dispatcher_id
        != parsed_policy["full_admissibility_validation"]["dispatcher_id"]
    ):
        _reject("HOOK_MATRIX_MISMATCH")
    parsed_roles = parsed_policy["roles"]
    if set(policy.roles) != set(parsed_roles):
        _reject("EVIDENCE_ROLE_INVALID")
    for name, row in parsed_roles.items():
        contract = policy.roles[name]
        expected = (
            row["ordinal"],
            row["storage_class"],
            row["producer"],
            row["source_kind"],
            row["media_type"],
            row["maximum_bytes"],
            row["zero_byte_payload_allowed"],
            row["canonical_json_required"],
            row["strict_utf8_required"],
            tuple(row["required_metadata"]),
            tuple(row.get("conditional_required_metadata", ())),
        )
        actual = (
            contract.ordinal,
            contract.storage_class,
            contract.producer,
            contract.source_kind,
            contract.media_type,
            contract.maximum_bytes,
            contract.zero_byte_payload_allowed,
            contract.canonical_json_required,
            contract.strict_utf8_required,
            contract.required_metadata,
            contract.conditional_required_metadata,
        )
        if actual != expected:
            _reject("EVIDENCE_ROLE_INVALID")
    bindings = policy.policy.get("bindings")
    if not isinstance(bindings, Mapping):
        _reject("POLICY_BINDING_MISSING")
    contract = bindings.get("contract")
    profile = bindings.get("execution_profile")
    if not isinstance(contract, Mapping) or not isinstance(profile, Mapping):
        _reject("POLICY_BINDING_MISSING")
    identity = PolicyIdentity(
        manifest_sha256=policy.manifest_sha256,
        policy_sha256=hashlib.sha256(policy.policy_bytes).hexdigest(),
        schema_sha256=hashlib.sha256(policy.schema_bytes).hexdigest(),
        dispatcher_id=policy.dispatcher_id,
        contract_sha256=contract["sha256"],
        contract_tag_object=contract["tag_object"],
        execution_profile_sha256=profile["sha256"],
        execution_profile_tag_object=profile["tag_object"],
    )
    _AUTHENTICATED_POLICY_CACHE[id(policy)] = (policy, identity)
    return identity


def _iter_evidence_refs(value: Any):
    if isinstance(value, Mapping):
        if value.get("document_type") in (
            "PAYLOAD_EVIDENCE_REF_V1",
            "DESCRIPTOR_EVIDENCE_REF_V1",
        ):
            yield value
        for child in value.values():
            yield from _iter_evidence_refs(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _iter_evidence_refs(child)


def _publication_for(
    reference: EvidenceRef,
    records: tuple[Mapping[str, Any], ...],
) -> PublicationRecord:
    for record in records:
        if reference.descriptor_sha256 not in record.get("referenced_descriptor_sha256", ()):
            continue
        if (
            reference.payload_sha256 is not None
            and reference.payload_sha256 not in record.get("referenced_payload_sha256", ())
        ):
            continue
        return PublicationRecord(
            sequence_number=record["sequence_number"],
            journal_record_sha256=record["canonical_current_entry_sha256"],
        )
    _reject("PUBLICATION_RECORD_MISSING")


def _evaluation_authorization_contexts(
    policy: AuthenticatedPolicy, state: JournalState
) -> Mapping[str, Mapping[str, Any]]:
    """Reconstruct one authorization context per predicate from journal bytes.

    v1.1 retained only the LAST marker, so a terminal attempt exposed a single
    context and could not revalidate its own earlier adjudications.  v1.2 keeps
    every predicate's own context, with `authorization_state` replayed to that
    marker's own sequence -- never the terminal state.

    The journal is the only authority.  This re-derives the mapping here and
    requires it to equal what `JournalState` derived; a caller-supplied or
    schema-carried mapping that differs is rejected.
    """
    matrix = policy.policy["full_admissibility_validation"][
        "adjudication_predicate_raw_role_context_deadline_matrix"
    ]
    derived: dict[str, Any] = {}
    authorization_state = "CREATED"
    for record in state.records:
        transition = record.get("transition")
        if not isinstance(transition, str):
            continue
        if transition.startswith("EVALUATION_AUTHORIZED:"):
            predicate = transition.split(":", 1)[1]
            row = matrix.get(predicate)
            if not isinstance(row, Mapping):
                _reject("JOURNAL_CONTEXT_MISMATCH")
            if predicate in derived:
                _reject("JOURNAL_CONTEXT_MISMATCH")
            if authorization_state not in row["allowed_states"]:
                _reject("JOURNAL_CONTEXT_MISMATCH")
            derived[predicate] = {
                "predicate_id": predicate,
                "phase": row["phase"],
                "deadline_identity": row["deadline_identity"],
                "authorization_state": authorization_state,
                "marker_sequence_number": record.get("sequence_number"),
            }
        elif transition.startswith("STATE_VERIFIED:"):
            proposed = transition.split(":", 1)[1]
            if proposed != authorization_state:
                _reject("JOURNAL_CONTEXT_MISMATCH")
        elif "->" in transition and not transition.startswith("OPERATION_AUTHORIZED:"):
            source, target = transition.split("->", 1)
            if source != authorization_state:
                _reject("JOURNAL_CONTEXT_MISMATCH")
            authorization_state = target
    if _thaw(state.evaluation_authorization_contexts) != derived:
        _reject("JOURNAL_CONTEXT_MISMATCH")
    return _freeze(derived)


def _reference_from_descriptor(
    policy: AuthenticatedPolicy,
    descriptor: Mapping[str, Any],
    descriptor_bytes: bytes,
    path: str,
) -> EvidenceRef:
    role_name = descriptor.get("role")
    if not isinstance(role_name, str) or role_name == "terminal_manifest":
        _reject("EVIDENCE_REFERENCE_INVALID")
    try:
        role = policy.role(role_name)
    except Exception:
        _reject("EVIDENCE_REFERENCE_INVALID")
    digest = descriptor_content_sha256(descriptor)
    if path != f"descriptors/sha256/{digest[:2]}/{digest}.json":
        _reject("DESCRIPTOR_HASH_MISMATCH")
    if len(descriptor_bytes) > role.maximum_bytes:
        _reject("DESCRIPTOR_REFERENCE_MISMATCH")
    payload = role.storage_class == "PAYLOAD_WITH_DESCRIPTOR"
    reference = EvidenceRef(
        document_type="PAYLOAD_EVIDENCE_REF_V1" if payload else "DESCRIPTOR_EVIDENCE_REF_V1",
        schema_version=1,
        evidence_id="ev-" + digest[:32],
        role=role_name,
        producer=role.producer,
        source_kind=role.source_kind,
        media_type=role.media_type,
        storage_class=role.storage_class,
        descriptor_sha256=digest,
        descriptor_size_bytes=len(descriptor_bytes),
        descriptor_relative_path=path,
        redaction_status="NOT_REDACTED",
        payload_sha256=descriptor.get("payload_sha256") if payload else None,
        payload_size_bytes=descriptor.get("payload_size_bytes") if payload else None,
        payload_relative_path=descriptor.get("payload_relative_path") if payload else None,
        projection_class=(
            descriptor.get("projection_class")
            if role_name == "kubernetes_object_projection"
            else None
        ),
    )
    try:
        policy.structural_validate(descriptor)
        return validate_evidence_ref(
            policy,
            reference,
            descriptor_bytes=descriptor_bytes,
            expected_run_id=descriptor.get("run_id"),
            expected_attempt_id=descriptor.get("attempt_id"),
        )
    except Exception as error:
        code = str(error)
        allowed = {
            "DESCRIPTOR_HASH_MISMATCH",
            "DESCRIPTOR_CANONICAL_MISMATCH",
            "DESCRIPTOR_REFERENCE_MISMATCH",
            "RUN_ATTEMPT_MISMATCH",
        }
        _reject(code if code in allowed else "DESCRIPTOR_REFERENCE_MISMATCH")


def resolve_evidence_context(
    policy: AuthenticatedPolicy,
    attempt_root: Path | SafeRoot,
    run_id: str,
    attempt_id: str,
    external_anchor: ExternalAnchor,
    *,
    expected_terminal_manifest_identity: str,
) -> ResolvedEvidenceContext:
    """Resolve one sealed attempt without writing or reopening its acquired root."""

    identity = _policy_identity(policy)
    if (
        not isinstance(external_anchor, ExternalAnchor)
        or not isinstance(expected_terminal_manifest_identity, str)
        or _SHA256.fullmatch(expected_terminal_manifest_identity) is None
        or external_anchor.recorded_by != "IMMUTABLE_RUN_INDEX_OUTSIDE_ATTEMPT_ROOT"
        or external_anchor.cited_by_result_aggregation is not True
        or not external_anchor.attempt_root_identifier
    ):
        _reject("EXTERNAL_SEAL_MISSING")
    if expected_terminal_manifest_identity != external_anchor.terminal_manifest_sha256:
        _reject("EXTERNAL_MANIFEST_HASH_MISMATCH")
    owns_root = not isinstance(attempt_root, SafeRoot)
    try:
        fs = SafeRoot(Path(attempt_root)) if owns_root else attempt_root
    except Exception as error:
        _reject(str(error) if str(error).isupper() else "EVIDENCE_ROOT_INVALID")
    context: ResolvedEvidenceContext | None = None
    try:
        try:
            manifest_bytes = fs.read_bytes_bounded(
                external_anchor.manifest_relative_path,
                maximum_bytes=_MAXIMUM_MANIFEST_BYTES,
            )
        except JournalError:
            _reject("EXTERNAL_SEAL_MISSING")
        if sha256_hex(manifest_bytes) != expected_terminal_manifest_identity:
            _reject("EXTERNAL_MANIFEST_HASH_MISMATCH")
        try:
            rows = _parse_manifest(manifest_bytes)
        except Exception as error:
            _reject(str(error) if str(error).startswith("EXTERNAL_") else "EXTERNAL_MANIFEST_INVALID")
        row_hashes = dict(rows)
        paths = tuple(path for path, _digest in rows)
        journal_paths = tuple(path for path in paths if path.startswith("journal/"))
        descriptor_paths = tuple(path for path in paths if path.startswith("descriptors/"))
        payload_paths = tuple(path for path in paths if path.startswith("objects/"))
        terminal_paths = tuple(path for path in paths if path.startswith("terminal/"))
        if (
            journal_paths != ("journal/attempt.jsonl",)
            or any(path != "terminal/global-stop.json" for path in terminal_paths)
            or len(terminal_paths) > 1
            or len(paths)
            != len(journal_paths) + len(descriptor_paths) + len(payload_paths) + len(terminal_paths)
        ):
            _reject("EXTERNAL_MANIFEST_INVALID")
        actual = fs.list_all_regular_files()
        expected_files = tuple(
            sorted(paths + (external_anchor.manifest_relative_path,), key=lambda item: item.encode("utf-8"))
        )
        if actual != expected_files:
            _reject("EXTERNAL_MANIFEST_COVERAGE_MISMATCH")

        try:
            journal_bytes = fs.read_bytes_bounded(
                "journal/attempt.jsonl", maximum_bytes=_MAXIMUM_JOURNAL_BYTES
            )
        except JournalError as error:
            _reject(str(error))
        if sha256_hex(journal_bytes) != row_hashes["journal/attempt.jsonl"]:
            _reject("EXTERNAL_MANIFEST_COVERAGE_MISMATCH")
        try:
            with Journal(fs.root, policy, run_id, attempt_id, _safe_root=fs) as journal:
                journal_state = journal.reconstruct_bytes(journal_bytes)
        except Exception as error:
            code = str(error)
            _reject(code if code.isupper() else "JOURNAL_CHAIN_INVALID")
        if not journal_state.terminal:
            _reject("ATTEMPT_FINALITY_INVALID")
        frozen_records = tuple(_freeze(_thaw(record)) for record in journal_state.records)
        frozen_state = JournalState(
            frozen_records,
            journal_state.state,
            journal_state.sequence_number,
            journal_state.terminal,
            _freeze(_thaw(journal_state.evaluation_authorization_contexts)),
            _freeze(_thaw(journal_state.operation)) if journal_state.operation is not None else None,
        )

        maximum_descriptor = max(role.maximum_bytes for role in policy.roles.values())
        descriptors: dict[str, tuple[EvidenceRef, bytes, Mapping[str, Any]]] = {}
        for path in descriptor_paths:
            try:
                descriptor_bytes = fs.read_bytes_bounded(path, maximum_bytes=maximum_descriptor)
            except JournalError as error:
                _reject(str(error))
            if sha256_hex(descriptor_bytes) != row_hashes[path]:
                _reject("EXTERNAL_MANIFEST_COVERAGE_MISMATCH")
            try:
                descriptor = parse_canonical_json(descriptor_bytes)
            except Exception:
                _reject("DESCRIPTOR_CANONICAL_MISMATCH")
            if not isinstance(descriptor, dict):
                _reject("DESCRIPTOR_CANONICAL_MISMATCH")
            reference = _reference_from_descriptor(policy, descriptor, descriptor_bytes, path)
            if descriptor.get("run_id") != run_id or descriptor.get("attempt_id") != attempt_id:
                _reject("RUN_ATTEMPT_MISMATCH")
            if reference.evidence_id in descriptors:
                _reject("EVIDENCE_REFERENCE_INVALID")
            descriptors[reference.evidence_id] = (reference, descriptor_bytes, _freeze(descriptor))

        expected_payload_paths = {
            reference.payload_relative_path
            for reference, _descriptor_bytes, _descriptor in descriptors.values()
            if reference.payload_relative_path is not None
        }
        if expected_payload_paths != set(payload_paths):
            _reject("EXTERNAL_MANIFEST_COVERAGE_MISMATCH")
        resolved: dict[str, ResolvedEvidence] = {}
        for evidence_id, (reference, descriptor_bytes, descriptor) in descriptors.items():
            payload_bytes: bytes | None = None
            if reference.storage_class == "PAYLOAD_WITH_DESCRIPTOR":
                role = policy.role(reference.role)
                try:
                    payload_bytes = fs.read_bytes_bounded(
                        reference.payload_relative_path or "",
                        maximum_bytes=role.maximum_bytes,
                        exact_size=reference.payload_size_bytes,
                    )
                except JournalError:
                    _reject("PAYLOAD_SIZE_MISMATCH")
                if sha256_hex(payload_bytes) != reference.payload_sha256:
                    _reject("PAYLOAD_HASH_MISMATCH")
                if row_hashes.get(reference.payload_relative_path or "") != reference.payload_sha256:
                    _reject("EXTERNAL_MANIFEST_COVERAGE_MISMATCH")
            try:
                validated = validate_evidence_ref(
                    policy,
                    reference,
                    descriptor_bytes=descriptor_bytes,
                    payload_bytes=payload_bytes,
                    expected_run_id=run_id,
                    expected_attempt_id=attempt_id,
                )
            except Exception as error:
                code = str(error)
                _reject(code if code.isupper() else "EVIDENCE_REFERENCE_INVALID")
            publication = _publication_for(validated, frozen_records)
            resolved[evidence_id] = ResolvedEvidence(
                validated,
                bytes(descriptor_bytes),
                descriptor,
                None if payload_bytes is None else bytes(payload_bytes),
                publication,
            )

        stop_bytes: bytes | None = None
        stop: Mapping[str, Any] | None = None
        if terminal_paths:
            try:
                stop_bytes = fs.read_bytes_bounded(terminal_paths[0], maximum_bytes=1024 * 1024)
            except JournalError as error:
                _reject(str(error))
            if sha256_hex(stop_bytes) != row_hashes[terminal_paths[0]]:
                _reject("EXTERNAL_MANIFEST_COVERAGE_MISMATCH")
            try:
                parsed_stop = parse_canonical_json(stop_bytes)
                policy.structural_validate(parsed_stop)
            except Exception:
                _reject("ATTEMPT_FINALITY_INVALID")
            if (
                not isinstance(parsed_stop, dict)
                or parsed_stop.get("run_id") != run_id
                or parsed_stop.get("attempt_id") != attempt_id
            ):
                _reject("RUN_ATTEMPT_MISMATCH")
            stop = _freeze(parsed_stop)
        if (journal_state.state == "RESTORATION_BLOCKED") != (stop is not None):
            _reject("ATTEMPT_FINALITY_INVALID")
        classification = (
            "SEALED_PARTIAL_WITH_GLOBAL_STOP"
            if journal_state.state == "RESTORATION_BLOCKED"
            else "COMPLETE"
        )
        context = ResolvedEvidenceContext.__new__(
            ResolvedEvidenceContext, _resolver_token=_RESOLVER_TOKEN
        )
        values = {
            "policy_identity": identity,
            "run_id": run_id,
            "attempt_id": attempt_id,
            "attempt_root_identifier": external_anchor.attempt_root_identifier,
            "manifest_relative_path": external_anchor.manifest_relative_path,
            "terminal_manifest_sha256": expected_terminal_manifest_identity,
            "terminal_manifest_bytes": bytes(manifest_bytes),
            "manifest_rows": tuple(rows),
            "evidence": MappingProxyType(dict(resolved)),
            "journal_bytes": bytes(journal_bytes),
            "journal_records": frozen_records,
            "journal_state": frozen_state,
            "terminal_outcome": journal_state.state,
            "classification": classification,
            "terminal_global_stop_bytes": None if stop_bytes is None else bytes(stop_bytes),
            "terminal_global_stop": stop,
            "expected_operation_context": frozen_state.operation,
            "evaluation_authorization_contexts": _evaluation_authorization_contexts(
                policy, frozen_state
            ),
            "_authentication_marker": _RESOLVER_TOKEN,
            "_policy": policy,
        }
        for name, value in values.items():
            object.__setattr__(context, name, value)
        return context
    finally:
        if owns_root:
            # Resolution retains exact immutable bytes, not a filesystem capability.
            fs.close()

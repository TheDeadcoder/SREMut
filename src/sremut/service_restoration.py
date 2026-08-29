"""Pure derivation and authenticated validation of frozen Service restoration bodies."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
from types import MappingProxyType
from typing import Any, Mapping, NoReturn, Sequence

from jsonschema import Draft202012Validator

from sremut.canonical_json import canonical_json_bytes, parse_canonical_json
from sremut.sensitive import SensitiveCaptureError, detect_sensitive


SERVICE_RESTORATION_SOURCE = "SERVICE_RESTORATION_SOURCE_V1"
SERVICE_RESTORATION_BODY = "SERVICE_RESTORATION_BODY_V1"
DERIVATION_ALGORITHM = "KUBERNETES_SERVICE_CREATE_BODY_NORMALIZATION_V1"


@dataclass(slots=True)
class ServiceRestorationError(ValueError):
    """Stable non-sensitive restoration validation failure."""

    code: str

    def __str__(self) -> str:
        return self.code


def _reject(code: str) -> NoReturn:
    raise ServiceRestorationError(code) from None


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [_thaw(child) for child in value]
    if isinstance(value, list):
        return [_thaw(child) for child in value]
    return value


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(child) for key, child in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(child) for child in value)
    return value


def _reference_dict(reference: Any) -> dict[str, Any]:
    if hasattr(reference, "as_dict"):
        value = reference.as_dict()
    elif isinstance(reference, Mapping):
        value = _thaw(reference)
    else:
        _reject("RESTORATION_SOURCE_REFERENCE_MISSING")
    if not isinstance(value, dict):
        _reject("RESTORATION_SOURCE_REFERENCE_MISSING")
    return value


def _stripped_paths(policy: Any) -> tuple[tuple[str, ...], ...]:
    try:
        row = policy.policy["service_restoration_projection"]
        paths = tuple(tuple(item) for item in row["exact_stripped_field_token_paths"])
    except Exception:
        _reject("SERVICE_RESTORATION_BODY_INVALID")
    if not paths or any(not path or any(not isinstance(token, str) for token in path) for path in paths):
        _reject("SERVICE_RESTORATION_BODY_INVALID")
    return paths


def _remove_path(value: Any, tokens: Sequence[str]) -> None:
    if not tokens:
        return
    token = tokens[0]
    if token == "[]":
        if isinstance(value, list):
            for child in value:
                _remove_path(child, tokens[1:])
        return
    if not isinstance(value, dict) or token not in value:
        return
    if len(tokens) == 1:
        value.pop(token, None)
    else:
        _remove_path(value[token], tokens[1:])


def _path_present(value: Any, tokens: Sequence[str]) -> bool:
    if not tokens:
        return True
    token = tokens[0]
    if token == "[]":
        return isinstance(value, list) and any(_path_present(child, tokens[1:]) for child in value)
    return isinstance(value, Mapping) and token in value and _path_present(value[token], tokens[1:])


def _service_identity(source: Mapping[str, Any]) -> tuple[str, str]:
    if source.get("apiVersion") != "v1" or source.get("kind") != "Service":
        _reject("RESTORATION_SERVICE_MISMATCH")
    metadata = source.get("metadata")
    if not isinstance(metadata, Mapping) or metadata.get("name") != "user-service" or metadata.get("namespace") != "social-network":
        _reject("RESTORATION_SERVICE_MISMATCH")
    uid = metadata.get("uid")
    resource_version = metadata.get("resourceVersion")
    if not isinstance(uid, str) or not uid or not isinstance(resource_version, str) or not resource_version:
        _reject("RESTORATION_SERVICE_MISMATCH")
    return uid, resource_version


def _screen(value: Mapping[str, Any]) -> None:
    try:
        data = canonical_json_bytes(_thaw(value))
        if detect_sensitive(data, structured_format="json") is not None:
            _reject("SENSITIVE_CAPTURE_REJECTED")
    except ServiceRestorationError:
        raise
    except SensitiveCaptureError:
        _reject("SENSITIVE_CAPTURE_REJECTED")
    except Exception:
        _reject("SERVICE_RESTORATION_BODY_INVALID")


def _validate_create_body(policy: Any, body: Mapping[str, Any]) -> None:
    try:
        schema = _thaw(policy.schema)["$defs"]["service_create_body"]
        Draft202012Validator(schema).validate(_thaw(body))
    except Exception:
        _reject("SERVICE_RESTORATION_BODY_INVALID")


def _derive_document(
    policy: Any,
    source: Mapping[str, Any],
    source_request_reference: Any,
    capture_timestamp: str,
    source_service_reference: Any,
) -> dict[str, Any]:
    source_value = _thaw(source)
    if not isinstance(source_value, dict):
        _reject("RESTORATION_SOURCE_UNRESOLVED")
    uid, resource_version = _service_identity(source_value)
    _screen(source_value)
    request_ref = _reference_dict(source_request_reference)
    source_ref = _reference_dict(source_service_reference)
    if request_ref.get("role") != "kubernetes_request_identity":
        _reject("RESTORATION_SOURCE_REFERENCE_MISSING")
    if source_ref.get("role") != "kubernetes_object_projection" or source_ref.get("projection_class") != SERVICE_RESTORATION_SOURCE:
        _reject("RESTORATION_SOURCE_CLASS_INVALID")
    normalized = deepcopy(source_value)
    paths = _stripped_paths(policy)
    for path in paths:
        _remove_path(normalized, path)
    _validate_create_body(policy, normalized)
    _screen(normalized)
    body_bytes = canonical_json_bytes(normalized)
    try:
        round_trip = parse_canonical_json(body_bytes)
    except Exception:
        _reject("SERVICE_RESTORATION_BODY_INVALID")
    if canonical_json_bytes(round_trip) != body_bytes:
        _reject("SERVICE_RESTORATION_BODY_INVALID")
    digest = hashlib.sha256(body_bytes).hexdigest()
    return {
        "document_type": SERVICE_RESTORATION_BODY,
        "schema_version": 1,
        "projection_class": SERVICE_RESTORATION_BODY,
        "derivation_algorithm": DERIVATION_ALGORITHM,
        "source_service_uid": uid,
        "source_service_resource_version": resource_version,
        "exact_stripped_field_token_paths": [list(path) for path in paths],
        "normalized_body_sha256": digest,
        "canonical_json_identity": digest,
        "capture_timestamp": capture_timestamp,
        "source_request_reference": request_ref,
        "source_service_reference": source_ref,
        "normalized_service_create_body": round_trip,
    }


def derive_service_restoration_body_preseal(
    store: Any,
    source_service_reference: Any,
    source_request_reference: Any,
    capture_timestamp: str,
) -> Mapping[str, Any]:
    """Derive the restoration body BEFORE the attempt is sealed.

    `derive_service_restoration_body` authenticates a SEALED attempt through a
    `ResolvedEvidenceContext`, which cannot exist until the terminal manifest is
    written.  The live MS-M01 lifecycle needs the body *before* the Service is
    deleted, so this is the pre-seal counterpart: it takes an authenticated
    pre-seal `EvidenceStore` and derives the body only from bytes that store has
    already retained.

    The caller supplies REFERENCES, never material.  A raw Service mapping is
    not accepted and cannot be smuggled in: the projection payload is read back
    from the store and byte-authenticated by `EvidenceStore.resolve`, which
    re-checks descriptor identity, payload hash and size, and the run/attempt
    binding.  Stripping, `service_create_body` schema validation, sensitive
    screening and the canonical hash rules are the same `_derive_document` the
    sealed path uses; that function stays private.
    """
    from sremut.evidence import EvidenceStore

    if not isinstance(store, EvidenceStore):
        _reject("RESTORATION_SOURCE_UNRESOLVED")
    policy = store.policy
    try:
        source_resolved = store.resolve(source_service_reference)
        request_resolved = store.resolve(source_request_reference)
    except Exception:
        _reject("RESTORATION_SOURCE_UNRESOLVED")
    if source_resolved is None or request_resolved is None:
        _reject("RESTORATION_SOURCE_UNRESOLVED")
    source_ref, source_descriptor_bytes, source_payload = source_resolved
    request_ref, _request_descriptor, _request_payload = request_resolved
    if (
        source_ref.role != "kubernetes_object_projection"
        or source_ref.projection_class != SERVICE_RESTORATION_SOURCE
    ):
        _reject("RESTORATION_SOURCE_CLASS_INVALID")
    if request_ref.role != "kubernetes_request_identity":
        _reject("RESTORATION_SOURCE_REFERENCE_MISSING")
    # The Service projection was captured by ONE request, and its retained
    # descriptor says which.  The caller does not get to nominate a different
    # request: any other request descriptor -- even a valid, same-run, same-role
    # one -- would attribute this body to a capture that did not produce it.
    try:
        source_descriptor = parse_canonical_json(source_descriptor_bytes)
    except Exception:
        _reject("RESTORATION_SOURCE_UNRESOLVED")
    cited_request = source_descriptor.get("request_identity_reference")
    if not isinstance(cited_request, Mapping):
        _reject("RESTORATION_SOURCE_REFERENCE_MISSING")
    if _thaw(cited_request) != request_ref.as_dict():
        _reject("KUBERNETES_PROJECTION_REQUEST_MISMATCH")
    if source_payload is None:
        _reject("PAYLOAD_BYTES_MISSING")
    # The run/attempt binding is already enforced inside `EvidenceStore.resolve`,
    # which passes expected_run_id/expected_attempt_id to `validate_evidence_ref`
    # and rejects a foreign reference with RUN_ATTEMPT_MISMATCH before returning.
    if hashlib.sha256(source_payload).hexdigest() != source_ref.payload_sha256:
        _reject("RESTORATION_BODY_HASH_MISMATCH")
    try:
        source = parse_canonical_json(source_payload)
    except Exception:
        _reject("RESTORATION_SOURCE_UNRESOLVED")
    if not isinstance(source, dict):
        _reject("RESTORATION_SOURCE_UNRESOLVED")
    return _freeze(
        _derive_document(
            policy,
            source,
            request_ref.as_dict(),
            capture_timestamp,
            source_ref.as_dict(),
        )
    )


def derive_service_restoration_body(
    context: Any,
    source_service_reference: Any,
    source_request_reference: Any,
    capture_timestamp: str,
) -> Mapping[str, Any]:
    """Derive a body only from a byte-authenticated resolved Service projection."""

    from sremut.resolved_context import ResolvedEvidenceContext

    if not isinstance(context, ResolvedEvidenceContext):
        _reject("RESTORATION_SOURCE_UNRESOLVED")
    try:
        row = context.resolve_reference(source_service_reference)
    except Exception:
        _reject("RESTORATION_SOURCE_UNRESOLVED")
    if row.reference.role != "kubernetes_object_projection" or row.reference.projection_class != SERVICE_RESTORATION_SOURCE or row.payload_bytes is None:
        _reject("RESTORATION_SOURCE_CLASS_INVALID")
    if row.descriptor.get("projection_schema_id") != SERVICE_RESTORATION_SOURCE:
        _reject("RESTORATION_SOURCE_CLASS_INVALID")
    try:
        source = parse_canonical_json(row.payload_bytes)
    except Exception:
        _reject("RESTORATION_SOURCE_UNRESOLVED")
    document = _derive_document(
        context._policy,
        source,
        source_request_reference,
        capture_timestamp,
        row.reference,
    )
    return _freeze(document)


def _resolved_row(context: Any, reference: Any, *, role: str | None = None, payload: bool | None = None, unresolved: str = "EVIDENCE_REFERENCE_UNRESOLVED") -> Any:
    try:
        row = context.resolve_reference(reference)
    except Exception:
        _reject(unresolved)
    if role is not None and row.reference.role != role:
        _reject("RESTORATION_SOURCE_CLASS_INVALID" if role == "kubernetes_object_projection" else "MUTATION_RECEIPT_REQUEST_MISMATCH")
    if payload is True and row.payload_bytes is None:
        _reject("PAYLOAD_BYTES_MISSING")
    if payload is False and row.payload_bytes is not None:
        _reject("SERVICE_RESTORATION_BODY_INVALID")
    return row


def _validate_document(context: Any, document: Mapping[str, Any], body_row: Any | None = None) -> Any:
    try:
        context._policy.structural_validate(_thaw(document))
    except Exception:
        _reject("SERVICE_RESTORATION_BODY_INVALID")
    if tuple(tuple(path) for path in document.get("exact_stripped_field_token_paths", ())) != _stripped_paths(context._policy):
        _reject("RESTORATION_DERIVATION_MISMATCH")
    normalized = document.get("normalized_service_create_body")
    if not isinstance(normalized, Mapping):
        _reject("SERVICE_RESTORATION_BODY_INVALID")
    body_bytes = canonical_json_bytes(_thaw(normalized))
    digest = hashlib.sha256(body_bytes).hexdigest()
    if document.get("normalized_body_sha256") != digest or document.get("canonical_json_identity") != digest:
        _reject("RESTORATION_BODY_HASH_MISMATCH")
    if any(_path_present(normalized, path) for path in _stripped_paths(context._policy)):
        _reject("RESTORATION_DERIVATION_MISMATCH")
    _validate_create_body(context._policy, normalized)
    _screen(normalized)
    source_ref = document.get("source_service_reference")
    if not isinstance(source_ref, Mapping):
        _reject("RESTORATION_SOURCE_REFERENCE_MISSING")
    source_row = _resolved_row(
        context,
        source_ref,
        role="kubernetes_object_projection",
        payload=True,
        unresolved="RESTORATION_SOURCE_UNRESOLVED",
    )
    if source_row.reference.projection_class != SERVICE_RESTORATION_SOURCE or source_row.descriptor.get("projection_schema_id") != SERVICE_RESTORATION_SOURCE:
        _reject("RESTORATION_SOURCE_CLASS_INVALID")
    try:
        source = parse_canonical_json(source_row.payload_bytes)
    except Exception:
        _reject("RESTORATION_SOURCE_UNRESOLVED")
    expected = _derive_document(
        context._policy,
        source,
        document.get("source_request_reference"),
        document.get("capture_timestamp"),
        source_row.reference,
    )
    if canonical_json_bytes(expected) != canonical_json_bytes(_thaw(document)):
        _reject("RESTORATION_DERIVATION_MISMATCH")
    if body_row is not None and source_row.publication.sequence_number >= body_row.publication.sequence_number:
        _reject("MUTATION_RECEIPT_PUBLICATION_INVALID")
    return source_row


def _body_from_reference(context: Any, reference: Any) -> tuple[Any, Mapping[str, Any]]:
    row = _resolved_row(
        context,
        reference,
        role="kubernetes_object_projection",
        payload=True,
        unresolved="RESTORATION_BODY_UNRESOLVED",
    )
    if row.reference.projection_class != SERVICE_RESTORATION_BODY:
        _reject("RESTORATION_BODY_UNRESOLVED")
    try:
        document = parse_canonical_json(row.payload_bytes)
    except Exception:
        _reject("RESTORATION_BODY_HASH_MISMATCH")
    if not isinstance(document, dict):
        _reject("SERVICE_RESTORATION_BODY_INVALID")
    _validate_document(context, document, row)
    return row, document


def _operation_markers(context: Any) -> tuple[tuple[int, Mapping[str, Any]], ...]:
    rows: list[tuple[int, Mapping[str, Any]]] = []
    for record in context.journal_records:
        transition = record.get("transition")
        if not isinstance(transition, str) or not transition.startswith("OPERATION_AUTHORIZED:"):
            continue
        try:
            raw = bytes.fromhex(transition.split(":", 1)[1])
            operation = parse_canonical_json(raw)
        except Exception:
            _reject("JOURNAL_CONTEXT_MISMATCH")
        if not isinstance(operation, dict) or canonical_json_bytes(operation) != raw:
            _reject("JOURNAL_CONTEXT_MISMATCH")
        rows.append((record["sequence_number"], operation))
    return tuple(rows)


def _validate_intent(context: Any, candidate: Mapping[str, Any]) -> None:
    operation = candidate.get("operation_kind")
    if candidate.get("resource_kind") != "Service" or operation not in {
        "INITIAL_USER_SERVICE_DELETION",
        "MUTANT_SERVICE_DELETION",
        "MUTANT_SERVICE_CREATION",
        "RESTORED_SERVICE_CREATION",
    }:
        return
    if candidate.get("namespace") != "social-network" or candidate.get("object_name") != "user-service":
        _reject("RESTORATION_SERVICE_MISMATCH")
    reference = candidate.get("service_restoration_body_reference")
    if not isinstance(reference, Mapping):
        _reject("RESTORATION_SOURCE_REFERENCE_MISSING")
    body, document = _body_from_reference(context, reference)
    if candidate.get("service_restoration_body_sha256") != body.reference.payload_sha256:
        _reject("RESTORATION_BODY_HASH_MISMATCH")
    if candidate.get("expected_uid_when_existing") != document.get("source_service_uid") or candidate.get("expected_resource_version_when_applicable") != document.get("source_service_resource_version"):
        _reject("RESTORATION_SERVICE_MISMATCH")
    intent = context._candidate_evidence(candidate)
    if body.publication.sequence_number >= intent.publication.sequence_number:
        _reject("MUTATION_RECEIPT_PUBLICATION_INVALID")


def _validate_receipt(context: Any, candidate: Mapping[str, Any]) -> None:
    receipt = context._candidate_evidence(candidate)
    if candidate.get("run_id") != context.run_id or candidate.get("attempt_id") != context.attempt_id:
        _reject("RUN_ATTEMPT_MISMATCH")
    body_ref = candidate.get("service_restoration_body_reference")
    body_hash = candidate.get("service_restoration_body_sha256")
    if body_ref is None and body_hash is None:
        return
    if not isinstance(body_ref, Mapping) or not isinstance(body_hash, str):
        _reject("MUTATION_RECEIPT_IDENTITY_INVALID")
    body, _document = _body_from_reference(context, body_ref)
    if body.reference.payload_sha256 != body_hash:
        _reject("RESTORATION_BODY_HASH_MISMATCH")
    request = _resolved_row(context, candidate.get("operation_request_reference"), role="kubernetes_request_identity", payload=False, unresolved="MUTATION_RECEIPT_REQUEST_MISMATCH")
    request_doc = request.descriptor.get("canonical_request")
    if not isinstance(request_doc, Mapping) or request_doc.get("operation") != "CREATE" or request_doc.get("resource") != "services" or request_doc.get("name") != "user-service" or request_doc.get("namespace") != "social-network":
        _reject("MUTATION_RECEIPT_REQUEST_MISMATCH")
    observation = _resolved_row(context, candidate.get("post_create_observation_reference"), role="kubernetes_object_projection", payload=True, unresolved="RESTORATION_BODY_UNRESOLVED")
    markers = [row for row in _operation_markers(context) if row[1].get("request_reference") == request.reference.as_dict()]
    if len(markers) != 1:
        _reject("MUTATION_RECEIPT_PUBLICATION_INVALID")
    sequence, operation = markers[0]
    if operation.get("request_body_reference") != body.reference.as_dict() or not (body.publication.sequence_number < sequence < receipt.publication.sequence_number) or observation.publication.sequence_number >= receipt.publication.sequence_number:
        _reject("MUTATION_RECEIPT_PUBLICATION_INVALID")
    terminal_states = frozenset(context._policy.policy["verified_attempt_state_machine"]["terminal_states"])
    terminal_sequence = None
    for record in context.journal_records:
        transition = record.get("transition")
        if not isinstance(transition, str):
            continue
        target = transition.split("->", 1)[1] if "->" in transition else (
            transition.split(":", 1)[1] if transition.startswith("STATE_VERIFIED:") else None
        )
        if target in terminal_states:
            terminal_sequence = record.get("sequence_number")
            break
    if not isinstance(terminal_sequence, int) or receipt.publication.sequence_number >= terminal_sequence:
        _reject("MUTATION_RECEIPT_PUBLICATION_INVALID")


def validate_resolved_service_restoration(context: Any, candidate: Mapping[str, Any]) -> None:
    """Frozen final-hook implementation over authenticated retained bytes only."""

    kind = candidate.get("document_type")
    role = candidate.get("role")
    if kind == SERVICE_RESTORATION_BODY:
        matches = []
        for row in context.evidence.values():
            if row.reference.projection_class != SERVICE_RESTORATION_BODY or row.payload_bytes is None:
                continue
            try:
                if parse_canonical_json(row.payload_bytes) == _thaw(candidate):
                    matches.append(row)
            except Exception:
                continue
        if len(matches) != 1:
            _reject("RESTORATION_BODY_UNRESOLVED")
        _validate_document(context, candidate, matches[0])
        return
    if kind == "PAYLOAD_EVIDENCE_DESCRIPTOR_V1" and role == "kubernetes_object_projection":
        row = context._candidate_evidence(candidate)
        if row.reference.projection_class == SERVICE_RESTORATION_BODY:
            if row.payload_bytes is None:
                _reject("RESTORATION_BODY_UNRESOLVED")
            try:
                document = parse_canonical_json(row.payload_bytes)
            except Exception:
                _reject("RESTORATION_BODY_HASH_MISMATCH")
            _validate_document(context, document, row)
        elif row.reference.projection_class == SERVICE_RESTORATION_SOURCE:
            if row.payload_bytes is None:
                _reject("RESTORATION_SOURCE_UNRESOLVED")
            try:
                source = parse_canonical_json(row.payload_bytes)
            except Exception:
                _reject("RESTORATION_SOURCE_UNRESOLVED")
            _service_identity(source)
            _screen(source)
        return
    if kind == "DESCRIPTOR_EVIDENCE_DESCRIPTOR_V1" and role == "mutation_intent":
        _validate_intent(context, candidate)
        return
    if kind == "DESCRIPTOR_EVIDENCE_DESCRIPTOR_V1" and role == "mutation_receipt":
        _validate_receipt(context, candidate)
        return
    if kind == "KUBERNETES_REQUEST_V1" and candidate.get("resource") == "services" and candidate.get("operation") in ("CREATE", "DELETE"):
        intent = _resolved_row(context, candidate.get("intent_reference"), role="mutation_intent", payload=False, unresolved="RESTORATION_INTENT_UNRESOLVED")
        _validate_intent(context, _thaw(intent.descriptor))

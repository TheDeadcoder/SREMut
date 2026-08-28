"""Authenticated raw-backing validation for frozen adjudication evidence."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any, Mapping, NoReturn

from sremut.canonical_json import canonical_json_bytes, parse_canonical_json


@dataclass(slots=True)
class AdjudicationError(ValueError):
    """Stable non-sensitive adjudication validation failure."""

    code: str

    def __str__(self) -> str:
        return self.code


def _reject(code: str) -> NoReturn:
    raise AdjudicationError(code) from None


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [_thaw(child) for child in value]
    if isinstance(value, list):
        return [_thaw(child) for child in value]
    return value


def _evaluation_marker_sequence(context: Any, predicate: str) -> int:
    matches = []
    for record in context.journal_records:
        if record.get("transition") == f"EVALUATION_AUTHORIZED:{predicate}":
            matches.append(record.get("sequence_number"))
    if len(matches) != 1 or not isinstance(matches[0], int):
        _reject("JOURNAL_EVALUATION_MARKER_MISSING")
    return matches[0]


def _authorization_context(context: Any, predicate: Any) -> Mapping[str, Any]:
    """Select the authorization context belonging to this candidate's predicate."""
    contexts = getattr(context, "evaluation_authorization_contexts", None)
    if not isinstance(contexts, Mapping):
        _reject("JOURNAL_EVALUATION_MARKER_MISSING")
    if not isinstance(predicate, str) or predicate not in contexts:
        _reject("JOURNAL_EVALUATION_MARKER_MISSING")
    row = contexts[predicate]
    if not isinstance(row, Mapping):
        _reject("JOURNAL_EVALUATION_MARKER_MISSING")
    return row


def _matrix_row(context: Any, predicate: Any) -> Mapping[str, Any]:
    matrix = context._policy.policy["full_admissibility_validation"][
        "adjudication_predicate_raw_role_context_deadline_matrix"
    ]
    row = matrix.get(predicate) if isinstance(predicate, str) else None
    if not isinstance(row, Mapping):
        _reject("ADJUDICATION_EVALUATION_PHASE_MISMATCH")
    return row


def _resolve_payload(context: Any, reference: Any) -> Any:
    try:
        row = context.resolve_reference(reference)
    except Exception:
        _reject("EVIDENCE_REFERENCE_UNRESOLVED")
    if row.payload_bytes is None:
        _reject("PAYLOAD_BYTES_MISSING")
    if hashlib.sha256(row.payload_bytes).hexdigest() != row.reference.payload_sha256:
        _reject("PAYLOAD_HASH_MISMATCH")
    if len(row.payload_bytes) != row.reference.payload_size_bytes:
        _reject("PAYLOAD_SIZE_MISMATCH")
    return row


def _workload_boolean(context: Any, candidate: Mapping[str, Any], rows: tuple[Any, ...]) -> bool | None:
    parse_rows = tuple(row for row in rows if row.reference.role == "workload_parse_result")
    if not parse_rows:
        return None
    if len(parse_rows) != 1:
        _reject("ADJUDICATION_RAW_ROLE_INVALID")
    try:
        from sremut.workload_evidence import (
            WorkloadEvidenceError,
            validate_resolved_workload_cardinality,
            validate_resolved_workload_window,
        )

        validate_resolved_workload_cardinality(context, candidate)
        validate_resolved_workload_window(context, candidate)
        parsed = parse_canonical_json(parse_rows[0].payload_bytes)
    except AdjudicationError:
        raise
    except WorkloadEvidenceError as error:
        _reject(error.code)
    except Exception:
        _reject("ADJUDICATION_RAW_ROLE_INVALID")
    fresh = parsed.get("fresh_request_count") if isinstance(parsed, dict) else None
    failures = parsed.get("failure_marker_count") if isinstance(parsed, dict) else None
    if not isinstance(fresh, int) or isinstance(fresh, bool) or not isinstance(failures, int) or isinstance(failures, bool):
        _reject("ADJUDICATION_RAW_ROLE_INVALID")
    return fresh >= 50 and failures == 0


def _original_oracle_value(rows: tuple[Any, ...]) -> bool | None:
    result_rows = tuple(row for row in rows if row.reference.role == "original_oracle_result")
    if not result_rows:
        return None
    if len(result_rows) != 1:
        _reject("ADJUDICATION_RAW_ROLE_INVALID")
    try:
        value = parse_canonical_json(result_rows[0].payload_bytes)
    except Exception:
        _reject("ADJUDICATION_RAW_ROLE_INVALID")
    outcome = value.get("outcome") if isinstance(value, dict) else None
    returned = value.get("returned_boolean") if isinstance(value, dict) else None
    if outcome == "RETURNED_TRUE" and returned is True:
        return True
    if outcome == "RETURNED_FALSE" and returned is False:
        return False
    if outcome in {
        "ORACLE_EXCEPTION", "TIMEOUT", "INTERRUPTED", "NONZERO_EXIT",
        "INVALID_WORKER_OUTPUT", "ORIGINAL_ORACLE_RETURN_SHAPE_INVALID",
    } and returned is None:
        _reject("ADJUDICATION_RAW_ROLE_INVALID")
    _reject("ADJUDICATION_RAW_ROLE_INVALID")


def recompute_adjudication(context: Any, candidate: Mapping[str, Any], rows: tuple[Any, ...]) -> Any:
    """Recompute supported Boolean outcomes; never coerce caller values."""

    if candidate.get("result_type") != "BOOLEAN":
        if candidate.get("result_type") != "CATEGORICAL":
            _reject("ADJUDICATION_RAW_ROLE_INVALID")
        # The frozen three evaluation predicates define Boolean workload
        # decisions. A categorical value has no closed recomputation rule here.
        _reject("ADJUDICATION_RAW_ROLE_INVALID")
    workload = _workload_boolean(context, candidate, rows)
    oracle = _original_oracle_value(rows)
    values = tuple(value for value in (workload, oracle) if value is not None)
    if not values:
        # No frozen coercion exists for observation-only evidence. A caller
        # supplied Boolean is therefore never authoritative.
        _reject("ADJUDICATION_RAW_ROLE_INVALID")
    if len(set(values)) != 1:
        _reject("ADJUDICATION_RAW_ROLE_INVALID")
    return values[0]


def validate_adjudication_descriptor(context: Any, candidate: Mapping[str, Any]) -> None:
    if candidate.get("role") != "adjudication":
        _reject("ADJUDICATION_RAW_ROLE_INVALID")
    try:
        candidate_row = context._candidate_evidence(candidate)
    except Exception:
        _reject("EVIDENCE_REFERENCE_UNRESOLVED")
    if candidate.get("run_id") != context.run_id or candidate.get("attempt_id") != context.attempt_id:
        _reject("RUN_ATTEMPT_MISMATCH")
    # The context is selected by the CANDIDATE's own predicate.  There is no
    # fallback to a last marker: under v1.1 that fallback made every adjudication
    # except the last one unverifiable in its own sealed attempt.
    predicate = candidate.get("predicate_oracle_or_classification_id")
    expected = _authorization_context(context, predicate)
    matrix = _matrix_row(context, predicate)
    if predicate != expected.get("predicate_id") or matrix.get("phase") != expected.get("phase"):
        _reject("ADJUDICATION_EVALUATION_PHASE_MISMATCH")
    if candidate.get("applicable_deadline") != expected.get("deadline_identity") or matrix.get("deadline_identity") != expected.get("deadline_identity"):
        _reject("ADJUDICATION_DEADLINE_MISMATCH")
    marker = _evaluation_marker_sequence(context, predicate)
    references = candidate.get("raw_evidence_references")
    hashes = candidate.get("raw_evidence_sha256_per_reference")
    if not isinstance(references, (list, tuple)) or not references or not isinstance(hashes, (list, tuple)) or len(references) != len(hashes):
        _reject("ADJUDICATION_RAW_REFERENCE_REQUIRED")
    allowed_roles = set(matrix.get("allowed_raw_roles", ()))
    rows = []
    seen = set()
    for index, reference in enumerate(references):
        row = _resolve_payload(context, reference)
        if row.reference.evidence_id in seen or row.reference.role not in allowed_roles:
            _reject("ADJUDICATION_RAW_ROLE_INVALID")
        seen.add(row.reference.evidence_id)
        if hashes[index] != row.reference.payload_sha256:
            _reject("PAYLOAD_HASH_MISMATCH")
        if not (marker < row.publication.sequence_number < candidate_row.publication.sequence_number):
            _reject("PUBLICATION_ORDER_INVALID")
        rows.append(row)
    uid_refs = candidate.get("kubernetes_uid_and_resource_version_references_when_applicable")
    if not isinstance(uid_refs, (list, tuple)):
        _reject("ADJUDICATION_RAW_REFERENCE_REQUIRED")
    for reference in uid_refs:
        row = _resolve_payload(context, reference)
        if row.reference.role != "kubernetes_object_projection" or row.reference.evidence_id not in seen:
            _reject("ADJUDICATION_RAW_ROLE_INVALID")
    expected_value = recompute_adjudication(context, candidate, tuple(rows))
    if type(candidate.get("boolean_or_categorical_value")) is not type(expected_value) or candidate.get("boolean_or_categorical_value") != expected_value:
        _reject("ADJUDICATION_RAW_ROLE_INVALID")


def _closure_reject(context: Any) -> NoReturn:
    """Fail a closure defect with a code the AUTHENTICATED policy actually defines.

    v1.2 adds the precise `ADJUDICATION_CLOSURE_INCOMPLETE`; v1.1 does not define
    it, so under a v1.1 binding the closure defect is reported with the code v1.1
    does define.  A validator must never emit a code outside its own policy's
    vocabulary.
    """
    try:
        vocabulary = set(
            context._policy.policy["full_admissibility_validation"]["failure_codes"]
        )
    except Exception:
        vocabulary = set()
    if "ADJUDICATION_CLOSURE_INCOMPLETE" in vocabulary:
        _reject("ADJUDICATION_CLOSURE_INCOMPLETE")
    _reject("ADJUDICATION_RAW_REFERENCE_REQUIRED")


def _required_predicates(context: Any) -> set[str]:
    """Which predicates this terminal outcome must carry exactly one adjudication for.

    FINALIZED reached every phase, so all three frozen predicates are required.
    ABORTED_SAFE and RESTORATION_BLOCKED stop early, so they require exactly the
    predicates whose authorization markers the attempt actually reached -- no
    evidence is demanded for a phase that was never legally entered, and none may
    be smuggled in for one that was not.
    """
    contexts = getattr(context, "evaluation_authorization_contexts", None)
    if not isinstance(contexts, Mapping):
        _reject("JOURNAL_EVALUATION_MARKER_MISSING")
    if context.terminal_outcome == "FINALIZED":
        matrix = context._policy.policy["full_admissibility_validation"][
            "adjudication_predicate_raw_role_context_deadline_matrix"
        ]
        return set(matrix)
    return set(contexts)


def validate_attempt_adjudication_closure(context: Any, candidate: Mapping[str, Any]) -> None:
    references = candidate.get("adjudication_references")
    raw_references = candidate.get("raw_evidence_references")
    if not isinstance(references, (list, tuple)) or not references or not isinstance(raw_references, (list, tuple)) or not raw_references:
        _reject("ADJUDICATION_RAW_REFERENCE_REQUIRED")
    adjudications = []
    cited_ids: list[str] = []
    predicates: list[str] = []
    for reference in references:
        try:
            row = context.resolve_reference(reference)
        except Exception:
            _reject("EVIDENCE_REFERENCE_UNRESOLVED")
        if row.reference.role != "adjudication" or row.payload_bytes is not None:
            _reject("ADJUDICATION_RAW_ROLE_INVALID")
        adjudications.append(row)
        cited_ids.append(row.reference.evidence_id)
        # Every cited adjudication is revalidated, against its OWN predicate's
        # authorization context.
        validate_adjudication_descriptor(context, _thaw(row.descriptor))
        predicates.append(
            row.descriptor.get("predicate_oracle_or_classification_id")
        )
    if len(set(cited_ids)) != len(cited_ids):
        _closure_reject(context)
    # A duplicate predicate is rejected even when the evidence ids differ: two
    # distinct descriptors adjudicating the same predicate is not a closure.
    if len(set(predicates)) != len(predicates):
        _closure_reject(context)
    if set(predicates) != _required_predicates(context):
        _closure_reject(context)
    cited = {
        raw.get("evidence_id")
        for row in adjudications
        for raw in row.descriptor.get("raw_evidence_references", ())
        if isinstance(raw, Mapping)
    }
    envelope_ids: list[str] = []
    for reference in raw_references:
        row = _resolve_payload(context, reference)
        envelope_ids.append(row.reference.evidence_id)
    if len(set(envelope_ids)) != len(envelope_ids):
        _closure_reject(context)
    if set(envelope_ids) != cited:
        _reject("ADJUDICATION_RAW_REFERENCE_REQUIRED")
    if candidate.get("terminal_outcome") != context.terminal_outcome:
        _reject("RUN_ATTEMPT_MISMATCH")
    if context.terminal_outcome == "RESTORATION_BLOCKED":
        stop = context.terminal_global_stop
        if not isinstance(stop, Mapping):
            _reject("ADJUDICATION_RAW_REFERENCE_REQUIRED")
        stop_ref = stop.get("adjudication_reference")
        try:
            stop_row = context.resolve_reference(stop_ref)
        except Exception:
            _reject("EVIDENCE_REFERENCE_UNRESOLVED")
        if stop_row.reference.evidence_id not in {row.reference.evidence_id for row in adjudications}:
            _reject("ADJUDICATION_RAW_REFERENCE_REQUIRED")


def validate_resolved_adjudication(context: Any, candidate: Mapping[str, Any]) -> None:
    """Frozen final-hook implementation over authenticated retained bytes only."""

    if candidate.get("role") == "adjudication":
        validate_adjudication_descriptor(context, candidate)
    elif candidate.get("document_type") == "ATTEMPT_VALIDATION_ENVELOPE_V1":
        validate_attempt_adjudication_closure(context, candidate)

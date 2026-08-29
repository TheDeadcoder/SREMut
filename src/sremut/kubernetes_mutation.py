"""Offline-safe guarded Kubernetes mutation primitives for the frozen M01 lifecycle.

The module has no Kubernetes import.  A future orchestrator must inject the narrow
transport, authenticated policy, read-session identity, attempt roots, and a
verified runner release.  This slice never observes or reconciles by itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib
from pathlib import Path
import re
import time
from types import MappingProxyType
from typing import Any, Callable, Mapping, NoReturn, Protocol, runtime_checkable

from sremut.canonical_json import canonical_json_bytes, canonical_json_line, parse_canonical_json
from sremut.evidence import EvidenceRef, EvidenceStore, ZERO_SHA256
from sremut.journal import Journal, SafeRoot
from sremut.kubernetes_readonly import (
    CapturedObjectIdentity,
    KubernetesCapture,
    ReadOnlyKubernetesClient,
    validate_prepublication_capture,
    validate_kubernetes_list_response,
)
from sremut.policy_runtime import AuthenticatedPolicy
from sremut.sensitive import detect_sensitive
from sremut.service_restoration import (
    SERVICE_RESTORATION_BODY,
    SERVICE_RESTORATION_SOURCE,
    _derive_document,
)


NAMESPACE = "social-network"
SERVICE_NAME = "user-service"
MUTANT = "MS-M01"
MUTATION_LEDGER = "journal/mutation-events.jsonl"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RUN_ID = re.compile(r"^sremut-ms-m01-r0[1-3]-a0[1-2]-[0-9a-f]{12}$")
_ATTEMPT_ID = re.compile(r"^a0[1-2]$")
_BOOT_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
_TOKEN = object()
_BUNDLE_TOKEN = object()
_RESTORATION_TOKEN = object()
_POD_TOKEN = object()
_CHALLENGE_TOKEN = object()

OPERATION_TABLE = MappingProxyType(
    {
        "INITIAL_USER_SERVICE_DELETION": MappingProxyType(
            {
                "kind": "Service", "namespace": NAMESPACE, "name_rule": "user-service",
                "method": "CoreV1Api.delete_namespaced_service",
                "consumer": "MUTATION_CONTROLLER", "states": ("HEALTHY_STATE_CAPTURED",),
                "body": "V1DeleteOptions(uid,resourceVersion)",
            }
        ),
        "INITIAL_CAPTURED_POD_RECYCLE_DELETE": MappingProxyType(
            {
                "kind": "Pod", "namespace": NAMESPACE, "name_rule": "captured Pod",
                "method": "CoreV1Api.delete_namespaced_pod",
                "consumer": "MUTATION_CONTROLLER", "states": ("HEALTHY_STATE_CAPTURED",),
                "body": "V1DeleteOptions(uid,resourceVersion)",
            }
        ),
        "MUTANT_SERVICE_CREATION": MappingProxyType(
            {
                "kind": "Service", "namespace": NAMESPACE, "name_rule": "user-service",
                "method": "CoreV1Api.create_namespaced_service",
                "consumer": "MUTATION_CONTROLLER", "states": ("HEALTHY_STATE_CAPTURED",),
                "body": "frozen mutant Service body",
            }
        ),
        "CHALLENGE_POD_CREATION": MappingProxyType(
            {
                "kind": "Pod", "namespace": NAMESPACE, "name_rule": "deterministic challenge",
                "method": "CoreV1Api.create_namespaced_pod",
                "consumer": "CONTRACT_EVALUATOR", "states": ("ORIGINAL_ORACLE_EVALUATED",),
                "body": "frozen challenge Pod",
            }
        ),
        "REPLACEMENT_POD_DELETION": MappingProxyType(
            {
                "kind": "Pod", "namespace": NAMESPACE, "name_rule": "captured replacement Pod",
                "method": "CoreV1Api.delete_namespaced_pod",
                # The replacement deletion belongs to REPLACEMENT_PERSISTENCE_EVALUATION,
                # whose evidence-policy authorization state is CONTRACT_EVALUATED.  The
                # deletion must therefore be dispatchable exactly there: dispatching it
                # in ORIGINAL_ORACLE_EVALUATED would publish the persistence window's raw
                # evidence before that predicate's EVALUATION_AUTHORIZED marker.
                "consumer": "CONTRACT_EVALUATOR", "states": ("CONTRACT_EVALUATED",),
                "body": "V1DeleteOptions(uid,resourceVersion)",
            }
        ),
        "CHALLENGE_POD_DELETION": MappingProxyType(
            {
                "kind": "Pod", "namespace": NAMESPACE, "name_rule": "deterministic challenge",
                "method": "CoreV1Api.delete_namespaced_pod",
                "consumer": "RESTORATION_CONTROLLER",
                "states": ("CONTRACT_EVALUATED", "RESTORE_STARTED"),
                "body": "V1DeleteOptions(uid)",
            }
        ),
        "MUTANT_SERVICE_DELETION": MappingProxyType(
            {
                "kind": "Service", "namespace": NAMESPACE, "name_rule": "user-service",
                "method": "CoreV1Api.delete_namespaced_service",
                "consumer": "RESTORATION_CONTROLLER",
                "states": ("CONTRACT_EVALUATED", "RESTORE_STARTED"),
                "body": "V1DeleteOptions(uid,resourceVersion)",
            }
        ),
        "RESTORED_SERVICE_CREATION": MappingProxyType(
            {
                "kind": "Service", "namespace": NAMESPACE, "name_rule": "user-service",
                "method": "CoreV1Api.create_namespaced_service",
                "consumer": "RESTORATION_CONTROLLER", "states": ("RESTORE_STARTED",),
                "body": "SERVICE_RESTORATION_BODY_V1",
            }
        ),
        "RECOVERY_POD_RECYCLE_DELETE": MappingProxyType(
            {
                "kind": "Pod", "namespace": NAMESPACE, "name_rule": "captured recovery Pod",
                "method": "CoreV1Api.delete_namespaced_pod",
                "consumer": "RESTORATION_CONTROLLER", "states": ("RESTORE_STARTED",),
                "body": "V1DeleteOptions(uid,resourceVersion)",
            }
        ),
    }
)

_IMPLEMENTED_OPERATIONS = frozenset(
    {
        "INITIAL_USER_SERVICE_DELETION",
        "CHALLENGE_POD_CREATION",
        "REPLACEMENT_POD_DELETION",
        "CHALLENGE_POD_DELETION",
        "RESTORED_SERVICE_CREATION",
    }
)


@dataclass(slots=True)
class KubernetesMutationError(ValueError):
    """Stable failure without request bodies, credentials, or raw exceptions."""

    code: str
    result: Any | None = field(default=None, repr=False, compare=False)

    def __str__(self) -> str:
        return self.code


def _reject(code: str, result: Any | None = None) -> NoReturn:
    raise KubernetesMutationError(code, result) from None


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


def _reference(value: EvidenceRef | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(value, EvidenceRef):
        return value.as_dict()
    if isinstance(value, Mapping):
        return _thaw(value)
    _reject("MUTATION_REFERENCE_INVALID")


def _utc_now() -> str:
    value = datetime.now(timezone.utc)
    return value.strftime("%Y-%m-%dT%H:%M:%S.") + f"{value.microsecond:06d}000Z"


@dataclass(frozen=True, slots=True, init=False, repr=False)
class VerifiedRunnerBundle:
    manifest_sha256: str
    bundle_sha256: str
    git_commit: str
    git_tree: str
    annotated_tag_name: str
    annotated_tag_object: str
    pyproject_sha256: str
    uv_lock_sha256: str
    _marker: object = field(repr=False, compare=False)

    def __new__(cls, *args: Any, **kwargs: Any) -> "VerifiedRunnerBundle":
        if kwargs.pop("_token", None) is not _BUNDLE_TOKEN or args or kwargs:
            raise TypeError("RUNNER_BUNDLE_DIRECT_CONSTRUCTION_FORBIDDEN")
        return object.__new__(cls)

    @classmethod
    def authenticate(
        cls, document: Mapping[str, Any], manifest_bytes: bytes
    ) -> "VerifiedRunnerBundle":
        required = {
            "manifest_sha256", "bundle_sha256", "git_commit", "git_tree",
            "annotated_tag_name", "annotated_tag_object", "pyproject_sha256",
            "uv_lock_sha256",
        }
        if not isinstance(document, Mapping) or set(document) != required or not isinstance(manifest_bytes, bytes):
            _reject("RUNNER_BUNDLE_IDENTITY_INVALID")
        value = dict(document)
        if value["manifest_sha256"] != hashlib.sha256(manifest_bytes).hexdigest():
            _reject("RUNNER_BUNDLE_IDENTITY_INVALID")
        sha256_fields = {
            "manifest_sha256", "bundle_sha256", "pyproject_sha256",
            "uv_lock_sha256",
        }
        git_fields = {"git_commit", "git_tree", "annotated_tag_object"}
        if (
            any(
                not isinstance(value[name], str)
                or _SHA256.fullmatch(value[name]) is None
                for name in sha256_fields
            )
            or any(
                not isinstance(value[name], str)
                or re.fullmatch(r"[0-9a-f]{40}", value[name]) is None
                for name in git_fields
            )
        ):
            _reject("RUNNER_BUNDLE_IDENTITY_INVALID")
        if not isinstance(value["annotated_tag_name"], str) or not value["annotated_tag_name"]:
            _reject("RUNNER_BUNDLE_IDENTITY_INVALID")
        instance = cls(_token=_BUNDLE_TOKEN)
        for name, child in value.items():
            object.__setattr__(instance, name, child)
        object.__setattr__(instance, "_marker", _BUNDLE_TOKEN)
        return instance


@dataclass(frozen=True, slots=True, init=False, repr=False)
class RestorationBodyCapability:
    source_reference: Mapping[str, Any] = field(repr=False)
    body_reference: Mapping[str, Any] = field(repr=False)
    body: Mapping[str, Any] = field(repr=False)
    source_uid: str
    source_resource_version: str
    policy_manifest_sha256: str
    run_id: str
    attempt_id: str
    _marker: object = field(repr=False, compare=False)

    def __new__(cls, *args: Any, **kwargs: Any) -> "RestorationBodyCapability":
        if kwargs.pop("_token", None) is not _RESTORATION_TOKEN or args or kwargs:
            raise TypeError("RESTORATION_CAPABILITY_DIRECT_CONSTRUCTION_FORBIDDEN")
        return object.__new__(cls)

    def __repr__(self) -> str:
        return (
            "RestorationBodyCapability("
            f"source_uid={self.source_uid!r}, run_id={self.run_id!r}, "
            f"attempt_id={self.attempt_id!r})"
        )


def authenticate_restoration_body(
    store: EvidenceStore,
    source_reference: EvidenceRef | Mapping[str, Any],
    body_reference: EvidenceRef | Mapping[str, Any],
) -> RestorationBodyCapability:
    if not isinstance(store, EvidenceStore):
        _reject("RESTORATION_CAPABILITY_INVALID")
    try:
        source_resolved = store.resolve(source_reference)
        body_resolved = store.resolve(body_reference)
    except Exception:
        _reject("RESTORATION_CAPABILITY_INVALID")
    if source_resolved is None or body_resolved is None:
        _reject("RESTORATION_CAPABILITY_INVALID")
    source_ref, source_descriptor_bytes, source_bytes = source_resolved
    body_ref, body_descriptor_bytes, body_bytes = body_resolved
    if (
        source_ref.role != "kubernetes_object_projection"
        or source_ref.projection_class != SERVICE_RESTORATION_SOURCE
        or source_bytes is None
        or body_ref.role != "kubernetes_object_projection"
        or body_ref.projection_class != SERVICE_RESTORATION_BODY
        or body_bytes is None
    ):
        _reject("RESTORATION_CAPABILITY_INVALID")
    try:
        source_descriptor = parse_canonical_json(source_descriptor_bytes)
        body_descriptor = parse_canonical_json(body_descriptor_bytes)
        source = parse_canonical_json(source_bytes)
        document = parse_canonical_json(body_bytes)
    except Exception:
        _reject("RESTORATION_CAPABILITY_INVALID")
    if (
        not isinstance(source_descriptor, dict)
        or source_descriptor.get("projection_schema_id") != SERVICE_RESTORATION_SOURCE
        or not isinstance(body_descriptor, dict)
        or body_descriptor.get("projection_schema_id") != SERVICE_RESTORATION_BODY
        or not isinstance(source, dict)
        or not isinstance(document, dict)
        or document.get("source_service_reference") != source_ref.as_dict()
    ):
        _reject("RESTORATION_CAPABILITY_INVALID")
    expected = _derive_document(
        store.policy,
        source,
        document.get("source_request_reference"),
        document.get("capture_timestamp"),
        source_ref,
    )
    if canonical_json_bytes(expected) != body_bytes:
        _reject("RESTORATION_CAPABILITY_INVALID")
    metadata = source.get("metadata")
    if not isinstance(metadata, dict):
        _reject("RESTORATION_CAPABILITY_INVALID")
    uid = metadata.get("uid")
    resource_version = metadata.get("resourceVersion")
    if not isinstance(uid, str) or not uid or not isinstance(resource_version, str) or not resource_version:
        _reject("RESTORATION_CAPABILITY_INVALID")
    instance = RestorationBodyCapability(_token=_RESTORATION_TOKEN)
    for name, value in {
        "source_reference": _freeze(source_ref.as_dict()),
        "body_reference": _freeze(body_ref.as_dict()),
        "body": _freeze(document["normalized_service_create_body"]),
        "source_uid": uid,
        "source_resource_version": resource_version,
        "policy_manifest_sha256": store.policy.manifest_sha256,
        "run_id": store.run_id,
        "attempt_id": store.attempt_id,
        "_marker": _RESTORATION_TOKEN,
    }.items():
        object.__setattr__(instance, name, value)
    return instance


@dataclass(frozen=True, slots=True, init=False, repr=False)
class ReplacementPodTarget:
    name: str
    namespace: str
    uid: str
    resource_version: str
    reference: Mapping[str, Any] = field(repr=False)
    run_id: str
    attempt_id: str
    policy_manifest_sha256: str
    _session_token: object = field(repr=False, compare=False)
    _projection_sha256: str = field(repr=False, compare=False)
    _marker: object = field(repr=False, compare=False)

    def __new__(cls, *args: Any, **kwargs: Any) -> "ReplacementPodTarget":
        if kwargs.pop("_token", None) is not _POD_TOKEN or args or kwargs:
            raise TypeError("REPLACEMENT_TARGET_DIRECT_CONSTRUCTION_FORBIDDEN")
        return object.__new__(cls)


def select_replacement_pod(
    capture: KubernetesCapture,
    reference: EvidenceRef | Mapping[str, Any],
    descriptor_bytes: bytes,
) -> ReplacementPodTarget:
    if not isinstance(capture, KubernetesCapture) or capture.response_evidence is None:
        _reject("MUTATION_CAPTURE_INVALID")
    try:
        validate_prepublication_capture(capture._policy, capture)
    except Exception:
        _reject("MUTATION_CAPTURE_INVALID")
    request = _thaw(capture.request.canonical)
    payload = capture.response_evidence.payload_bytes
    if (
        request.get("request_rule_id") != "PODS_SOCIAL_READ_AND_GUARDED_MUTATION"
        or request.get("operation") != "LIST"
        or request.get("namespace") != NAMESPACE
        or request.get("label_selector") != "service=user-service"
        or request.get("field_selector") != ""
        or request.get("consumer") != "CONTRACT_EVALUATOR"
        or payload is None
    ):
        _reject("MUTATION_CAPTURE_INVALID")
    try:
        from sremut.evidence import validate_evidence_ref

        validated = validate_evidence_ref(
            capture._policy,
            reference,
            descriptor_bytes=descriptor_bytes,
            payload_bytes=payload,
            expected_run_id=capture.request.run_id,
            expected_attempt_id=capture.request.attempt_id,
        )
        descriptor = parse_canonical_json(descriptor_bytes)
        value = parse_canonical_json(payload)
        validate_kubernetes_list_response(capture._policy, value)
    except Exception:
        _reject("MUTATION_CAPTURE_INVALID")
    if (
        validated.role != "kubernetes_object_projection"
        or not isinstance(descriptor, dict)
        or descriptor.get("trusted_source_sha256") != hashlib.sha256(payload).hexdigest()
        or descriptor.get("request_identity_reference") is None
        or not isinstance(value, dict)
    ):
        _reject("MUTATION_CAPTURE_INVALID")
    eligible: list[tuple[str, str, str, str]] = []
    for item in value["items"]:
        projected = item.get("projected_fields")
        if not isinstance(projected, dict):
            continue
        metadata = projected.get("metadata")
        status = projected.get("status")
        if not isinstance(metadata, dict) or not isinstance(status, dict):
            continue
        ready = any(
            isinstance(condition, dict)
            and condition.get("type") == "Ready"
            and condition.get("status") == "True"
            for condition in status.get("conditions", [])
        )
        identity = (
            metadata.get("name"), metadata.get("namespace"),
            metadata.get("uid"), metadata.get("resourceVersion"),
        )
        if (
            ready
            and metadata.get("deletionTimestamp") in (None, "")
            and metadata.get("labels", {}).get("service") == "user-service"
            and all(isinstance(child, str) and child for child in identity)
        ):
            eligible.append(identity)
    if not eligible:
        _reject("MUTATION_CAPTURE_SELECTION_INVALID")
    selected = sorted(eligible, key=lambda row: row[0].encode("utf-8"))[0]
    instance = ReplacementPodTarget(_token=_POD_TOKEN)
    for name, child in {
        "name": selected[0], "namespace": selected[1], "uid": selected[2],
        "resource_version": selected[3], "reference": _freeze(validated.as_dict()),
        "run_id": capture.request.run_id, "attempt_id": capture.request.attempt_id,
        "policy_manifest_sha256": capture._policy.manifest_sha256,
        "_session_token": capture._session_token,
        "_projection_sha256": hashlib.sha256(payload).hexdigest(),
        "_marker": _POD_TOKEN,
    }.items():
        object.__setattr__(instance, name, child)
    return instance


@dataclass(frozen=True, slots=True, init=False, repr=False)
class ChallengePodTarget:
    name: str
    uid: str
    policy_manifest_sha256: str
    run_id: str
    attempt_id: str
    _session_token: object = field(repr=False, compare=False)
    _marker: object = field(repr=False, compare=False)

    def __new__(cls, *args: Any, **kwargs: Any) -> "ChallengePodTarget":
        if kwargs.pop("_token", None) is not _CHALLENGE_TOKEN or args or kwargs:
            raise TypeError("CHALLENGE_TARGET_DIRECT_CONSTRUCTION_FORBIDDEN")
        return object.__new__(cls)


@dataclass(frozen=True, slots=True)
class MutationTransportResult:
    http_status: int
    returned_uid: str | None
    returned_resource_version: str | None
    status: str
    reason: str | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.http_status, int)
            or isinstance(self.http_status, bool)
            or not 100 <= self.http_status <= 599
            or self.status not in ("Success", "Failure")
            or self.returned_uid is not None and (not isinstance(self.returned_uid, str) or not self.returned_uid)
            or self.returned_resource_version is not None
            and (not isinstance(self.returned_resource_version, str) or not self.returned_resource_version)
            or self.reason is not None and (not isinstance(self.reason, str) or not self.reason)
        ):
            _reject("MUTATION_TRANSPORT_RESULT_INVALID")


@runtime_checkable
class MutationTransport(Protocol):
    def delete_namespaced_service(
        self, name: str, namespace: str, body: Mapping[str, Any], timeout_seconds: int
    ) -> MutationTransportResult: ...

    def delete_namespaced_pod(
        self, name: str, namespace: str, body: Mapping[str, Any], timeout_seconds: int
    ) -> MutationTransportResult: ...

    def create_namespaced_service(
        self, namespace: str, body: Mapping[str, Any], timeout_seconds: int
    ) -> MutationTransportResult: ...

    def create_namespaced_pod(
        self, namespace: str, body: Mapping[str, Any], timeout_seconds: int
    ) -> MutationTransportResult: ...


class ObservedEffect(str, Enum):
    NOT_DISPATCHED = "NOT_DISPATCHED"
    ACKNOWLEDGED_APPLIED = "ACKNOWLEDGED_APPLIED"
    ACKNOWLEDGED_REJECTED = "ACKNOWLEDGED_REJECTED"
    OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"
    OBSERVED_APPLIED_AFTER_RECOVERY = "OBSERVED_APPLIED_AFTER_RECOVERY"
    OBSERVED_NOT_APPLIED_AFTER_RECOVERY = "OBSERVED_NOT_APPLIED_AFTER_RECOVERY"
    CONFLICTING_STATE = "CONFLICTING_STATE"


@dataclass(frozen=True, slots=True)
class MutationObservation:
    available: bool
    present: bool
    uid: str | None
    semantic_match: bool | None = None


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    classification: ObservedEffect
    retry_permitted: bool
    safe_to_continue: bool


@dataclass(frozen=True, slots=True, repr=False)
class MutationDispatchResult:
    operation_id: str
    operation_kind: str
    intent_reference: EvidenceRef
    request_reference: EvidenceRef
    status_reference: EvidenceRef
    receipt_reference: EvidenceRef
    acknowledgement: ObservedEffect
    challenge_target: ChallengePodTarget | None = field(default=None, repr=False)


#: The only two events a mutation-ledger row may carry, in dependency order.
MUTATION_LEDGER_EVENTS = ("INTENT_DURABLE", "RECEIPT_DURABLE")
#: The exact key set of a mutation-ledger row.
_MUTATION_LEDGER_KEYS = frozenset(
    {
        "schema_version",
        "sequence",
        "previous_sha256",
        "event",
        "operation_id",
        "descriptor_sha256",
        "record_sha256",
    }
)
_MUTATION_LEDGER_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MUTATION_OPERATION_ID = re.compile(r"^[A-Z_]+:[0-9]{2}$")


def validate_mutation_ledger_bytes(data: bytes) -> tuple[Mapping[str, Any], ...]:
    """The single strict reader for the durable mutation ledger.

    One definition, used by live reconstruction inside `GuardedMutationSession`,
    by sealed-context resolution, and by pending-mutation recovery, so a ledger
    that any one of them accepts is a ledger all three accept.

    Every row must be a canonical JSON line with exactly the frozen key set, a
    contiguous sequence from zero, the previous row's record hash, its own
    correct record hash, one of the two frozen events, and an operation id
    matching the frozen grammar.  Per operation the events must be exactly
    (INTENT_DURABLE,) or (INTENT_DURABLE, RECEIPT_DURABLE): a receipt without an
    intent, a duplicated event, or an out-of-order pair is refused.
    """
    if not isinstance(data, (bytes, bytearray)):
        _reject("MUTATION_LEDGER_INVALID")
    data = bytes(data)
    if not data:
        return ()
    if not data.endswith(b"\n"):
        _reject("MUTATION_LEDGER_INVALID")
    rows: list[Mapping[str, Any]] = []
    previous = ZERO_SHA256
    for sequence, line in enumerate(data.splitlines(keepends=True)):
        try:
            row = parse_canonical_json(line, line=True)
        except Exception:
            _reject("MUTATION_LEDGER_INVALID")
        if not isinstance(row, dict) or set(row) != _MUTATION_LEDGER_KEYS:
            _reject("MUTATION_LEDGER_INVALID")
        material = dict(row)
        digest = material.pop("record_sha256", None)
        if (
            row.get("schema_version") != 1
            or row.get("sequence") != sequence
            or type(row.get("sequence")) is not int
            or row.get("previous_sha256") != previous
            or row.get("event") not in MUTATION_LEDGER_EVENTS
            or not isinstance(row.get("operation_id"), str)
            or _MUTATION_OPERATION_ID.fullmatch(row["operation_id"]) is None
            or not isinstance(row.get("descriptor_sha256"), str)
            or _MUTATION_LEDGER_SHA256.fullmatch(row["descriptor_sha256"]) is None
            or not isinstance(digest, str)
            or _MUTATION_LEDGER_SHA256.fullmatch(digest) is None
            or digest != hashlib.sha256(canonical_json_bytes(material)).hexdigest()
        ):
            _reject("MUTATION_LEDGER_INVALID")
        previous = digest
        rows.append(MappingProxyType(dict(row)))
    events: dict[str, list[str]] = {}
    for row in rows:
        events.setdefault(row["operation_id"], []).append(row["event"])
    for operation_id, group in events.items():
        if tuple(group) not in (
            ("INTENT_DURABLE",),
            ("INTENT_DURABLE", "RECEIPT_DURABLE"),
        ):
            _reject("MUTATION_LEDGER_INVALID")
        if not operation_id.startswith(tuple(f"{name}:" for name in OPERATION_TABLE)):
            _reject("MUTATION_LEDGER_INVALID")
    return tuple(rows)


class GuardedMutationSession:
    """One lock-held attempt-local mutation facade with five closed primitives."""

    __slots__ = (
        "_policy", "_run_id", "_attempt_id", "_mutant", "_repetition",
        "_attempt_number", "_runner", "_root", "_host_root", "_host_lock",
        "_store", "_journal", "_transport", "_read_session", "_timeout",
        "_boot_identity", "_utc", "_monotonic", "_token", "_closed",
    )

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        raise TypeError("MUTATION_SESSION_DIRECT_CONSTRUCTION_FORBIDDEN")

    @classmethod
    def open(
        cls,
        *,
        policy: AuthenticatedPolicy,
        execution_profile_tag_object: str,
        execution_profile_sha256: str,
        run_id: str,
        attempt_id: str,
        mutant_id: str,
        repetition: int,
        attempt_number: int,
        runner_bundle: VerifiedRunnerBundle,
        host_lock_root: Path,
        attempt_root: Path,
        transport: MutationTransport,
        read_session: ReadOnlyKubernetesClient,
        timeout_seconds: int,
        boot_identity: str,
        utc_clock: Callable[[], str] = _utc_now,
        monotonic_clock: Callable[[], int] = time.monotonic_ns,
    ) -> "GuardedMutationSession":
        try:
            binding = policy.policy["bindings"]["execution_profile"]
        except Exception:
            _reject("MUTATION_SESSION_IDENTITY_INVALID")
        if (
            not isinstance(policy, AuthenticatedPolicy)
            or binding.get("tag_object") != execution_profile_tag_object
            or binding.get("sha256") != execution_profile_sha256
            or _RUN_ID.fullmatch(run_id) is None
            or _ATTEMPT_ID.fullmatch(attempt_id) is None
            or mutant_id != MUTANT
            or type(repetition) is not int
            or repetition not in (1, 2, 3)
            or type(attempt_number) is not int
            or attempt_number not in (1, 2)
            or attempt_id != f"a{attempt_number:02d}"
            or not isinstance(runner_bundle, VerifiedRunnerBundle)
            or runner_bundle._marker is not _BUNDLE_TOKEN
            or not isinstance(transport, MutationTransport)
            or not isinstance(read_session, ReadOnlyKubernetesClient)
            or read_session._policy is not policy
            or read_session._run_id != run_id
            or read_session._attempt_id != attempt_id
            or type(timeout_seconds) is not int
            or not 1 <= timeout_seconds <= 30
            or _BOOT_ID.fullmatch(boot_identity) is None
            or not Path(attempt_root).is_absolute()
            or not Path(host_lock_root).is_absolute()
        ):
            _reject("MUTATION_SESSION_IDENTITY_INVALID")
        instance = object.__new__(cls)
        host_root = SafeRoot(Path(host_lock_root))
        lock = host_root.lock(blocking=False)
        try:
            lock.__enter__()
            root = SafeRoot(Path(attempt_root))
            store = EvidenceStore(Path(attempt_root), policy, run_id, attempt_id, _safe_root=root)
            journal = Journal(Path(attempt_root), policy, run_id, attempt_id, _safe_root=root)
            journal.reconstruct()
        except Exception:
            try:
                lock.__exit__(None, None, None)
            finally:
                host_root.close()
            raise
        values = {
            "_policy": policy, "_run_id": run_id, "_attempt_id": attempt_id,
            "_mutant": mutant_id, "_repetition": repetition,
            "_attempt_number": attempt_number, "_runner": runner_bundle,
            "_root": root, "_host_root": host_root, "_host_lock": lock,
            "_store": store, "_journal": journal, "_transport": transport,
            "_read_session": read_session, "_timeout": timeout_seconds,
            "_boot_identity": boot_identity, "_utc": utc_clock,
            "_monotonic": monotonic_clock, "_token": object(), "_closed": False,
        }
        for name, value in values.items():
            object.__setattr__(instance, name, value)
        return instance

    def __enter__(self) -> "GuardedMutationSession":
        self._ensure_open()
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        self.close()

    def close(self) -> None:
        if not getattr(self, "_closed", True):
            object.__setattr__(self, "_closed", True)
            self._journal.close()
            self._store.close()
            self._root.close()
            self._host_lock.__exit__(None, None, None)
            self._host_root.close()

    def __copy__(self) -> NoReturn:
        _reject("MUTATION_SESSION_COPY_FORBIDDEN")

    def __deepcopy__(self, _memo: Any) -> NoReturn:
        _reject("MUTATION_SESSION_COPY_FORBIDDEN")

    def _ensure_open(self) -> None:
        if getattr(self, "_closed", True):
            _reject("MUTATION_SESSION_CLOSED")
        state = self._journal.reconstruct()
        if state.terminal or self._root.exists("manifests/terminal.sha256"):
            _reject("POST_TERMINAL_OPERATION")

    def _state(self, operation_kind: str) -> str:
        self._ensure_open()
        state = self._journal.reconstruct().state
        if operation_kind not in _IMPLEMENTED_OPERATIONS or state not in OPERATION_TABLE[operation_kind]["states"]:
            _reject("STATE_OPERATION_FORBIDDEN")
        return state

    def _metadata_time(self) -> tuple[str, int]:
        utc = self._utc()
        monotonic = self._monotonic()
        if not isinstance(utc, str) or not isinstance(monotonic, int) or isinstance(monotonic, bool) or monotonic < 0:
            _reject("MUTATION_CLOCK_INVALID")
        return utc, monotonic

    def _ledger_rows(self) -> list[dict[str, Any]]:
        if not self._root.exists(MUTATION_LEDGER):
            return []
        return [dict(row) for row in validate_mutation_ledger_bytes(
            self._root.read_bytes(MUTATION_LEDGER)
        )]

    def _append_ledger(
        self, event: str, operation_id: str, descriptor_sha256: str
    ) -> None:
        rows = self._ledger_rows()
        row = {
            "schema_version": 1,
            "sequence": len(rows),
            "previous_sha256": ZERO_SHA256 if not rows else rows[-1]["record_sha256"],
            "event": event,
            "operation_id": operation_id,
            "descriptor_sha256": descriptor_sha256,
            "record_sha256": ZERO_SHA256,
        }
        material = dict(row)
        material.pop("record_sha256")
        row["record_sha256"] = hashlib.sha256(canonical_json_bytes(material)).hexdigest()
        self._root.append_durable(MUTATION_LEDGER, canonical_json_line(row))
        self._ledger_rows()

    def _publish_intent(
        self,
        *,
        operation_kind: str,
        target_kind: str,
        target_name: str,
        expected_uid: str | None,
        expected_resource_version: str | None,
        body_reference: Mapping[str, Any] | None,
        body_sha256: str | None,
        captured_reference: Mapping[str, Any] | None,
    ) -> tuple[str, EvidenceRef]:
        if any(
            row["event"] == "INTENT_DURABLE"
            and row["operation_id"].startswith(operation_kind + ":")
            for row in self._ledger_rows()
        ):
            _reject("DUPLICATE_MUTATION_FORBIDDEN")
        utc, monotonic = self._metadata_time()
        ordinal = 1 + sum(row["event"] == "INTENT_DURABLE" for row in self._ledger_rows())
        operation_id = f"{operation_kind}:{ordinal:02d}"
        captured_sha = (
            captured_reference.get("descriptor_sha256")
            if isinstance(captured_reference, Mapping)
            else ZERO_SHA256
        )
        metadata: dict[str, Any] = {
            "operation_id": operation_id,
            "run_id": self._run_id,
            "attempt_id": self._attempt_id,
            "operation_ordinal": ordinal,
            "operation_kind": operation_kind,
            "namespace": NAMESPACE,
            "resource_kind": target_kind,
            "object_name": target_name,
            "expected_uid_when_existing": expected_uid,
            "expected_resource_version_when_applicable": expected_resource_version,
            "desired_canonical_body_sha256_when_creation": body_sha256,
            "captured_prestate_evidence_path": (
                captured_reference.get("descriptor_relative_path")
                if isinstance(captured_reference, Mapping)
                else ""
            ),
            "captured_prestate_evidence_sha256": captured_sha,
            "exact_idempotency_and_adoption_labels": {},
            "request_preconditions": {
                "uid": expected_uid,
                "resourceVersion": expected_resource_version,
            },
            "permitted_postconditions": ["EXACT_FROZEN_OPERATION_EFFECT"],
            "forbidden_postconditions": ["UNRELATED_OBJECT_MUTATION"],
            "created_utc": utc,
            "monotonic_time": monotonic,
            "boot_identity": self._boot_identity,
            "evaluator_or_runner_bundle_sha256": self._runner.bundle_sha256,
            "status": "INTENT_DURABLE",
        }
        if body_reference is not None:
            metadata["service_restoration_body_reference"] = _thaw(body_reference)
            metadata["service_restoration_body_sha256"] = body_sha256
        intent = self._store.publish_descriptor("mutation_intent", metadata)
        resolved = self._store.resolve(intent)
        if resolved is None or resolved[0].descriptor_sha256 != intent.descriptor_sha256:
            _reject("MUTATION_INTENT_NOT_DURABLE")
        self._append_ledger("INTENT_DURABLE", operation_id, intent.descriptor_sha256)
        return operation_id, intent

    def _request(
        self,
        operation_kind: str,
        target_name: str,
        intent: EvidenceRef,
        captured_reference: Mapping[str, Any] | None,
        captured_uid: str | None,
        captured_resource_version: str | None,
    ) -> tuple[dict[str, Any], EvidenceRef]:
        table = OPERATION_TABLE[operation_kind]
        operation = "CREATE" if "CREATION" in operation_kind else "DELETE"
        resource = "services" if table["kind"] == "Service" else "pods"
        rule_id = (
            "USER_SERVICE_READ_AND_GUARDED_MUTATION"
            if resource == "services"
            else "PODS_SOCIAL_READ_AND_GUARDED_MUTATION"
        )
        rule = next(
            row for row in self._policy.policy["kubernetes_evidence_surface"]["resource_rules"]
            if row["id"] == rule_id
        )
        request = {
            "document_type": "KUBERNETES_REQUEST_V1", "schema_version": 1,
            "request_rule_id": rule_id, "context": "kind-kind",
            "api_group": "", "api_version": "v1", "resource": resource,
            "response_kind": table["kind"], "subresource": "",
            "operation": operation, "namespace": NAMESPACE, "name": target_name,
            "label_selector": "", "field_selector": "",
            "request_options": _thaw(rule["request_options"]),
            "purpose": rule["purpose"], "consumer": table["consumer"],
            "projection_class": rule["projection_class"],
            "captured_object_name": target_name if captured_reference is not None else None,
            "captured_object_uid": captured_uid,
            "captured_object_resource_version": captured_resource_version,
            "captured_object_reference": (
                _thaw(captured_reference) if captured_reference is not None else None
            ),
            "uid_precondition": captured_uid if operation == "DELETE" else None,
            "authorized_operation_kind": operation_kind,
            "intent_reference": intent.as_dict(),
        }
        try:
            self._policy.structural_validate(request)
        except Exception:
            _reject("MUTATION_REQUEST_INVALID")
        identity = hashlib.sha256(
            canonical_json_bytes(
                {"request": request, "run_id": self._run_id, "attempt_id": self._attempt_id}
            )
        ).hexdigest()
        utc, monotonic = self._metadata_time()
        request_ref = self._store.publish_descriptor(
            "kubernetes_request_identity",
            {
                "run_id": self._run_id, "attempt_id": self._attempt_id,
                "created_utc": utc, "monotonic_ns": monotonic,
                "boot_identity": self._boot_identity, "request_rule_id": rule_id,
                "operation": operation, "resource": resource, "subresource": "",
                "namespace": NAMESPACE, "name_rule": target_name, "selector": "",
                "projection_paths": _thaw(rule["projection_paths"]),
                "canonical_request": request, "request_identity_sha256": identity,
            },
        )
        return request, request_ref

    def _publish_request_body(
        self, request_ref: EvidenceRef, body: Mapping[str, Any], state: str
    ) -> EvidenceRef:
        payload = canonical_json_bytes(_thaw(body))
        utc, monotonic = self._metadata_time()
        return self._store.publish_payload(
            "kubernetes_object_projection",
            payload,
            {
                "run_id": self._run_id, "attempt_id": self._attempt_id,
                "created_utc": utc, "monotonic_ns": monotonic,
                "boot_identity": self._boot_identity,
                "request_identity_reference": request_ref.as_dict(),
                "object_count": 1,
                "projection_class": "KUBERNETES_OBJECT_PROJECTION_V1",
                "projection_schema_id": "KUBERNETES_OBJECT_PROJECTION_V1",
                "capture_state": state,
                "trusted_source_sha256": hashlib.sha256(payload).hexdigest(),
            },
        )

    def _authorize(
        self,
        operation_kind: str,
        request_ref: EvidenceRef,
        body_ref: Mapping[str, Any] | None,
        intent: EvidenceRef,
    ) -> None:
        operation = {
            "operation_kind": operation_kind,
            "request_rule_id": (
                "USER_SERVICE_READ_AND_GUARDED_MUTATION"
                if OPERATION_TABLE[operation_kind]["kind"] == "Service"
                else "PODS_SOCIAL_READ_AND_GUARDED_MUTATION"
            ),
            "resource": (
                "services" if OPERATION_TABLE[operation_kind]["kind"] == "Service" else "pods"
            ),
            "namespace": NAMESPACE,
            "name": None,
            "request_reference": request_ref.as_dict(),
            "projection_reference": None,
            "request_body_reference": _thaw(body_ref) if body_ref is not None else None,
            "request_dispatch_sequence": None,
        }
        request = self._store.resolve(request_ref)
        if request is None:
            _reject("MUTATION_REQUEST_NOT_DURABLE")
        descriptor = parse_canonical_json(request[1])
        operation["name"] = descriptor["canonical_request"]["name"]
        encoded = canonical_json_bytes(operation).hex()
        utc, monotonic = self._metadata_time()
        self._journal.append_state_transition(
            "OPERATION_AUTHORIZED:" + encoded,
            utc_time=utc,
            monotonic_ns=monotonic,
            boot_identity=self._boot_identity,
            descriptor_sha256=(request_ref.descriptor_sha256,),
            intent_receipt_adjudication_sha256=(intent.descriptor_sha256,),
        )
        rebuilt = self._journal.reconstruct()
        if rebuilt.operation is None or _thaw(rebuilt.operation) != operation:
            _reject("MUTATION_INTENT_NOT_DURABLE")

    def _publish_status(
        self, request_ref: EvidenceRef, response: MutationTransportResult,
        operation_kind: str, target_name: str,
    ) -> EvidenceRef:
        value: dict[str, Any] = {
            "apiVersion": "v1", "kind": "Status", "status": response.status,
            "code": response.http_status,
            "details": {
                "name": target_name,
                "kind": OPERATION_TABLE[operation_kind]["kind"],
            },
        }
        if response.reason is not None:
            value["reason"] = response.reason
        payload = canonical_json_bytes(value)
        if detect_sensitive(payload, structured_format="json") is not None:
            _reject("SENSITIVE_CAPTURE_REJECTED")
        utc, monotonic = self._metadata_time()
        return self._store.publish_payload(
            "kubernetes_api_status",
            payload,
            {
                "run_id": self._run_id, "attempt_id": self._attempt_id,
                "created_utc": utc, "monotonic_ns": monotonic,
                "boot_identity": self._boot_identity,
                "request_identity_reference": request_ref.as_dict(),
                "http_status": response.http_status,
            },
        )

    def _publish_receipt(
        self,
        *,
        operation_id: str,
        operation_kind: str,
        request_ref: EvidenceRef,
        status_ref: EvidenceRef,
        response: MutationTransportResult,
        dispatch_start: str,
        dispatch_finish: str,
        body_ref: Mapping[str, Any] | None,
        body_sha256: str | None,
        exception_code: str | None = None,
    ) -> EvidenceRef:
        utc, monotonic = self._metadata_time()
        acknowledged = (
            ObservedEffect.OUTCOME_UNKNOWN
            if exception_code is not None
            else ObservedEffect.ACKNOWLEDGED_APPLIED
            if 200 <= response.http_status < 300 and response.status == "Success"
            else ObservedEffect.ACKNOWLEDGED_REJECTED
        )
        metadata: dict[str, Any] = {
            "run_id": self._run_id, "attempt_id": self._attempt_id,
            "created_utc": utc, "monotonic_ns": monotonic,
            "boot_identity": self._boot_identity, "operation_id": operation_id,
            "dispatch_start_utc": dispatch_start,
            "dispatch_finish_utc": dispatch_finish,
            "api_method": OPERATION_TABLE[operation_kind]["method"],
            "fixed_target": (
                f"{OPERATION_TABLE[operation_kind]['kind']}/{NAMESPACE}/"
                f"{self._request_target(request_ref)}"
            ),
            "http_status_or_typed_client_exception": (
                exception_code if exception_code is not None else response.http_status
            ),
            "returned_uid_when_present": response.returned_uid,
            "returned_resource_version_when_present": response.returned_resource_version,
            "raw_response_evidence_path": status_ref.payload_relative_path,
            "raw_response_evidence_sha256": status_ref.payload_sha256,
            "post_operation_get_or_list_evidence_paths": [],
            "post_operation_get_or_list_evidence_sha256": ZERO_SHA256,
            "observed_effect_classification": acknowledged.value,
            "effect_directly_acknowledged_or_recovered_from_observation": False,
            "status": "RECEIPT_DURABLE",
            "operation_request_reference": request_ref.as_dict(),
        }
        if body_ref is not None and operation_kind == "RESTORED_SERVICE_CREATION":
            metadata["service_restoration_body_reference"] = _thaw(body_ref)
            metadata["service_restoration_body_sha256"] = body_sha256
        receipt = self._store.publish_descriptor("mutation_receipt", metadata)
        if self._store.resolve(receipt) is None:
            _reject("MUTATION_RECEIPT_NOT_DURABLE")
        self._append_ledger("RECEIPT_DURABLE", operation_id, receipt.descriptor_sha256)
        return receipt

    def _request_target(self, request_ref: EvidenceRef) -> str:
        resolved = self._store.resolve(request_ref)
        if resolved is None:
            _reject("MUTATION_REQUEST_INVALID")
        value = parse_canonical_json(resolved[1])
        return value["canonical_request"]["name"]

    def _dispatch(
        self,
        *,
        operation_kind: str,
        target_name: str,
        expected_uid: str | None,
        expected_resource_version: str | None,
        captured_reference: Mapping[str, Any] | None,
        body_reference: Mapping[str, Any] | None,
        body_sha256: str | None,
        body: Mapping[str, Any],
        function: Callable[..., MutationTransportResult],
        arguments: tuple[Any, ...],
    ) -> MutationDispatchResult:
        self._state(operation_kind)
        operation_id, intent = self._publish_intent(
            operation_kind=operation_kind,
            target_kind=OPERATION_TABLE[operation_kind]["kind"],
            target_name=target_name,
            expected_uid=expected_uid,
            expected_resource_version=expected_resource_version,
            body_reference=body_reference,
            body_sha256=body_sha256,
            captured_reference=captured_reference,
        )
        _request, request_ref = self._request(
            operation_kind, target_name, intent, captured_reference,
            expected_uid, expected_resource_version,
        )
        effective_body_reference = body_reference
        if operation_kind == "CHALLENGE_POD_CREATION":
            challenge_body = self._publish_request_body(
                request_ref, body, "ORIGINAL_ORACLE_EVALUATED"
            )
            effective_body_reference = challenge_body.as_dict()
        self._authorize(
            operation_kind, request_ref, effective_body_reference, intent
        )
        dispatch_start, _start_monotonic = self._metadata_time()
        failure_code = None
        try:
            response = function(*arguments, body, self._timeout)
        except TimeoutError:
            failure_code = "KUBERNETES_MUTATION_TIMEOUT"
            response = MutationTransportResult(599, None, None, "Failure", "Timeout")
        except KeyboardInterrupt:
            failure_code = "KUBERNETES_MUTATION_INTERRUPTED"
            response = MutationTransportResult(599, None, None, "Failure", "Interrupted")
        except Exception:
            failure_code = "KUBERNETES_MUTATION_API_EXCEPTION"
            response = MutationTransportResult(599, None, None, "Failure", "ApiException")
        if type(response) is not MutationTransportResult:
            _reject("MUTATION_TRANSPORT_RESULT_INVALID")
        dispatch_finish, _finish_monotonic = self._metadata_time()
        status_ref = self._publish_status(
            request_ref, response, operation_kind, target_name
        )
        receipt = self._publish_receipt(
            operation_id=operation_id, operation_kind=operation_kind,
            request_ref=request_ref, status_ref=status_ref, response=response,
            dispatch_start=dispatch_start, dispatch_finish=dispatch_finish,
            body_ref=body_reference, body_sha256=body_sha256,
            exception_code=failure_code,
        )
        acknowledgement = (
            ObservedEffect.ACKNOWLEDGED_APPLIED
            if 200 <= response.http_status < 300 and response.status == "Success"
            else ObservedEffect.ACKNOWLEDGED_REJECTED
        )
        if failure_code is not None:
            failed = MutationDispatchResult(
                operation_id, operation_kind, intent, request_ref, status_ref,
                receipt, ObservedEffect.OUTCOME_UNKNOWN, None,
            )
            _reject(failure_code, failed)
        challenge = None
        if operation_kind == "CHALLENGE_POD_CREATION" and acknowledgement is ObservedEffect.ACKNOWLEDGED_APPLIED:
            if response.returned_uid is None:
                _reject("CHALLENGE_UID_MISSING")
            challenge = ChallengePodTarget(_token=_CHALLENGE_TOKEN)
            for name, value in {
                "name": target_name, "uid": response.returned_uid,
                "policy_manifest_sha256": self._policy.manifest_sha256,
                "run_id": self._run_id, "attempt_id": self._attempt_id,
                "_session_token": self._token, "_marker": _CHALLENGE_TOKEN,
            }.items():
                object.__setattr__(challenge, name, value)
        return MutationDispatchResult(
            operation_id, operation_kind, intent, request_ref, status_ref,
            receipt, acknowledgement, challenge,
        )

    def delete_user_service(
        self, restoration: RestorationBodyCapability
    ) -> MutationDispatchResult:
        self._validate_restoration(restoration)
        options = {
            "apiVersion": "v1", "kind": "DeleteOptions",
            "preconditions": {
                "uid": restoration.source_uid,
                "resourceVersion": restoration.source_resource_version,
            },
        }
        return self._dispatch(
            operation_kind="INITIAL_USER_SERVICE_DELETION",
            target_name=SERVICE_NAME, expected_uid=restoration.source_uid,
            expected_resource_version=restoration.source_resource_version,
            captured_reference=restoration.source_reference,
            body_reference=restoration.body_reference,
            body_sha256=restoration.body_reference["payload_sha256"],
            body=options, function=self._transport.delete_namespaced_service,
            arguments=(SERVICE_NAME, NAMESPACE),
        )

    def delete_replacement_pod(
        self, target: ReplacementPodTarget
    ) -> MutationDispatchResult:
        if (
            not isinstance(target, ReplacementPodTarget)
            or target._marker is not _POD_TOKEN
            or target.policy_manifest_sha256 != self._policy.manifest_sha256
            or target.run_id != self._run_id
            or target.attempt_id != self._attempt_id
            or target.namespace != NAMESPACE
            or target._session_token is not self._read_session._session_token
        ):
            _reject("MUTATION_CAPTURE_INVALID")
        resolved_target = self._store.resolve(target.reference)
        if (
            resolved_target is None
            or resolved_target[0].payload_sha256 != target._projection_sha256
        ):
            _reject("MUTATION_CAPTURE_INVALID")
        options = {
            "apiVersion": "v1", "kind": "DeleteOptions",
            "preconditions": {
                "uid": target.uid, "resourceVersion": target.resource_version,
            },
        }
        return self._dispatch(
            operation_kind="REPLACEMENT_POD_DELETION", target_name=target.name,
            expected_uid=target.uid,
            expected_resource_version=target.resource_version,
            captured_reference=target.reference, body_reference=None,
            body_sha256=None, body=options,
            function=self._transport.delete_namespaced_pod,
            arguments=(target.name, NAMESPACE),
        )

    def create_challenge_pod(self) -> MutationDispatchResult:
        self._state("CHALLENGE_POD_CREATION")
        profile = self._policy.policy["challenge_request_binding"]
        labels = {
            "app.kubernetes.io/name": "sremut-challenge",
            "app.kubernetes.io/managed-by": "sremut",
            "sremut-run-id": self._run_id,
            "sremut-mutant": self._mutant,
            "sremut-attempt": self._attempt_id,
        }
        security = profile["security_context"]
        spec = {
            "automountServiceAccountToken": profile["automount_service_account_token"],
            "restartPolicy": profile["restart_policy"],
            "activeDeadlineSeconds": profile["active_deadline_seconds"],
            "terminationGracePeriodSeconds": profile["termination_grace_period_seconds"],
            "containers": [{
                "name": "challenge", "image": profile["image"],
                "imagePullPolicy": profile["image_pull_policy"],
                "command": _thaw(profile["container_command"]),
                "securityContext": {
                    "runAsNonRoot": security["run_as_non_root"],
                    "runAsUser": security["run_as_user"],
                    "runAsGroup": security["run_as_group"],
                    "readOnlyRootFilesystem": security["read_only_root_filesystem"],
                    "allowPrivilegeEscalation": security["allow_privilege_escalation"],
                    "capabilities": {"drop": _thaw(security["capabilities_drop"])},
                    "seccompProfile": {"type": security["seccomp_profile"]},
                },
                "resources": _thaw(profile["resources"]),
            }],
        }
        frozen_spec_sha256 = hashlib.sha256(
            canonical_json_bytes(
                {"metadata": {"namespace": NAMESPACE, "labels": labels}, "spec": spec}
            )
        ).hexdigest()
        material = {
            "run_id": self._run_id, "mutant_id": self._mutant,
            "repetition": self._repetition, "attempt_id": self._attempt_id,
            "frozen_spec_sha256": frozen_spec_sha256,
        }
        name = "sremut-challenge-" + hashlib.sha256(
            canonical_json_bytes(material)
        ).hexdigest()[:16]
        body = {
            "apiVersion": "v1", "kind": "Pod",
            "metadata": {"name": name, "namespace": NAMESPACE, "labels": labels},
            "spec": spec,
        }
        body_sha = hashlib.sha256(canonical_json_bytes(body)).hexdigest()
        return self._dispatch(
            operation_kind="CHALLENGE_POD_CREATION", target_name=name,
            expected_uid=None, expected_resource_version=None,
            captured_reference=None, body_reference=None,
            body_sha256=body_sha, body=body,
            function=self._transport.create_namespaced_pod,
            arguments=(NAMESPACE,),
        )

    def delete_challenge_pod(
        self, target: ChallengePodTarget
    ) -> MutationDispatchResult:
        if (
            not isinstance(target, ChallengePodTarget)
            or target._marker is not _CHALLENGE_TOKEN
            or target._session_token is not self._token
            or target.policy_manifest_sha256 != self._policy.manifest_sha256
            or target.run_id != self._run_id
            or target.attempt_id != self._attempt_id
        ):
            _reject("CHALLENGE_TARGET_INVALID")
        options = {
            "apiVersion": "v1", "kind": "DeleteOptions",
            "preconditions": {"uid": target.uid},
        }
        return self._dispatch(
            operation_kind="CHALLENGE_POD_DELETION", target_name=target.name,
            expected_uid=target.uid, expected_resource_version=None,
            captured_reference=None, body_reference=None, body_sha256=None,
            body=options, function=self._transport.delete_namespaced_pod,
            arguments=(target.name, NAMESPACE),
        )

    def restore_user_service(
        self, restoration: RestorationBodyCapability
    ) -> MutationDispatchResult:
        self._validate_restoration(restoration)
        return self._dispatch(
            operation_kind="RESTORED_SERVICE_CREATION",
            target_name=SERVICE_NAME, expected_uid=restoration.source_uid,
            expected_resource_version=restoration.source_resource_version,
            captured_reference=restoration.source_reference,
            body_reference=restoration.body_reference,
            body_sha256=restoration.body_reference["payload_sha256"],
            body=restoration.body,
            function=self._transport.create_namespaced_service,
            arguments=(NAMESPACE,),
        )

    def _validate_restoration(
        self, restoration: RestorationBodyCapability
    ) -> None:
        if (
            not isinstance(restoration, RestorationBodyCapability)
            or restoration._marker is not _RESTORATION_TOKEN
            or restoration.policy_manifest_sha256 != self._policy.manifest_sha256
            or restoration.run_id != self._run_id
            or restoration.attempt_id != self._attempt_id
        ):
            _reject("RESTORATION_CAPABILITY_INVALID")
        if self._store.resolve(restoration.source_reference) is None or self._store.resolve(restoration.body_reference) is None:
            _reject("RESTORATION_CAPABILITY_INVALID")

    def reconcile_pending(
        self, operation_id: str, operation_kind: str,
        captured_uid: str | None, observation: MutationObservation,
    ) -> ReconciliationResult:
        self._ensure_open()
        rows = [row for row in self._ledger_rows() if row["operation_id"] == operation_id]
        events = tuple(row["event"] for row in rows)
        if events != ("INTENT_DURABLE",):
            _reject("RECONCILIATION_PENDING_INTENT_INVALID")
        if not operation_id.startswith(operation_kind + ":"):
            _reject("RECONCILIATION_INPUT_INVALID")
        return self.reconcile(operation_kind, captured_uid, observation)

    def reconcile(
        self,
        operation_kind: str,
        captured_uid: str | None,
        observation: MutationObservation,
    ) -> ReconciliationResult:
        if operation_kind not in _IMPLEMENTED_OPERATIONS or not isinstance(observation, MutationObservation):
            _reject("RECONCILIATION_INPUT_INVALID")
        if not observation.available:
            return ReconciliationResult(ObservedEffect.OUTCOME_UNKNOWN, False, False)
        deleting = operation_kind in {
            "INITIAL_USER_SERVICE_DELETION",
            "REPLACEMENT_POD_DELETION",
            "CHALLENGE_POD_DELETION",
        }
        if deleting:
            if not observation.present:
                return ReconciliationResult(
                    ObservedEffect.OBSERVED_APPLIED_AFTER_RECOVERY, False, True
                )
            if observation.uid == captured_uid:
                return ReconciliationResult(
                    ObservedEffect.OBSERVED_NOT_APPLIED_AFTER_RECOVERY, True, False
                )
            return ReconciliationResult(
                ObservedEffect.OBSERVED_APPLIED_AFTER_RECOVERY, False, True
            )
        if not observation.present:
            return ReconciliationResult(
                ObservedEffect.OBSERVED_NOT_APPLIED_AFTER_RECOVERY, True, False
            )
        if observation.semantic_match is True:
            return ReconciliationResult(
                ObservedEffect.OBSERVED_APPLIED_AFTER_RECOVERY, False, True
            )
        return ReconciliationResult(ObservedEffect.CONFLICTING_STATE, False, False)

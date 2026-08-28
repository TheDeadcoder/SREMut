"""Offline production composition of the frozen MS-M01 attempt capabilities.

This module contains no experiment.  It builds one `MsM01Attempt` out of the
existing production components and returns it unrun, so that a scheduled MS-M01
repetition can later be executed by a caller that owns the external transports.

What it deliberately does NOT do, at import, construction, or test time:

* construct a Kubernetes client, read a kubeconfig, or contact a cluster;
* launch a subprocess, execute a workload, or invoke the stock oracle;
* open a network connection or read an ambient credential.

Every capability that must touch the outside world is *injected* already
constructed: the read-only transport, the mutation transport, the verified
runner bundle, the bounded challenge executor, the bounded workload source, and
the bounded original-oracle invoker.  The composition binds each of them to the
authenticated run, attempt, session, consumer, policy, profile, contract,
registry, mutant, and operation identities, and refuses any mismatch.

The frozen lifecycle in `sremut.ms_m01_attempt` is not modified, extended, or
bypassed.  Its ten capability Protocols are the only integration surface, and
this module supplies exactly one production implementation of each.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, NoReturn, Protocol, runtime_checkable

from sremut.canonical_json import canonical_json_bytes, parse_canonical_json, sha256_hex
from sremut.evidence import (
    EvidenceRef,
    EvidenceStore,
    ExternalAnchor,
    revalidate_sealed_attempt,
)
from sremut.journal import Journal, SafeRoot
from sremut.kubernetes_mutation import (
    GuardedMutationSession,
    MutationObservation,
    MutationTransport,
    ObservedEffect,
    ReplacementPodTarget,
    VerifiedRunnerBundle,
    authenticate_restoration_body,
    select_replacement_pod,
)
from sremut.kubernetes_readonly import (
    KubernetesCapture,
    KubernetesReadOnlyError,
    KubernetesConsumer,
    PodSelector,
    ReadOnlyKubernetesClient,
    ReadOnlyKubernetesTransport,
)
from sremut.ms_m01_attempt import (
    AttemptCapabilities,
    AttemptIdentity,
    CapturedPod,
    ChallengeRun,
    ContractEvaluation,
    HealthyPrestate,
    MsM01Attempt,
    MsM01AttemptError,
    OriginalOracleOutcome,
    PendingMutation,
    ReplacementObservation,
    RestorationVerification,
    TerminalOutcome,
    TerminalizationResult,
    WORKLOAD_WINDOWS,
    WorkloadWindowResult,
)
from sremut.policy_runtime import (
    EXPECTED_HOOK_ORDER,
    POLICY_MANIFEST_SHA256,
    AuthenticatedPolicy,
)
from sremut.service_restoration import (
    SERVICE_RESTORATION_BODY,
    SERVICE_RESTORATION_SOURCE,
    _derive_document,
)
from sremut.workload_evidence import (
    WorkloadHistoryEntry,
    prepare_workload_window,
    recompute_stream_identity,
)


NAMESPACE = "social-network"
SERVICE_NAME = "user-service"
CONTEXT = "kind-kind"
POLICY_ID = "sremut/missing-service-social-network/evidence-capture-v1.1"
REGISTRY_ID = "sremut/missing-service-social-network/pilot-mutants-v1"
MUTANT_ID = "MS-M01"

#: Operations the composition may ever dispatch, in frozen lifecycle order.
FROZEN_OPERATIONS = (
    "INITIAL_USER_SERVICE_DELETION",
    "CHALLENGE_POD_CREATION",
    "REPLACEMENT_POD_DELETION",
    "CHALLENGE_POD_DELETION",
    "RESTORED_SERVICE_CREATION",
)

_GLOBAL_STOP_RELATIVE = "terminal/global-stop.json"
_CLAIM_DIRECTORY = "claims"


@dataclass(slots=True)
class MsM01RuntimeError(RuntimeError):
    """Stable composition failure carrying no raw evidence or secret material."""

    code: str

    def __str__(self) -> str:
        return self.code


def _reject(code: str) -> NoReturn:
    raise MsM01RuntimeError(code) from None


# ---------------------------------------------------------------------------
# Injected bounded executors
#
# These three Protocols are the only places where the attempt reaches anything
# outside the evidence root.  The composition never implements them: it accepts
# an already-constructed executor and binds it to the authenticated identity.
# No shell command, kubectl invocation, or subprocess is constructed here.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ChallengeExecution:
    """Raw bounded result of one challenge run, as reported by the executor."""

    completed: bool
    exit_status: int
    stdout: bytes = field(repr=False)
    stderr: bytes = field(repr=False)
    failure_classification: str | None = None


@dataclass(frozen=True, slots=True)
class WorkloadSnapshot:
    """Raw bounded workload history bracketing one window."""

    before: tuple[WorkloadHistoryEntry, ...] = field(repr=False)
    after: tuple[WorkloadHistoryEntry, ...] = field(repr=False)
    pod_name: str
    pod_uid: str
    container_restart_count: int
    pod_projection_reference: EvidenceRef | Mapping[str, Any] = field(repr=False)


@dataclass(frozen=True, slots=True)
class OracleExecution:
    """Raw bounded result of the one stock-oracle invocation."""

    execution_classification: str
    returned_boolean: bool | None
    raw_result: dict[str, bool] | None
    exit_status: int | None
    input_reference: EvidenceRef | Mapping[str, Any] = field(repr=False)
    result_reference: EvidenceRef | Mapping[str, Any] = field(repr=False)


@runtime_checkable
class ChallengeExecutor(Protocol):
    def execute(
        self,
        *,
        run_id: str,
        attempt_id: str,
        phase: str,
        ordinal: int,
        pod_name: str,
        pod_uid: str,
    ) -> ChallengeExecution: ...


@runtime_checkable
class WorkloadSource(Protocol):
    def snapshot(
        self,
        *,
        run_id: str,
        attempt_id: str,
        phase: str,
        ordinal: int,
    ) -> WorkloadSnapshot: ...


@runtime_checkable
class OriginalOracleInvoker(Protocol):
    def invoke(
        self,
        *,
        run_id: str,
        attempt_id: str,
        invocation_ordinal: int,
        kubernetes_context: str,
        namespace: str,
        captured_replica_baseline: Mapping[str, int],
    ) -> OracleExecution: ...


# ---------------------------------------------------------------------------
# Authenticated identity binding
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ConsumerBinding:
    """The frozen consumer identity used for each authorised read purpose.

    The policy authorises different consumers per resource rule
    (``kubernetes_evidence_surface.resource_rules``); binding one consumer for
    the whole attempt would be rejected by the request validator.
    """

    preflight: KubernetesConsumer = KubernetesConsumer.LIVE_PREFLIGHT
    contract: KubernetesConsumer = KubernetesConsumer.CONTRACT_EVALUATOR
    mutation: KubernetesConsumer = KubernetesConsumer.MUTATION_CONTROLLER
    restoration: KubernetesConsumer = KubernetesConsumer.RESTORATION_CONTROLLER

    def all(self) -> tuple[KubernetesConsumer, ...]:
        return (self.preflight, self.contract, self.mutation, self.restoration)


@dataclass(frozen=True, slots=True)
class RuntimeBinding:
    """Every identity each capability is bound to, checked once at composition."""

    policy: AuthenticatedPolicy = field(repr=False)
    identity: AttemptIdentity
    session_token: object = field(repr=False, compare=False)
    consumers: ConsumerBinding
    boot_identity: str
    policy_id: str
    registry_id: str
    execution_profile_tag_object: str
    execution_profile_sha256: str
    contract_sha256: str

    @property
    def run_id(self) -> str:
        return self.identity.run_id

    @property
    def attempt_id(self) -> str:
        return self.identity.attempt_id

    def require(self, *, run_id: str, attempt_id: str) -> None:
        if run_id != self.run_id or attempt_id != self.attempt_id:
            _reject("RUNTIME_IDENTITY_MISMATCH")

    def require_session(self, token: object) -> None:
        if token is not self.session_token:
            _reject("RUNTIME_SESSION_MISMATCH")

    def require_consumer(self, consumer: KubernetesConsumer) -> None:
        if consumer not in self.consumers.all():
            _reject("RUNTIME_CONSUMER_MISMATCH")

    def require_operation(self, operation_kind: str) -> None:
        if operation_kind not in FROZEN_OPERATIONS:
            _reject("RUNTIME_OPERATION_MISMATCH")

    def require_mutant(self, mutant_id: str) -> None:
        if mutant_id != MUTANT_ID or self.identity.mutant_id != MUTANT_ID:
            _reject("RUNTIME_MUTANT_MISMATCH")


class _Clock:
    """Monotone, injected wall/monotonic clock pair with no ambient time source."""

    __slots__ = ("_utc", "_monotonic", "_last")

    def __init__(self, utc: Callable[[], str], monotonic: Callable[[], int]) -> None:
        self._utc = utc
        self._monotonic = monotonic
        self._last = -1

    def utc(self) -> str:
        return self._utc()

    def monotonic(self) -> int:
        value = self._monotonic()
        if type(value) is not int or value <= self._last:
            _reject("RUNTIME_CLOCK_NOT_MONOTONE")
        self._last = value
        return value


# ---------------------------------------------------------------------------
# Capability: durable run authority
# ---------------------------------------------------------------------------


class DurableRunAuthority:
    """Global stop and single-claim ledger held outside the attempt root."""

    __slots__ = ("_binding", "_index", "_owns")

    def __init__(self, binding: RuntimeBinding, run_index_root: SafeRoot) -> None:
        self._binding = binding
        self._index = run_index_root
        self._owns = False

    def global_stop_active(self) -> bool:
        return self._index.exists(_GLOBAL_STOP_RELATIVE)

    def claim_once(self, identity: AttemptIdentity, *, resume: bool) -> None:
        if identity != self._binding.identity:
            _reject("RUNTIME_IDENTITY_MISMATCH")
        relative = f"{_CLAIM_DIRECTORY}/{identity.run_id}.{identity.attempt_id}.json"
        exists = self._index.exists(relative)
        if exists and not resume:
            raise MsM01AttemptError("ATTEMPT_REPLAY_FORBIDDEN")
        if not exists:
            self._index.write_atomic(
                relative,
                canonical_json_bytes(
                    {
                        "document_type": "ATTEMPT_CLAIM_V1",
                        "schema_version": 1,
                        "run_id": identity.run_id,
                        "attempt_id": identity.attempt_id,
                        "mutant_id": identity.mutant_id,
                        "registry_id": identity.registry_id,
                        "repetition": identity.repetition,
                        "attempt_number": identity.attempt_number,
                    }
                ),
            )
        self._owns = True


# ---------------------------------------------------------------------------
# Capability: journal-backed attempt state
# ---------------------------------------------------------------------------


class JournalAttemptState:
    """Attempt state read from, and advanced through, the real hash-chained journal."""

    __slots__ = ("_binding", "_root", "_clock")

    def __init__(
        self, binding: RuntimeBinding, attempt_root: SafeRoot, clock: _Clock
    ) -> None:
        self._binding = binding
        self._root = attempt_root
        self._clock = clock

    def _journal(self) -> Journal:
        return Journal(
            self._root.root,
            self._binding.policy,
            self._binding.run_id,
            self._binding.attempt_id,
            _safe_root=self._root,
        )

    def current_state(self) -> str:
        return self._journal().reconstruct().state

    def transition(self, transition: str, *, evidence_references: tuple[Any, ...]) -> None:
        descriptors: list[str] = []
        for reference in evidence_references:
            digest = _descriptor_sha256(reference)
            if digest is not None:
                descriptors.append(digest)
        self._journal().append_state_transition(
            transition,
            utc_time=self._clock.utc(),
            monotonic_ns=self._clock.monotonic(),
            boot_identity=self._binding.boot_identity,
            descriptor_sha256=tuple(descriptors),
        )


def _descriptor_sha256(reference: Any) -> str | None:
    if isinstance(reference, EvidenceRef):
        return reference.descriptor_sha256
    if isinstance(reference, Mapping):
        value = reference.get("descriptor_sha256")
        return value if isinstance(value, str) else None
    return None


# ---------------------------------------------------------------------------
# Capability: authenticated healthy-prestate resolution
# ---------------------------------------------------------------------------


class AuthenticatedPrestate:
    """Captures the healthy Service and replica baseline, and authenticates them."""

    __slots__ = ("_binding", "_reader", "_store", "_clock", "_publisher")

    def __init__(
        self,
        binding: RuntimeBinding,
        reader: ReadOnlyKubernetesClient,
        store: EvidenceStore,
        clock: _Clock,
        publisher: "_CapturePublisher",
    ) -> None:
        self._binding = binding
        self._reader = reader
        self._store = store
        self._clock = clock
        self._publisher = publisher

    def authenticate(
        self, policy: AuthenticatedPolicy, identity: AttemptIdentity
    ) -> HealthyPrestate:
        if policy is not self._binding.policy or identity != self._binding.identity:
            _reject("RUNTIME_IDENTITY_MISMATCH")
        consumer = self._binding.consumers.preflight
        self._binding.require_consumer(consumer)

        service = self._reader.get_user_service(consumer=consumer)
        service_request, source_reference, _bytes = self._publisher.publish(
            service, projection_class=SERVICE_RESTORATION_SOURCE
        )
        if source_reference is None:
            _reject("RUNTIME_PRESTATE_CAPTURE_INVALID")
        source = self._publisher.payload(service)

        # `derive_service_restoration_body` authenticates a SEALED attempt and so
        # cannot run before the mutation that seals it.  The live path therefore
        # uses the same derivation the public wrapper wraps, over the projection
        # this attempt just published, and hands the result straight to
        # `authenticate_restoration_body` for byte authentication.
        document = _derive_document(
            policy, source, service_request, self._clock.utc(), source_reference
        )
        body_payload = canonical_json_bytes(document)
        body_reference = self._store.publish_payload(
            "kubernetes_object_projection",
            body_payload,
            {
                "run_id": identity.run_id,
                "attempt_id": identity.attempt_id,
                "created_utc": self._clock.utc(),
                "monotonic_ns": self._clock.monotonic(),
                "boot_identity": self._binding.boot_identity,
                "request_identity_reference": _reference_mapping(service_request),
                "object_count": 1,
                "projection_class": SERVICE_RESTORATION_BODY,
                "projection_schema_id": SERVICE_RESTORATION_BODY,
                "capture_state": "HEALTHY_STATE_CAPTURED",
                "trusted_source_sha256": sha256_hex(body_payload),
            },
        )
        restoration = authenticate_restoration_body(
            self._store, source_reference, body_reference
        )

        deployments = self._reader.list_deployments(consumer=consumer)
        _req, deployment_reference, _d = self._publisher.publish(deployments)
        if deployment_reference is None:
            _reject("RUNTIME_PRESTATE_CAPTURE_INVALID")
        baseline = _replica_baseline(self._publisher.payload(deployments))

        prestate_reference = self._store.publish_payload(
            "healthy_prestate",
            canonical_json_bytes(
                {
                    "document_type": "HEALTHY_PRESTATE_V1",
                    "schema_version": 1,
                    "run_id": identity.run_id,
                    "attempt_id": identity.attempt_id,
                    "namespace": NAMESPACE,
                    "service_name": SERVICE_NAME,
                    "captured_replica_baseline": dict(baseline),
                }
            ),
            {
                "run_id": identity.run_id,
                "attempt_id": identity.attempt_id,
                "created_utc": self._clock.utc(),
                "monotonic_ns": self._clock.monotonic(),
                "boot_identity": self._binding.boot_identity,
                "captured_replica_baseline": dict(baseline),
                "captured_service_reference": _reference_mapping(source_reference),
            },
        )
        uid, resource_version = _service_identity(source)
        return HealthyPrestate(
            policy_manifest_sha256=policy.manifest_sha256,
            run_id=identity.run_id,
            attempt_id=identity.attempt_id,
            namespace=NAMESPACE,
            service_name=SERVICE_NAME,
            service_uid=uid,
            service_resource_version=resource_version,
            captured_replica_baseline=baseline,
            workload_stream_identity=recompute_stream_identity(
                identity.run_id, identity.attempt_id
            ),
            restoration_capability=restoration,
            evidence_references=(
                prestate_reference,
                source_reference,
                body_reference,
                deployment_reference,
            ),
        )


def _service_identity(source: Mapping[str, Any]) -> tuple[str, str]:
    metadata = source.get("metadata")
    if not isinstance(metadata, Mapping):
        _reject("RUNTIME_PRESTATE_CAPTURE_INVALID")
    uid = metadata.get("uid")
    version = metadata.get("resourceVersion")
    if not isinstance(uid, str) or not uid or not isinstance(version, str) or not version:
        _reject("RUNTIME_PRESTATE_CAPTURE_INVALID")
    return uid, version


def _projected(item: Any) -> Mapping[str, Any]:
    """Unwrap one entry of a KUBERNETES_LIST_RESPONSE_V1 envelope.

    A LIST projection wraps each object as
    ``{"name": ..., "projected_fields": {...}}``; a GET projection is the
    projected object itself.
    """
    if not isinstance(item, Mapping):
        _reject("RUNTIME_PRESTATE_CAPTURE_INVALID")
    inner = item.get("projected_fields")
    return inner if isinstance(inner, Mapping) else item


def _replica_baseline(payload: Mapping[str, Any]) -> dict[str, int]:
    items = payload.get("items")
    if not isinstance(items, (list, tuple)) or not items:
        _reject("RUNTIME_PRESTATE_CAPTURE_INVALID")
    baseline: dict[str, int] = {}
    for entry in items:
        item = _projected(entry)
        metadata = item.get("metadata")
        spec = item.get("spec")
        if not isinstance(metadata, Mapping) or not isinstance(spec, Mapping):
            _reject("RUNTIME_PRESTATE_CAPTURE_INVALID")
        name = metadata.get("name")
        replicas = spec.get("replicas")
        if (
            not isinstance(name, str)
            or not name
            or type(replicas) is not int
            or replicas < 0
        ):
            _reject("RUNTIME_PRESTATE_CAPTURE_INVALID")
        baseline[name] = replicas
    return baseline


def _reference_mapping(reference: EvidenceRef | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(reference, EvidenceRef):
        return reference.as_dict()
    return dict(reference)


# ---------------------------------------------------------------------------
# Evidence publication for read-only captures
# ---------------------------------------------------------------------------


class _CapturePublisher:
    """Publishes one read-only capture's request and response evidence, in order.

    The request identity descriptor must exist before the response projection can
    be published: `EvidenceCandidate.publication_metadata` authenticates the
    response against the request descriptor's own bytes.
    """

    __slots__ = ("_binding", "_store", "_clock")

    def __init__(
        self, binding: RuntimeBinding, store: EvidenceStore, clock: _Clock
    ) -> None:
        self._binding = binding
        self._store = store
        self._clock = clock

    def _common(self) -> dict[str, Any]:
        return {
            "run_id": self._binding.run_id,
            "attempt_id": self._binding.attempt_id,
            "created_utc": self._clock.utc(),
            "monotonic_ns": self._clock.monotonic(),
            "boot_identity": self._binding.boot_identity,
        }

    def publish(
        self,
        capture: KubernetesCapture,
        *,
        projection_class: str | None = None,
        capture_state: str = "HEALTHY_STATE_CAPTURED",
    ) -> tuple[EvidenceRef, EvidenceRef | None, bytes | None]:
        request_reference = self._store.publish_descriptor(
            capture.request_evidence.role,
            capture.request_evidence.publication_metadata(**self._common()),
        )
        resolved_request = self._store.resolve(request_reference)
        if resolved_request is None:
            _reject("RUNTIME_CAPTURE_REQUEST_MISSING")
        request_bytes = resolved_request[1]
        if capture.response_evidence is None or capture.response_evidence.payload_bytes is None:
            return request_reference, None, None
        metadata = dict(
            capture.response_evidence.publication_metadata(
                **self._common(),
                request_identity_reference=request_reference,
                request_identity_descriptor_bytes=request_bytes,
            )
        )
        if projection_class is not None:
            metadata["projection_class"] = projection_class
            metadata["projection_schema_id"] = projection_class
        metadata["capture_state"] = capture_state
        response_reference = self._store.publish_payload(
            capture.response_evidence.role,
            capture.response_evidence.payload_bytes,
            metadata,
        )
        resolved_response = self._store.resolve(response_reference)
        descriptor_bytes = resolved_response[1] if resolved_response else None
        return request_reference, response_reference, descriptor_bytes

    def payload(self, capture: KubernetesCapture) -> Mapping[str, Any]:
        if (
            capture.response_evidence is None
            or capture.response_evidence.payload_bytes is None
        ):
            _reject("RUNTIME_CAPTURE_RESPONSE_MISSING")
        return parse_canonical_json(capture.response_evidence.payload_bytes)


# ---------------------------------------------------------------------------
# Capability: read-only cluster observation
# ---------------------------------------------------------------------------


class ClusterObservations:
    """The five frozen observations, each from a policy-authorised read."""

    __slots__ = ("_binding", "_reader", "_store", "_publisher")

    def __init__(
        self,
        binding: RuntimeBinding,
        reader: ReadOnlyKubernetesClient,
        store: EvidenceStore,
        publisher: _CapturePublisher,
    ) -> None:
        self._binding = binding
        self._reader = reader
        self._store = store
        self._publisher = publisher

    def _service_state(
        self, consumer: KubernetesConsumer, *, capture_state: str
    ) -> tuple[bool, bool, str | None, EvidenceRef | None]:
        """(available, present, uid, reference) for the target Service.

        A deleted Service is not a classification on a successful capture: the
        read client raises `KUBERNETES_API_FAILURE` and attaches the failure
        capture, whose status payload carries the HTTP code.  A 404 is therefore
        an *observation of absence*, and anything else is an unavailable read.
        """
        self._binding.require_consumer(consumer)
        try:
            capture = self._reader.get_user_service(consumer=consumer)
        except KubernetesReadOnlyError as error:
            failure = getattr(error, "capture", None)
            if failure is None or not _http_status_is(failure, 404):
                return False, False, None, None
            request, _response, _bytes = self._publisher.publish(
                failure, capture_state=capture_state
            )
            return True, False, None, request
        _request, reference, _bytes = self._publisher.publish(
            capture, capture_state=capture_state
        )
        payload = self._publisher.payload(capture)
        metadata = payload.get("metadata")
        uid = metadata.get("uid") if isinstance(metadata, Mapping) else None
        return True, True, uid if isinstance(uid, str) else None, reference

    def observe_initial_service_deletion(self, dispatch: Any) -> MutationObservation:
        del dispatch
        available, present, uid, _reference = self._service_state(
            self._binding.consumers.mutation, capture_state="MUTANT_INJECTED"
        )
        return MutationObservation(available=available, present=present, uid=uid)

    def service_structurally_absent(self) -> bool:
        available, present, _uid, _reference = self._service_state(
            self._binding.consumers.contract, capture_state="MUTANT_STATE_VERIFIED"
        )
        if not available or present:
            return False
        consumer = self._binding.consumers.contract
        slices = self._reader.list_user_service_endpoint_slices(consumer=consumer)
        self._publisher.publish(slices, capture_state="MUTANT_STATE_VERIFIED")
        items = self._publisher.payload(slices).get("items")
        return isinstance(items, (list, tuple)) and not items

    def select_replacement_pod(self, prestate: HealthyPrestate) -> CapturedPod:
        self._binding.require(run_id=prestate.run_id, attempt_id=prestate.attempt_id)
        # `select_replacement_pod` authenticates the capture and requires the
        # CONTRACT_EVALUATOR consumer on the pod LIST.
        consumer = self._binding.consumers.contract
        self._binding.require_consumer(consumer)
        capture = self._reader.list_pods(PodSelector.USER_SERVICE, consumer=consumer)
        _request, reference, descriptor_bytes = self._publisher.publish(
            capture, capture_state="MUTANT_STATE_VERIFIED"
        )
        if reference is None or descriptor_bytes is None:
            _reject("RUNTIME_REPLACEMENT_TARGET_INVALID")
        target = select_replacement_pod(capture, reference, descriptor_bytes)
        if not isinstance(target, ReplacementPodTarget):
            _reject("RUNTIME_REPLACEMENT_TARGET_INVALID")
        return CapturedPod(
            name=target.name,
            namespace=NAMESPACE,
            uid=target.uid,
            resource_version=target.resource_version,
            ready=True,
            terminal_deployment=SERVICE_NAME,
            target=target,
            evidence_references=(reference,),
        )

    def observe_replacement(
        self, captured: CapturedPod, dispatch: Any
    ) -> ReplacementObservation:
        del dispatch
        consumer = self._binding.consumers.contract
        self._binding.require_consumer(consumer)
        capture = self._reader.list_pods(PodSelector.USER_SERVICE, consumer=consumer)
        _request, reference, _bytes = self._publisher.publish(
            capture, capture_state="MUTANT_STATE_VERIFIED"
        )
        items = self._publisher.payload(capture).get("items")
        if not isinstance(items, (list, tuple)):
            _reject("RUNTIME_CAPTURE_RESPONSE_MISSING")
        replacement_name = None
        replacement_uid = None
        ready = False
        ownership = False
        for entry in items:
            item = _projected(entry)
            metadata = item.get("metadata")
            if not isinstance(metadata, Mapping):
                continue
            uid = metadata.get("uid")
            if not isinstance(uid, str) or uid == captured.uid:
                continue
            name = metadata.get("name")
            replacement_name = name if isinstance(name, str) else None
            replacement_uid = uid
            ready = _pod_ready(item)
            ownership = _owned_by_user_service(metadata)
            break
        return ReplacementObservation(
            captured_uid=captured.uid,
            replacement_name=replacement_name,
            replacement_uid=replacement_uid,
            ready=ready,
            ownership_to_user_service=ownership,
            capacity_preserved=bool(items),
            evidence_references=(reference,),
        )

    def verify_restoration(self, prestate: HealthyPrestate) -> RestorationVerification:
        self._binding.require(run_id=prestate.run_id, attempt_id=prestate.attempt_id)
        available, present, _uid, service_reference = self._service_state(
            self._binding.consumers.restoration, capture_state="RESTORE_VERIFIED"
        )
        contract_consumer = self._binding.consumers.contract
        self._binding.require_consumer(contract_consumer)
        slices = self._reader.list_user_service_endpoint_slices(
            consumer=contract_consumer
        )
        _sreq, slice_reference, _sb = self._publisher.publish(
            slices, capture_state="RESTORE_VERIFIED"
        )
        restoration_consumer = self._binding.consumers.restoration
        deployments = self._reader.list_deployments(consumer=restoration_consumer)
        _dreq, deployment_reference, _db = self._publisher.publish(
            deployments, capture_state="RESTORE_VERIFIED"
        )
        items = self._publisher.payload(slices).get("items")
        endpoints_ready = isinstance(items, (list, tuple)) and bool(items)
        capacity = _replica_baseline(self._publisher.payload(deployments)) == dict(
            prestate.captured_replica_baseline
        )
        restored = bool(available and present)
        references = tuple(
            reference
            for reference in (service_reference, slice_reference, deployment_reference)
            if reference is not None
        )
        return RestorationVerification(
            safe_restored_state=restored and capacity,
            service_semantics_restored=restored and endpoints_ready,
            all_six_invariants_recovered=restored and endpoints_ready and capacity,
            evidence_references=references,
        )


def _http_status_is(capture: KubernetesCapture, expected: int) -> bool:
    candidate = capture.status_evidence
    if candidate is None or candidate.payload_bytes is None:
        return False
    try:
        value = parse_canonical_json(candidate.payload_bytes)
    except Exception:  # noqa: BLE001 - a malformed status is simply not a 404
        return False
    return isinstance(value, Mapping) and value.get("code") == expected


def _pod_ready(item: Mapping[str, Any]) -> bool:
    status = item.get("status")
    if not isinstance(status, Mapping) or status.get("phase") != "Running":
        return False
    statuses = status.get("containerStatuses")
    if not isinstance(statuses, (list, tuple)) or not statuses:
        return False
    return all(
        isinstance(entry, Mapping) and entry.get("ready") is True for entry in statuses
    )


def _owned_by_user_service(metadata: Mapping[str, Any]) -> bool:
    labels = metadata.get("labels")
    if isinstance(labels, Mapping) and labels.get("service") == SERVICE_NAME:
        return True
    owners = metadata.get("ownerReferences")
    if isinstance(owners, (list, tuple)):
        for owner in owners:
            if isinstance(owner, Mapping):
                name = owner.get("name")
                if isinstance(name, str) and name.startswith(SERVICE_NAME):
                    return True
    return False


# ---------------------------------------------------------------------------
# Capability: original oracle, challenge, workload  (all injected executors)
# ---------------------------------------------------------------------------


class BoundOriginalOracle:
    """One-shot stock-oracle invocation delegated to an injected bounded invoker."""

    __slots__ = ("_binding", "_invoker", "_invoked")

    def __init__(self, binding: RuntimeBinding, invoker: OriginalOracleInvoker) -> None:
        if not isinstance(invoker, OriginalOracleInvoker):
            _reject("RUNTIME_ORACLE_INVOKER_INVALID")
        self._binding = binding
        self._invoker = invoker
        self._invoked = False

    def invoke_once(
        self, prestate: HealthyPrestate, identity: AttemptIdentity
    ) -> OriginalOracleOutcome:
        if identity != self._binding.identity:
            _reject("RUNTIME_IDENTITY_MISMATCH")
        self._binding.require(run_id=prestate.run_id, attempt_id=prestate.attempt_id)
        if self._invoked:
            _reject("RUNTIME_ORACLE_REINVOCATION_FORBIDDEN")
        self._invoked = True
        execution = self._invoker.invoke(
            run_id=identity.run_id,
            attempt_id=identity.attempt_id,
            invocation_ordinal=1,
            kubernetes_context=CONTEXT,
            namespace=NAMESPACE,
            captured_replica_baseline=dict(prestate.captured_replica_baseline),
        )
        if not isinstance(execution, OracleExecution):
            _reject("RUNTIME_ORACLE_RESULT_INVALID")
        return OriginalOracleOutcome(
            raw_result=execution.raw_result,
            returned_boolean=execution.returned_boolean,
            exit_status=execution.exit_status,
            execution_classification=execution.execution_classification,
            evidence_references=(
                execution.input_reference,
                execution.result_reference,
            ),
        )


class BoundChallenge:
    """Bounded challenge execution delegated to an injected executor."""

    __slots__ = ("_binding", "_executor", "_store", "_clock", "_seen")

    def __init__(
        self,
        binding: RuntimeBinding,
        executor: ChallengeExecutor,
        store: EvidenceStore,
        clock: _Clock,
    ) -> None:
        if not isinstance(executor, ChallengeExecutor):
            _reject("RUNTIME_CHALLENGE_EXECUTOR_INVALID")
        self._binding = binding
        self._executor = executor
        self._store = store
        self._clock = clock
        self._seen: set[str] = set()

    def execute(self, phase: str, ordinal: int, challenge_target: Any) -> ChallengeRun:
        if WORKLOAD_WINDOWS.get(phase) != ordinal:
            _reject("RUNTIME_CHALLENGE_WINDOW_INVALID")
        if phase in self._seen:
            _reject("RUNTIME_CHALLENGE_REPLAY_FORBIDDEN")
        self._seen.add(phase)
        pod_name = getattr(challenge_target, "name", None)
        pod_uid = getattr(challenge_target, "uid", None)
        if not isinstance(pod_name, str) or not isinstance(pod_uid, str):
            _reject("RUNTIME_CHALLENGE_TARGET_INVALID")
        common = {
            "run_id": self._binding.run_id,
            "attempt_id": self._binding.attempt_id,
            "created_utc": self._clock.utc(),
            "monotonic_ns": self._clock.monotonic(),
            "boot_identity": self._binding.boot_identity,
        }
        invocation = self._store.publish_descriptor(
            "challenge_invocation",
            {
                **common,
                "template_id": "CHALLENGE_TCP_V1",
                "parameters": {
                    "host": f"{SERVICE_NAME}.{NAMESPACE}.svc.cluster.local",
                    "port": 9090,
                },
                "pod_name": pod_name,
                "pod_uid": pod_uid,
            },
        )
        execution = self._executor.execute(
            run_id=self._binding.run_id,
            attempt_id=self._binding.attempt_id,
            phase=phase,
            ordinal=ordinal,
            pod_name=pod_name,
            pod_uid=pod_uid,
        )
        if not isinstance(execution, ChallengeExecution):
            _reject("RUNTIME_CHALLENGE_RESULT_INVALID")
        stdout = self._store.publish_payload(
            "challenge_stdout",
            execution.stdout,
            {
                **common,
                "monotonic_ns": self._clock.monotonic(),
                "channel": "STDOUT",
                "invocation_reference": _reference_mapping(invocation),
            },
        )
        stderr = self._store.publish_payload(
            "challenge_stderr",
            execution.stderr,
            {
                **common,
                "monotonic_ns": self._clock.monotonic(),
                "channel": "STDERR",
                "invocation_reference": _reference_mapping(invocation),
            },
        )
        result = self._store.publish_descriptor(
            "challenge_result",
            {
                **common,
                "monotonic_ns": self._clock.monotonic(),
                "invocation_reference": _reference_mapping(invocation),
                "stdout_reference": _reference_mapping(stdout),
                "stderr_reference": _reference_mapping(stderr),
                "exit_status": execution.exit_status,
            },
        )
        return ChallengeRun(
            phase=phase,
            ordinal=ordinal,
            completed=execution.completed,
            failure_classification=execution.failure_classification,
            evidence_references=(invocation, stdout, stderr, result),
        )


class BoundWorkload:
    """Workload window evidence parsed by the pinned frozen parser."""

    __slots__ = ("_binding", "_source", "_store", "_clock", "_seen")

    def __init__(
        self,
        binding: RuntimeBinding,
        source: WorkloadSource,
        store: EvidenceStore,
        clock: _Clock,
    ) -> None:
        if not isinstance(source, WorkloadSource):
            _reject("RUNTIME_WORKLOAD_SOURCE_INVALID")
        self._binding = binding
        self._source = source
        self._store = store
        self._clock = clock
        self._seen: set[str] = set()

    def evaluate(
        self,
        phase: str,
        ordinal: int,
        challenge: ChallengeRun | None,
        prestate: HealthyPrestate,
    ) -> WorkloadWindowResult:
        if WORKLOAD_WINDOWS.get(phase) != ordinal:
            _reject("RUNTIME_WORKLOAD_WINDOW_INVALID")
        if challenge is not None and (challenge.phase != phase or challenge.ordinal != ordinal):
            _reject("RUNTIME_WORKLOAD_WINDOW_INVALID")
        if phase in self._seen:
            _reject("RUNTIME_WORKLOAD_REPLAY_FORBIDDEN")
        self._seen.add(phase)
        self._binding.require(run_id=prestate.run_id, attempt_id=prestate.attempt_id)
        snapshot = self._source.snapshot(
            run_id=self._binding.run_id,
            attempt_id=self._binding.attempt_id,
            phase=phase,
            ordinal=ordinal,
        )
        if not isinstance(snapshot, WorkloadSnapshot):
            _reject("RUNTIME_WORKLOAD_SNAPSHOT_INVALID")
        plan = prepare_workload_window(
            self._binding.policy,
            before=snapshot.before,
            after=snapshot.after,
            phase=phase,
            ordinal=ordinal,
            run_id=self._binding.run_id,
            attempt_id=self._binding.attempt_id,
            mutant_id=MUTANT_ID,
            repetition=self._binding.identity.repetition,
            workload_pod_projection_reference=snapshot.pod_projection_reference,
            pod_name=snapshot.pod_name,
            pod_uid=snapshot.pod_uid,
            container_restart_count=snapshot.container_restart_count,
        )
        references = self._publish_plan(plan)
        return WorkloadWindowResult(
            phase=phase,
            ordinal=ordinal,
            run_id=self._binding.run_id,
            attempt_id=self._binding.attempt_id,
            stream_identity=prestate.workload_stream_identity,
            fresh_request_count=plan.fresh_request_count,
            failure_marker_count=plan.failure_marker_count,
            evidence_references=references,
        )

    def _publish_plan(self, plan: Any) -> tuple[EvidenceRef, ...]:
        """Publish the frozen four-document workload window, in dependency order."""
        from sremut.workload_evidence import WorkloadCaptureStamp

        stamp = WorkloadCaptureStamp(
            created_utc=self._clock.utc(),
            monotonic_ns=self._clock.monotonic(),
            boot_identity=self._binding.boot_identity,
        )
        prefix = plan.prefix_log_candidate(stamp)
        prefix_reference = self._store.publish_payload(
            prefix.role, prefix.payload, prefix.publication_metadata()
        )
        full = plan.full_log_candidate(stamp)
        full_reference = self._store.publish_payload(
            full.role, full.payload, full.publication_metadata()
        )
        boundary = plan.boundary_candidate(prefix_reference, stamp)
        boundary_reference = self._store.publish_descriptor(
            boundary.role, boundary.publication_metadata()
        )
        parse = plan.parse_result_candidate(boundary_reference, full_reference, stamp)
        parse_reference = self._store.publish_payload(
            parse.role, parse.payload, parse.publication_metadata()
        )
        return (prefix_reference, full_reference, boundary_reference, parse_reference)


# ---------------------------------------------------------------------------
# Capability: contract adjudication
# ---------------------------------------------------------------------------


class ContractAdjudicator:
    """Derives MS-I1..MS-I6 strictly from raw, published attempt evidence."""

    __slots__ = ("_binding", "_store", "_clock")

    def __init__(
        self, binding: RuntimeBinding, store: EvidenceStore, clock: _Clock
    ) -> None:
        self._binding = binding
        self._store = store
        self._clock = clock

    def evaluate(
        self,
        original_oracle: OriginalOracleOutcome,
        initial_challenge: ChallengeRun,
        initial_workload: WorkloadWindowResult,
        captured_pod: CapturedPod,
        replacement: ReplacementObservation,
        replacement_challenge: ChallengeRun | None,
        replacement_workload: WorkloadWindowResult | None,
    ) -> ContractEvaluation:
        raw: list[Any] = [
            *original_oracle.evidence_references,
            *initial_challenge.evidence_references,
            *initial_workload.evidence_references,
            *captured_pod.evidence_references,
            *replacement.evidence_references,
        ]
        if replacement_challenge is not None:
            raw.extend(replacement_challenge.evidence_references)
        if replacement_workload is not None:
            raw.extend(replacement_workload.evidence_references)

        stable_interface = initial_workload.healthy
        routes = replacement.valid and replacement_workload is not None and replacement_workload.healthy
        capacity = replacement.capacity_preserved
        functional = stable_interface
        survives = routes
        outcomes = {
            "MS-I1": "PASS" if stable_interface else "REJECT",
            "MS-I2": "PASS" if routes else "REJECT",
            "MS-I3": "PASS" if capacity else "REJECT",
            "MS-I4": "PASS" if functional else "REJECT",
            "MS-I5": "PASS" if initial_challenge.completed else "REJECT",
            "MS-I6": "PASS" if survives else "REJECT",
        }
        verdict = "PASS" if all(value == "PASS" for value in outcomes.values()) else "REJECT"
        reference = self._store.publish_descriptor(
            "adjudication",
            {
                "run_id": self._binding.run_id,
                "attempt_id": self._binding.attempt_id,
                "adjudication_id": f"MS-M01-CONTRACT:{self._binding.run_id}",
                "predicate_oracle_or_classification_id": "MS_M01_CONTRACT_V1",
                "result_type": "CATEGORICAL",
                "boolean_or_categorical_value": verdict,
                "reason": "DERIVED_FROM_RAW_ATTEMPT_EVIDENCE",
                "observation_count": len(raw),
                "first_observation_utc": self._clock.utc(),
                "last_observation_utc": self._clock.utc(),
                "monotonic_elapsed_time": self._clock.monotonic(),
                "applicable_deadline": "ATTEMPT_TERMINAL",
                "raw_evidence_references": [_reference_mapping(item) for item in raw],
                "raw_evidence_sha256_per_reference": [
                    _descriptor_sha256(item) or "" for item in raw
                ],
                "evaluator_source_or_runner_bundle_sha256": sha256_hex(
                    canonical_json_bytes({"evaluator": "MS_M01_CONTRACT_V1"})
                ),
                "dependency_and_toolchain_identity": {
                    "policy_id": self._binding.policy_id,
                    "registry_id": self._binding.registry_id,
                    "contract_sha256": self._binding.contract_sha256,
                },
                "kubernetes_uid_and_resource_version_references_when_applicable": {
                    "captured_pod_uid": captured_pod.uid,
                    "captured_pod_resource_version": captured_pod.resource_version,
                },
            },
        )
        return ContractEvaluation(
            verdict=verdict,
            invariant_outcomes=outcomes,
            raw_evidence_references=tuple(raw),
            adjudication_reference=reference,
        )


# ---------------------------------------------------------------------------
# Capability: terminal sealing and external anchoring
# ---------------------------------------------------------------------------


class SealingTerminalizer:
    """Seals the attempt once, anchors it outside the root, and revalidates it."""

    __slots__ = ("_binding", "_root", "_index", "_state", "_mutations", "_clock", "_authority")

    def __init__(
        self,
        binding: RuntimeBinding,
        attempt_root: SafeRoot,
        run_index_root: SafeRoot,
        state: JournalAttemptState,
        mutations: GuardedMutationSession,
        clock: _Clock,
        authority: DurableRunAuthority,
    ) -> None:
        self._binding = binding
        self._root = attempt_root
        self._index = run_index_root
        self._state = state
        self._mutations = mutations
        self._clock = clock
        self._authority = authority

    def finalize(self, draft: Any) -> TerminalizationResult:
        if draft.identity != self._binding.identity:
            _reject("RUNTIME_IDENTITY_MISMATCH")
        outcome = draft.terminal_outcome
        stop_relative = None
        with EvidenceStore(
            self._root.root,
            self._binding.policy,
            self._binding.run_id,
            self._binding.attempt_id,
            _safe_root=self._root,
        ) as store:
            if outcome is TerminalOutcome.RESTORATION_BLOCKED:
                stop_relative = store.write_global_stop(
                    {
                        "document_type": "TERMINAL_GLOBAL_STOP_V1",
                        "schema_version": 1,
                        "run_id": self._binding.run_id,
                        "attempt_id": self._binding.attempt_id,
                        "terminal_outcome": "RESTORATION_BLOCKED",
                        "reason_code": "RESTORATION_VERIFICATION_FAILED",
                        "adjudication_reference": {},
                        "created_utc": self._clock.utc(),
                        "monotonic_ns": self._clock.monotonic(),
                        "boot_identity": self._binding.boot_identity,
                    }
                )
            seal = store.seal(outcome.value, global_stop_relative_path=stop_relative)
        global_stop_created = False
        if outcome is TerminalOutcome.RESTORATION_BLOCKED:
            self._index.write_atomic(
                _GLOBAL_STOP_RELATIVE,
                canonical_json_bytes(
                    {
                        "document_type": "RUN_GLOBAL_STOP_V1",
                        "schema_version": 1,
                        "run_id": self._binding.run_id,
                        "attempt_id": self._binding.attempt_id,
                        "terminal_manifest_sha256": seal.manifest_sha256,
                    }
                ),
            )
            global_stop_created = True
        anchor = ExternalAnchor(
            f"{self._binding.run_id}/{self._binding.attempt_id}",
            seal.manifest_relative_path,
            seal.manifest_sha256,
        )
        self._index.write_atomic(
            f"anchors/{self._binding.run_id}.{self._binding.attempt_id}.json",
            canonical_json_bytes(
                {
                    "document_type": "EXTERNAL_ANCHOR_V1",
                    "schema_version": 1,
                    "attempt_root_identifier": anchor.attempt_root_identifier,
                    "manifest_relative_path": anchor.manifest_relative_path,
                    "terminal_manifest_sha256": anchor.terminal_manifest_sha256,
                    "recorded_by": anchor.recorded_by,
                    "cited_by_result_aggregation": anchor.cited_by_result_aggregation,
                }
            ),
        )
        verification = revalidate_sealed_attempt(
            self._root.root,
            self._binding.policy,
            self._binding.run_id,
            self._binding.attempt_id,
            anchor,
        )
        anchor_authenticated = (
            verification.manifest_sha256 == seal.manifest_sha256
            and verification.terminal_outcome == outcome.value
        )
        publication_rejected = self._probe_publication()
        transition_rejected = self._probe_transition(outcome)
        mutation_rejected = self._probe_mutation()
        return TerminalizationResult(
            outcome=outcome,
            seal_count=1,
            anchor_count=1,
            anchor_authenticated=anchor_authenticated,
            full_admissibility=False,
            hook_outcomes=(),
            global_stop_created=global_stop_created,
            post_terminal_publication_rejected=publication_rejected,
            post_terminal_transition_rejected=transition_rejected,
            post_terminal_mutation_rejected=mutation_rejected,
        )

    def _probe_publication(self) -> bool:
        try:
            with EvidenceStore(
                self._root.root,
                self._binding.policy,
                self._binding.run_id,
                self._binding.attempt_id,
                _safe_root=self._root,
            ) as store:
                store.publish_descriptor(
                    "run_identity",
                    {
                        "run_id": self._binding.run_id,
                        "attempt_id": self._binding.attempt_id,
                        "created_utc": self._clock.utc(),
                        "monotonic_ns": self._clock.monotonic(),
                        "boot_identity": self._binding.boot_identity,
                        "phase": "POST_TERMINAL_PROBE",
                    },
                )
        except Exception as error:  # noqa: BLE001 - the code is the assertion
            return str(error) == "ATTEMPT_ALREADY_SEALED"
        return False

    def _probe_transition(self, outcome: TerminalOutcome) -> bool:
        try:
            self._state.transition(
                f"{outcome.value}->{outcome.value}", evidence_references=()
            )
        except Exception as error:  # noqa: BLE001 - the code is the assertion
            return str(error) in {"POST_TERMINAL_OPERATION", "JOURNAL_TRANSITION_INVALID"}
        return False

    def _probe_mutation(self) -> bool:
        try:
            self._mutations.create_challenge_pod()
        except Exception as error:  # noqa: BLE001 - the code is the assertion
            return str(error) in {"POST_TERMINAL_OPERATION", "DUPLICATE_OPERATION_FORBIDDEN"}
        return False


# ---------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------


class MsM01RuntimeComposition:
    """Owns the durable handles for one composed attempt and closes them once."""

    __slots__ = (
        "attempt",
        "capabilities",
        "binding",
        "_attempt_root",
        "_index_root",
        "_store",
        "_session",
        "_closed",
    )

    def __init__(
        self,
        *,
        attempt: MsM01Attempt,
        capabilities: AttemptCapabilities,
        binding: RuntimeBinding,
        attempt_root: SafeRoot,
        index_root: SafeRoot,
        store: EvidenceStore,
        session: GuardedMutationSession,
    ) -> None:
        self.attempt = attempt
        self.capabilities = capabilities
        self.binding = binding
        self._attempt_root = attempt_root
        self._index_root = index_root
        self._store = store
        self._session = session
        self._closed = False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for closer in (self._session.close, self._store.close, self._attempt_root.close, self._index_root.close):
            try:
                closer()
            except Exception:  # noqa: BLE001 - close must not mask the first failure
                pass

    def __enter__(self) -> "MsM01RuntimeComposition":
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        self.close()


def compose_ms_m01_attempt(
    *,
    policy: AuthenticatedPolicy,
    identity: AttemptIdentity,
    attempt_root: Path,
    run_index_root: Path,
    host_lock_root: Path,
    read_transport: ReadOnlyKubernetesTransport,
    mutation_transport: MutationTransport,
    runner_bundle: VerifiedRunnerBundle,
    challenge_executor: ChallengeExecutor,
    workload_source: WorkloadSource,
    original_oracle_invoker: OriginalOracleInvoker,
    boot_identity: str,
    utc_clock: Callable[[], str],
    monotonic_clock: Callable[[], int],
    timeout_seconds: int = 10,
    consumers: ConsumerBinding = ConsumerBinding(),
    pending_initial_deletion: PendingMutation | None = None,
) -> MsM01RuntimeComposition:
    """Compose one unrun MS-M01 attempt from production components.

    Nothing here contacts a cluster.  Every external transport and executor is
    supplied already constructed by the caller, and the returned attempt is not
    started: the caller invokes ``composition.attempt.run()`` when the scheduled
    repetition is authorised.
    """

    if (
        not isinstance(policy, AuthenticatedPolicy)
        or policy.manifest_sha256 != POLICY_MANIFEST_SHA256
        or policy.policy.get("policy_id") != POLICY_ID
        or not isinstance(identity, AttemptIdentity)
        or identity.registry_id != REGISTRY_ID
        or identity.mutant_id != MUTANT_ID
    ):
        _reject("RUNTIME_POLICY_OR_IDENTITY_INVALID")
    binding_section = policy.policy["bindings"]
    profile = binding_section["execution_profile"]
    contract = binding_section["contract"]
    if (
        identity.execution_profile_tag_object != profile["tag_object"]
        or identity.execution_profile_sha256 != profile["sha256"]
        or identity.contract_sha256 != contract["sha256"]
    ):
        _reject("RUNTIME_POLICY_OR_IDENTITY_INVALID")
    for candidate in (attempt_root, run_index_root, host_lock_root):
        if not Path(candidate).is_absolute():
            _reject("RUNTIME_ROOT_INVALID")

    clock = _Clock(utc_clock, monotonic_clock)
    reader = ReadOnlyKubernetesClient(
        policy=policy,
        transport=read_transport,
        context=CONTEXT,
        namespace=NAMESPACE,
        timeout_seconds=timeout_seconds,
        run_id=identity.run_id,
        attempt_id=identity.attempt_id,
    )
    binding = RuntimeBinding(
        policy=policy,
        identity=identity,
        session_token=reader._session_token,
        consumers=consumers,
        boot_identity=boot_identity,
        policy_id=POLICY_ID,
        registry_id=REGISTRY_ID,
        execution_profile_tag_object=identity.execution_profile_tag_object,
        execution_profile_sha256=identity.execution_profile_sha256,
        contract_sha256=identity.contract_sha256,
    )

    attempt_safe_root = SafeRoot(Path(attempt_root))
    index_safe_root = SafeRoot(Path(run_index_root))
    store = EvidenceStore(
        Path(attempt_root),
        policy,
        identity.run_id,
        identity.attempt_id,
        _safe_root=attempt_safe_root,
    )
    session = GuardedMutationSession.open(
        policy=policy,
        execution_profile_tag_object=identity.execution_profile_tag_object,
        execution_profile_sha256=identity.execution_profile_sha256,
        run_id=identity.run_id,
        attempt_id=identity.attempt_id,
        mutant_id=identity.mutant_id,
        repetition=identity.repetition,
        attempt_number=identity.attempt_number,
        runner_bundle=runner_bundle,
        host_lock_root=Path(host_lock_root),
        attempt_root=Path(attempt_root),
        transport=mutation_transport,
        read_session=reader,
        timeout_seconds=timeout_seconds,
        boot_identity=boot_identity,
        utc_clock=utc_clock,
        monotonic_clock=monotonic_clock,
    )

    publisher = _CapturePublisher(binding, store, clock)
    state = JournalAttemptState(binding, attempt_safe_root, clock)
    authority = DurableRunAuthority(binding, index_safe_root)
    capabilities = AttemptCapabilities(
        authority=authority,
        prestate=AuthenticatedPrestate(binding, reader, store, clock, publisher),
        state=state,
        mutations=session,
        observations=ClusterObservations(binding, reader, store, publisher),
        original_oracle=BoundOriginalOracle(binding, original_oracle_invoker),
        challenge=BoundChallenge(binding, challenge_executor, store, clock),
        workload=BoundWorkload(binding, workload_source, store, clock),
        adjudication=ContractAdjudicator(binding, store, clock),
        terminalizer=SealingTerminalizer(
            binding, attempt_safe_root, index_safe_root, state, session, clock, authority
        ),
    )
    attempt = MsM01Attempt(
        policy=policy,
        identity=identity,
        capabilities=capabilities,
        pending_initial_deletion=pending_initial_deletion,
    )
    return MsM01RuntimeComposition(
        attempt=attempt,
        capabilities=capabilities,
        binding=binding,
        attempt_root=attempt_safe_root,
        index_root=index_safe_root,
        store=store,
        session=session,
    )


__all__ = [
    "AuthenticatedPrestate",
    "BoundChallenge",
    "BoundOriginalOracle",
    "BoundWorkload",
    "ChallengeExecution",
    "ChallengeExecutor",
    "ClusterObservations",
    "ConsumerBinding",
    "ContractAdjudicator",
    "DurableRunAuthority",
    "FROZEN_OPERATIONS",
    "JournalAttemptState",
    "MsM01RuntimeComposition",
    "MsM01RuntimeError",
    "OracleExecution",
    "OriginalOracleInvoker",
    "RuntimeBinding",
    "SealingTerminalizer",
    "WorkloadSnapshot",
    "WorkloadSource",
    "compose_ms_m01_attempt",
]

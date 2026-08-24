"""Closed, typed, read-only Kubernetes evidence capture.

The module deliberately imports no Kubernetes package at import time.  Its
public client exposes only the GET/LIST operations frozen in evidence-policy
v1; it has no generic request or serialization escape hatch.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
import hashlib
import ipaddress
import json
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, NoReturn, Protocol, runtime_checkable

from sremut.canonical_json import canonical_json_bytes, parse_canonical_json
from sremut.evidence import EvidenceRef, descriptor_content_sha256, validate_evidence_ref
from sremut.policy_runtime import AuthenticatedPolicy
from sremut.sensitive import SensitiveCaptureError, validate_payload, validate_projection_allowlist


REQUEST_DOCUMENT = "KUBERNETES_REQUEST_V1"
LIST_RESPONSE_DOCUMENT = "KUBERNETES_LIST_RESPONSE_V1"
PREFLIGHT_MARKER = "NON_RESEARCH_READ_ONLY_PREFLIGHT"
_PURPOSE = "CAPTURE_ADMISSIBLE_EVIDENCE_OR_PROFILE_AUTHORIZED_OPERATION"
_MAXIMUM_TIMEOUT_SECONDS = 30
_REQUEST_KEYS = frozenset(
    {
        "document_type", "schema_version", "request_rule_id", "context",
        "api_group", "api_version", "resource", "response_kind", "subresource",
        "operation", "namespace", "name", "label_selector", "field_selector",
        "request_options", "purpose", "consumer", "projection_class",
        "captured_object_name", "captured_object_uid",
        "captured_object_resource_version", "captured_object_reference",
        "uid_precondition", "authorized_operation_kind", "intent_reference",
    }
)
_READ_RULE_METHODS = MappingProxyType(
    {
        ("DEPLOYMENTS_SOCIAL_READ", "GET"): "AppsV1Api.read_namespaced_deployment",
        ("DEPLOYMENTS_SOCIAL_READ", "LIST"): "AppsV1Api.list_namespaced_deployment",
        ("COREDNS_DEPLOYMENT_READ", "GET"): "AppsV1Api.read_namespaced_deployment",
        ("REPLICASET_OWNER_READ", "GET"): "AppsV1Api.read_namespaced_replica_set",
        ("PODS_SOCIAL_READ_AND_GUARDED_MUTATION", "GET"): "CoreV1Api.read_namespaced_pod",
        ("PODS_SOCIAL_READ_AND_GUARDED_MUTATION", "LIST"): "CoreV1Api.list_namespaced_pod",
        ("COREDNS_PODS_READ", "LIST"): "CoreV1Api.list_namespaced_pod",
        ("USER_SERVICE_READ_AND_GUARDED_MUTATION", "GET"): "CoreV1Api.read_namespaced_service",
        ("USER_SERVICE_ENDPOINT_SLICES_READ", "LIST"): "DiscoveryV1Api.list_namespaced_endpoint_slice",
    }
)


@dataclass(slots=True)
class KubernetesReadOnlyError(ValueError):
    """A stable failure that never renders Kubernetes or credential content."""

    code: str
    capture: Any | None = field(default=None, repr=False, compare=False)

    def __str__(self) -> str:
        return self.code


def _reject(code: str, capture: Any | None = None) -> NoReturn:
    raise KubernetesReadOnlyError(code, capture) from None


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


def _reference_dict(value: EvidenceRef | Mapping[str, Any] | None) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, EvidenceRef):
        return value.as_dict()
    if isinstance(value, Mapping):
        return _thaw(value)
    _reject("KUBERNETES_CAPTURE_UNRESOLVED")


class KubernetesConsumer(str, Enum):
    LIVE_PREFLIGHT = "LIVE_PREFLIGHT"
    CONTRACT_EVALUATOR = "CONTRACT_EVALUATOR"
    RESTORATION_CONTROLLER = "RESTORATION_CONTROLLER"
    ADJUDICATOR = "ADJUDICATOR"
    MUTATION_CONTROLLER = "MUTATION_CONTROLLER"


class PodSelector(str, Enum):
    ALL = ""
    USER_SERVICE = "service=user-service"
    WORKLOAD = "job-name=wrk2-job"
    MUTANT_BACKEND = "sremut-mutant-backend=ms-m02"


def _validate_identity_source_request(
    request: Mapping[str, Any],
    *,
    expected_kind: str,
) -> None:
    if expected_kind == "Pod":
        expected = (
            "PODS_SOCIAL_READ_AND_GUARDED_MUTATION",
            "LIST",
            "social-network",
            PodSelector.USER_SERVICE.value,
            "",
        )
    elif expected_kind == "ReplicaSet":
        expected = (
            "PODS_SOCIAL_READ_AND_GUARDED_MUTATION",
            "GET",
            "social-network",
            "",
            "",
        )
    else:
        _reject("KUBERNETES_CAPTURE_CLASS_INVALID")
    observed = (
        request.get("request_rule_id"),
        request.get("operation"),
        request.get("namespace"),
        request.get("label_selector"),
        request.get("field_selector"),
    )
    if observed != expected:
        _reject("KUBERNETES_CAPTURE_SOURCE_MISMATCH")
    if expected_kind == "ReplicaSet" and request.get(
        "captured_object_reference"
    ) is None:
        _reject("KUBERNETES_CAPTURE_SOURCE_MISMATCH")

def _pod_is_eligible(value: Mapping[str, Any]) -> bool:
    metadata = value.get("metadata")
    status = value.get("status")
    if not isinstance(metadata, Mapping) or not isinstance(status, Mapping):
        return False
    if metadata.get("deletionTimestamp") not in (None, ""):
        return False
    conditions = status.get("conditions")
    if not isinstance(conditions, list):
        return False
    return any(
        isinstance(condition, Mapping)
        and condition.get("type") == "Ready"
        and condition.get("status") == "True"
        for condition in conditions
    )


def _projected_owner_identity(
    value: Mapping[str, Any],
    *,
    source_kind: str,
    owner_kind: str,
) -> tuple[str, str, str, None]:
    _name, namespace, _uid, _resource_version = _object_identity(
        value,
        expected_kind=source_kind,
    )
    owners = value["metadata"].get("ownerReferences", [])
    controllers = [
        owner
        for owner in owners
        if isinstance(owner, Mapping) and owner.get("controller") is True
    ]
    if len(controllers) != 1:
        _reject("KUBERNETES_CAPTURE_OWNER_INVALID")
    owner = controllers[0]
    if (
        owner.get("apiVersion") != "apps/v1"
        or owner.get("kind") != owner_kind
        or not isinstance(owner.get("name"), str)
        or not owner["name"]
        or not isinstance(owner.get("uid"), str)
        or not owner["uid"]
    ):
        _reject("KUBERNETES_CAPTURE_OWNER_INVALID")
    return owner["name"], namespace, owner["uid"], None


def _projected_identity(
    policy: AuthenticatedPolicy,
    payload: bytes,
    *,
    expected_kind: str,
    item_name: str | None,
) -> tuple[str, str, str, str | None]:
    value = parse_canonical_json(payload)
    if isinstance(value, dict) and value.get("document_type") == LIST_RESPONSE_DOCUMENT:
        validate_kubernetes_list_response(policy, value)
        matches = [
            item["projected_fields"]
            for item in value["items"]
            if item.get("kind") == expected_kind
            and (item_name is None or item.get("name") == item_name)
            and (expected_kind != "Pod" or _pod_is_eligible(item["projected_fields"]))
        ]
        if len(matches) != 1:
            _reject("KUBERNETES_CAPTURE_UNRESOLVED")
        return _object_identity(matches[0], expected_kind=expected_kind)
    if not isinstance(value, dict):
        _reject("KUBERNETES_CAPTURE_UNRESOLVED")
    if expected_kind == "ReplicaSet" and value.get("kind") == "Pod":
        return _projected_owner_identity(
            value,
            source_kind="Pod",
            owner_kind="ReplicaSet",
        )
    if item_name is not None and value.get("metadata", {}).get("name") != item_name:
        _reject("KUBERNETES_CAPTURE_UNRESOLVED")
    return _object_identity(value, expected_kind=expected_kind)


@dataclass(frozen=True, slots=True, init=False)
class CapturedObjectIdentity:
    """Provisional or resolved identity derived only from validated projection bytes."""

    name: str
    namespace: str
    uid: str
    resource_version: str | None
    kind: str
    reference: Mapping[str, Any] = field(repr=False)
    _policy_manifest_sha256: str = field(repr=False, compare=False)
    _session_token: object | None = field(repr=False, compare=False)
    _run_id: str = field(repr=False, compare=False)
    _attempt_id: str = field(repr=False, compare=False)
    _consumer: str = field(repr=False, compare=False)
    _source_request_identity_sha256: str = field(repr=False, compare=False)
    _source_projection_sha256: str = field(repr=False, compare=False)

    @classmethod
    def _build(
        cls,
        identity: tuple[str, str, str, str | None],
        *,
        expected_kind: str,
        reference: EvidenceRef | Mapping[str, Any],
        policy_manifest_sha256: str,
        session_token: object | None,
        run_id: str,
        attempt_id: str,
        consumer: str,
        source_request_identity_sha256: str,
        source_projection_sha256: str,
    ) -> CapturedObjectIdentity:
        instance = object.__new__(cls)
        for name, child in {
            "name": identity[0],
            "namespace": identity[1],
            "uid": identity[2],
            "resource_version": identity[3],
            "kind": expected_kind,
            "reference": _freeze(_reference_dict(reference)),
            "_policy_manifest_sha256": policy_manifest_sha256,
            "_session_token": session_token,
            "_run_id": run_id,
            "_attempt_id": attempt_id,
            "_consumer": consumer,
            "_source_request_identity_sha256": source_request_identity_sha256,
            "_source_projection_sha256": source_projection_sha256,
        }.items():
            object.__setattr__(instance, name, child)
        return instance

    @classmethod
    def from_capture(
        cls,
        capture: KubernetesCapture,
        reference: EvidenceRef | Mapping[str, Any],
        descriptor_bytes: bytes,
        *,
        expected_kind: str,
        item_name: str | None = None,
    ) -> CapturedObjectIdentity:
        """Derive an unpublished, session-bound identity from retained projection bytes."""

        if (
            not isinstance(capture, KubernetesCapture)
            or getattr(capture, "_session_token", None) is None
            or capture.response_evidence is None
            or capture.response_evidence.payload_bytes is None
            or not isinstance(descriptor_bytes, bytes)
        ):
            _reject("KUBERNETES_CAPTURE_CLASS_INVALID")
        payload = capture.response_evidence.payload_bytes
        policy = capture._policy
        try:
            validate_prepublication_capture(policy, capture)
            _validate_identity_source_request(capture.request.canonical, expected_kind=expected_kind)
            validated = validate_evidence_ref(
                policy,
                reference,
                descriptor_bytes=descriptor_bytes,
                payload_bytes=payload,
                expected_run_id=capture.request.run_id,
                expected_attempt_id=capture.request.attempt_id,
            )
            descriptor = parse_canonical_json(descriptor_bytes)
            if (
                validated.role != "kubernetes_object_projection"
                or not isinstance(descriptor, dict)
                or descriptor.get("projection_class")
                != capture.request.canonical["projection_class"]
                or descriptor.get("trusted_source_sha256")
                != hashlib.sha256(payload).hexdigest()
                or descriptor.get("request_identity_reference") is None
                or validated.projection_class
                != capture.request.canonical["projection_class"]
            ):
                _reject("KUBERNETES_CAPTURE_SOURCE_MISMATCH")
            identity = _projected_identity(
                policy,
                payload,
                expected_kind=expected_kind,
                item_name=item_name,
            )
        except KubernetesReadOnlyError:
            raise
        except Exception:
            _reject("KUBERNETES_CAPTURE_UNRESOLVED")
        return cls._build(
            identity,
            expected_kind=expected_kind,
            reference=validated,
            policy_manifest_sha256=policy.manifest_sha256,
            session_token=capture._session_token,
            run_id=capture.request.run_id,
            attempt_id=capture.request.attempt_id,
            consumer=capture.request.canonical["consumer"],
            source_request_identity_sha256=capture.request.identity_sha256,
            source_projection_sha256=hashlib.sha256(payload).hexdigest(),
        )

    @classmethod
    def from_resolved_context(
        cls,
        context: Any,
        reference: EvidenceRef | Mapping[str, Any],
        *,
        expected_kind: str,
        item_name: str | None = None,
    ) -> CapturedObjectIdentity:
        from sremut.resolved_context import ResolvedEvidenceContext

        if not isinstance(context, ResolvedEvidenceContext):
            _reject("KUBERNETES_CAPTURE_UNRESOLVED")
        try:
            row = context.resolve_reference(reference)
            if row.reference.role != "kubernetes_object_projection" or row.payload_bytes is None:
                _reject("KUBERNETES_CAPTURE_CLASS_INVALID")
            descriptor = _thaw(row.descriptor)
            request_reference = descriptor.get("request_identity_reference")
            if not isinstance(request_reference, Mapping):
                _reject("KUBERNETES_CAPTURE_SOURCE_MISMATCH")
            request_row = context.resolve_reference(request_reference)
            canonical_request = _thaw(request_row.descriptor).get("canonical_request")
            _validate_identity_source_request(canonical_request, expected_kind=expected_kind)
            request_sha256 = _thaw(request_row.descriptor).get(
                "request_identity_sha256"
            )
            if (
                not isinstance(canonical_request, Mapping)
                or not isinstance(request_sha256, str)
                or canonical_request.get("consumer") is None
            ):
                _reject("KUBERNETES_CAPTURE_SOURCE_MISMATCH")
            identity = _projected_identity(
                context._policy,
                row.payload_bytes,
                expected_kind=expected_kind,
                item_name=item_name,
            )
        except KubernetesReadOnlyError:
            raise
        except Exception:
            _reject("KUBERNETES_CAPTURE_UNRESOLVED")
        return cls._build(
            identity,
            expected_kind=expected_kind,
            reference=row.reference,
            policy_manifest_sha256=context._policy.manifest_sha256,
            session_token=None,
            run_id=context.run_id,
            attempt_id=context.attempt_id,
            consumer=canonical_request["consumer"],
            source_request_identity_sha256=request_sha256,
            source_projection_sha256=hashlib.sha256(row.payload_bytes).hexdigest(),
        )


@dataclass(frozen=True, slots=True, init=False)
class KubernetesRequestIdentity:
    canonical: Mapping[str, Any]
    canonical_bytes: bytes = field(repr=False)
    identity_sha256: str
    run_id: str
    attempt_id: str
    transport_timeout_seconds: int
    typed_method: str
    _policy: AuthenticatedPolicy = field(repr=False, compare=False)

    @classmethod
    def _create(
        cls,
        policy: AuthenticatedPolicy,
        request: Mapping[str, Any],
        *,
        run_id: str,
        attempt_id: str,
        timeout_seconds: int,
    ) -> KubernetesRequestIdentity:
        canonical = validate_kubernetes_read_request(policy, request)
        if (
            not isinstance(run_id, str) or not run_id
            or not isinstance(attempt_id, str) or not attempt_id
            or not isinstance(timeout_seconds, int) or isinstance(timeout_seconds, bool)
            or timeout_seconds < 1 or timeout_seconds > _MAXIMUM_TIMEOUT_SECONDS
        ):
            _reject("KUBERNETES_REQUEST_RULE_INVALID")
        method = _READ_RULE_METHODS.get((canonical["request_rule_id"], canonical["operation"]))
        if method is None:
            _reject("KUBERNETES_REQUEST_RULE_INVALID")
        request_bytes = canonical_json_bytes(canonical)
        material = {"request": canonical, "run_id": run_id, "attempt_id": attempt_id}
        instance = object.__new__(cls)
        for name, value in {
            "canonical": _freeze(canonical),
            "canonical_bytes": bytes(request_bytes),
            "identity_sha256": hashlib.sha256(canonical_json_bytes(material)).hexdigest(),
            "run_id": run_id,
            "attempt_id": attempt_id,
            "transport_timeout_seconds": timeout_seconds,
            "typed_method": method,
            "_policy": policy,
        }.items():
            object.__setattr__(instance, name, value)
        return instance

@dataclass(frozen=True, slots=True)
class EvidenceCandidate:
    role: str
    producer: str
    media_type: str
    storage_class: str
    payload_bytes: bytes | None = field(repr=False)
    metadata: Mapping[str, Any] = field(repr=False)
    expected_request_identity_sha256: str | None = field(default=None, repr=False)
    expected_canonical_request: Mapping[str, Any] | None = field(default=None, repr=False)

    def publication_metadata(
        self,
        *,
        run_id: str,
        attempt_id: str,
        created_utc: str,
        monotonic_ns: int,
        boot_identity: str,
        request_identity_reference: EvidenceRef | Mapping[str, Any] | None = None,
        request_identity_descriptor_bytes: bytes | None = None,
    ) -> Mapping[str, Any]:
        value = {
            "run_id": run_id, "attempt_id": attempt_id, "created_utc": created_utc,
            "monotonic_ns": monotonic_ns, "boot_identity": boot_identity,
            **_thaw(self.metadata),
        }
        if self.role != "kubernetes_request_identity":
            reference = _reference_dict(request_identity_reference)
            if (
                reference is None
                or reference.get("document_type") != "DESCRIPTOR_EVIDENCE_REF_V1"
                or reference.get("role") != "kubernetes_request_identity"
                or reference.get("producer") != "RUNNER_KUBERNETES_CLIENT"
                or reference.get("source_kind") != "KUBERNETES"
                or reference.get("media_type") != "application/json"
                or reference.get("storage_class") != "DESCRIPTOR_ONLY"
                or not isinstance(request_identity_descriptor_bytes, bytes)
                or self.expected_request_identity_sha256 is None
                or self.expected_canonical_request is None
            ):
                _reject("KUBERNETES_REQUEST_IDENTITY_MISMATCH")
            try:
                descriptor = parse_canonical_json(request_identity_descriptor_bytes)
                if not isinstance(descriptor, dict):
                    _reject("KUBERNETES_REQUEST_IDENTITY_MISMATCH")
                digest = descriptor_content_sha256(descriptor)
            except KubernetesReadOnlyError:
                raise
            except Exception:
                _reject("KUBERNETES_REQUEST_IDENTITY_MISMATCH")
            if (
                descriptor.get("role") != "kubernetes_request_identity"
                or descriptor.get("run_id") != run_id
                or descriptor.get("attempt_id") != attempt_id
                or descriptor.get("canonical_request") != _thaw(self.expected_canonical_request)
                or descriptor.get("request_identity_sha256") != self.expected_request_identity_sha256
                or reference.get("descriptor_sha256") != digest
                or reference.get("evidence_id") != "ev-" + digest[:32]
                or reference.get("descriptor_relative_path")
                != f"descriptors/sha256/{digest[:2]}/{digest}.json"
            ):
                _reject("KUBERNETES_REQUEST_IDENTITY_MISMATCH")
            value["request_identity_reference"] = reference
        return value


@dataclass(frozen=True, slots=True, init=False)
class KubernetesCapture:
    classification: str
    request: KubernetesRequestIdentity
    request_evidence: EvidenceCandidate
    response_evidence: EvidenceCandidate | None
    status_evidence: EvidenceCandidate
    error_body_evidence: EvidenceCandidate | None
    typed_method: str
    _policy: AuthenticatedPolicy = field(repr=False, compare=False)
    _session_token: object = field(repr=False, compare=False)

    @classmethod
    def _create(
        cls,
        *,
        classification: str,
        request: KubernetesRequestIdentity,
        request_evidence: EvidenceCandidate,
        response_evidence: EvidenceCandidate | None,
        status_evidence: EvidenceCandidate,
        error_body_evidence: EvidenceCandidate | None,
        typed_method: str,
        policy: AuthenticatedPolicy,
        session_token: object,
    ) -> KubernetesCapture:
        instance = object.__new__(cls)
        for name, value in {
            "classification": classification,
            "request": request,
            "request_evidence": request_evidence,
            "response_evidence": response_evidence,
            "status_evidence": status_evidence,
            "error_body_evidence": error_body_evidence,
            "typed_method": typed_method,
            "_policy": policy,
            "_session_token": session_token,
        }.items():
            object.__setattr__(instance, name, value)
        return instance


@dataclass(slots=True)
class _TransportFailure(Exception):
    code: str
    http_status: int | None = None
    body: bytes | None = field(default=None, repr=False)

    def __str__(self) -> str:
        return self.code


@dataclass(frozen=True, slots=True)
class TransportCall:
    method: str
    namespace: str
    name: str
    label_selector: str
    field_selector: str
    timeout_seconds: int
    watch: bool = False


@runtime_checkable
class ReadOnlyKubernetesTransport(Protocol):
    """The exact seven generated-client methods needed by frozen read rules."""

    def read_namespaced_deployment(self, name: str, namespace: str, timeout_seconds: int) -> Any: ...
    def list_namespaced_deployment(self, namespace: str, label_selector: str, field_selector: str, timeout_seconds: int) -> Any: ...
    def read_namespaced_replica_set(self, name: str, namespace: str, timeout_seconds: int) -> Any: ...
    def read_namespaced_pod(self, name: str, namespace: str, timeout_seconds: int) -> Any: ...
    def list_namespaced_pod(self, namespace: str, label_selector: str, field_selector: str, timeout_seconds: int) -> Any: ...
    def read_namespaced_service(self, name: str, namespace: str, timeout_seconds: int) -> Any: ...
    def list_namespaced_endpoint_slice(self, namespace: str, label_selector: str, field_selector: str, timeout_seconds: int) -> Any: ...


class Kubernetes32ReadOnlyTransport:
    """Explicit Kubernetes 32.0.1 transport with no default config discovery."""

    __slots__ = ("_apps", "_core", "_discovery", "_calls", "_closed", "_api_client")

    def __init__(
        self,
        *,
        kubeconfig_path: Path,
        context: str,
        policy: AuthenticatedPolicy,
        namespace: str,
        timeout_seconds: int,
    ) -> None:
        if (
            not isinstance(policy, AuthenticatedPolicy)
            or context != "kind-kind"
            or namespace != "social-network"
            or not isinstance(timeout_seconds, int) or isinstance(timeout_seconds, bool)
            or not 1 <= timeout_seconds <= _MAXIMUM_TIMEOUT_SECONDS
        ):
            _reject("KUBERNETES_CLIENT_CONFIGURATION_INVALID")
        path = Path(kubeconfig_path)
        if not path.is_absolute() or not path.is_file():
            _reject("KUBERNETES_CLIENT_CONFIGURATION_INVALID")
        try:
            import kubernetes
            from kubernetes import client, config
        except Exception:
            _reject("KUBERNETES_CLIENT_IMPORT_FAILED")
        if getattr(kubernetes, "__version__", None) != "32.0.1":
            _reject("KUBERNETES_CLIENT_VERSION_MISMATCH")
        configuration = client.Configuration()
        try:
            config.load_kube_config(
                config_file=str(path),
                context=context,
                client_configuration=configuration,
                persist_config=False,
            )
            configuration.retries = 0
            api_client = client.ApiClient(configuration=configuration)
            self._apps = client.AppsV1Api(api_client)
            self._core = client.CoreV1Api(api_client)
            self._discovery = client.DiscoveryV1Api(api_client)
        except Exception:
            _reject("KUBERNETES_CLIENT_CONFIGURATION_INVALID")
        self._api_client = api_client
        self._calls: list[TransportCall] = []
        self._closed = False

    @property
    def calls(self) -> tuple[TransportCall, ...]:
        return tuple(self._calls)

    def close(self) -> None:
        if not self._closed:
            self._api_client.close()
            self._closed = True

    def __enter__(self) -> Kubernetes32ReadOnlyTransport:
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        self.close()

    def _invoke(self, method: str, function: Any, *, namespace: str, name: str = "", label_selector: str = "", field_selector: str = "", timeout_seconds: int) -> Any:
        if self._closed:
            _reject("KUBERNETES_TRANSPORT_CLOSED")
        call = TransportCall(method, namespace, name, label_selector, field_selector, timeout_seconds)
        self._calls.append(call)
        kwargs: dict[str, Any] = {"namespace": namespace, "pretty": False, "_request_timeout": timeout_seconds}
        if name:
            kwargs["name"] = name
        else:
            kwargs.update({"label_selector": label_selector, "field_selector": field_selector, "watch": False})
        try:
            return function(**kwargs)
        except Exception as error:
            _raise_transport_failure(error, internal=True)

    def read_namespaced_deployment(self, name: str, namespace: str, timeout_seconds: int) -> Any:
        return self._invoke("AppsV1Api.read_namespaced_deployment", self._apps.read_namespaced_deployment, name=name, namespace=namespace, timeout_seconds=timeout_seconds)

    def list_namespaced_deployment(self, namespace: str, label_selector: str, field_selector: str, timeout_seconds: int) -> Any:
        return self._invoke("AppsV1Api.list_namespaced_deployment", self._apps.list_namespaced_deployment, namespace=namespace, label_selector=label_selector, field_selector=field_selector, timeout_seconds=timeout_seconds)

    def read_namespaced_replica_set(self, name: str, namespace: str, timeout_seconds: int) -> Any:
        return self._invoke("AppsV1Api.read_namespaced_replica_set", self._apps.read_namespaced_replica_set, name=name, namespace=namespace, timeout_seconds=timeout_seconds)

    def read_namespaced_pod(self, name: str, namespace: str, timeout_seconds: int) -> Any:
        return self._invoke("CoreV1Api.read_namespaced_pod", self._core.read_namespaced_pod, name=name, namespace=namespace, timeout_seconds=timeout_seconds)

    def list_namespaced_pod(self, namespace: str, label_selector: str, field_selector: str, timeout_seconds: int) -> Any:
        return self._invoke("CoreV1Api.list_namespaced_pod", self._core.list_namespaced_pod, namespace=namespace, label_selector=label_selector, field_selector=field_selector, timeout_seconds=timeout_seconds)

    def read_namespaced_service(self, name: str, namespace: str, timeout_seconds: int) -> Any:
        return self._invoke("CoreV1Api.read_namespaced_service", self._core.read_namespaced_service, name=name, namespace=namespace, timeout_seconds=timeout_seconds)

    def list_namespaced_endpoint_slice(self, namespace: str, label_selector: str, field_selector: str, timeout_seconds: int) -> Any:
        return self._invoke("DiscoveryV1Api.list_namespaced_endpoint_slice", self._discovery.list_namespaced_endpoint_slice, namespace=namespace, label_selector=label_selector, field_selector=field_selector, timeout_seconds=timeout_seconds)


def _raise_transport_failure(error: Exception, *, internal: bool = False) -> NoReturn:
    name = type(error).__name__.lower()
    module = type(error).__module__.lower()
    code = "KUBERNETES_TRANSPORT_FAILURE"
    status: int | None = None
    body: bytes | None = None
    if "timeout" in name:
        code = "KUBERNETES_TRANSPORT_TIMEOUT"
    elif "ssl" in name or "ssl" in module or "certificate" in name:
        code = "KUBERNETES_TRANSPORT_TLS_FAILURE"
    elif isinstance(error, (json.JSONDecodeError, UnicodeDecodeError)) or "decode" in name:
        code = "KUBERNETES_TRANSPORT_DECODING_FAILURE"
    elif hasattr(error, "status"):
        code = "KUBERNETES_API_FAILURE"
        observed = getattr(error, "status", None)
        status = observed if isinstance(observed, int) and not isinstance(observed, bool) and 100 <= observed <= 599 else None
        raw = getattr(error, "body", None)
        if isinstance(raw, str):
            body = raw.encode("utf-8", errors="strict")
        elif isinstance(raw, bytes):
            body = bytes(raw)
        if body is not None and len(body) > 65536:
            body = None
    if internal:
        raise _TransportFailure(code, status, body) from None
    _reject(code)


def _resource_rules(policy: AuthenticatedPolicy) -> tuple[Mapping[str, Any], ...]:
    try:
        rows = policy.policy["kubernetes_evidence_surface"]["resource_rules"]
    except Exception:
        _reject("KUBERNETES_REQUEST_RULE_INVALID")
    return tuple(rows)


def _rule(policy: AuthenticatedPolicy, rule_id: str) -> Mapping[str, Any]:
    matches = tuple(row for row in _resource_rules(policy) if row.get("id") == rule_id)
    if len(matches) != 1:
        _reject("KUBERNETES_REQUEST_RULE_INVALID")
    return matches[0]


def validate_kubernetes_read_request(
    policy: AuthenticatedPolicy,
    request: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate one exact schema-shaped GET/LIST request against frozen bytes."""

    if not isinstance(policy, AuthenticatedPolicy) or not isinstance(request, Mapping):
        _reject("KUBERNETES_REQUEST_RULE_INVALID")
    value = _thaw(request)
    if set(value) != _REQUEST_KEYS or value.get("document_type") != REQUEST_DOCUMENT or value.get("schema_version") != 1:
        _reject("KUBERNETES_REQUEST_RULE_INVALID")
    if value.get("operation") not in ("GET", "LIST"):
        _reject("KUBERNETES_REQUEST_RULE_INVALID")
    rule = _rule(policy, value.get("request_rule_id", ""))
    exact = {
        "context": rule["context"], "api_group": rule["api_group"],
        "api_version": rule["api_version"], "resource": rule["resource"],
        "response_kind": rule["response_kind"], "subresource": rule["subresource"],
        "namespace": rule["namespace"], "purpose": rule["purpose"],
        "projection_class": rule["projection_class"],
    }
    if any(value.get(key) != expected for key, expected in exact.items()):
        _reject("KUBERNETES_REQUEST_RULE_INVALID")
    if value["operation"] not in rule["operations"]:
        _reject("KUBERNETES_REQUEST_RULE_INVALID")
    if value.get("label_selector") not in rule["label_selectors"] or value.get("field_selector") not in rule["field_selectors"]:
        _reject("KUBERNETES_REQUEST_RULE_INVALID")
    if value.get("request_options") != _thaw(rule["request_options"]):
        _reject("KUBERNETES_REQUEST_RULE_INVALID")
    if value.get("consumer") not in rule["consumers"]:
        _reject("KUBERNETES_REQUEST_RULE_INVALID")
    if any(value.get(key) is not None for key in ("uid_precondition", "authorized_operation_kind", "intent_reference")):
        _reject("KUBERNETES_REQUEST_RULE_INVALID")
    predicates = rule["name_predicates_by_operation"].get(value["operation"], ())
    matched = False
    for predicate in predicates:
        kind = predicate["type"]
        if kind == "LIST_NO_NAME":
            matched = value["name"] == "" and all(value.get(key) is None for key in ("captured_object_name", "captured_object_uid", "captured_object_resource_version", "captured_object_reference"))
        elif kind == "EXACT_NAME":
            matched = value["name"] in predicate["allowed_names"] and all(value.get(key) is None for key in ("captured_object_name", "captured_object_uid", "captured_object_resource_version", "captured_object_reference"))
        elif kind == "CAPTURED_OBJECT_NAME":
            reference = value.get("captured_object_reference")
            captured_resource_version = value.get(
                "captured_object_resource_version"
            )
            resource_version_valid = (
                captured_resource_version is None
                if value["request_rule_id"] == "REPLICASET_OWNER_READ"
                else isinstance(captured_resource_version, str)
                and bool(captured_resource_version)
            )
            matched = (
                isinstance(reference, dict)
                and reference.get("role") == "kubernetes_object_projection"
                and isinstance(value.get("captured_object_name"), str)
                and bool(value["captured_object_name"])
                and value["name"] == value["captured_object_name"]
                and isinstance(value.get("captured_object_uid"), str)
                and bool(value["captured_object_uid"])
                and resource_version_valid
            )
        if matched:
            break
    if not matched or (value["request_rule_id"], value["operation"]) not in _READ_RULE_METHODS:
        _reject("KUBERNETES_REQUEST_RULE_INVALID")
    try:
        policy.structural_validate(value)
    except Exception:
        _reject("KUBERNETES_REQUEST_RULE_INVALID")
    return value


def _request(
    policy: AuthenticatedPolicy,
    rule_id: str,
    operation: str,
    *,
    name: str,
    selector: str,
    consumer: KubernetesConsumer | str,
    captured: CapturedObjectIdentity | None,
) -> dict[str, Any]:
    rule = _rule(policy, rule_id)
    consumer_value = consumer.value if isinstance(consumer, KubernetesConsumer) else consumer
    return {
        "document_type": REQUEST_DOCUMENT, "schema_version": 1,
        "request_rule_id": rule_id, "context": rule["context"],
        "api_group": rule["api_group"], "api_version": rule["api_version"],
        "resource": rule["resource"], "response_kind": rule["response_kind"],
        "subresource": rule["subresource"], "operation": operation,
        "namespace": rule["namespace"], "name": name,
        "label_selector": selector, "field_selector": "",
        "request_options": _thaw(rule["request_options"]), "purpose": _PURPOSE,
        "consumer": consumer_value, "projection_class": rule["projection_class"],
        "captured_object_name": None if captured is None else captured.name,
        "captured_object_uid": None if captured is None else captured.uid,
        "captured_object_resource_version": None if captured is None else captured.resource_version,
        "captured_object_reference": None if captured is None else _thaw(captured.reference),
        "uid_precondition": None, "authorized_operation_kind": None,
        "intent_reference": None,
    }


def _read_attribute(value: Any, token: str) -> tuple[bool, Any]:
    if isinstance(value, Mapping):
        return (token in value, value.get(token))
    attributes = getattr(value, "attribute_map", None)
    if isinstance(attributes, Mapping):
        for attribute, serialized in attributes.items():
            if serialized == token:
                return (True, getattr(value, attribute, None))
    return (False, None)


_LEAF = object()


def _path_tree(paths: Any) -> dict[str, Any]:
    root: dict[str, Any] = {}
    for path in paths:
        node = root
        for token in path:
            node = node.setdefault(token, {})
        node[_LEAF] = True
    return root


def _canonical_scalar(value: Any) -> Any:
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    if isinstance(value, (date, datetime)):
        return value.isoformat().replace("+00:00", "Z")
    _reject("KUBERNETES_PROJECTION_FIELD_FORBIDDEN")


def _project_node(source: Any, tree: Mapping[Any, Any]) -> Any:
    if _LEAF in tree:
        if isinstance(source, (list, tuple)):
            return [_canonical_scalar(value) for value in source]
        return _canonical_scalar(source)
    if "[]" in tree:
        if source is None:
            return []
        if not isinstance(source, (list, tuple)):
            _reject("KUBERNETES_PROJECTION_FIELD_FORBIDDEN")
        return [_project_node(item, tree["[]"]) for item in source]
    result: dict[str, Any] = {}
    for token, child in tree.items():
        if token is _LEAF:
            continue
        present, value = _read_attribute(source, token)
        if present and value is not None:
            result[token] = _project_node(value, child)
    return result


def _object_identity(value: Any, *, expected_kind: str | None = None) -> tuple[str, str, str, str]:
    if not isinstance(value, Mapping):
        _reject("KUBERNETES_LIST_INVALID")
    metadata = value.get("metadata")
    if not isinstance(metadata, Mapping):
        _reject("KUBERNETES_LIST_INVALID")
    values = (metadata.get("name"), metadata.get("namespace"), metadata.get("uid"), metadata.get("resourceVersion"))
    if any(not isinstance(item, str) or not item for item in values):
        _reject("KUBERNETES_LIST_INVALID")
    if expected_kind is not None and value.get("kind") != expected_kind:
        _reject("KUBERNETES_LIST_INVALID")
    return values  # type: ignore[return-value]


_EXPECTED_ITEM_MODELS = MappingProxyType({
    "Deployment": "V1Deployment",
    "ReplicaSet": "V1ReplicaSet",
    "Pod": "V1Pod",
    "Service": "V1Service",
    "EndpointSlice": "V1EndpointSlice",
})
_EXPECTED_LIST_MODELS = MappingProxyType({
    "Deployment": "V1DeploymentList",
    "Pod": "V1PodList",
    "EndpointSlice": "V1EndpointSliceList",
})


def _expected_api_version(rule: Mapping[str, Any]) -> str:
    return (
        f'{rule["api_group"]}/{rule["api_version"]}'
        if rule["api_group"] else rule["api_version"]
    )


def _generated_model_matches(value: Any, expected_name: str) -> bool:
    model_type = type(value)
    return model_type.__name__ == expected_name and model_type.__module__.startswith(
        "kubernetes.client.models."
    )


def _validate_response_model(rule: Mapping[str, Any], value: Any, *, list_envelope: bool = False) -> bool:
    """Bind a response to its authenticated generated model or closed fake shape.

    Returns true only when the expected generated model permits omitted TypeMeta.
    Mapping fakes must carry exact TypeMeta and therefore cannot cross kinds.
    """

    if list_envelope:
        expected = _EXPECTED_LIST_MODELS.get(rule["response_kind"] )
        if expected is None:
            _reject("KUBERNETES_LIST_INVALID")
        if isinstance(value, Mapping):
            if not isinstance(value.get("items"), (list, tuple)):
                _reject("KUBERNETES_LIST_INVALID")
            return False
        if not _generated_model_matches(value, expected):
            _reject("KUBERNETES_LIST_INVALID")
        return True
    expected = _EXPECTED_ITEM_MODELS.get(rule["response_kind"] )
    if expected is None:
        _reject("KUBERNETES_LIST_INVALID")
    if isinstance(value, Mapping):
        if (
            value.get("apiVersion") != _expected_api_version(rule)
            or value.get("kind") != rule["response_kind"]
        ):
            _reject("KUBERNETES_LIST_INVALID")
        return False
    if not _generated_model_matches(value, expected):
        _reject("KUBERNETES_LIST_INVALID")
    return True


def _complete_frozen_type_meta(rule: Mapping[str, Any], value: dict[str, Any]) -> dict[str, Any]:
    """Restore TypeMeta omitted by generated typed response models.

    The values are derived only from the authenticated rule selected before the
    API call; an observed non-null conflicting value remains rejectable.
    """

    expected_api = _expected_api_version(rule)
    value.setdefault("apiVersion", expected_api)
    value.setdefault("kind", rule["response_kind"] )
    return value


def _validate_capture_payload(
    policy: AuthenticatedPolicy,
    role: str,
    payload: bytes,
    *,
    media_type: str,
) -> None:
    if b"-----BEGIN CERTIFICATE-----" in payload or b"-----END CERTIFICATE-----" in payload:
        _reject("SENSITIVE_CAPTURE_REJECTED")
    try:
        validate_payload(policy, role, payload, media_type=media_type)
    except SensitiveCaptureError:
        _reject("SENSITIVE_CAPTURE_REJECTED")


def _bounded_port(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 65535


def _validate_endpoint_slice_shape(value: Mapping[str, Any]) -> None:
    address_type = value.get("addressType")
    if address_type not in ("IPv4", "IPv6", "FQDN"):
        _reject("KUBERNETES_LIST_INVALID")
    ports = value.get("ports", [])
    endpoints = value.get("endpoints", [])
    if not isinstance(ports, list) or not isinstance(endpoints, list):
        _reject("KUBERNETES_LIST_INVALID")
    for port in ports:
        if not isinstance(port, Mapping):
            _reject("KUBERNETES_LIST_INVALID")
        if "port" in port and not _bounded_port(port["port"]):
            _reject("KUBERNETES_LIST_INVALID")
        if "protocol" in port and port["protocol"] not in ("TCP", "UDP", "SCTP"):
            _reject("KUBERNETES_LIST_INVALID")
        if "name" in port and (not isinstance(port["name"], str) or not port["name"]):
            _reject("KUBERNETES_LIST_INVALID")
    for endpoint in endpoints:
        if not isinstance(endpoint, Mapping):
            _reject("KUBERNETES_LIST_INVALID")
        addresses = endpoint.get("addresses")
        if not isinstance(addresses, list) or not addresses:
            _reject("KUBERNETES_LIST_INVALID")
        for address in addresses:
            if not isinstance(address, str) or not address:
                _reject("KUBERNETES_LIST_INVALID")
            if address_type in ("IPv4", "IPv6"):
                try:
                    parsed = ipaddress.ip_address(address)
                except ValueError:
                    _reject("KUBERNETES_LIST_INVALID")
                if parsed.version != (4 if address_type == "IPv4" else 6):
                    _reject("KUBERNETES_LIST_INVALID")
        conditions = endpoint.get("conditions", {})
        if not isinstance(conditions, Mapping) or any(
            key in conditions and not isinstance(conditions[key], bool)
            for key in ("ready", "terminating")
        ):
            _reject("KUBERNETES_LIST_INVALID")
        target = endpoint.get("targetRef")
        if target is not None and (
            not isinstance(target, Mapping)
            or any(not isinstance(item, str) or not item for item in target.values())
        ):
            _reject("KUBERNETES_LIST_INVALID")


def _validate_service_shape(value: Mapping[str, Any]) -> None:
    spec = value.get("spec")
    if not isinstance(spec, Mapping):
        _reject("KUBERNETES_LIST_INVALID")
    ports = spec.get("ports")
    if not isinstance(ports, list):
        _reject("KUBERNETES_LIST_INVALID")
    for port in ports:
        if not isinstance(port, Mapping) or not _bounded_port(port.get("port")):
            _reject("KUBERNETES_LIST_INVALID")
        if "protocol" in port and port["protocol"] not in ("TCP", "UDP", "SCTP"):
            _reject("KUBERNETES_LIST_INVALID")
        target = port.get("targetPort")
        if target is not None and not (
            _bounded_port(target) or (isinstance(target, str) and bool(target))
        ):
            _reject("KUBERNETES_LIST_INVALID")
        if "nodePort" in port and not _bounded_port(port["nodePort"]):
            _reject("KUBERNETES_LIST_INVALID")
    for key in ("publishNotReadyAddresses", "allocateLoadBalancerNodePorts"):
        if key in spec and not isinstance(spec[key], bool):
            _reject("KUBERNETES_LIST_INVALID")


def _validate_projected_shape(rule: Mapping[str, Any], value: Mapping[str, Any]) -> None:
    if rule["response_kind"] == "EndpointSlice":
        _validate_endpoint_slice_shape(value)
    elif rule["response_kind"] == "Service":
        _validate_service_shape(value)


def _validate_projected_object(rule: Mapping[str, Any], value: Any, *, request: Mapping[str, Any], list_item: bool) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        _reject("KUBERNETES_LIST_INVALID")
    thawed = _thaw(value)
    try:
        validate_projection_allowlist(thawed, rule["projection_paths"])
    except SensitiveCaptureError:
        _reject("KUBERNETES_PROJECTION_FIELD_FORBIDDEN")
    name, namespace, _uid, _resource_version = _object_identity(thawed, expected_kind=rule["response_kind"])
    _validate_projected_shape(rule, thawed)
    expected_api = _expected_api_version(rule)
    if thawed.get("apiVersion") != expected_api or namespace != rule["namespace"]:
        _reject("KUBERNETES_LIST_INVALID")
    if not list_item and request["operation"] == "GET" and name != request["name"]:
        _reject("KUBERNETES_LIST_INVALID")
    if not list_item and request.get("captured_object_reference") is not None:
        metadata = thawed["metadata"]
        expected_resource_version = request.get(
            "captured_object_resource_version"
        )
        if (
            metadata["uid"] != request.get("captured_object_uid")
            or expected_resource_version is not None
            and metadata["resourceVersion"] != expected_resource_version
        ):
            _reject("KUBERNETES_CAPTURE_IDENTITY_MISMATCH")
    if rule["response_kind"] == "ReplicaSet":
        owners = thawed["metadata"].get("ownerReferences", [])
        controllers = [
            owner for owner in owners
            if isinstance(owner, dict) and owner.get("controller") is True
        ]
        if len(controllers) != 1 or controllers[0].get("kind") != "Deployment" or controllers[0].get("name") != "user-service" or not controllers[0].get("uid"):
            _reject("KUBERNETES_CAPTURE_OWNER_INVALID")
    if rule["response_kind"] == "Pod" and not list_item and request.get("captured_object_reference") is not None:
        owners = thawed["metadata"].get("ownerReferences", [])
        controllers = [
            owner for owner in owners
            if isinstance(owner, dict) and owner.get("controller") is True
        ]
        if len(controllers) != 1 or controllers[0].get("kind") != "ReplicaSet" or not controllers[0].get("name") or not controllers[0].get("uid"):
            _reject("KUBERNETES_CAPTURE_OWNER_INVALID")
    if list_item:
        predicate = rule["list_item_name_predicate"]
        if predicate["type"] == "EXACT_NAME" and name not in predicate["allowed_names"]:
            _reject("KUBERNETES_LIST_INVALID")
        selector = request["label_selector"]
        if selector:
            key, expected = selector.split("=", 1)
            labels = thawed.get("metadata", {}).get("labels", {})
            if not isinstance(labels, Mapping) or labels.get(key) != expected:
                _reject("KUBERNETES_LIST_INVALID")
    return thawed


def project_kubernetes_response(policy: AuthenticatedPolicy, identity: KubernetesRequestIdentity, raw: Any) -> bytes:
    """Project a typed Kubernetes result without generic model serialization."""

    request = _thaw(identity.canonical)
    rule = _rule(policy, request["request_rule_id"])
    tree = _path_tree(rule["projection_paths"])
    if request["operation"] == "GET":
        _validate_response_model(rule, raw)
        projected = _complete_frozen_type_meta(rule, _project_node(raw, tree))
        validated = _validate_projected_object(rule, projected, request=request, list_item=False)
        payload = canonical_json_bytes(validated)
    else:
        _validate_response_model(rule, raw, list_envelope=True)
        present, items = _read_attribute(raw, "items")
        if not present or not isinstance(items, (list, tuple)) or len(items) > rule["maximum_list_items"]:
            _reject("KUBERNETES_LIST_INVALID")
        present, metadata = _read_attribute(raw, "metadata")
        if not present:
            _reject("KUBERNETES_LIST_INVALID")
        projected_items = []
        for item in items:
            _validate_response_model(rule, item)
            projected = _complete_frozen_type_meta(rule, _project_node(item, tree))
            projected = _validate_projected_object(rule, projected, request=request, list_item=True)
            name, namespace, _uid, _rv = _object_identity(projected)
            projected_items.append(
                {
                    "apiVersion": projected["apiVersion"], "kind": projected["kind"],
                    "namespace": namespace, "name": name,
                    "projection_class": rule["projection_class"],
                    "projected_fields": projected,
                }
            )
        list_metadata: dict[str, Any] = {}
        for token in ("resourceVersion", "continue", "remainingItemCount"):
            found, child = _read_attribute(metadata, token)
            if found and child is not None:
                list_metadata[token] = _canonical_scalar(child)
        if list_metadata.get("continue") not in (None, "") or list_metadata.get("remainingItemCount") not in (None, 0):
            _reject("KUBERNETES_PAGINATION_FORBIDDEN")
        response = {
            "document_type": LIST_RESPONSE_DOCUMENT, "schema_version": 1,
            "request": request, "list_metadata": list_metadata, "items": projected_items,
        }
        validate_kubernetes_list_response(policy, response)
        payload = canonical_json_bytes(response)
    _validate_capture_payload(
        policy, "kubernetes_object_projection", payload, media_type="application/json"
    )
    return payload


def validate_kubernetes_list_response(policy: AuthenticatedPolicy, response: Mapping[str, Any]) -> None:
    value = _thaw(response)
    if set(value) != {"document_type", "schema_version", "request", "list_metadata", "items"} or value.get("document_type") != LIST_RESPONSE_DOCUMENT or value.get("schema_version") != 1:
        _reject("KUBERNETES_LIST_INVALID")
    request = validate_kubernetes_read_request(policy, value.get("request", {}))
    if request["operation"] != "LIST":
        _reject("KUBERNETES_LIST_INVALID")
    rule = _rule(policy, request["request_rule_id"])
    metadata = value.get("list_metadata")
    if not isinstance(metadata, dict) or set(metadata) - {"resourceVersion", "continue", "remainingItemCount"}:
        _reject("KUBERNETES_LIST_INVALID")
    if metadata.get("continue") not in (None, "") or metadata.get("remainingItemCount") not in (None, 0):
        _reject("KUBERNETES_PAGINATION_FORBIDDEN")
    items = value.get("items")
    if not isinstance(items, list) or len(items) > rule["maximum_list_items"]:
        _reject("KUBERNETES_LIST_INVALID")
    for item in items:
        if not isinstance(item, dict) or set(item) != {"apiVersion", "kind", "namespace", "name", "projection_class", "projected_fields"}:
            _reject("KUBERNETES_LIST_INVALID")
        projected = _validate_projected_object(rule, item["projected_fields"], request=request, list_item=True)
        name, namespace, _uid, _rv = _object_identity(projected)
        expected = (projected["apiVersion"], projected["kind"], namespace, name, rule["projection_class"])
        observed = (item["apiVersion"], item["kind"], item["namespace"], item["name"], item["projection_class"])
        if observed != expected:
            _reject("KUBERNETES_LIST_INVALID")
    try:
        policy.structural_validate(value)
    except Exception:
        _reject("KUBERNETES_LIST_INVALID")


def _projection_metadata(payload: bytes, projection_class: str) -> Mapping[str, Any]:
    value = parse_canonical_json(payload)
    count = len(value["items"]) if isinstance(value, dict) and isinstance(value.get("items"), list) else 1
    metadata: dict[str, Any] = {
        "object_count": count, "projection_class": projection_class,
        "projection_schema_id": projection_class,
        "capture_state": "PREFLIGHT_PASS",
        "trusted_source_sha256": hashlib.sha256(payload).hexdigest(),
    }
    if isinstance(value, dict) and isinstance(value.get("list_metadata"), dict):
        rv = value["list_metadata"].get("resourceVersion")
        if isinstance(rv, str):
            metadata["source_list_resource_version"] = rv
    return _freeze(metadata)


def _status_candidate(request: KubernetesRequestIdentity, code: int, status: str, reason: str | None = None) -> EvidenceCandidate:
    value: dict[str, Any] = {"apiVersion": "v1", "kind": "Status", "status": status, "code": code}
    if reason:
        value["reason"] = reason
    payload = canonical_json_bytes(value)
    _validate_capture_payload(
        request._policy, "kubernetes_api_status", payload, media_type="application/json"
    )
    return EvidenceCandidate(
        "kubernetes_api_status", "RUNNER_KUBERNETES_CLIENT", "application/json",
        "PAYLOAD_WITH_DESCRIPTOR", payload, _freeze({"http_status": code}),
        request.identity_sha256, request.canonical,
    )


class ReadOnlyKubernetesClient:
    """Closed facade over the frozen GET/LIST authorization matrix."""

    __slots__ = ("_policy", "_transport", "_context", "_namespace", "_timeout", "_run_id", "_attempt_id", "_session_token")

    def __init__(self, *, policy: AuthenticatedPolicy, transport: ReadOnlyKubernetesTransport, context: str, namespace: str, timeout_seconds: int, run_id: str, attempt_id: str) -> None:
        if not isinstance(policy, AuthenticatedPolicy) or not isinstance(transport, ReadOnlyKubernetesTransport):
            _reject("KUBERNETES_CLIENT_CONFIGURATION_INVALID")
        if context != "kind-kind" or namespace != "social-network" or not isinstance(timeout_seconds, int) or isinstance(timeout_seconds, bool) or not 1 <= timeout_seconds <= _MAXIMUM_TIMEOUT_SECONDS:
            _reject("KUBERNETES_CLIENT_CONFIGURATION_INVALID")
        if not isinstance(run_id, str) or not run_id or not isinstance(attempt_id, str) or not attempt_id:
            _reject("KUBERNETES_CLIENT_CONFIGURATION_INVALID")
        self._policy = policy
        self._transport = transport
        self._context = context
        self._namespace = namespace
        self._timeout = timeout_seconds
        self._run_id = run_id
        self._attempt_id = attempt_id
        self._session_token = object()

    def _identity(self, request: Mapping[str, Any]) -> KubernetesRequestIdentity:
        return KubernetesRequestIdentity._create(self._policy, request, run_id=self._run_id, attempt_id=self._attempt_id, timeout_seconds=self._timeout)

    def _validated_captured_identity(
        self,
        captured: CapturedObjectIdentity,
        *,
        expected_kind: str,
        consumer: KubernetesConsumer,
    ) -> CapturedObjectIdentity:
        if not isinstance(captured, CapturedObjectIdentity):
            _reject("KUBERNETES_CAPTURE_CLASS_INVALID")
        try:
            session_token = captured._session_token
            policy_sha256 = captured._policy_manifest_sha256
            run_id = captured._run_id
            attempt_id = captured._attempt_id
            source_consumer = captured._consumer
            source_request_sha256 = captured._source_request_identity_sha256
            source_projection_sha256 = captured._source_projection_sha256
        except AttributeError:
            _reject("KUBERNETES_CAPTURE_CLASS_INVALID")
        consumer_value = (
            consumer.value if isinstance(consumer, KubernetesConsumer) else consumer
        )
        if (
            captured.kind != expected_kind
            or captured.namespace != "social-network"
            or policy_sha256 != self._policy.manifest_sha256
            or run_id != self._run_id
            or attempt_id != self._attempt_id
            or source_consumer != consumer_value
            or session_token is not None and session_token is not self._session_token
            or not isinstance(source_request_sha256, str)
            or len(source_request_sha256) != 64
            or not isinstance(source_projection_sha256, str)
            or len(source_projection_sha256) != 64
        ):
            _reject("KUBERNETES_CAPTURE_IDENTITY_MISMATCH")
        try:
            validated = validate_evidence_ref(self._policy, captured.reference)
        except Exception:
            _reject("KUBERNETES_CAPTURE_SOURCE_MISMATCH")
        if validated.role != "kubernetes_object_projection":
            _reject("KUBERNETES_CAPTURE_SOURCE_MISMATCH")
        return captured

    def _raise_capture_failure(
        self,
        identity: KubernetesRequestIdentity,
        request_candidate: EvidenceCandidate,
        error: _TransportFailure,
    ) -> NoReturn:
        if error.code != "KUBERNETES_API_FAILURE":
            _reject(error.code)
        status_code = error.http_status if error.http_status is not None else 0
        status = _status_candidate(identity, status_code, "Failure", "ApiFailure")
        error_body = None
        if error.body is not None:
            _validate_capture_payload(
                self._policy,
                "kubernetes_api_error_body",
                error.body,
                media_type="application/octet-stream",
            )
            error_body = EvidenceCandidate(
                "kubernetes_api_error_body", "RUNNER_KUBERNETES_CLIENT",
                "application/octet-stream", "PAYLOAD_WITH_DESCRIPTOR", bytes(error.body),
                _freeze({"http_status": status_code}),
                identity.identity_sha256, identity.canonical,
            )
        failure_capture = KubernetesCapture._create(
            classification=PREFLIGHT_MARKER if self._run_id == PREFLIGHT_MARKER else "READ_ONLY_CAPTURE",
            request=identity,
            request_evidence=request_candidate,
            response_evidence=None,
            status_evidence=status,
            error_body_evidence=error_body,
            typed_method=identity.typed_method,
            policy=self._policy,
            session_token=self._session_token,
        )
        _reject(error.code, failure_capture)

    def _capture(self, request: Mapping[str, Any], function: Any, *arguments: Any) -> KubernetesCapture:
        identity = self._identity(request)
        request_candidate = EvidenceCandidate(
            "kubernetes_request_identity", "RUNNER_KUBERNETES_CLIENT", "application/json", "DESCRIPTOR_ONLY", None,
            _freeze({
                "request_rule_id": request["request_rule_id"], "operation": request["operation"],
                "resource": request["resource"], "subresource": request["subresource"],
                "namespace": request["namespace"], "name_rule": request["name"],
                "selector": request["label_selector"],
                "projection_paths": _thaw(_rule(self._policy, request["request_rule_id"])["projection_paths"]),
                "canonical_request": _thaw(identity.canonical),
                "request_identity_sha256": identity.identity_sha256,
            }),
        )
        try:
            raw = function(*arguments, self._timeout)
            payload = project_kubernetes_response(self._policy, identity, raw)
            response = EvidenceCandidate(
                "kubernetes_object_projection", "RUNNER_KUBERNETES_CLIENT", "application/json", "PAYLOAD_WITH_DESCRIPTOR",
                payload, _projection_metadata(payload, request["projection_class"]),
                identity.identity_sha256, identity.canonical,
            )
            status = _status_candidate(identity, 200, "Success")
            return KubernetesCapture._create(
                classification=PREFLIGHT_MARKER if self._run_id == PREFLIGHT_MARKER else "READ_ONLY_CAPTURE",
                request=identity,
                request_evidence=request_candidate,
                response_evidence=response,
                status_evidence=status,
                error_body_evidence=None,
                typed_method=identity.typed_method,
                policy=self._policy,
                session_token=self._session_token,
            )
        except _TransportFailure as error:
            self._raise_capture_failure(identity, request_candidate, error)
        except KubernetesReadOnlyError:
            raise
        except Exception as error:
            try:
                _raise_transport_failure(error, internal=True)
            except _TransportFailure as failure:
                self._raise_capture_failure(identity, request_candidate, failure)

    def get_deployment(self, name: str, *, consumer: KubernetesConsumer) -> KubernetesCapture:
        request = _request(self._policy, "DEPLOYMENTS_SOCIAL_READ", "GET", name=name, selector="", consumer=consumer, captured=None)
        return self._capture(request, self._transport.read_namespaced_deployment, name, "social-network")

    def list_deployments(self, *, consumer: KubernetesConsumer) -> KubernetesCapture:
        request = _request(self._policy, "DEPLOYMENTS_SOCIAL_READ", "LIST", name="", selector="", consumer=consumer, captured=None)
        return self._capture(request, self._transport.list_namespaced_deployment, "social-network", "", "")

    def get_coredns_deployment(self, *, consumer: KubernetesConsumer) -> KubernetesCapture:
        request = _request(self._policy, "COREDNS_DEPLOYMENT_READ", "GET", name="coredns", selector="", consumer=consumer, captured=None)
        return self._capture(request, self._transport.read_namespaced_deployment, "coredns", "kube-system")

    def get_captured_replica_set(self, captured: CapturedObjectIdentity, *, consumer: KubernetesConsumer) -> KubernetesCapture:
        captured = self._validated_captured_identity(
            captured, expected_kind="ReplicaSet", consumer=consumer
        )
        request = _request(self._policy, "REPLICASET_OWNER_READ", "GET", name=captured.name, selector="", consumer=consumer, captured=captured)
        return self._capture(request, self._transport.read_namespaced_replica_set, captured.name, "social-network")

    def get_captured_pod(self, captured: CapturedObjectIdentity, *, consumer: KubernetesConsumer) -> KubernetesCapture:
        captured = self._validated_captured_identity(
            captured, expected_kind="Pod", consumer=consumer
        )
        request = _request(self._policy, "PODS_SOCIAL_READ_AND_GUARDED_MUTATION", "GET", name=captured.name, selector="", consumer=consumer, captured=captured)
        return self._capture(request, self._transport.read_namespaced_pod, captured.name, "social-network")

    def list_pods(self, selector: PodSelector, *, consumer: KubernetesConsumer) -> KubernetesCapture:
        if not isinstance(selector, PodSelector):
            _reject("KUBERNETES_REQUEST_RULE_INVALID")
        request = _request(self._policy, "PODS_SOCIAL_READ_AND_GUARDED_MUTATION", "LIST", name="", selector=selector.value, consumer=consumer, captured=None)
        return self._capture(request, self._transport.list_namespaced_pod, "social-network", selector.value, "")

    def list_coredns_pods(self, *, consumer: KubernetesConsumer) -> KubernetesCapture:
        request = _request(self._policy, "COREDNS_PODS_READ", "LIST", name="", selector="k8s-app=kube-dns", consumer=consumer, captured=None)
        return self._capture(request, self._transport.list_namespaced_pod, "kube-system", "k8s-app=kube-dns", "")

    def get_user_service(self, *, consumer: KubernetesConsumer) -> KubernetesCapture:
        request = _request(self._policy, "USER_SERVICE_READ_AND_GUARDED_MUTATION", "GET", name="user-service", selector="", consumer=consumer, captured=None)
        return self._capture(request, self._transport.read_namespaced_service, "user-service", "social-network")

    def list_user_service_endpoint_slices(self, *, consumer: KubernetesConsumer) -> KubernetesCapture:
        request = _request(self._policy, "USER_SERVICE_ENDPOINT_SLICES_READ", "LIST", name="", selector="kubernetes.io/service-name=user-service", consumer=consumer, captured=None)
        return self._capture(request, self._transport.list_namespaced_endpoint_slice, "social-network", "kubernetes.io/service-name=user-service", "")


def validate_prepublication_capture(policy: AuthenticatedPolicy, capture: KubernetesCapture) -> None:
    """Run the same closed request/response semantics before EvidenceStore publication."""

    if not isinstance(capture, KubernetesCapture):
        _reject("KUBERNETES_PROJECTION_REQUEST_MISMATCH")
    request = validate_kubernetes_read_request(policy, capture.request.canonical)
    material = {"request": request, "run_id": capture.request.run_id, "attempt_id": capture.request.attempt_id}
    if hashlib.sha256(canonical_json_bytes(material)).hexdigest() != capture.request.identity_sha256:
        _reject("KUBERNETES_REQUEST_IDENTITY_MISMATCH")
    if capture.response_evidence is None or capture.response_evidence.payload_bytes is None:
        _reject("KUBERNETES_PROJECTION_REQUEST_MISMATCH")
    payload = parse_canonical_json(capture.response_evidence.payload_bytes)
    if request["operation"] == "LIST":
        if payload.get("request") != request:
            _reject("KUBERNETES_PROJECTION_REQUEST_MISMATCH")
        validate_kubernetes_list_response(policy, payload)
    else:
        rule = _rule(policy, request["request_rule_id"])
        _validate_projected_object(rule, payload, request=request, list_item=False)
    _validate_capture_payload(
        policy,
        "kubernetes_object_projection",
        capture.response_evidence.payload_bytes,
        media_type="application/json",
    )
    if capture.status_evidence.payload_bytes is not None:
        _validate_capture_payload(
            policy,
            "kubernetes_api_status",
            capture.status_evidence.payload_bytes,
            media_type="application/json",
        )



def _operation_markers(context: Any) -> tuple[tuple[int, Mapping[str, Any]], ...]:
    rows: list[tuple[int, Mapping[str, Any]]] = []
    for record in context.journal_records:
        transition = record.get("transition")
        if not isinstance(transition, str) or not transition.startswith("OPERATION_AUTHORIZED:"):
            continue
        try:
            data = bytes.fromhex(transition.split(":", 1)[1])
            value = parse_canonical_json(data)
        except Exception:
            _reject("JOURNAL_OPERATION_MARKER_MISSING")
        if not isinstance(value, dict) or canonical_json_bytes(value) != data:
            _reject("JOURNAL_OPERATION_MARKER_MISSING")
        rows.append((record["sequence_number"], value))
    return tuple(rows)


def _resolved_request_row(context: Any, request: Mapping[str, Any], candidate: Mapping[str, Any] | None = None) -> tuple[Any, Mapping[str, Any], int]:
    canonical = validate_kubernetes_read_request(context._policy, request)
    rows = []
    if candidate is not None and candidate.get("role") == "kubernetes_request_identity":
        rows.append(context._candidate_evidence(candidate))
    else:
        rows.extend(
            row for row in context.evidence.values()
            if row.reference.role == "kubernetes_request_identity"
            and _thaw(row.descriptor).get("canonical_request") == canonical
        )
    if len(rows) != 1:
        _reject("KUBERNETES_REQUEST_IDENTITY_MISMATCH")
    row = rows[0]
    descriptor = _thaw(row.descriptor)
    rule = _rule(context._policy, canonical["request_rule_id"])
    material = {"request": canonical, "run_id": context.run_id, "attempt_id": context.attempt_id}
    if (
        descriptor.get("canonical_request") != canonical
        or descriptor.get("request_identity_sha256") != hashlib.sha256(canonical_json_bytes(material)).hexdigest()
        or descriptor.get("request_rule_id") != canonical["request_rule_id"]
        or descriptor.get("operation") != canonical["operation"]
        or descriptor.get("resource") != canonical["resource"]
        or descriptor.get("subresource") != canonical["subresource"]
        or descriptor.get("namespace") != canonical["namespace"]
        or descriptor.get("name_rule") != canonical["name"]
        or descriptor.get("selector") != canonical["label_selector"]
        or descriptor.get("projection_paths") != _thaw(rule["projection_paths"])
    ):
        _reject("KUBERNETES_REQUEST_IDENTITY_MISMATCH")
    reference = row.reference.as_dict()
    markers = tuple(
        (sequence, marker) for sequence, marker in _operation_markers(context)
        if marker.get("request_reference") == reference
    )
    if len(markers) != 1:
        _reject("JOURNAL_OPERATION_MARKER_MISSING")
    sequence, marker = markers[0]
    expected = {
        "operation_kind": canonical["authorized_operation_kind"],
        "request_rule_id": canonical["request_rule_id"],
        "resource": canonical["resource"],
        "namespace": canonical["namespace"],
        "name": canonical["name"],
    }
    if any(marker.get(name) != value for name, value in expected.items()):
        _reject("JOURNAL_CONTEXT_MISMATCH")
    if row.publication.sequence_number >= sequence:
        _reject("JOURNAL_OPERATION_MARKER_MISSING")
    captured_reference = canonical.get("captured_object_reference")
    if captured_reference is not None:
        try:
            captured = context.resolve_reference(captured_reference)
        except Exception:
            _reject("KUBERNETES_CAPTURE_UNRESOLVED")
        if captured.reference.role != "kubernetes_object_projection" or captured.payload_bytes is None:
            _reject("KUBERNETES_CAPTURE_CLASS_INVALID")
        if captured.publication.sequence_number >= sequence:
            _reject("KUBERNETES_CAPTURE_STATE_INVALID")
        try:
            name, namespace, uid, rv = _projected_identity(
                context._policy,
                captured.payload_bytes,
                expected_kind=rule["response_kind"],
                item_name=canonical["captured_object_name"],
            )
        except Exception:
            _reject("KUBERNETES_CAPTURE_IDENTITY_MISMATCH")
        if (
            canonical["name"] != name or canonical["captured_object_name"] != name
            or canonical["namespace"] != namespace
            or canonical["captured_object_uid"] != uid
            or canonical["captured_object_resource_version"] != rv
        ):
            _reject("KUBERNETES_CAPTURE_IDENTITY_MISMATCH")
    return row, marker, sequence


def validate_resolved_kubernetes_request(context: Any, candidate: Mapping[str, Any]) -> None:
    """Authenticated implementation of ``VALIDATE_KUBERNETES_REQUEST_V1``."""

    if candidate.get("document_type") == REQUEST_DOCUMENT:
        request = candidate
    elif candidate.get("document_type") == "DESCRIPTOR_EVIDENCE_DESCRIPTOR_V1" and candidate.get("role") == "kubernetes_request_identity":
        request = candidate.get("canonical_request")
    else:
        return
    if not isinstance(request, Mapping):
        _reject("KUBERNETES_REQUEST_IDENTITY_MISMATCH")
    _resolved_request_row(context, request, candidate)


def _resolved_projection_row(context: Any, candidate: Mapping[str, Any]) -> tuple[Any, Any]:
    if candidate.get("document_type") == "PAYLOAD_EVIDENCE_DESCRIPTOR_V1" and candidate.get("role") == "kubernetes_object_projection":
        row = context._candidate_evidence(candidate)
        if row.payload_bytes is None:
            _reject("KUBERNETES_PROJECTION_REQUEST_MISMATCH")
        try:
            return row, parse_canonical_json(row.payload_bytes)
        except Exception:
            _reject("KUBERNETES_PROJECTION_FIELD_FORBIDDEN")
    if candidate.get("document_type") == LIST_RESPONSE_DOCUMENT:
        matches = []
        for row in context.evidence.values():
            if row.reference.role != "kubernetes_object_projection" or row.payload_bytes is None:
                continue
            try:
                if parse_canonical_json(row.payload_bytes) == _thaw(candidate):
                    matches.append(row)
            except Exception:
                continue
        if len(matches) != 1:
            _reject("KUBERNETES_PROJECTION_REQUEST_MISMATCH")
        return matches[0], _thaw(candidate)
    return None, None


def validate_resolved_kubernetes_response(context: Any, candidate: Mapping[str, Any]) -> None:
    """Authenticated implementation of ``VALIDATE_KUBERNETES_RESPONSE_V1``."""

    row, payload = _resolved_projection_row(context, candidate)
    if row is None:
        return
    descriptor = _thaw(row.descriptor)
    reference = descriptor.get("request_identity_reference")
    if not isinstance(reference, Mapping):
        _reject("KUBERNETES_PROJECTION_REQUEST_MISMATCH")
    projection_markers = tuple(
        marker for _sequence, marker in _operation_markers(context)
        if marker.get("projection_reference") == row.reference.as_dict()
    )
    if len(projection_markers) != 1 or projection_markers[0].get("request_reference") != _thaw(reference):
        _reject("KUBERNETES_PROJECTION_REQUEST_MISMATCH")
    try:
        request_row = context.resolve_reference(reference)
    except Exception:
        _reject("KUBERNETES_REQUEST_IDENTITY_MISMATCH")
    request = _thaw(request_row.descriptor).get("canonical_request")
    if not isinstance(request, Mapping):
        _reject("KUBERNETES_REQUEST_IDENTITY_MISMATCH")
    resolved_request, marker, marker_sequence = _resolved_request_row(context, request)
    if resolved_request.reference.as_dict() != _thaw(reference):
        _reject("KUBERNETES_PROJECTION_REQUEST_MISMATCH")
    if row.publication.sequence_number <= marker_sequence or row.publication.sequence_number <= request_row.publication.sequence_number:
        _reject("PUBLICATION_ORDER_INVALID")
    if marker.get("projection_reference") not in (None, row.reference.as_dict()):
        _reject("KUBERNETES_PROJECTION_REQUEST_MISMATCH")
    if descriptor.get("request_identity_reference") != request_row.reference.as_dict():
        _reject("KUBERNETES_PROJECTION_REQUEST_MISMATCH")
    if descriptor.get("projection_class") != request["projection_class"]:
        _reject("KUBERNETES_CAPTURE_CLASS_INVALID")
    if descriptor.get("trusted_source_sha256") != hashlib.sha256(row.payload_bytes).hexdigest():
        _reject("KUBERNETES_SOURCE_PROJECTION_MISMATCH")
    rule = _rule(context._policy, request["request_rule_id"])
    if request["operation"] == "LIST":
        if not isinstance(payload, Mapping) or payload.get("request") != _thaw(request):
            _reject("KUBERNETES_PROJECTION_REQUEST_MISMATCH")
        validate_kubernetes_list_response(context._policy, payload)
        observed_count = len(payload["items"])
        source_rv = payload["list_metadata"].get("resourceVersion")
        if descriptor.get("source_list_resource_version") != source_rv:
            _reject("KUBERNETES_LIST_RESOURCE_VERSION_MISMATCH")
    else:
        _validate_projected_object(rule, payload, request=request, list_item=False)
        observed_count = 1
    if descriptor.get("object_count") != observed_count:
        _reject("KUBERNETES_LIST_INVALID")
    _validate_capture_payload(
        context._policy,
        "kubernetes_object_projection",
        row.payload_bytes,
        media_type="application/json",
    )

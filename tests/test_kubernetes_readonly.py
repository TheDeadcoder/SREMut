"""Offline adversarial tests for the closed Kubernetes read-only layer."""

from __future__ import annotations

import copy
from dataclasses import FrozenInstanceError
import inspect
import json
from pathlib import Path
import ssl
import sys
import tempfile
import unittest

from sremut.canonical_json import canonical_json_bytes, parse_canonical_json
from sremut.evidence import EvidenceStore, ExternalAnchor
from sremut.journal import Journal
from sremut.policy_runtime import EXPECTED_HOOK_ORDER, POLICY_MANIFEST_SHA256, load_policy_bundle
from sremut.resolved_context import CONNECTED_HOOKS, resolve_evidence_context
from sremut.kubernetes_readonly import (
    CapturedObjectIdentity,
    Kubernetes32ReadOnlyTransport,
    KubernetesConsumer,
    KubernetesReadOnlyError,
    PodSelector,
    PREFLIGHT_MARKER,
    ReadOnlyKubernetesClient,
    validate_kubernetes_list_response,
    validate_kubernetes_read_request,
    validate_prepublication_capture,
)


REPOSITORY = Path(__file__).resolve().parents[1]
RUN_ID = "sremut-ms-m01-r01-a01-abcdef123456"
ATTEMPT_ID = "a01"


def load_policy():
    return load_policy_bundle(
        REPOSITORY / "policies/missing_service_social_network/evidence-capture-v1.1.yaml",
        REPOSITORY / "schemas/evidence-capture-policy-v1.1.schema.json",
        REPOSITORY / "EVIDENCE_CAPTURE_POLICY_V1_1_SHA256SUMS",
        expected_manifest_sha256=POLICY_MANIFEST_SHA256,
    )


def metadata(name, namespace, uid=None, resource_version="10", **extra):
    value = {
        "name": name, "namespace": namespace,
        "uid": uid or f"uid-{name}", "resourceVersion": resource_version,
        "generation": 1,
    }
    value.update(extra)
    return value


def deployment(name="user-service", namespace="social-network"):
    return {
        "apiVersion": "apps/v1", "kind": "Deployment",
        "metadata": metadata(name, namespace),
        "spec": {"replicas": 1},
        "status": {
            "replicas": 1, "updatedReplicas": 1, "readyReplicas": 1,
            "availableReplicas": 1, "unavailableReplicas": 0,
            "conditions": [{"type": "Available", "status": "True", "reason": "MinimumReplicasAvailable", "lastTransitionTime": "2026-01-01T00:00:00Z"}],
        },
    }


def pod(name="user-service-abc", namespace="social-network", **metadata_extra):
    owner = {"apiVersion": "apps/v1", "kind": "ReplicaSet", "name": "user-service-rs", "uid": "uid-rs", "controller": True}
    labels = metadata_extra.pop("labels", {"service": "user-service"})
    return {
        "apiVersion": "v1", "kind": "Pod",
        "metadata": metadata(name, namespace, ownerReferences=[owner], labels=labels, **metadata_extra),
        "status": {
            "phase": "Running", "podIP": "10.0.0.2",
            "conditions": [{"type": "Ready", "status": "True", "reason": "Ready", "lastTransitionTime": "2026-01-01T00:00:00Z"}],
            "containerStatuses": [{"name": "user-service", "ready": True, "restartCount": 0, "state": {}}],
        },
    }


def replica_set(name="user-service-rs"):
    owner = {"apiVersion": "apps/v1", "kind": "Deployment", "name": "user-service", "uid": "uid-deployment", "controller": True}
    return {"apiVersion": "apps/v1", "kind": "ReplicaSet", "metadata": metadata(name, "social-network", uid="uid-rs", ownerReferences=[owner])}


def service():
    return {
        "apiVersion": "v1", "kind": "Service",
        "metadata": metadata(
            "user-service", "social-network",
            labels={"app.kubernetes.io/managed-by": "Helm", "ignored": "not-projected"},
            annotations={"meta.helm.sh/release-name": "social-network", "meta.helm.sh/release-namespace": "social-network"},
        ),
        "spec": {
            "type": "ClusterIP", "clusterIP": "10.96.0.50", "clusterIPs": ["10.96.0.50"],
            "ipFamilies": ["IPv4"], "ipFamilyPolicy": "SingleStack", "internalTrafficPolicy": "Cluster",
            "sessionAffinity": "None", "selector": {"service": "user-service", "unapproved": "hidden"},
            "ports": [{"name": "thrift", "protocol": "TCP", "port": 9090, "targetPort": 9090}],
            "publishNotReadyAddresses": False, "allocateLoadBalancerNodePorts": False,
        },
        "secret": "must-never-be-projected",
    }


def endpoint_slice():
    return {
        "apiVersion": "discovery.k8s.io/v1", "kind": "EndpointSlice",
        "metadata": metadata("user-service-abc", "social-network", labels={"kubernetes.io/service-name": "user-service"}),
        "addressType": "IPv4",
        "ports": [{"name": "thrift", "protocol": "TCP", "port": 9090}],
        "endpoints": [{
            "addresses": ["10.0.0.2"], "conditions": {"ready": True, "terminating": False},
            "targetRef": {"apiVersion": "v1", "kind": "Pod", "namespace": "social-network", "name": "user-service-abc", "uid": "uid-user-service-abc"},
        }],
    }


def list_result(items, resource_version="20", continuation="", remaining=0):
    return {"metadata": {"resourceVersion": resource_version, "continue": continuation, "remainingItemCount": remaining}, "items": items}


class FakeTransport:
    def __init__(self):
        self.calls = []
        self.failure = None
        self.results = {
            "read_namespaced_deployment": deployment(),
            "list_namespaced_deployment": list_result([deployment()]),
            "read_namespaced_replica_set": replica_set(),
            "read_namespaced_pod": pod(),
            "list_namespaced_pod": list_result([pod()]),
            "read_namespaced_service": service(),
            "list_namespaced_endpoint_slice": list_result([endpoint_slice()]),
        }

    def _call(self, method, *args):
        self.calls.append((method, args))
        if self.failure is not None:
            raise self.failure
        if method == "read_namespaced_deployment" and args[:2] == ("coredns", "kube-system"):
            return deployment("coredns", "kube-system")
        if method == "list_namespaced_pod" and args[:2] == ("kube-system", "k8s-app=kube-dns"):
            return list_result([pod("coredns-abc", "kube-system", labels={"k8s-app": "kube-dns"})])
        return copy.deepcopy(self.results[method])

    def read_namespaced_deployment(self, name, namespace, timeout_seconds): return self._call("read_namespaced_deployment", name, namespace, timeout_seconds)
    def list_namespaced_deployment(self, namespace, label_selector, field_selector, timeout_seconds): return self._call("list_namespaced_deployment", namespace, label_selector, field_selector, timeout_seconds)
    def read_namespaced_replica_set(self, name, namespace, timeout_seconds): return self._call("read_namespaced_replica_set", name, namespace, timeout_seconds)
    def read_namespaced_pod(self, name, namespace, timeout_seconds): return self._call("read_namespaced_pod", name, namespace, timeout_seconds)
    def list_namespaced_pod(self, namespace, label_selector, field_selector, timeout_seconds): return self._call("list_namespaced_pod", namespace, label_selector, field_selector, timeout_seconds)
    def read_namespaced_service(self, name, namespace, timeout_seconds): return self._call("read_namespaced_service", name, namespace, timeout_seconds)
    def list_namespaced_endpoint_slice(self, namespace, label_selector, field_selector, timeout_seconds): return self._call("list_namespaced_endpoint_slice", namespace, label_selector, field_selector, timeout_seconds)


def captured(kind, value):
    instance = object.__new__(CapturedObjectIdentity)
    identity = value["metadata"]
    reference = {
        "document_type": "PAYLOAD_EVIDENCE_REF_V1", "schema_version": 1,
        "evidence_id": "ev-" + "1" * 32, "role": "kubernetes_object_projection",
        "producer": "RUNNER_KUBERNETES_CLIENT", "source_kind": "KUBERNETES",
        "media_type": "application/json", "storage_class": "PAYLOAD_WITH_DESCRIPTOR",
        "descriptor_sha256": "1" * 64, "descriptor_size_bytes": 1,
        "descriptor_relative_path": "descriptors/sha256/11/" + "1" * 64 + ".json",
        "redaction_status": "NOT_REDACTED", "payload_sha256": "2" * 64,
        "payload_size_bytes": 1, "payload_relative_path": "objects/sha256/22/" + "2" * 64,
        "projection_class": "KUBERNETES_OBJECT_PROJECTION_V1",
    }
    for key, child in {
        "name": identity["name"], "namespace": identity["namespace"], "uid": identity["uid"],
        "resource_version": identity["resourceVersion"], "kind": kind, "reference": reference,
    }.items():
        object.__setattr__(instance, key, child)
    return instance


class KubernetesReadOnlyCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.policy = load_policy()

    def setUp(self):
        self.transport = FakeTransport()
        self.client = ReadOnlyKubernetesClient(
            policy=self.policy, transport=self.transport, context="kind-kind",
            namespace="social-network", timeout_seconds=7, run_id=RUN_ID, attempt_id=ATTEMPT_ID,
        )

    def assert_code(self, code, function, *args, **kwargs):
        with self.assertRaises(KubernetesReadOnlyError) as caught:
            function(*args, **kwargs)
        self.assertEqual(str(caught.exception), code)
        self.assertNotIn("secret", str(caught.exception).lower())

    def prepared_projection(self, capture, *, monotonic_ns=1):
        with tempfile.TemporaryDirectory(prefix="sremut-capture-prepare-") as root:
            with EvidenceStore(Path(root), self.policy, RUN_ID, ATTEMPT_ID) as store:
                common = {
                    "run_id": RUN_ID,
                    "attempt_id": ATTEMPT_ID,
                    "created_utc": "2026-08-20T10:00:00.123456789Z",
                    "monotonic_ns": monotonic_ns,
                    "boot_identity": "123e4567-e89b-12d3-a456-426614174000",
                }
                _request, request_descriptor, request_reference = store._descriptor(
                    capture.request_evidence.role,
                    capture.request_evidence.publication_metadata(**common),
                    None,
                )
                response_metadata = capture.response_evidence.publication_metadata(
                    **common,
                    request_identity_reference=request_reference,
                    request_identity_descriptor_bytes=request_descriptor,
                )
                _response, response_descriptor, response_reference = store._descriptor(
                    capture.response_evidence.role,
                    response_metadata,
                    capture.response_evidence.payload_bytes,
                )
                self.assertEqual(
                    store.fs.list_regular_files(
                        ("objects", "descriptors", "journal")
                    ),
                    (),
                )
        return response_reference, response_descriptor

    def capture_identity(self, capture, *, expected_kind, item_name=None):
        reference, descriptor = self.prepared_projection(capture)
        return CapturedObjectIdentity.from_capture(
            capture,
            reference,
            descriptor,
            expected_kind=expected_kind,
            item_name=item_name,
        )

    def captured_pod_identity(self):
        capture = self.client.list_pods(
            PodSelector.USER_SERVICE,
            consumer=KubernetesConsumer.CONTRACT_EVALUATOR,
        )
        return self.capture_identity(
            capture,
            expected_kind="Pod",
            item_name="user-service-abc",
        )

    def captured_replica_set_identity(self):
        pod_capture = self.client.get_captured_pod(
            self.captured_pod_identity(),
            consumer=KubernetesConsumer.CONTRACT_EVALUATOR,
        )
        return self.capture_identity(
            pod_capture,
            expected_kind="ReplicaSet",
        )


class AuthorizationTests(KubernetesReadOnlyCase):
    def test_every_frozen_read_operation(self):
        captures = [
            self.client.get_deployment("user-service", consumer=KubernetesConsumer.LIVE_PREFLIGHT),
            self.client.list_deployments(consumer=KubernetesConsumer.LIVE_PREFLIGHT),
            self.client.get_coredns_deployment(consumer=KubernetesConsumer.LIVE_PREFLIGHT),
            self.client.get_captured_replica_set(self.captured_replica_set_identity(), consumer=KubernetesConsumer.CONTRACT_EVALUATOR),
            self.client.get_captured_pod(self.captured_pod_identity(), consumer=KubernetesConsumer.CONTRACT_EVALUATOR),
            self.client.list_pods(PodSelector.USER_SERVICE, consumer=KubernetesConsumer.LIVE_PREFLIGHT),
            self.client.list_coredns_pods(consumer=KubernetesConsumer.LIVE_PREFLIGHT),
            self.client.get_user_service(consumer=KubernetesConsumer.LIVE_PREFLIGHT),
            self.client.list_user_service_endpoint_slices(consumer=KubernetesConsumer.CONTRACT_EVALUATOR),
        ]
        self.assertEqual(len(captures), 9)
        for result in captures:
            validate_prepublication_capture(self.policy, result)
            self.assertEqual(result.request.transport_timeout_seconds, 7)
            self.assertIsNotNone(result.response_evidence)

    def test_all_pod_selectors_are_exact_enums(self):
        for selector in PodSelector:
            with self.subTest(selector=selector):
                self.transport.results["list_namespaced_pod"] = (
                    list_result([pod()]) if selector in (PodSelector.ALL, PodSelector.USER_SERVICE)
                    else list_result([])
                )
                validate_prepublication_capture(self.policy, self.client.list_pods(selector, consumer=KubernetesConsumer.LIVE_PREFLIGHT))
        self.assert_code("KUBERNETES_REQUEST_RULE_INVALID", self.client.list_pods, "service = user-service", consumer=KubernetesConsumer.LIVE_PREFLIGHT)

    def test_forbidden_name_namespace_context_consumer(self):
        self.assert_code("KUBERNETES_REQUEST_RULE_INVALID", self.client.get_deployment, "not-frozen", consumer=KubernetesConsumer.LIVE_PREFLIGHT)
        with self.assertRaises(KubernetesReadOnlyError):
            ReadOnlyKubernetesClient(policy=self.policy, transport=self.transport, context="other", namespace="social-network", timeout_seconds=7, run_id=RUN_ID, attempt_id=ATTEMPT_ID)
        with self.assertRaises(KubernetesReadOnlyError):
            ReadOnlyKubernetesClient(policy=self.policy, transport=self.transport, context="kind-kind", namespace="other", timeout_seconds=7, run_id=RUN_ID, attempt_id=ATTEMPT_ID)
        self.assert_code("KUBERNETES_REQUEST_RULE_INVALID", self.client.get_user_service, consumer=KubernetesConsumer.ADJUDICATOR)
        self.assert_code("KUBERNETES_REQUEST_RULE_INVALID", self.client.list_user_service_endpoint_slices, consumer=KubernetesConsumer.LIVE_PREFLIGHT)

    def test_wrong_group_version_operation_selector_option_and_extra_field(self):
        base = json.loads(self.client.list_deployments(consumer=KubernetesConsumer.LIVE_PREFLIGHT).request.canonical_bytes)
        cases = [
            ("api_group", ""), ("api_version", "v2"), ("operation", "DELETE"),
            ("label_selector", "service=user-service"), ("request_options", {"watch": False, "pretty": False, "dry_run": False, "limit": 1}),
        ]
        for field, value in cases:
            with self.subTest(field=field):
                changed = copy.deepcopy(base); changed[field] = value
                self.assert_code("KUBERNETES_REQUEST_RULE_INVALID", validate_kubernetes_read_request, self.policy, changed)
        extra = copy.deepcopy(base); extra["timeout"] = 7
        self.assert_code("KUBERNETES_REQUEST_RULE_INVALID", validate_kubernetes_read_request, self.policy, extra)

    def test_timeout_bound_and_no_generic_or_mutation_api(self):
        for timeout in (0, 31, True):
            with self.subTest(timeout=timeout):
                with self.assertRaises(KubernetesReadOnlyError):
                    ReadOnlyKubernetesClient(policy=self.policy, transport=self.transport, context="kind-kind", namespace="social-network", timeout_seconds=timeout, run_id=RUN_ID, attempt_id=ATTEMPT_ID)
        forbidden = ("call_api", "create", "delete", "patch", "replace", "apply", "watch", "exec", "logs", "port_forward")
        for name in forbidden:
            self.assertFalse(hasattr(self.client, name), name)

    def test_all_public_operations_require_explicit_consumer(self):
        methods = (
            "get_deployment", "list_deployments", "get_coredns_deployment",
            "get_captured_replica_set", "get_captured_pod", "list_pods",
            "list_coredns_pods", "get_user_service",
            "list_user_service_endpoint_slices",
        )
        for name in methods:
            parameter = inspect.signature(getattr(ReadOnlyKubernetesClient, name)).parameters["consumer"]
            self.assertIs(parameter.default, inspect.Parameter.empty, name)
        with self.assertRaises(TypeError):
            self.client.get_user_service()

    def test_request_and_capture_are_immutable(self):
        capture = self.client.get_user_service(consumer=KubernetesConsumer.LIVE_PREFLIGHT)
        with self.assertRaises((TypeError, FrozenInstanceError, AttributeError)):
            capture.request.canonical["name"] = "changed"
        with self.assertRaises((TypeError, FrozenInstanceError, AttributeError)):
            capture.typed_method = "changed"


class TransportTests(KubernetesReadOnlyCase):
    def test_exactly_one_method_timeout_and_no_watch(self):
        result = self.client.list_pods(PodSelector.USER_SERVICE, consumer=KubernetesConsumer.LIVE_PREFLIGHT)
        self.assertEqual(self.transport.calls, [("list_namespaced_pod", ("social-network", "service=user-service", "", 7))])
        self.assertEqual(result.typed_method, "CoreV1Api.list_namespaced_pod")

    def test_timeout_tls_decode_and_api_errors_never_become_empty(self):
        class ApiFailure(Exception):
            status = 500
        for error, code in (
            (TimeoutError(), "KUBERNETES_TRANSPORT_TIMEOUT"),
            (ssl.SSLError(), "KUBERNETES_TRANSPORT_TLS_FAILURE"),
            (json.JSONDecodeError("x", "", 0), "KUBERNETES_TRANSPORT_DECODING_FAILURE"),
            (ApiFailure(), "KUBERNETES_API_FAILURE"),
        ):
            with self.subTest(code=code):
                self.transport.failure = error
                self.assert_code(code, self.client.list_deployments, consumer=KubernetesConsumer.LIVE_PREFLIGHT)
                self.transport.failure = None

    def test_api_error_status_and_bounded_body_candidates(self):
        class ApiFailure(Exception):
            status = 404
            body = b'{"apiVersion":"v1","code":404,"kind":"Status","reason":"NotFound","status":"Failure"}'

        self.transport.failure = ApiFailure()
        with self.assertRaises(KubernetesReadOnlyError) as caught:
            self.client.get_user_service(consumer=KubernetesConsumer.LIVE_PREFLIGHT)
        self.assertEqual(caught.exception.code, "KUBERNETES_API_FAILURE")
        capture = caught.exception.capture
        self.assertIsNotNone(capture)
        self.assertIsNone(capture.response_evidence)
        self.assertEqual(capture.status_evidence.role, "kubernetes_api_status")
        self.assertEqual(capture.error_body_evidence.role, "kubernetes_api_error_body")
        self.assertNotIn(b"Authorization", capture.error_body_evidence.payload_bytes)

    def test_api_error_publication_rejects_unrelated_request_reference(self):
        class ApiFailure(Exception):
            status = 404
            body = b'{"apiVersion":"v1","code":404,"kind":"Status","reason":"NotFound","status":"Failure"}'

        self.transport.failure = ApiFailure()
        with self.assertRaises(KubernetesReadOnlyError) as caught:
            self.client.get_user_service(consumer=KubernetesConsumer.LIVE_PREFLIGHT)
        candidate = caught.exception.capture.error_body_evidence
        wrong = {
            "document_type": "DESCRIPTOR_EVIDENCE_REF_V1", "schema_version": 1,
            "evidence_id": "ev-" + "3" * 32, "role": "adjudication",
            "producer": "ADJUDICATOR", "source_kind": "GENERATED_DESCRIPTOR",
            "media_type": "application/json", "storage_class": "DESCRIPTOR_ONLY",
            "descriptor_sha256": "3" * 64, "descriptor_size_bytes": 1,
            "descriptor_relative_path": "descriptors/sha256/33/" + "3" * 64 + ".json",
            "redaction_status": "NOT_REDACTED",
        }
        self.assert_code(
            "KUBERNETES_REQUEST_IDENTITY_MISMATCH",
            candidate.publication_metadata,
            run_id=RUN_ID, attempt_id=ATTEMPT_ID,
            created_utc="2026-08-20T10:00:00.123456789Z", monotonic_ns=1,
            boot_identity="123e4567-e89b-12d3-a456-426614174000",
            request_identity_reference=wrong,
            request_identity_descriptor_bytes=b"{}",
        )
        structurally_typed = dict(wrong)
        structurally_typed.update({
            "role": "kubernetes_request_identity",
            "producer": "RUNNER_KUBERNETES_CLIENT",
            "source_kind": "KUBERNETES",
            "storage_class": "DESCRIPTOR_ONLY",
        })
        self.assert_code(
            "KUBERNETES_REQUEST_IDENTITY_MISMATCH",
            candidate.publication_metadata,
            run_id=RUN_ID, attempt_id=ATTEMPT_ID,
            created_utc="2026-08-20T10:00:00.123456789Z", monotonic_ns=1,
            boot_identity="123e4567-e89b-12d3-a456-426614174000",
            request_identity_reference=structurally_typed,
            request_identity_descriptor_bytes=canonical_json_bytes([]),
        )

    def test_sensitive_api_error_body_is_never_retained(self):
        class ApiFailure(Exception):
            status = 401
            body = b"Authorization: Bearer abcdefghijklmnopqrstuvwxyz"

        self.transport.failure = ApiFailure()
        self.assert_code("SENSITIVE_CAPTURE_REJECTED", self.client.get_user_service, consumer=KubernetesConsumer.LIVE_PREFLIGHT)

    def test_raw_certificate_api_error_body_is_never_retained(self):
        class ApiFailure(Exception):
            status = 500
            body = b"-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----"

        self.transport.failure = ApiFailure()
        self.assert_code(
            "SENSITIVE_CAPTURE_REJECTED",
            self.client.get_user_service,
            consumer=KubernetesConsumer.LIVE_PREFLIGHT,
        )

    def test_empty_and_maximum_lists_and_oversize(self):
        self.transport.results["list_namespaced_pod"] = list_result([])
        capture = self.client.list_pods(PodSelector.ALL, consumer=KubernetesConsumer.LIVE_PREFLIGHT)
        self.assertEqual(parse_canonical_json(capture.response_evidence.payload_bytes)["items"], [])
        minimal = lambda index: {
            "apiVersion": "v1", "kind": "Pod",
            "metadata": metadata(f"pod-{index}", "social-network"),
        }
        self.transport.results["list_namespaced_pod"] = list_result([minimal(index) for index in range(256)])
        self.client.list_pods(PodSelector.ALL, consumer=KubernetesConsumer.LIVE_PREFLIGHT)
        self.transport.results["list_namespaced_pod"] = list_result([minimal(index) for index in range(257)])
        self.assert_code("KUBERNETES_LIST_INVALID", self.client.list_pods, PodSelector.ALL, consumer=KubernetesConsumer.LIVE_PREFLIGHT)

    def test_pagination_is_rejected(self):
        self.transport.results["list_namespaced_deployment"] = list_result([deployment()], continuation="next")
        self.assert_code("KUBERNETES_PAGINATION_FORBIDDEN", self.client.list_deployments, consumer=KubernetesConsumer.LIVE_PREFLIGHT)


class ProjectionTests(KubernetesReadOnlyCase):
    def test_valid_singular_and_list_for_every_kind(self):
        for capture in (
            self.client.get_deployment("user-service", consumer=KubernetesConsumer.LIVE_PREFLIGHT), self.client.get_coredns_deployment(consumer=KubernetesConsumer.LIVE_PREFLIGHT),
            self.client.get_captured_replica_set(self.captured_replica_set_identity(), consumer=KubernetesConsumer.CONTRACT_EVALUATOR),
            self.client.get_captured_pod(self.captured_pod_identity(), consumer=KubernetesConsumer.CONTRACT_EVALUATOR), self.client.get_user_service(consumer=KubernetesConsumer.LIVE_PREFLIGHT),
            self.client.list_deployments(consumer=KubernetesConsumer.LIVE_PREFLIGHT), self.client.list_pods(PodSelector.USER_SERVICE, consumer=KubernetesConsumer.LIVE_PREFLIGHT),
            self.client.list_coredns_pods(consumer=KubernetesConsumer.LIVE_PREFLIGHT), self.client.list_user_service_endpoint_slices(consumer=KubernetesConsumer.CONTRACT_EVALUATOR),
        ):
            validate_prepublication_capture(self.policy, capture)

    def test_dotted_key_nested_arrays_and_no_raw_leakage(self):
        service_capture = self.client.get_user_service(consumer=KubernetesConsumer.LIVE_PREFLIGHT)
        service_value = parse_canonical_json(service_capture.response_evidence.payload_bytes)
        self.assertEqual(service_value["metadata"]["annotations"]["meta.helm.sh/release-name"], "social-network")
        self.assertNotIn("ignored", service_capture.response_evidence.payload_bytes.decode())
        self.assertNotIn("must-never", service_capture.response_evidence.payload_bytes.decode())
        endpoint_capture = self.client.list_user_service_endpoint_slices(consumer=KubernetesConsumer.CONTRACT_EVALUATOR)
        value = parse_canonical_json(endpoint_capture.response_evidence.payload_bytes)
        self.assertEqual(value["items"][0]["projected_fields"]["endpoints"][0]["addresses"], ["10.0.0.2"])

    def test_missing_typemeta_mapping_cannot_cross_model_boundary(self):
        self.transport.results["read_namespaced_deployment"] = {
            "metadata": metadata("user-service", "social-network"),
            "status": {"phase": "Running", "podIP": "10.0.0.9", "conditions": []},
        }
        self.assert_code(
            "KUBERNETES_LIST_INVALID",
            self.client.get_deployment,
            "user-service",
            consumer=KubernetesConsumer.LIVE_PREFLIGHT,
        )

    def test_generated_model_omitted_type_meta_is_derived_from_frozen_rule(self):
        model_type = type("V1Deployment", (), {})
        model_type.__module__ = "kubernetes.client.models.v1_deployment"
        raw = model_type()
        raw.attribute_map = {
            "api_version": "apiVersion", "kind": "kind", "metadata": "metadata",
            "spec": "spec", "status": "status",
        }
        source = deployment()
        raw.api_version = None
        raw.kind = None
        raw.metadata = source["metadata"]
        raw.spec = source["spec"]
        raw.status = source["status"]
        self.transport.results["read_namespaced_deployment"] = raw
        value = parse_canonical_json(self.client.get_deployment("user-service", consumer=KubernetesConsumer.LIVE_PREFLIGHT).response_evidence.payload_bytes)
        self.assertEqual((value["apiVersion"], value["kind"]), ("apps/v1", "Deployment"))

    def test_missing_uid_resource_version_wrong_namespace_and_kind(self):
        for mutation in ("uid", "resourceVersion"):
            self.transport.results["read_namespaced_service"] = service()
            del self.transport.results["read_namespaced_service"]["metadata"][mutation]
            self.assert_code("KUBERNETES_LIST_INVALID", self.client.get_user_service, consumer=KubernetesConsumer.LIVE_PREFLIGHT)
        self.transport.results["read_namespaced_service"] = service()
        self.transport.results["read_namespaced_service"]["metadata"]["namespace"] = "other"
        self.assert_code("KUBERNETES_LIST_INVALID", self.client.get_user_service, consumer=KubernetesConsumer.LIVE_PREFLIGHT)
        self.transport.results["read_namespaced_service"] = service()
        self.transport.results["read_namespaced_service"]["kind"] = "Secret"
        self.assert_code("KUBERNETES_LIST_INVALID", self.client.get_user_service, consumer=KubernetesConsumer.LIVE_PREFLIGHT)

    def test_mixed_kind_and_wrong_owner_chain(self):
        replica_identity = self.captured_replica_set_identity()
        self.transport.results["list_namespaced_pod"] = list_result([pod(), {**pod("other"), "kind": "Service"}])
        self.assert_code("KUBERNETES_LIST_INVALID", self.client.list_pods, PodSelector.ALL, consumer=KubernetesConsumer.LIVE_PREFLIGHT)
        bad = replica_set(); bad["metadata"]["ownerReferences"][0]["name"] = "unrelated"
        self.transport.results["read_namespaced_replica_set"] = bad
        self.assert_code("KUBERNETES_CAPTURE_OWNER_INVALID", self.client.get_captured_replica_set, replica_identity, consumer=KubernetesConsumer.CONTRACT_EVALUATOR)

    def test_wrong_captured_uid_and_resource_version(self):
        expected = self.captured_pod_identity()
        for field, value in (("uid", "different"), ("resourceVersion", "99")):
            self.transport.results["read_namespaced_pod"] = pod()
            self.transport.results["read_namespaced_pod"]["metadata"][field] = value
            self.assert_code("KUBERNETES_CAPTURE_IDENTITY_MISMATCH", self.client.get_captured_pod, expected, consumer=KubernetesConsumer.CONTRACT_EVALUATOR)

    def test_unapproved_projected_field_rejected(self):
        capture = self.client.list_deployments(consumer=KubernetesConsumer.LIVE_PREFLIGHT)
        value = parse_canonical_json(capture.response_evidence.payload_bytes)
        value["items"][0]["projected_fields"]["metadata"]["annotations"] = {"x": "y"}
        self.assert_code("KUBERNETES_PROJECTION_FIELD_FORBIDDEN", validate_kubernetes_list_response, self.policy, value)

    def test_secret_like_allowed_value_is_rejected(self):
        bad = pod(); bad["metadata"]["labels"]["service"] = "Bearer abcdefghijklmnopqrstuvwxyz"
        self.transport.results["list_namespaced_pod"] = list_result([bad])
        self.assert_code("KUBERNETES_LIST_INVALID", self.client.list_pods, PodSelector.USER_SERVICE, consumer=KubernetesConsumer.LIVE_PREFLIGHT)
        self.transport.results["list_namespaced_pod"] = list_result([bad])
        self.assert_code("SENSITIVE_CAPTURE_REJECTED", self.client.list_pods, PodSelector.ALL, consumer=KubernetesConsumer.LIVE_PREFLIGHT)

    def test_malformed_endpoint_slice_values_are_rejected(self):
        mutations = (
            lambda value: value["endpoints"][0]["addresses"].__setitem__(0, "not-an-ip"),
            lambda value: value["endpoints"][0]["conditions"].__setitem__("ready", "yes"),
            lambda value: value["ports"][0].__setitem__("port", 70000),
            lambda value: value["ports"][0].__setitem__("protocol", "HTTP"),
            lambda value: value["endpoints"][0]["targetRef"].__setitem__("uid", 7),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                value = endpoint_slice()
                mutate(value)
                self.transport.results["list_namespaced_endpoint_slice"] = list_result([value])
                self.assert_code(
                    "KUBERNETES_LIST_INVALID",
                    self.client.list_user_service_endpoint_slices,
                    consumer=KubernetesConsumer.CONTRACT_EVALUATOR,
                )

    def test_service_extra_ports_are_closed_and_raw_certificates_reject(self):
        value = service()
        value["spec"]["ports"].append(
            {"name": "metrics", "protocol": "TCP", "port": 9091, "targetPort": "metrics"}
        )
        self.transport.results["read_namespaced_service"] = value
        projected = parse_canonical_json(
            self.client.get_user_service(
                consumer=KubernetesConsumer.LIVE_PREFLIGHT
            ).response_evidence.payload_bytes
        )
        self.assertEqual(len(projected["spec"]["ports"]), 2)
        self.assertNotIn("unexpected", projected["spec"]["ports"][1])

        value = service()
        value["metadata"]["annotations"]["meta.helm.sh/release-name"] = (
            "-----BEGIN CERTIFICATE-----"
        )
        self.transport.results["read_namespaced_service"] = value
        self.assert_code(
            "SENSITIVE_CAPTURE_REJECTED",
            self.client.get_user_service,
            consumer=KubernetesConsumer.LIVE_PREFLIGHT,
        )


class CaptureTimeIdentityTests(KubernetesReadOnlyCase):
    def test_complete_collection_first_owner_chain(self):
        captures = []
        captures.append(
            self.client.list_deployments(
                consumer=KubernetesConsumer.LIVE_PREFLIGHT
            )
        )
        captures.append(
            self.client.get_deployment(
                "user-service",
                consumer=KubernetesConsumer.LIVE_PREFLIGHT,
            )
        )
        pod_list = self.client.list_pods(
            PodSelector.USER_SERVICE,
            consumer=KubernetesConsumer.CONTRACT_EVALUATOR,
        )
        captures.append(pod_list)
        pod_identity = self.capture_identity(
            pod_list,
            expected_kind="Pod",
            item_name="user-service-abc",
        )
        pod_capture = self.client.get_captured_pod(
            pod_identity,
            consumer=KubernetesConsumer.CONTRACT_EVALUATOR,
        )
        captures.append(pod_capture)
        replica_identity = self.capture_identity(
            pod_capture,
            expected_kind="ReplicaSet",
        )
        self.assertIsNone(replica_identity.resource_version)
        replica_capture = self.client.get_captured_replica_set(
            replica_identity,
            consumer=KubernetesConsumer.CONTRACT_EVALUATOR,
        )
        captures.append(replica_capture)
        captures.append(
            self.client.get_user_service(
                consumer=KubernetesConsumer.LIVE_PREFLIGHT
            )
        )
        captures.append(
            self.client.get_coredns_deployment(
                consumer=KubernetesConsumer.LIVE_PREFLIGHT
            )
        )
        captures.append(
            self.client.list_coredns_pods(
                consumer=KubernetesConsumer.LIVE_PREFLIGHT
            )
        )
        self.assertEqual(
            [method for method, _arguments in self.transport.calls],
            [
                "list_namespaced_deployment",
                "read_namespaced_deployment",
                "list_namespaced_pod",
                "read_namespaced_pod",
                "read_namespaced_replica_set",
                "read_namespaced_service",
                "read_namespaced_deployment",
                "list_namespaced_pod",
            ],
        )
        for capture in captures:
            validate_prepublication_capture(self.policy, capture)
        replica = parse_canonical_json(
            replica_capture.response_evidence.payload_bytes
        )
        owner = replica["metadata"]["ownerReferences"][0]
        self.assertEqual(
            (
                pod_identity.name,
                replica_identity.name,
                owner["kind"],
                owner["name"],
            ),
            (
                "user-service-abc",
                "user-service-rs",
                "Deployment",
                "user-service",
            ),
        )

    def test_direct_forged_and_cross_session_identities_reject(self):
        forged = captured("Pod", pod())
        self.assert_code(
            "KUBERNETES_CAPTURE_CLASS_INVALID",
            self.client.get_captured_pod,
            forged,
            consumer=KubernetesConsumer.CONTRACT_EVALUATOR,
        )
        identity = self.captured_pod_identity()
        other_client = ReadOnlyKubernetesClient(
            policy=self.policy,
            transport=self.transport,
            context="kind-kind",
            namespace="social-network",
            timeout_seconds=7,
            run_id=RUN_ID,
            attempt_id=ATTEMPT_ID,
        )
        self.assert_code(
            "KUBERNETES_CAPTURE_IDENTITY_MISMATCH",
            other_client.get_captured_pod,
            identity,
            consumer=KubernetesConsumer.CONTRACT_EVALUATOR,
        )

    def test_cross_run_attempt_and_consumer_reject(self):
        identity = self.captured_pod_identity()
        for run_id, attempt_id in (
            ("sremut-ms-m01-r01-a01-fedcba654321", ATTEMPT_ID),
            (RUN_ID, "a02"),
        ):
            with self.subTest(run_id=run_id, attempt_id=attempt_id):
                client = ReadOnlyKubernetesClient(
                    policy=self.policy,
                    transport=self.transport,
                    context="kind-kind",
                    namespace="social-network",
                    timeout_seconds=7,
                    run_id=run_id,
                    attempt_id=attempt_id,
                )
                self.assert_code(
                    "KUBERNETES_CAPTURE_IDENTITY_MISMATCH",
                    client.get_captured_pod,
                    identity,
                    consumer=KubernetesConsumer.CONTRACT_EVALUATOR,
                )
        self.assert_code(
            "KUBERNETES_CAPTURE_IDENTITY_MISMATCH",
            self.client.get_captured_pod,
            identity,
            consumer=KubernetesConsumer.LIVE_PREFLIGHT,
        )

    def test_wrong_selector_cross_request_and_mutable_source_reject(self):
        all_pods = self.client.list_pods(
            PodSelector.ALL,
            consumer=KubernetesConsumer.CONTRACT_EVALUATOR,
        )
        reference, descriptor = self.prepared_projection(all_pods)
        self.assert_code(
            "KUBERNETES_CAPTURE_SOURCE_MISMATCH",
            CapturedObjectIdentity.from_capture,
            all_pods,
            reference,
            descriptor,
            expected_kind="Pod",
            item_name="user-service-abc",
        )

        pod_list = self.client.list_pods(
            PodSelector.USER_SERVICE,
            consumer=KubernetesConsumer.CONTRACT_EVALUATOR,
        )
        unrelated = self.client.list_deployments(
            consumer=KubernetesConsumer.CONTRACT_EVALUATOR,
        )
        wrong_reference, wrong_descriptor = self.prepared_projection(unrelated)
        self.assert_code(
            "KUBERNETES_CAPTURE_UNRESOLVED",
            CapturedObjectIdentity.from_capture,
            pod_list,
            wrong_reference,
            wrong_descriptor,
            expected_kind="Pod",
            item_name="user-service-abc",
        )

        reference, descriptor = self.prepared_projection(pod_list)
        self.transport.results["list_namespaced_pod"]["items"][0]["metadata"][
            "uid"
        ] = "uid-mutated-after-capture"
        identity = CapturedObjectIdentity.from_capture(
            pod_list,
            reference,
            descriptor,
            expected_kind="Pod",
            item_name="user-service-abc",
        )
        self.assertEqual(identity.uid, "uid-user-service-abc")

    def test_pod_match_cardinality_and_identity_fields_fail_closed(self):
        for items in ([], [pod(), pod()]):
            with self.subTest(count=len(items)):
                self.transport.results["list_namespaced_pod"] = list_result(items)
                capture = self.client.list_pods(
                    PodSelector.USER_SERVICE,
                    consumer=KubernetesConsumer.CONTRACT_EVALUATOR,
                )
                reference, descriptor = self.prepared_projection(capture)
                self.assert_code(
                    "KUBERNETES_CAPTURE_UNRESOLVED",
                    CapturedObjectIdentity.from_capture,
                    capture,
                    reference,
                    descriptor,
                    expected_kind="Pod",
                    item_name="user-service-abc",
                )
        for field in ("uid", "resourceVersion"):
            with self.subTest(field=field):
                value = pod()
                value["metadata"][field] = ""
                self.transport.results["list_namespaced_pod"] = list_result(
                    [value]
                )
                self.assert_code(
                    "KUBERNETES_LIST_INVALID",
                    self.client.list_pods,
                    PodSelector.USER_SERVICE,
                    consumer=KubernetesConsumer.CONTRACT_EVALUATOR,
                )

    def test_replica_set_owner_and_returned_uid_fail_closed(self):
        pod_identity = self.captured_pod_identity()
        owner_mutations = (
            lambda owners: owners.clear(),
            lambda owners: owners.append(dict(owners[0])),
            lambda owners: owners[0].__setitem__("kind", "Deployment"),
        )
        for mutate in owner_mutations:
            with self.subTest(mutate=mutate):
                value = pod()
                mutate(value["metadata"]["ownerReferences"])
                self.transport.results["read_namespaced_pod"] = value
                self.assert_code(
                    "KUBERNETES_CAPTURE_OWNER_INVALID",
                    self.client.get_captured_pod,
                    pod_identity,
                    consumer=KubernetesConsumer.CONTRACT_EVALUATOR,
                )

        self.transport.results["read_namespaced_pod"] = pod()
        pod_capture = self.client.get_captured_pod(
            pod_identity,
            consumer=KubernetesConsumer.CONTRACT_EVALUATOR,
        )
        replica_identity = self.capture_identity(
            pod_capture,
            expected_kind="ReplicaSet",
        )
        self.transport.results["read_namespaced_replica_set"][
            "metadata"
        ]["uid"] = "wrong-replica-uid"
        self.assert_code(
            "KUBERNETES_CAPTURE_IDENTITY_MISMATCH",
            self.client.get_captured_replica_set,
            replica_identity,
            consumer=KubernetesConsumer.CONTRACT_EVALUATOR,
        )

    def test_collection_finishes_before_single_seal_and_hook_revalidation(self):
        temporary = tempfile.TemporaryDirectory(
            prefix="sremut-collection-first-seal-"
        )
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        prepared = []
        captures = []

        with EvidenceStore(root, self.policy, RUN_ID, ATTEMPT_ID) as store:
            def retain(capture, ordinal):
                common = {
                    "run_id": RUN_ID,
                    "attempt_id": ATTEMPT_ID,
                    "created_utc": "2026-08-20T10:00:00.123456789Z",
                    "monotonic_ns": ordinal * 10,
                    "boot_identity": "123e4567-e89b-12d3-a456-426614174000",
                }
                request_metadata = dict(
                    capture.request_evidence.publication_metadata(**common)
                )
                (
                    _request,
                    request_descriptor,
                    request_reference,
                ) = store._descriptor(
                    capture.request_evidence.role,
                    request_metadata,
                    None,
                )
                response_metadata = dict(
                    capture.response_evidence.publication_metadata(
                        **common,
                        request_identity_reference=request_reference,
                        request_identity_descriptor_bytes=request_descriptor,
                    )
                )
                (
                    _response,
                    response_descriptor,
                    response_reference,
                ) = store._descriptor(
                    capture.response_evidence.role,
                    response_metadata,
                    capture.response_evidence.payload_bytes,
                )
                prepared.append(
                    {
                        "capture": capture,
                        "request_metadata": request_metadata,
                        "request_descriptor": request_descriptor,
                        "request_reference": request_reference,
                        "response_metadata": response_metadata,
                        "response_descriptor": response_descriptor,
                        "response_reference": response_reference,
                    }
                )
                captures.append(capture)
                return response_reference, response_descriptor

            retain(
                self.client.list_deployments(
                    consumer=KubernetesConsumer.LIVE_PREFLIGHT
                ),
                1,
            )
            retain(
                self.client.get_deployment(
                    "user-service",
                    consumer=KubernetesConsumer.LIVE_PREFLIGHT,
                ),
                2,
            )
            pod_list = self.client.list_pods(
                PodSelector.USER_SERVICE,
                consumer=KubernetesConsumer.CONTRACT_EVALUATOR,
            )
            pod_reference, pod_descriptor = retain(pod_list, 3)
            pod_identity = CapturedObjectIdentity.from_capture(
                pod_list,
                pod_reference,
                pod_descriptor,
                expected_kind="Pod",
                item_name="user-service-abc",
            )
            pod_capture = self.client.get_captured_pod(
                pod_identity,
                consumer=KubernetesConsumer.CONTRACT_EVALUATOR,
            )
            pod_get_reference, pod_get_descriptor = retain(pod_capture, 4)
            replica_identity = CapturedObjectIdentity.from_capture(
                pod_capture,
                pod_get_reference,
                pod_get_descriptor,
                expected_kind="ReplicaSet",
            )
            retain(
                self.client.get_captured_replica_set(
                    replica_identity,
                    consumer=KubernetesConsumer.CONTRACT_EVALUATOR,
                ),
                5,
            )
            retain(
                self.client.get_user_service(
                    consumer=KubernetesConsumer.LIVE_PREFLIGHT
                ),
                6,
            )
            retain(
                self.client.get_coredns_deployment(
                    consumer=KubernetesConsumer.LIVE_PREFLIGHT
                ),
                7,
            )
            retain(
                self.client.list_coredns_pods(
                    consumer=KubernetesConsumer.LIVE_PREFLIGHT
                ),
                8,
            )

            self.assertEqual(
                store.fs.list_regular_files(
                    ("objects", "descriptors", "journal", "manifests")
                ),
                (),
            )
            for row in prepared:
                request_reference = store.publish_descriptor(
                    row["capture"].request_evidence.role,
                    row["request_metadata"],
                )
                self.assertEqual(
                    request_reference,
                    row["request_reference"],
                )
                response_reference = store.publish_payload(
                    row["capture"].response_evidence.role,
                    row["capture"].response_evidence.payload_bytes,
                    row["response_metadata"],
                )
                self.assertEqual(
                    response_reference,
                    row["response_reference"],
                )

            with Journal(root, self.policy, RUN_ID, ATTEMPT_ID) as journal:
                journal.append_state_transition(
                    "CREATED->PREFLIGHT_PASS",
                    utc_time="2026-08-20T10:00:00.123456789Z",
                    monotonic_ns=100,
                    boot_identity="123e4567-e89b-12d3-a456-426614174000",
                    descriptor_sha256=tuple(
                        row["request_reference"].descriptor_sha256
                        for row in prepared
                    ),
                )
                ordered = [
                    prepared[index]
                    for index in (2, 0, 3, 1, 4, 5, 6, 7)
                ]
                previous = None
                for ordinal, row in enumerate(ordered, 1):
                    request_reference = row["request_reference"]
                    response_reference = row["response_reference"]
                    request = dict(row["capture"].request.canonical)
                    marker = {
                        "operation_kind": None,
                        "request_rule_id": request["request_rule_id"],
                        "resource": request["resource"],
                        "namespace": request["namespace"],
                        "name": request["name"],
                        "request_reference": request_reference.as_dict(),
                        "projection_reference": response_reference.as_dict(),
                    }
                    journal.append_state_transition(
                        "OPERATION_AUTHORIZED:"
                        + canonical_json_bytes(marker).hex(),
                        utc_time="2026-08-20T10:00:00.123456789Z",
                        monotonic_ns=100 + ordinal,
                        boot_identity="123e4567-e89b-12d3-a456-426614174000",
                        descriptor_sha256=(
                            ()
                            if previous is None
                            else (
                                previous["response_reference"].descriptor_sha256,
                            )
                        ),
                        payload_sha256=(
                            ()
                            if previous is None
                            else (
                                previous["response_reference"].payload_sha256,
                            )
                        ),
                    )
                    previous = row
                journal.append_state_transition(
                    "PREFLIGHT_PASS->ABORTED_SAFE",
                    utc_time="2026-08-20T10:00:00.123456789Z",
                    monotonic_ns=1000,
                    boot_identity="123e4567-e89b-12d3-a456-426614174000",
                    descriptor_sha256=(
                        previous["response_reference"].descriptor_sha256,
                    ),
                    payload_sha256=(
                        previous["response_reference"].payload_sha256,
                    ),
                )
            seal = store.seal("ABORTED_SAFE")
            self.assertEqual(seal.terminal_outcome, "ABORTED_SAFE")
            with self.assertRaises(ValueError) as caught:
                store.publish_descriptor(
                    prepared[0]["capture"].request_evidence.role,
                    prepared[0]["request_metadata"],
                )
            self.assertEqual(str(caught.exception), "ATTEMPT_ALREADY_SEALED")

        anchor = ExternalAnchor(
            "collection-first-attempt",
            seal.manifest_relative_path,
            seal.manifest_sha256,
        )
        context = resolve_evidence_context(
            self.policy,
            root,
            RUN_ID,
            ATTEMPT_ID,
            anchor,
            expected_terminal_manifest_identity=seal.manifest_sha256,
        )
        for row in prepared:
            request_candidate = parse_canonical_json(
                context.evidence[
                    row["request_reference"].evidence_id
                ].descriptor_bytes
            )
            response_candidate = parse_canonical_json(
                context.evidence[
                    row["response_reference"].evidence_id
                ].descriptor_bytes
            )
            request_result = self.policy.full_admissibility(
                request_candidate,
                context,
            )
            response_result = self.policy.full_admissibility(
                response_candidate,
                context,
            )
            self.assertEqual(
                next(
                    outcome.outcome
                    for outcome in request_result.hook_outcomes
                    if outcome.hook_id
                    == "VALIDATE_KUBERNETES_REQUEST_V1"
                ),
                "PASS",
            )
            self.assertEqual(
                next(
                    outcome.outcome
                    for outcome in response_result.hook_outcomes
                    if outcome.hook_id
                    == "VALIDATE_KUBERNETES_RESPONSE_V1"
                ),
                "PASS",
            )
            self.assertTrue(request_result.valid)
            self.assertTrue(response_result.valid)
            self.assertIsNone(request_result.failure_code)
            self.assertIsNone(response_result.failure_code)





class BindingAndSafetyTests(KubernetesReadOnlyCase):
    def test_collection_first_capture_identity_bootstrap(self):
        pod_list_capture = self.client.list_pods(
            PodSelector.USER_SERVICE,
            consumer=KubernetesConsumer.CONTRACT_EVALUATOR,
        )
        with tempfile.TemporaryDirectory(prefix="sremut-capture-bootstrap-") as root:
            with EvidenceStore(Path(root), self.policy, RUN_ID, ATTEMPT_ID) as store:
                common = {
                    "run_id": RUN_ID,
                    "attempt_id": ATTEMPT_ID,
                    "created_utc": "2026-08-20T10:00:00.123456789Z",
                    "monotonic_ns": 1,
                    "boot_identity": "123e4567-e89b-12d3-a456-426614174000",
                }
                _request, request_descriptor, request_reference = store._descriptor(
                    pod_list_capture.request_evidence.role,
                    pod_list_capture.request_evidence.publication_metadata(**common),
                    None,
                )
                response_metadata = pod_list_capture.response_evidence.publication_metadata(
                    **common,
                    request_identity_reference=request_reference,
                    request_identity_descriptor_bytes=request_descriptor,
                )
                _response, response_descriptor, response_reference = store._descriptor(
                    pod_list_capture.response_evidence.role,
                    response_metadata,
                    pod_list_capture.response_evidence.payload_bytes,
                )
                self.assertEqual(
                    store.fs.list_regular_files(("objects", "descriptors", "journal")),
                    (),
                )

        identity = CapturedObjectIdentity.from_capture(
            pod_list_capture,
            response_reference,
            response_descriptor,
            expected_kind="Pod",
            item_name="user-service-abc",
        )
        pod_capture = self.client.get_captured_pod(
            identity,
            consumer=KubernetesConsumer.CONTRACT_EVALUATOR,
        )
        self.assertEqual(
            self.transport.calls[-1],
            ("read_namespaced_pod", ("user-service-abc", "social-network", 7)),
        )
        validate_prepublication_capture(self.policy, pod_capture)

    def test_response_request_binding_and_cross_identity(self):
        capture = self.client.list_deployments(consumer=KubernetesConsumer.LIVE_PREFLIGHT)
        validate_prepublication_capture(self.policy, capture)
        object.__setattr__(capture.request, "identity_sha256", "f" * 64)
        self.assert_code("KUBERNETES_REQUEST_IDENTITY_MISMATCH", validate_prepublication_capture, self.policy, capture)

    def test_list_response_wrong_request_selector_option_kind_and_status_substitution(self):
        capture = self.client.list_deployments(consumer=KubernetesConsumer.LIVE_PREFLIGHT)
        value = parse_canonical_json(capture.response_evidence.payload_bytes)
        for field, changed_value in (("label_selector", "x=y"), ("request_options", {"watch": True, "pretty": False, "dry_run": False})):
            changed = copy.deepcopy(value); changed["request"][field] = changed_value
            self.assert_code("KUBERNETES_REQUEST_RULE_INVALID", validate_kubernetes_list_response, self.policy, changed)
        changed = copy.deepcopy(value); changed["items"][0]["projection_class"] = "STATUS_V1"
        self.assert_code("KUBERNETES_LIST_INVALID", validate_kubernetes_list_response, self.policy, changed)

    def test_import_is_lazy_and_public_surface_has_no_raw_client(self):
        self.assertNotIn("kubernetes", sys.modules)
        names = set(dir(ReadOnlyKubernetesClient))
        self.assertFalse(names & {"call_api", "api_client", "core_v1_api", "apps_v1_api", "discovery_v1_api"})

    def test_live_transport_requires_explicit_absolute_kubeconfig(self):
        self.assert_code(
            "KUBERNETES_CLIENT_CONFIGURATION_INVALID",
            Kubernetes32ReadOnlyTransport,
            kubeconfig_path=Path("relative"), context="kind-kind", policy=self.policy,
            namespace="social-network", timeout_seconds=7,
        )

    def test_capture_does_not_publish(self):
        before = set(REPOSITORY.rglob("*"))
        self.client.get_user_service(consumer=KubernetesConsumer.LIVE_PREFLIGHT)
        after = set(REPOSITORY.rglob("*"))
        self.assertEqual(before, after)

    def test_non_research_marker_is_preserved(self):
        client = ReadOnlyKubernetesClient(
            policy=self.policy, transport=self.transport, context="kind-kind",
            namespace="social-network", timeout_seconds=7,
            run_id=PREFLIGHT_MARKER, attempt_id=PREFLIGHT_MARKER,
        )
        self.assertEqual(client.get_user_service(consumer=KubernetesConsumer.LIVE_PREFLIGHT).classification, PREFLIGHT_MARKER)


class ResolvedHookIntegrationTests(KubernetesReadOnlyCase):
    CREATED_UTC = "2026-08-20T10:00:00.123456789Z"
    BOOT = "123e4567-e89b-12d3-a456-426614174000"

    def _context(self, *, wrong_request_reference=False, wrong_list_rv=False, pod_list=False):
        capture = (
            self.client.list_pods(PodSelector.USER_SERVICE, consumer=KubernetesConsumer.CONTRACT_EVALUATOR)
            if pod_list else
            self.client.list_deployments(consumer=KubernetesConsumer.CONTRACT_EVALUATOR)
        )
        temporary = tempfile.TemporaryDirectory(prefix="sremut-kubernetes-hook-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        common = {"created_utc": self.CREATED_UTC, "monotonic_ns": 1, "boot_identity": self.BOOT}
        with EvidenceStore(root, self.policy, RUN_ID, ATTEMPT_ID) as store:
            request_reference = store.publish_descriptor(
                capture.request_evidence.role,
                capture.request_evidence.publication_metadata(
                    run_id=RUN_ID, attempt_id=ATTEMPT_ID, **common,
                ),
            )
            bound_reference = request_reference
            if wrong_request_reference:
                alternate = self.client.get_deployment("user-service", consumer=KubernetesConsumer.CONTRACT_EVALUATOR)
                bound_reference = store.publish_descriptor(
                    alternate.request_evidence.role,
                    alternate.request_evidence.publication_metadata(
                        run_id=RUN_ID, attempt_id=ATTEMPT_ID,
                        created_utc=self.CREATED_UTC, monotonic_ns=2, boot_identity=self.BOOT,
                    ),
                )
            if wrong_request_reference:
                response_metadata = {
                    "run_id": RUN_ID, "attempt_id": ATTEMPT_ID,
                    "created_utc": self.CREATED_UTC, "monotonic_ns": 3,
                    "boot_identity": self.BOOT,
                    **parse_canonical_json(canonical_json_bytes(dict(capture.response_evidence.metadata))),
                    "request_identity_reference": bound_reference.as_dict(),
                }
            else:
                bound_descriptor_bytes = store.resolve(bound_reference)[1]
                response_metadata = dict(capture.response_evidence.publication_metadata(
                    run_id=RUN_ID, attempt_id=ATTEMPT_ID,
                    created_utc=self.CREATED_UTC, monotonic_ns=3, boot_identity=self.BOOT,
                    request_identity_reference=bound_reference,
                    request_identity_descriptor_bytes=bound_descriptor_bytes,
                ))
            if wrong_list_rv:
                response_metadata["source_list_resource_version"] = "wrong"
            response_reference = store.publish_payload(
                capture.response_evidence.role,
                capture.response_evidence.payload_bytes,
                response_metadata,
            )
            canonical_request = dict(capture.request.canonical)
            marker = {
                "operation_kind": None,
                "request_rule_id": canonical_request["request_rule_id"],
                "resource": canonical_request["resource"],
                "namespace": canonical_request["namespace"],
                "name": canonical_request["name"],
                "request_reference": request_reference.as_dict(),
                "projection_reference": response_reference.as_dict(),
            }
            with Journal(root, self.policy, RUN_ID, ATTEMPT_ID) as journal:
                journal.append_state_transition(
                    "CREATED->PREFLIGHT_PASS", utc_time=self.CREATED_UTC,
                    monotonic_ns=1, boot_identity=self.BOOT,
                    descriptor_sha256=(request_reference.descriptor_sha256,) + ((bound_reference.descriptor_sha256,) if wrong_request_reference else ()),
                )
                journal.append_state_transition(
                    "OPERATION_AUTHORIZED:" + canonical_json_bytes(marker).hex(),
                    utc_time=self.CREATED_UTC, monotonic_ns=2, boot_identity=self.BOOT,
                )
                journal.append_state_transition(
                    "PREFLIGHT_PASS->ABORTED_SAFE", utc_time=self.CREATED_UTC,
                    monotonic_ns=3, boot_identity=self.BOOT,
                    descriptor_sha256=(response_reference.descriptor_sha256,),
                    payload_sha256=(response_reference.payload_sha256,),
                )
            seal = store.seal("ABORTED_SAFE")
        anchor = ExternalAnchor("kubernetes-hook-attempt", seal.manifest_relative_path, seal.manifest_sha256)
        context = resolve_evidence_context(
            self.policy, root, RUN_ID, ATTEMPT_ID, anchor,
            expected_terminal_manifest_identity=seal.manifest_sha256,
        )
        request_candidate = parse_canonical_json(context.evidence[request_reference.evidence_id].descriptor_bytes)
        response_candidate = parse_canonical_json(context.evidence[response_reference.evidence_id].descriptor_bytes)
        return context, request_candidate, response_candidate

    def test_authenticated_pod_list_bootstraps_captured_pod_get(self):
        context, _request, _response = self._context(pod_list=True)
        row = next(
            item for item in context.evidence.values()
            if item.reference.role == "kubernetes_object_projection"
        )
        identity = CapturedObjectIdentity.from_resolved_context(
            context,
            row.reference,
            expected_kind="Pod",
            item_name="user-service-abc",
        )
        self.assertEqual(
            (identity.name, identity.uid, identity.resource_version),
            ("user-service-abc", "uid-user-service-abc", "10"),
        )
        capture = self.client.get_captured_pod(
            identity,
            consumer=KubernetesConsumer.CONTRACT_EVALUATOR,
        )
        self.assertEqual(
            capture.request.canonical["captured_object_reference"],
            row.reference.as_dict(),
        )
        validate_prepublication_capture(self.policy, capture)

    def test_kubernetes_hooks_remain_connected_in_frozen_positions(self):
        request_hook = "VALIDATE_KUBERNETES_REQUEST_V1"
        response_hook = "VALIDATE_KUBERNETES_RESPONSE_V1"
        self.assertIn(request_hook, CONNECTED_HOOKS)
        self.assertIn(response_hook, CONNECTED_HOOKS)
        self.assertEqual(EXPECTED_HOOK_ORDER.index(request_hook), 8)
        self.assertEqual(EXPECTED_HOOK_ORDER.index(response_hook), 9)
        self.assertEqual(EXPECTED_HOOK_ORDER.count(request_hook), 1)
        self.assertEqual(EXPECTED_HOOK_ORDER.count(response_hook), 1)
        context, request, response = self._context()
        request_result = self.policy.full_admissibility(request, context)
        response_result = self.policy.full_admissibility(response, context)
        self.assertTrue(request_result.valid)
        self.assertTrue(response_result.valid)
        request_rows = [row for row in request_result.hook_outcomes if row.hook_id == request_hook]
        response_rows = [row for row in response_result.hook_outcomes if row.hook_id == response_hook]
        self.assertEqual([(row.hook_id, row.outcome) for row in request_rows], [(request_hook, "PASS")])
        self.assertEqual([(row.hook_id, row.outcome) for row in response_rows], [(response_hook, "PASS")])

    def test_wrong_request_reference_fails_through_public_dispatcher(self):
        context, _request, response = self._context(wrong_request_reference=True)
        result = self.policy.full_admissibility(response, context)
        self.assertFalse(result.valid)
        self.assertEqual(result.hook_id, "VALIDATE_KUBERNETES_RESPONSE_V1")
        self.assertEqual(result.failure_code, "KUBERNETES_PROJECTION_REQUEST_MISMATCH")

    def test_wrong_list_resource_version_fails_before_later_restoration_hook(self):
        context, _request, response = self._context(wrong_list_rv=True)
        result = self.policy.full_admissibility(response, context)
        self.assertFalse(result.valid)
        self.assertEqual(result.hook_id, "VALIDATE_KUBERNETES_RESPONSE_V1")
        self.assertEqual(result.failure_code, "KUBERNETES_LIST_RESOURCE_VERSION_MISMATCH")
        self.assertNotIn("VALIDATE_SERVICE_RESTORATION_BODY_V1", [row.hook_id for row in result.hook_outcomes])


if __name__ == "__main__":
    unittest.main()

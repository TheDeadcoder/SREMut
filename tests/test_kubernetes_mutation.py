"""Offline tests for the closed guarded Kubernetes mutation kernel."""

from __future__ import annotations

import copy
from dataclasses import FrozenInstanceError, replace
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest

from sremut.canonical_json import (
    canonical_json_bytes,
    canonical_json_line,
    parse_canonical_json,
)
from sremut.evidence import EvidenceStore
from sremut.journal import Journal, SafeRoot
from sremut.kubernetes_mutation import (
    GuardedMutationSession,
    KubernetesMutationError,
    MutationObservation,
    MutationTransportResult,
    ObservedEffect,
    MUTATION_LEDGER,
    MUTATION_LEDGER_EVENTS,
    OPERATION_TABLE,
    ReplacementPodTarget,
    RestorationBodyCapability,
    VerifiedRunnerBundle,
    authenticate_restoration_body,
    select_replacement_pod,
    validate_mutation_ledger_bytes,
)
from sremut.kubernetes_readonly import (
    KubernetesConsumer,
    PodSelector,
    ReadOnlyKubernetesClient,
)
from sremut.policy_runtime import POLICY_MANIFEST_SHA256, load_policy_bundle
from sremut.service_restoration import (
    SERVICE_RESTORATION_BODY,
    SERVICE_RESTORATION_SOURCE,
    _derive_document,
)


ROOT = Path(__file__).resolve().parents[1]
RUN_ID = "sremut-ms-m01-r01-a01-abcdef123456"
ATTEMPT_ID = "a01"
UTC = "2026-08-20T10:00:00.123456789Z"
BOOT = "123e4567-e89b-12d3-a456-426614174000"


def load_policy():
    return load_policy_bundle(
        ROOT / "policies/missing_service_social_network/evidence-capture-v1.1.yaml",
        ROOT / "schemas/evidence-capture-policy-v1.1.schema.json",
        ROOT / "EVIDENCE_CAPTURE_POLICY_V1_1_SHA256SUMS",
        expected_manifest_sha256=POLICY_MANIFEST_SHA256,
    )


def metadata(name, namespace, uid, rv="10", **extra):
    value = {
        "name": name, "namespace": namespace, "uid": uid,
        "resourceVersion": rv, "generation": 1,
    }
    value.update(extra)
    return value


def service():
    return {
        "apiVersion": "v1", "kind": "Service",
        "metadata": metadata(
            "user-service", "social-network", "service-uid", "123",
            labels={"app.kubernetes.io/managed-by": "Helm"},
            annotations={
                "meta.helm.sh/release-name": "social-network",
                "meta.helm.sh/release-namespace": "social-network",
            },
        ),
        "spec": {
            "type": "ClusterIP", "clusterIP": "10.96.0.50",
            "clusterIPs": ["10.96.0.50"], "ipFamilies": ["IPv4"],
            "ipFamilyPolicy": "SingleStack", "internalTrafficPolicy": "Cluster",
            "sessionAffinity": "None", "selector": {"service": "user-service"},
            "ports": [{
                "name": "thrift", "protocol": "TCP", "port": 9090,
                "targetPort": 9090,
            }],
            "publishNotReadyAddresses": False,
            "allocateLoadBalancerNodePorts": False,
        },
    }


def pod(name, uid, rv):
    return {
        "apiVersion": "v1", "kind": "Pod",
        "metadata": metadata(
            name, "social-network", uid, rv,
            labels={"service": "user-service"},
            ownerReferences=[{
                "apiVersion": "apps/v1", "kind": "ReplicaSet",
                "name": "user-service-rs", "uid": "rs-uid", "controller": True,
            }],
        ),
        "status": {
            "phase": "Running", "podIP": "10.0.0.2",
            "conditions": [{
                "type": "Ready", "status": "True", "reason": "Ready",
                "lastTransitionTime": "2026-01-01T00:00:00Z",
            }],
            "containerStatuses": [{
                "name": "user-service", "ready": True, "restartCount": 0,
                "state": {},
            }],
        },
    }


def list_result(items):
    return {
        "metadata": {
            "resourceVersion": "20", "continue": "", "remainingItemCount": 0,
        },
        "items": items,
    }


class FakeReadTransport:
    def __init__(self):
        self.pods = list_result([
            pod("user-service-z", "pod-z", "11"),
            pod("user-service-a", "pod-a", "12"),
        ])

    def read_namespaced_deployment(self, name, namespace, timeout_seconds):
        raise AssertionError("unused")

    def list_namespaced_deployment(self, namespace, label_selector, field_selector, timeout_seconds):
        raise AssertionError("unused")

    def read_namespaced_replica_set(self, name, namespace, timeout_seconds):
        raise AssertionError("unused")

    def read_namespaced_pod(self, name, namespace, timeout_seconds):
        return copy.deepcopy(self.pods["items"][0])

    def list_namespaced_pod(self, namespace, label_selector, field_selector, timeout_seconds):
        return copy.deepcopy(self.pods)

    def read_namespaced_service(self, name, namespace, timeout_seconds):
        return copy.deepcopy(service())

    def list_namespaced_endpoint_slice(self, namespace, label_selector, field_selector, timeout_seconds):
        raise AssertionError("unused")


class FakeMutationTransport:
    def __init__(self, attempt_root):
        self.attempt_root = Path(attempt_root)
        self.calls = []
        self.response = MutationTransportResult(200, None, None, "Success")
        self.failure = None

    def _call(self, method, *args):
        ledger = self.attempt_root / "journal/mutation-events.jsonl"
        if not ledger.exists() or b'"event":"INTENT_DURABLE"' not in ledger.read_bytes():
            raise RuntimeError("intent-not-durable")
        self.calls.append((method, args))
        if self.failure is not None:
            raise self.failure
        return self.response

    def delete_namespaced_service(self, name, namespace, body, timeout_seconds):
        return self._call("delete_namespaced_service", name, namespace, body, timeout_seconds)

    def delete_namespaced_pod(self, name, namespace, body, timeout_seconds):
        return self._call("delete_namespaced_pod", name, namespace, body, timeout_seconds)

    def create_namespaced_service(self, namespace, body, timeout_seconds):
        return self._call("create_namespaced_service", namespace, body, timeout_seconds)

    def create_namespaced_pod(self, namespace, body, timeout_seconds):
        return self._call("create_namespaced_pod", namespace, body, timeout_seconds)


class MutationCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.policy = load_policy()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="sremut-mutation-test-")
        self.base = Path(self.temporary.name)
        self.attempt = self.base / "attempt"
        self.host = self.base / "host-lock"
        self.attempt.mkdir(mode=0o700)
        self.host.mkdir(mode=0o700)
        self.read_transport = FakeReadTransport()
        self.read_client = ReadOnlyKubernetesClient(
            policy=self.policy, transport=self.read_transport,
            context="kind-kind", namespace="social-network",
            timeout_seconds=7, run_id=RUN_ID, attempt_id=ATTEMPT_ID,
        )
        manifest = b"a" * 64 + b"  src/sremut/example.py\n"
        self.runner = VerifiedRunnerBundle.authenticate(
            {
                "manifest_sha256": hashlib.sha256(manifest).hexdigest(),
                "bundle_sha256": hashlib.sha256(manifest).hexdigest(),
                "git_commit": "1" * 40, "git_tree": "2" * 40,
                "annotated_tag_name": "fixture-runner-v1",
                "annotated_tag_object": "3" * 40,
                "pyproject_sha256": "4" * 64, "uv_lock_sha256": "5" * 64,
            },
            manifest,
        )
        self.transport = FakeMutationTransport(self.attempt)
        self.counter = 1

    def tearDown(self):
        self.temporary.cleanup()

    def assert_code(self, code, function, *args, **kwargs):
        with self.assertRaises(Exception) as caught:
            function(*args, **kwargs)
        self.assertEqual(str(caught.exception), code)
        self.assertNotIn("secret", str(caught.exception).lower())
        return caught.exception

    def common(self, monotonic=None):
        if monotonic is None:
            monotonic = self.counter
            self.counter += 1
        return {
            "run_id": RUN_ID, "attempt_id": ATTEMPT_ID, "created_utc": UTC,
            "monotonic_ns": monotonic, "boot_identity": BOOT,
        }

    def advance(self, state):
        paths = {
            "PREFLIGHT_PASS": ("CREATED->PREFLIGHT_PASS",),
            "HEALTHY_STATE_CAPTURED": (
                "CREATED->PREFLIGHT_PASS",
                "PREFLIGHT_PASS->HEALTHY_STATE_CAPTURED",
            ),
            "ORIGINAL_ORACLE_EVALUATED": (
                "CREATED->PREFLIGHT_PASS",
                "PREFLIGHT_PASS->HEALTHY_STATE_CAPTURED",
                "HEALTHY_STATE_CAPTURED->MUTANT_INJECTED",
                "MUTANT_INJECTED->MUTANT_STATE_VERIFIED",
                "MUTANT_STATE_VERIFIED->ORIGINAL_ORACLE_STARTED",
                "ORIGINAL_ORACLE_STARTED->ORIGINAL_ORACLE_EVALUATED",
            ),
            "CONTRACT_EVALUATED": (
                "CREATED->PREFLIGHT_PASS",
                "PREFLIGHT_PASS->HEALTHY_STATE_CAPTURED",
                "HEALTHY_STATE_CAPTURED->MUTANT_INJECTED",
                "MUTANT_INJECTED->MUTANT_STATE_VERIFIED",
                "MUTANT_STATE_VERIFIED->ORIGINAL_ORACLE_STARTED",
                "ORIGINAL_ORACLE_STARTED->ORIGINAL_ORACLE_EVALUATED",
                "ORIGINAL_ORACLE_EVALUATED->CONTRACT_EVALUATED",
            ),
            "RESTORE_STARTED": (
                "CREATED->PREFLIGHT_PASS",
                "PREFLIGHT_PASS->HEALTHY_STATE_CAPTURED",
                "HEALTHY_STATE_CAPTURED->RESTORE_STARTED",
            ),
        }
        with Journal(self.attempt, self.policy, RUN_ID, ATTEMPT_ID) as journal:
            for transition in paths[state]:
                journal.append_state_transition(
                    transition, utc_time=UTC, monotonic_ns=self.counter,
                    boot_identity=BOOT,
                )
                self.counter += 1

    def open_session(self):
        binding = self.policy.policy["bindings"]["execution_profile"]
        return GuardedMutationSession.open(
            policy=self.policy,
            execution_profile_tag_object=binding["tag_object"],
            execution_profile_sha256=binding["sha256"],
            run_id=RUN_ID, attempt_id=ATTEMPT_ID, mutant_id="MS-M01",
            repetition=1, attempt_number=1, runner_bundle=self.runner,
            host_lock_root=self.host, attempt_root=self.attempt,
            transport=self.transport, read_session=self.read_client,
            timeout_seconds=7, boot_identity=BOOT,
            utc_clock=lambda: UTC,
            monotonic_clock=self._monotonic,
        )

    def _monotonic(self):
        value = self.counter
        self.counter += 1
        return value

    def publish_capture(self, capture, *, projection_class=None, capture_state="HEALTHY_STATE_CAPTURED"):
        with EvidenceStore(self.attempt, self.policy, RUN_ID, ATTEMPT_ID) as store:
            request_ref = store.publish_descriptor(
                capture.request_evidence.role,
                capture.request_evidence.publication_metadata(**self.common()),
            )
            request_bytes = store.resolve(request_ref)[1]
            metadata_value = dict(
                capture.response_evidence.publication_metadata(
                    **self.common(),
                    request_identity_reference=request_ref,
                    request_identity_descriptor_bytes=request_bytes,
                )
            )
            if projection_class is not None:
                metadata_value["projection_class"] = projection_class
                metadata_value["projection_schema_id"] = projection_class
            metadata_value["capture_state"] = capture_state
            response_ref = store.publish_payload(
                capture.response_evidence.role,
                capture.response_evidence.payload_bytes,
                metadata_value,
            )
            return request_ref, response_ref, store.resolve(response_ref)[1]

    def restoration_capability(self):
        capture = self.read_client.get_user_service(
            consumer=KubernetesConsumer.MUTATION_CONTROLLER
        )
        request_ref, source_ref, _descriptor = self.publish_capture(
            capture, projection_class=SERVICE_RESTORATION_SOURCE
        )
        with EvidenceStore(self.attempt, self.policy, RUN_ID, ATTEMPT_ID) as store:
            source = parse_canonical_json(store.resolve(source_ref)[2])
            document = _derive_document(
                self.policy, source, request_ref, UTC, source_ref
            )
            payload = canonical_json_bytes(document)
            body_ref = store.publish_payload(
                "kubernetes_object_projection",
                payload,
                {
                    **self.common(),
                    "request_identity_reference": request_ref.as_dict(),
                    "object_count": 1,
                    "projection_class": SERVICE_RESTORATION_BODY,
                    "projection_schema_id": SERVICE_RESTORATION_BODY,
                    "capture_state": "HEALTHY_STATE_CAPTURED",
                    "trusted_source_sha256": hashlib.sha256(payload).hexdigest(),
                },
            )
            return authenticate_restoration_body(store, source_ref, body_ref)

    def replacement_target(self):
        capture = self.read_client.list_pods(
            PodSelector.USER_SERVICE,
            consumer=KubernetesConsumer.CONTRACT_EVALUATOR,
        )
        _request_ref, response_ref, descriptor = self.publish_capture(capture)
        return select_replacement_pod(capture, response_ref, descriptor)


class FrozenInventoryTests(MutationCase):
    def test_exact_nine_frozen_operations_and_five_implemented_methods(self):
        self.assertEqual(
            tuple(OPERATION_TABLE),
            (
                "INITIAL_USER_SERVICE_DELETION",
                "INITIAL_CAPTURED_POD_RECYCLE_DELETE",
                "MUTANT_SERVICE_CREATION",
                "CHALLENGE_POD_CREATION",
                "REPLACEMENT_POD_DELETION",
                "CHALLENGE_POD_DELETION",
                "MUTANT_SERVICE_DELETION",
                "RESTORED_SERVICE_CREATION",
                "RECOVERY_POD_RECYCLE_DELETE",
            ),
        )
        self.assertEqual(
            {row["method"] for row in OPERATION_TABLE.values()},
            {
                "CoreV1Api.delete_namespaced_service",
                "CoreV1Api.delete_namespaced_pod",
                "CoreV1Api.create_namespaced_service",
                "CoreV1Api.create_namespaced_pod",
            },
        )
        profile = self.policy.policy["kubernetes_evidence_surface"]["resource_rules"]
        by_id = {row["id"]: row for row in profile}
        self.assertEqual(
            by_id["PODS_SOCIAL_READ_AND_GUARDED_MUTATION"][
                "profile_operation_kinds_by_operation"
            ]["CREATE"],
            ("CHALLENGE_POD_CREATION",),
        )
        self.assertEqual(
            by_id["USER_SERVICE_READ_AND_GUARDED_MUTATION"][
                "profile_operation_kinds_by_operation"
            ]["DELETE"],
            ("INITIAL_USER_SERVICE_DELETION", "MUTANT_SERVICE_DELETION"),
        )

    def test_import_is_lazy_and_has_no_mutation_cli_surface(self):
        self.assertFalse(any(name == "kubernetes" or name.startswith("kubernetes.") for name in sys.modules))
        public = {name for name in dir(GuardedMutationSession) if not name.startswith("_")}
        self.assertEqual(
            public,
            {
                "close", "create_challenge_pod", "delete_challenge_pod",
                "delete_replacement_pod", "delete_user_service", "open",
                "reconcile", "reconcile_pending", "restore_user_service",
            },
        )


class SessionSafetyTests(MutationCase):
    def test_direct_construction_capability_construction_and_copy_reject(self):
        self.assert_code(
            "MUTATION_SESSION_DIRECT_CONSTRUCTION_FORBIDDEN",
            GuardedMutationSession,
        )
        with self.assertRaises(TypeError):
            RestorationBodyCapability()
        with self.assertRaises(TypeError):
            ReplacementPodTarget()
        self.advance("HEALTHY_STATE_CAPTURED")
        session = self.open_session()
        try:
            import copy as copy_module
            self.assert_code("MUTATION_SESSION_COPY_FORBIDDEN", copy_module.copy, session)
            self.assert_code("MUTATION_SESSION_COPY_FORBIDDEN", copy_module.deepcopy, session)
        finally:
            session.close()

    def test_host_lock_is_exclusive_and_released_on_close(self):
        self.advance("HEALTHY_STATE_CAPTURED")
        first = self.open_session()
        try:
            self.assert_code("JOURNAL_LOCK_UNAVAILABLE", self.open_session)
        finally:
            first.close()
        second = self.open_session()
        second.close()

    def test_wrong_mutant_runner_identity_and_terminal_attempt_reject(self):
        binding = self.policy.policy["bindings"]["execution_profile"]
        kwargs = {
            "policy": self.policy,
            "execution_profile_tag_object": binding["tag_object"],
            "execution_profile_sha256": binding["sha256"],
            "run_id": RUN_ID, "attempt_id": ATTEMPT_ID, "mutant_id": "MS-M02",
            "repetition": 1, "attempt_number": 1,
            "runner_bundle": self.runner, "host_lock_root": self.host,
            "attempt_root": self.attempt, "transport": self.transport,
            "read_session": self.read_client, "timeout_seconds": 7,
            "boot_identity": BOOT,
        }
        self.assert_code(
            "MUTATION_SESSION_IDENTITY_INVALID", GuardedMutationSession.open, **kwargs
        )
        with self.assertRaises(TypeError):
            VerifiedRunnerBundle()


class PrimitiveTests(MutationCase):
    def test_service_delete_has_uid_and_rv_precondition_and_durable_order(self):
        restoration = self.restoration_capability()
        self.advance("HEALTHY_STATE_CAPTURED")
        self.transport.response = MutationTransportResult(
            200, None, None, "Success"
        )
        with self.open_session() as session:
            result = session.delete_user_service(restoration)
        self.assertEqual(result.acknowledgement, ObservedEffect.ACKNOWLEDGED_APPLIED)
        self.assertEqual(len(self.transport.calls), 1)
        method, args = self.transport.calls[0]
        self.assertEqual(method, "delete_namespaced_service")
        self.assertEqual(args[:2], ("user-service", "social-network"))
        self.assertEqual(
            args[2]["preconditions"],
            {"uid": "service-uid", "resourceVersion": "123"},
        )
        ledger = (self.attempt / "journal/mutation-events.jsonl").read_bytes()
        self.assertLess(ledger.index(b"INTENT_DURABLE"), ledger.index(b"RECEIPT_DURABLE"))

    def test_replacement_selection_is_lexical_and_delete_is_uid_guarded_once(self):
        target = self.replacement_target()
        self.assertEqual((target.name, target.uid), ("user-service-a", "pod-a"))
        # The replacement deletion belongs to REPLACEMENT_PERSISTENCE_EVALUATION,
        # which the evidence policy authorizes only in CONTRACT_EVALUATED.
        self.advance("CONTRACT_EVALUATED")
        with self.open_session() as session:
            session.delete_replacement_pod(target)
            self.assert_code(
                "DUPLICATE_MUTATION_FORBIDDEN",
                session.delete_replacement_pod,
                target,
            )
        _method, args = self.transport.calls[0]
        self.assertEqual(args[0], "user-service-a")
        self.assertEqual(
            args[2]["preconditions"], {"uid": "pod-a", "resourceVersion": "12"}
        )

    def test_challenge_create_exact_body_and_uid_guarded_delete(self):
        self.advance("ORIGINAL_ORACLE_EVALUATED")
        self.transport.response = MutationTransportResult(
            201, "challenge-uid", "91", "Success"
        )
        with self.open_session() as session:
            created = session.create_challenge_pod()
            target = created.challenge_target
            self.assertIsNotNone(target)
            with Journal(self.attempt, self.policy, RUN_ID, ATTEMPT_ID) as journal:
                journal.append_state_transition(
                    "ORIGINAL_ORACLE_EVALUATED->CONTRACT_EVALUATED",
                    utc_time=UTC, monotonic_ns=self._monotonic(), boot_identity=BOOT,
                    intent_receipt_adjudication_sha256=(
                        created.receipt_reference.descriptor_sha256,
                    ),
                )
                journal.append_state_transition(
                    "CONTRACT_EVALUATED->RESTORE_STARTED",
                    utc_time=UTC, monotonic_ns=self._monotonic(), boot_identity=BOOT,
                )
            self.transport.response = MutationTransportResult(
                200, None, None, "Success"
            )
            session.delete_challenge_pod(target)
        create_call, delete_call = self.transport.calls
        self.assertEqual(create_call[0], "create_namespaced_pod")
        body = create_call[1][1]
        self.assertTrue(body["metadata"]["name"].startswith("sremut-challenge-"))
        container = body["spec"]["containers"][0]
        self.assertEqual(container["image"], self.policy.policy["challenge_request_binding"]["image"])
        self.assertEqual(container["securityContext"]["runAsUser"], 65532)
        self.assertEqual(container["securityContext"]["capabilities"]["drop"], ["ALL"])
        self.assertFalse(body["spec"]["automountServiceAccountToken"])
        self.assertEqual(body["spec"]["activeDeadlineSeconds"], 600)
        self.assertEqual(delete_call[1][2]["preconditions"], {"uid": "challenge-uid"})

    def test_service_restore_uses_exact_capability_body(self):
        restoration = self.restoration_capability()
        self.advance("RESTORE_STARTED")
        self.transport.response = MutationTransportResult(
            201, "new-service-uid", "200", "Success"
        )
        with self.open_session() as session:
            session.restore_user_service(restoration)
        method, args = self.transport.calls[0]
        self.assertEqual(method, "create_namespaced_service")
        self.assertEqual(args[0], "social-network")
        self.assertEqual(args[1], dict(restoration.body))
        self.assertNotIn("clusterIP", args[1]["spec"])
        self.assertEqual(args[1]["spec"]["ports"][0]["targetPort"], 9090)

    def test_api_success_is_not_observed_effect(self):
        restoration = self.restoration_capability()
        self.advance("HEALTHY_STATE_CAPTURED")
        with self.open_session() as session:
            result = session.delete_user_service(restoration)
            receipt = parse_canonical_json(
                session._store.resolve(result.receipt_reference)[1]
            )
        self.assertEqual(
            receipt["observed_effect_classification"], "ACKNOWLEDGED_APPLIED"
        )
        self.assertFalse(
            receipt["effect_directly_acknowledged_or_recovered_from_observation"]
        )


class AdversarialTests(MutationCase):
    def test_wrong_state_and_post_terminal_reject_before_transport(self):
        restoration = self.restoration_capability()
        self.advance("PREFLIGHT_PASS")
        with self.open_session() as session:
            self.assert_code(
                "STATE_OPERATION_FORBIDDEN", session.delete_user_service, restoration
            )
        self.assertEqual(self.transport.calls, [])

    def test_cross_attempt_and_caller_created_targets_reject(self):
        target = self.replacement_target()
        object.__setattr__(target, "attempt_id", "a02")
        self.advance("ORIGINAL_ORACLE_EVALUATED")
        with self.open_session() as session:
            self.assert_code(
                "MUTATION_CAPTURE_INVALID", session.delete_replacement_pod, target
            )

    def test_arbitrary_restoration_body_and_cross_run_capability_reject(self):
        restoration = self.restoration_capability()
        object.__setattr__(restoration, "run_id", "sremut-ms-m01-r01-a02-abcdef123456")
        self.advance("RESTORE_STARTED")
        with self.open_session() as session:
            self.assert_code(
                "RESTORATION_CAPABILITY_INVALID",
                session.restore_user_service,
                restoration,
            )

    def test_challenge_target_is_immutable_and_cannot_be_reconstructed(self):
        self.advance("ORIGINAL_ORACLE_EVALUATED")
        self.transport.response = MutationTransportResult(
            201, "challenge-uid", "91", "Success"
        )
        with self.open_session() as session:
            target = session.create_challenge_pod().challenge_target
            with self.assertRaises(FrozenInstanceError):
                target.uid = "other"
            with self.assertRaises(TypeError):
                type(target)(name=target.name, uid="other")

    def test_timeout_no_retry_and_exception_is_stable(self):
        restoration = self.restoration_capability()
        self.advance("HEALTHY_STATE_CAPTURED")
        self.transport.failure = TimeoutError("secret timeout detail")
        with self.open_session() as session:
            error = self.assert_code(
                "KUBERNETES_MUTATION_TIMEOUT",
                session.delete_user_service,
                restoration,
            )
            self.assertEqual(
                error.result.acknowledgement, ObservedEffect.OUTCOME_UNKNOWN
            )
        self.assertEqual(len(self.transport.calls), 1)
        ledger = (self.attempt / "journal/mutation-events.jsonl").read_bytes()
        self.assertIn(b"RECEIPT_DURABLE", ledger)

    def test_sensitive_status_reason_is_rejected_without_echo(self):
        restoration = self.restoration_capability()
        self.advance("HEALTHY_STATE_CAPTURED")
        self.transport.response = MutationTransportResult(
            403, None, None, "Failure",
            "Authorization Bearer abcdefghijklmnopqrstuvwxyz",
        )
        with self.open_session() as session:
            self.assert_code(
                "SENSITIVE_CAPTURE_REJECTED",
                session.delete_user_service,
                restoration,
            )

    def test_symlink_attempt_root_and_missing_host_lock_reject(self):
        outside = self.base / "outside"
        outside.mkdir()
        linked = self.base / "linked"
        linked.symlink_to(outside, target_is_directory=True)
        binding = self.policy.policy["bindings"]["execution_profile"]
        kwargs = {
            "policy": self.policy,
            "execution_profile_tag_object": binding["tag_object"],
            "execution_profile_sha256": binding["sha256"],
            "run_id": RUN_ID, "attempt_id": ATTEMPT_ID, "mutant_id": "MS-M01",
            "repetition": 1, "attempt_number": 1,
            "runner_bundle": self.runner, "host_lock_root": self.host,
            "attempt_root": linked, "transport": self.transport,
            "read_session": self.read_client, "timeout_seconds": 7,
            "boot_identity": BOOT,
        }
        self.assert_code("EVIDENCE_SYMLINK_REFUSED", GuardedMutationSession.open, **kwargs)


class ReconciliationTests(MutationCase):
    def test_process_crash_leaves_reconcilable_intent_without_retry(self):
        restoration = self.restoration_capability()
        self.advance("HEALTHY_STATE_CAPTURED")
        self.transport.failure = SystemExit(17)
        with self.assertRaises(SystemExit):
            with self.open_session() as session:
                session.delete_user_service(restoration)
        ledger = (self.attempt / "journal/mutation-events.jsonl").read_bytes()
        self.assertIn(b"INTENT_DURABLE", ledger)
        self.assertNotIn(b"RECEIPT_DURABLE", ledger)
        self.transport.failure = None
        # A Boolean-only observation is no longer enough to close a durable
        # intent: the recovery receipt has to name the evidence it was drawn
        # from, so the observation carries authenticated retained references.
        observed = self.read_client.get_user_service(
            consumer=KubernetesConsumer.MUTATION_CONTROLLER
        )
        request_ref, response_ref, _descriptor = self.publish_capture(
            observed, capture_state="MUTANT_INJECTED"
        )
        with self.open_session() as session:
            self.assert_code(
                "RECONCILIATION_OBSERVATION_EVIDENCE_MISSING",
                session.reconcile_pending,
                "INITIAL_USER_SERVICE_DELETION:01",
                "INITIAL_USER_SERVICE_DELETION",
                "service-uid",
                MutationObservation(True, False, None),
            )
            recovered = session.reconcile_pending(
                "INITIAL_USER_SERVICE_DELETION:01",
                "INITIAL_USER_SERVICE_DELETION",
                "service-uid",
                MutationObservation(
                    True, False, None,
                    request_reference=request_ref,
                    projection_reference=response_ref,
                ),
            )
        self.assertEqual(
            recovered.classification,
            ObservedEffect.OBSERVED_APPLIED_AFTER_RECOVERY,
        )
        self.assertFalse(recovered.retry_permitted)
        # The transport was never touched again.
        self.assertEqual(len(self.transport.calls), 1)
        # And the same operation is now a complete intent/receipt pair.
        rows = validate_mutation_ledger_bytes(
            (self.attempt / MUTATION_LEDGER).read_bytes()
        )
        events = [
            row["event"]
            for row in rows
            if row["operation_id"] == "INITIAL_USER_SERVICE_DELETION:01"
        ]
        self.assertEqual(events, ["INTENT_DURABLE", "RECEIPT_DURABLE"])
        digest = [
            row["descriptor_sha256"]
            for row in rows
            if row["event"] == "RECEIPT_DURABLE"
        ][0]
        receipt = parse_canonical_json(
            (
                self.attempt / f"descriptors/sha256/{digest[:2]}/{digest}.json"
            ).read_bytes()
        )
        self.assertEqual(receipt["operation_id"], "INITIAL_USER_SERVICE_DELETION:01")
        self.assertEqual(receipt["status"], "RECEIPT_DURABLE")
        self.assertEqual(
            receipt["observed_effect_classification"],
            "OBSERVED_APPLIED_AFTER_RECOVERY",
        )
        self.assertIs(
            receipt["effect_directly_acknowledged_or_recovered_from_observation"], True
        )


    def test_delete_reconciliation_matrix(self):
        self.advance("HEALTHY_STATE_CAPTURED")
        with self.open_session() as session:
            cases = (
                (MutationObservation(False, False, None), ObservedEffect.OUTCOME_UNKNOWN, False),
                (MutationObservation(True, False, None), ObservedEffect.OBSERVED_APPLIED_AFTER_RECOVERY, False),
                (MutationObservation(True, True, "old"), ObservedEffect.OBSERVED_NOT_APPLIED_AFTER_RECOVERY, True),
                (MutationObservation(True, True, "new"), ObservedEffect.OBSERVED_APPLIED_AFTER_RECOVERY, False),
            )
            for observation, expected, retry in cases:
                with self.subTest(observation=observation):
                    result = session.reconcile(
                        "INITIAL_USER_SERVICE_DELETION", "old", observation
                    )
                    self.assertEqual(result.classification, expected)
                    self.assertEqual(result.retry_permitted, retry)

    def test_restore_reconciliation_conflict_and_adoption(self):
        self.advance("RESTORE_STARTED")
        with self.open_session() as session:
            self.assertEqual(
                session.reconcile(
                    "RESTORED_SERVICE_CREATION", None,
                    MutationObservation(True, True, "new", True),
                ).classification,
                ObservedEffect.OBSERVED_APPLIED_AFTER_RECOVERY,
            )
            self.assertEqual(
                session.reconcile(
                    "RESTORED_SERVICE_CREATION", None,
                    MutationObservation(True, True, "other", False),
                ).classification,
                ObservedEffect.CONFLICTING_STATE,
            )



class MutationStrengthTests(MutationCase):
    def test_sixteen_isolated_mutations_are_detected(self):
        source_path = ROOT / "src/sremut/kubernetes_mutation.py"
        source = source_path.read_text(encoding="utf-8")

        def guards(value):
            return (
                '"uid": restoration.source_uid' in value,
                value.index("operation_id, intent = self._publish_intent")
                < value.index("response = function(*arguments"),
                'self._append_ledger("INTENT_DURABLE"' in value,
                value.count("target_name=SERVICE_NAME") == 2,
                "isinstance(target, ReplacementPodTarget)" in value,
                "isinstance(restoration, RestorationBodyCapability)" in value,
                '"effect_directly_acknowledged_or_recovered_from_observation": False'
                in value,
                "response.http_status == 404" not in value,
                "while False:" not in value,
                '"DUPLICATE_MUTATION_FORBIDDEN"' in value,
                "host_root.lock(blocking=False)" in value,
                'state.terminal or self._root.exists("manifests/terminal.sha256")'
                in value,
                "getattr(self._transport" not in value and "call_api" not in value,
                '"runAsNonRoot": security["run_as_non_root"]' in value
                and '"capabilities": {"drop":' in value,
                value.index("receipt = self._publish_receipt")
                > value.index("response = function(*arguments"),
                value.count("target.attempt_id != self._attempt_id") == 2,
            )

        mutations = (
            ('"uid": restoration.source_uid', '"uid": None'),
            ("operation_id, intent = self._publish_intent", "response = function(*arguments\n        operation_id, intent = self._publish_intent"),
            ('self._append_ledger("INTENT_DURABLE"', 'self._append_ledger("INTENT_SKIPPED"'),
            ("target_name=SERVICE_NAME", 'target_name="arbitrary"'),
            ("isinstance(target, ReplacementPodTarget)", "True"),
            ("isinstance(restoration, RestorationBodyCapability)", "True"),
            ('"effect_directly_acknowledged_or_recovered_from_observation": False', '"effect_directly_acknowledged_or_recovered_from_observation": True'),
            ("failure_code = None", "failure_code = None\n        response.http_status == 404"),
            ("failure_code = None", "failure_code = None\n        while False:\n            pass"),
            ('"DUPLICATE_MUTATION_FORBIDDEN"', '"DUPLICATE_MUTATION_ALLOWED"'),
            ("host_root.lock(blocking=False)", "host_root.lock(blocking=True)"),
            ('state.terminal or self._root.exists("manifests/terminal.sha256")', "False"),
            ("failure_code = None", "failure_code = None\n        getattr(self._transport, method_name)"),
            ('"runAsNonRoot": security["run_as_non_root"]', '"runAsNonRoot": False'),
            ("failure_code = None", "receipt = self._publish_receipt\n        failure_code = None"),
            ("target.attempt_id != self._attempt_id", "False"),
        )
        original = guards(source)
        self.assertTrue(all(original))
        for index, (old, new) in enumerate(mutations):
            mutated = source.replace(old, new, 1)
            path = self.base / f"mutation-{index:02d}.py"
            path.write_text(mutated, encoding="utf-8")
            observed = guards(path.read_text(encoding="utf-8"))
            with self.subTest(mutation=index + 1):
                self.assertFalse(observed[index])


class ReplacementDeletionStateTests(MutationCase):
    """The replacement deletion is dispatchable exactly in CONTRACT_EVALUATED."""

    def test_table_pins_the_single_authorized_state(self):
        self.assertEqual(
            tuple(OPERATION_TABLE["REPLACEMENT_POD_DELETION"]["states"]),
            ("CONTRACT_EVALUATED",),
        )
        # The challenge pod is unchanged: it is created before the contract state.
        self.assertEqual(
            tuple(OPERATION_TABLE["CHALLENGE_POD_CREATION"]["states"]),
            ("ORIGINAL_ORACLE_EVALUATED",),
        )

    def _rejects_in(self, state):
        target = self.replacement_target()
        self.advance(state)
        with self.open_session() as session:
            self.assert_code(
                "STATE_OPERATION_FORBIDDEN", session.delete_replacement_pod, target
            )
        self.assertEqual(self.transport.calls, [])

    def test_replacement_deletion_rejects_in_healthy_state_captured(self):
        self._rejects_in("HEALTHY_STATE_CAPTURED")

    def test_replacement_deletion_rejects_in_original_oracle_evaluated(self):
        self._rejects_in("ORIGINAL_ORACLE_EVALUATED")

    def test_replacement_deletion_succeeds_in_contract_evaluated(self):
        target = self.replacement_target()
        self.advance("CONTRACT_EVALUATED")
        with self.open_session() as session:
            dispatch = session.delete_replacement_pod(target)
        self.assertEqual(dispatch.operation_kind, "REPLACEMENT_POD_DELETION")
        self.assertEqual(
            [method for method, _args in self.transport.calls],
            ["delete_namespaced_pod"],
        )


class MutationLedgerValidatorTests(MutationCase):
    """The one strict ledger reader shared by every consumer."""

    def _ledger(self) -> bytes:
        restoration = self.restoration_capability()
        self.advance("HEALTHY_STATE_CAPTURED")
        with self.open_session() as session:
            session.delete_user_service(restoration)
        return (self.attempt / MUTATION_LEDGER).read_bytes()

    def test_a_real_ledger_validates_and_reports_its_rows(self):
        rows = validate_mutation_ledger_bytes(self._ledger())
        self.assertEqual(
            [row["event"] for row in rows], list(MUTATION_LEDGER_EVENTS)
        )
        self.assertEqual([row["sequence"] for row in rows], [0, 1])
        self.assertEqual(len({row["operation_id"] for row in rows}), 1)

    def test_empty_ledger_is_empty_not_an_error(self):
        self.assertEqual(validate_mutation_ledger_bytes(b""), ())

    def test_tampered_and_malformed_ledgers_reject(self):
        data = self._ledger()
        lines = data.splitlines(keepends=True)
        cases = {
            "missing final newline": data[:-1],
            "not a ledger row": b"{}\n",
            "truncated chain": lines[1],
            "reordered rows": lines[1] + lines[0],
            "duplicated row": data + lines[-1],
            "renumbered sequence": data.replace(b'"sequence":1', b'"sequence":2', 1),
            "unknown event": data.replace(b"RECEIPT_DURABLE", b"RECEIPT_MAYBE", 1),
        }
        # A record hash that no longer covers its own row.
        first = dict(validate_mutation_ledger_bytes(data)[0])
        digest = first["record_sha256"]
        first["record_sha256"] = ("0" if digest[0] != "0" else "1") + digest[1:]
        cases["flipped record hash"] = canonical_json_line(first) + lines[1]
        # A payload field inside the hashed material, changed after the fact.
        second = dict(validate_mutation_ledger_bytes(data)[0])
        second["descriptor_sha256"] = "f" * 64
        cases["tampered descriptor hash"] = canonical_json_line(second) + lines[1]
        for label, payload in cases.items():
            with self.subTest(case=label):
                self.assert_code(
                    "MUTATION_LEDGER_INVALID",
                    validate_mutation_ledger_bytes,
                    payload,
                )

    def test_receipt_without_intent_rejects(self):
        """Structurally perfect bytes whose event structure is impossible."""
        rows = validate_mutation_ledger_bytes(self._ledger())
        row = dict(rows[1])
        row["sequence"] = 0
        row["previous_sha256"] = "0" * 64
        material = {key: value for key, value in row.items() if key != "record_sha256"}
        row["record_sha256"] = hashlib.sha256(
            canonical_json_bytes(material)
        ).hexdigest()
        self.assert_code(
            "MUTATION_LEDGER_INVALID",
            validate_mutation_ledger_bytes,
            canonical_json_line(row),
        )


if __name__ == "__main__":
    unittest.main()

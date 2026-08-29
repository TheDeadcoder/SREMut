"""Offline tests for frozen Service-restoration derivation and validation."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
import unittest

from sremut.canonical_json import canonical_json_bytes, parse_canonical_json
from sremut.policy_runtime import POLICY_MANIFEST_SHA256, load_policy_bundle
from sremut.service_restoration import (
    SERVICE_RESTORATION_BODY,
    SERVICE_RESTORATION_SOURCE,
    ServiceRestorationError,
    _derive_document,
    _validate_intent,
    _validate_receipt,
    _validate_document,
    derive_service_restoration_body,
    derive_service_restoration_body_preseal,
    validate_resolved_service_restoration,
)


ROOT = Path(__file__).resolve().parents[1]
CREATED = "2026-08-20T10:00:00.123456789Z"


def load_policy():
    return load_policy_bundle(
        ROOT / "policies/missing_service_social_network/evidence-capture-v1.1.yaml",
        ROOT / "schemas/evidence-capture-policy-v1.1.schema.json",
        ROOT / "EVIDENCE_CAPTURE_POLICY_V1_1_SHA256SUMS",
        expected_manifest_sha256=POLICY_MANIFEST_SHA256,
    )


def request_reference():
    digest = "1" * 64
    return {
        "document_type": "DESCRIPTOR_EVIDENCE_REF_V1", "schema_version": 1,
        "evidence_id": "ev-" + digest[:32], "role": "kubernetes_request_identity",
        "producer": "RUNNER_KUBERNETES_CLIENT", "source_kind": "KUBERNETES",
        "media_type": "application/json", "storage_class": "DESCRIPTOR_ONLY",
        "descriptor_sha256": digest, "descriptor_size_bytes": 1,
        "descriptor_relative_path": f"descriptors/sha256/{digest[:2]}/{digest}.json",
        "redaction_status": "NOT_REDACTED",
    }


def payload_reference(projection_class, digest):
    payload = "a" * 64
    return {
        "document_type": "PAYLOAD_EVIDENCE_REF_V1", "schema_version": 1,
        "evidence_id": "ev-" + digest[:32], "role": "kubernetes_object_projection",
        "producer": "RUNNER_KUBERNETES_CLIENT", "source_kind": "KUBERNETES",
        "media_type": "application/json", "storage_class": "PAYLOAD_WITH_DESCRIPTOR",
        "descriptor_sha256": digest, "descriptor_size_bytes": 1,
        "descriptor_relative_path": f"descriptors/sha256/{digest[:2]}/{digest}.json",
        "redaction_status": "NOT_REDACTED", "payload_sha256": payload,
        "payload_size_bytes": 1, "payload_relative_path": f"objects/sha256/{payload[:2]}/{payload}",
        "projection_class": projection_class,
    }


def service_source():
    return {
        "apiVersion": "v1", "kind": "Service",
        "metadata": {
            "name": "user-service", "namespace": "social-network", "uid": "service-uid",
            "resourceVersion": "123", "creationTimestamp": "2026-08-20T00:00:00Z",
            "generation": 4, "managedFields": [{"manager": "controller"}],
            "labels": {"service": "user-service"},
            "annotations": {"meta.helm.sh/release-name": "social-network"},
        },
        "spec": {
            "type": "ClusterIP", "clusterIP": "10.96.0.50", "clusterIPs": ["10.96.0.50"],
            "selector": {"service": "user-service"},
            "ports": [{"name": "grpc", "protocol": "TCP", "port": 9090, "targetPort": 9090, "nodePort": 30090}],
        },
        "status": {"loadBalancer": {}},
    }


@dataclass(frozen=True)
class FakeReference:
    value: dict

    def as_dict(self):
        return deepcopy(self.value)

    def __getattr__(self, name):
        return self.value.get(name)


class FakeContext:
    def __init__(self, policy, source, document):
        self._policy = policy
        self.run_id = "sremut-ms-m01-r01-a01-abcdef123456"
        self.attempt_id = "a01"
        source_value = payload_reference(SERVICE_RESTORATION_SOURCE, "2" * 64)
        source_bytes = canonical_json_bytes(source)
        source_value.update({
            "payload_sha256": hashlib.sha256(source_bytes).hexdigest(),
            "payload_size_bytes": len(source_bytes),
            "payload_relative_path": "objects/sha256/" + hashlib.sha256(source_bytes).hexdigest()[:2] + "/" + hashlib.sha256(source_bytes).hexdigest(),
        })
        body_bytes = canonical_json_bytes(document)
        body_value = payload_reference(SERVICE_RESTORATION_BODY, "3" * 64)
        body_value.update({
            "payload_sha256": hashlib.sha256(body_bytes).hexdigest(),
            "payload_size_bytes": len(body_bytes),
            "payload_relative_path": "objects/sha256/" + hashlib.sha256(body_bytes).hexdigest()[:2] + "/" + hashlib.sha256(body_bytes).hexdigest(),
        })
        document["source_service_reference"] = deepcopy(source_value)
        body_bytes = canonical_json_bytes(document)
        body_value["payload_sha256"] = hashlib.sha256(body_bytes).hexdigest()
        body_value["payload_size_bytes"] = len(body_bytes)
        body_value["payload_relative_path"] = "objects/sha256/" + body_value["payload_sha256"][:2] + "/" + body_value["payload_sha256"]
        source_row = SimpleNamespace(
            reference=FakeReference(source_value), payload_bytes=source_bytes,
            descriptor=MappingProxyType({"projection_schema_id": SERVICE_RESTORATION_SOURCE}),
            publication=SimpleNamespace(sequence_number=1),
        )
        body_row = SimpleNamespace(
            reference=FakeReference(body_value), payload_bytes=body_bytes,
            descriptor=MappingProxyType({"projection_schema_id": SERVICE_RESTORATION_BODY}),
            publication=SimpleNamespace(sequence_number=2),
        )
        self.evidence = {source_value["evidence_id"]: source_row, body_value["evidence_id"]: body_row}
        self.source_reference = source_value
        self.body_reference = body_value
        self.journal_records = ()

    def add_descriptor(self, role, candidate, sequence, digest):
        value = request_reference()
        value.update({
            "evidence_id": "ev-" + digest[:32],
            "role": role,
            "descriptor_sha256": digest,
            "descriptor_relative_path": f"descriptors/sha256/{digest[:2]}/{digest}.json",
        })
        row = SimpleNamespace(
            reference=FakeReference(value), payload_bytes=None,
            descriptor=MappingProxyType(deepcopy(candidate)),
            publication=SimpleNamespace(sequence_number=sequence),
        )
        self.evidence[value["evidence_id"]] = row
        return row

    def resolve_reference(self, reference):
        key = reference.value["evidence_id"] if isinstance(reference, FakeReference) else reference["evidence_id"]
        if key not in self.evidence:
            raise ValueError("missing")
        row = self.evidence[key]
        supplied = reference.as_dict() if isinstance(reference, FakeReference) else dict(reference)
        if supplied != row.reference.as_dict():
            raise ValueError("mismatch")
        return row

    def _candidate_evidence(self, candidate):
        for row in self.evidence.values():
            if row.payload_bytes is not None and row.payload_bytes == canonical_json_bytes(candidate):
                return row
            if row.payload_bytes is None and dict(row.descriptor) == candidate:
                return row
        raise ValueError("missing")


class ServiceRestorationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.policy = load_policy()

    def document(self):
        return _derive_document(
            self.policy,
            service_source(),
            request_reference(),
            CREATED,
            payload_reference(SERVICE_RESTORATION_SOURCE, "2" * 64),
        )

    def test_exact_thirteen_stripped_paths_and_semantic_preservation(self):
        document = self.document()
        body = document["normalized_service_create_body"]
        self.assertEqual(len(document["exact_stripped_field_token_paths"]), 13)
        self.assertNotIn("uid", body["metadata"])
        self.assertNotIn("resourceVersion", body["metadata"])
        self.assertNotIn("clusterIP", body["spec"])
        self.assertNotIn("nodePort", body["spec"]["ports"][0])
        self.assertEqual(body["spec"]["selector"], {"service": "user-service"})
        self.assertEqual(body["spec"]["ports"][0]["targetPort"], 9090)
        self.policy.structural_validate(document)

    def test_repeated_derivation_is_byte_identical(self):
        self.assertEqual(canonical_json_bytes(self.document()), canonical_json_bytes(self.document()))

    def test_public_derivation_rejects_arbitrary_mapping(self):
        with self.assertRaises(ServiceRestorationError) as caught:
            derive_service_restoration_body({}, {}, request_reference(), CREATED)
        self.assertEqual(caught.exception.code, "RESTORATION_SOURCE_UNRESOLVED")

    def test_resolved_body_matches_authenticated_source(self):
        document = self.document()
        context = FakeContext(self.policy, service_source(), document)
        document["source_service_reference"] = deepcopy(context.source_reference)
        context = FakeContext(self.policy, service_source(), document)
        document["source_service_reference"] = deepcopy(context.source_reference)
        validate_resolved_service_restoration(context, document)

    def assert_invalid_change(self, mutator, code="RESTORATION_DERIVATION_MISMATCH"):
        document = self.document()
        context = FakeContext(self.policy, service_source(), document)
        candidate = deepcopy(document)
        candidate["source_service_reference"] = deepcopy(context.source_reference)
        mutator(candidate)
        with self.assertRaises(ServiceRestorationError) as caught:
            _validate_document(context, candidate)
        self.assertEqual(caught.exception.code, code)

    def test_selector_port_protocol_and_target_port_changes_reject(self):
        changes = (
            lambda value: value["normalized_service_create_body"]["spec"]["selector"].update({"service": "other"}),
            lambda value: value["normalized_service_create_body"]["spec"]["ports"][0].update({"port": 8080}),
            lambda value: value["normalized_service_create_body"]["spec"]["ports"][0].update({"protocol": "UDP"}),
            lambda value: value["normalized_service_create_body"]["spec"]["ports"][0].update({"targetPort": 8080}),
        )
        for change in changes:
            with self.subTest(change=change):
                self.assert_invalid_change(change, "RESTORATION_BODY_HASH_MISMATCH")

    def test_rehashed_semantic_substitution_still_rejects_source_derivation(self):
        changes = (
            lambda value: value["normalized_service_create_body"]["spec"]["selector"].update({"service": "other"}),
            lambda value: value["normalized_service_create_body"]["spec"]["ports"][0].update({"targetPort": 8080}),
            lambda value: value["normalized_service_create_body"]["spec"].update({"clusterIP": "10.0.0.1"}),
            lambda value: value["normalized_service_create_body"]["spec"].pop("selector"),
        )
        for change in changes:
            document = self.document()
            context = FakeContext(self.policy, service_source(), document)
            candidate = deepcopy(document)
            candidate["source_service_reference"] = deepcopy(context.source_reference)
            change(candidate)
            digest = hashlib.sha256(canonical_json_bytes(candidate["normalized_service_create_body"])).hexdigest()
            candidate["normalized_body_sha256"] = digest
            candidate["canonical_json_identity"] = digest
            with self.subTest(change=change):
                with self.assertRaises(ServiceRestorationError) as caught:
                    _validate_document(context, candidate)
                self.assertEqual(caught.exception.code, "RESTORATION_DERIVATION_MISMATCH")

    def test_under_strip_over_strip_and_cluster_ip_reject(self):
        self.assert_invalid_change(lambda value: value["normalized_service_create_body"]["spec"].update({"clusterIP": "10.0.0.1"}), "RESTORATION_BODY_HASH_MISMATCH")
        self.assert_invalid_change(lambda value: value["normalized_service_create_body"]["spec"].pop("selector"), "RESTORATION_BODY_HASH_MISMATCH")
        self.assert_invalid_change(lambda value: value["exact_stripped_field_token_paths"].pop(), "SERVICE_RESTORATION_BODY_INVALID")

    def test_wrong_type_meta_name_namespace_and_sensitive_material_reject(self):
        for change in (
            lambda value: value["normalized_service_create_body"].update({"kind": "Secret"}),
            lambda value: value["normalized_service_create_body"]["metadata"].update({"name": "other"}),
            lambda value: value["normalized_service_create_body"]["metadata"].update({"namespace": "other"}),
        ):
            with self.subTest(change=change):
                self.assert_invalid_change(change, "SERVICE_RESTORATION_BODY_INVALID")
        document = self.document()
        context = FakeContext(self.policy, service_source(), document)
        candidate = deepcopy(document)
        candidate["source_service_reference"] = deepcopy(context.source_reference)
        candidate["normalized_service_create_body"]["metadata"].setdefault("annotations", {})["authorization"] = "Bearer abcdefghijklmnopqrstuvwxyz"
        digest = hashlib.sha256(canonical_json_bytes(candidate["normalized_service_create_body"])).hexdigest()
        candidate["normalized_body_sha256"] = digest
        candidate["canonical_json_identity"] = digest
        with self.assertRaises(ServiceRestorationError) as caught:
            _validate_document(context, candidate)
        self.assertEqual(caught.exception.code, "SENSITIVE_CAPTURE_REJECTED")


    def test_service_intent_binds_exact_body_uid_version_and_publication(self):
        document = self.document()
        context = FakeContext(self.policy, service_source(), document)
        candidate = {
            "operation_kind": "RESTORED_SERVICE_CREATION",
            "resource_kind": "Service",
            "namespace": "social-network",
            "object_name": "user-service",
            "service_restoration_body_reference": deepcopy(context.body_reference),
            "service_restoration_body_sha256": context.body_reference["payload_sha256"],
            "expected_uid_when_existing": "service-uid",
            "expected_resource_version_when_applicable": "123",
        }
        context.add_descriptor("mutation_intent", candidate, 3, "4" * 64)
        _validate_intent(context, candidate)
        for field, value in (
            ("service_restoration_body_sha256", "f" * 64),
            ("expected_uid_when_existing", "other"),
            ("expected_resource_version_when_applicable", "other"),
        ):
            changed = deepcopy(candidate)
            changed[field] = value
            context.add_descriptor("mutation_intent", changed, 3, "5" * 64)
            with self.subTest(field=field), self.assertRaises(ServiceRestorationError):
                _validate_intent(context, changed)

    def test_restoration_receipt_was_published_before_terminal_transition(self):
        document = self.document()
        context = FakeContext(self.policy, service_source(), document)
        request = {
            "canonical_request": {
                "operation": "CREATE", "resource": "services",
                "name": "user-service", "namespace": "social-network",
            }
        }
        request_row = context.add_descriptor("kubernetes_request_identity", request, 4, "6" * 64)
        observation_value = payload_reference(SERVICE_RESTORATION_SOURCE, "8" * 64)
        observation_bytes = canonical_json_bytes(service_source())
        observation_value.update({
            "payload_sha256": hashlib.sha256(observation_bytes).hexdigest(),
            "payload_size_bytes": len(observation_bytes),
            "payload_relative_path": "objects/sha256/" + hashlib.sha256(observation_bytes).hexdigest()[:2] + "/" + hashlib.sha256(observation_bytes).hexdigest(),
        })
        context.evidence[observation_value["evidence_id"]] = SimpleNamespace(
            reference=FakeReference(observation_value), payload_bytes=observation_bytes,
            descriptor=MappingProxyType({"projection_schema_id": SERVICE_RESTORATION_SOURCE}),
            publication=SimpleNamespace(sequence_number=6),
        )
        receipt = {
            "run_id": context.run_id,
            "attempt_id": context.attempt_id,
            "service_restoration_body_reference": deepcopy(context.body_reference),
            "service_restoration_body_sha256": context.body_reference["payload_sha256"],
            "operation_request_reference": request_row.reference.as_dict(),
            "post_create_observation_reference": deepcopy(observation_value),
        }
        context.add_descriptor("mutation_receipt", receipt, 7, "7" * 64)
        operation = canonical_json_bytes({
            "request_reference": request_row.reference.as_dict(),
            "request_body_reference": deepcopy(context.body_reference),
        }).hex()
        context.journal_records = (
            {"sequence_number": 5, "transition": "OPERATION_AUTHORIZED:" + operation},
            {"sequence_number": 8, "transition": "RESTORE_VERIFIED->FINALIZED"},
        )
        _validate_receipt(context, receipt)
        context.journal_records = (
            {"sequence_number": 5, "transition": "OPERATION_AUTHORIZED:" + operation},
            {"sequence_number": 7, "transition": "RESTORE_VERIFIED->FINALIZED"},
        )
        with self.assertRaises(ServiceRestorationError) as caught:
            _validate_receipt(context, receipt)
        self.assertEqual(caught.exception.code, "MUTATION_RECEIPT_PUBLICATION_INVALID")


if __name__ == "__main__":
    unittest.main()


class PreSealDerivationTests(unittest.TestCase):
    """The pre-seal counterpart of `derive_service_restoration_body`.

    The sealed derivation needs a `ResolvedEvidenceContext`, which cannot exist
    until the terminal manifest is written -- but the live lifecycle needs the
    restoration body BEFORE the Service is deleted.  These tests pin that the
    pre-seal API takes its authority from bytes the store already retained, and
    never from a caller-supplied Service mapping.
    """

    @classmethod
    def setUpClass(cls):
        cls.policy = load_policy()

    def setUp(self):
        import tempfile

        import tests.test_kubernetes_mutation as mutation_tests

        from sremut.evidence import EvidenceStore
        from sremut.kubernetes_readonly import KubernetesConsumer, ReadOnlyKubernetesClient

        self.mutation_tests = mutation_tests
        self.EvidenceStore = EvidenceStore
        self.temporary = tempfile.TemporaryDirectory(prefix="sremut-preseal-test-")
        self.root = Path(self.temporary.name) / "attempt"
        self.root.mkdir(mode=0o700)
        self.run_id = mutation_tests.RUN_ID
        self.attempt_id = mutation_tests.ATTEMPT_ID
        client = ReadOnlyKubernetesClient(
            policy=self.policy,
            transport=mutation_tests.FakeReadTransport(),
            context="kind-kind",
            namespace="social-network",
            timeout_seconds=7,
            run_id=self.run_id,
            attempt_id=self.attempt_id,
        )
        capture = client.get_user_service(consumer=KubernetesConsumer.MUTATION_CONTROLLER)
        common = {
            "run_id": self.run_id,
            "attempt_id": self.attempt_id,
            "created_utc": mutation_tests.UTC,
            "monotonic_ns": 1,
            "boot_identity": mutation_tests.BOOT,
        }
        self.store = EvidenceStore(self.root, self.policy, self.run_id, self.attempt_id)
        self.request = self.store.publish_descriptor(
            capture.request_evidence.role,
            capture.request_evidence.publication_metadata(**common),
        )
        request_bytes = self.store.resolve(self.request)[1]
        metadata = dict(
            capture.response_evidence.publication_metadata(
                **{**common, "monotonic_ns": 2},
                request_identity_reference=self.request,
                request_identity_descriptor_bytes=request_bytes,
            )
        )
        metadata.update(
            {
                "projection_class": SERVICE_RESTORATION_SOURCE,
                "projection_schema_id": SERVICE_RESTORATION_SOURCE,
                "capture_state": "HEALTHY_STATE_CAPTURED",
            }
        )
        self.source = self.store.publish_payload(
            capture.response_evidence.role,
            capture.response_evidence.payload_bytes,
            metadata,
        )

    def tearDown(self):
        self.store.close()
        self.temporary.cleanup()

    def assert_code(self, code, *args):
        with self.assertRaises(Exception) as caught:
            derive_service_restoration_body_preseal(*args)
        self.assertEqual(str(caught.exception), code)
        return caught.exception

    def _second_request(self, monotonic_ns=97):
        """Another VALID, same-run, same-role request identity descriptor."""
        from sremut.kubernetes_readonly import KubernetesConsumer, ReadOnlyKubernetesClient

        mutation_tests = self.mutation_tests
        client = ReadOnlyKubernetesClient(
            policy=self.policy,
            transport=mutation_tests.FakeReadTransport(),
            context="kind-kind",
            namespace="social-network",
            timeout_seconds=7,
            run_id=self.run_id,
            attempt_id=self.attempt_id,
        )
        capture = client.get_user_service(consumer=KubernetesConsumer.MUTATION_CONTROLLER)
        return self.store.publish_descriptor(
            capture.request_evidence.role,
            capture.request_evidence.publication_metadata(
                run_id=self.run_id,
                attempt_id=self.attempt_id,
                created_utc=mutation_tests.UTC,
                monotonic_ns=monotonic_ns,
                boot_identity=mutation_tests.BOOT,
            ),
        )

    def test_request_must_be_the_one_the_projection_cites(self):
        """A different valid request cannot be attributed to this capture.

        The Service projection was produced by exactly one request, and its
        retained descriptor records which.  A second request descriptor is
        equally well formed -- same run, same attempt, same role, resolvable --
        and is still refused, because it did not produce this projection.
        """
        other = self._second_request()
        self.assertNotEqual(other.evidence_id, self.request.evidence_id)
        cited = parse_canonical_json(
            self.store.resolve(self.source)[1])["request_identity_reference"]
        self.assertEqual(cited["evidence_id"], self.request.evidence_id)
        self.assert_code(
            "KUBERNETES_PROJECTION_REQUEST_MISMATCH",
            self.store, self.source, other, CREATED,
        )

    def test_the_cited_request_still_derives(self):
        """The correlation check does not disturb the legitimate path."""
        self._second_request()
        body = derive_service_restoration_body_preseal(
            self.store, self.source, self.request, CREATED
        )
        self.assertEqual(body["document_type"], SERVICE_RESTORATION_BODY)
        self.assertEqual(
            body["source_request_reference"]["evidence_id"], self.request.evidence_id)

    def test_derives_a_valid_body_from_retained_bytes(self):
        body = derive_service_restoration_body_preseal(
            self.store, self.source, self.request, CREATED
        )
        self.assertEqual(body["document_type"], SERVICE_RESTORATION_BODY)
        self.assertEqual(body["projection_class"], SERVICE_RESTORATION_BODY)
        self.assertEqual(body["canonical_json_identity"], body["normalized_body_sha256"])
        self.assertEqual(
            sorted(body["normalized_service_create_body"]),
            ["apiVersion", "kind", "metadata", "spec"],
        )
        self.assertTrue(body["exact_stripped_field_token_paths"])

    def test_matches_the_private_derivation_byte_for_byte(self):
        from sremut.canonical_json import parse_canonical_json

        payload = self.store.resolve(self.source)[2]
        expected = _derive_document(
            self.policy,
            parse_canonical_json(payload),
            self.request.as_dict(),
            CREATED,
            self.source.as_dict(),
        )
        actual = derive_service_restoration_body_preseal(
            self.store, self.source, self.request, CREATED
        )
        from sremut.service_restoration import _thaw

        self.assertEqual(
            canonical_json_bytes(_thaw(actual)), canonical_json_bytes(_thaw(expected))
        )

    def test_caller_supplied_service_mapping_is_never_authority(self):
        self.assert_code(
            "RESTORATION_SOURCE_UNRESOLVED",
            self.mutation_tests.service(), self.source, self.request, CREATED,
        )
        self.assert_code("RESTORATION_SOURCE_UNRESOLVED", None, self.source, self.request, CREATED)
        self.assert_code("RESTORATION_SOURCE_UNRESOLVED", {}, self.source, self.request, CREATED)

    def test_wrong_role_and_projection_class_reject(self):
        self.assert_code(
            "RESTORATION_SOURCE_CLASS_INVALID",
            self.store, self.request, self.request, CREATED,
        )
        self.assert_code(
            "RESTORATION_SOURCE_REFERENCE_MISSING",
            self.store, self.source, self.source, CREATED,
        )

    def test_wrong_run_and_attempt_reject(self):
        for run_id, attempt_id in (
            ("sremut-ms-m01-r02-a01-abcdef123456", "a01"),
            (self.run_id, "a02"),
        ):
            other = Path(self.temporary.name) / f"other-{run_id[-6:]}-{attempt_id}"
            other.mkdir(mode=0o700)
            with self.EvidenceStore(other, self.policy, run_id, attempt_id) as store:
                self.assert_code(
                    "RESTORATION_SOURCE_UNRESOLVED",
                    store, self.source, self.request, CREATED,
                )

    def test_unresolved_reference_rejects(self):
        missing = dict(self.source.as_dict())
        missing["descriptor_relative_path"] = "descriptors/sha256/ff/" + "f" * 64 + ".json"
        self.assert_code(
            "RESTORATION_SOURCE_UNRESOLVED", self.store, missing, self.request, CREATED
        )

    def test_tampered_projection_hash_rejects(self):
        forged = dict(self.source.as_dict())
        forged["payload_sha256"] = "0" * 64
        self.assert_code(
            "RESTORATION_SOURCE_UNRESOLVED", self.store, forged, self.request, CREATED
        )

    def test_foreign_policy_rejects(self):
        """A store bound to a different authenticated policy is not authority."""
        from sremut.policy_runtime import (
            POLICY_V1_2_MANIFEST_SHA256,
            load_v1_2_policy_bundle,
        )

        other_policy = load_v1_2_policy_bundle(
            ROOT / "policies/missing_service_social_network/evidence-capture-v1.2.yaml",
            ROOT / "schemas/evidence-capture-policy-v1.2.schema.json",
            ROOT / "EVIDENCE_CAPTURE_POLICY_V1_2_SHA256SUMS",
            expected_manifest_sha256=POLICY_V1_2_MANIFEST_SHA256,
        )
        other_root = Path(self.temporary.name) / "foreign-policy"
        other_root.mkdir(mode=0o700)
        with self.EvidenceStore(
            other_root, other_policy, self.run_id, self.attempt_id
        ) as store:
            self.assert_code(
                "RESTORATION_SOURCE_UNRESOLVED",
                store, self.source, self.request, CREATED,
            )

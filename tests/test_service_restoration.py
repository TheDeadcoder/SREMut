"""Offline tests for frozen Service-restoration derivation and validation."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
import unittest

from sremut.canonical_json import canonical_json_bytes
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

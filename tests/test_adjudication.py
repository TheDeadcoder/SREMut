"""Offline tests for authenticated adjudication raw-backing closure."""

from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
from types import SimpleNamespace
import unittest

from sremut.adjudication import (
    AdjudicationError,
    _original_oracle_value,
    validate_adjudication_descriptor,
)
from sremut.canonical_json import canonical_json_bytes, parse_canonical_json
from sremut.policy_runtime import EXPECTED_HOOK_ORDER
from sremut.resolved_context import CONNECTED_HOOKS, ResolvedContextError
import tests.test_workload_evidence as workload_tests


ROOT = Path(__file__).resolve().parents[1]


class AdjudicationTests(unittest.TestCase):
    def workload_context(self, **kwargs):
        workload_tests.WorkloadResolvedHookTests.setUpClass()
        owner = workload_tests.WorkloadResolvedHookTests(methodName="test_both_workload_hooks_pass_through_public_dispatcher")
        context, _candidate = owner.build_context(**kwargs)
        owner.doCleanups()
        adjudications = [row for row in context.evidence.values() if row.reference.role == "adjudication"]
        self.assertEqual(len(adjudications), 1)
        return owner.policy, context, parse_canonical_json(adjudications[0].descriptor_bytes)

    def test_global_hook_state_is_exact_twelve_connected_zero_unavailable(self):
        self.assertEqual(CONNECTED_HOOKS, frozenset(EXPECTED_HOOK_ORDER))
        self.assertEqual(tuple(hook for hook in EXPECTED_HOOK_ORDER if hook not in CONNECTED_HOOKS), ())
        self.assertEqual(EXPECTED_HOOK_ORDER, (
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
        ))

    def test_complete_finalized_adjudication_passes_full_dispatch(self):
        policy, context, candidate = self.workload_context()
        result = policy.full_admissibility(candidate, context)
        self.assertTrue(result.valid)
        self.assertIsNone(result.failure_code)
        self.assertEqual(
            tuple(row.hook_id for row in result.hook_outcomes),
            tuple(hook.hook_id for hook in policy.hook_plan(candidate["document_type"], "adjudication")),
        )
        self.assertTrue(all(row.outcome == "PASS" for row in result.hook_outcomes))

    def test_public_context_hook_cannot_skip_adjudication_recomputation(self):
        _policy, context, candidate = self.workload_context()
        changed = deepcopy(candidate)
        changed["boolean_or_categorical_value"] = False
        with self.assertRaises(ResolvedContextError):
            context.validate_hook("VALIDATE_ADJUDICATION_RAW_BACKING_V1", changed)

    def test_boolean_is_recomputed_from_exact_workload_payload(self):
        _policy, context, candidate = self.workload_context()
        candidate = deepcopy(candidate)
        candidate["boolean_or_categorical_value"] = False
        with self.assertRaises(AdjudicationError) as caught:
            validate_adjudication_descriptor(context, candidate)
        self.assertIn(caught.exception.code, {"EVIDENCE_REFERENCE_UNRESOLVED", "ADJUDICATION_RAW_ROLE_INVALID"})

    def synthetic_context(self, *, role="healthy_prestate", payload=b"{}", sequence=2):
        digest = "a" * 64 if payload is None else hashlib.sha256(payload).hexdigest()
        reference = {"evidence_id": "ev-" + "a" * 32}
        raw = SimpleNamespace(
            reference=SimpleNamespace(
                evidence_id=reference["evidence_id"], role=role,
                payload_sha256=digest, payload_size_bytes=0 if payload is None else len(payload),
            ),
            payload_bytes=payload, publication=SimpleNamespace(sequence_number=sequence),
        )
        context = SimpleNamespace(
            run_id="sremut-ms-m01-r01-a01-abcdef123456", attempt_id="a01",
            expected_evaluation_context={
                "predicate_id": "INITIAL_INVARIANT_EVALUATION",
                "phase": "INITIAL_MUTANT_CHALLENGE",
                "deadline_identity": "INITIAL_FRESH_WORKLOAD_60S",
            },
            journal_records=({
                "sequence_number": 1,
                "transition": "EVALUATION_AUTHORIZED:INITIAL_INVARIANT_EVALUATION",
            },),
            _policy=SimpleNamespace(policy={
                "full_admissibility_validation": {
                    "adjudication_predicate_raw_role_context_deadline_matrix": {
                        "INITIAL_INVARIANT_EVALUATION": {
                            "phase": "INITIAL_MUTANT_CHALLENGE",
                            "deadline_identity": "INITIAL_FRESH_WORKLOAD_60S",
                            "allowed_raw_roles": ["workload_log_bytes"],
                        }
                    }
                }
            }),
        )
        context._candidate_evidence = lambda _candidate: SimpleNamespace(
            publication=SimpleNamespace(sequence_number=3)
        )
        context.resolve_reference = lambda _reference: raw
        candidate = {
            "role": "adjudication", "run_id": context.run_id, "attempt_id": context.attempt_id,
            "predicate_oracle_or_classification_id": "INITIAL_INVARIANT_EVALUATION",
            "applicable_deadline": "INITIAL_FRESH_WORKLOAD_60S",
            "result_type": "BOOLEAN", "boolean_or_categorical_value": True,
            "raw_evidence_references": [reference],
            "raw_evidence_sha256_per_reference": [digest],
            "kubernetes_uid_and_resource_version_references_when_applicable": [],
        }
        return context, candidate

    def test_role_missing_payload_cross_attempt_and_order_fail_closed(self):
        cases = (
            ({"role": "healthy_prestate"}, "ADJUDICATION_RAW_ROLE_INVALID"),
            ({
                "role": "original_oracle_result",
                "payload": canonical_json_bytes({"outcome": "RETURNED_TRUE", "returned_boolean": True}),
            }, "ADJUDICATION_RAW_ROLE_INVALID"),
            ({"role": "workload_log_bytes", "payload": None}, "PAYLOAD_BYTES_MISSING"),
            ({"role": "workload_log_bytes", "sequence": 1}, "PUBLICATION_ORDER_INVALID"),
        )
        for kwargs, code in cases:
            context, candidate = self.synthetic_context(**kwargs)
            with self.subTest(code=code):
                with self.assertRaises(AdjudicationError) as caught:
                    validate_adjudication_descriptor(context, candidate)
                self.assertEqual(caught.exception.code, code)
        context, candidate = self.synthetic_context(role="workload_log_bytes")
        candidate["attempt_id"] = "a02"
        with self.assertRaises(AdjudicationError) as caught:
            validate_adjudication_descriptor(context, candidate)
        self.assertEqual(caught.exception.code, "RUN_ATTEMPT_MISMATCH")

    def test_missing_raw_reference_and_hash_arrays_reject(self):
        _policy, context, candidate = self.workload_context()
        for field, value in (
            ("raw_evidence_references", []),
            ("raw_evidence_sha256_per_reference", []),
        ):
            changed = deepcopy(candidate)
            changed[field] = value
            with self.subTest(field=field), self.assertRaises(AdjudicationError):
                validate_adjudication_descriptor(context, changed)

    def test_wrong_deadline_phase_and_cross_attempt_reject(self):
        _policy, context, candidate = self.workload_context()
        cases = (
            ("applicable_deadline", "WRONG", "ADJUDICATION_DEADLINE_MISMATCH"),
            ("predicate_oracle_or_classification_id", "RESTORATION_POSITIVE_CONTROL", "ADJUDICATION_EVALUATION_PHASE_MISMATCH"),
            ("attempt_id", "a02", "EVIDENCE_REFERENCE_UNRESOLVED"),
        )
        for field, value, expected in cases:
            changed = deepcopy(candidate)
            changed[field] = value
            with self.subTest(field=field), self.assertRaises(AdjudicationError) as caught:
                validate_adjudication_descriptor(context, changed)
            self.assertIn(caught.exception.code, {expected, "EVIDENCE_REFERENCE_UNRESOLVED"})

    def test_payload_hash_and_publication_order_are_bound(self):
        _policy, context, candidate = self.workload_context()
        raw = context.resolve_reference(candidate["raw_evidence_references"][0])
        adjudication = context._candidate_evidence(candidate)
        marker = next(
            row["sequence_number"] for row in context.journal_records
            if row["transition"] == "EVALUATION_AUTHORIZED:INITIAL_INVARIANT_EVALUATION"
        )
        self.assertLess(marker, raw.publication.sequence_number)
        self.assertLess(raw.publication.sequence_number, adjudication.publication.sequence_number)
        changed = deepcopy(candidate)
        changed["raw_evidence_sha256_per_reference"][0] = "f" * 64
        with self.assertRaises(AdjudicationError):
            validate_adjudication_descriptor(context, changed)

    def test_observation_only_boolean_is_never_trusted(self):
        from sremut.adjudication import recompute_adjudication

        row = SimpleNamespace(
            reference=SimpleNamespace(role="kubernetes_object_projection"),
            payload_bytes=canonical_json_bytes({"kind": "Pod"}),
        )
        with self.assertRaises(AdjudicationError) as caught:
            recompute_adjudication(None, {
                "result_type": "BOOLEAN",
                "boolean_or_categorical_value": True,
            }, (row,))
        self.assertEqual(caught.exception.code, "ADJUDICATION_RAW_ROLE_INVALID")

    def test_oracle_exception_is_not_boolean_false(self):
        payload = canonical_json_bytes({"outcome": "ORACLE_EXCEPTION", "returned_boolean": None})
        row = SimpleNamespace(
            reference=SimpleNamespace(role="original_oracle_result"),
            payload_bytes=payload,
        )
        with self.assertRaises(AdjudicationError) as caught:
            _original_oracle_value((row,))
        self.assertEqual(caught.exception.code, "ADJUDICATION_RAW_ROLE_INVALID")

    def test_original_oracle_exact_true_and_false_are_distinct(self):
        for value in (True, False):
            payload = canonical_json_bytes({
                "outcome": "RETURNED_TRUE" if value else "RETURNED_FALSE",
                "returned_boolean": value,
            })
            row = SimpleNamespace(
                reference=SimpleNamespace(role="original_oracle_result"),
                payload_bytes=payload,
            )
            self.assertIs(_original_oracle_value((row,)), value)

    def test_structural_seal_without_context_never_passes(self):
        policy, _context, candidate = self.workload_context()
        result = policy.full_admissibility(candidate)
        self.assertFalse(result.valid)
        self.assertEqual(result.failure_code, "MISSING_RESOLVED_CONTEXT")

    def test_validation_result_cannot_be_constructed_publicly(self):
        from sremut.policy_runtime import ValidationResult

        with self.assertRaises(TypeError):
            ValidationResult(True, "forged", None, None, None)


if __name__ == "__main__":
    unittest.main()

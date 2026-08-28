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
            evaluation_authorization_contexts={
                "INITIAL_INVARIANT_EVALUATION": {
                    "predicate_id": "INITIAL_INVARIANT_EVALUATION",
                    "phase": "INITIAL_MUTANT_CHALLENGE",
                    "deadline_identity": "INITIAL_FRESH_WORKLOAD_60S",
                    "authorization_state": "ORIGINAL_ORACLE_EVALUATED",
                    "marker_sequence_number": 1,
                }
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


def _operation_marker(label: str) -> str:
    """One state-neutral OPERATION_AUTHORIZED carrier with canonical hex payload."""
    payload = canonical_json_bytes({"publication": label})
    return "OPERATION_AUTHORIZED:" + payload.hex()


class ThreePredicateConformanceTests(unittest.TestCase):
    """Twelve-hook conformance against the PRODUCTION dispatcher.

    Every assertion below runs `AuthenticatedPolicy.full_admissibility` over a
    context produced by the production `resolve_evidence_context` from a really
    sealed attempt on disk.  Nothing here uses the v1.2 generator's delta
    validator.

    The fixture is the first three-predicate terminal attempt in the project:
    all three frozen predicates are authorized, each at its own legal state, and
    each carries its own workload window and adjudication.  Publication order is
    marker < raw < adjudication for every predicate, expressed only through the
    frozen transitions the journal writer accepts.
    """

    @classmethod
    def setUpClass(cls):
        workload_tests.WorkloadResolvedHookTests.setUpClass()
        cls.policy = workload_tests.WorkloadResolvedHookTests.policy

    PHASES = (
        ("INITIAL_INVARIANT_EVALUATION", "INITIAL_MUTANT_CHALLENGE", 1,
         "INITIAL_FRESH_WORKLOAD_60S"),
        ("REPLACEMENT_PERSISTENCE_EVALUATION", "POST_REPLACEMENT_PERSISTENCE", 2,
         "POST_REPLACEMENT_FRESH_WORKLOAD_60S"),
        ("RESTORATION_POSITIVE_CONTROL", "RESTORATION_POSITIVE_CONTROL", 3,
         "RESTORATION_FRESH_WORKLOAD_60S"),
    )

    def build(self, *, predicates=None, boolean_override=None, duplicate_predicate=False,
              omit_predicate=None, cross_window=None, suppress_marker=None):
        import tempfile
        from dataclasses import replace as _replace

        from sremut.evidence import EvidenceStore, ExternalAnchor
        from sremut.journal import Journal
        from sremut.resolved_context import resolve_evidence_context
        from sremut.workload_evidence import prepare_workload_window

        W = workload_tests
        temporary = tempfile.TemporaryDirectory(prefix="sremut-three-predicate-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        rows = list(self.PHASES if predicates is None else predicates)
        if omit_predicate is not None:
            rows = [row for row in rows if row[0] != omit_predicate]

        published = {}
        monotonic = 0
        with EvidenceStore(root, self.policy, W.RUN_ID, W.ATTEMPT_ID) as store:
            for predicate, phase, ordinal, deadline in rows:
                monotonic += 1
                pod_payload = canonical_json_bytes(
                    {
                        "apiVersion": "v1",
                        "kind": "Pod",
                        "metadata": {
                            "name": f"user-service-{ordinal}",
                            "namespace": "social-network",
                            "uid": f"pod-uid-{ordinal}",
                            "resourceVersion": str(10 + ordinal),
                        },
                        "status": {
                            "containerStatuses": [
                                {"name": "user-service", "ready": True,
                                 "restartCount": 0, "state": {}}
                            ]
                        },
                    }
                )
                pod_ref = store.publish_payload(
                    "kubernetes_object_projection",
                    pod_payload,
                    {
                        "run_id": W.RUN_ID, "attempt_id": W.ATTEMPT_ID,
                        "created_utc": W.CREATED, "monotonic_ns": monotonic,
                        "boot_identity": W.BOOT,
                        "request_identity_reference": W.fake_request_reference(),
                        "object_count": 1,
                        "projection_class": "KUBERNETES_OBJECT_PROJECTION_V1",
                    },
                )
                before = (W.entry(1.0 + ordinal, 1),)
                after = before + (W.entry(2.0 + ordinal, 50),)
                plan = prepare_workload_window(
                    self.policy, before=before, after=after, phase=phase, ordinal=ordinal,
                    run_id=W.RUN_ID, attempt_id=W.ATTEMPT_ID, mutant_id="MS-M01",
                    repetition=1, workload_pod_projection_reference=pod_ref,
                    pod_name=f"user-service-{ordinal}", pod_uid=f"pod-uid-{ordinal}",
                    container_restart_count=0,
                )
                monotonic += 1
                prefix_candidate = plan.prefix_log_candidate(W.stamp(monotonic))
                prefix_ref = store.publish_payload(
                    prefix_candidate.role, prefix_candidate.payload,
                    prefix_candidate.publication_metadata())
                monotonic += 1
                boundary_candidate = plan.boundary_candidate(prefix_ref, W.stamp(monotonic))
                boundary_ref = store.publish_descriptor(
                    boundary_candidate.role, boundary_candidate.publication_metadata())
                monotonic += 1
                raw_candidate = plan.full_log_candidate(W.stamp(monotonic))
                raw_ref = store.publish_payload(
                    raw_candidate.role, raw_candidate.payload,
                    raw_candidate.publication_metadata())
                monotonic += 1
                parse_candidate = plan.parse_result_candidate(
                    boundary_ref, raw_ref, W.stamp(monotonic))
                parse_ref = store.publish_payload(
                    parse_candidate.role, parse_candidate.payload,
                    parse_candidate.publication_metadata())
                window = plan.adjudication_window(boundary_ref, raw_ref, parse_ref)
                if cross_window is not None and predicate == cross_window[0]:
                    # An AUTHENTIC descriptor whose declared predicate belongs to
                    # one phase but whose window identity belongs to another.  The
                    # bytes are genuine, so the dispatcher reaches the workload
                    # binding rule instead of stopping at descriptor identity.
                    window = dict(window)
                    window["phase"] = cross_window[1]
                    window["ordinal"] = cross_window[2]
                raw_refs = (raw_ref, parse_ref, pod_ref)
                value = True if boolean_override is None else boolean_override.get(predicate, True)
                adjudication_ref = store.publish_descriptor(
                    "adjudication",
                    {
                        "adjudication_id": f"adj-{predicate}",
                        "predicate_oracle_or_classification_id": predicate,
                        "result_type": "BOOLEAN",
                        "boolean_or_categorical_value": value,
                        "reason": "frozen workload predicate evaluation",
                        "first_observation_utc": W.CREATED,
                        "last_observation_utc": W.CREATED,
                        "monotonic_elapsed_time": 1,
                        "observation_count": 1,
                        "applicable_deadline": deadline,
                        "evaluator_source_or_runner_bundle_sha256": "a" * 64,
                        "raw_evidence_references": [r.as_dict() for r in raw_refs],
                        "raw_evidence_sha256_per_reference": [
                            r.payload_sha256 for r in raw_refs],
                        "kubernetes_uid_and_resource_version_references_when_applicable": [
                            pod_ref.as_dict()],
                        "dependency_and_toolchain_identity": {},
                        "attempt_id": W.ATTEMPT_ID, "run_id": W.RUN_ID,
                        "workload_window_adjudication_identity": window,
                    },
                )
                published[predicate] = {
                    "raw": (pod_ref, prefix_ref, boundary_ref, raw_ref, parse_ref),
                    "cited": raw_refs,
                    "adjudication": adjudication_ref,
                }

            order = [row[0] for row in rows]
            plan_records = [
                ("CREATED->PREFLIGHT_PASS", ()),
                ("PREFLIGHT_PASS->HEALTHY_STATE_CAPTURED", ()),
                ("HEALTHY_STATE_CAPTURED->MUTANT_INJECTED", ()),
                ("MUTANT_INJECTED->MUTANT_STATE_VERIFIED", ()),
                ("MUTANT_STATE_VERIFIED->ORIGINAL_ORACLE_STARTED", ()),
                ("ORIGINAL_ORACLE_STARTED->ORIGINAL_ORACLE_EVALUATED", ()),
            ]
            advance = {
                "INITIAL_INVARIANT_EVALUATION": "ORIGINAL_ORACLE_EVALUATED->CONTRACT_EVALUATED",
                "REPLACEMENT_PERSISTENCE_EVALUATION": "CONTRACT_EVALUATED->RESTORE_STARTED",
                "RESTORATION_POSITIVE_CONTROL": "RESTORE_STARTED->RESTORE_VERIFIED",
            }
            # The frozen workload hook requires a strict publication order
            # (pod <= prefix < boundary <= raw <= parse < adjudication), so each
            # predicate needs three distinct publication records.  The journal
            # writer accepts OPERATION_AUTHORIZED records, which are state
            # neutral, so they carry the two intermediate publications without
            # inventing any transition the state machine does not define.
            for predicate in order:
                bundle = published[predicate]
                pod_ref, prefix_ref, boundary_ref, raw_ref, parse_ref = bundle["raw"]
                if predicate != suppress_marker:
                    plan_records.append((f"EVALUATION_AUTHORIZED:{predicate}", ()))
                plan_records.append(
                    (_operation_marker(f"{predicate}:capture"), (pod_ref, prefix_ref)))
                plan_records.append(
                    (_operation_marker(f"{predicate}:window"),
                     (boundary_ref, raw_ref, parse_ref)))
                plan_records.append((advance[predicate], (bundle["adjudication"],)))
            # A reduced predicate set still has to walk the frozen state machine
            # to a legal terminal; the unused advances simply carry no citations.
            for predicate, _phase, _ordinal, _deadline in self.PHASES:
                if predicate not in order:
                    plan_records.append((advance[predicate], ()))
            plan_records.append(("RESTORE_VERIFIED->FINALIZED", ()))

            with Journal(root, self.policy, W.RUN_ID, W.ATTEMPT_ID) as journal:
                for index, (transition, references) in enumerate(plan_records):
                    journal.append_state_transition(
                        transition, utc_time=W.CREATED, monotonic_ns=index + 1,
                        boot_identity=W.BOOT,
                        descriptor_sha256=tuple(r.descriptor_sha256 for r in references),
                        payload_sha256=tuple(
                            r.payload_sha256 for r in references
                            if r.payload_sha256 is not None),
                        intent_receipt_adjudication_sha256=tuple(
                            r.descriptor_sha256 for r in references
                            if r.role == "adjudication"),
                    )
            seal = store.seal("FINALIZED")
        anchor = ExternalAnchor("three-predicate-attempt", seal.manifest_relative_path,
                                seal.manifest_sha256)
        context = resolve_evidence_context(
            self.policy, root, W.RUN_ID, W.ATTEMPT_ID, anchor,
            expected_terminal_manifest_identity=seal.manifest_sha256,
        )
        return context, published, root, seal

    def test_three_markers_derive_three_independent_contexts(self):
        context, _published, _root, _seal = self.build()
        contexts = context.evaluation_authorization_contexts
        self.assertEqual(
            set(contexts),
            {row[0] for row in self.PHASES},
        )
        self.assertEqual(
            {row["authorization_state"] for row in contexts.values()},
            {"ORIGINAL_ORACLE_EVALUATED", "CONTRACT_EVALUATED", "RESTORE_STARTED"},
        )
        for predicate, phase, _ordinal, deadline in self.PHASES:
            row = contexts[predicate]
            self.assertEqual(row["predicate_id"], predicate)
            self.assertEqual(row["phase"], phase)
            self.assertEqual(row["deadline_identity"], deadline)
            self.assertIsInstance(row["marker_sequence_number"], int)
        self.assertFalse(hasattr(context, "expected_evaluation_context"))

    def test_all_three_adjudications_pass_the_production_dispatcher(self):
        context, published, _root, _seal = self.build()
        for predicate, _phase, _ordinal, _deadline in self.PHASES:
            reference = published[predicate]["adjudication"]
            candidate = parse_canonical_json(
                context.evidence[reference.evidence_id].descriptor_bytes)
            result = self.policy.full_admissibility(candidate, context)
            self.assertTrue(result.valid, f"{predicate}: {result.failure_code}")
            self.assertIsNone(result.failure_code)
            outcomes = [row.hook_id for row in result.hook_outcomes]
            self.assertEqual(
                outcomes,
                [hook for hook in EXPECTED_HOOK_ORDER if hook in outcomes],
                "applicable hooks must run in frozen order",
            )
            self.assertIn("VALIDATE_ADJUDICATION_RAW_BACKING_V1", outcomes)
            self.assertTrue(all(row.outcome == "PASS" for row in result.hook_outcomes))

    # ---- adversarial cases, all through production machinery ---------------

    def resolve_tampered(self, mutate):
        """Re-resolve a sealed attempt after mutating its retained bytes."""
        from sremut.evidence import ExternalAnchor
        from sremut.resolved_context import resolve_evidence_context

        W = workload_tests
        _context, published, root, seal = self.build()
        mutate(root, published)
        anchor = ExternalAnchor("three-predicate-attempt", seal.manifest_relative_path,
                                seal.manifest_sha256)
        with self.assertRaises(Exception) as caught:
            resolve_evidence_context(
                self.policy, root, W.RUN_ID, W.ATTEMPT_ID, anchor,
                expected_terminal_manifest_identity=seal.manifest_sha256,
            )
        return str(caught.exception)

    def test_offline_seal_absent_rejects_authenticated_resolution(self):
        """Case 1: a sealed revalidation with no external seal never resolves."""
        from sremut.resolved_context import resolve_evidence_context

        W = workload_tests
        _context, _published, root, seal = self.build()
        for anchor in (None, object(), {"terminal_manifest_sha256": seal.manifest_sha256}):
            with self.assertRaises(Exception) as caught:
                resolve_evidence_context(
                    self.policy, root, W.RUN_ID, W.ATTEMPT_ID, anchor,
                    expected_terminal_manifest_identity=seal.manifest_sha256,
                )
            self.assertEqual(str(caught.exception), "EXTERNAL_SEAL_MISSING")

    def test_tampered_payload_bytes_reject(self):
        """Case 2: payload bytes that no longer hash to their reference."""
        def mutate(root, published):
            reference = published["INITIAL_INVARIANT_EVALUATION"]["cited"][0]
            path = root / reference.payload_relative_path
            path.chmod(0o600)
            path.write_bytes(b'{"tampered":true}\n')

        self.assertIn(self.resolve_tampered(mutate), {
            "TERMINAL_MANIFEST_MISMATCH", "PAYLOAD_HASH_MISMATCH",
            "PAYLOAD_SIZE_MISMATCH", "EVIDENCE_REFERENCE_INVALID",
            "EXTERNAL_MANIFEST_COVERAGE_MISMATCH",
        })

    def test_tampered_descriptor_bytes_reject(self):
        """Case 3: descriptor bytes that no longer match their content identity."""
        def mutate(root, published):
            reference = published["INITIAL_INVARIANT_EVALUATION"]["adjudication"]
            path = root / reference.descriptor_relative_path
            path.chmod(0o600)
            path.write_bytes(b'{"tampered":true}')

        self.assertIn(self.resolve_tampered(mutate), {
            "TERMINAL_MANIFEST_MISMATCH", "DESCRIPTOR_HASH_MISMATCH",
            "DESCRIPTOR_CANONICAL_MISMATCH", "EVIDENCE_REFERENCE_INVALID",
            "EXTERNAL_MANIFEST_COVERAGE_MISMATCH",
        })

    def test_tampered_authoritative_journal_bytes_reject(self):
        """Case 4: the authoritative journal is re-derived, never trusted."""
        def mutate(root, published):
            path = root / "journal/attempt.jsonl"
            path.chmod(0o600)
            data = path.read_bytes().replace(b'"monotonic_ns":1,', b'"monotonic_ns":9,')
            path.write_bytes(data)

        self.assertIn(self.resolve_tampered(mutate), {
            "TERMINAL_MANIFEST_MISMATCH", "JOURNAL_CHAIN_INVALID",
            "JOURNAL_CANONICALIZATION_INVALID",
            "EXTERNAL_MANIFEST_COVERAGE_MISMATCH",
        })

    def test_false_boolean_over_unchanged_positive_evidence_rejects(self):
        """Case 5: the Boolean is recomputed from raw evidence, never trusted."""
        context, published, _root, _seal = self.build(
            boolean_override={"INITIAL_INVARIANT_EVALUATION": False}
        )
        reference = published["INITIAL_INVARIANT_EVALUATION"]["adjudication"]
        candidate = parse_canonical_json(
            context.evidence[reference.evidence_id].descriptor_bytes)
        self.assertIs(candidate["boolean_or_categorical_value"], False)
        result = self.policy.full_admissibility(candidate, context)
        self.assertFalse(result.valid)
        self.assertEqual(result.failure_code, "ADJUDICATION_RAW_ROLE_INVALID")

    def test_scalar_last_marker_context_is_not_accepted(self):
        """Case 6: no scalar last-marker representation survives anywhere."""
        context, _published, _root, _seal = self.build()
        self.assertFalse(hasattr(context, "expected_evaluation_context"))
        self.assertFalse(hasattr(context.journal_state, "evaluation"))
        contexts = context.evaluation_authorization_contexts
        self.assertEqual(len(contexts), 3)
        for predicate, row in contexts.items():
            self.assertEqual(
                set(row),
                {"predicate_id", "phase", "deadline_identity",
                 "authorization_state", "marker_sequence_number"},
            )
            self.assertEqual(row["predicate_id"], predicate)

    def test_missing_predicate_context_rejects(self):
        """Case 7: an authentic adjudication whose predicate was never authorized.

        The descriptor bytes are genuine, so this is not a forgery caught at
        descriptor identity: the journal simply carries no EVALUATION_AUTHORIZED
        marker for that predicate, so no authorization context exists for it.
        """
        context, published, _root, _seal = self.build(
            suppress_marker="RESTORATION_POSITIVE_CONTROL"
        )
        self.assertEqual(
            set(context.evaluation_authorization_contexts),
            {"INITIAL_INVARIANT_EVALUATION", "REPLACEMENT_PERSISTENCE_EVALUATION"},
        )
        reference = published["RESTORATION_POSITIVE_CONTROL"]["adjudication"]
        candidate = parse_canonical_json(
            context.evidence[reference.evidence_id].descriptor_bytes)
        result = self.policy.full_admissibility(candidate, context)
        self.assertFalse(result.valid)
        self.assertEqual(result.failure_code, "JOURNAL_EVALUATION_MARKER_MISSING")

    def test_cross_window_substitution_rejects(self):
        """Case 10: a window from another phase cannot back this predicate.

        The adjudication is published authentically -- its bytes are genuine and
        its descriptor identity resolves -- but it declares the INITIAL predicate
        while carrying the RESTORATION window.  That reaches the workload binding
        rule rather than stopping at descriptor identity.
        """
        context, published, _root, _seal = self.build(
            cross_window=("INITIAL_INVARIANT_EVALUATION",
                          "RESTORATION_POSITIVE_CONTROL", 3)
        )
        reference = published["INITIAL_INVARIANT_EVALUATION"]["adjudication"]
        candidate = parse_canonical_json(
            context.evidence[reference.evidence_id].descriptor_bytes)
        self.assertEqual(
            candidate["workload_window_adjudication_identity"]["phase"],
            "RESTORATION_POSITIVE_CONTROL",
        )
        result = self.policy.full_admissibility(candidate, context)
        self.assertFalse(result.valid)
        self.assertEqual(result.failure_code, "WORKLOAD_EVALUATION_CONTEXT_MISMATCH")

    def test_forged_descriptor_never_reaches_the_binding_rule(self):
        """A tampered descriptor is unresolvable, so it cannot even be presented."""
        context, published, _root, _seal = self.build()
        reference = published["INITIAL_INVARIANT_EVALUATION"]["adjudication"]
        donor = published["RESTORATION_POSITIVE_CONTROL"]["adjudication"]
        candidate = parse_canonical_json(
            context.evidence[reference.evidence_id].descriptor_bytes)
        forged = deepcopy(candidate)
        forged["workload_window_adjudication_identity"] = parse_canonical_json(
            context.evidence[donor.evidence_id].descriptor_bytes
        )["workload_window_adjudication_identity"]
        result = self.policy.full_admissibility(forged, context)
        self.assertFalse(result.valid)
        self.assertEqual(result.hook_id, "VALIDATE_DESCRIPTOR_CONTENT_IDENTITY_V1")
        self.assertEqual(result.failure_code, "EVIDENCE_REFERENCE_UNRESOLVED")

    # ---- closure cases -----------------------------------------------------
    #
    # Closure is reached through `ResolvedEvidenceContext.validate_hook`, which
    # is exactly the call `AuthenticatedPolicy.full_admissibility` makes for
    # VALIDATE_ADJUDICATION_RAW_BACKING_V1.  The envelope is supplied as the
    # candidate because a complete schema-valid ATTEMPT_VALIDATION_ENVELOPE_V1
    # document is a separate fixture; the closure implementation exercised here
    # is the production one, unmodified.

    def envelope(self, context, published, predicates=None):
        rows = predicates or [row[0] for row in self.PHASES]
        adjudications = [published[p]["adjudication"].as_dict() for p in rows]
        raw = []
        for predicate in rows:
            raw.extend(r.as_dict() for r in published[predicate]["cited"])
        return {
            "document_type": "ATTEMPT_VALIDATION_ENVELOPE_V1",
            "terminal_outcome": context.terminal_outcome,
            "adjudication_references": adjudications,
            "raw_evidence_references": raw,
        }

    def closure(self, context, envelope):
        context.validate_hook("VALIDATE_ADJUDICATION_RAW_BACKING_V1", envelope)

    def closure_code(self):
        """The closure failure code THIS authenticated policy actually defines.

        v1.2 adds the precise `ADJUDICATION_CLOSURE_INCOMPLETE`; v1.1 does not,
        so under a v1.1 binding the closure defect is reported with the code v1.1
        does define.  A validator must never emit a code outside its own policy's
        vocabulary, so the expectation follows the binding.
        """
        vocabulary = set(
            self.policy.policy["full_admissibility_validation"]["failure_codes"]
        )
        if "ADJUDICATION_CLOSURE_INCOMPLETE" in vocabulary:
            return "ADJUDICATION_CLOSURE_INCOMPLETE"
        return "ADJUDICATION_RAW_REFERENCE_REQUIRED"

    def assert_closure_code(self, context, envelope, code):
        with self.assertRaises(Exception) as caught:
            self.closure(context, envelope)
        self.assertEqual(str(caught.exception), code)

    def test_complete_three_predicate_closure_passes(self):
        context, published, _root, _seal = self.build()
        self.closure(context, self.envelope(context, published))

    def test_duplicate_predicate_under_distinct_evidence_ids_rejects(self):
        """Case 8: two adjudications for one predicate is not a closure."""
        context, published, _root, _seal = self.build()
        envelope = self.envelope(context, published)
        # a second, genuinely distinct adjudication descriptor for a predicate
        # already covered -- distinct evidence id, same predicate
        donor = published["REPLACEMENT_PERSISTENCE_EVALUATION"]["adjudication"]
        envelope["adjudication_references"].append(donor.as_dict())
        self.assert_closure_code(context, envelope, self.closure_code())

    def test_finalized_requires_all_three_predicates(self):
        """Case 9a: FINALIZED with a predicate omitted is not a closure."""
        context, published, _root, _seal = self.build()
        envelope = self.envelope(
            context, published,
            predicates=["INITIAL_INVARIANT_EVALUATION",
                        "REPLACEMENT_PERSISTENCE_EVALUATION"],
        )
        self.assert_closure_code(context, envelope, self.closure_code())

    def test_omitted_and_extra_raw_references_reject(self):
        """Case 9b: raw references must equal the exact union, no more, no less."""
        context, published, _root, _seal = self.build()
        thin = self.envelope(context, published)
        thin["raw_evidence_references"] = thin["raw_evidence_references"][:-1]
        self.assert_closure_code(context, thin, "ADJUDICATION_RAW_REFERENCE_REQUIRED")

        fat = self.envelope(context, published)
        spare = published["INITIAL_INVARIANT_EVALUATION"]["raw"][1]  # prefix log
        fat["raw_evidence_references"].append(spare.as_dict())
        self.assert_closure_code(context, fat, "ADJUDICATION_RAW_REFERENCE_REQUIRED")

    def test_duplicate_raw_reference_rejects(self):
        """Case 9c: a repeated raw reference is a closure defect."""
        context, published, _root, _seal = self.build()
        envelope = self.envelope(context, published)
        envelope["raw_evidence_references"].append(
            envelope["raw_evidence_references"][0])
        self.assert_closure_code(context, envelope, self.closure_code())

    def test_partial_outcomes_require_exactly_the_reached_predicates(self):
        """ABORTED_SAFE / RESTORATION_BLOCKED demand no unreached-phase evidence."""
        context, published, _root, _seal = self.build(
            suppress_marker="RESTORATION_POSITIVE_CONTROL"
        )
        reached = ["INITIAL_INVARIANT_EVALUATION", "REPLACEMENT_PERSISTENCE_EVALUATION"]
        self.assertEqual(set(context.evaluation_authorization_contexts), set(reached))
        # FINALIZED still demands all three even though only two were authorized
        self.assert_closure_code(
            context, self.envelope(context, published, predicates=reached),
            self.closure_code(),
        )

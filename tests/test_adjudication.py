"""Offline tests for authenticated adjudication raw-backing closure."""

from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
from types import SimpleNamespace
from typing import Mapping
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


def _plain(value):
    """Deep-convert a frozen retained value to plain JSON types."""
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


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


# The nine fields the frozen v1.2 schema requires of an operation context.  A
# carrier record that advances nothing still becomes the attempt's retained
# operation context, so its payload has to be shaped like one -- all null, since
# the carrier requests nothing.
_EMPTY_OPERATION = {
    "operation_kind": None, "request_rule_id": None, "resource": None,
    "namespace": None, "name": None, "request_reference": None,
    "projection_reference": None, "request_body_reference": None,
    "request_dispatch_sequence": None,
}


def _operation_marker(label: str) -> str:
    """One state-neutral OPERATION_AUTHORIZED carrier with canonical hex payload."""
    payload = canonical_json_bytes(dict(_EMPTY_OPERATION, name=label))
    return "OPERATION_AUTHORIZED:" + payload.hex()


IDENTITY_HEX = "b" * 64
IDENTITY_ALIAS = {
    key: IDENTITY_HEX
    for key in (
        "SREMUT_REPOSITORY", "SREGYM_REPOSITORY", "SREGYM_APPLICATIONS_REPOSITORY",
        "EXECUTION_PROFILE", "CONTRACT", "EXECUTION_PROFILE_SCHEMA",
        "EVIDENCE_POLICY_SCHEMA", "PYPROJECT", "UV_LOCK", "ORIGINAL_ORACLE_EXECUTABLE",
        "ATTEMPT_ROOT", "WORKLOAD_HISTORY", "BOOT_ID_SOURCE", "KUBECONFIG_HASH_SOURCE",
        "KUBECTL_CACHE_SNAPSHOT",
    )
}
IDENTITY_RELEASE = {
    "binding_mode": "SEPARATELY_FROZEN_EXECUTION_RELEASE",
    "release_artifact": "RUNNER_BUNDLE_SHA256SUMS",
    "manifest_sha256": IDENTITY_HEX, "bundle_sha256": IDENTITY_HEX,
    "git_commit": "c" * 40, "git_tree": "d" * 40,
    "annotated_tag_name": "sremut-runner-release-v1", "annotated_tag_object": "e" * 40,
    "pyproject_sha256": IDENTITY_HEX, "uv_lock_sha256": IDENTITY_HEX,
}


def load_v1_2_policy():
    """The exact authenticated v1.2 bundle -- never v1.1, never an override."""
    from sremut import policy_runtime as runtime

    return runtime.load_v1_2_policy_bundle(
        ROOT / "policies/missing_service_social_network/evidence-capture-v1.2.yaml",
        ROOT / "schemas/evidence-capture-policy-v1.2.schema.json",
        ROOT / "EVIDENCE_CAPTURE_POLICY_V1_2_SHA256SUMS",
        expected_manifest_sha256=runtime.POLICY_V1_2_MANIFEST_SHA256,
    )


class ThreePredicateConformanceTests(unittest.TestCase):
    """Dispatcher conformance under the AUTHENTICATED evidence-policy v1.2.

    Every assertion runs `AuthenticatedPolicy.full_admissibility` over a context
    the production `resolve_evidence_context` rebuilt from a really sealed
    attempt on disk, under the v1.2 bundle loaded by `load_v1_2_policy_bundle`.
    Nothing here uses the v1.2 generator's delta validator, and nothing calls
    `context.validate_hook` as a substitute for the dispatcher.  Every piece of
    evidence is published through the public `EvidenceStore` API; no descriptor
    file is written directly.

    These are evidence-layer conformance fixtures, NOT complete MS-M01 attempts:
    they exercise terminal evidence consistency and closure.  They run no
    mutation, no restoration and no oracle, and `operations` is deliberately
    empty -- operation closure is out of scope here.

    Two document kinds are exercised, and neither runs all twelve hooks: the
    frozen applicability matrix gives an adjudication descriptor six hooks and an
    attempt-validation envelope five.  Each test asserts the exact plan the
    matrix declares for that candidate, so the hook counts are measured rather
    than assumed.
    """

    @classmethod
    def setUpClass(cls):
        cls.policy = load_v1_2_policy()

    # predicate -> (phase, ordinal, deadline, advancing transition)
    PHASES = {
        "INITIAL_INVARIANT_EVALUATION": (
            "INITIAL_MUTANT_CHALLENGE", 1, "INITIAL_FRESH_WORKLOAD_60S",
            "ORIGINAL_ORACLE_EVALUATED->CONTRACT_EVALUATED",
        ),
        "REPLACEMENT_PERSISTENCE_EVALUATION": (
            "POST_REPLACEMENT_PERSISTENCE", 2, "POST_REPLACEMENT_FRESH_WORKLOAD_60S",
            "CONTRACT_EVALUATED->RESTORE_STARTED",
        ),
        "RESTORATION_POSITIVE_CONTROL": (
            "RESTORATION_POSITIVE_CONTROL", 3, "RESTORATION_FRESH_WORKLOAD_60S",
            "RESTORE_STARTED->RESTORE_VERIFIED",
        ),
    }
    ORDER = (
        "INITIAL_INVARIANT_EVALUATION",
        "REPLACEMENT_PERSISTENCE_EVALUATION",
        "RESTORATION_POSITIVE_CONTROL",
    )
    # Which predicates an outcome can LEGALLY authorize, read off the frozen
    # state machine: ABORTED_SAFE is reachable only from CREATED,
    # PREFLIGHT_PASS or HEALTHY_STATE_CAPTURED, so such an attempt never reaches
    # ORIGINAL_ORACLE_EVALUATED and can authorize nothing at all.
    REACHED = {
        "FINALIZED": ORDER,
        "RESTORATION_BLOCKED": ORDER[:2],
        "ABORTED_SAFE": (),
    }
    PREFIX = (
        "CREATED->PREFLIGHT_PASS",
        "PREFLIGHT_PASS->HEALTHY_STATE_CAPTURED",
        "HEALTHY_STATE_CAPTURED->MUTANT_INJECTED",
        "MUTANT_INJECTED->MUTANT_STATE_VERIFIED",
        "MUTANT_STATE_VERIFIED->ORIGINAL_ORACLE_STARTED",
        "ORIGINAL_ORACLE_STARTED->ORIGINAL_ORACLE_EVALUATED",
    )

    # ---- fixture -----------------------------------------------------------

    def build(self, outcome="FINALIZED"):
        """One authentic sealed attempt for `outcome`, resolved by production code."""
        import tempfile

        from sremut.evidence import EvidenceStore, ExternalAnchor
        from sremut.journal import Journal
        from sremut.resolved_context import resolve_evidence_context
        from sremut.workload_evidence import prepare_workload_window

        W = workload_tests
        temporary = tempfile.TemporaryDirectory(prefix="sremut-v1-2-conformance-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        # An outcome that can authorize predicates captures and retains all
        # three windows; what differs is how many it legally AUTHORIZED, so an
        # unreached phase leaves well-formed, publication-recorded evidence with
        # no EVALUATION_AUTHORIZED marker in front of it.  ABORTED_SAFE is
        # different in kind: it never reaches the challenge at all, so it
        # synthesizes no pod, workload or adjudication evidence whatsoever.
        publishable = () if outcome == "ABORTED_SAFE" else self.ORDER

        published = {}
        monotonic = 0
        with EvidenceStore(root, self.policy, W.RUN_ID, W.ATTEMPT_ID) as store:
            for predicate in publishable:
                phase, ordinal, deadline, _advance = self.PHASES[predicate]
                monotonic += 1
                pod_ref = store.publish_payload(
                    "kubernetes_object_projection",
                    canonical_json_bytes(
                        {
                            "apiVersion": "v1", "kind": "Pod",
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
                    ),
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
                cited = (raw_ref, parse_ref, pod_ref)

                def adjudication(label):
                    return store.publish_descriptor(
                        "adjudication",
                        {
                            "adjudication_id": label,
                            "predicate_oracle_or_classification_id": predicate,
                            "result_type": "BOOLEAN",
                            "boolean_or_categorical_value": True,
                            "reason": "frozen workload predicate evaluation",
                            "first_observation_utc": W.CREATED,
                            "last_observation_utc": W.CREATED,
                            "monotonic_elapsed_time": 1,
                            "observation_count": 1,
                            "applicable_deadline": deadline,
                            "evaluator_source_or_runner_bundle_sha256": "a" * 64,
                            "raw_evidence_references": [r.as_dict() for r in cited],
                            "raw_evidence_sha256_per_reference": [
                                r.payload_sha256 for r in cited],
                            "kubernetes_uid_and_resource_version_references_when_applicable": [
                                pod_ref.as_dict()],
                            "dependency_and_toolchain_identity": {},
                            "attempt_id": W.ATTEMPT_ID, "run_id": W.RUN_ID,
                            "workload_window_adjudication_identity": window,
                        },
                    )

                published[predicate] = {
                    "raw": (pod_ref, prefix_ref, boundary_ref, raw_ref, parse_ref),
                    "cited": cited,
                    "adjudication": adjudication(f"adj-{predicate}"),
                    # A SECOND, separately published descriptor for the same
                    # predicate.  Distinct adjudication_id -> distinct bytes ->
                    # distinct evidence id; everything else is equally valid.
                    "twin": adjudication(f"adj-{predicate}-twin"),
                }

            identities = self._identities(store, outcome)
            plan_records = self._journal_plan(outcome, published, identities)
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
            stop_path = None
            if outcome == "RESTORATION_BLOCKED":
                stop_path = store.write_global_stop(self._global_stop(published))
            seal = store.seal(outcome, global_stop_relative_path=stop_path)
        anchor = ExternalAnchor("v1-2-conformance-attempt", seal.manifest_relative_path,
                                seal.manifest_sha256)
        context = resolve_evidence_context(
            self.policy, root, W.RUN_ID, W.ATTEMPT_ID, anchor,
            expected_terminal_manifest_identity=seal.manifest_sha256,
        )
        # The envelope embeds the RETAINED canonical descriptors, read back out
        # of the authenticated context rather than reconstructed here.
        start_reference, terminal_reference = identities["references"]
        identities["documents"] = (
            parse_canonical_json(
                context.evidence[start_reference.evidence_id].descriptor_bytes),
            parse_canonical_json(
                context.evidence[terminal_reference.evidence_id].descriptor_bytes),
        )
        return context, published, root, seal, identities

    def _identities(self, store, outcome):
        """Publish the START and TERMINAL run identities through the public API.

        v1.2 widened `roles.run_identity.conditional_required_metadata` to the
        exact union of the fields the closed START and TERMINAL descriptor
        schemas already require, so `EvidenceStore.publish_descriptor` is now
        the only thing this fixture needs.  Nothing is written to a descriptor
        file directly.
        """
        W = workload_tests
        common = dict(run_id=W.RUN_ID, attempt_id=W.ATTEMPT_ID,
                      created_utc=W.CREATED, boot_identity=W.BOOT)
        start_reference = store.publish_descriptor("run_identity", dict(
            common, monotonic_ns=1, phase="START",
            runtime_identity={
                "python_version": "3.12.3", "pyyaml_version": "6.0.2",
                "kubernetes_version": "32.0.1", "jsonschema_version": "4.23.0",
                "uv_version": "0.12.5", "kubectl_version": "v1.32.0"},
            contract_binding={
                "tag_object": "378e9e9180438910611e7642402220e76bb302ca",
                "commit": "abed58d67e3f91e61f3ad666a47f0101cc680b93",
                "tree": "2648659b56a7d472bcedb38b8ffb6bc085aea650",
                "sha256": "bda78e1b07b5eb0628954bcafd8fae3fc1bb2f3b22770046584bd873b72a2488"},
            execution_profile_binding={
                "tag_object": "7c6493eb7dce68370fd0d5be572edd968654a1d6",
                "commit": "35fcaeecd6cca02aaec6ebed63455f662bc28176",
                "tree": "c55ac0748275b9b3ea1ec133700cb7eeec54325b",
                "sha256": "79cb2d45298e6221d78fcc3aea82df52c71f60f0ab4ba872e1f0579a908703c7"},
            runner_release_binding=IDENTITY_RELEASE,
            pyproject_sha256="93e3c59d74450ab9df1e09128a929fd50804ddbf693a2bc4d005e5620579e0f7",
            uv_lock_sha256="700c432b80e151da281f8052451be13ef28b8bf61cfdff21c8c215367db79f01",
            source_alias_sha256=IDENTITY_ALIAS,
            kubeconfig_content_sha256=IDENTITY_HEX,
            kubectl_default_cache_before_sha256=IDENTITY_HEX,
        ))
        terminal_reference = store.publish_descriptor("run_identity", dict(
            common, monotonic_ns=2, phase="TERMINAL",
            start_identity_reference=start_reference.as_dict(),
            terminal_release_identity=IDENTITY_RELEASE,
            terminal_source_alias_sha256=IDENTITY_ALIAS,
            kubectl_default_cache_before_sha256=IDENTITY_HEX,
            kubectl_default_cache_after_sha256=IDENTITY_HEX,
            terminal_outcome=outcome,
        ))
        return {
            "references": (start_reference, terminal_reference),
            "documents": None,
        }

    def _journal_plan(self, outcome, published, identities):
        """The exact frozen-transition walk this outcome requires.

        START is cited in the initial publication record; TERMINAL is cited only
        on the transition into the terminal state, which is the first moment the
        terminal outcome it records is true.
        """
        start_reference, terminal_reference = identities["references"]
        reached = self.REACHED[outcome]
        records = [(self.PREFIX[0], (start_reference,))]
        if outcome == "ABORTED_SAFE":
            records.append((self.PREFIX[1], ()))
        else:
            records.extend((transition, ()) for transition in self.PREFIX[1:])
        for predicate in reached:
            _phase, _ordinal, _deadline, advance = self.PHASES[predicate]
            records.append((f"EVALUATION_AUTHORIZED:{predicate}", ()))
            records.extend(self._publication_records(predicate, published))
            records.append((advance, (published[predicate]["adjudication"],)))
            records.append(
                (_operation_marker(f"{predicate}:twin"), (published[predicate]["twin"],)))
        for predicate in self.ORDER:
            if predicate in reached or predicate not in published:
                continue
            records.extend(self._publication_records(predicate, published))
            records.append((
                _operation_marker(f"{predicate}:unauthorized"),
                (published[predicate]["adjudication"], published[predicate]["twin"]),
            ))
        if outcome == "ABORTED_SAFE":
            terminal = "HEALTHY_STATE_CAPTURED->ABORTED_SAFE"
        elif outcome == "RESTORATION_BLOCKED":
            terminal = "RESTORE_STARTED->RESTORATION_BLOCKED"
        else:
            terminal = "RESTORE_VERIFIED->FINALIZED"
        records.append((terminal, (terminal_reference,)))
        return records

    def _publication_records(self, predicate, published):
        """Carriers honouring pod <= prefix < boundary <= raw <= parse < adjudication.

        The frozen workload hook requires three distinct publication records per
        predicate.  `OPERATION_AUTHORIZED` is state neutral and is the only form
        the journal writer accepts for a record that must not advance the state
        machine, so it carries the two intermediate publications without
        inventing a transition the frozen machine does not define.
        """
        pod_ref, prefix_ref, boundary_ref, raw_ref, parse_ref = published[predicate]["raw"]
        return [
            (_operation_marker(f"{predicate}:capture"), (pod_ref, prefix_ref)),
            (_operation_marker(f"{predicate}:window"), (boundary_ref, raw_ref, parse_ref)),
        ]

    def _global_stop(self, published):
        W = workload_tests
        return {
            "document_type": "TERMINAL_GLOBAL_STOP_V1", "schema_version": 1,
            "run_id": W.RUN_ID, "attempt_id": W.ATTEMPT_ID,
            "terminal_outcome": "RESTORATION_BLOCKED",
            "reason_code": "RESTORATION_VERIFICATION_FAILED",
            "adjudication_reference":
                published["REPLACEMENT_PERSISTENCE_EVALUATION"]["adjudication"].as_dict(),
            "created_utc": W.CREATED, "monotonic_ns": 99, "boot_identity": W.BOOT,
        }

    # ---- envelope ----------------------------------------------------------

    def envelope(self, context, published, identities, *, predicates=None):
        """A complete, schema-valid `ATTEMPT_VALIDATION_ENVELOPE_V1`.

        `operations` is empty on purpose: these fixtures assert terminal
        evidence consistency, and operation closure is a separate protocol that
        this correction deliberately does not invent.
        """
        rows = list(self.REACHED[context.terminal_outcome] if predicates is None else predicates)
        rows = [predicate for predicate in rows if predicate in published]
        raw = []
        for predicate in rows:
            raw.extend(reference.as_dict() for reference in published[predicate]["cited"])
        return {
            "document_type": "ATTEMPT_VALIDATION_ENVELOPE_V1",
            "schema_version": 1,
            "run_id": context.run_id,
            "attempt_id": context.attempt_id,
            "terminal_outcome": context.terminal_outcome,
            # The exact retained canonical descriptors, not a reconstruction.
            "run_identities": [_plain(document) for document in identities["documents"]],
            "operations": [],
            "journal_records": [_plain(record) for record in context.journal_records],
            "adjudication_references": [
                published[predicate]["adjudication"].as_dict() for predicate in rows],
            "raw_evidence_references": raw,
            "terminal_manifest": {
                "document_type": "TERMINAL_MANIFEST_V1", "schema_version": 1,
                "run_id": context.run_id, "attempt_id": context.attempt_id,
                "terminal_outcome": context.terminal_outcome,
                "terminal_cache_snapshot_monotonic_ns": 10,
                "installed_monotonic_ns": 20,
                "manifest_relative_path": context.manifest_relative_path,
            },
        }

    # ---- dispatcher helpers ------------------------------------------------

    def expected_plan(self, candidate):
        """The hooks the FROZEN applicability matrix declares for this candidate."""
        kind = candidate["document_type"]
        role = candidate.get("role") if kind.endswith("DESCRIPTOR_V1") else None
        return [hook.hook_id for hook in self.policy.hook_plan(kind, role)]

    def dispatch(self, candidate, context):
        """Structural validation first, then the production dispatcher."""
        self.policy.structural_validate(candidate)
        result = self.policy.full_admissibility(candidate, context)
        executed = [row.hook_id for row in result.hook_outcomes]
        self.assertEqual(
            executed,
            [hook for hook in EXPECTED_HOOK_ORDER if hook in executed],
            "executed hooks must appear in frozen order",
        )
        return result, executed

    def assert_passes(self, candidate, context):
        result, executed = self.dispatch(candidate, context)
        self.assertTrue(result.valid, f"{result.hook_id}: {result.failure_code}")
        self.assertIsNone(result.failure_code)
        # A passing candidate ran EXACTLY the hooks the matrix declares for it.
        self.assertEqual(executed, self.expected_plan(candidate))
        self.assertTrue(all(row.outcome == "PASS" for row in result.hook_outcomes))
        return result, executed

    def assert_rejects(self, candidate, context, code, hook=None):
        result, executed = self.dispatch(candidate, context)
        self.assertFalse(result.valid)
        self.assertEqual(result.failure_code, code)
        if hook is not None:
            self.assertEqual(result.hook_id, hook)
        # The dispatcher stops at the failing hook: nothing after it ran.
        plan = self.expected_plan(candidate)
        self.assertEqual(executed, plan[: plan.index(result.hook_id) + 1])
        return result, executed

    def descriptor_of(self, context, reference):
        return parse_canonical_json(context.evidence[reference.evidence_id].descriptor_bytes)

    # ---- the authenticated binding ----------------------------------------

    def test_fixture_is_the_authenticated_v1_2_bundle(self):
        from sremut import policy_runtime as runtime

        self.assertEqual(self.policy.manifest_sha256, runtime.POLICY_V1_2_MANIFEST_SHA256)
        self.assertEqual(self.policy.policy["policy_id"], runtime.POLICY_V1_2_ID)
        self.assertEqual(self.policy.policy["semantic_version"], "1.2")
        self.assertIs(runtime.policy_binding(self.policy), runtime._V1_2_BINDING)
        self.assertIn(
            "ADJUDICATION_CLOSURE_INCOMPLETE",
            self.policy.policy["full_admissibility_validation"]["failure_codes"],
        )

    def test_declared_hook_plans_are_five_and_six_not_twelve(self):
        """The matrix, not the test, decides how many hooks a candidate runs."""
        self.assertEqual(len(EXPECTED_HOOK_ORDER), 12)
        self.assertEqual(
            len(self.policy.hook_plan("DESCRIPTOR_EVIDENCE_DESCRIPTOR_V1", "adjudication")), 6)
        self.assertEqual(len(self.policy.hook_plan("ATTEMPT_VALIDATION_ENVELOPE_V1", "NONE")), 5)

    # ---- resolved context --------------------------------------------------

    def test_v1_2_context_reconstructs_three_authorization_contexts(self):
        context, _published, _root, _seal, _identities = self.build()
        self.assertEqual(
            context.policy_identity.manifest_sha256,
            self.policy.manifest_sha256,
        )
        contexts = context.evaluation_authorization_contexts
        self.assertEqual(set(contexts), set(self.ORDER))
        self.assertEqual(
            {row["authorization_state"] for row in contexts.values()},
            {"ORIGINAL_ORACLE_EVALUATED", "CONTRACT_EVALUATED", "RESTORE_STARTED"},
        )
        for predicate in self.ORDER:
            phase, _ordinal, deadline, _advance = self.PHASES[predicate]
            row = contexts[predicate]
            self.assertEqual(row["predicate_id"], predicate)
            self.assertEqual(row["phase"], phase)
            self.assertEqual(row["deadline_identity"], deadline)
            self.assertIsInstance(row["marker_sequence_number"], int)
        self.assertFalse(hasattr(context, "expected_evaluation_context"))
        self.assertFalse(hasattr(context.journal_state, "evaluation"))

    def test_v1_2_resolved_context_document_validates_against_the_v1_2_schema(self):
        context, _published, _root, _seal, _identities = self.build()
        document = context.as_v1_2_document()
        self.assertEqual(document["document_type"], "RESOLVED_EVIDENCE_CONTEXT_V1_2")
        # The committed v1.2 schema, through the authenticated policy.
        self.policy.structural_validate(document)
        self.assertEqual(
            set(document["evaluation_authorization_contexts"]), set(self.ORDER))
        self.assertEqual(document["validation_mode"], "OFFLINE_SEALED_REVALIDATION")

    # ---- adjudication candidates through the dispatcher --------------------

    def test_every_adjudication_passes_the_production_dispatcher(self):
        context, published, _root, _seal, _identities = self.build()
        for predicate in self.ORDER:
            candidate = self.descriptor_of(context, published[predicate]["adjudication"])
            _result, executed = self.assert_passes(candidate, context)
            self.assertIn("VALIDATE_ADJUDICATION_RAW_BACKING_V1", executed)
            self.assertEqual(len(executed), 6)

    def test_missing_predicate_context_rejects(self):
        """An authentic adjudication whose predicate the attempt never authorized.

        The descriptor bytes are genuine and resolvable -- this is not a forgery
        caught at descriptor identity.  The attempt simply never raised an
        EVALUATION_AUTHORIZED marker for that predicate, so no authorization
        context exists to bind it to.
        """
        context, published, _root, _seal, _identities = self.build("RESTORATION_BLOCKED")
        self.assertEqual(
            set(context.evaluation_authorization_contexts), set(self.ORDER[:2]))
        # the two reached predicates are admissible ...
        for predicate in self.ORDER[:2]:
            self.assert_passes(
                self.descriptor_of(context, published[predicate]["adjudication"]), context)
        # ... the unreached one is not, on identically well-formed evidence
        self.assert_rejects(
            self.descriptor_of(
                context, published["RESTORATION_POSITIVE_CONTROL"]["adjudication"]),
            context, "JOURNAL_EVALUATION_MARKER_MISSING")

    def test_aborted_safe_authorizes_nothing_and_retains_no_adjudication(self):
        """The frozen state machine forbids ABORTED_SAFE from reaching a predicate."""
        context, published, _root, _seal, _identities = self.build("ABORTED_SAFE")
        self.assertEqual(context.terminal_outcome, "ABORTED_SAFE")
        self.assertEqual(dict(context.evaluation_authorization_contexts), {})
        self.assertEqual(published, {})
        # No challenge, workload or adjudication evidence was synthesized at all.
        roles = {row.reference.role for row in context.evidence.values()}
        self.assertEqual(roles, {"run_identity"})

    # ---- the envelope through the dispatcher -------------------------------

    def test_complete_envelope_passes_the_production_dispatcher(self):
        context, published, _root, _seal, identities = self.build()
        envelope = self.envelope(context, published, identities)
        _result, executed = self.assert_passes(envelope, context)
        self.assertEqual(executed, [
            "VALIDATE_CANONICAL_NO_FLOATS_V1",
            "VALIDATE_EVIDENCE_REF_HASH_PATH_ID_V1",
            "VALIDATE_ATTEMPT_PHASES_AND_FINALITY_V1",
            "VALIDATE_ADJUDICATION_RAW_BACKING_V1",
            "VALIDATE_JOURNAL_HASH_CHAIN_V1",
        ])

    def closure_reject(self, context, envelope, code="ADJUDICATION_CLOSURE_INCOMPLETE"):
        return self.assert_rejects(
            envelope, context, code, hook="VALIDATE_ADJUDICATION_RAW_BACKING_V1")

    def test_second_descriptor_for_one_predicate_rejects(self):
        """A genuinely distinct evidence id adjudicating an already covered predicate."""
        context, published, _root, _seal, identities = self.build()
        envelope = self.envelope(context, published, identities)
        twin = published["REPLACEMENT_PERSISTENCE_EVALUATION"]["twin"]
        cited = {row["evidence_id"] for row in envelope["adjudication_references"]}
        self.assertNotIn(twin.evidence_id, cited)
        # the twin is itself a fully valid adjudication for that predicate
        self.assert_passes(self.descriptor_of(context, twin), context)
        envelope["adjudication_references"].append(twin.as_dict())
        self.closure_reject(context, envelope)

    def test_duplicate_citation_of_one_evidence_id_rejects(self):
        context, published, _root, _seal, identities = self.build()
        envelope = self.envelope(context, published, identities)
        envelope["adjudication_references"].append(
            envelope["adjudication_references"][0])
        self.closure_reject(context, envelope)

    def test_missing_adjudication_rejects(self):
        context, published, _root, _seal, identities = self.build()
        envelope = self.envelope(
            context, published, identities, predicates=self.ORDER[:2])
        self.closure_reject(context, envelope)

    def test_extra_adjudication_for_an_unreached_predicate_rejects(self):
        """RESTORATION_BLOCKED never authorized the positive control."""
        context, published, _root, _seal, identities = self.build("RESTORATION_BLOCKED")
        envelope = self.envelope(context, published, identities, predicates=self.ORDER)
        self.closure_reject(context, envelope, "JOURNAL_EVALUATION_MARKER_MISSING")

    def test_omitted_raw_reference_rejects(self):
        context, published, _root, _seal, identities = self.build()
        envelope = self.envelope(context, published, identities)
        envelope["raw_evidence_references"] = envelope["raw_evidence_references"][:-1]
        self.closure_reject(context, envelope, "ADJUDICATION_RAW_REFERENCE_REQUIRED")

    def test_extra_raw_reference_rejects(self):
        context, published, _root, _seal, identities = self.build()
        envelope = self.envelope(context, published, identities)
        spare = published["INITIAL_INVARIANT_EVALUATION"]["raw"][1]  # prefix log
        envelope["raw_evidence_references"].append(spare.as_dict())
        self.closure_reject(context, envelope, "ADJUDICATION_RAW_REFERENCE_REQUIRED")

    def test_duplicate_raw_reference_rejects(self):
        context, published, _root, _seal, identities = self.build()
        envelope = self.envelope(context, published, identities)
        envelope["raw_evidence_references"].append(
            envelope["raw_evidence_references"][0])
        self.closure_reject(context, envelope)

    # ---- partial terminal coverage ----------------------------------------

    def test_restoration_blocked_closure_requires_exactly_two_predicates(self):
        context, published, _root, _seal, identities = self.build("RESTORATION_BLOCKED")
        self.assertEqual(context.terminal_outcome, "RESTORATION_BLOCKED")
        self.assertIsNotNone(context.terminal_global_stop)
        self.assertEqual(
            context.terminal_global_stop["reason_code"], "RESTORATION_VERIFICATION_FAILED")
        envelope = self.envelope(context, published, identities)
        self.assertEqual(len(envelope["adjudication_references"]), 2)
        self.assert_passes(envelope, context)
        thin = self.envelope(
            context, published, identities, predicates=self.ORDER[:1])
        self.closure_reject(context, thin)

    def test_aborted_safe_closes_with_zero_references(self):
        """Correction B: an attempt that authorized nothing closes empty.

        Under v1.1 the envelope schema required at least one adjudication and
        one raw reference, so this attempt had no representable closure at all.
        """
        context, published, _root, _seal, identities = self.build("ABORTED_SAFE")
        envelope = self.envelope(context, published, identities, predicates=[])
        self.assertEqual(envelope["adjudication_references"], [])
        self.assertEqual(envelope["raw_evidence_references"], [])
        _result, executed = self.assert_passes(envelope, context)
        self.assertIn("VALIDATE_ADJUDICATION_RAW_BACKING_V1", executed)

    def test_zero_references_reject_for_finalized(self):
        """FINALIZED still requires all three authorized predicates."""
        context, published, _root, _seal, identities = self.build()
        envelope = self.envelope(context, published, identities, predicates=[])
        # The frozen schema keeps minItems 1 for every outcome but ABORTED_SAFE,
        # so an empty FINALIZED envelope is refused before the dispatcher runs.
        with self.assertRaises(Exception) as caught:
            self.policy.structural_validate(envelope)
        self.assertEqual(str(caught.exception), "STRUCTURAL_SCHEMA_INVALID")
        result = self.policy.full_admissibility(envelope, context)
        self.assertFalse(result.valid)
        self.assertEqual(result.failure_code, "STRUCTURAL_SCHEMA_INVALID")

    def test_zero_references_reject_for_restoration_blocked_that_authorized(self):
        context, published, _root, _seal, identities = self.build("RESTORATION_BLOCKED")
        self.assertTrue(dict(context.evaluation_authorization_contexts))
        envelope = self.envelope(context, published, identities, predicates=[])
        with self.assertRaises(Exception) as caught:
            self.policy.structural_validate(envelope)
        self.assertEqual(str(caught.exception), "STRUCTURAL_SCHEMA_INVALID")

    def test_partially_empty_closure_rejects(self):
        """Emptying only one of the two arrays is never a closure."""
        context, published, _root, _seal, identities = self.build()
        thin = self.envelope(context, published, identities)
        thin["adjudication_references"] = []
        with self.assertRaises(Exception):
            self.policy.structural_validate(thin)
        aborted_context, aborted_published, _r, _s, aborted_identities = self.build(
            "ABORTED_SAFE")
        half = self.envelope(
            aborted_context, aborted_published, aborted_identities, predicates=[])
        # A raw reference with no adjudication behind it: schema-valid for
        # ABORTED_SAFE, and still refused by the closure.
        # An ABORTED_SAFE attempt retained no raw evidence at all, so any raw
        # reference it could cite is necessarily foreign and unresolvable.
        donor_context, donor_published, _r2, _s2, _i2 = self.build()
        half["raw_evidence_references"] = [
            donor_published["INITIAL_INVARIANT_EVALUATION"]["cited"][0].as_dict()]
        self.policy.structural_validate(half)
        result = self.policy.full_admissibility(half, aborted_context)
        self.assertFalse(result.valid)
        self.assertEqual(result.hook_id, "VALIDATE_EVIDENCE_REF_HASH_PATH_ID_V1")
        self.assertEqual(result.failure_code, "EVIDENCE_REFERENCE_UNRESOLVED")

    # ---- retained run-identity binding (correction C) ----------------------

    def identity_mutation(self, mutate, *, outcome="FINALIZED"):
        """Mutate the envelope's embedded identities and dispatch it."""
        context, published, _root, _seal, identities = self.build(outcome)
        envelope = self.envelope(context, published, identities)
        mutate(envelope["run_identities"], context, identities)
        return envelope, context

    def assert_identity_rejects(self, mutate, *, structural=True, outcome="FINALIZED"):
        envelope, context = self.identity_mutation(mutate, outcome=outcome)
        if structural:
            self.policy.structural_validate(envelope)
        result = self.policy.full_admissibility(envelope, context)
        self.assertFalse(result.valid)
        self.assertIn(
            result.failure_code,
            set(self.policy.policy["full_admissibility_validation"]["failure_codes"]),
            "the failure code must be in this policy's own vocabulary",
        )
        return result

    def test_changed_start_monotonic_time_rejects(self):
        result = self.assert_identity_rejects(
            lambda rows, ctx, ids: rows[0].__setitem__("monotonic_ns", 987654321))
        self.assertEqual(result.hook_id, "VALIDATE_ATTEMPT_PHASES_AND_FINALITY_V1")
        self.assertEqual(result.failure_code, "ATTEMPT_FINALITY_INVALID")

    def test_changed_terminal_monotonic_time_rejects(self):
        result = self.assert_identity_rejects(
            lambda rows, ctx, ids: rows[1].__setitem__("monotonic_ns", 0))
        self.assertEqual(result.failure_code, "ATTEMPT_FINALITY_INVALID")

    def test_changed_runner_release_hash_rejects(self):
        def mutate(rows, ctx, ids):
            rows[0]["runner_release_binding"] = dict(
                rows[0]["runner_release_binding"], bundle_sha256="9" * 64)

        result = self.assert_identity_rejects(mutate)
        self.assertEqual(result.failure_code, "ATTEMPT_FINALITY_INVALID")

    def test_changed_contract_or_profile_binding_rejects(self):
        def contract(rows, ctx, ids):
            rows[0]["contract_binding"] = dict(rows[0]["contract_binding"], tree="0" * 40)

        def profile(rows, ctx, ids):
            rows[0]["execution_profile_binding"] = dict(
                rows[0]["execution_profile_binding"], commit="1" * 40)

        for mutate in (contract, profile):
            # These break the closed START schema's consts, so they never even
            # reach the dispatcher -- which is a stronger refusal, not a weaker.
            envelope, context = self.identity_mutation(mutate)
            with self.assertRaises(Exception) as caught:
                self.policy.structural_validate(envelope)
            self.assertEqual(str(caught.exception), "STRUCTURAL_SCHEMA_INVALID")
            result = self.policy.full_admissibility(envelope, context)
            self.assertFalse(result.valid)

    def test_changed_start_identity_reference_rejects(self):
        def mutate(rows, ctx, ids):
            reference = dict(rows[1]["start_identity_reference"])
            digest = "a" * 64
            reference.update({
                "evidence_id": "ev-" + digest[:32], "descriptor_sha256": digest,
                "descriptor_relative_path":
                    f"descriptors/sha256/{digest[:2]}/{digest}.json"})
            rows[1]["start_identity_reference"] = reference

        result = self.assert_identity_rejects(mutate)
        # An unresolvable reference is caught at the reference hook, which runs
        # before finality; either way the envelope is refused.
        self.assertIn(result.failure_code,
                      {"ATTEMPT_FINALITY_INVALID", "EVIDENCE_REFERENCE_UNRESOLVED"})

    def test_identity_substituted_from_another_attempt_rejects(self):
        """A schema-valid identity that this attempt never retained.

        The START record is content addressed and identical across attempts with
        the same run and attempt, so the substitutable one is TERMINAL: the donor
        attempt ended RESTORATION_BLOCKED and cites its own START.
        """
        _dc, _dp, _dr, _ds, donor_identities = self.build("RESTORATION_BLOCKED")
        donor_terminal = _plain(donor_identities["documents"][1])
        context, published, _root, _seal, identities = self.build()
        envelope = self.envelope(context, published, identities)
        self.assertNotEqual(donor_terminal, envelope["run_identities"][1])
        envelope["run_identities"][1] = donor_terminal
        self.policy.structural_validate(envelope)
        result = self.policy.full_admissibility(envelope, context)
        self.assertFalse(result.valid)
        self.assertEqual(result.hook_id, "VALIDATE_ATTEMPT_PHASES_AND_FINALITY_V1")
        self.assertEqual(result.failure_code, "ATTEMPT_FINALITY_INVALID")

    def test_swapped_start_and_terminal_rejects(self):
        envelope, context = self.identity_mutation(
            lambda rows, ctx, ids: rows.reverse())
        with self.assertRaises(Exception) as caught:
            self.policy.structural_validate(envelope)
        self.assertEqual(str(caught.exception), "STRUCTURAL_SCHEMA_INVALID")
        self.assertFalse(self.policy.full_admissibility(envelope, context).valid)

    def test_duplicate_start_or_terminal_identity_rejects(self):
        for index, other in ((0, 1), (1, 0)):
            envelope, context = self.identity_mutation(
                lambda rows, ctx, ids, i=index, o=other: rows.__setitem__(o, rows[i]))
            with self.assertRaises(Exception) as caught:
                self.policy.structural_validate(envelope)
            self.assertEqual(str(caught.exception), "STRUCTURAL_SCHEMA_INVALID")
            self.assertFalse(self.policy.full_admissibility(envelope, context).valid)

    def test_retained_identities_are_exactly_one_start_and_one_terminal(self):
        context, _published, _root, _seal, _identities = self.build()
        rows = [row for row in context.evidence.values()
                if row.reference.role == "run_identity"]
        self.assertEqual(len(rows), 2)
        phases = sorted(row.descriptor["phase"] for row in rows)
        self.assertEqual(phases, ["START", "TERMINAL"])
        start = next(r for r in rows if r.descriptor["phase"] == "START")
        terminal = next(r for r in rows if r.descriptor["phase"] == "TERMINAL")
        self.assertLess(start.descriptor["monotonic_ns"], terminal.descriptor["monotonic_ns"])
        self.assertEqual(
            _plain(terminal.descriptor["start_identity_reference"]),
            start.reference.as_dict())
        self.assertEqual(terminal.descriptor["terminal_outcome"], context.terminal_outcome)

    # ---- authenticated resolution adversaries ------------------------------

    def resolve_tampered(self, mutate):
        from sremut.evidence import ExternalAnchor
        from sremut.resolved_context import resolve_evidence_context

        W = workload_tests
        _context, published, root, seal, _identities = self.build()
        mutate(root, published)
        anchor = ExternalAnchor("v1-2-conformance-attempt", seal.manifest_relative_path,
                                seal.manifest_sha256)
        with self.assertRaises(Exception) as caught:
            resolve_evidence_context(
                self.policy, root, W.RUN_ID, W.ATTEMPT_ID, anchor,
                expected_terminal_manifest_identity=seal.manifest_sha256,
            )
        return str(caught.exception)

    def test_offline_seal_absent_rejects_authenticated_resolution(self):
        from sremut.resolved_context import resolve_evidence_context

        W = workload_tests
        _context, _published, root, seal, _identities = self.build()
        for anchor in (None, object(), {"terminal_manifest_sha256": seal.manifest_sha256}):
            with self.assertRaises(Exception) as caught:
                resolve_evidence_context(
                    self.policy, root, W.RUN_ID, W.ATTEMPT_ID, anchor,
                    expected_terminal_manifest_identity=seal.manifest_sha256,
                )
            self.assertEqual(str(caught.exception), "EXTERNAL_SEAL_MISSING")

    def test_tampered_payload_bytes_reject(self):
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

    def test_forged_descriptor_never_reaches_the_binding_rule(self):
        context, published, _root, _seal, _identities = self.build()
        reference = published["INITIAL_INVARIANT_EVALUATION"]["adjudication"]
        donor = published["RESTORATION_POSITIVE_CONTROL"]["adjudication"]
        forged = deepcopy(self.descriptor_of(context, reference))
        forged["workload_window_adjudication_identity"] = self.descriptor_of(
            context, donor)["workload_window_adjudication_identity"]
        self.assert_rejects(
            forged, context, "EVIDENCE_REFERENCE_UNRESOLVED",
            hook="VALIDATE_DESCRIPTOR_CONTENT_IDENTITY_V1")

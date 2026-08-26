"""Offline lifecycle tests for the closed MS-M01 attempt coordinator."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from sremut.evidence import EvidenceStore, ExternalAnchor, revalidate_sealed_attempt
from sremut.journal import Journal
from sremut.kubernetes_mutation import (
    KubernetesMutationError,
    MutationObservation,
    ObservedEffect,
    ReconciliationResult,
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
    WorkloadWindowResult,
)
from sremut.policy_runtime import (
    EXPECTED_HOOK_ORDER,
    POLICY_MANIFEST_SHA256,
    load_policy_bundle,
)
from sremut.workload_evidence import (
    WorkloadHistoryEntry,
    prepare_workload_window,
    recompute_stream_identity,
)
import tests.test_resolved_context as context_tests
import tests.test_workload_evidence as workload_tests


ROOT = Path(__file__).resolve().parents[1]
RUN_ID = "sremut-ms-m01-r01-a01-abcdef123456"
ATTEMPT_ID = "a01"
UTC = "2026-08-20T10:00:00.123456789Z"
BOOT = "123e4567-e89b-12d3-a456-426614174000"
REGISTRY = "sremut/missing-service-social-network/pilot-mutants-v1"
INVARIANTS = ("MS-I1", "MS-I2", "MS-I3", "MS-I4", "MS-I5", "MS-I6")


def load_policy():
    return load_policy_bundle(
        ROOT / "policies/missing_service_social_network/evidence-capture-v1.1.yaml",
        ROOT / "schemas/evidence-capture-policy-v1.1.schema.json",
        ROOT / "EVIDENCE_CAPTURE_POLICY_V1_1_SHA256SUMS",
        expected_manifest_sha256=POLICY_MANIFEST_SHA256,
    )


def fake_adjudication_ref():
    digest = "1" * 64
    return {
        "document_type": "DESCRIPTOR_EVIDENCE_REF_V1",
        "schema_version": 1,
        "evidence_id": "ev-" + digest[:32],
        "role": "adjudication",
        "producer": "ADJUDICATOR",
        "source_kind": "GENERATED_DESCRIPTOR",
        "media_type": "application/json",
        "storage_class": "DESCRIPTOR_ONLY",
        "descriptor_sha256": digest,
        "descriptor_size_bytes": 1,
        "descriptor_relative_path": f"descriptors/sha256/{digest[:2]}/{digest}.json",
        "redaction_status": "NOT_REDACTED",
    }


def fake_pod_ref():
    digest = "2" * 64
    return {
        "document_type": "PAYLOAD_EVIDENCE_REF_V1",
        "schema_version": 1,
        "evidence_id": "ev-" + digest[:32],
        "role": "kubernetes_object_projection",
        "producer": "RUNNER_KUBERNETES_CLIENT",
        "source_kind": "KUBERNETES",
        "media_type": "application/json",
        "storage_class": "PAYLOAD_WITH_DESCRIPTOR",
        "descriptor_sha256": digest,
        "descriptor_size_bytes": 1,
        "descriptor_relative_path": f"descriptors/sha256/{digest[:2]}/{digest}.json",
        "redaction_status": "NOT_REDACTED",
        "payload_sha256": "3" * 64,
        "payload_size_bytes": 1,
        "payload_relative_path": "objects/sha256/33/" + "3" * 64,
        "projection_class": "KUBERNETES_OBJECT_PROJECTION_V1",
    }


def wrk_log(number: int, failure: bool = False) -> str:
    rows = [
        "-----------------------------------",
        f"  {number} requests in 10.00s, 1.00KB read",
    ]
    if failure:
        rows.append(f"Non-2xx or 3xx responses: {number}")
    rows.extend(("Requests/sec: 5.00", "Transfer/sec: 1.00KB"))
    return "\n".join(rows)


class Authority:
    def __init__(self):
        self.claimed = set()
        self.stopped = False

    def global_stop_active(self):
        return self.stopped

    def claim_once(self, identity, *, resume):
        key = (identity.run_id, identity.attempt_id)
        if key in self.claimed:
            raise MsM01AttemptError("ATTEMPT_REPLAY_FORBIDDEN")
        self.claimed.add(key)


class RealState:
    def __init__(self, root, policy):
        self.root = root
        self.policy = policy
        self.clock = 0
        with Journal(root, policy, RUN_ID, ATTEMPT_ID) as journal:
            for transition in (
                "CREATED->PREFLIGHT_PASS",
                "PREFLIGHT_PASS->HEALTHY_STATE_CAPTURED",
            ):
                self.clock += 1
                journal.append_state_transition(
                    transition,
                    utc_time=UTC,
                    monotonic_ns=self.clock,
                    boot_identity=BOOT,
                )

    def current_state(self):
        with Journal(self.root, self.policy, RUN_ID, ATTEMPT_ID) as journal:
            return journal.reconstruct().state

    def transition(self, transition, *, evidence_references):
        del evidence_references
        self.clock += 1
        with Journal(self.root, self.policy, RUN_ID, ATTEMPT_ID) as journal:
            journal.append_state_transition(
                transition,
                utc_time=UTC,
                monotonic_ns=self.clock,
                boot_identity=BOOT,
            )


class SkippingState(RealState):
    def transition(self, transition, *, evidence_references):
        if transition == "MUTANT_INJECTED->MUTANT_STATE_VERIFIED":
            return
        super().transition(transition, evidence_references=evidence_references)


class Prestate:
    def __init__(self, identity):
        self.identity = identity

    def authenticate(self, policy, identity):
        if identity != self.identity:
            raise MsM01AttemptError("HEALTHY_PRESTATE_AUTHENTICATION_FAILED")
        return HealthyPrestate(
            policy_manifest_sha256=policy.manifest_sha256,
            run_id=identity.run_id,
            attempt_id=identity.attempt_id,
            namespace="social-network",
            service_name="user-service",
            service_uid="service-uid",
            service_resource_version="10",
            captured_replica_baseline={"user-service": 1},
            workload_stream_identity=recompute_stream_identity(
                identity.run_id, identity.attempt_id
            ),
            restoration_capability=object(),
            evidence_references=("healthy-prestate",),
        )


class Mutations:
    def __init__(self, state):
        self.state = state
        self.calls = []
        self.delete_error = None
        self.challenge_error = False
        self.restore_error = False
        self.reconciliation = None

    def _guard(self):
        if self.state.current_state() in {
            "FINALIZED",
            "ABORTED_SAFE",
            "RESTORATION_BLOCKED",
        }:
            raise KubernetesMutationError("POST_TERMINAL_OPERATION")

    def _dispatch(self, kind, *, challenge=False):
        self._guard()
        self.calls.append(kind)
        values = {"operation_kind": kind}
        if challenge:
            values["challenge_target"] = object()
        return SimpleNamespace(**values)

    def delete_user_service(self, restoration):
        del restoration
        result = self._dispatch("INITIAL_USER_SERVICE_DELETION")
        if self.delete_error:
            raise KubernetesMutationError(self.delete_error, result)
        return result

    def reconcile_pending(self, operation_id, operation_kind, captured_uid, observation):
        del operation_id
        return self.reconcile(operation_kind, captured_uid, observation)

    def reconcile(self, operation_kind, captured_uid, observation):
        del operation_kind
        if self.reconciliation is not None:
            return ReconciliationResult(self.reconciliation, False, False)
        if not observation.available:
            effect = ObservedEffect.OUTCOME_UNKNOWN
        elif not observation.present:
            effect = ObservedEffect.OBSERVED_APPLIED_AFTER_RECOVERY
        elif observation.uid == captured_uid:
            effect = ObservedEffect.OBSERVED_NOT_APPLIED_AFTER_RECOVERY
        else:
            effect = ObservedEffect.CONFLICTING_STATE
        return ReconciliationResult(effect, False, effect is ObservedEffect.OBSERVED_APPLIED_AFTER_RECOVERY)

    def create_challenge_pod(self):
        self._guard()
        if self.challenge_error:
            raise KubernetesMutationError("KUBERNETES_MUTATION_API_EXCEPTION")
        return self._dispatch("CHALLENGE_POD_CREATION", challenge=True)

    def delete_replacement_pod(self, target):
        del target
        return self._dispatch("REPLACEMENT_POD_DELETION")

    def delete_challenge_pod(self, target):
        del target
        return self._dispatch("CHALLENGE_POD_DELETION")

    def restore_user_service(self, restoration):
        del restoration
        if self.restore_error:
            raise KubernetesMutationError("RESTORATION_BODY_MISMATCH")
        return self._dispatch("RESTORED_SERVICE_CREATION")


class Observations:
    def __init__(self):
        self.initial = MutationObservation(True, False, None)
        self.absent = True
        self.replacement_uid = "replacement-uid"
        self.replacement_owner = True
        self.replacement_capacity = True
        self.restored = True

    def observe_initial_service_deletion(self, dispatch):
        del dispatch
        return self.initial

    def service_structurally_absent(self):
        return self.absent

    def select_replacement_pod(self, prestate):
        del prestate
        return CapturedPod(
            "user-service-a",
            "social-network",
            "captured-uid",
            "20",
            True,
            "user-service",
            object(),
            ("captured-pod",),
        )

    def observe_replacement(self, captured, dispatch):
        del dispatch
        return ReplacementObservation(
            captured.uid,
            "user-service-b",
            self.replacement_uid,
            True,
            self.replacement_owner,
            self.replacement_capacity,
            ("replacement-pod",),
        )

    def verify_restoration(self, prestate):
        del prestate
        return RestorationVerification(
            self.restored,
            self.restored,
            self.restored,
            ("restoration-verification",),
        )


class Oracle:
    def __init__(self):
        self.boolean = True
        self.classification = None
        self.calls = 0

    def invoke_once(self, prestate, identity):
        del prestate, identity
        self.calls += 1
        if self.classification:
            return OriginalOracleOutcome(
                None, None, None, self.classification, ("oracle-failure",)
            )
        return OriginalOracleOutcome(
            {"success": self.boolean},
            self.boolean,
            0,
            "RETURNED_TRUE" if self.boolean else "RETURNED_FALSE",
            ("oracle-result",),
        )


class Challenges:
    def __init__(self):
        self.fail_phase = None
        self.calls = []

    def execute(self, phase, ordinal, challenge_target):
        del challenge_target
        self.calls.append(phase)
        failed = self.fail_phase == phase
        return ChallengeRun(
            phase,
            ordinal,
            not failed,
            "HARNESS_TIMING_FAILURE" if failed else None,
            (f"challenge:{phase}",),
        )


class Workloads:
    def __init__(self, policy):
        self.policy = policy
        self.requests = {}
        self.failures = set()
        self.substitute_phase = None
        self.calls = []

    def evaluate(self, phase, ordinal, challenge, prestate):
        del challenge
        self.calls.append(phase)
        request_count = self.requests.get(phase, 50)
        failure = phase in self.failures
        before = (
            WorkloadHistoryEntry(1.0, 1, wrk_log(1), True),
        )
        after = before + (
            WorkloadHistoryEntry(2.0, request_count, wrk_log(request_count, failure), not failure),
        )
        plan = prepare_workload_window(
            self.policy,
            before=before,
            after=after,
            phase=phase,
            ordinal=ordinal,
            run_id=RUN_ID,
            attempt_id=ATTEMPT_ID,
            mutant_id="MS-M01",
            repetition=1,
            workload_pod_projection_reference=fake_pod_ref(),
            pod_name="user-service-a",
            pod_uid="captured-uid",
            container_restart_count=0,
        )
        observed_phase = self.substitute_phase or phase
        observed_ordinal = {
            "INITIAL_MUTANT_CHALLENGE": 1,
            "POST_REPLACEMENT_PERSISTENCE": 2,
            "RESTORATION_POSITIVE_CONTROL": 3,
        }[observed_phase]
        return WorkloadWindowResult(
            observed_phase,
            observed_ordinal,
            RUN_ID,
            ATTEMPT_ID,
            plan.window["stream_identity"],
            plan.fresh_request_count,
            plan.failure_marker_count,
            (f"workload:{phase}",),
        )


class Adjudicator:
    def __init__(self):
        self.verdict = "REJECT"
        self.raw_backing = True
        self.calls = 0

    def evaluate(
        self,
        original,
        initial_challenge,
        initial_workload,
        captured,
        replacement,
        replacement_challenge,
        replacement_workload,
    ):
        self.calls += 1
        references = (
            *original.evidence_references,
            *initial_challenge.evidence_references,
            *initial_workload.evidence_references,
            *captured.evidence_references,
            *replacement.evidence_references,
        )
        if replacement_challenge is not None:
            references += replacement_challenge.evidence_references
        if replacement_workload is not None:
            references += replacement_workload.evidence_references
        if not self.raw_backing:
            references = ()
        if self.verdict == "PASS":
            outcomes = {name: "PASS" for name in INVARIANTS}
        else:
            outcomes = {
                name: ("PASS" if name == "MS-I5" else "REJECT")
                for name in INVARIANTS
            }
        return ContractEvaluation(
            self.verdict,
            outcomes,
            tuple(references),
            fake_adjudication_ref(),
        )


class Terminalizer:
    def __init__(self, root, policy, state, authority, mutations, *, full=False):
        self.root = root
        self.policy = policy
        self.state = state
        self.authority = authority
        self.mutations = mutations
        self.full = full
        self.seal = None
        self.anchor = None
        self.verification = None
        self.calls = 0

    def _global_stop(self):
        return {
            "document_type": "TERMINAL_GLOBAL_STOP_V1",
            "schema_version": 1,
            "run_id": RUN_ID,
            "attempt_id": ATTEMPT_ID,
            "terminal_outcome": "RESTORATION_BLOCKED",
            "reason_code": "RESTORATION_VERIFICATION_FAILED",
            "adjudication_reference": fake_adjudication_ref(),
            "created_utc": UTC,
            "monotonic_ns": 99,
            "boot_identity": BOOT,
        }

    def finalize(self, draft):
        self.calls += 1
        with EvidenceStore(self.root, self.policy, RUN_ID, ATTEMPT_ID) as store:
            stop = None
            if draft.terminal_outcome is TerminalOutcome.RESTORATION_BLOCKED:
                stop = store.write_global_stop(self._global_stop())
                self.authority.stopped = True
            self.seal = store.seal(
                draft.terminal_outcome.value,
                global_stop_relative_path=stop,
            )
        self.anchor = ExternalAnchor(
            "ms-m01-attempt-root",
            self.seal.manifest_relative_path,
            self.seal.manifest_sha256,
        )
        self.verification = revalidate_sealed_attempt(
            self.root, self.policy, RUN_ID, ATTEMPT_ID, self.anchor
        )
        full = False
        hooks = ()
        if self.full:
            workload_tests.WorkloadResolvedHookTests.setUpClass()
            owner = workload_tests.WorkloadResolvedHookTests(
                methodName="test_both_workload_hooks_pass_through_public_dispatcher"
            )
            context, candidate = owner.build_context()
            validation = owner.policy.full_admissibility(candidate, context)
            owner.doCleanups()
            full = validation.valid
            hooks = EXPECTED_HOOK_ORDER if validation.valid else ()
        publication_rejected = False
        try:
            with EvidenceStore(self.root, self.policy, RUN_ID, ATTEMPT_ID) as store:
                store.publish_descriptor(
                    "challenge_invocation",
                    {
                        "run_id": RUN_ID,
                        "attempt_id": ATTEMPT_ID,
                        "created_utc": UTC,
                        "monotonic_ns": 100,
                        "boot_identity": BOOT,
                        "template_id": "CHALLENGE_TCP_V1",
                        "parameters": {
                            "host": "user-service.social-network.svc.cluster.local",
                            "port": 9090,
                        },
                        "pod_name": "sremut-challenge-fixed",
                        "pod_uid": "11111111-1111-4111-8111-111111111111",
                    },
                )
        except Exception as error:
            publication_rejected = str(error) == "ATTEMPT_ALREADY_SEALED"
        transition_rejected = False
        try:
            self.state.transition("FINALIZED->FINALIZED", evidence_references=())
        except Exception as error:
            transition_rejected = str(error) == "POST_TERMINAL_OPERATION"
        mutation_rejected = False
        try:
            self.mutations.create_challenge_pod()
        except Exception as error:
            mutation_rejected = str(error) == "POST_TERMINAL_OPERATION"
        return TerminalizationResult(
            draft.terminal_outcome,
            1,
            1,
            True,
            full,
            hooks,
            draft.terminal_outcome is TerminalOutcome.RESTORATION_BLOCKED,
            publication_rejected,
            transition_rejected,
            mutation_rejected,
        )


class MsM01AttemptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.policy = load_policy()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="sremut-ms-m01-test-")
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def build(self, *, full=False, pending=None, state_type=RealState):
        profile = self.policy.policy["bindings"]["execution_profile"]
        contract = self.policy.policy["bindings"]["contract"]
        identity = AttemptIdentity(
            RUN_ID,
            ATTEMPT_ID,
            "MS-M01",
            1,
            1,
            REGISTRY,
            profile["tag_object"],
            profile["sha256"],
            contract["sha256"],
        )
        state = state_type(self.root, self.policy)
        authority = Authority()
        mutations = Mutations(state)
        observations = Observations()
        oracle = Oracle()
        challenge = Challenges()
        workload = Workloads(self.policy)
        adjudication = Adjudicator()
        terminalizer = Terminalizer(
            self.root,
            self.policy,
            state,
            authority,
            mutations,
            full=full,
        )
        capabilities = AttemptCapabilities(
            authority,
            Prestate(identity),
            state,
            mutations,
            observations,
            oracle,
            challenge,
            workload,
            adjudication,
            terminalizer,
        )
        attempt = MsM01Attempt(
            policy=self.policy,
            identity=identity,
            capabilities=capabilities,
            pending_initial_deletion=pending,
        )
        return SimpleNamespace(
            attempt=attempt,
            identity=identity,
            state=state,
            authority=authority,
            mutations=mutations,
            observations=observations,
            oracle=oracle,
            challenge=challenge,
            workload=workload,
            adjudication=adjudication,
            terminalizer=terminalizer,
        )

    def test_complete_expected_blind_spot_is_full_and_ordered(self):
        case = self.build(full=True)
        result = case.attempt.run()
        self.assertEqual(result.terminal_outcome, TerminalOutcome.FINALIZED)
        self.assertEqual(result.original_oracle_verdict, "PASS")
        self.assertEqual(result.contract_verdict, "REJECT")
        self.assertEqual(result.failure_classification, "CONTRACT_REJECT")
        self.assertTrue(result.terminalization.full_admissibility)
        self.assertEqual(result.terminalization.hook_outcomes, EXPECTED_HOOK_ORDER)
        self.assertEqual(case.oracle.calls, 1)
        self.assertLess(
            result.trace.index("ORIGINAL_ORACLE_INVOKED"),
            result.trace.index("CHALLENGE_POD_CREATION"),
        )
        self.assertEqual(
            dict(result.operation_counts),
            {
                "INITIAL_USER_SERVICE_DELETION": 1,
                "ORIGINAL_ORACLE_INVOCATION": 1,
                "CHALLENGE_POD_CREATION": 1,
                "INITIAL_CHALLENGE_EXECUTION": 1,
                "REPLACEMENT_POD_DELETION": 1,
                "POST_REPLACEMENT_CHALLENGE_EXECUTION": 1,
                "CHALLENGE_POD_DELETION": 1,
                "RESTORED_SERVICE_CREATION": 1,
            },
        )
        self.assertEqual(case.terminalizer.verification.classification, "COMPLETE")

    def test_scientific_outcome_pairs_remain_independent(self):
        for oracle_value, contract_value in (
            (True, "PASS"),
            (False, "REJECT"),
            (False, "PASS"),
        ):
            with self.subTest(oracle=oracle_value, contract=contract_value):
                nested = tempfile.TemporaryDirectory(prefix="sremut-ms-m01-pair-")
                old = self.root
                self.root = Path(nested.name)
                try:
                    case = self.build()
                    case.oracle.boolean = oracle_value
                    case.adjudication.verdict = contract_value
                    result = case.attempt.run()
                    self.assertEqual(
                        result.original_oracle_verdict,
                        "PASS" if oracle_value else "REJECT",
                    )
                    self.assertEqual(result.contract_verdict, contract_value)
                finally:
                    self.root = old
                    nested.cleanup()

    def test_acknowledgement_is_not_observed_effect(self):
        case = self.build()
        case.observations.initial = MutationObservation(True, True, "service-uid")
        result = case.attempt.run()
        self.assertEqual(result.terminal_outcome, TerminalOutcome.ABORTED_SAFE)
        self.assertEqual(case.oracle.calls, 0)
        self.assertNotIn("CHALLENGE_POD_CREATION", case.mutations.calls)

    def test_unknown_or_conflicting_effect_blocks_without_retry(self):
        for effect in (ObservedEffect.OUTCOME_UNKNOWN, ObservedEffect.CONFLICTING_STATE):
            with self.subTest(effect=effect):
                nested = tempfile.TemporaryDirectory(prefix="sremut-ms-m01-unknown-")
                old = self.root
                self.root = Path(nested.name)
                try:
                    case = self.build()
                    case.mutations.reconciliation = effect
                    result = case.attempt.run()
                    self.assertEqual(
                        result.terminal_outcome, TerminalOutcome.RESTORATION_BLOCKED
                    )
                    self.assertEqual(
                        case.mutations.calls.count("INITIAL_USER_SERVICE_DELETION"), 1
                    )
                    self.assertNotIn(
                        "RESTORED_SERVICE_CREATION", case.mutations.calls
                    )
                    self.assertTrue(result.terminalization.global_stop_created)
                finally:
                    self.root = old
                    nested.cleanup()

    def test_timeout_with_observed_applied_continues_without_redispatch(self):
        case = self.build()
        case.mutations.delete_error = "KUBERNETES_MUTATION_TIMEOUT"
        result = case.attempt.run()
        self.assertEqual(result.terminal_outcome, TerminalOutcome.FINALIZED)
        self.assertEqual(
            case.mutations.calls.count("INITIAL_USER_SERVICE_DELETION"), 1
        )
        self.assertIn(
            "INITIAL_EFFECT:OBSERVED_APPLIED_AFTER_RECOVERY", result.trace
        )

    def test_pending_intent_recovers_applied_without_redispatch(self):
        pending = PendingMutation(
            "INITIAL_USER_SERVICE_DELETION:pending", "INITIAL_USER_SERVICE_DELETION", "service-uid"
        )
        case = self.build(pending=pending)
        result = case.attempt.run()
        self.assertEqual(result.terminal_outcome, TerminalOutcome.FINALIZED)
        self.assertNotIn("INITIAL_USER_SERVICE_DELETION", case.mutations.calls)
        self.assertIn("INITIAL_USER_SERVICE_DELETION_RECOVERED", result.trace)

    def test_challenge_creation_and_execution_failures_restore(self):
        case = self.build()
        case.mutations.challenge_error = True
        result = case.attempt.run()
        self.assertEqual(result.terminal_outcome, TerminalOutcome.FINALIZED)
        self.assertEqual(result.failure_classification, "HARNESS_TIMING_FAILURE")
        nested = tempfile.TemporaryDirectory(prefix="sremut-ms-m01-challenge-")
        old = self.root
        self.root = Path(nested.name)
        try:
            case = self.build()
            case.challenge.fail_phase = "INITIAL_MUTANT_CHALLENGE"
            result = case.attempt.run()
            self.assertEqual(result.terminal_outcome, TerminalOutcome.FINALIZED)
            self.assertIn("RESTORATION_POSITIVE_CONTROL", case.workload.calls)
        finally:
            self.root = old
            nested.cleanup()

    def test_workload_failures_are_preserved_in_contract_result(self):
        for requests, failure in ((50, True), (49, False)):
            with self.subTest(requests=requests, failure=failure):
                nested = tempfile.TemporaryDirectory(prefix="sremut-ms-m01-workload-")
                old = self.root
                self.root = Path(nested.name)
                try:
                    case = self.build()
                    case.workload.requests["INITIAL_MUTANT_CHALLENGE"] = requests
                    if failure:
                        case.workload.failures.add("INITIAL_MUTANT_CHALLENGE")
                    result = case.attempt.run()
                    self.assertEqual(result.contract_verdict, "REJECT")
                    self.assertEqual(result.terminal_outcome, TerminalOutcome.FINALIZED)
                finally:
                    self.root = old
                    nested.cleanup()

    def test_cross_window_substitution_restores_fail_closed(self):
        case = self.build()
        case.workload.substitute_phase = "POST_REPLACEMENT_PERSISTENCE"
        result = case.attempt.run()
        self.assertEqual(result.failure_classification, "RESTORATION_FAILURE")
        self.assertEqual(result.contract_verdict, "INCOMPLETE")
        self.assertEqual(result.terminal_outcome, TerminalOutcome.RESTORATION_BLOCKED)
        self.assertTrue(result.terminalization.global_stop_created)

    def test_replacement_uid_ownership_and_capacity_are_raw_inputs(self):
        for attribute, value in (
            ("replacement_uid", "captured-uid"),
            ("replacement_owner", False),
            ("replacement_capacity", False),
        ):
            with self.subTest(attribute=attribute):
                nested = tempfile.TemporaryDirectory(prefix="sremut-ms-m01-replacement-")
                old = self.root
                self.root = Path(nested.name)
                try:
                    case = self.build()
                    setattr(case.observations, attribute, value)
                    result = case.attempt.run()
                    self.assertEqual(result.contract_verdict, "REJECT")
                    self.assertNotIn(
                        "POST_REPLACEMENT_PERSISTENCE", case.workload.calls
                    )
                finally:
                    self.root = old
                    nested.cleanup()

    def test_restoration_mismatch_and_positive_control_failure_block(self):
        case = self.build()
        case.mutations.restore_error = True
        result = case.attempt.run()
        self.assertEqual(result.terminal_outcome, TerminalOutcome.RESTORATION_BLOCKED)
        self.assertTrue(case.authority.stopped)
        self.assertEqual(case.terminalizer.verification.classification, "SEALED_PARTIAL_WITH_GLOBAL_STOP")
        nested = tempfile.TemporaryDirectory(prefix="sremut-ms-m01-positive-")
        old = self.root
        self.root = Path(nested.name)
        try:
            case = self.build()
            case.workload.requests["RESTORATION_POSITIVE_CONTROL"] = 49
            result = case.attempt.run()
            self.assertEqual(result.terminal_outcome, TerminalOutcome.RESTORATION_BLOCKED)
            self.assertTrue(result.terminalization.global_stop_created)
        finally:
            self.root = old
            nested.cleanup()

    def test_oracle_failure_is_not_boolean_rejection(self):
        case = self.build()
        case.oracle.classification = "TIMEOUT"
        result = case.attempt.run()
        self.assertEqual(result.original_oracle_verdict, "INTERRUPTED")
        self.assertEqual(result.contract_verdict, "NOT_EVALUATED")
        self.assertEqual(result.failure_classification, "HARNESS_TIMING_FAILURE")

    def test_replay_and_global_stop_reject_before_effect(self):
        case = self.build()
        case.attempt.run()
        with self.assertRaisesRegex(MsM01AttemptError, "ATTEMPT_REPLAY_FORBIDDEN"):
            case.attempt.run()
        nested = tempfile.TemporaryDirectory(prefix="sremut-ms-m01-stop-")
        old = self.root
        self.root = Path(nested.name)
        try:
            stopped = self.build()
            stopped.authority.stopped = True
            with self.assertRaisesRegex(MsM01AttemptError, "GLOBAL_STOP_ACTIVE"):
                stopped.attempt.run()
            self.assertEqual(stopped.mutations.calls, [])
        finally:
            self.root = old
            nested.cleanup()

    def test_post_terminal_publication_transition_and_mutation_reject(self):
        result = self.build().attempt.run()
        terminal = result.terminalization
        self.assertTrue(terminal.post_terminal_publication_rejected)
        self.assertTrue(terminal.post_terminal_transition_rejected)
        self.assertTrue(terminal.post_terminal_mutation_rejected)

    def test_invalid_or_skipped_state_transition_rejects(self):
        case = self.build(state_type=SkippingState)
        with self.assertRaisesRegex(MsM01AttemptError, "ATTEMPT_STATE_AUTHORITY_MISMATCH"):
            case.attempt.run()

    def test_missing_raw_backing_fails_before_terminalization(self):
        case = self.build()
        case.adjudication.raw_backing = False
        with self.assertRaisesRegex(MsM01AttemptError, "CONTRACT_ADJUDICATION_INVALID"):
            case.attempt.run()
        self.assertEqual(case.terminalizer.calls, 0)

    def test_tampered_descriptor_journal_and_anchor_reject(self):
        legacy = context_tests.ContextConstructionTests(
            methodName="test_valid_structurally_sealed_attempt_and_safe_repr"
        )
        legacy.setUpClass()
        legacy.setUp()
        try:
            _seal, anchor, references = legacy.seal(evidence_kind="input")
            descriptor = legacy.root / references[0].descriptor_relative_path
            descriptor.write_bytes(descriptor.read_bytes() + b" ")
            with self.assertRaises(Exception):
                revalidate_sealed_attempt(
                    legacy.root,
                    legacy.policy,
                    context_tests.RUN_ID,
                    context_tests.ATTEMPT_ID,
                    anchor,
                )
        finally:
            legacy.tearDown()
        case = self.build()
        case.attempt.run()
        bad = replace(case.terminalizer.anchor, terminal_manifest_sha256="f" * 64)
        with self.assertRaisesRegex(Exception, "EXTERNAL_MANIFEST_HASH_MISMATCH"):
            revalidate_sealed_attempt(self.root, self.policy, RUN_ID, ATTEMPT_ID, bad)
        journal = self.root / "journal/attempt.jsonl"
        journal.write_bytes(journal.read_bytes() + b"{}\n")
        with self.assertRaises(Exception):
            revalidate_sealed_attempt(
                self.root,
                self.policy,
                RUN_ID,
                ATTEMPT_ID,
                case.terminalizer.anchor,
            )

    def test_identity_and_capability_boundaries_are_closed(self):
        case = self.build()
        bad = replace(case.identity, registry_id="wrong")
        with self.assertRaisesRegex(MsM01AttemptError, "MS_M01_POLICY_OR_IDENTITY_INVALID"):
            MsM01Attempt(
                policy=self.policy,
                identity=bad,
                capabilities=case.attempt._capabilities,
            )
        with self.assertRaisesRegex(MsM01AttemptError, "ATTEMPT_CAPABILITY_MISSING"):
            AttemptCapabilities(
                object(),
                object(),
                object(),
                object(),
                object(),
                object(),
                object(),
                object(),
                object(),
                object(),
            )

    def test_module_has_no_live_construction_or_execution_surface(self):
        source = (ROOT / "src/sremut/ms_m01_attempt.py").read_text(encoding="utf-8")
        for forbidden in (
            "load_kube_config",
            "CoreV1Api",
            "AppsV1Api",
            "import subprocess",
            "import socket",
            "kubectl.",
            "StreamWorkloadManager(",
        ):
            self.assertNotIn(forbidden, source)

    def test_fourteen_orchestration_mutations_are_detected(self):
        source = (ROOT / "src/sremut/ms_m01_attempt.py").read_text(encoding="utf-8")

        def guards(value):
            return (
                value.count("ORIGINAL_ORACLE_INVOCATION") == 1,
                0 <= value.find('self._transition("ORIGINAL_ORACLE_EVALUATED"')
                < value.find('self._count("CHALLENGE_POD_CREATION")'),
                "OBSERVED_APPLIED_AFTER_RECOVERY" in value,
                "INITIAL_USER_SERVICE_DELETION_RECOVERED" in value
                and "reconcile_pending" in value,
                "self.replacement_uid != self.captured_uid" in value,
                "WORKLOAD_WINDOW_SUBSTITUTION" in value,
                "CONTRACT_ADJUDICATION_RAW_BACKING_INVALID" in value,
                value.count("return self._restore(") >= 9,
                '"RESTORATION_POSITIVE_CONTROL", 3' in value,
                'self._transition("RESTORATION_BLOCKED")' in value,
                'self._event("TERMINAL_SEALED_AND_ANCHORED")' in value,
                "post_terminal_mutation_rejected" in value,
                "EXPECTED_HOOK_ORDER" in value,
                "FROZEN_TRANSITIONS" in value
                and "ATTEMPT_STATE_AUTHORITY_MISMATCH" in value,
            )

        self.assertTrue(all(guards(source)))
        mutations = (
            source.replace(
                'self._count("ORIGINAL_ORACLE_INVOCATION")',
                'self._count("ORACLE_INVOCATION_MUTATED")',
            ),
            source.replace(
                'self._transition("ORIGINAL_ORACLE_EVALUATED", *original.evidence_references)',
                'self._event("ORIGINAL_ORACLE_PENDING")',
            ),
            source.replace(
                "ObservedEffect.OBSERVED_APPLIED_AFTER_RECOVERY",
                "ObservedEffect.ACKNOWLEDGED_APPLIED",
            ),
            source.replace(
                'self._event("INITIAL_USER_SERVICE_DELETION_RECOVERED")',
                'self._event("INITIAL_USER_SERVICE_DELETION_REDISPATCHED")',
            ),
            source.replace("self.replacement_uid != self.captured_uid", "True"),
            source.replace("WORKLOAD_WINDOW_SUBSTITUTION", "WORKLOAD_WINDOW_ACCEPTED"),
            source.replace(
                "CONTRACT_ADJUDICATION_RAW_BACKING_INVALID",
                "CONTRACT_ADJUDICATION_ACCEPTED",
            ),
            source.replace("return self._restore(", "return self._terminalize("),
            source.replace(
                '"RESTORATION_POSITIVE_CONTROL", 3', '"RESTORATION_SKIPPED", 3'
            ),
            source.replace(
                'self._transition("RESTORATION_BLOCKED")',
                'self._transition("ABORTED_SAFE")',
            ),
            source.replace(
                'self._event("TERMINAL_SEALED_AND_ANCHORED")',
                'self._event("TERMINAL_SEALED_EARLY")',
            ),
            source.replace(
                "post_terminal_mutation_rejected", "post_terminal_mutation_accepted"
            ),
            source.replace("EXPECTED_HOOK_ORDER", "()"),
            source.replace(
                "ATTEMPT_STATE_AUTHORITY_MISMATCH", "ATTEMPT_STATE_IGNORED"
            ),
        )
        for index, mutated in enumerate(mutations, 1):
            with self.subTest(mutation=index):
                observed = guards(mutated)
                self.assertFalse(observed[index - 1])


if __name__ == "__main__":
    unittest.main()

"""Offline tests for frozen-v1.1 workload evidence and semantic hooks."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
import hashlib
import math
from pathlib import Path
import tempfile
import unittest

from sremut.canonical_json import canonical_json_bytes, parse_canonical_json
from sremut.evidence import EvidenceStore, ExternalAnchor
from sremut.journal import Journal
from sremut.policy_runtime import EXPECTED_HOOK_ORDER, POLICY_MANIFEST_SHA256, load_policy_bundle
from sremut.resolved_context import CONNECTED_HOOKS, resolve_evidence_context
from sremut.workload_evidence import (
    POLICY_ID,
    WorkloadCaptureStamp,
    WorkloadEvidenceError,
    WorkloadHistoryEntry,
    binary64_time_identity,
    parse_historical_workload_log,
    parse_pinned_wrk2_round,
    parse_workload_jsonl,
    prepare_workload_window,
    recompute_stream_identity,
    serialize_workload_history,
)


REPOSITORY = Path(__file__).resolve().parents[1]
BASELINES = REPOSITORY.parent / "baselines/missing_service_social_network"
RUN_ID = "sremut-ms-m01-r01-a01-abcdef123456"
ATTEMPT_ID = "a01"
CREATED = "2026-08-20T10:00:00.123456789Z"
BOOT = "123e4567-e89b-12d3-a456-426614174000"


def load_policy():
    return load_policy_bundle(
        REPOSITORY / "policies/missing_service_social_network/evidence-capture-v1.1.yaml",
        REPOSITORY / "schemas/evidence-capture-policy-v1.1.schema.json",
        REPOSITORY / "EVIDENCE_CAPTURE_POLICY_V1_1_SHA256SUMS",
        expected_manifest_sha256=POLICY_MANIFEST_SHA256,
    )


def wrk_log(number=50, failure=False, complete=True):
    rows = [
        "-----------------------------------",
        f"  {number} requests in 10.00s, 1.00KB read",
    ]
    if failure:
        rows.append(f"Non-2xx or 3xx responses: {number}")
    rows.append("Requests/sec: 5.00")
    if complete:
        rows.append("Transfer/sec: 1.00KB")
    return "\n".join(rows)


def entry(time=1.0, number=50, failure=False, complete=True):
    return WorkloadHistoryEntry(time, number, wrk_log(number, failure, complete), not failure)


def fake_reference(role, *, digest="1" * 64):
    role_values = {
        "kubernetes_object_projection": (
            "PAYLOAD_EVIDENCE_REF_V1", "RUNNER_KUBERNETES_CLIENT", "KUBERNETES",
            "application/json", "PAYLOAD_WITH_DESCRIPTOR",
        ),
        "workload_log_bytes": (
            "PAYLOAD_EVIDENCE_REF_V1", "WORKLOAD_EVIDENCE_ADAPTER", "IN_PROCESS_WORKLOAD",
            "application/octet-stream", "PAYLOAD_WITH_DESCRIPTOR",
        ),
    }
    document, producer, source, media, storage = role_values[role]
    result = {
        "document_type": document,
        "schema_version": 1,
        "evidence_id": "ev-" + digest[:32],
        "role": role,
        "producer": producer,
        "source_kind": source,
        "media_type": media,
        "storage_class": storage,
        "descriptor_sha256": digest,
        "descriptor_size_bytes": 1,
        "descriptor_relative_path": f"descriptors/sha256/{digest[:2]}/{digest}.json",
        "redaction_status": "NOT_REDACTED",
        "payload_sha256": "2" * 64,
        "payload_size_bytes": 1,
        "payload_relative_path": "objects/sha256/22/" + "2" * 64,
    }
    if role == "kubernetes_object_projection":
        result["projection_class"] = "KUBERNETES_OBJECT_PROJECTION_V1"
    return result


def fake_request_reference():
    digest = "3" * 64
    return {
        "document_type": "DESCRIPTOR_EVIDENCE_REF_V1",
        "schema_version": 1,
        "evidence_id": "ev-" + digest[:32],
        "role": "kubernetes_request_identity",
        "producer": "RUNNER_KUBERNETES_CLIENT",
        "source_kind": "KUBERNETES",
        "media_type": "application/json",
        "storage_class": "DESCRIPTOR_ONLY",
        "descriptor_sha256": digest,
        "descriptor_size_bytes": 1,
        "descriptor_relative_path": f"descriptors/sha256/{digest[:2]}/{digest}.json",
        "redaction_status": "NOT_REDACTED",
    }


def stamp(number):
    return WorkloadCaptureStamp(CREATED, number, BOOT)


class WorkloadPureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.policy = load_policy()

    def assert_code(self, code, function, *args, **kwargs):
        with self.assertRaises(WorkloadEvidenceError) as caught:
            function(*args, **kwargs)
        self.assertEqual(str(caught.exception), code)

    def plan(self, before=(), after=None, **changes):
        values = {
            "policy": self.policy,
            "before": before,
            "after": (entry(),) if after is None else after,
            "phase": "INITIAL_MUTANT_CHALLENGE",
            "ordinal": 1,
            "run_id": RUN_ID,
            "attempt_id": ATTEMPT_ID,
            "mutant_id": "MS-M01",
            "repetition": 1,
            "workload_pod_projection_reference": fake_reference("kubernetes_object_projection"),
            "pod_name": "user-service-abc",
            "pod_uid": "pod-uid",
            "container_restart_count": 0,
        }
        values.update(changes)
        return prepare_workload_window(**values)

    def test_exact_jsonl_schema_canonical_bytes_and_embedded_newline(self):
        history = (WorkloadHistoryEntry(1.0, 50, wrk_log() + "\nembedded", True),)
        payload = serialize_workload_history(history)
        records = parse_workload_jsonl(payload)
        self.assertEqual(records[0].log, history[0].log)
        self.assertTrue(payload.endswith(b"\n"))
        self.assertEqual(payload.count(b"\n"), 1)
        self.assertEqual(serialize_workload_history(history), payload)

    def test_missing_extra_joined_missing_lf_blank_and_invalid_utf8_reject(self):
        valid = serialize_workload_history((entry(),))
        value = parse_canonical_json(valid, line=True)
        cases = [
            canonical_json_bytes({key: child for key, child in value.items() if key != "ok"}) + b"\n",
            canonical_json_bytes({**value, "extra": 1}) + b"\n",
            wrk_log().encode(),
            valid[:-1],
            valid + b"\n",
            b"\xff\n",
            b'{"index":0, "log":"x","number":1,"ok":true,"time_binary64_hex":"3ff0000000000000"}\n',
        ]
        for candidate in cases:
            with self.subTest(size=len(candidate)):
                self.assert_code("WORKLOAD_PARSE_RECOMPUTATION_MISMATCH", parse_workload_jsonl, candidate)

    def test_exact_binary64_identity_and_nonfinite_reject(self):
        self.assertEqual(binary64_time_identity(1.0), "3ff0000000000000")
        for value in (math.nan, math.inf, -math.inf, 1, True, "1"):
            with self.subTest(value=repr(value)):
                self.assert_code("WORKLOAD_TIMESTAMP_ORDER_INVALID", binary64_time_identity, value)

    def test_uppercase_malformed_and_nonfinite_hex_reject(self):
        base = parse_canonical_json(serialize_workload_history((entry(),)), line=True)
        for identity in ("3FF0000000000000", "0" * 15, "z" * 16, "7ff0000000000000"):
            changed = {**base, "time_binary64_hex": identity}
            with self.subTest(identity=identity):
                code = "WORKLOAD_TIMESTAMP_ORDER_INVALID" if identity == "7ff0000000000000" else "WORKLOAD_PARSE_RECOMPUTATION_MISMATCH"
                self.assert_code(code, parse_workload_jsonl, canonical_json_bytes(changed) + b"\n")

    def test_nonboolean_ok_and_noninteger_index_number_reject(self):
        base = parse_canonical_json(serialize_workload_history((entry(),)), line=True)
        for key, value in (("ok", 1), ("number", True), ("index", False), ("number", -1)):
            with self.subTest(key=key, value=value):
                self.assert_code(
                    "WORKLOAD_PARSE_RECOMPUTATION_MISMATCH",
                    parse_workload_jsonl,
                    canonical_json_bytes({**base, key: value}) + b"\n",
                )

    def test_duplicate_reordered_index_and_timestamp_order_reject(self):
        payload = serialize_workload_history((entry(1.0), entry(2.0)))
        rows = payload.splitlines(keepends=True)
        first = parse_canonical_json(rows[0], line=True)
        second = parse_canonical_json(rows[1], line=True)
        cases = (
            (canonical_json_bytes({**second, "index": 0}) + b"\n") * 2,
            rows[1] + rows[0],
            canonical_json_bytes(first) + b"\n" + canonical_json_bytes({**second, "time_binary64_hex": binary64_time_identity(0.5)}) + b"\n",
        )
        expected = ("WORKLOAD_ENTRY_ORDER_INVALID", "WORKLOAD_ENTRY_ORDER_INVALID", "WORKLOAD_TIMESTAMP_ORDER_INVALID")
        for candidate, code in zip(cases, expected):
            with self.subTest(code=code):
                self.assert_code(code, parse_workload_jsonl, candidate)

    def test_pinned_round_parser_and_incomplete_round(self):
        self.assertEqual(parse_pinned_wrk2_round(wrk_log(50)), (50, True))
        self.assertEqual(parse_pinned_wrk2_round(wrk_log(50, True)), (50, False))
        self.assert_code("WORKLOAD_PARSE_RECOMPUTATION_MISMATCH", parse_pinned_wrk2_round, wrk_log(50, complete=False))

    def test_stream_identity_exact_and_all_substitutions_reject(self):
        identity = recompute_stream_identity(RUN_ID, ATTEMPT_ID)
        self.assertRegex(identity, r"^[0-9a-f]{64}$")
        self.assertEqual(identity, recompute_stream_identity(RUN_ID, ATTEMPT_ID))
        self.assertNotEqual(identity, recompute_stream_identity("sremut-ms-m01-r01-a02-abcdef123456", "a02"))
        self.assertNotEqual(identity, recompute_stream_identity(RUN_ID, "a02"))
        cases = (
            {"policy_id": "old"},
            {"source": "other"},
            {"manager_instance_ordinal": 2},
            {"manager_instance_ordinal": True},
        )
        for kwargs in cases:
            with self.subTest(kwargs=kwargs):
                self.assert_code("WORKLOAD_STREAM_IDENTITY_MISMATCH", recompute_stream_identity, RUN_ID, ATTEMPT_ID, **kwargs)

    def test_exact_three_window_name_ordinal_pairs(self):
        pairs = (
            ("INITIAL_MUTANT_CHALLENGE", 1),
            ("POST_REPLACEMENT_PERSISTENCE", 2),
            ("RESTORATION_POSITIVE_CONTROL", 3),
        )
        for phase, ordinal in pairs:
            with self.subTest(phase=phase):
                plan = self.plan(phase=phase, ordinal=ordinal)
                self.assertEqual((plan.window["phase"], plan.window["ordinal"]), (phase, ordinal))
        for phase, ordinal in (("INITIAL_MUTANT_CHALLENGE", 2), ("UNKNOWN", 1), ("1", 1), ("INITIAL_MUTANT_CHALLENGE", True)):
            with self.subTest(phase=phase, ordinal=ordinal):
                self.assert_code("WORKLOAD_WINDOW_MISMATCH", self.plan, phase=phase, ordinal=ordinal)

    def test_prefix_append_only_truncation_and_mutation(self):
        first = entry(1.0, 1)
        second = entry(2.0, 50)
        plan = self.plan(before=(first,), after=(first, second))
        self.assertTrue(plan.full_payload.startswith(plan.prefix_payload))
        self.assertEqual(plan.fresh_request_count, 50)
        self.assert_code("WORKLOAD_RAW_PREFIX_MISMATCH", self.plan, before=(first, second), after=(first,))
        changed = WorkloadHistoryEntry(1.0, 1, wrk_log(1) + "x", True)
        self.assert_code("WORKLOAD_RAW_PREFIX_MISMATCH", self.plan, before=(first,), after=(changed, second))

    def test_absent_and_present_zero_payload_are_distinct(self):
        plan = self.plan(before=(), after=(entry(),))
        candidate = plan.prefix_log_candidate(stamp(1))
        self.assertEqual(candidate.payload, b"")
        self.assertIsNotNone(candidate.payload)
        self.assertEqual(parse_workload_jsonl(candidate.payload), ())

    def test_exactly_fifty_accepted_forty_nine_rejected_by_health(self):
        self.assertTrue(self.plan(after=(entry(number=50),)).healthy)
        plan = self.plan(after=(entry(number=49),))
        self.assertFalse(plan.healthy)
        self.assertEqual(plan.fresh_request_count, 49)

    def test_failure_marker_detected_and_caller_cannot_supply_counts(self):
        plan = self.plan(after=(entry(number=50, failure=True),))
        self.assertEqual(plan.failure_marker_count, 1)
        self.assertFalse(plan.healthy)
        self.assertNotIn("fresh_request_count", prepare_workload_window.__annotations__)

    def test_cross_run_and_attempt_change_recomputed_identity(self):
        original = self.plan().window["stream_identity"]
        changed = self.plan(
            run_id="sremut-ms-m01-r01-a02-abcdef123456", attempt_id="a02"
        ).window["stream_identity"]
        self.assertNotEqual(original, changed)
        self.assert_code("WORKLOAD_WINDOW_MISMATCH", self.plan, attempt_id="a02")

    def test_cross_mutant_and_repetition_identity_mismatch_reject(self):
        for changes in ({"mutant_id": "MS-M02"}, {"repetition": 2}):
            with self.subTest(changes=changes):
                self.assert_code("WORKLOAD_WINDOW_MISMATCH", self.plan, **changes)

    def test_candidate_metadata_is_immutable_and_references_are_checked(self):
        plan = self.plan()
        candidate = plan.full_log_candidate(stamp(1))
        with self.assertRaises(TypeError):
            candidate._metadata["run_id"] = "changed"
        bad = fake_reference("workload_log_bytes")
        self.assert_code("WORKLOAD_PARSE_REFERENCE_MISMATCH", plan.boundary_candidate, bad, stamp(2))

    def test_historical_baseline_parser_counts_without_claiming_v11_evidence(self):
        expected = ((1, 30), (2, 14), (3, 26))
        for run, rounds in expected:
            with self.subTest(run=run):
                observed, requests, failures = parse_historical_workload_log(
                    (BASELINES / f"run-{run:02d}/workload.log").read_bytes()
                )
                self.assertEqual(observed, rounds)
                self.assertEqual(set(requests), {1024})
                self.assertEqual(failures, 0)

    def test_global_hook_state_is_exact_ten_connected_two_unavailable(self):
        expected = frozenset(
            {
                "VALIDATE_CANONICAL_NO_FLOATS_V1",
                "VALIDATE_EVIDENCE_REF_HASH_PATH_ID_V1",
                "VALIDATE_DESCRIPTOR_CONTENT_IDENTITY_V1",
                "VALIDATE_ATTEMPT_PHASES_AND_FINALITY_V1",
                "VALIDATE_WORKLOAD_CARDINALITY_V1",
                "VALIDATE_WORKLOAD_WINDOW_CONSISTENCY_V1",
                "VALIDATE_JOURNAL_HASH_CHAIN_V1",
                "VALIDATE_KUBERNETES_REQUEST_V1",
                "VALIDATE_KUBERNETES_RESPONSE_V1",
                "VALIDATE_SENSITIVE_CAPTURE_V1",
            }
        )
        self.assertEqual(CONNECTED_HOOKS, expected)
        self.assertEqual(tuple(hook for hook in EXPECTED_HOOK_ORDER if hook not in CONNECTED_HOOKS), (
            "VALIDATE_ADJUDICATION_RAW_BACKING_V1",
            "VALIDATE_SERVICE_RESTORATION_BODY_V1",
        ))
        self.assertEqual(len(EXPECTED_HOOK_ORDER), 12)
        self.assertEqual(
            EXPECTED_HOOK_ORDER,
            (
                "VALIDATE_CANONICAL_NO_FLOATS_V1", "VALIDATE_EVIDENCE_REF_HASH_PATH_ID_V1",
                "VALIDATE_DESCRIPTOR_CONTENT_IDENTITY_V1", "VALIDATE_ATTEMPT_PHASES_AND_FINALITY_V1",
                "VALIDATE_WORKLOAD_CARDINALITY_V1", "VALIDATE_WORKLOAD_WINDOW_CONSISTENCY_V1",
                "VALIDATE_ADJUDICATION_RAW_BACKING_V1", "VALIDATE_JOURNAL_HASH_CHAIN_V1",
                "VALIDATE_KUBERNETES_REQUEST_V1", "VALIDATE_KUBERNETES_RESPONSE_V1",
                "VALIDATE_SERVICE_RESTORATION_BODY_V1", "VALIDATE_SENSITIVE_CAPTURE_V1",
            ),
        )


class WorkloadResolvedHookTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.policy = load_policy()

    def build_context(self, *, requests=50, failure=False, parse_count_override=None,
                      predicate="INITIAL_INVARIANT_EVALUATION", omit_raw_reference=False,
                      raw_number_override=None):
        temporary = tempfile.TemporaryDirectory(prefix="sremut-workload-hook-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        with EvidenceStore(root, self.policy, RUN_ID, ATTEMPT_ID) as store:
            pod_payload = canonical_json_bytes(
                {
                    "apiVersion": "v1",
                    "kind": "Pod",
                    "metadata": {
                        "name": "user-service-abc",
                        "namespace": "social-network",
                        "uid": "pod-uid",
                        "resourceVersion": "10",
                    },
                    "status": {
                        "containerStatuses": [
                            {"name": "user-service", "ready": True, "restartCount": 0, "state": {}}
                        ]
                    },
                }
            )
            pod_ref = store.publish_payload(
                "kubernetes_object_projection",
                pod_payload,
                {
                    "run_id": RUN_ID,
                    "attempt_id": ATTEMPT_ID,
                    "created_utc": CREATED,
                    "monotonic_ns": 1,
                    "boot_identity": BOOT,
                    "request_identity_reference": fake_request_reference(),
                    "object_count": 1,
                    "projection_class": "KUBERNETES_OBJECT_PROJECTION_V1",
                },
            )
            before = (entry(1.0, 1),)
            after = before + (entry(2.0, requests, failure),)
            plan = prepare_workload_window(
                self.policy,
                before=before,
                after=after,
                phase="INITIAL_MUTANT_CHALLENGE",
                ordinal=1,
                run_id=RUN_ID,
                attempt_id=ATTEMPT_ID,
                mutant_id="MS-M01",
                repetition=1,
                workload_pod_projection_reference=pod_ref,
                pod_name="user-service-abc",
                pod_uid="pod-uid",
                container_restart_count=0,
            )
            prefix_candidate = plan.prefix_log_candidate(stamp(2))
            prefix_ref = store.publish_payload(
                prefix_candidate.role, prefix_candidate.payload, prefix_candidate.publication_metadata()
            )
            boundary_candidate = plan.boundary_candidate(prefix_ref, stamp(3))
            boundary_ref = store.publish_descriptor(
                boundary_candidate.role, boundary_candidate.publication_metadata()
            )
            raw_candidate = plan.full_log_candidate(stamp(4))
            raw_payload = raw_candidate.payload
            if raw_number_override is not None:
                rows = raw_payload.splitlines(keepends=True)
                value = parse_canonical_json(rows[-1], line=True)
                value["number"] = raw_number_override
                raw_payload = b"".join(rows[:-1]) + canonical_json_bytes(value) + b"\n"
                plan = replace(plan, full_payload=raw_payload, full_records=parse_workload_jsonl(raw_payload))
                raw_candidate = plan.full_log_candidate(stamp(4))
            raw_ref = store.publish_payload(
                raw_candidate.role, raw_payload, raw_candidate.publication_metadata()
            )
            parse_candidate = plan.parse_result_candidate(boundary_ref, raw_ref, stamp(5))
            parse_payload = parse_candidate.payload
            parse_metadata = parse_candidate.publication_metadata()
            effective_parse_count = raw_number_override if raw_number_override is not None else parse_count_override
            if effective_parse_count is not None:
                value = parse_canonical_json(parse_payload)
                value["fresh_request_count"] = effective_parse_count
                parse_payload = canonical_json_bytes(value)
                parse_metadata["fresh_request_count"] = effective_parse_count
            parse_ref = store.publish_payload(
                parse_candidate.role, parse_payload, parse_metadata
            )
            window = plan.adjudication_window(boundary_ref, raw_ref, parse_ref)
            raw_refs = (parse_ref, pod_ref) if omit_raw_reference else (raw_ref, parse_ref, pod_ref)
            adjudication_ref = store.publish_descriptor(
                "adjudication",
                {
                    "adjudication_id": "adj-workload-initial",
                    "predicate_oracle_or_classification_id": predicate,
                    "result_type": "BOOLEAN",
                    "boolean_or_categorical_value": not failure and requests >= 50,
                    "reason": "frozen workload predicate evaluation",
                    "first_observation_utc": CREATED,
                    "last_observation_utc": CREATED,
                    "monotonic_elapsed_time": 1,
                    "observation_count": 1,
                    "applicable_deadline": "INITIAL_FRESH_WORKLOAD_60S",
                    "evaluator_source_or_runner_bundle_sha256": "a" * 64,
                    "raw_evidence_references": [reference.as_dict() for reference in raw_refs],
                    "raw_evidence_sha256_per_reference": [reference.payload_sha256 for reference in raw_refs],
                    "kubernetes_uid_and_resource_version_references_when_applicable": [pod_ref.as_dict()],
                    "dependency_and_toolchain_identity": {},
                    "attempt_id": ATTEMPT_ID,
                    "run_id": RUN_ID,
                    "workload_window_adjudication_identity": window,
                },
            )
            transitions = (
                ("CREATED->PREFLIGHT_PASS", (pod_ref,)),
                ("PREFLIGHT_PASS->HEALTHY_STATE_CAPTURED", (prefix_ref,)),
                ("HEALTHY_STATE_CAPTURED->MUTANT_INJECTED", (boundary_ref,)),
                ("MUTANT_INJECTED->MUTANT_STATE_VERIFIED", (raw_ref,)),
                ("MUTANT_STATE_VERIFIED->ORIGINAL_ORACLE_STARTED", ()),
                ("ORIGINAL_ORACLE_STARTED->ORIGINAL_ORACLE_EVALUATED", ()),
                ("EVALUATION_AUTHORIZED:INITIAL_INVARIANT_EVALUATION", (parse_ref,)),
                ("ORIGINAL_ORACLE_EVALUATED->CONTRACT_EVALUATED", (adjudication_ref,)),
                ("CONTRACT_EVALUATED->RESTORE_STARTED", ()),
                ("RESTORE_STARTED->RESTORE_VERIFIED", ()),
                ("RESTORE_VERIFIED->FINALIZED", ()),
            )
            with Journal(root, self.policy, RUN_ID, ATTEMPT_ID) as journal:
                for index, (transition, references) in enumerate(transitions):
                    journal.append_state_transition(
                        transition,
                        utc_time=CREATED,
                        monotonic_ns=index + 1,
                        boot_identity=BOOT,
                        descriptor_sha256=tuple(reference.descriptor_sha256 for reference in references),
                        payload_sha256=tuple(
                            reference.payload_sha256 for reference in references
                            if reference.payload_sha256 is not None
                        ),
                        intent_receipt_adjudication_sha256=(
                            (adjudication_ref.descriptor_sha256,)
                            if reference_is_adjudication(references, adjudication_ref) else ()
                        ),
                    )
            seal = store.seal("FINALIZED")
        anchor = ExternalAnchor("workload-hook-attempt", seal.manifest_relative_path, seal.manifest_sha256)
        context = resolve_evidence_context(
            self.policy,
            root,
            RUN_ID,
            ATTEMPT_ID,
            anchor,
            expected_terminal_manifest_identity=seal.manifest_sha256,
        )
        candidate = parse_canonical_json(context.evidence[raw_ref.evidence_id].descriptor_bytes)
        return context, candidate

    def test_both_hooks_pass_through_public_dispatcher_and_global_stays_false(self):
        context, candidate = self.build_context()
        result = self.policy.full_admissibility(candidate, context)
        outcomes = {row.hook_id: row.outcome for row in result.hook_outcomes}
        self.assertEqual(outcomes["VALIDATE_WORKLOAD_CARDINALITY_V1"], "PASS")
        self.assertEqual(outcomes["VALIDATE_WORKLOAD_WINDOW_CONSISTENCY_V1"], "PASS")
        self.assertFalse(result.valid)
        self.assertEqual(result.failure_code, "RUNNER_IMPLEMENTATION_INCOMPLETE")
        self.assertEqual(result.hook_id, "VALIDATE_ADJUDICATION_RAW_BACKING_V1")

    def test_forty_nine_requests_rejected_through_public_dispatcher(self):
        context, candidate = self.build_context(requests=49)
        result = self.policy.full_admissibility(candidate, context)
        self.assertEqual(result.hook_id, "VALIDATE_WORKLOAD_CARDINALITY_V1")
        self.assertEqual(result.failure_code, "WORKLOAD_CARDINALITY_INVALID")

    def test_failure_marker_rejected_through_public_dispatcher(self):
        context, candidate = self.build_context(failure=True)
        result = self.policy.full_admissibility(candidate, context)
        self.assertEqual(result.hook_id, "VALIDATE_WORKLOAD_CARDINALITY_V1")
        self.assertEqual(result.failure_code, "WORKLOAD_CARDINALITY_INVALID")

    def test_caller_count_override_rejected_through_public_dispatcher(self):
        context, candidate = self.build_context(parse_count_override=500)
        result = self.policy.full_admissibility(candidate, context)
        self.assertEqual(result.hook_id, "VALIDATE_WORKLOAD_CARDINALITY_V1")
        self.assertEqual(result.failure_code, "WORKLOAD_PARSE_RECOMPUTATION_MISMATCH")

    def test_adjudication_predicate_is_bound_to_verified_evaluation_context(self):
        context, candidate = self.build_context(predicate="REPLACEMENT_PERSISTENCE_EVALUATION")
        result = self.policy.full_admissibility(candidate, context)
        self.assertEqual(result.hook_id, "VALIDATE_WORKLOAD_CARDINALITY_V1")
        self.assertEqual(result.failure_code, "WORKLOAD_EVALUATION_CONTEXT_MISMATCH")

    def test_adjudication_raw_reference_set_covers_resolved_workload(self):
        context, candidate = self.build_context(omit_raw_reference=True)
        result = self.policy.full_admissibility(candidate, context)
        self.assertEqual(result.hook_id, "VALIDATE_WORKLOAD_CARDINALITY_V1")
        self.assertEqual(result.failure_code, "WORKLOAD_PARSE_REFERENCE_MISMATCH")

    def test_raw_round_is_reparsed_instead_of_trusting_structured_count(self):
        context, candidate = self.build_context(raw_number_override=500)
        result = self.policy.full_admissibility(candidate, context)
        self.assertEqual(result.hook_id, "VALIDATE_WORKLOAD_CARDINALITY_V1")
        self.assertEqual(result.failure_code, "WORKLOAD_PARSE_RECOMPUTATION_MISMATCH")


def reference_is_adjudication(references, adjudication_reference):
    return any(reference.evidence_id == adjudication_reference.evidence_id for reference in references)


if __name__ == "__main__":
    unittest.main()

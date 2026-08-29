"""Offline adversarial tests for authenticated sealed-attempt resolution."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
import os
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import yaml

from sremut.canonical_json import canonical_json_line, parse_canonical_json, sha256_hex
from sremut.evidence import EvidenceStore, ExternalAnchor
from sremut.journal import Journal, JournalError, SafeRoot, record_sha256
from sremut.policy_runtime import EXPECTED_HOOK_ORDER, POLICY_MANIFEST_SHA256, load_policy_bundle
from sremut.resolved_context import (
    CONNECTED_HOOKS,
    ResolvedContextError,
    ResolvedEvidenceContext,
    _policy_identity,
    resolve_evidence_context,
)


REPOSITORY = Path(__file__).resolve().parents[1]
POLICY_PATH = REPOSITORY / "policies/missing_service_social_network/evidence-capture-v1.1.yaml"
SCHEMA_PATH = REPOSITORY / "schemas/evidence-capture-policy-v1.1.schema.json"
MANIFEST_PATH = REPOSITORY / "EVIDENCE_CAPTURE_POLICY_V1_1_SHA256SUMS"
RUN_ID = "sremut-ms-m01-r01-a01-abcdef123456"
ATTEMPT_ID = "a01"
CREATED_UTC = "2026-08-20T10:00:00.123456789Z"
BOOT_IDENTITY = "123e4567-e89b-12d3-a456-426614174000"


def load_policy():
    return load_policy_bundle(
        POLICY_PATH,
        SCHEMA_PATH,
        MANIFEST_PATH,
        expected_manifest_sha256=POLICY_MANIFEST_SHA256,
    )


def common_metadata(**extra):
    value = {
        "run_id": RUN_ID,
        "attempt_id": ATTEMPT_ID,
        "created_utc": CREATED_UTC,
        "monotonic_ns": 1,
        "boot_identity": BOOT_IDENTITY,
    }
    value.update(extra)
    return value


def challenge_metadata():
    return common_metadata(
        template_id="CHALLENGE_TCP_V1",
        parameters={"host": "user-service.social-network.svc.cluster.local", "port": 9090},
        pod_name="sremut-challenge-fixed",
        pod_uid="11111111-1111-4111-8111-111111111111",
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


def global_stop():
    return {
        "document_type": "TERMINAL_GLOBAL_STOP_V1",
        "schema_version": 1,
        "run_id": RUN_ID,
        "attempt_id": ATTEMPT_ID,
        "terminal_outcome": "RESTORATION_BLOCKED",
        "reason_code": "RESTORATION_VERIFICATION_FAILED",
        "adjudication_reference": fake_adjudication_ref(),
        "created_utc": CREATED_UTC,
        "monotonic_ns": 99,
        "boot_identity": BOOT_IDENTITY,
    }


class ResolvedContextCase(unittest.TestCase):
    policy = None

    @classmethod
    def setUpClass(cls):
        cls.policy = load_policy()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="sremut-resolved-context-test-")
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def assert_code(self, code, function, *args, **kwargs):
        with self.assertRaises(Exception) as caught:
            function(*args, **kwargs)
        self.assertEqual(str(caught.exception), code)
        self.assertNotIn("credential", str(caught.exception).lower())
        self.assertNotIn(str(self.root), str(caught.exception))
        return caught.exception

    def _transitions(self, outcome):
        if outcome == "ABORTED_SAFE":
            return ("CREATED->ABORTED_SAFE",)
        if outcome == "RESTORATION_BLOCKED":
            return (
                "CREATED->PREFLIGHT_PASS",
                "PREFLIGHT_PASS->HEALTHY_STATE_CAPTURED",
                "HEALTHY_STATE_CAPTURED->MUTANT_INJECTED",
                "MUTANT_INJECTED->MUTANT_STATE_VERIFIED",
                "MUTANT_STATE_VERIFIED->RESTORE_STARTED",
                "RESTORE_STARTED->RESTORATION_BLOCKED",
            )
        return (
            "CREATED->PREFLIGHT_PASS",
            "PREFLIGHT_PASS->HEALTHY_STATE_CAPTURED",
            "HEALTHY_STATE_CAPTURED->MUTANT_INJECTED",
            "MUTANT_INJECTED->MUTANT_STATE_VERIFIED",
            "MUTANT_STATE_VERIFIED->ORIGINAL_ORACLE_STARTED",
            "ORIGINAL_ORACLE_STARTED->ORIGINAL_ORACLE_EVALUATED",
            "ORIGINAL_ORACLE_EVALUATED->CONTRACT_EVALUATED",
            "CONTRACT_EVALUATED->RESTORE_STARTED",
            "RESTORE_STARTED->RESTORE_VERIFIED",
            "RESTORE_VERIFIED->FINALIZED",
        )

    def seal(self, outcome="ABORTED_SAFE", evidence_kind=None, payload=b'{"ok":true}', bypass=False):
        references = []
        with EvidenceStore(self.root, self.policy, RUN_ID, ATTEMPT_ID) as store:
            if evidence_kind == "input":
                if bypass:
                    with patch("sremut.evidence.validate_payload"):
                        references.append(
                            store.publish_payload(
                                "original_oracle_input",
                                payload,
                                common_metadata(invocation_ordinal=1),
                            )
                        )
                else:
                    references.append(
                        store.publish_payload(
                            "original_oracle_input",
                            payload,
                            common_metadata(invocation_ordinal=1),
                        )
                    )
            elif evidence_kind == "input_raw":
                _descriptor, descriptor_bytes, reference = store._descriptor(
                    "original_oracle_input",
                    common_metadata(invocation_ordinal=1),
                    payload,
                )
                store.fs.write_atomic(reference.payload_relative_path or "", payload)
                store.fs.write_atomic(reference.descriptor_relative_path, descriptor_bytes)
                references.append(reference)
            elif evidence_kind == "stdout":
                invocation = store.publish_descriptor("challenge_invocation", challenge_metadata())
                if bypass:
                    with patch("sremut.evidence.validate_payload"):
                        output = store.publish_payload(
                            "challenge_stdout",
                            payload,
                            common_metadata(invocation_reference=invocation.as_dict(), channel="STDOUT"),
                        )
                else:
                    output = store.publish_payload(
                        "challenge_stdout",
                        payload,
                        common_metadata(invocation_reference=invocation.as_dict(), channel="STDOUT"),
                    )
                references.extend((invocation, output))
            elif evidence_kind == "workload":
                workload_window = {
                    "phase": "INITIAL_MUTANT_CHALLENGE",
                    "ordinal": 1,
                    "run_id": RUN_ID,
                    "attempt_id": ATTEMPT_ID,
                    "mutant_id": "MS-M01",
                    "repetition": 1,
                }
                metadata = common_metadata(
                    complete_entry_count=1,
                    entry_time_ieee754_binary64_hex=["3ff0000000000000"],
                    entry_indexes=[0],
                    workload_window=workload_window,
                )
                if bypass:
                    with patch("sremut.evidence.validate_payload"):
                        references.append(
                            store.publish_payload("workload_log_bytes", payload, metadata)
                        )
                else:
                    references.append(
                        store.publish_payload("workload_log_bytes", payload, metadata)
                    )
            descriptor_hashes = tuple(reference.descriptor_sha256 for reference in references)
            payload_hashes = tuple(
                reference.payload_sha256
                for reference in references
                if reference.payload_sha256 is not None
            )
            with Journal(self.root, self.policy, RUN_ID, ATTEMPT_ID) as journal:
                for index, transition in enumerate(self._transitions(outcome)):
                    journal.append_state_transition(
                        transition,
                        utc_time=CREATED_UTC,
                        monotonic_ns=index + 1,
                        boot_identity=BOOT_IDENTITY,
                        descriptor_sha256=descriptor_hashes if index == 0 else (),
                        payload_sha256=payload_hashes if index == 0 else (),
                    )
            stop_path = None
            if outcome == "RESTORATION_BLOCKED":
                stop_path = store.write_global_stop(global_stop())
            seal = store.seal(outcome, global_stop_relative_path=stop_path)
        anchor = ExternalAnchor(
            attempt_root_identifier="attempt-root-fixed",
            manifest_relative_path=seal.manifest_relative_path,
            terminal_manifest_sha256=seal.manifest_sha256,
        )
        return seal, anchor, tuple(references)

    def resolve(self, anchor, *, root=None, run_id=RUN_ID, attempt_id=ATTEMPT_ID, policy=None, expected=None):
        return resolve_evidence_context(
            self.policy if policy is None else policy,
            self.root if root is None else root,
            run_id,
            attempt_id,
            anchor,
            expected_terminal_manifest_identity=(
                anchor.terminal_manifest_sha256 if expected is None else expected
            ),
        )

    def rewrite_manifest_for(self, relative):
        manifest_path = self.root / "manifests/terminal.sha256"
        rows = []
        for line in manifest_path.read_text(encoding="utf-8").splitlines():
            digest, path = line.split("  ", 1)
            if path == relative:
                digest = sha256_hex((self.root / path).read_bytes())
            rows.append((path, digest))
        data = "".join(f"{digest}  {path}\n" for path, digest in rows).encode()
        manifest_path.write_bytes(data)
        return ExternalAnchor(
            attempt_root_identifier="attempt-root-fixed",
            manifest_relative_path="manifests/terminal.sha256",
            terminal_manifest_sha256=sha256_hex(data),
        )


class ContextConstructionTests(ResolvedContextCase):
    def test_valid_structurally_sealed_attempt_and_safe_repr(self):
        _seal, anchor, _refs = self.seal()
        context = self.resolve(anchor)
        self.assertEqual(context.terminal_outcome, "ABORTED_SAFE")
        self.assertEqual(context.classification, "COMPLETE")
        self.assertEqual(context.evidence, {})
        rendered = repr(context)
        self.assertNotIn(str(self.root), rendered)
        self.assertNotIn("payload_bytes", rendered)

    def test_context_binds_external_attempt_root_identity(self):
        _seal, anchor, _refs = self.seal()
        context = self.resolve(anchor)
        self.assertEqual(context.attempt_root_identifier, anchor.attempt_root_identifier)

    def test_direct_constructor_and_forged_context_reject(self):
        self.assert_code(
            "RESOLVED_CONTEXT_DIRECT_CONSTRUCTION_FORBIDDEN",
            ResolvedEvidenceContext,
        )
        forged = object.__new__(ResolvedEvidenceContext)
        policy_document = yaml.safe_load(self.policy.policy_bytes)
        result = self.policy.full_admissibility(policy_document, forged)
        self.assertFalse(result.valid)
        self.assertEqual(result.failure_code, "RESOLVED_CONTEXT_INVALID")

    def test_unsealed_missing_manifest_and_wrong_identity_reject(self):
        anchor = ExternalAnchor("attempt", "manifests/terminal.sha256", "0" * 64)
        self.assert_code("EXTERNAL_SEAL_MISSING", self.resolve, anchor)
        _seal, good, _refs = self.seal()
        self.assert_code("EXTERNAL_MANIFEST_HASH_MISMATCH", self.resolve, good, expected="f" * 64)
        bad = replace(good, terminal_manifest_sha256="f" * 64)
        self.assert_code("EXTERNAL_MANIFEST_HASH_MISMATCH", self.resolve, bad)

    def test_run_attempt_and_policy_identity_mismatch_reject(self):
        _seal, anchor, _refs = self.seal()
        self.assert_code("JOURNAL_CONTEXT_MISMATCH", self.resolve, anchor, run_id=RUN_ID.replace("m01", "m02"))
        self.assert_code("JOURNAL_CONTEXT_MISMATCH", self.resolve, anchor, attempt_id="a02")
        changed = replace(self.policy, manifest_sha256="f" * 64)
        self.assert_code("POLICY_MANIFEST_HASH_MISMATCH", self.resolve, anchor, policy=changed)
        changed_matrix = replace(self.policy, applicability=())
        self.assert_code("HOOK_MATRIX_MISMATCH", self.resolve, anchor, policy=changed_matrix)

    def test_root_rename_replacement_after_safe_open_uses_original_descriptor(self):
        _seal, anchor, _refs = self.seal()
        with SafeRoot(self.root) as safe:
            original = self.root.parent / (self.root.name + "-moved")
            self.root.rename(original)
            self.root.mkdir()
            try:
                context = self.resolve(anchor, root=safe)
                self.assertEqual(context.terminal_outcome, "ABORTED_SAFE")
            finally:
                shutil.rmtree(self.root)
                original.rename(self.root)

    def test_symlink_traversal_unexpected_and_missing_files_reject(self):
        _seal, anchor, _refs = self.seal(evidence_kind="input")
        (self.root / "unexpected").write_bytes(b"x")
        self.assert_code("EXTERNAL_MANIFEST_COVERAGE_MISMATCH", self.resolve, anchor)
        (self.root / "unexpected").unlink()
        descriptor = next((self.root / "descriptors").rglob("*.json"))
        descriptor.unlink()
        self.assert_code("EXTERNAL_MANIFEST_COVERAGE_MISMATCH", self.resolve, anchor)

        other = Path(tempfile.mkdtemp(prefix="sremut-resolved-symlink-"))
        self.addCleanup(shutil.rmtree, other)
        link = other / "attempt"
        link.symlink_to(self.root, target_is_directory=True)
        self.assert_code("EVIDENCE_SYMLINK_REFUSED", self.resolve, anchor, root=link)
        traversal = replace(anchor, manifest_relative_path="../terminal.sha256")
        self.assert_code("EXTERNAL_SEAL_MISSING", self.resolve, traversal)

    def test_missing_payload_and_present_zero_byte_are_distinct(self):
        _seal, anchor, _refs = self.seal(evidence_kind="input")
        payload = next(path for path in (self.root / "objects").rglob("*") if path.is_file())
        payload.unlink()
        self.assert_code("EXTERNAL_MANIFEST_COVERAGE_MISMATCH", self.resolve, anchor)

        self.temporary.cleanup()
        self.temporary = tempfile.TemporaryDirectory(prefix="sremut-resolved-zero-")
        self.root = Path(self.temporary.name)
        _seal, zero_anchor, refs = self.seal(evidence_kind="stdout", payload=b"")
        context = self.resolve(zero_anchor)
        row = context.evidence[refs[-1].evidence_id]
        self.assertEqual(row.payload_bytes, b"")
        self.assertIsNotNone(row.payload_bytes)

    def test_safe_root_read_retries_eintr_and_enforces_post_open_bounds(self):
        path = self.root / "bounded"
        path.write_bytes(b"abcdef")
        original_read = os.read
        calls = 0

        def interrupted_then_short(descriptor, count):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise InterruptedError
            return original_read(descriptor, min(count, 2))

        with SafeRoot(self.root) as safe, patch(
            "sremut.journal.os.read", interrupted_then_short
        ):
            self.assertEqual(
                safe.read_bytes_bounded("bounded", maximum_bytes=6, exact_size=6),
                b"abcdef",
            )

        original_fstat = os.fstat

        def stale_small_size(descriptor):
            info = original_fstat(descriptor)
            return SimpleNamespace(st_mode=info.st_mode, st_size=1)

        with SafeRoot(self.root) as safe, patch(
            "sremut.journal.os.fstat", stale_small_size
        ):
            with self.assertRaises(JournalError) as caught:
                safe.read_bytes_bounded("bounded", maximum_bytes=1)
        self.assertEqual(str(caught.exception), "EVIDENCE_SIZE_INVALID")

        path.write_bytes(b"abc")

        def stale_exact_size(descriptor):
            info = original_fstat(descriptor)
            return SimpleNamespace(st_mode=info.st_mode, st_size=6)

        with SafeRoot(self.root) as safe, patch(
            "sremut.journal.os.fstat", stale_exact_size
        ):
            with self.assertRaises(JournalError) as caught:
                safe.read_bytes_bounded("bounded", exact_size=6)
        self.assertEqual(str(caught.exception), "EVIDENCE_SIZE_INVALID")

    def test_oversize_payload_rejected_by_open_descriptor_bound(self):
        _seal, anchor, refs = self.seal(evidence_kind="stdout", payload=b"")
        payload_path = self.root / (refs[-1].payload_relative_path or "")
        payload_path.write_bytes(b"x")
        anchor = self.rewrite_manifest_for(refs[-1].payload_relative_path)
        self.assert_code("PAYLOAD_SIZE_MISMATCH", self.resolve, anchor)

    def test_context_mappings_records_and_bytes_are_immutable(self):
        _seal, anchor, refs = self.seal(evidence_kind="input")
        context = self.resolve(anchor)
        with self.assertRaises((TypeError, FrozenInstanceError, AttributeError)):
            context.evidence["x"] = context.evidence[refs[0].evidence_id]
        with self.assertRaises((TypeError, FrozenInstanceError, AttributeError)):
            context.journal_records[0]["sequence_number"] = 9
        with self.assertRaises((TypeError, FrozenInstanceError, AttributeError)):
            context.run_id = "changed"
        self.assertIsInstance(context.journal_bytes, bytes)

    def test_resolution_performs_no_write(self):
        _seal, anchor, _refs = self.seal(evidence_kind="input")
        with patch.object(SafeRoot, "write_atomic") as write, patch.object(
            SafeRoot, "append_durable"
        ) as append:
            self.resolve(anchor)
        write.assert_not_called()
        append.assert_not_called()


class ConnectedHookTests(ResolvedContextCase):
    def context_with_input(self, payload=b'{"ok":true}', bypass=False):
        _seal, anchor, references = self.seal(
            evidence_kind="input", payload=payload, bypass=bypass
        )
        return self.resolve(anchor), references[0]

    def test_final_hooks_are_wired_in_frozen_dispatch_positions(self):
        adjudication = "VALIDATE_ADJUDICATION_RAW_BACKING_V1"
        restoration = "VALIDATE_SERVICE_RESTORATION_BODY_V1"
        self.assertIn(adjudication, CONNECTED_HOOKS)
        self.assertIn(restoration, CONNECTED_HOOKS)
        self.assertEqual(EXPECTED_HOOK_ORDER.index(adjudication), 6)
        self.assertEqual(EXPECTED_HOOK_ORDER.index(restoration), 10)
        self.assertEqual(EXPECTED_HOOK_ORDER.count(adjudication), 1)
        self.assertEqual(EXPECTED_HOOK_ORDER.count(restoration), 1)

    def test_valid_reference_descriptor_and_sensitive_hooks_pass_in_order(self):
        context, reference = self.context_with_input()
        candidate = parse_canonical_json(context.evidence[reference.evidence_id].descriptor_bytes)
        result = self.policy.full_admissibility(candidate, context)
        self.assertTrue(result.valid)
        self.assertIsNone(result.failure_code)
        self.assertEqual(
            tuple(outcome.hook_id for outcome in result.hook_outcomes),
            (
                "VALIDATE_CANONICAL_NO_FLOATS_V1",
                "VALIDATE_EVIDENCE_REF_HASH_PATH_ID_V1",
                "VALIDATE_DESCRIPTOR_CONTENT_IDENTITY_V1",
                "VALIDATE_SENSITIVE_CAPTURE_V1",
            ),
        )
        self.assertTrue(all(outcome.outcome == "PASS" for outcome in result.hook_outcomes))

    def test_reference_substitution_matrix_rejects(self):
        context, reference = self.context_with_input()
        variants = []
        for field, value in (
            ("evidence_id", "ev-" + "f" * 32),
            ("role", "challenge_stdout"),
            ("producer", "CALLER"),
            ("descriptor_relative_path", "descriptors/sha256/ff/" + "f" * 64 + ".json"),
            ("payload_sha256", "f" * 64),
            ("descriptor_sha256", "f" * 64),
            ("attempt", "cross-attempt"),
            ("run", "cross-run"),
            ("storage_class", "DESCRIPTOR_ONLY"),
        ):
            changed = reference.as_dict()
            if field == "attempt":
                changed["descriptor_sha256"] = "e" * 64
                changed["evidence_id"] = "ev-" + "e" * 32
                changed["descriptor_relative_path"] = "descriptors/sha256/ee/" + "e" * 64 + ".json"
            elif field == "run":
                changed["descriptor_sha256"] = "d" * 64
                changed["evidence_id"] = "ev-" + "d" * 32
                changed["descriptor_relative_path"] = "descriptors/sha256/dd/" + "d" * 64 + ".json"
            else:
                changed[field] = value
                if field == "payload_sha256":
                    changed["payload_relative_path"] = "objects/sha256/ff/" + "f" * 64
            variants.append(changed)
        for candidate in variants:
            with self.subTest(candidate=candidate.get("evidence_id")):
                with self.assertRaises(ResolvedContextError):
                    context.validate_hook("VALIDATE_EVIDENCE_REF_HASH_PATH_ID_V1", candidate)

    def test_descriptor_candidate_substitutions_reject(self):
        context, reference = self.context_with_input()
        descriptor = dict(context.evidence[reference.evidence_id].descriptor)
        for field, value in (
            ("media_type", "text/plain"),
            ("storage_class", "DESCRIPTOR_ONLY"),
            ("redaction_status", "REDACTED"),
            ("payload_size_bytes", 999),
            ("role", "challenge_stdout"),
        ):
            with self.subTest(field=field):
                changed = dict(descriptor)
                changed[field] = value
                self.assert_code(
                    "EVIDENCE_REFERENCE_UNRESOLVED",
                    context.validate_hook,
                    "VALIDATE_DESCRIPTOR_CONTENT_IDENTITY_V1",
                    changed,
                )

    def test_wrong_policy_context_and_missing_context_reject(self):
        context, reference = self.context_with_input()
        candidate = reference.as_dict()
        missing = self.policy.full_admissibility(candidate)
        self.assertEqual(missing.failure_code, "MISSING_RESOLVED_CONTEXT")
        changed = replace(self.policy, manifest_sha256="f" * 64)
        alternate = changed.full_admissibility(candidate, context)
        self.assertEqual(alternate.failure_code, "RESOLVED_CONTEXT_INVALID")

    def test_journal_hook_exact_record_and_altered_record(self):
        _seal, anchor, _refs = self.seal()
        context = self.resolve(anchor)
        candidate = dict(parse_canonical_json(context.journal_bytes.splitlines(keepends=True)[0], line=True))
        context.validate_hook("VALIDATE_JOURNAL_HASH_CHAIN_V1", candidate)
        candidate["monotonic_ns"] += 1
        self.assert_code(
            "JOURNAL_CHAIN_INVALID",
            context.validate_hook,
            "VALIDATE_JOURNAL_HASH_CHAIN_V1",
            candidate,
        )

    def test_valid_terminal_classifications_and_global_stop_hook(self):
        expected = {
            "FINALIZED": "COMPLETE",
            "ABORTED_SAFE": "COMPLETE",
            "RESTORATION_BLOCKED": "SEALED_PARTIAL_WITH_GLOBAL_STOP",
        }
        for outcome, classification in expected.items():
            with self.subTest(outcome=outcome):
                root = Path(tempfile.mkdtemp(prefix="sremut-resolved-finality-"))
                self.addCleanup(shutil.rmtree, root)
                old = self.root
                self.root = root
                try:
                    _seal, anchor, _refs = self.seal(outcome=outcome)
                    context = self.resolve(anchor)
                    self.assertEqual(context.classification, classification)
                    if outcome == "RESTORATION_BLOCKED":
                        context.validate_hook(
                            "VALIDATE_ATTEMPT_PHASES_AND_FINALITY_V1",
                            dict(context.terminal_global_stop),
                        )
                finally:
                    self.root = old

    def test_sensitive_payload_matrix_and_false_positive(self):
        positives = (
            b"Authorization: credential\r\n",
            b'{"token":"credential-do-not-leak"}',
            b'{"apiVersion":"v1","clusters":[],"contexts":[],"kind":"Config","users":[{"user":{"exec":{"command":"x"}}}]}',
            b'{"token":"credential-do-not-leak","token":""}',
        )
        for payload in positives:
            with self.subTest(payload=payload[:12]):
                root = Path(tempfile.mkdtemp(prefix="sremut-resolved-sensitive-"))
                self.addCleanup(shutil.rmtree, root)
                old = self.root
                self.root = root
                try:
                    if payload.startswith(b"Authorization"):
                        evidence_kind = "stdout"
                    elif payload.count(b'"token"') > 1:
                        evidence_kind = "input_raw"
                    else:
                        evidence_kind = "input"
                    _seal, anchor, refs = self.seal(
                        evidence_kind=evidence_kind, payload=payload, bypass=True
                    )
                    context = self.resolve(anchor)
                    candidate = parse_canonical_json(context.evidence[refs[-1].evidence_id].descriptor_bytes)
                    result = self.policy.full_admissibility(candidate, context)
                    self.assertEqual(result.failure_code, "SENSITIVE_CAPTURE_REJECTED")
                    self.assertEqual(result.hook_id, "VALIDATE_SENSITIVE_CAPTURE_V1")
                    self.assertNotIn("credential-do-not-leak", repr(result))
                finally:
                    self.root = old
        root = Path(tempfile.mkdtemp(prefix="sremut-resolved-false-positive-"))
        self.addCleanup(shutil.rmtree, root)
        old = self.root
        self.root = root
        try:
            _seal, anchor, refs = self.seal(
                evidence_kind="stdout", payload=b"token_count=4", bypass=False
            )
            context = self.resolve(anchor)
            result = self.policy.full_admissibility(
                parse_canonical_json(context.evidence[refs[-1].evidence_id].descriptor_bytes), context
            )
            self.assertTrue(result.valid)
            self.assertIsNone(result.failure_code)
        finally:
            self.root = old


    def test_frozen_nested_descriptor_matches_exact_parsed_candidate(self):
        _seal, anchor, refs = self.seal(
            evidence_kind="input", payload=b'{"ok":true}'
        )
        context = self.resolve(anchor)
        candidate = parse_canonical_json(
            context.evidence[refs[-1].evidence_id].descriptor_bytes
        )
        context.validate_hook("VALIDATE_DESCRIPTOR_CONTENT_IDENTITY_V1", candidate)

    def test_connected_sensitive_failure_is_not_hidden(self):
        _seal, anchor, refs = self.seal(
            evidence_kind="input_raw", payload=b"\xff", bypass=True
        )
        context = self.resolve(anchor)
        candidate = parse_canonical_json(
            context.evidence[refs[-1].evidence_id].descriptor_bytes
        )
        result = self.policy.full_admissibility(candidate, context)
        self.assertFalse(result.valid)
        self.assertEqual(result.hook_id, "VALIDATE_SENSITIVE_CAPTURE_V1")
        self.assertEqual(result.failure_code, "SENSITIVE_CAPTURE_REJECTED")
        self.assertEqual(
            tuple(outcome.hook_id for outcome in result.hook_outcomes),
            (
                "VALIDATE_CANONICAL_NO_FLOATS_V1",
                "VALIDATE_EVIDENCE_REF_HASH_PATH_ID_V1",
                "VALIDATE_DESCRIPTOR_CONTENT_IDENTITY_V1",
                "VALIDATE_SENSITIVE_CAPTURE_V1",
            ),
        )


class SealedTamperTests(ResolvedContextCase):
    def mutate_journal(self, transform):
        _seal, anchor, _refs = self.seal(outcome="FINALIZED")
        path = self.root / "journal/attempt.jsonl"
        lines = path.read_bytes().splitlines(keepends=True)
        path.write_bytes(transform(lines))
        return self.rewrite_manifest_for("journal/attempt.jsonl")

    def test_wrong_previous_sequence_truncation_reorder_duplicate_and_extra_reject(self):
        transforms = []

        def wrong_previous(lines):
            record = dict(parse_canonical_json(lines[0], line=True))
            record["previous_entry_sha256"] = "f" * 64
            record["canonical_current_entry_sha256"] = record_sha256(record)
            return canonical_json_line(record) + b"".join(lines[1:])

        def wrong_sequence(lines):
            record = dict(parse_canonical_json(lines[1], line=True))
            record["sequence_number"] = 99
            record["canonical_current_entry_sha256"] = record_sha256(record)
            return lines[0] + canonical_json_line(record) + b"".join(lines[2:])

        transforms.extend(
            (
                wrong_previous,
                wrong_sequence,
                lambda lines: b"".join(lines)[:-1],
                lambda lines: b"".join((lines[1], lines[0], *lines[2:])),
                lambda lines: b"".join((lines[0], lines[0], *lines[1:])),
                lambda lines: b"".join(lines) + b"extra",
            )
        )
        for transform in transforms:
            with self.subTest(transform=getattr(transform, "__name__", "lambda")):
                root = Path(tempfile.mkdtemp(prefix="sremut-resolved-journal-tamper-"))
                self.addCleanup(shutil.rmtree, root)
                old = self.root
                self.root = root
                try:
                    anchor = self.mutate_journal(transform)
                    with self.assertRaises(ResolvedContextError):
                        self.resolve(anchor)
                finally:
                    self.root = old

    def test_event_after_terminal_rejects_without_repair(self):
        _seal, _anchor, _refs = self.seal()
        path = self.root / "journal/attempt.jsonl"
        records = path.read_bytes().splitlines(keepends=True)
        last = dict(parse_canonical_json(records[-1], line=True))
        extra = dict(last)
        extra["sequence_number"] = last["sequence_number"] + 1
        extra["previous_entry_sha256"] = last["canonical_current_entry_sha256"]
        extra["monotonic_ns"] = last["monotonic_ns"] + 1
        extra["canonical_current_entry_sha256"] = "0" * 64
        extra["canonical_current_entry_sha256"] = record_sha256(extra)
        path.write_bytes(b"".join(records) + canonical_json_line(extra))
        anchor = self.rewrite_manifest_for("journal/attempt.jsonl")
        self.assert_code("POST_TERMINAL_OPERATION", self.resolve, anchor)

    def test_manifest_classification_substitution_rejects(self):
        _seal, anchor, _refs = self.seal(outcome="RESTORATION_BLOCKED")
        stop = self.root / "terminal/global-stop.json"
        stop.unlink()
        manifest = self.root / "manifests/terminal.sha256"
        rows = [line for line in manifest.read_bytes().splitlines(keepends=True) if b"terminal/global-stop.json" not in line]
        manifest.write_bytes(b"".join(rows))
        changed = replace(anchor, terminal_manifest_sha256=sha256_hex(manifest.read_bytes()))
        self.assert_code("ATTEMPT_FINALITY_INVALID", self.resolve, changed)


class VersionAwarePolicyIdentityTests(ResolvedContextCase):
    """Authentication follows the binding the policy carries, not a fixed pin."""

    @classmethod
    def setUpClass(cls):
        from sremut import policy_runtime as runtime

        cls.runtime = runtime
        cls.policy = load_policy()
        cls.v1_2 = runtime.load_v1_2_policy_bundle(
            REPOSITORY / "policies/missing_service_social_network/evidence-capture-v1.2.yaml",
            REPOSITORY / "schemas/evidence-capture-policy-v1.2.schema.json",
            REPOSITORY / "EVIDENCE_CAPTURE_POLICY_V1_2_SHA256SUMS",
            expected_manifest_sha256=runtime.POLICY_V1_2_MANIFEST_SHA256,
        )

    def test_policy_binding_is_closed_over_exactly_two_bundles(self):
        binding = self.runtime.policy_binding
        self.assertIs(binding(self.policy), self.runtime._V1_1_BINDING)
        self.assertIs(binding(self.v1_2), self.runtime._V1_2_BINDING)
        self.assertEqual(len(self.runtime.PINNED_BINDINGS), 2)
        for value in (None, object(), "1.2", {"semantic_version": "1.2"}):
            with self.assertRaises(Exception) as caught:
                binding(value)
            self.assertEqual(str(caught.exception), "POLICY_BINDING_MISSING")

    def test_version_is_never_taken_from_a_caller_supplied_string(self):
        """A policy whose carried binding is not pinned is refused outright."""
        from dataclasses import replace as _replace

        forged = _replace(self.v1_2, binding=_replace(self.runtime._V1_2_BINDING))
        with self.assertRaises(Exception) as caught:
            self.runtime.policy_binding(forged)
        self.assertEqual(str(caught.exception), "POLICY_BINDING_MISSING")

    def test_identity_reports_each_version_exactly(self):
        first = _policy_identity(self.policy)
        second = _policy_identity(self.v1_2)
        self.assertEqual(first.manifest_sha256, self.runtime.POLICY_MANIFEST_SHA256)
        self.assertEqual(second.manifest_sha256, self.runtime.POLICY_V1_2_MANIFEST_SHA256)
        self.assertEqual(second.policy_sha256, self.runtime.POLICY_V1_2_POLICY_SHA256)
        self.assertEqual(second.schema_sha256, self.runtime.POLICY_V1_2_SCHEMA_SHA256)
        self.assertNotEqual(first.manifest_sha256, second.manifest_sha256)
        # The frozen contract and profile identities are shared, as they must be.
        self.assertEqual(first.contract_sha256, second.contract_sha256)
        self.assertEqual(
            first.execution_profile_tag_object, second.execution_profile_tag_object)

    def test_a_v1_2_sealed_attempt_resolves_through_the_production_path(self):
        # Build and seal the attempt with the v1.2 bundle itself.
        v1_1 = self.policy
        self.policy = self.v1_2
        seal, anchor, _references = self.seal("ABORTED_SAFE")
        context = resolve_evidence_context(
            self.v1_2, self.root, RUN_ID, ATTEMPT_ID, anchor,
            expected_terminal_manifest_identity=seal.manifest_sha256,
        )
        self.assertEqual(
            context.policy_identity.manifest_sha256,
            self.runtime.POLICY_V1_2_MANIFEST_SHA256,
        )
        self.assertEqual(self.v1_2.policy["policy_id"], self.runtime.POLICY_V1_2_ID)
        self.assertTrue(context.authenticates(self.v1_2))
        # A v1.2 context does not authenticate under the v1.1 bundle.
        self.assertFalse(context.authenticates(v1_1))
        self.assertEqual(dict(context.evaluation_authorization_contexts), {})
        self.assertFalse(hasattr(context.journal_state, "evaluation"))

    def test_v1_1_attempts_still_resolve_unchanged(self):
        seal, anchor, _references = self.seal("ABORTED_SAFE")
        context = self.resolve(anchor, expected=seal.manifest_sha256)
        self.assertEqual(
            context.policy_identity.manifest_sha256, self.runtime.POLICY_MANIFEST_SHA256)
        # v1.1 defines no resolved-context document, so none is emitted for it.
        with self.assertRaises(Exception) as caught:
            context.as_v1_2_document()
        self.assertEqual(str(caught.exception), "POLICY_BINDING_MISSING")


if __name__ == "__main__":
    unittest.main()

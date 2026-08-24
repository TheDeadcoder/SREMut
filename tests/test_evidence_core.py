"""Deterministic offline tests for the first SREMut evidence-core slice."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, asdict, replace
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import yaml

from sremut.canonical_json import (
    CanonicalJSONError,
    canonical_json_bytes,
    canonical_json_line,
    canonical_sha256,
    parse_canonical_json,
    sha256_hex,
)
from sremut.evidence import (
    EvidenceError,
    EvidenceRef,
    EvidenceStore,
    ExternalAnchor,
    ZERO_SHA256,
    revalidate_sealed_attempt,
    validate_evidence_ref,
)
from sremut.journal import GENESIS_SHA256, Journal, JournalError, SafeRoot, record_sha256
from sremut.policy_runtime import (
    EXPECTED_HOOK_ORDER,
    POLICY_MANIFEST_SHA256,
    PolicyRuntimeError,
    ValidationResult,
    _parse_manifest,
    load_policy_bundle,
)
from sremut.sensitive import (
    SensitiveCaptureError,
    detect_sensitive,
    validate_payload,
    validate_projection_allowlist,
    validate_redaction_status,
)


REPOSITORY = Path(__file__).resolve().parents[1]
POLICY_PATH = REPOSITORY / "policies/missing_service_social_network/evidence-capture-v1.1.yaml"
SCHEMA_PATH = REPOSITORY / "schemas/evidence-capture-policy-v1.1.schema.json"
MANIFEST_PATH = REPOSITORY / "EVIDENCE_CAPTURE_POLICY_V1_1_SHA256SUMS"
RUN_ID = "sremut-ms-m01-r01-a01-abcdef123456"
ATTEMPT_ID = "a01"
CREATED_UTC = "2026-08-20T10:00:00.123456789Z"
BOOT_IDENTITY = "123e4567-e89b-12d3-a456-426614174000"


def load_frozen_policy():
    return load_policy_bundle(
        POLICY_PATH,
        SCHEMA_PATH,
        MANIFEST_PATH,
        expected_manifest_sha256=POLICY_MANIFEST_SHA256,
    )


def common_metadata(**extra):
    result = {
        "run_id": RUN_ID,
        "attempt_id": ATTEMPT_ID,
        "created_utc": CREATED_UTC,
        "monotonic_ns": 1,
        "boot_identity": BOOT_IDENTITY,
    }
    result.update(extra)
    return result


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


def terminal_global_stop():
    return {
        "document_type": "TERMINAL_GLOBAL_STOP_V1",
        "schema_version": 1,
        "run_id": RUN_ID,
        "attempt_id": ATTEMPT_ID,
        "terminal_outcome": "RESTORATION_BLOCKED",
        "reason_code": "RESTORATION_VERIFICATION_FAILED",
        "adjudication_reference": fake_adjudication_ref(),
        "created_utc": CREATED_UTC,
        "monotonic_ns": 9,
        "boot_identity": BOOT_IDENTITY,
    }


class TemporaryRootCase(unittest.TestCase):
    policy = None

    @classmethod
    def setUpClass(cls):
        cls.policy = load_frozen_policy()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="sremut-evidence-core-test-")
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def assert_code(self, code, function, *args, **kwargs):
        with self.assertRaises(Exception) as caught:
            function(*args, **kwargs)
        self.assertEqual(str(caught.exception), code)
        self.assertNotIn("secret", str(caught.exception).lower())
        return caught.exception


class PolicyAuthenticationTests(TemporaryRootCase):
    def copy_bundle(self):
        policy = self.root / "policy.yaml"
        schema = self.root / "schema.json"
        manifest = self.root / "manifest.sha256"
        shutil.copy2(POLICY_PATH, policy)
        shutil.copy2(SCHEMA_PATH, schema)
        shutil.copy2(MANIFEST_PATH, manifest)
        return policy, schema, manifest

    def load(self, paths, **kwargs):
        return load_policy_bundle(
            *paths,
            expected_manifest_sha256=POLICY_MANIFEST_SHA256,
            **kwargs,
        )

    def test_valid_frozen_bundle_and_closed_counts(self):
        self.assertEqual(len(self.policy.roles), 21)
        self.assertEqual(tuple(h.hook_id for h in self.policy.hooks), EXPECTED_HOOK_ORDER)
        self.assertEqual(len(self.policy.applicability), 19)
        self.assertEqual(self.policy.dispatcher_id, "SREMUT_FULL_ADMISSIBILITY_DISPATCHER_V1")

    def test_historical_v1_bundle_is_rejected_for_current_capture(self):
        historical = (
            REPOSITORY / "policies/missing_service_social_network/evidence-capture-v1.yaml",
            REPOSITORY / "schemas/evidence-capture-policy-v1.schema.json",
            REPOSITORY / "EVIDENCE_CAPTURE_POLICY_V1_SHA256SUMS",
        )
        self.assert_code(
            "POLICY_SUPERSEDED",
            load_policy_bundle,
            *historical,
            expected_manifest_sha256="7b99a435afbd5b8d692fa6654997a06baabd51209176b13103ecb68be21eee3d",
        )

    def test_modified_policy_byte_rejected(self):
        paths = self.copy_bundle()
        paths[0].write_bytes(paths[0].read_bytes() + b" ")
        self.assert_code("POLICY_HASH_MISMATCH", self.load, paths)

    def test_modified_schema_byte_rejected(self):
        paths = self.copy_bundle()
        paths[1].write_bytes(paths[1].read_bytes() + b" ")
        self.assert_code("POLICY_SCHEMA_HASH_MISMATCH", self.load, paths)

    def test_modified_and_malformed_manifest_rejected(self):
        for payload in (b"x", b"", b"0" * 64 + b" bad\n"):
            with self.subTest(payload=payload[:4]):
                paths = self.copy_bundle()
                paths[2].write_bytes(payload)
                self.assert_code("POLICY_MANIFEST_HASH_MISMATCH", self.load, paths)

    def test_duplicate_and_unsafe_manifest_paths_rejected(self):
        original = MANIFEST_PATH.read_bytes().splitlines(keepends=True)
        variants = (
            b"".join((original[0], original[0], original[2])),
            original[0] + original[1].replace(b"policies/", b"../x___/") + original[2],
        )
        for data in variants:
            with self.subTest(data=data[:16]):
                paths = self.copy_bundle()
                paths[2].write_bytes(data)
                self.assert_code("POLICY_MANIFEST_HASH_MISMATCH", self.load, paths)

    def test_hook_removal_addition_reordering_rejected(self):
        source = POLICY_PATH.read_text(encoding="utf-8")
        variants = (
            source.replace("  - hook_id: VALIDATE_CANONICAL_NO_FLOATS_V1\n", "", 1),
            source.replace("hook_contracts:\n", "hook_contracts:\n  - hook_id: UNKNOWN_V1\n", 1),
            source.replace("VALIDATE_CANONICAL_NO_FLOATS_V1", "VALIDATE_EVIDENCE_REF_HASH_PATH_ID_V1", 1),
        )
        for value in variants:
            with self.subTest(length=len(value)):
                paths = self.copy_bundle()
                paths[0].write_text(value, encoding="utf-8")
                self.assert_code("POLICY_HASH_MISMATCH", self.load, paths)

    def test_applicability_and_role_contract_substitution_rejected(self):
        source = POLICY_PATH.read_text(encoding="utf-8")
        variants = (
            source.replace("hook_applicability_matrix:", "hook_applicability_matrix_bypassed:", 1),
            source.replace("media_type: application/json", "media_type: text/plain", 1),
            source.replace("storage_class: DESCRIPTOR_ONLY", "storage_class: PAYLOAD_WITH_DESCRIPTOR", 1),
            source.replace("producer: RUNNER", "producer: CALLER", 1),
        )
        for value in variants:
            with self.subTest(length=len(value)):
                paths = self.copy_bundle()
                paths[0].write_text(value, encoding="utf-8")
                self.assert_code("POLICY_HASH_MISMATCH", self.load, paths)

    def test_parsed_policy_and_dispatch_overrides_rejected(self):
        paths = self.copy_bundle()
        for keyword in (
            {"parsed_policy_override": {}},
            {"hook_contracts_override": ()},
            {"applicability_override": ()},
        ):
            with self.subTest(keyword=keyword):
                self.assert_code("POLICY_PARSED_CONTENT_MISMATCH", self.load, paths, **keyword)

    def test_authentication_precedes_parsing_and_exact_symlink_bytes_are_allowed(self):
        paths = self.copy_bundle()
        paths[2].write_bytes(paths[2].read_bytes() + b" ")
        with patch("sremut.policy_runtime.yaml.safe_load") as parser:
            self.assert_code("POLICY_MANIFEST_HASH_MISMATCH", self.load, paths)
            parser.assert_not_called()

        targets = self.copy_bundle()
        links = tuple(self.root / f"link-{index}" for index in range(3))
        for link, target in zip(links, targets):
            link.symlink_to(target)
        linked = self.load(links)
        self.assertEqual(linked.manifest_sha256, POLICY_MANIFEST_SHA256)

    def test_every_hook_plan_is_unique_and_in_frozen_order(self):
        for row in self.policy.applicability:
            role = None if row.roles == ("NONE",) else row.roles[0]
            plan = self.policy.hook_plan(row.document_kind, role)
            self.assertEqual(
                tuple(hook.hook_id for hook in plan),
                tuple(hook for hook in EXPECTED_HOOK_ORDER if hook in row.hooks),
            )
            self.assertEqual(len(plan), len(set(row.hooks)))

    def test_runtime_objects_are_immutable(self):
        with self.assertRaises((TypeError, FrozenInstanceError, AttributeError)):
            self.policy.roles["run_identity"] = self.policy.roles["run_identity"]
        with self.assertRaises((TypeError, FrozenInstanceError, AttributeError)):
            self.policy.roles["run_identity"].producer = "CALLER"

    def test_dispatch_is_closed_and_missing_handlers_fail_closed(self):
        self.assert_code("UNKNOWN_DOCUMENT_ROLE", self.policy.hook_plan, "UNKNOWN_DOCUMENT_V1", None)
        candidate = {
            "document_type": "DESCRIPTOR_EVIDENCE_REF_V1",
            "schema_version": 1,
            "evidence_id": "ev-" + "1" * 32,
            "role": "challenge_invocation",
            "producer": "CHALLENGE_EXEC_ADAPTER",
            "source_kind": "KUBERNETES",
            "media_type": "application/json",
            "storage_class": "DESCRIPTOR_ONLY",
            "descriptor_sha256": "1" * 64,
            "descriptor_size_bytes": 1,
            "descriptor_relative_path": "descriptors/sha256/11/" + "1" * 64 + ".json",
            "redaction_status": "NOT_REDACTED",
        }
        result = self.policy.full_admissibility(candidate, {})
        self.assertFalse(result.valid)
        self.assertEqual(result.failure_code, "RESOLVED_CONTEXT_INVALID")
        self.assertEqual(result.hook_id, "VALIDATE_DESCRIPTOR_CONTENT_IDENTITY_V1")

    def test_full_dispatch_requires_context_and_success_result_is_not_forgeable(self):
        candidate = yaml.safe_load(self.policy.policy_bytes)
        missing = self.policy.full_admissibility(candidate)
        self.assertFalse(missing.valid)
        self.assertEqual(missing.failure_code, "MISSING_RESOLVED_CONTEXT")
        supplied_but_unsupported = self.policy.full_admissibility(candidate, {})
        self.assertFalse(supplied_but_unsupported.valid)
        self.assertEqual(supplied_but_unsupported.failure_code, "RESOLVED_CONTEXT_INVALID")
        with self.assertRaises(TypeError):
            ValidationResult(True, self.policy.dispatcher_id, None, None, None)

    def test_coordinated_bundle_replacement_and_manifest_grammar_rejected(self):
        paths = self.copy_bundle()
        paths[0].write_bytes(paths[0].read_bytes() + b" ")
        paths[1].write_bytes(paths[1].read_bytes() + b" ")
        rows = MANIFEST_PATH.read_text(encoding="ascii").splitlines()
        replacements = {
            "policies/missing_service_social_network/evidence-capture-v1.1.yaml": sha256_hex(paths[0].read_bytes()),
            "schemas/evidence-capture-policy-v1.1.schema.json": sha256_hex(paths[1].read_bytes()),
        }
        manifest = "".join(
            f"{replacements.get(path, digest)}  {path}\n"
            for digest, path in (line.split("  ", 1) for line in rows)
        ).encode("ascii")
        paths[2].write_bytes(manifest)
        with self.assertRaises(PolicyRuntimeError) as caught:
            load_policy_bundle(*paths, expected_manifest_sha256=sha256_hex(manifest))
        self.assertEqual(str(caught.exception), "POLICY_MANIFEST_HASH_MISMATCH")

        original = MANIFEST_PATH.read_bytes().splitlines(keepends=True)
        malformed = (
            b"".join((original[0], original[0], original[2])),
            original[0] + original[1].replace(b"policies/", b"../escape/") + original[2],
            original[0] + original[1].replace(b"policies/", b"/absolute/") + original[2],
            original[0].upper() + original[1] + original[2],
            original[0].replace(original[0][:64], b"g" * 64) + original[1] + original[2],
        )
        for value in malformed:
            with self.subTest(value=value[:80]):
                with self.assertRaises(PolicyRuntimeError):
                    _parse_manifest(value)


class CanonicalJSONTests(unittest.TestCase):
    def assert_code(self, code, function, *args, **kwargs):
        with self.assertRaises(CanonicalJSONError) as caught:
            function(*args, **kwargs)
        self.assertEqual(str(caught.exception), code)

    def test_deterministic_mapping_array_and_separators(self):
        value = {"z": [3, 2, 1], "a": {"b": True, "a": None}}
        self.assertEqual(canonical_json_bytes(value), b'{"a":{"a":null,"b":true},"z":[3,2,1]}')
        self.assertNotIn(b" ", canonical_json_bytes(value))

    def test_unicode_is_utf8_not_ascii_escaped(self):
        self.assertEqual(canonical_json_bytes({"value": "\u03bb"}), '{"value":"\u03bb"}'.encode("utf-8"))

    def test_invalid_utf8_rejected(self):
        self.assert_code("CANONICAL_INVALID_JSON", parse_canonical_json, b'{"x":"\xff"}')

    def test_floats_nan_and_infinity_rejected(self):
        for value in (1.0, float("nan"), float("inf"), -float("inf")):
            with self.subTest(value=value):
                self.assert_code("CANONICAL_FLOAT_FORBIDDEN", canonical_json_bytes, {"x": value})
        for raw in (b'{"x":1.0}', b'{"x":NaN}', b'{"x":Infinity}'):
            with self.subTest(raw=raw):
                self.assert_code("CANONICAL_FLOAT_FORBIDDEN", parse_canonical_json, raw)

    def test_boolean_distinct_from_integer_and_nonstring_key_rejected(self):
        self.assertEqual(canonical_json_bytes({"a": True, "b": 1}), b'{"a":true,"b":1}')
        self.assert_code("CANONICAL_NON_STRING_KEY", canonical_json_bytes, {1: "x"})
        self.assert_code("CANONICAL_UNSUPPORTED_TYPE", canonical_json_bytes, {"x": (1,)})

    def test_newline_contract(self):
        value = {"a": 1}
        self.assertEqual(canonical_json_line(value), b'{"a":1}\n')
        self.assertEqual(parse_canonical_json(b'{"a":1}\n', line=True), value)
        self.assert_code("CANONICAL_NEWLINE_INVALID", parse_canonical_json, b'{"a":1}\n')
        self.assert_code("CANONICAL_NEWLINE_INVALID", parse_canonical_json, b'{"a":1}', line=True)

    def test_noncanonical_key_order_and_whitespace_rejected(self):
        self.assert_code("CANONICAL_ENCODING_MISMATCH", parse_canonical_json, b'{"b":2, "a":1}')
        self.assert_code("CANONICAL_ENCODING_MISMATCH", parse_canonical_json, b'{"b":2,"a":1}')

    def test_closed_parser_rejects_duplicates_surrogates_trailing_and_negative_zero(self):
        cases = (
            b'{"a":1,"a":1}',
            b'{"a":1}x',
            b'{"x":-0}',
            b'{"x":-0.0}',
            b'{"x":"\\ud800"}',
            b'{"a":1}\n\n',
        )
        for value in cases:
            with self.subTest(value=value):
                with self.assertRaises(CanonicalJSONError) as caught:
                    parse_canonical_json(value)
                self.assertNotIn(value.decode("ascii", errors="ignore"), str(caught.exception))
        canonical = canonical_json_bytes({"a": [True, 1, None], "z": "value"})
        self.assertEqual(canonical_json_bytes(parse_canonical_json(canonical)), canonical)

    def test_identical_values_have_identical_bytes_and_hashes(self):
        left = {"b": [1, 2], "a": "\u03bb"}
        right = {"a": "\u03bb", "b": [1, 2]}
        self.assertEqual(canonical_json_bytes(left), canonical_json_bytes(right))
        self.assertEqual(canonical_sha256(left), canonical_sha256(right))
        self.assertEqual(canonical_sha256(left), sha256_hex(canonical_json_bytes(left)))


class SensitiveMaterialTests(TemporaryRootCase):
    def test_all_frozen_positive_classes_detect(self):
        structured = (
            {"apiVersion": "v1", "kind": "Secret"},
            {
                "apiVersion": "v1",
                "kind": "Config",
                "clusters": [],
                "contexts": [],
                "users": [{"user": {"exec": {"command": "x"}}}],
            },
            {"token": "populated"},
            {"nested": [{"client_secret": "populated"}]},
            [{"token": "secret-value"}],
        )
        positives = [
            (b"Authorization: credential", None),
            (b"Bearer abcdefghijklmnop", None),
            (b"-----BEGIN PRIVATE KEY-----", None),
            (b"https://user:credential@example.invalid/x", None),
            (b"HTTPS://user:credential@example.invalid/", None),
            (b"Authorization: credential\r\n", None),
            (b"TOKEN=value", None),
            (b"A=1\nB=2\nC=3\nD=4", None),
        ]
        positives.extend((canonical_json_bytes(value), "json") for value in structured)
        self.assertEqual(len(positives), 13)
        for data, kind in positives:
            with self.subTest(data=data[:20]):
                finding = detect_sensitive(data, structured_format=kind)
                self.assertIsNotNone(finding)
                self.assertTrue(finding.rejection_code.startswith("CAPTURE_REJECTED_"))

    def test_all_frozen_false_positive_controls_accepted(self):
        structured = (
            {"username": "ordinary", "exec": "ordinary"},
            {"nested": [{"username": "ordinary", "exec": "ordinary"}]},
        )
        negatives = [
            (b"token_count=4", None),
            (b"password_policy_text=long", None),
            (b"automountServiceAccountToken=false", None),
            (b"public certificate fingerprint 00:11", None),
            (b'print("Authorization: example")', None),
            (b"A=1\nB=2\nC=3", None),
            (b"TOKEN=", None),
            (b"https://user:@example.invalid/", None),
        ]
        negatives.extend((canonical_json_bytes(value), "json") for value in structured)
        self.assertEqual(len(negatives), 10)
        for data, kind in negatives:
            with self.subTest(data=data[:20]):
                self.assertIsNone(detect_sensitive(data, structured_format=kind))

    def test_list_root_and_recursive_nested_mapping(self):
        self.assertIsNotNone(detect_sensitive(b'[{"token":"value"}]', structured_format="json"))
        self.assertIsNotNone(
            detect_sensitive(b'{"a":[{"b":{"private_key":"value"}}]}', structured_format="json")
        )

    def test_duplicate_structured_keys_cannot_hide_sensitive_values(self):
        for data, kind in (
            (b'{"token":"credential-do-not-leak","token":""}', "json"),
            (b'token: credential-do-not-leak\ntoken: ""\n', "yaml"),
        ):
            with self.subTest(kind=kind):
                self.assert_code(
                    "CAPTURE_REJECTED_INVALID_ENCODING",
                    detect_sensitive,
                    data,
                    structured_format=kind,
                )

    def test_projection_token_array_and_dotted_key_semantics(self):
        validate_projection_allowlist(
            {"items": [{"metadata.name": "pod"}]},
            (("items", "[]", "metadata.name"),),
        )
        self.assert_code(
            "CAPTURE_REJECTED_UNAUTHORIZED_SOURCE",
            validate_projection_allowlist,
            {"items": [{"metadata": {"name": "pod"}}]},
            (("items", "[]", "metadata.name"),),
        )

    def test_invalid_utf8_for_strict_role_rejected(self):
        self.assert_code(
            "CAPTURE_REJECTED_INVALID_ENCODING",
            validate_payload,
            self.policy,
            "challenge_stdout",
            b"\xff",
        )

    def test_size_rejection_precedes_detector_parsing(self):
        with patch("sremut.sensitive.detect_sensitive") as detector:
            self.assert_code(
                "CAPTURE_REJECTED_SIZE",
                validate_payload,
                self.policy,
                "challenge_stdout",
                b"x" * 16385,
                structured_format="json",
            )
            detector.assert_not_called()

    def test_zero_byte_and_maximum_size_enforced(self):
        validate_payload(self.policy, "challenge_stdout", b"")
        self.assert_code(
            "CAPTURE_REJECTED_SIZE",
            validate_payload,
            self.policy,
            "original_oracle_input",
            b"",
        )
        validate_payload(self.policy, "challenge_stdout", b"x" * 16384)
        self.assert_code(
            "CAPTURE_REJECTED_SIZE",
            validate_payload,
            self.policy,
            "challenge_stdout",
            b"x" * 16385,
        )

    def test_media_storage_and_redaction_are_closed(self):
        self.assert_code(
            "CAPTURE_REJECTED_UNAUTHORIZED_SOURCE",
            validate_payload,
            self.policy,
            "challenge_stdout",
            b"x",
            media_type="application/json",
        )
        self.assert_code(
            "CAPTURE_REJECTED_UNAUTHORIZED_SOURCE",
            validate_payload,
            self.policy,
            "challenge_invocation",
            b"x",
        )
        self.assert_code("CAPTURE_REJECTED_UNAUTHORIZED_SOURCE", validate_redaction_status, "REDACTED")

    def test_projection_allowlist_accepts_only_approved_fields(self):
        allowed = (("metadata", "name"), ("items", "[]", "metadata", "uid"))
        validate_projection_allowlist(
            {"metadata": {"name": "x"}, "items": [{"metadata": {"uid": "u"}}]},
            allowed,
        )
        self.assert_code(
            "CAPTURE_REJECTED_UNAUTHORIZED_SOURCE",
            validate_projection_allowlist,
            {"metadata": {"name": "x", "labels": {}}},
            allowed,
        )

    def test_errors_never_include_rejected_content(self):
        marker = "credential-do-not-leak"
        with self.assertRaises(SensitiveCaptureError) as caught:
            validate_payload(
                self.policy,
                "challenge_stdout",
                f"Authorization: {marker}".encode(),
            )
        self.assertNotIn(marker, str(caught.exception))
        self.assertNotIn("/", str(caught.exception))


class JournalTests(TemporaryRootCase):
    def append(self, journal, transition, sequence):
        return journal.append_state_transition(
            transition,
            utc_time=CREATED_UTC,
            monotonic_ns=sequence + 1,
            boot_identity=BOOT_IDENTITY,
        )

    def make_valid_three_record_chain(self):
        with Journal(self.root, self.policy, RUN_ID, ATTEMPT_ID) as journal:
            self.append(journal, "CREATED->PREFLIGHT_PASS", 0)
            self.append(journal, "PREFLIGHT_PASS->HEALTHY_STATE_CAPTURED", 1)
            self.append(journal, "HEALTHY_STATE_CAPTURED->ABORTED_SAFE", 2)

    def journal_path(self):
        return self.root / "journal/attempt.jsonl"

    def test_valid_chain_and_deterministic_reconstruction(self):
        self.make_valid_three_record_chain()
        with Journal(self.root, self.policy, RUN_ID, ATTEMPT_ID) as journal:
            first = journal.reconstruct()
            second = journal.reconstruct()
        self.assertEqual(first.records, second.records)
        self.assertEqual(first.state, "ABORTED_SAFE")
        self.assertTrue(first.terminal)
        self.assertEqual(first.sequence_number, 2)

    def test_tampered_record_rejected(self):
        self.make_valid_three_record_chain()
        lines = self.journal_path().read_bytes().splitlines(keepends=True)
        record = json.loads(lines[1])
        record["monotonic_ns"] = 999
        lines[1] = canonical_json_line(record)
        self.journal_path().write_bytes(b"".join(lines))
        with Journal(self.root, self.policy, RUN_ID, ATTEMPT_ID) as journal:
            self.assert_code("JOURNAL_CHAIN_INVALID", journal.reconstruct)

    def test_missing_reordered_and_duplicate_record_rejected(self):
        self.make_valid_three_record_chain()
        original = self.journal_path().read_bytes().splitlines(keepends=True)
        variants = (original[:1] + original[2:], [original[1], original[0], original[2]], original[:2] + [original[1]] + original[2:])
        for index, lines in enumerate(variants):
            with self.subTest(index=index):
                self.journal_path().write_bytes(b"".join(lines))
                with Journal(self.root, self.policy, RUN_ID, ATTEMPT_ID) as journal:
                    self.assert_code("JOURNAL_CHAIN_INVALID", journal.reconstruct)
                self.journal_path().write_bytes(b"".join(original))

    def test_incorrect_previous_hash_rejected(self):
        self.make_valid_three_record_chain()
        lines = self.journal_path().read_bytes().splitlines(keepends=True)
        record = json.loads(lines[1])
        record["previous_entry_sha256"] = "f" * 64
        record["canonical_current_entry_sha256"] = record_sha256(record)
        lines[1] = canonical_json_line(record)
        self.journal_path().write_bytes(b"".join(lines))
        with Journal(self.root, self.policy, RUN_ID, ATTEMPT_ID) as journal:
            self.assert_code("JOURNAL_CHAIN_INVALID", journal.reconstruct)

    def test_torn_final_and_malformed_middle_rejected(self):
        self.make_valid_three_record_chain()
        original = self.journal_path().read_bytes()
        variants = (original[:-1], original.splitlines(keepends=True)[0] + b"not-json\n" + b"".join(original.splitlines(keepends=True)[2:]))
        for index, value in enumerate(variants):
            with self.subTest(index=index):
                self.journal_path().write_bytes(value)
                with Journal(self.root, self.policy, RUN_ID, ATTEMPT_ID) as journal:
                    self.assert_code("JOURNAL_CANONICALIZATION_INVALID", journal.reconstruct)
                self.journal_path().write_bytes(original)

    def test_mutable_pointer_is_never_authoritative(self):
        self.make_valid_three_record_chain()
        pointer = self.root / "state/current.json"
        pointer.parent.mkdir()
        pointer.write_bytes(b'{"state":"CREATED"}')
        with Journal(self.root, self.policy, RUN_ID, ATTEMPT_ID) as journal:
            state = journal.reconstruct(mutable_pointer_relative_path="state/current.json")
        self.assertEqual(state.state, "ABORTED_SAFE")

    def test_exclusive_lock_contention_fails_closed(self):
        with SafeRoot(self.root) as locked:
            with locked.lock():
                with Journal(self.root, self.policy, RUN_ID, ATTEMPT_ID) as journal:
                    self.assert_code(
                        "JOURNAL_LOCK_UNAVAILABLE",
                        self.append,
                        journal,
                        "CREATED->ABORTED_SAFE",
                        0,
                    )

    def test_public_append_cannot_assert_a_verified_state(self):
        with Journal(self.root, self.policy, RUN_ID, ATTEMPT_ID) as journal:
            self.assert_code(
                "JOURNAL_STATE_DERIVATION_FAILED",
                self.append,
                journal,
                "STATE_VERIFIED:FINALIZED",
                0,
            )
        self.assertFalse(self.journal_path().exists())

    def test_interrupted_and_partial_writes_are_retried(self):
        original = os.write
        calls = {"count": 0}

        def interrupted_then_partial(descriptor, data):
            calls["count"] += 1
            if calls["count"] == 1:
                raise InterruptedError()
            return original(descriptor, data[:1])

        with SafeRoot(self.root) as fs:
            with patch("sremut.journal.os.write", interrupted_then_partial):
                fs.write_atomic("objects/value", b"complete")
        self.assertEqual((self.root / "objects/value").read_bytes(), b"complete")
        self.assertGreater(calls["count"], 2)

    def test_atomic_publication_never_overwrites_a_racing_destination(self):
        target = self.root / "objects/value"
        sentinel = b"outside-sentinel"
        original_rename = os.rename
        original_link = os.link

        def create_target():
            target.write_bytes(sentinel)

        def racing_rename(source, destination, **kwargs):
            create_target()
            return original_rename(source, destination, **kwargs)

        def racing_link(source, destination, **kwargs):
            create_target()
            return original_link(source, destination, **kwargs)

        with SafeRoot(self.root) as fs:
            with patch("sremut.journal.os.rename", racing_rename), patch(
                "sremut.journal.os.link", racing_link
            ):
                self.assert_code(
                    "EVIDENCE_ALREADY_EXISTS",
                    fs.write_atomic,
                    "objects/value",
                    b"published",
                )
        self.assertEqual(target.read_bytes(), sentinel)

    def test_seal_does_not_reopen_a_replaced_root_path(self):
        attempt = self.root / "attempt"
        attempt.mkdir()
        store = EvidenceStore(attempt, self.policy, RUN_ID, ATTEMPT_ID)
        self.addCleanup(store.close)
        with Journal(attempt, self.policy, RUN_ID, ATTEMPT_ID) as journal:
            self.append(journal, "CREATED->PREFLIGHT_PASS", 0)
        original = self.root / "original-attempt"
        attempt.rename(original)
        attempt.mkdir()
        with Journal(attempt, self.policy, RUN_ID, ATTEMPT_ID) as replacement:
            self.append(replacement, "CREATED->ABORTED_SAFE", 0)
        replacement_bytes = (attempt / "journal/attempt.jsonl").read_bytes()
        self.assert_code("ATTEMPT_FINALITY_INVALID", store.seal, "ABORTED_SAFE")
        self.assertEqual((attempt / "journal/attempt.jsonl").read_bytes(), replacement_bytes)
        self.assertFalse((original / "manifests/terminal.sha256").exists())

    def test_two_process_lock_contention_fails_closed(self):
        ready_read, ready_write = os.pipe()
        release_read, release_write = os.pipe()
        pid = os.fork()
        if pid == 0:
            try:
                os.close(ready_read)
                os.close(release_write)
                with SafeRoot(self.root) as child_root:
                    with child_root.lock():
                        os.write(ready_write, b"1")
                        os.read(release_read, 1)
            finally:
                os._exit(0)
        os.close(ready_write)
        os.close(release_read)
        try:
            self.assertEqual(os.read(ready_read, 1), b"1")
            with SafeRoot(self.root) as parent_root:
                self.assert_code("JOURNAL_LOCK_UNAVAILABLE", parent_root.lock().__enter__)
        finally:
            os.write(release_write, b"1")
            os.close(ready_read)
            os.close(release_write)
            waited, status = os.waitpid(pid, 0)
        self.assertEqual(waited, pid)
        self.assertEqual(status, 0)

    def test_path_and_preexisting_hardlink_substitutions_preserve_sentinel(self):
        attempt = self.root / "attempt"
        attempt.mkdir()
        outside = self.root / "outside-sentinel"
        outside.write_bytes(b"unchanged")
        linked = attempt / "linked"
        os.link(outside, linked)
        with SafeRoot(attempt) as fs:
            for value in ("../escape", "/absolute", "a//b", "a/./b", ""):
                with self.subTest(value=value):
                    self.assert_code("EVIDENCE_PATH_INVALID", fs.write_atomic, value, b"x")
            self.assert_code("EVIDENCE_ALREADY_EXISTS", fs.write_atomic, "linked", b"replacement")
        self.assertEqual(outside.read_bytes(), b"unchanged")
        self.assertEqual(linked.read_bytes(), b"unchanged")

    def test_symlinked_root_component_refused(self):
        actual = self.root / "actual"
        actual.mkdir()
        link = self.root / "link"
        link.symlink_to(actual, target_is_directory=True)
        self.assert_code("EVIDENCE_SYMLINK_REFUSED", SafeRoot, link)

    def test_final_target_symlink_and_path_traversal_refused(self):
        journal_dir = self.root / "journal"
        journal_dir.mkdir()
        outside = self.root / "outside"
        outside.write_bytes(b"")
        (journal_dir / "attempt.jsonl").symlink_to(outside)
        with Journal(self.root, self.policy, RUN_ID, ATTEMPT_ID) as journal:
            self.assert_code(
                "EVIDENCE_SYMLINK_REFUSED",
                self.append,
                journal,
                "CREATED->ABORTED_SAFE",
                0,
            )
        self.assert_code(
            "EVIDENCE_PATH_INVALID",
            Journal,
            self.root,
            self.policy,
            RUN_ID,
            ATTEMPT_ID,
            relative_path="../journal",
        )

    def test_capture_rejected_record_is_privacy_minimal(self):
        with Journal(self.root, self.policy, RUN_ID, ATTEMPT_ID) as journal:
            record = journal.append_capture_rejected(
                intended_role="challenge_stdout",
                rejection_code="CAPTURE_REJECTED_BEARER",
                detector_id="SENSITIVE_BEARER_CREDENTIAL",
                source_kind="KUBERNETES",
                observed_size=42,
                created_utc=CREATED_UTC,
                monotonic_ns=1,
                boot_identity=BOOT_IDENTITY,
            )
        forbidden = {
            "payload_sha256",
            "descriptor_sha256",
            "matched_bytes",
            "absolute_path",
            "rejected_value",
        }
        self.assertFalse(forbidden & set(record))

    def test_invalid_capture_rejection_is_not_auto_repaired(self):
        with Journal(self.root, self.policy, RUN_ID, ATTEMPT_ID) as journal:
            self.assert_code(
                "CAPTURE_REJECTION_PRIVACY_INVALID",
                journal.append_capture_rejected,
                intended_role="challenge_stdout",
                rejection_code="INVALID",
                detector_id="SENSITIVE_BEARER_CREDENTIAL",
                source_kind="KUBERNETES",
                observed_size=1,
                created_utc=CREATED_UTC,
                monotonic_ns=1,
                boot_identity=BOOT_IDENTITY,
            )
        self.assertFalse(self.journal_path().exists())

    def test_invalid_event_context_fields_rejected(self):
        with Journal(self.root, self.policy, RUN_ID, ATTEMPT_ID) as journal:
            self.assert_code(
                "JOURNAL_CANONICALIZATION_INVALID",
                journal.append_state_transition,
                "CREATED->ABORTED_SAFE",
                utc_time="2026-08-20T10:00:00Z",
                monotonic_ns=1,
                boot_identity=BOOT_IDENTITY,
            )
            self.assert_code(
                "CAPTURE_REJECTION_PRIVACY_INVALID",
                journal.append_capture_rejected,
                intended_role="challenge_stdout",
                rejection_code="CAPTURE_REJECTED_BEARER",
                detector_id="SENSITIVE_BEARER_CREDENTIAL",
                source_kind="SUBPROCESS",
                observed_size=1,
                created_utc=CREATED_UTC,
                monotonic_ns=1,
                boot_identity=BOOT_IDENTITY,
            )
        self.assertFalse(self.journal_path().exists())

    def test_post_terminal_append_rejected_without_repair(self):
        with Journal(self.root, self.policy, RUN_ID, ATTEMPT_ID) as journal:
            self.append(journal, "CREATED->ABORTED_SAFE", 0)
            before = self.journal_path().read_bytes()
            self.assert_code(
                "POST_TERMINAL_OPERATION",
                self.append,
                journal,
                "CREATED->PREFLIGHT_PASS",
                1,
            )
            self.assertEqual(self.journal_path().read_bytes(), before)


class EvidenceTests(TemporaryRootCase):
    def publish_input(self, store, payload=b'{"ok":true}'):
        return store.publish_payload(
            "original_oracle_input",
            payload,
            common_metadata(invocation_ordinal=1),
        )

    def publish_invocation(self, store):
        return store.publish_descriptor("challenge_invocation", challenge_metadata())

    def terminal_journal(self, outcome):
        with Journal(self.root, self.policy, RUN_ID, ATTEMPT_ID) as journal:
            if outcome == "ABORTED_SAFE":
                journal.append_state_transition(
                    "CREATED->ABORTED_SAFE",
                    utc_time=CREATED_UTC,
                    monotonic_ns=1,
                    boot_identity=BOOT_IDENTITY,
                )
            elif outcome == "RESTORATION_BLOCKED":
                transitions = (
                    "CREATED->PREFLIGHT_PASS",
                    "PREFLIGHT_PASS->HEALTHY_STATE_CAPTURED",
                    "HEALTHY_STATE_CAPTURED->MUTANT_INJECTED",
                    "MUTANT_INJECTED->MUTANT_STATE_VERIFIED",
                    "MUTANT_STATE_VERIFIED->RESTORE_STARTED",
                    "RESTORE_STARTED->RESTORATION_BLOCKED",
                )
                for index, transition in enumerate(transitions, 1):
                    journal.append_state_transition(
                        transition,
                        utc_time=CREATED_UTC,
                        monotonic_ns=index,
                        boot_identity=BOOT_IDENTITY,
                    )
            else:
                transitions = (
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
                for index, transition in enumerate(transitions, 1):
                    journal.append_state_transition(
                        transition,
                        utc_time=CREATED_UTC,
                        monotonic_ns=index,
                        boot_identity=BOOT_IDENTITY,
                    )

    def seal(self, outcome, with_payload=False):
        with EvidenceStore(self.root, self.policy, RUN_ID, ATTEMPT_ID) as store:
            if with_payload:
                self.publish_input(store)
            self.terminal_journal(outcome)
            global_stop = None
            if outcome == "RESTORATION_BLOCKED":
                global_stop = store.write_global_stop(terminal_global_stop())
            result = store.seal(outcome, global_stop_relative_path=global_stop)
        return result

    def anchor(self, seal):
        return ExternalAnchor(
            attempt_root_identifier="attempt-root-fixed",
            manifest_relative_path=seal.manifest_relative_path,
            terminal_manifest_sha256=seal.manifest_sha256,
        )

    def test_payload_descriptor_and_reference_are_immutable_and_bound(self):
        with EvidenceStore(self.root, self.policy, RUN_ID, ATTEMPT_ID) as store:
            reference = self.publish_input(store)
            resolved = store.resolve(reference)
            self.assertEqual(resolved[2], b'{"ok":true}')
            self.assertEqual(sha256_hex(resolved[2]), reference.payload_sha256)
            self.assert_code(
                "EVIDENCE_ALREADY_EXISTS",
                store.publish_payload,
                "original_oracle_input",
                b'{"ok":true}',
                common_metadata(invocation_ordinal=1),
            )

    def test_descriptor_only_role_and_absent_reference(self):
        with EvidenceStore(self.root, self.policy, RUN_ID, ATTEMPT_ID) as store:
            reference = self.publish_invocation(store)
            resolved = store.resolve(reference)
            self.assertIsNone(resolved[2])
            self.assertIsNone(store.resolve(None))
            self.assertEqual(reference.storage_class, "DESCRIPTOR_ONLY")

    def test_present_zero_byte_payload_is_not_absent(self):
        with EvidenceStore(self.root, self.policy, RUN_ID, ATTEMPT_ID) as store:
            invocation = self.publish_invocation(store)
            reference = store.publish_payload(
                "challenge_stdout",
                b"",
                common_metadata(invocation_reference=invocation.as_dict(), channel="STDOUT"),
            )
            resolved = store.resolve(reference)
        self.assertEqual(reference.payload_sha256, ZERO_SHA256)
        self.assertEqual(reference.payload_size_bytes, 0)
        self.assertEqual(resolved[2], b"")

    def test_wrong_role_producer_media_and_storage_rejected(self):
        with EvidenceStore(self.root, self.policy, RUN_ID, ATTEMPT_ID) as store:
            reference = self.publish_input(store)
            _, descriptor, payload = store.resolve(reference)
        variants = []
        for field, value in (
            ("role", "challenge_stdout"),
            ("producer", "CALLER"),
            ("media_type", "text/plain"),
            ("storage_class", "DESCRIPTOR_ONLY"),
        ):
            changed = reference.as_dict()
            changed[field] = value
            variants.append(changed)
        for value in variants:
            with self.subTest(value=value):
                self.assert_code(
                    "EVIDENCE_REFERENCE_INVALID",
                    validate_evidence_ref,
                    self.policy,
                    value,
                    descriptor_bytes=descriptor,
                    payload_bytes=payload,
                )

    def test_maximum_size_boundary_and_overflow(self):
        with EvidenceStore(self.root, self.policy, RUN_ID, ATTEMPT_ID) as store:
            invocation = self.publish_invocation(store)
            metadata = common_metadata(invocation_reference=invocation.as_dict(), channel="STDOUT")
            reference = store.publish_payload("challenge_stdout", b"x" * 16384, metadata)
            self.assertEqual(reference.payload_size_bytes, 16384)
        other = Path(tempfile.mkdtemp(prefix="sremut-evidence-core-overflow-"))
        self.addCleanup(shutil.rmtree, other)
        with EvidenceStore(other, self.policy, RUN_ID, ATTEMPT_ID) as store:
            invocation = self.publish_invocation(store)
            self.assert_code(
                "CAPTURE_REJECTED_SIZE",
                store.publish_payload,
                "challenge_stdout",
                b"x" * 16385,
                common_metadata(invocation_reference=invocation.as_dict(), channel="STDOUT"),
            )

    def test_wrong_payload_and_descriptor_hash_rejected(self):
        with EvidenceStore(self.root, self.policy, RUN_ID, ATTEMPT_ID) as store:
            reference = self.publish_input(store)
            _, descriptor, payload = store.resolve(reference)
        payload_bad = reference.as_dict()
        payload_bad["payload_sha256"] = "f" * 64
        payload_bad["payload_relative_path"] = "objects/sha256/ff/" + "f" * 64
        self.assert_code(
            "PAYLOAD_HASH_MISMATCH",
            validate_evidence_ref,
            self.policy,
            payload_bad,
            descriptor_bytes=descriptor,
            payload_bytes=payload,
        )
        descriptor_bad = reference.as_dict()
        descriptor_bad["descriptor_sha256"] = "f" * 64
        descriptor_bad["evidence_id"] = "ev-" + "f" * 32
        descriptor_bad["descriptor_relative_path"] = "descriptors/sha256/ff/" + "f" * 64 + ".json"
        self.assert_code(
            "DESCRIPTOR_HASH_MISMATCH",
            validate_evidence_ref,
            self.policy,
            descriptor_bad,
            descriptor_bytes=descriptor,
            payload_bytes=payload,
        )

    def test_cross_attempt_reference_and_path_traversal_rejected(self):
        with EvidenceStore(self.root, self.policy, RUN_ID, ATTEMPT_ID) as store:
            reference = self.publish_input(store)
            _, descriptor, payload = store.resolve(reference)
        self.assert_code(
            "RUN_ATTEMPT_MISMATCH",
            validate_evidence_ref,
            self.policy,
            reference,
            descriptor_bytes=descriptor,
            payload_bytes=payload,
            expected_run_id=RUN_ID,
            expected_attempt_id="a02",
        )
        changed = reference.as_dict()
        changed["descriptor_relative_path"] = "../descriptor"
        self.assert_code("EVIDENCE_REFERENCE_INVALID", validate_evidence_ref, self.policy, changed)

    def test_symlinked_descriptor_is_unresolved(self):
        with EvidenceStore(self.root, self.policy, RUN_ID, ATTEMPT_ID) as store:
            reference = self.publish_input(store)
            descriptor_path = self.root / reference.descriptor_relative_path
            descriptor_bytes = descriptor_path.read_bytes()
            descriptor_path.unlink()
            outside = self.root / "outside.json"
            outside.write_bytes(descriptor_bytes)
            descriptor_path.symlink_to(outside)
            self.assert_code("EVIDENCE_REFERENCE_UNRESOLVED", store.resolve, reference)

    def test_duplicate_identity_cannot_be_silently_adopted(self):
        with EvidenceStore(self.root, self.policy, RUN_ID, ATTEMPT_ID) as store:
            first = self.publish_invocation(store)
            self.assert_code(
                "EVIDENCE_ALREADY_EXISTS",
                store.publish_descriptor,
                "challenge_invocation",
                challenge_metadata(),
            )
            self.assertIsNotNone(store.resolve(first))

    def test_terminal_manifest_is_installed_last_and_has_no_self_hash(self):
        seal = self.seal("ABORTED_SAFE")
        manifest = (self.root / seal.manifest_relative_path).read_bytes()
        self.assertNotIn(b"manifests/terminal.sha256", manifest)
        self.assertNotIn(seal.manifest_sha256.encode(), manifest)
        with EvidenceStore(self.root, self.policy, RUN_ID, ATTEMPT_ID) as store:
            self.assert_code("ATTEMPT_ALREADY_SEALED", self.publish_invocation, store)

    def test_missing_descriptor_and_orphan_payload_coverage_rejected(self):
        with EvidenceStore(self.root, self.policy, RUN_ID, ATTEMPT_ID) as store:
            reference = self.publish_input(store)
            (self.root / reference.descriptor_relative_path).unlink()
            self.terminal_journal("ABORTED_SAFE")
            self.assert_code("TERMINAL_MANIFEST_COVERAGE_MISMATCH", store.seal, "ABORTED_SAFE")

    def test_terminal_manifest_self_reference_rejected(self):
        seal = self.seal("ABORTED_SAFE")
        manifest_path = self.root / seal.manifest_relative_path
        manifest_path.write_bytes(
            ("0" * 64 + "  manifests/terminal.sha256\n").encode()
        )
        bad_anchor = replace(self.anchor(seal), terminal_manifest_sha256=sha256_hex(manifest_path.read_bytes()))
        self.assert_code(
            "EXTERNAL_MANIFEST_INVALID",
            revalidate_sealed_attempt,
            self.root,
            self.policy,
            RUN_ID,
            ATTEMPT_ID,
            bad_anchor,
        )

    def test_finalized_aborted_and_restoration_blocked_classifications(self):
        expected = {
            "FINALIZED": "COMPLETE",
            "ABORTED_SAFE": "COMPLETE",
            "RESTORATION_BLOCKED": "SEALED_PARTIAL_WITH_GLOBAL_STOP",
        }
        for outcome, classification in expected.items():
            with self.subTest(outcome=outcome):
                root = Path(tempfile.mkdtemp(prefix="sremut-evidence-core-terminal-"))
                self.addCleanup(shutil.rmtree, root)
                old = self.root
                self.root = root
                try:
                    seal = self.seal(outcome)
                    verified = revalidate_sealed_attempt(
                        root,
                        self.policy,
                        RUN_ID,
                        ATTEMPT_ID,
                        self.anchor(seal),
                    )
                    self.assertEqual(seal.classification, classification)
                    self.assertEqual(verified.classification, classification)
                    for result in (seal, verified):
                        serialized = asdict(result)
                        self.assertEqual(serialized["validation_level"], "STRUCTURAL_SCHEMA_VALIDATION")
                        self.assertFalse(serialized["full_admissibility"])
                        self.assertEqual(
                            serialized["semantic_validation_status"],
                            "RUNNER_IMPLEMENTATION_INCOMPLETE",
                        )
                finally:
                    self.root = old

    def test_external_anchor_mismatch_rejected(self):
        seal = self.seal("ABORTED_SAFE")
        anchor = replace(self.anchor(seal), terminal_manifest_sha256="f" * 64)
        self.assert_code(
            "EXTERNAL_MANIFEST_HASH_MISMATCH",
            revalidate_sealed_attempt,
            self.root,
            self.policy,
            RUN_ID,
            ATTEMPT_ID,
            anchor,
        )

    def test_consistent_internal_seal_cannot_satisfy_other_anchor(self):
        first = self.seal("ABORTED_SAFE", with_payload=True)
        first_anchor = self.anchor(first)
        second_root = Path(tempfile.mkdtemp(prefix="sremut-evidence-core-second-"))
        self.addCleanup(shutil.rmtree, second_root)
        old = self.root
        self.root = second_root
        try:
            with EvidenceStore(second_root, self.policy, RUN_ID, ATTEMPT_ID) as store:
                self.publish_input(store, b'{"ok":false}')
            second = self.seal("ABORTED_SAFE")
            second_anchor = self.anchor(second)
            self.assertNotEqual(first_anchor.terminal_manifest_sha256, second_anchor.terminal_manifest_sha256)
            self.assert_code(
                "EXTERNAL_MANIFEST_HASH_MISMATCH",
                revalidate_sealed_attempt,
                second_root,
                self.policy,
                RUN_ID,
                ATTEMPT_ID,
                first_anchor,
            )
            self.assertEqual(
                revalidate_sealed_attempt(
                    second_root,
                    self.policy,
                    RUN_ID,
                    ATTEMPT_ID,
                    second_anchor,
                ).terminal_outcome,
                "ABORTED_SAFE",
            )
        finally:
            self.root = old

    def test_corruption_after_sealing_rejected(self):
        seal = self.seal("ABORTED_SAFE", with_payload=True)
        object_path = next((self.root / "objects").rglob("*"))
        if object_path.is_dir():
            object_path = next(path for path in (self.root / "objects").rglob("*") if path.is_file())
        object_path.write_bytes(b"corrupt")
        self.assert_code(
            "EXTERNAL_MANIFEST_COVERAGE_MISMATCH",
            revalidate_sealed_attempt,
            self.root,
            self.policy,
            RUN_ID,
            ATTEMPT_ID,
            self.anchor(seal),
        )

    def test_offline_revalidation_is_deterministic(self):
        seal = self.seal("FINALIZED", with_payload=True)
        anchor = self.anchor(seal)
        first = revalidate_sealed_attempt(self.root, self.policy, RUN_ID, ATTEMPT_ID, anchor)
        second = revalidate_sealed_attempt(self.root, self.policy, RUN_ID, ATTEMPT_ID, anchor)
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()

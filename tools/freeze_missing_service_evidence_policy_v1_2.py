#!/usr/bin/env python3
"""Freeze evidence-capture policy v1.2 for missing_service_social_network.

v1.2 is a bounded, versioned correction of v1.1.  It is derived from the
hash-authenticated v1.1 bundle at run time; it does not copy or fork the v1 or
v1.1 generators.  Both are imported, re-executed, and their reconstructed
artifacts are required to equal the frozen bytes before any correction is
applied.

WHY v1.2 EXISTS
---------------
v1.1 declares three adjudication predicates that occur inside ONE MS-M01
attempt:

    INITIAL_INVARIANT_EVALUATION        (state ORIGINAL_ORACLE_EVALUATED | CONTRACT_EVALUATED)
    REPLACEMENT_PERSISTENCE_EVALUATION  (state CONTRACT_EVALUATED)
    RESTORATION_POSITIVE_CONTROL        (state RESTORE_STARTED | RESTORE_VERIFIED)

Each authorization state is individually reachable, so the three markers can all
be appended legally in one attempt.  But the v1.1 resolved context retains only
the LAST EVALUATION_AUTHORIZED marker as a single authoritative value, so a
sealed terminal attempt exposes exactly one evaluation context.  Every cited
adjudication is then compared against that one context, and the two earlier
predicates are rejected with ADJUDICATION_EVALUATION_PHASE_MISMATCH.  A terminal
attempt therefore cannot revalidate all three of its own adjudications.

v1.2 replaces the single retained marker with one bounded, authoritative
per-predicate authorization mapping reconstructed from exact journal bytes, and
makes adjudication and workload-window validation select the context belonging
to the candidate's own predicate.

SCOPE
-----
v1.2 supersedes v1.1 for FUTURE PROSPECTIVE ATTEMPTS ONLY.  It does not
reinterpret historical evidence and does not claim to have existed before any
historical run.  The prospective mutant matrix is 0/9 at freeze time, so no
attempt has been executed under v1.1 whose meaning could change.

WHAT v1.2 DOES NOT TOUCH
------------------------
predicate identities, phases, deadlines and allowed states; workload window
identities and ordinals; state names and legal transitions; invariants; evidence
roles; mutation operations; restoration rules; workload cardinality; stream
identity; Kubernetes capture rules; verdict vocabularies.

Usage:
    python3 tools/freeze_missing_service_evidence_policy_v1_2.py
    python3 tools/freeze_missing_service_evidence_policy_v1_2.py --check
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import stat
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

import yaml
from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
GENERATOR = Path(__file__).resolve()
POLICY = ROOT / "policies/missing_service_social_network/evidence-capture-v1.2.yaml"
SCHEMA = ROOT / "schemas/evidence-capture-policy-v1.2.schema.json"
MANIFEST = ROOT / "EVIDENCE_CAPTURE_POLICY_V1_2_SHA256SUMS"

BASE_GENERATOR = ROOT / "tools/freeze_missing_service_evidence_policy_v1_1.py"
BASE_POLICY = ROOT / "policies/missing_service_social_network/evidence-capture-v1.1.yaml"
BASE_SCHEMA = ROOT / "schemas/evidence-capture-policy-v1.1.schema.json"
BASE_MANIFEST = ROOT / "EVIDENCE_CAPTURE_POLICY_V1_1_SHA256SUMS"

GENERATOR_REL = GENERATOR.relative_to(ROOT).as_posix()
POLICY_REL = POLICY.relative_to(ROOT).as_posix()
SCHEMA_REL = SCHEMA.relative_to(ROOT).as_posix()
MANIFEST_REL = MANIFEST.relative_to(ROOT).as_posix()
OWN_PATHS = frozenset({GENERATOR_REL, POLICY_REL, SCHEMA_REL, MANIFEST_REL})

BASE_TAG = "sremut-missing-service-evidence-policy-v1.1"
BASE_TAG_OBJECT = "8e44e66c4424f0c5d06fb7484b960e1041466d9f"
BASE_COMMIT = "560e8e81aed626f06e5b08786be51138daaf99dd"
BASE_TREE = "7b6d7d75ef9417e570a7495d407daf954a6e8481"
BASE_MANIFEST_SHA256 = "9ef4415ce50193eb615b0cf0313ad05f1ea3806bae43fffae83bd85616454cb7"
BASE_GENERATOR_SHA256 = "b48ae7e6ec3775b6920d4148326cd1063255eaa7c3ed3925bb7067f5ae4bc04a"
BASE_POLICY_SHA256 = "f2dfe841b7def08302c56ff51e6bfc166eaa46038429d8d2134ead00fff9c5db"
BASE_SCHEMA_SHA256 = "0283d9fffcda72d7450c5237b72e7ea59ceb8bc0ecb464a1f9bf4640724c2689"
BASE_POLICY_ID = "sremut/missing-service-social-network/evidence-capture-v1.1"

POLICY_ID = "sremut/missing-service-social-network/evidence-capture-v1.2"
SEMANTIC_VERSION = "1.2"
FROZEN_AT = "2026-08-28T17:44:05.118293+00:00"
TAG_NAME = "sremut-missing-service-evidence-policy-v1.2"

SHA256_PATTERN = r"^[0-9a-f]{64}$"

BASE_ARTIFACTS = {
    "tools/freeze_missing_service_evidence_policy_v1_1.py": BASE_GENERATOR_SHA256,
    "policies/missing_service_social_network/evidence-capture-v1.1.yaml": BASE_POLICY_SHA256,
    "schemas/evidence-capture-policy-v1.1.schema.json": BASE_SCHEMA_SHA256,
}

#: Every policy path v1.2 is permitted to change relative to v1.1.
POLICY_ALLOWED_PREFIXES = (
    "document_type",
    "semantic_version",
    "policy_id",
    "status",
    "frozen_at",
    "future_annotated_tag",
    "base_policy_provenance",
    "supersession",
    "correction_reason",
    "semantic_diff_report",
    "runner_release_binding.evidence_policy_annotated_tag",
    "authenticated_policy_input.expected_entry_paths",
    "authenticated_policy_input.required_exact_bytes",
    "authoritative_journal_derivation.algorithm",
    "authoritative_journal_derivation.evaluation_authorization_derivation",
    "authoritative_journal_derivation.caller_verified_evaluation_context_authoritative",
    "full_admissibility_validation.resolved_context_document",
    "full_admissibility_validation.evaluation_authorization_contexts",
    "full_admissibility_validation.adjudication_context_selection",
    "full_admissibility_validation.workload_window_context_selection",
    "full_admissibility_validation.attempt_envelope_adjudication_closure",
    "full_admissibility_validation.failure_codes",
    "workload_stream_identity_protocol.evidence_policy_id",
)

#: Every schema path v1.2 is permitted to change relative to v1.1.
SCHEMA_ALLOWED_PREFIXES = (
    "$id",
    "title",
    "$defs.evidence_policy_document",
    "$defs.resolved_evidence_context",
    "$defs.authenticated_policy_input",
    "$defs.offline_seal_input",
    "x-evidence-policy-semantic-version",
)

#: The three frozen predicates, carried through unchanged from v1.1.
PREDICATES = (
    "INITIAL_INVARIANT_EVALUATION",
    "REPLACEMENT_PERSISTENCE_EVALUATION",
    "RESTORATION_POSITIVE_CONTROL",
)

MARKER_PREFIX = "EVALUATION_AUTHORIZED:"
OPERATION_PREFIX = "OPERATION_AUTHORIZED:"

NEW_FAILURE_CODES = (
    "EVALUATION_AUTHORIZATION_DUPLICATE",
    "EVALUATION_AUTHORIZATION_UNKNOWN_PREDICATE",
    "EVALUATION_AUTHORIZATION_STATE_INVALID",
    "EVALUATION_AUTHORIZATION_CONTEXT_MISSING",
    "ADJUDICATION_CLOSURE_INCOMPLETE",
)


class FreezeError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# primitives
# ---------------------------------------------------------------------------


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_path(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def reject_floats(value: Any) -> None:
    if isinstance(value, float):
        raise FreezeError("FLOAT_FORBIDDEN")
    if isinstance(value, dict):
        if any(type(key) is not str for key in value):
            raise FreezeError("NONSTRING_KEY")
        for child in value.values():
            reject_floats(child)
    elif isinstance(value, list):
        for child in value:
            reject_floats(child)


def canonical_json_bytes(value: Any) -> bytes:
    reject_floats(value)
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def run_git(*arguments: str) -> bytes:
    completed = subprocess.run(
        ["/usr/bin/git", "-C", str(ROOT), *arguments],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        check=False,
        timeout=20,
        env={"LC_ALL": "C.UTF-8", "PATH": "/usr/bin:/bin"},
    )
    if completed.returncode != 0:
        raise FreezeError("GIT_PROVENANCE_FAILURE")
    return completed.stdout


def parse_manifest(data: bytes) -> dict[str, str]:
    if not data or not data.endswith(b"\n"):
        raise FreezeError("MANIFEST_INVALID")
    rows: dict[str, str] = {}
    for line in data.decode("ascii").splitlines():
        parts = line.split("  ")
        if len(parts) != 2 or not re.fullmatch(SHA256_PATTERN, parts[0]):
            raise FreezeError("MANIFEST_INVALID")
        path = parts[1]
        if not path or path.startswith("/") or any(p in ("", ".", "..") for p in path.split("/")):
            raise FreezeError("MANIFEST_INVALID")
        if path in rows:
            raise FreezeError("MANIFEST_INVALID")
        rows[path] = parts[0]
    return rows


def verify_repository_state() -> None:
    if run_git("rev-parse", f"refs/tags/{BASE_TAG}").strip().decode() != BASE_TAG_OBJECT:
        raise FreezeError("BASE_TAG_OBJECT_MISMATCH")
    if run_git("rev-parse", f"refs/tags/{BASE_TAG}^{{}}").strip().decode() != BASE_COMMIT:
        raise FreezeError("BASE_TAG_TARGET_MISMATCH")
    if run_git("rev-parse", f"refs/tags/{BASE_TAG}^{{}}^{{tree}}").strip().decode() != BASE_TREE:
        raise FreezeError("BASE_TAG_TREE_MISMATCH")
    if subprocess.run(
        ["/usr/bin/git", "-C", str(ROOT), "merge-base", "--is-ancestor", BASE_COMMIT, "HEAD"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        shell=False,
        timeout=20,
    ).returncode != 0:
        raise FreezeError("CURRENT_HEAD_NOT_BASE_DESCENDANT")
    for line in run_git("status", "--porcelain=v1", "--untracked-files=all").decode().splitlines():
        if len(line) < 4 or line[3:] not in OWN_PATHS:
            raise FreezeError("UNAUTHORIZED_DIRTY_PATH")


def verify_base_artifacts() -> None:
    for relative, expected in BASE_ARTIFACTS.items():
        path = ROOT / relative
        if path.is_symlink() or not path.is_file() or sha256_path(path) != expected:
            raise FreezeError("BASE_ARTIFACT_MISMATCH")
        tagged = run_git("show", f"{BASE_COMMIT}:{relative}")
        if tagged != path.read_bytes() or sha256_bytes(tagged) != expected:
            raise FreezeError("BASE_TAGGED_BLOB_MISMATCH")
    if sha256_path(BASE_MANIFEST) != BASE_MANIFEST_SHA256:
        raise FreezeError("BASE_MANIFEST_MISMATCH")
    if parse_manifest(BASE_MANIFEST.read_bytes()) != {
        "tools/freeze_missing_service_evidence_policy_v1_1.py": BASE_GENERATOR_SHA256,
        "policies/missing_service_social_network/evidence-capture-v1.1.yaml": BASE_POLICY_SHA256,
        "schemas/evidence-capture-policy-v1.1.schema.json": BASE_SCHEMA_SHA256,
    }:
        raise FreezeError("BASE_MANIFEST_MISMATCH")


def load_base_module() -> Any:
    spec = importlib.util.spec_from_file_location(
        "sremut_frozen_evidence_policy_v1_1", BASE_GENERATOR
    )
    if spec is None or spec.loader is None:
        raise FreezeError("BASE_GENERATOR_IMPORT_FAILED")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def reconstruct_v1_1() -> tuple[Any, Any, dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Re-execute the authenticated v1 and v1.1 generators and require exact bytes."""
    v11 = load_base_module()
    v1, v1_policy, v1_schema = v11.reconstruct_v1()
    policy = v11.build_policy(v1_policy)
    schema = v11.build_schema(v1, policy, v1_schema)
    if v11.render_policy(policy) != BASE_POLICY.read_bytes():
        raise FreezeError("BASE_POLICY_RECONSTRUCTION_MISMATCH")
    if v11.render_schema(schema) != BASE_SCHEMA.read_bytes():
        raise FreezeError("BASE_SCHEMA_RECONSTRUCTION_MISMATCH")
    if policy.get("policy_id") != BASE_POLICY_ID:
        raise FreezeError("BASE_POLICY_IDENTITY_MISMATCH")
    return v11, v1, v1_policy, policy, schema


def object_diff(left: Any, right: Any, path: str = "") -> list[str]:
    if type(left) is not type(right):
        return [path or "$"]
    if isinstance(left, dict):
        changed: list[str] = []
        for key in sorted(set(left) | set(right)):
            child = f"{path}.{key}" if path else key
            if key not in left or key not in right:
                changed.append(child)
            else:
                changed.extend(object_diff(left[key], right[key], child))
        return changed
    if isinstance(left, list):
        changed = []
        for index in range(max(len(left), len(right))):
            child = f"{path}[{index}]"
            if index >= len(left) or index >= len(right):
                changed.append(child)
            else:
                changed.extend(object_diff(left[index], right[index], child))
        return changed
    return [] if left == right else [path or "$"]


def path_allowed(path: str, prefixes: tuple[str, ...]) -> bool:
    return any(
        path == prefix or path.startswith(prefix + ".") or path.startswith(prefix + "[")
        for prefix in prefixes
    )


def assert_allowed_diff(left: Any, right: Any, prefixes: tuple[str, ...], label: str) -> list[str]:
    changed = object_diff(left, right)
    forbidden = [path for path in changed if not path_allowed(path, prefixes)]
    if forbidden:
        raise FreezeError(f"UNAUTHORIZED_{label}_SEMANTIC_DRIFT:{forbidden[0]}")
    return changed


# ---------------------------------------------------------------------------
# v1.2 dispatch failure vocabulary
#
# Two distinct error classes.  `FreezeError` is a generator build-time fault.
# `DispatchError` is a validation outcome, and every code it can carry must
# already exist in the authenticated policy's failure vocabulary -- emitting a
# code the policy does not define is itself a generator fault.
# ---------------------------------------------------------------------------


class DispatchError(RuntimeError):
    """One closed v1.2 full-admissibility validation failure."""


def dispatch_reject(policy: dict[str, Any], code: str) -> Any:
    if code not in set(policy["full_admissibility_validation"]["failure_codes"]):
        raise FreezeError(f"FAILURE_CODE_NOT_IN_POLICY:{code}")
    raise DispatchError(code)


# ---------------------------------------------------------------------------
# v1.2 reference implementation
#
# This is the corrected semantics the policy describes.  `v1_2_full_admissibility`
# is the single dispatch entry point; the reference tests drive it, never the
# helpers below in isolation.
# ---------------------------------------------------------------------------


def predicate_matrix(policy: dict[str, Any]) -> dict[str, Any]:
    return policy["full_admissibility_validation"][
        "adjudication_predicate_raw_role_context_deadline_matrix"
    ]


def journal_record_sha256(record: dict[str, Any]) -> str:
    material = {k: v for k, v in record.items() if k != "canonical_current_entry_sha256"}
    return sha256_bytes(canonical_json_bytes(material))


def reconstruct_journal(policy: dict[str, Any], records: list[dict[str, Any]]) -> dict[str, Any]:
    """Canonical journal + hash chain + per-predicate authorization contexts.

    The authorization state is the state AT each marker's own sequence, replayed
    forward from CREATED.  It is never the terminal state.
    """
    machine = policy["verified_attempt_state_machine"]
    states = set(machine["states"])
    legal = set(machine["legal_transitions"])
    terminal_states = set(machine["terminal_states"])
    matrix = predicate_matrix(policy)

    contexts: dict[str, dict[str, Any]] = {}
    publications: dict[str, dict[str, Any]] = {}
    state = "CREATED"
    previous = "0" * 64
    terminal_seen = False
    for index, record in enumerate(records):
        if terminal_seen:
            dispatch_reject(policy, "POST_TERMINAL_OPERATION")
        if not isinstance(record, dict) or record.get("document_type") != "JOURNAL_RECORD_V1":
            dispatch_reject(policy, "JOURNAL_CANONICALIZATION_INVALID")
        if record.get("sequence_number") != index or record.get("previous_entry_sha256") != previous:
            dispatch_reject(policy, "JOURNAL_CHAIN_INVALID")
        current = journal_record_sha256(record)
        if record.get("canonical_current_entry_sha256") != current:
            dispatch_reject(policy, "JOURNAL_CHAIN_INVALID")
        transition = record.get("transition")
        if not isinstance(transition, str) or not transition:
            dispatch_reject(policy, "JOURNAL_STATE_DERIVATION_FAILED")
        if transition.startswith("STATE_VERIFIED:"):
            proposed = transition.split(":", 1)[1]
            if proposed not in states or proposed != state:
                dispatch_reject(policy, "JOURNAL_STATE_DERIVATION_FAILED")
        elif transition in legal:
            source, target = transition.split("->", 1)
            if source != state:
                dispatch_reject(policy, "JOURNAL_STATE_DERIVATION_FAILED")
            state = target
        elif transition.startswith(MARKER_PREFIX):
            predicate = transition.split(":", 1)[1]
            row = matrix.get(predicate)
            if not isinstance(row, dict):
                dispatch_reject(policy, "EVALUATION_AUTHORIZATION_UNKNOWN_PREDICATE")
            if predicate in contexts:
                dispatch_reject(policy, "EVALUATION_AUTHORIZATION_DUPLICATE")
            if state not in row["allowed_states"]:
                dispatch_reject(policy, "EVALUATION_AUTHORIZATION_STATE_INVALID")
            contexts[predicate] = {
                "predicate_id": predicate,
                "phase": row["phase"],
                "deadline_identity": row["deadline_identity"],
                "authorization_state": state,
                "marker_sequence_number": index,
            }
        elif transition.startswith(OPERATION_PREFIX):
            pass
        else:
            dispatch_reject(policy, "JOURNAL_STATE_DERIVATION_FAILED")
        for digest in record.get("referenced_descriptor_sha256", ()):
            publications.setdefault(digest, {"sequence_number": index,
                                             "journal_record_sha256": current})
        previous = current
        terminal_seen = state in terminal_states
    return {
        "evaluation_authorization_contexts": contexts,
        "publication_by_descriptor_sha256": publications,
        "terminal_state": state,
        "terminal": terminal_seen,
        "record_count": len(records),
    }


def select_evaluation_context(
    policy: dict[str, Any], contexts: dict[str, dict[str, Any]], predicate: Any
) -> dict[str, Any]:
    """Select the context belonging to the CANDIDATE's own predicate."""
    if not isinstance(predicate, str):
        dispatch_reject(policy, "EVALUATION_AUTHORIZATION_CONTEXT_MISSING")
    if predicate not in predicate_matrix(policy):
        dispatch_reject(policy, "EVALUATION_AUTHORIZATION_UNKNOWN_PREDICATE")
    if predicate not in contexts:
        dispatch_reject(policy, "EVALUATION_AUTHORIZATION_CONTEXT_MISSING")
    return contexts[predicate]


def publication_of(policy: dict[str, Any], derived: dict[str, Any], reference: dict[str, Any]) -> int:
    digest = reference.get("descriptor_sha256")
    row = derived["publication_by_descriptor_sha256"].get(digest)
    if row is None:
        dispatch_reject(policy, "PUBLICATION_RECORD_MISSING")
    return row["sequence_number"]


def validate_workload_window(
    policy: dict[str, Any], derived: dict[str, Any], predicate: str, window: Any
) -> None:
    """Validate one workload window against ITS predicate's reconstructed context."""
    context = select_evaluation_context(
        policy, derived["evaluation_authorization_contexts"], predicate
    )
    if not isinstance(window, dict):
        dispatch_reject(policy, "WORKLOAD_WINDOW_MISMATCH")
    ordinals = policy["workload_evidence_protocol"]["window_identity"]["phase_ordinal_enum"]
    if window.get("phase") != context["phase"]:
        dispatch_reject(policy, "WORKLOAD_EVALUATION_CONTEXT_MISMATCH")
    if window.get("ordinal") != ordinals.get(window.get("phase")):
        dispatch_reject(policy, "WORKLOAD_WINDOW_MISMATCH")
    if window.get("stream_identity") != stream_identity(window.get("run_id"), window.get("attempt_id")):
        dispatch_reject(policy, "WORKLOAD_STREAM_IDENTITY_MISMATCH")


def validate_adjudication(
    policy: dict[str, Any], derived: dict[str, Any], resolved: dict[str, Any], candidate: dict[str, Any]
) -> str:
    """Validate one adjudication descriptor; returns its predicate id."""
    predicate = candidate.get("predicate_oracle_or_classification_id")
    context = select_evaluation_context(
        policy, derived["evaluation_authorization_contexts"], predicate
    )
    row = predicate_matrix(policy)[predicate]
    window = candidate.get("workload_window_adjudication_identity")
    # v1.2 takes the phase from the window identity, never from a top-level field.
    validate_workload_window(policy, derived, predicate, window)
    if window.get("phase") != row["phase"]:
        dispatch_reject(policy, "ADJUDICATION_EVALUATION_PHASE_MISMATCH")
    if (
        candidate.get("applicable_deadline") != row["deadline_identity"]
        or row["deadline_identity"] != context["deadline_identity"]
    ):
        dispatch_reject(policy, "ADJUDICATION_DEADLINE_MISMATCH")
    if candidate.get("result_type") != "BOOLEAN":
        dispatch_reject(policy, "ADJUDICATION_RAW_ROLE_INVALID")

    references = candidate.get("raw_evidence_references")
    hashes = candidate.get("raw_evidence_sha256_per_reference")
    if (
        not isinstance(references, list)
        or not references
        or not isinstance(hashes, list)
        or len(references) != len(hashes)
    ):
        dispatch_reject(policy, "ADJUDICATION_RAW_REFERENCE_REQUIRED")
    marker = context["marker_sequence_number"]
    own = publication_of(policy, derived, _self_reference(policy, resolved, candidate))
    allowed = set(row["allowed_raw_roles"])
    seen: set[str] = set()
    for index, reference in enumerate(references):
        if not isinstance(reference, dict):
            dispatch_reject(policy, "ADJUDICATION_RAW_REFERENCE_REQUIRED")
        evidence_id = reference.get("evidence_id")
        if evidence_id not in resolved["evidence_refs"]:
            dispatch_reject(policy, "EVIDENCE_REFERENCE_UNRESOLVED")
        if evidence_id in seen or reference.get("role") not in allowed:
            dispatch_reject(policy, "ADJUDICATION_RAW_ROLE_INVALID")
        seen.add(evidence_id)
        if hashes[index] != reference.get("payload_sha256"):
            dispatch_reject(policy, "PAYLOAD_HASH_MISMATCH")
        published = publication_of(policy, derived, reference)
        if not marker < published < own:
            dispatch_reject(policy, "PUBLICATION_ORDER_INVALID")
    return predicate


def _self_reference(
    policy: dict[str, Any], resolved: dict[str, Any], candidate: dict[str, Any]
) -> dict[str, Any]:
    digest = sha256_bytes(canonical_json_bytes(candidate))
    for reference in resolved["evidence_refs"].values():
        if reference.get("descriptor_sha256") == digest:
            return reference
    dispatch_reject(policy, "EVIDENCE_REFERENCE_UNRESOLVED")


def adjudication_descriptors(resolved: dict[str, Any]) -> dict[str, dict[str, Any]]:
    by_digest = {
        reference["descriptor_sha256"]: evidence_id
        for evidence_id, reference in resolved["evidence_refs"].items()
    }
    sealed: dict[str, dict[str, Any]] = {}
    for descriptor in resolved["parsed_canonical_descriptors"].values():
        if descriptor.get("role") != "adjudication":
            continue
        digest = sha256_bytes(canonical_json_bytes(descriptor))
        if digest in by_digest:
            sealed[by_digest[digest]] = descriptor
    return sealed


def required_predicates(policy: dict[str, Any], derived: dict[str, Any]) -> set[str]:
    """Predicates whose phase was legally reached for this terminal outcome.

    FINALIZED requires all three.  ABORTED_SAFE and RESTORATION_BLOCKED require
    only the predicates whose authorization state the attempt actually reached,
    so partial evidence stays admissible for outcomes that never reached a phase.
    """
    if derived["terminal_state"] == "FINALIZED":
        return set(PREDICATES)
    return set(derived["evaluation_authorization_contexts"])


def validate_envelope(
    policy: dict[str, Any], derived: dict[str, Any], resolved: dict[str, Any], candidate: dict[str, Any]
) -> None:
    """Revalidate every cited adjudication and require exact closure."""
    cited = candidate.get("adjudication_references")
    raw_references = candidate.get("raw_evidence_references")
    if not isinstance(cited, list) or not isinstance(raw_references, list):
        dispatch_reject(policy, "ADJUDICATION_RAW_REFERENCE_REQUIRED")
    if candidate.get("terminal_outcome") != derived["terminal_state"]:
        dispatch_reject(policy, "JOURNAL_CONTEXT_MISMATCH")
    cited_ids = [reference.get("evidence_id") for reference in cited]
    if len(set(cited_ids)) != len(cited_ids):
        dispatch_reject(policy, "ADJUDICATION_CLOSURE_INCOMPLETE")
    sealed = adjudication_descriptors(resolved)
    if set(cited_ids) != set(sealed):
        dispatch_reject(policy, "ADJUDICATION_CLOSURE_INCOMPLETE")
    closure: set[str] = set()
    predicates: list[str] = []
    for evidence_id in cited_ids:
        descriptor = sealed.get(evidence_id)
        if descriptor is None:
            dispatch_reject(policy, "EVIDENCE_REFERENCE_UNRESOLVED")
        predicates.append(validate_adjudication(policy, derived, resolved, descriptor))
        closure.update(
            reference["evidence_id"] for reference in descriptor["raw_evidence_references"]
        )
    if len(set(predicates)) != len(predicates):
        dispatch_reject(policy, "ADJUDICATION_CLOSURE_INCOMPLETE")
    if set(predicates) != required_predicates(policy, derived):
        dispatch_reject(policy, "ADJUDICATION_CLOSURE_INCOMPLETE")
    raw_ids = [reference.get("evidence_id") for reference in raw_references]
    if len(set(raw_ids)) != len(raw_ids) or set(raw_ids) != closure:
        dispatch_reject(policy, "ADJUDICATION_CLOSURE_INCOMPLETE")


def v1_2_full_admissibility(
    policy: dict[str, Any],
    schema: dict[str, Any],
    resolved: dict[str, Any],
    candidate: dict[str, Any],
) -> dict[str, Any]:
    """The single v1.2 dispatch path.

    1. schema-validate candidate and resolved context;
    2. reconstruct the canonical journal and hash chain;
    3. derive every evaluation context from exact journal records;
    4. select hooks from the authenticated v1.2 policy;
    5. run candidate-specific workload and adjudication validation;
    6. run complete envelope closure validation.
    """
    validator = Draft202012Validator(schema)
    if not validator.is_valid(resolved):
        dispatch_reject(policy, "RESOLVED_CONTEXT_INVALID")
    if not validator.is_valid(candidate):
        dispatch_reject(policy, "STRUCTURAL_SCHEMA_INVALID")
    if (
        resolved.get("run_id") != candidate.get("run_id")
        or resolved.get("attempt_id") != candidate.get("attempt_id")
    ):
        dispatch_reject(policy, "JOURNAL_CONTEXT_MISMATCH")
    if "expected_evaluation_context" in resolved:
        # No scalar last-marker representation may remain.
        dispatch_reject(policy, "RESOLVED_CONTEXT_INVALID")
    derived = reconstruct_journal(policy, list(resolved["attempt_journal_records"]))
    supplied = resolved.get("evaluation_authorization_contexts")
    if supplied != derived["evaluation_authorization_contexts"]:
        # A caller-supplied context never overrides journal reconstruction.
        dispatch_reject(policy, "JOURNAL_CONTEXT_MISMATCH")
    plan = [
        hook["hook_id"]
        for hook in policy["full_admissibility_validation"]["hook_contracts"]
        if candidate.get("document_type") in hook["applicable_document_kinds"]
        and (candidate.get("role") or "NONE") in hook["applicable_roles"]
    ]
    if "VALIDATE_ADJUDICATION_RAW_BACKING_V1" not in plan:
        dispatch_reject(policy, "HOOK_CONTEXT_MISSING")
    if candidate.get("role") == "adjudication":
        validate_adjudication(policy, derived, resolved, candidate)
    elif candidate.get("document_type") == "ATTEMPT_VALIDATION_ENVELOPE_V1":
        validate_envelope(policy, derived, resolved, candidate)
    else:
        dispatch_reject(policy, "HOOK_CONTEXT_MISSING")
    return {"valid": True, "hooks": tuple(plan), "derived": derived}


# ---------------------------------------------------------------------------
# schema-valid sealed-attempt fixture
# ---------------------------------------------------------------------------

FIXTURE_RUN_ID = "sremut-ms-m01-r01-a01-0123456789ab"
FIXTURE_ATTEMPT_ID = "a01"
FIXTURE_BOOT = "123e4567-e89b-12d3-a456-426614174000"
FIXTURE_UTC = "2026-08-20T10:00:00.123456789Z"
EXECUTION_PROFILE_TAG_OBJECT = "7c6493eb7dce68370fd0d5be572edd968654a1d6"
FROZEN_CONTRACT_PROFILE_IDENTITIES = {
    "contract_sha256": "bda78e1b07b5eb0628954bcafd8fae3fc1bb2f3b22770046584bd873b72a2488",
    "execution_profile_sha256": "79cb2d45298e6221d78fcc3aea82df52c71f60f0ab4ba872e1f0579a908703c7",
    "contract_tag_object": "378e9e9180438910611e7642402220e76bb302ca",
    "execution_profile_tag_object": EXECUTION_PROFILE_TAG_OBJECT,
}
STREAM_SOURCE = "StreamWorkloadManager.log_history"


def stream_identity(run_id: Any, attempt_id: Any) -> str:
    """v1.2 stream identity: v1.1 material and algorithm, v1.2 policy binding."""
    material = {
        "schema_version": 1,
        "execution_profile_tag_object": EXECUTION_PROFILE_TAG_OBJECT,
        "evidence_policy_id": POLICY_ID,
        "run_id": run_id,
        "attempt_id": attempt_id,
        "source": STREAM_SOURCE,
        "manager_instance_ordinal": 1,
    }
    return sha256_bytes(canonical_json_bytes(material))


def v1_1_stream_identity(run_id: Any, attempt_id: Any) -> str:
    """The superseded v1.1 binding, retained only to prove v1.2 rejects it."""
    material = {
        "schema_version": 1,
        "execution_profile_tag_object": EXECUTION_PROFILE_TAG_OBJECT,
        "evidence_policy_id": BASE_POLICY_ID,
        "run_id": run_id,
        "attempt_id": attempt_id,
        "source": STREAM_SOURCE,
        "manager_instance_ordinal": 1,
    }
    return sha256_bytes(canonical_json_bytes(material))


def _finalize_descriptor(
    descriptor: dict[str, Any], *, carries_evidence_id: bool = True
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Content-address one descriptor and derive its matching EvidenceRef.

    The frozen adjudication descriptor shape carries no `evidence_id`, so its
    identity is derived from its own canonical bytes and lives only in the ref.
    """
    if carries_evidence_id:
        working = deepcopy(descriptor)
        working.pop("evidence_id", None)
        digest = sha256_bytes(canonical_json_bytes(working))
        descriptor["evidence_id"] = "ev-" + digest[:32]
    descriptor_bytes = canonical_json_bytes(descriptor)
    descriptor_sha256 = sha256_bytes(descriptor_bytes)
    evidence_id = descriptor.get("evidence_id", "ev-" + descriptor_sha256[:32])
    relative = f"descriptors/sha256/{descriptor_sha256[:2]}/{descriptor_sha256}.json"
    reference = {
        "document_type": (
            "PAYLOAD_EVIDENCE_REF_V1"
            if descriptor["storage_class"] == "PAYLOAD_WITH_DESCRIPTOR"
            else "DESCRIPTOR_EVIDENCE_REF_V1"
        ),
        "schema_version": 1,
        "evidence_id": evidence_id,
        "role": descriptor["role"],
        "producer": descriptor["producer"],
        "source_kind": descriptor["source_kind"],
        "media_type": descriptor["media_type"],
        "storage_class": descriptor["storage_class"],
        "redaction_status": "NOT_REDACTED",
        "descriptor_sha256": descriptor_sha256,
        "descriptor_size_bytes": len(descriptor_bytes),
        "descriptor_relative_path": relative,
    }
    if descriptor["storage_class"] == "PAYLOAD_WITH_DESCRIPTOR":
        reference["payload_sha256"] = descriptor["payload_sha256"]
        reference["payload_size_bytes"] = descriptor["payload_size_bytes"]
        reference["payload_relative_path"] = descriptor["payload_relative_path"]
    return descriptor, reference


def _common_metadata() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "run_id": FIXTURE_RUN_ID,
        "attempt_id": FIXTURE_ATTEMPT_ID,
        "created_utc": FIXTURE_UTC,
        "boot_identity": FIXTURE_BOOT,
        "redaction_status": "NOT_REDACTED",
    }


def _payload_paths(payload: bytes) -> dict[str, Any]:
    digest = sha256_bytes(payload)
    return {
        "payload_sha256": digest,
        "payload_size_bytes": len(payload),
        "payload_relative_path": f"objects/sha256/{digest[:2]}/{digest}",
    }


def build_sealed_attempt(policy: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    """One complete, schema-valid, FINALIZED sealed attempt.

    Raw adjudication backing is the two payload-backed workload roles.  A
    Kubernetes projection chain would add fixture surface without exercising any
    semantics this correction changes, so `kubernetes_uid_and_resource_version_
    references_when_applicable` is the empty array the schema permits.
    """
    matrix = predicate_matrix(policy)
    ordinals = policy["workload_evidence_protocol"]["window_identity"]["phase_ordinal_enum"]
    identity = stream_identity(FIXTURE_RUN_ID, FIXTURE_ATTEMPT_ID)

    descriptors: dict[str, dict[str, Any]] = {}
    references: dict[str, dict[str, Any]] = {}
    payloads: dict[str, bytes] = {}
    per_predicate: dict[str, dict[str, Any]] = {}
    monotonic = 1000

    for predicate in PREDICATES:
        phase = matrix[predicate]["phase"]
        ordinal = ordinals[phase]
        window_core = {
            "phase": phase,
            "ordinal": ordinal,
            "run_id": FIXTURE_RUN_ID,
            "attempt_id": FIXTURE_ATTEMPT_ID,
            "mutant_id": "MS-M01",
            "repetition": 1,
            "stream_identity": identity,
        }
        log_payload = canonical_json_bytes({"window": ordinal, "entries": 60}) + b"\n"
        monotonic += 1
        log_descriptor, log_reference = _finalize_descriptor({
            **_common_metadata(),
            "document_type": "PAYLOAD_EVIDENCE_DESCRIPTOR_V1",
            "role": "workload_log_bytes",
            "producer": "WORKLOAD_EVIDENCE_ADAPTER",
            "source_kind": "IN_PROCESS_WORKLOAD",
            "media_type": "application/octet-stream",
            "storage_class": "PAYLOAD_WITH_DESCRIPTOR",
            "monotonic_ns": monotonic,
            "complete_entry_count": 60,
            "entry_indexes": list(range(60)),
            "entry_time_ieee754_binary64_hex": ["3ff0000000000000"] * 60,
            "workload_window": dict(window_core),
            "stream_identity": identity,
            **_payload_paths(log_payload),
        })
        payloads[log_reference["evidence_id"]] = log_payload

        monotonic += 1
        boundary_descriptor, boundary_reference = _finalize_descriptor({
            **_common_metadata(),
            "document_type": "DESCRIPTOR_EVIDENCE_DESCRIPTOR_V1",
            "role": "workload_boundary",
            "producer": "WORKLOAD_EVIDENCE_ADAPTER",
            "source_kind": "IN_PROCESS_WORKLOAD",
            "media_type": "application/json",
            "storage_class": "DESCRIPTOR_ONLY",
            "monotonic_ns": monotonic,
            "workload_pod_projection_reference": deepcopy(log_reference),
            "pod_name": "wrk2-job-abcde",
            "pod_uid": "11111111-1111-4111-8111-111111111111",
            "container_restart_count": 0,
            "raw_log_reference": deepcopy(log_reference),
            "raw_log_byte_length": len(log_payload),
            "raw_log_sha256": sha256_bytes(log_payload),
            "complete_entry_count": 60,
            "request_count": 60,
            "entry_time_ieee754_binary64_hex": ["3ff0000000000000"] * 60,
            "workload_window": dict(window_core),
            "stream_identity": identity,
        })

        parse_payload = canonical_json_bytes({
            "document_type": "WORKLOAD_PARSE_RESULT_V1",
            "schema_version": 1,
            "fresh_request_count": 60,
            "failure_marker_count": 0,
        })
        monotonic += 1
        parse_descriptor, parse_reference = _finalize_descriptor({
            **_common_metadata(),
            "document_type": "PAYLOAD_EVIDENCE_DESCRIPTOR_V1",
            "role": "workload_parse_result",
            "producer": "WORKLOAD_EVIDENCE_ADAPTER",
            "source_kind": "IN_PROCESS_WORKLOAD",
            "media_type": "application/json",
            "storage_class": "PAYLOAD_WITH_DESCRIPTOR",
            "monotonic_ns": monotonic,
            "boundary_reference": deepcopy(boundary_reference),
            "raw_log_reference": deepcopy(log_reference),
            "fresh_request_count": 60,
            "failure_marker_count": 0,
            "workload_window": dict(window_core),
            "stream_identity": identity,
            **_payload_paths(parse_payload),
        })
        payloads[parse_reference["evidence_id"]] = parse_payload

        window_identity = {
            **window_core,
            "boundary_reference": deepcopy(boundary_reference),
            "raw_log_reference": deepcopy(log_reference),
            "parse_result_reference": deepcopy(parse_reference),
        }
        raw_refs = [deepcopy(log_reference), deepcopy(parse_reference)]
        monotonic += 1
        adjudication_descriptor, adjudication_reference = _finalize_descriptor({
            "document_type": "DESCRIPTOR_EVIDENCE_DESCRIPTOR_V1",
            "role": "adjudication",
            "producer": "ADJUDICATOR",
            "source_kind": "GENERATED_DESCRIPTOR",
            "media_type": "application/json",
            "storage_class": "DESCRIPTOR_ONLY",
            "run_id": FIXTURE_RUN_ID,
            "attempt_id": FIXTURE_ATTEMPT_ID,
            "adjudication_id": f"{predicate}:{FIXTURE_RUN_ID}",
            "predicate_oracle_or_classification_id": predicate,
            "result_type": "BOOLEAN",
            "boolean_or_categorical_value": True,
            "reason": "DERIVED_FROM_RAW_ATTEMPT_EVIDENCE",
            "first_observation_utc": FIXTURE_UTC,
            "last_observation_utc": FIXTURE_UTC,
            "monotonic_elapsed_time": 60,
            "observation_count": len(raw_refs),
            "applicable_deadline": matrix[predicate]["deadline_identity"],
            "evaluator_source_or_runner_bundle_sha256": sha256_bytes(b"evaluator"),
            "raw_evidence_references": raw_refs,
            "raw_evidence_sha256_per_reference": [
                reference["payload_sha256"] for reference in raw_refs
            ],
            "kubernetes_uid_and_resource_version_references_when_applicable": [],
            "dependency_and_toolchain_identity": {"evidence_policy_id": POLICY_ID},
            "workload_window_adjudication_identity": window_identity,
        }, carries_evidence_id=False)
        for descriptor, reference in (
            (log_descriptor, log_reference),
            (boundary_descriptor, boundary_reference),
            (parse_descriptor, parse_reference),
            (adjudication_descriptor, adjudication_reference),
        ):
            descriptors[reference["evidence_id"]] = descriptor
            references[reference["evidence_id"]] = reference
        per_predicate[predicate] = {
            "raw": raw_refs,
            "adjudication": adjudication_reference,
            "window": window_identity,
        }

    records = _build_journal(per_predicate)
    resolved = _build_resolved_context(policy, records, descriptors, references, payloads)
    envelope = _build_envelope(schema, records, per_predicate)
    return {
        "policy": policy,
        "records": records,
        "resolved": resolved,
        "envelope": envelope,
        "per_predicate": per_predicate,
        "references": references,
        "descriptors": descriptors,
    }


# ---------------------------------------------------------------------------
# bounded schema-driven synthesis
#
# The frozen run-identity and terminal-manifest records are almost entirely
# `const`-driven.  Deriving them FROM the authenticated schema keeps the fixture
# honest -- it cannot drift from the frozen definition it must satisfy -- and
# avoids transcribing dozens of pinned digests by hand.
# ---------------------------------------------------------------------------


_SAMPLE_BY_PATTERN = {
    r"^[0-9a-f]{64}$": "a" * 64,
    r"^[0-9a-f]{40}$": "c" * 40,
    r"^[0-9a-f]{12}$": "d" * 12,
    r"^ev-[0-9a-f]{32}$": "ev-" + "b" * 32,
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$": FIXTURE_BOOT,
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{9}Z$": FIXTURE_UTC,
    r"^sremut-ms-(m01|m02|m03)-r0[1-3]-a0[1-2]-[0-9a-f]{12}$": FIXTURE_RUN_ID,
    r"^a0[1-2]$": FIXTURE_ATTEMPT_ID,
    r"^descriptors/sha256/[0-9a-f]{2}/[0-9a-f]{64}\.json$":
        "descriptors/sha256/aa/" + "a" * 64 + ".json",
    r"^manifests/[A-Za-z0-9._/-]+$": "manifests/terminal.sha256",
}


def _resolve(schema: dict[str, Any], node: Any) -> Any:
    while isinstance(node, dict) and "$ref" in node:
        node = schema["$defs"][node["$ref"].split("/")[-1]]
    return node


def synthesize(schema: dict[str, Any], node: Any, overrides: dict[str, Any] | None = None) -> Any:
    """Build one minimal value satisfying a closed frozen definition."""
    node = _resolve(schema, node)
    if not isinstance(node, dict):
        raise FreezeError("SYNTHESIS_NODE_INVALID")
    if "const" in node:
        return deepcopy(node["const"])
    if "enum" in node:
        return deepcopy(node["enum"][0])
    if "oneOf" in node:
        return synthesize(schema, node["oneOf"][0], overrides)
    kind = node.get("type")
    if kind == "object" or "properties" in node:
        value: dict[str, Any] = {}
        for name in node.get("required", ()):
            if overrides and name in overrides:
                value[name] = deepcopy(overrides[name])
                continue
            child = node.get("properties", {}).get(name)
            if child is None:
                raise FreezeError(f"SYNTHESIS_PROPERTY_MISSING:{name}")
            value[name] = synthesize(schema, child, overrides)
        return value
    if kind == "array":
        if "prefixItems" in node:
            return [synthesize(schema, item, overrides) for item in node["prefixItems"]]
        count = max(int(node.get("minItems", 0)), 0)
        return [synthesize(schema, node["items"], overrides) for _ in range(count)]
    if kind == "integer":
        return int(node.get("minimum", 0))
    if kind == "boolean":
        return True
    if kind == "null":
        return None
    if kind == "string":
        pattern = node.get("pattern")
        if pattern is not None:
            for known, sample in _SAMPLE_BY_PATTERN.items():
                if known == pattern:
                    return sample
            raise FreezeError(f"SYNTHESIS_PATTERN_UNKNOWN:{pattern}")
        return "x" * max(int(node.get("minLength", 1)), 1)
    raise FreezeError("SYNTHESIS_NODE_INVALID")


def _record(sequence: int, transition: str, previous: str, **extra: Any) -> dict[str, Any]:
    record = {
        "document_type": "JOURNAL_RECORD_V1",
        "schema_version": 1,
        "journal_record_type": "STATE_TRANSITION",
        "sequence_number": sequence,
        "previous_entry_sha256": previous,
        "canonical_current_entry_sha256": "0" * 64,
        "run_id": FIXTURE_RUN_ID,
        "attempt_id": FIXTURE_ATTEMPT_ID,
        "transition": transition,
        "referenced_intent_receipt_and_adjudication_sha256": [],
        "utc_time": FIXTURE_UTC,
        "monotonic_ns": sequence + 1,
        "boot_identity": FIXTURE_BOOT,
    }
    record.update(extra)
    record["canonical_current_entry_sha256"] = journal_record_sha256(record)
    return record


def _build_journal(per_predicate: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Publications ride state-neutral STATE_VERIFIED records, before terminal."""
    plan: list[tuple[str, dict[str, Any]]] = [
        ("CREATED->PREFLIGHT_PASS", {}),
        ("PREFLIGHT_PASS->HEALTHY_STATE_CAPTURED", {}),
        ("HEALTHY_STATE_CAPTURED->MUTANT_INJECTED", {}),
        ("MUTANT_INJECTED->MUTANT_STATE_VERIFIED", {}),
        ("MUTANT_STATE_VERIFIED->ORIGINAL_ORACLE_STARTED", {}),
        ("ORIGINAL_ORACLE_STARTED->ORIGINAL_ORACLE_EVALUATED", {}),
    ]
    order = (
        ("INITIAL_INVARIANT_EVALUATION", "ORIGINAL_ORACLE_EVALUATED",
         "ORIGINAL_ORACLE_EVALUATED->CONTRACT_EVALUATED"),
        ("REPLACEMENT_PERSISTENCE_EVALUATION", "CONTRACT_EVALUATED",
         "CONTRACT_EVALUATED->RESTORE_STARTED"),
        ("RESTORATION_POSITIVE_CONTROL", "RESTORE_STARTED",
         "RESTORE_STARTED->RESTORE_VERIFIED"),
    )
    for predicate, state, advance in order:
        bundle = per_predicate[predicate]
        plan.append((f"{MARKER_PREFIX}{predicate}", {}))
        plan.append((
            f"STATE_VERIFIED:{state}",
            {
                "referenced_descriptor_sha256": [
                    reference["descriptor_sha256"] for reference in bundle["raw"]
                ],
                "referenced_payload_sha256": [
                    reference["payload_sha256"] for reference in bundle["raw"]
                ],
            },
        ))
        plan.append((
            f"STATE_VERIFIED:{state}",
            {"referenced_descriptor_sha256": [bundle["adjudication"]["descriptor_sha256"]]},
        ))
        plan.append((advance, {}))
    plan.append(("RESTORE_VERIFIED->FINALIZED", {}))

    records: list[dict[str, Any]] = []
    previous = "0" * 64
    for index, (transition, extra) in enumerate(plan):
        record = _record(index, transition, previous, **extra)
        previous = record["canonical_current_entry_sha256"]
        records.append(record)
    return records


def _build_resolved_context(
    policy: dict[str, Any],
    records: list[dict[str, Any]],
    descriptors: dict[str, dict[str, Any]],
    references: dict[str, dict[str, Any]],
    payloads: dict[str, bytes],
) -> dict[str, Any]:
    derived = reconstruct_journal(policy, records)
    publications = {}
    for evidence_id, reference in references.items():
        row = derived["publication_by_descriptor_sha256"].get(reference["descriptor_sha256"])
        if row is not None:
            publications[evidence_id] = dict(row)
    journal_bytes = b"".join(canonical_json_bytes(record) + b"\n" for record in records)
    return {
        "document_type": "RESOLVED_EVIDENCE_CONTEXT_V1_2",
        "schema_version": 1,
        "run_id": FIXTURE_RUN_ID,
        "attempt_id": FIXTURE_ATTEMPT_ID,
        "mutant_id": "MS-M01",
        "repetition": 1,
        "evidence_refs": deepcopy(references),
        "exact_descriptor_bytes_hex": {
            evidence_id: canonical_json_bytes(descriptor).hex()
            for evidence_id, descriptor in descriptors.items()
        },
        "parsed_canonical_descriptors": deepcopy(descriptors),
        "exact_payload_bytes_hex": {
            evidence_id: payload.hex() for evidence_id, payload in payloads.items()
        },
        "journal_publication_records": publications,
        "attempt_journal_records": deepcopy(records),
        "exact_authoritative_journal_bytes_hex": journal_bytes.hex(),
        "validation_mode": "OFFLINE_SEALED_REVALIDATION",
        "trusted_capture_source_bytes_hex": {},
        "offline_seal": None,
        "current_verified_attempt_state": {
            "state": derived["terminal_state"],
            "sequence_number": len(records) - 1,
            "terminal": derived["terminal"],
        },
        "frozen_contract_profile_identities": FROZEN_CONTRACT_PROFILE_IDENTITIES,
        "expected_operation_context": None,
        "evaluation_authorization_contexts": deepcopy(
            derived["evaluation_authorization_contexts"]
        ),
        "challenge_identity": None,
    }


def _build_envelope(
    schema: dict[str, Any],
    records: list[dict[str, Any]],
    per_predicate: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    raw: list[dict[str, Any]] = []
    for predicate in PREDICATES:
        raw.extend(deepcopy(reference) for reference in per_predicate[predicate]["raw"])
    return {
        "document_type": "ATTEMPT_VALIDATION_ENVELOPE_V1",
        "schema_version": 1,
        "run_id": FIXTURE_RUN_ID,
        "attempt_id": FIXTURE_ATTEMPT_ID,
        "terminal_outcome": "FINALIZED",
        "run_identities": synthesize(
            schema,
            {"$ref": "#/$defs/attempt_validation_envelope"}["$ref"]
            and schema["$defs"]["attempt_validation_envelope"]["properties"]["run_identities"],
            {"run_id": FIXTURE_RUN_ID, "attempt_id": FIXTURE_ATTEMPT_ID,
             "terminal_outcome": "FINALIZED"},
        ),
        "operations": [],
        "journal_records": deepcopy(records),
        "adjudication_references": [
            deepcopy(per_predicate[predicate]["adjudication"]) for predicate in PREDICATES
        ],
        "raw_evidence_references": raw,
        "terminal_manifest": synthesize(
            schema,
            schema["$defs"]["terminal_manifest_record"],
            {"run_id": FIXTURE_RUN_ID, "attempt_id": FIXTURE_ATTEMPT_ID,
             "terminal_outcome": "FINALIZED"},
        ),
    }


# ---------------------------------------------------------------------------
# policy construction
# ---------------------------------------------------------------------------


def build_policy(base_policy: dict[str, Any]) -> dict[str, Any]:
    policy = deepcopy(base_policy)
    policy["document_type"] = "EVIDENCE_POLICY_DOCUMENT_V1_2"
    policy["semantic_version"] = SEMANTIC_VERSION
    policy["policy_id"] = POLICY_ID
    policy["status"] = "FROZEN_BEFORE_PROSPECTIVE_MUTANT_EXECUTION"
    policy["frozen_at"] = FROZEN_AT
    policy["future_annotated_tag"] = TAG_NAME
    policy["base_policy_provenance"] = {
        "tag": BASE_TAG,
        "tag_object": BASE_TAG_OBJECT,
        "peeled_commit": BASE_COMMIT,
        "tree": BASE_TREE,
        "manifest_sha256": BASE_MANIFEST_SHA256,
        "generator_sha256": BASE_GENERATOR_SHA256,
        "policy_sha256": BASE_POLICY_SHA256,
        "schema_sha256": BASE_SCHEMA_SHA256,
    }
    policy["supersession"] = {
        "supersedes": BASE_POLICY_ID,
        "scope": "FUTURE_PROSPECTIVE_MUTANT_EXECUTION_ONLY",
        "base_v1_1_historical_scope": "NOT_REINTERPRETED",
        "historical_evidence_reinterpreted": False,
        "claims_to_predate_historical_runs": False,
        "prospective_mutant_matrix_executed_at_freeze": 0,
        "prospective_mutant_matrix_planned_at_freeze": 9,
        # Scoped exactly: no OFFICIAL_FROZEN_ATTEMPT from the prospective 0/9
        # matrix was executed under v1.1.  This says nothing about the historical
        # mutant executions, which did occur and are not reinterpreted here.
        "prospective_matrix_results_produced_under_v1_1": False,
    }
    policy["correction_reason"] = [
        "SINGLE_RETAINED_EVALUATION_AUTHORIZATION_MARKER",
        "TERMINAL_ATTEMPT_CANNOT_REVALIDATE_ALL_ADJUDICATIONS",
    ]

    journal = policy["authoritative_journal_derivation"]
    algorithm = list(journal["algorithm"])
    marker_step = "derive evaluation predicate from EVALUATION_AUTHORIZED marker"
    if marker_step not in algorithm:
        raise FreezeError("BASE_JOURNAL_ALGORITHM_UNEXPECTED")
    algorithm[algorithm.index(marker_step)] = (
        "derive one authorization context per predicate from every "
        "EVALUATION_AUTHORIZED marker at its own sequence"
    )
    journal["algorithm"] = algorithm
    journal["evaluation_authorization_derivation"] = {
        "input": "exact canonical JSON-lines journal bytes",
        "marker_prefix": MARKER_PREFIX,
        "cardinality": "AT_MOST_ONE_AUTHORIZATION_PER_PREDICATE",
        "duplicate_predicate_authorization": "REJECT",
        "unknown_predicate": "REJECT",
        "authorization_state_source": "STATE_REPLAYED_TO_MARKER_SEQUENCE",
        "authorization_state_is_never_terminal_state": True,
        "marker_state_must_be_in_predicate_allowed_states": True,
        "retained_fields_per_predicate": [
            "predicate_id",
            "phase",
            "deadline_identity",
            "authorization_state",
            "marker_sequence_number",
        ],
        "failure_codes": list(NEW_FAILURE_CODES[:3]),
    }
    journal["caller_verified_evaluation_context_authoritative"] = False

    full = policy["full_admissibility_validation"]
    full["resolved_context_document"] = "RESOLVED_EVIDENCE_CONTEXT_V1_2"
    full["evaluation_authorization_contexts"] = {
        "representation": "BOUNDED_MAPPING_PREDICATE_ID_TO_AUTHORIZATION_CONTEXT",
        "maximum_entries": len(PREDICATES),
        "derived_from": "authoritative_journal_derivation.evaluation_authorization_derivation",
        "single_last_marker_field_retained": False,
        "no_competing_authoritative_representation": True,
        "caller_supplied_context_overrides_journal": False,
    }
    full["adjudication_context_selection"] = {
        "selector": "CANDIDATE_PREDICATE_ORACLE_OR_CLASSIFICATION_ID",
        "lookup": "EXACT_MATCH_IN_EVALUATION_AUTHORIZATION_CONTEXTS",
        "missing_context": "EVALUATION_AUTHORIZATION_CONTEXT_MISSING",
        "last_marker_substitution_permitted": False,
        "publication_order": [
            "evaluation marker sequence",
            "every cited raw evidence publication sequence",
            "adjudication publication sequence",
        ],
        "publication_order_strict": True,
        "result_type": "BOOLEAN",
    }
    full["workload_window_context_selection"] = {
        "selector": "CANDIDATE_PREDICATE_ORACLE_OR_CLASSIFICATION_ID",
        "window_phase_must_equal_context_phase": True,
        "window_ordinal_must_equal_frozen_ordinal": True,
        "cross_window_substitution": "WORKLOAD_WINDOW_SUBSTITUTION",
    }
    full["attempt_envelope_adjudication_closure"] = {
        "revalidate_every_cited_adjudication": True,
        "raw_references_equal_complete_adjudication_closure": True,
        "duplicate_adjudication_reference": "ADJUDICATION_CLOSURE_INCOMPLETE",
        "omitted_adjudication": "ADJUDICATION_CLOSURE_INCOMPLETE",
        "omitted_or_extra_raw_reference": "ADJUDICATION_CLOSURE_INCOMPLETE",
        "fail_closed": True,
    }
    codes = list(full["failure_codes"])
    for code in NEW_FAILURE_CODES:
        if code not in codes:
            codes.append(code)
    full["failure_codes"] = codes

    policy["workload_stream_identity_protocol"]["evidence_policy_id"] = POLICY_ID
    auth = policy["authenticated_policy_input"]
    auth["expected_entry_paths"] = [GENERATOR_REL, POLICY_REL, SCHEMA_REL]
    auth["required_exact_bytes"] = [POLICY_REL, SCHEMA_REL, MANIFEST_REL]
    policy["runner_release_binding"]["evidence_policy_annotated_tag"] = TAG_NAME

    changed = assert_allowed_diff(base_policy, policy, POLICY_ALLOWED_PREFIXES, "POLICY")
    policy["semantic_diff_report"] = {
        "base_policy_id": BASE_POLICY_ID,
        "target_policy_id": POLICY_ID,
        "allowlisted_policy_prefixes": list(POLICY_ALLOWED_PREFIXES),
        "allowlisted_schema_prefixes": list(SCHEMA_ALLOWED_PREFIXES),
        "observed_policy_leaf_paths_before_report": sorted(changed),
        "unchanged_frozen_semantics": [
            "adjudication_predicate_raw_role_context_deadline_matrix",
            "workload_evidence_protocol.window_identity",
            "verified_attempt_state_machine",
            "roles",
            "adjudication_vocabularies",
            "kubernetes_evidence_surface",
            "service_restoration_projection",
            "workload_stream_identity_protocol",
        ],
        "all_unlisted_policy_and_schema_values_must_equal_v1_1": True,
    }
    return policy


def _authorization_context_schema(predicate: str, row: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "predicate_id": {"type": "string", "const": predicate},
            "phase": {"type": "string", "const": row["phase"]},
            "deadline_identity": {"type": "string", "const": row["deadline_identity"]},
            "authorization_state": {"type": "string", "enum": list(row["allowed_states"])},
            "marker_sequence_number": {"type": "integer", "minimum": 0},
        },
        "required": [
            "predicate_id",
            "phase",
            "deadline_identity",
            "authorization_state",
            "marker_sequence_number",
        ],
    }


def _correct_resolved_context(schema: dict[str, Any]) -> None:
    """Replace the scalar last-marker context with the per-predicate mapping."""
    definition = schema["$defs"]["resolved_evidence_context"]
    properties = definition["properties"]
    properties["document_type"] = {
        "type": "string",
        "const": "RESOLVED_EVIDENCE_CONTEXT_V1_2",
    }
    properties.pop("expected_evaluation_context", None)
    definition["required"] = [
        name for name in definition["required"] if name != "expected_evaluation_context"
    ]
    matrix = schema["$defs"]["evidence_policy_document"]["properties"][
        "full_admissibility_validation"
    ]["properties"]["adjudication_predicate_raw_role_context_deadline_matrix"]["properties"]
    entries = {}
    for predicate in PREDICATES:
        row = {
            "phase": matrix[predicate]["properties"]["phase"]["const"],
            "deadline_identity": matrix[predicate]["properties"]["deadline_identity"]["const"],
            "allowed_states": [
                item["const"]
                for item in matrix[predicate]["properties"]["allowed_states"]["prefixItems"]
            ],
        }
        entries[predicate] = _authorization_context_schema(predicate, row)
    properties["evaluation_authorization_contexts"] = {
        "type": "object",
        "additionalProperties": False,
        "maxProperties": len(PREDICATES),
        "properties": entries,
    }
    definition["required"].append("evaluation_authorization_contexts")


def build_schema(v11: Any, v1: Any, policy: dict[str, Any], base_schema: dict[str, Any]) -> dict[str, Any]:
    schema = v1.build_schema(policy)
    hash_schema = {"type": "string", "pattern": SHA256_PATTERN}
    for definition in (
        "payload_descriptor_workload_log_bytes",
        "descriptor_descriptor_workload_boundary",
        "payload_descriptor_workload_parse_result",
    ):
        v11.add_required_property(schema, definition, "stream_identity", hash_schema)
    for definition in ("workload_window_descriptor_identity", "workload_window_identity"):
        v11.add_required_property(schema, definition, "stream_identity", hash_schema)
    v11.add_required_property(schema, "workload_parse_result_payload", "stream_identity", hash_schema)
    schema["$defs"]["workload_entry_jsonl_record_v1_1"] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "index": {"type": "integer", "minimum": 0},
            "time_binary64_hex": {"type": "string", "pattern": r"^[0-9a-f]{16}$"},
            "number": {"type": "integer", "minimum": 0},
            "log": {"type": "string"},
            "ok": {"type": "boolean"},
        },
        "required": ["index", "time_binary64_hex", "number", "log", "ok"],
    }
    schema["oneOf"].append({"$ref": "#/$defs/workload_entry_jsonl_record_v1_1"})
    schema["$id"] = "https://sremut.local/schemas/evidence-capture-policy-v1.2.schema.json"
    schema["title"] = "SREMut Evidence Capture Policy v1.2"
    auth = schema["$defs"]["authenticated_policy_input"]["properties"]
    auth["expected_policy_relative_path"] = {"type": "string", "const": POLICY_REL}
    auth["expected_schema_relative_path"] = {"type": "string", "const": SCHEMA_REL}
    auth["expected_generator_relative_path"] = {"type": "string", "const": GENERATOR_REL}
    release = schema["$defs"]["offline_seal_input"]["properties"]["runner_release_binding"]["properties"]
    release["evidence_policy_annotated_tag"] = {"type": "string", "const": TAG_NAME}
    _correct_resolved_context(schema)
    schema["x-evidence-policy-semantic-version"] = SEMANTIC_VERSION
    v1.assert_closed_object_schemas(schema)
    Draft202012Validator.check_schema(schema)
    v1.assert_all_defs_reachable(schema)
    assert_allowed_diff(base_schema, schema, SCHEMA_ALLOWED_PREFIXES, "SCHEMA")
    Draft202012Validator(schema).validate(policy)
    return schema


# ---------------------------------------------------------------------------
# reference tests -- every positive drives v1_2_full_admissibility
# ---------------------------------------------------------------------------


def expect_dispatch_failure(code: str, function: Callable[..., Any], *args: Any) -> None:
    try:
        function(*args)
    except DispatchError as error:
        if str(error) != code:
            raise FreezeError(f"UNEXPECTED_FAILURE_CODE:{error}:{code}") from None
        return
    raise FreezeError(f"EXPECTED_FAILURE_NOT_RAISED:{code}")


def _mutate(bundle: dict[str, Any], **changes: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    return deepcopy(bundle["resolved"]), deepcopy(bundle["envelope"])


def run_reference_tests(policy: dict[str, Any], schema: dict[str, Any]) -> tuple[int, int]:
    bundle = build_sealed_attempt(policy, schema)
    resolved = bundle["resolved"]
    envelope = bundle["envelope"]
    matrix = predicate_matrix(policy)
    validator = Draft202012Validator(schema)
    dispatch = lambda ctx, cand: v1_2_full_admissibility(policy, schema, ctx, cand)

    positive = 0
    negative = 0

    # P1 - the sealed context and every candidate are v1.2 schema-valid.
    for document in [resolved, envelope, *[
        bundle["descriptors"][bundle["per_predicate"][p]["adjudication"]["evidence_id"]]
        for p in PREDICATES
    ]]:
        validator.validate(document)
    positive += 1

    # P2 - one FINALIZED journal carries all three markers, each in its own state.
    derived = reconstruct_journal(policy, bundle["records"])
    contexts = derived["evaluation_authorization_contexts"]
    if derived["terminal_state"] != "FINALIZED" or set(contexts) != set(PREDICATES):
        raise FreezeError("REFERENCE_JOURNAL_INVALID")
    for predicate in PREDICATES:
        if contexts[predicate]["authorization_state"] not in matrix[predicate]["allowed_states"]:
            raise FreezeError("REFERENCE_JOURNAL_INVALID")
    if len({c["authorization_state"] for c in contexts.values()}) != 3:
        raise FreezeError("REFERENCE_JOURNAL_INVALID")
    positive += 1

    # P3 - all three adjudications pass the integrated dispatcher.
    for predicate in PREDICATES:
        candidate = bundle["descriptors"][
            bundle["per_predicate"][predicate]["adjudication"]["evidence_id"]
        ]
        if not dispatch(resolved, candidate)["valid"]:
            raise FreezeError("REFERENCE_ADJUDICATION_INVALID")
    positive += 1

    # P4 - all three workload windows pass through the dispatcher's own path.
    for predicate in PREDICATES:
        validate_workload_window(
            policy, derived, predicate, bundle["per_predicate"][predicate]["window"]
        )
    positive += 1

    # P5 - the complete FINALIZED envelope passes closure through the dispatcher.
    if not dispatch(resolved, envelope)["valid"]:
        raise FreezeError("REFERENCE_ENVELOPE_INVALID")
    positive += 1

    # P6 - the fixture and derivation are deterministic.
    again = build_sealed_attempt(policy, schema)
    if canonical_json_bytes(again["resolved"]) != canonical_json_bytes(resolved):
        raise FreezeError("REFERENCE_DERIVATION_NOT_DETERMINISTIC")
    positive += 1

    # ---- negatives ---------------------------------------------------------

    # N1 - scalar last-marker context.
    ctx, env = _mutate(bundle)
    ctx["expected_evaluation_context"] = {"predicate_id": "RESTORATION_POSITIVE_CONTROL"}
    expect_dispatch_failure("RESOLVED_CONTEXT_INVALID", dispatch, ctx, env)
    negative += 1

    # N2 - missing predicate context.
    ctx, env = _mutate(bundle)
    ctx["evaluation_authorization_contexts"].pop("INITIAL_INVARIANT_EVALUATION")
    expect_dispatch_failure("JOURNAL_CONTEXT_MISMATCH", dispatch, ctx, env)
    negative += 1

    # N3 - duplicate marker for one predicate.
    ctx, env = _mutate(bundle)
    ctx["attempt_journal_records"] = _rechain(
        _insert(bundle["records"], 7, f"{MARKER_PREFIX}INITIAL_INVARIANT_EVALUATION")
    )
    expect_dispatch_failure("EVALUATION_AUTHORIZATION_DUPLICATE", dispatch, ctx, env)
    negative += 1

    # N4 - marker in a state that predicate does not allow.
    ctx, env = _mutate(bundle)
    ctx["attempt_journal_records"] = _rechain(
        _insert(bundle["records"], 2, f"{MARKER_PREFIX}RESTORATION_POSITIVE_CONTROL")
    )
    expect_dispatch_failure("EVALUATION_AUTHORIZATION_STATE_INVALID", dispatch, ctx, env)
    negative += 1

    # N5 - wrong phase in the adjudication's window identity.
    ctx, env = _mutate(bundle)
    target = bundle["per_predicate"]["INITIAL_INVARIANT_EVALUATION"]["adjudication"]["evidence_id"]
    candidate = deepcopy(bundle["descriptors"][target])
    candidate["workload_window_adjudication_identity"]["phase"] = "RESTORATION_POSITIVE_CONTROL"
    candidate["workload_window_adjudication_identity"]["ordinal"] = 3
    expect_dispatch_failure("WORKLOAD_EVALUATION_CONTEXT_MISMATCH", dispatch, ctx, candidate)
    negative += 1

    # N6 - wrong deadline.
    candidate = deepcopy(bundle["descriptors"][target])
    candidate["applicable_deadline"] = "RESTORATION_FRESH_WORKLOAD_60S"
    expect_dispatch_failure("ADJUDICATION_DEADLINE_MISMATCH", dispatch, resolved, candidate)
    negative += 1

    # N7 - cross-window substitution (window ordinal not its frozen ordinal).
    candidate = deepcopy(bundle["descriptors"][target])
    candidate["workload_window_adjudication_identity"]["ordinal"] = 2
    expect_dispatch_failure("WORKLOAD_WINDOW_MISMATCH", dispatch, resolved, candidate)
    negative += 1

    # N8 - evidence publication after the terminal transition.
    ctx, env = _mutate(bundle)
    records = deepcopy(bundle["records"])
    extra = dict(records[-1])
    records.append(extra)
    ctx["attempt_journal_records"] = _rechain_records(records)
    expect_dispatch_failure("POST_TERMINAL_OPERATION", dispatch, ctx, env)
    negative += 1

    # N9 - evidence publication absent from the journal.
    ctx, env = _mutate(bundle)
    stripped = deepcopy(bundle["records"])
    for record in stripped:
        record.pop("referenced_descriptor_sha256", None)
        record.pop("referenced_payload_sha256", None)
    ctx["attempt_journal_records"] = _rechain_records(stripped)
    expect_dispatch_failure("PUBLICATION_RECORD_MISSING", dispatch, ctx, env)
    negative += 1

    # N10 - omitted adjudication together with its raw references.
    ctx, env = _mutate(bundle)
    drop = bundle["per_predicate"]["RESTORATION_POSITIVE_CONTROL"]
    drop_id = drop["adjudication"]["evidence_id"]
    drop_raw = {reference["evidence_id"] for reference in drop["raw"]}
    env["adjudication_references"] = [
        reference for reference in env["adjudication_references"]
        if reference["evidence_id"] != drop_id
    ]
    env["raw_evidence_references"] = [
        reference for reference in env["raw_evidence_references"]
        if reference["evidence_id"] not in drop_raw
    ]
    expect_dispatch_failure("ADJUDICATION_CLOSURE_INCOMPLETE", dispatch, ctx, env)
    negative += 1

    # N11 - duplicate predicate under a different evidence ID, fully published so
    # the duplicate-predicate rule itself is what rejects it.
    ctx, env = _mutate(bundle)
    source = bundle["descriptors"][target]
    clone = deepcopy(source)
    clone["adjudication_id"] = clone["adjudication_id"] + ":clone"
    clone, clone_reference = _finalize_descriptor(clone, carries_evidence_id=False)
    ctx["parsed_canonical_descriptors"][clone_reference["evidence_id"]] = clone
    ctx["evidence_refs"][clone_reference["evidence_id"]] = clone_reference
    ctx["exact_descriptor_bytes_hex"][clone_reference["evidence_id"]] = canonical_json_bytes(clone).hex()
    published = deepcopy(bundle["records"])
    published.insert(8, _record(8, "STATE_VERIFIED:ORIGINAL_ORACLE_EVALUATED", "0" * 64,
                                referenced_descriptor_sha256=[clone_reference["descriptor_sha256"]]))
    ctx["attempt_journal_records"] = _rechain_records(published)
    ctx["evaluation_authorization_contexts"] = deepcopy(
        reconstruct_journal(policy, ctx["attempt_journal_records"])[
            "evaluation_authorization_contexts"
        ]
    )
    env["adjudication_references"].append(deepcopy(clone_reference))
    expect_dispatch_failure("ADJUDICATION_CLOSURE_INCOMPLETE", dispatch, ctx, env)
    negative += 1

    # N12 - extra unreferenced adjudication left in the sealed context.
    ctx, env = _mutate(bundle)
    orphan = deepcopy(source)
    orphan["adjudication_id"] = orphan["adjudication_id"] + ":orphan"
    orphan, orphan_reference = _finalize_descriptor(orphan, carries_evidence_id=False)
    ctx["parsed_canonical_descriptors"][orphan_reference["evidence_id"]] = orphan
    ctx["evidence_refs"][orphan_reference["evidence_id"]] = orphan_reference
    ctx["exact_descriptor_bytes_hex"][orphan_reference["evidence_id"]] = canonical_json_bytes(orphan).hex()
    expect_dispatch_failure("ADJUDICATION_CLOSURE_INCOMPLETE", dispatch, ctx, env)
    negative += 1

    # N13 - omitted, extra, and duplicate raw references.
    for change in ("omit", "extra", "duplicate"):
        ctx, env = _mutate(bundle)
        if change == "omit":
            env["raw_evidence_references"] = env["raw_evidence_references"][:-1]
        elif change == "extra":
            spare_payload = b"spare-workload-log\n"
            spare, spare_reference = _finalize_descriptor({
                **_common_metadata(),
                "document_type": "PAYLOAD_EVIDENCE_DESCRIPTOR_V1",
                "role": "workload_log_bytes",
                "producer": "WORKLOAD_EVIDENCE_ADAPTER",
                "source_kind": "IN_PROCESS_WORKLOAD",
                "media_type": "application/octet-stream",
                "storage_class": "PAYLOAD_WITH_DESCRIPTOR",
                "monotonic_ns": 9999,
                "complete_entry_count": 0,
                "entry_indexes": [],
                "entry_time_ieee754_binary64_hex": [],
                "workload_window": deepcopy(
                    bundle["per_predicate"]["INITIAL_INVARIANT_EVALUATION"]["window"]
                ),
                "stream_identity": stream_identity(FIXTURE_RUN_ID, FIXTURE_ATTEMPT_ID),
                **_payload_paths(spare_payload),
            })
            spare["workload_window"] = {
                key: value for key, value in spare["workload_window"].items()
                if key in ("phase", "ordinal", "run_id", "attempt_id", "mutant_id",
                           "repetition", "stream_identity")
            }
            spare, spare_reference = _finalize_descriptor(spare)
            ctx["evidence_refs"][spare_reference["evidence_id"]] = spare_reference
            ctx["parsed_canonical_descriptors"][spare_reference["evidence_id"]] = spare
            ctx["exact_descriptor_bytes_hex"][spare_reference["evidence_id"]] = (
                canonical_json_bytes(spare).hex()
            )
            ctx["exact_payload_bytes_hex"][spare_reference["evidence_id"]] = spare_payload.hex()
            env["raw_evidence_references"].append(deepcopy(spare_reference))
        else:
            env["raw_evidence_references"].append(deepcopy(env["raw_evidence_references"][0]))
        expect_dispatch_failure("ADJUDICATION_CLOSURE_INCOMPLETE", dispatch, ctx, env)
    negative += 1

    # N14 - non-schema adjudication field (the v1.1 fixture's top-level phase).
    candidate = deepcopy(bundle["descriptors"][target])
    candidate["phase"] = "INITIAL_MUTANT_CHALLENGE"
    expect_dispatch_failure("STRUCTURAL_SCHEMA_INVALID", dispatch, resolved, candidate)
    negative += 1

    # N15 - caller-supplied context substituted for journal reconstruction.
    ctx, env = _mutate(bundle)
    ctx["evaluation_authorization_contexts"]["INITIAL_INVARIANT_EVALUATION"][
        "authorization_state"
    ] = "CONTRACT_EVALUATED"
    expect_dispatch_failure("JOURNAL_CONTEXT_MISMATCH", dispatch, ctx, env)
    negative += 1

    # N16 - a v1.1 stream identity presented as a v1.2 identity.
    ctx, env = _mutate(bundle)
    candidate = deepcopy(bundle["descriptors"][target])
    candidate["workload_window_adjudication_identity"]["stream_identity"] = v1_1_stream_identity(
        FIXTURE_RUN_ID, FIXTURE_ATTEMPT_ID
    )
    expect_dispatch_failure("WORKLOAD_STREAM_IDENTITY_MISMATCH", dispatch, ctx, candidate)
    negative += 1

    # N17 - tampered journal hash chain.
    ctx, env = _mutate(bundle)
    ctx["attempt_journal_records"][5]["canonical_current_entry_sha256"] = "0" * 64
    expect_dispatch_failure("JOURNAL_CHAIN_INVALID", dispatch, ctx, env)
    negative += 1

    if positive != 6 or negative != 17:
        raise FreezeError(f"REFERENCE_TEST_COUNT_INVALID:{positive}:{negative}")
    return positive, negative


def _insert(records: list[dict[str, Any]], index: int, transition: str) -> list[dict[str, Any]]:
    working = deepcopy(records)
    working.insert(index, _record(index, transition, "0" * 64))
    return working


def _rechain(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _rechain_records(records)


def _rechain_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    working = deepcopy(records)
    previous = "0" * 64
    for index, record in enumerate(working):
        record["sequence_number"] = index
        record["previous_entry_sha256"] = previous
        record["monotonic_ns"] = index + 1
        record.pop("canonical_current_entry_sha256", None)
        record["canonical_current_entry_sha256"] = journal_record_sha256(record)
        previous = record["canonical_current_entry_sha256"]
    return working


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------


def render_policy(policy: dict[str, Any]) -> bytes:
    return yaml.safe_dump(policy, sort_keys=False, allow_unicode=True, width=100).encode("utf-8")


def render_schema(schema: dict[str, Any]) -> bytes:
    return (json.dumps(schema, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def render_manifest(policy_bytes: bytes, schema_bytes: bytes) -> bytes:
    rows = (
        (sha256_path(GENERATOR), GENERATOR_REL),
        (sha256_bytes(policy_bytes), POLICY_REL),
        (sha256_bytes(schema_bytes), SCHEMA_REL),
    )
    return "".join(f"{digest}  {path}\n" for digest, path in rows).encode("ascii")


def expected_artifacts() -> tuple[dict[str, Any], dict[str, Any], dict[Path, bytes], tuple[int, int]]:
    verify_repository_state()
    verify_base_artifacts()
    v11, v1, _v1_policy, base_policy, base_schema = reconstruct_v1_1()
    policy = build_policy(base_policy)
    schema = build_schema(v11, v1, policy, base_schema)
    counts = run_reference_tests(policy, schema)
    policy_bytes = render_policy(policy)
    schema_bytes = render_schema(schema)
    return policy, schema, {
        POLICY: policy_bytes,
        SCHEMA: schema_bytes,
        MANIFEST: render_manifest(policy_bytes, schema_bytes),
    }, counts


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise FreezeError("SYMLINK_DESTINATION_FORBIDDEN")
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    if temporary.exists() or temporary.is_symlink():
        raise FreezeError("TEMPORARY_PATH_EXISTS")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise FreezeError("SHORT_WRITE")
            view = view[written:]
        os.fchmod(descriptor, 0o644)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary, path)
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def generate() -> None:
    _policy, _schema, artifacts, counts = expected_artifacts()
    for path in (POLICY, SCHEMA, MANIFEST):
        atomic_write(path, artifacts[path])
    print(f"V1_2_REFERENCE_POSITIVE={counts[0]}/6")
    print(f"V1_2_REFERENCE_NEGATIVE={counts[1]}/17")


def check() -> None:
    _policy, _schema, artifacts, counts = expected_artifacts()
    for path, expected in artifacts.items():
        if (
            path.is_symlink()
            or not path.is_file()
            or stat.S_IMODE(path.stat().st_mode) != 0o644
            or path.read_bytes() != expected
        ):
            raise FreezeError("V1_2_ARTIFACT_MISMATCH")
    if parse_manifest(MANIFEST.read_bytes()) != {
        GENERATOR_REL: sha256_path(GENERATOR),
        POLICY_REL: sha256_path(POLICY),
        SCHEMA_REL: sha256_path(SCHEMA),
    }:
        raise FreezeError("V1_2_MANIFEST_MISMATCH")
    print(f"V1_2_REFERENCE_POSITIVE={counts[0]}/6")
    print(f"V1_2_REFERENCE_NEGATIVE={counts[1]}/17")
    print("V1_2_CHECK=PASS")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    arguments = parser.parse_args()
    if arguments.check:
        check()
    else:
        generate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

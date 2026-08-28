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
)

#: Every schema path v1.2 is permitted to change relative to v1.1.
SCHEMA_ALLOWED_PREFIXES = (
    "$id",
    "title",
    "$defs.evidence_policy_document",
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
# v1.2 reference implementation
#
# These functions ARE the corrected semantics the policy describes.  The
# reference tests below drive this dispatcher; nothing here is a toy checker
# living beside an unexercised specification.
# ---------------------------------------------------------------------------


def predicate_matrix(policy: dict[str, Any]) -> dict[str, Any]:
    return policy["full_admissibility_validation"][
        "adjudication_predicate_raw_role_context_deadline_matrix"
    ]


def derive_evaluation_authorizations(
    policy: dict[str, Any], records: list[dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    """Reconstruct one authorization context per predicate from journal bytes.

    The authorization state is the attempt state AT the marker's own sequence
    number, replayed forward from CREATED; it is never the terminal state.  Each
    predicate may be authorized exactly once.
    """
    matrix = predicate_matrix(policy)
    contexts: dict[str, dict[str, Any]] = {}
    state = "CREATED"
    previous_sequence = -1
    for record in records:
        sequence = record.get("sequence_number")
        if type(sequence) is not int or sequence != previous_sequence + 1:
            raise FreezeError("JOURNAL_SEQUENCE_INVALID")
        previous_sequence = sequence
        transition = record.get("transition")
        if not isinstance(transition, str):
            raise FreezeError("JOURNAL_RECORD_INVALID")
        if transition.startswith(MARKER_PREFIX):
            predicate = transition[len(MARKER_PREFIX):]
            row = matrix.get(predicate)
            if not isinstance(row, dict):
                raise FreezeError("EVALUATION_AUTHORIZATION_UNKNOWN_PREDICATE")
            if predicate in contexts:
                raise FreezeError("EVALUATION_AUTHORIZATION_DUPLICATE")
            if state not in row["allowed_states"]:
                raise FreezeError("EVALUATION_AUTHORIZATION_STATE_INVALID")
            contexts[predicate] = {
                "predicate_id": predicate,
                "phase": row["phase"],
                "deadline_identity": row["deadline_identity"],
                "authorization_state": state,
                "marker_sequence_number": sequence,
            }
        elif transition.startswith(OPERATION_PREFIX):
            continue
        elif "->" in transition:
            source, target = transition.split("->", 1)
            if source != state:
                raise FreezeError("JOURNAL_CONTEXT_MISMATCH")
            state = target
    return contexts


def select_evaluation_context(
    contexts: dict[str, dict[str, Any]], predicate: Any
) -> dict[str, Any]:
    """Select the context belonging to the CANDIDATE's own predicate."""
    if not isinstance(predicate, str) or predicate not in contexts:
        raise FreezeError("EVALUATION_AUTHORIZATION_CONTEXT_MISSING")
    return contexts[predicate]


def validate_adjudication_candidate(
    policy: dict[str, Any],
    contexts: dict[str, dict[str, Any]],
    evidence: dict[str, dict[str, Any]],
    candidate: dict[str, Any],
    *,
    caller_context: Any = None,
) -> None:
    """Validate one adjudication descriptor under v1.2 candidate-specific lookup."""
    if caller_context is not None:
        # A caller-supplied "current"/"last" context is never authoritative.
        raise FreezeError("CALLER_EVALUATION_CONTEXT_NOT_AUTHORITATIVE")
    predicate = candidate.get("predicate_oracle_or_classification_id")
    context = select_evaluation_context(contexts, predicate)
    row = predicate_matrix(policy).get(predicate)
    if not isinstance(row, dict):
        raise FreezeError("EVALUATION_AUTHORIZATION_UNKNOWN_PREDICATE")
    if candidate.get("phase") != row["phase"] or row["phase"] != context["phase"]:
        raise FreezeError("ADJUDICATION_EVALUATION_PHASE_MISMATCH")
    if (
        candidate.get("applicable_deadline") != row["deadline_identity"]
        or row["deadline_identity"] != context["deadline_identity"]
    ):
        raise FreezeError("ADJUDICATION_DEADLINE_MISMATCH")
    if candidate.get("result_type") != "BOOLEAN":
        raise FreezeError("ADJUDICATION_RAW_ROLE_INVALID")
    marker = context["marker_sequence_number"]
    publication = candidate.get("publication_sequence_number")
    references = candidate.get("raw_evidence_references")
    hashes = candidate.get("raw_evidence_sha256_per_reference")
    if (
        type(publication) is not int
        or not isinstance(references, list)
        or not references
        or not isinstance(hashes, list)
        or len(references) != len(hashes)
    ):
        raise FreezeError("ADJUDICATION_RAW_REFERENCE_REQUIRED")
    allowed_roles = set(row["allowed_raw_roles"])
    seen: set[str] = set()
    for index, evidence_id in enumerate(references):
        row_evidence = evidence.get(evidence_id)
        if row_evidence is None:
            raise FreezeError("EVIDENCE_REFERENCE_UNRESOLVED")
        if evidence_id in seen or row_evidence["role"] not in allowed_roles:
            raise FreezeError("ADJUDICATION_RAW_ROLE_INVALID")
        seen.add(evidence_id)
        if hashes[index] != row_evidence["payload_sha256"]:
            raise FreezeError("PAYLOAD_HASH_MISMATCH")
        if not marker < row_evidence["publication_sequence_number"] < publication:
            raise FreezeError("PUBLICATION_ORDER_INVALID")
        if row_evidence.get("window_phase") not in (None, row["phase"]):
            raise FreezeError("WORKLOAD_WINDOW_SUBSTITUTION")


def validate_workload_window_candidate(
    policy: dict[str, Any],
    contexts: dict[str, dict[str, Any]],
    candidate: dict[str, Any],
) -> None:
    """Validate a workload window against ITS predicate's context."""
    predicate = candidate.get("predicate_oracle_or_classification_id")
    context = select_evaluation_context(contexts, predicate)
    window = candidate.get("workload_window")
    if not isinstance(window, dict):
        raise FreezeError("WORKLOAD_WINDOW_INVALID")
    if window.get("phase") != context["phase"]:
        raise FreezeError("WORKLOAD_WINDOW_SUBSTITUTION")
    ordinals = policy["workload_evidence_protocol"]["window_identity"]["phase_ordinal_enum"]
    if window.get("ordinal") != ordinals[window["phase"]]:
        raise FreezeError("WORKLOAD_WINDOW_SUBSTITUTION")


def validate_attempt_envelope(
    policy: dict[str, Any],
    contexts: dict[str, dict[str, Any]],
    evidence: dict[str, dict[str, Any]],
    adjudications: dict[str, dict[str, Any]],
    envelope: dict[str, Any],
) -> None:
    """Revalidate every cited adjudication and require exact raw closure."""
    cited = envelope.get("adjudication_references")
    raw_references = envelope.get("raw_evidence_references")
    if not isinstance(cited, list) or not cited or not isinstance(raw_references, list):
        raise FreezeError("ADJUDICATION_RAW_REFERENCE_REQUIRED")
    if len(set(cited)) != len(cited):
        raise FreezeError("ADJUDICATION_CLOSURE_INCOMPLETE")
    closure: set[str] = set()
    for identifier in cited:
        candidate = adjudications.get(identifier)
        if candidate is None:
            raise FreezeError("EVIDENCE_REFERENCE_UNRESOLVED")
        validate_adjudication_candidate(policy, contexts, evidence, candidate)
        closure.update(candidate["raw_evidence_references"])
    if len(set(raw_references)) != len(raw_references):
        raise FreezeError("ADJUDICATION_CLOSURE_INCOMPLETE")
    if set(raw_references) != closure:
        raise FreezeError("ADJUDICATION_CLOSURE_INCOMPLETE")


# ---------------------------------------------------------------------------
# policy construction
# ---------------------------------------------------------------------------


def build_policy(base_policy: dict[str, Any]) -> dict[str, Any]:
    policy = deepcopy(base_policy)
    policy["document_type"] = "EVIDENCE_POLICY_DOCUMENT_V1_2"
    policy["semantic_version"] = SEMANTIC_VERSION
    policy["policy_id"] = POLICY_ID
    policy["status"] = "FROZEN_BEFORE_MUTANT_EXECUTION"
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
        "mutant_results_produced_under_v1_1": False,
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

    auth = policy["authenticated_policy_input"]
    auth["expected_entry_paths"] = [GENERATOR_REL, POLICY_REL, SCHEMA_REL]
    auth["required_exact_bytes"] = [GENERATOR_REL, POLICY_REL, SCHEMA_REL]
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
    schema["x-evidence-policy-semantic-version"] = SEMANTIC_VERSION
    v1.assert_closed_object_schemas(schema)
    Draft202012Validator.check_schema(schema)
    v1.assert_all_defs_reachable(schema)
    assert_allowed_diff(base_schema, schema, SCHEMA_ALLOWED_PREFIXES, "SCHEMA")
    Draft202012Validator(schema).validate(policy)
    return schema


# ---------------------------------------------------------------------------
# reference tests
# ---------------------------------------------------------------------------


def expect_failure(code: str, function: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
    try:
        function(*args, **kwargs)
    except FreezeError as error:
        if str(error) != code:
            raise FreezeError(f"UNEXPECTED_FAILURE_CODE:{error}:{code}") from None
        return
    raise FreezeError(f"EXPECTED_FAILURE_NOT_RAISED:{code}")


def terminal_journal() -> list[dict[str, Any]]:
    """One FINALIZED attempt carrying all three markers in their own states."""
    transitions = [
        "CREATED->PREFLIGHT_PASS",
        "PREFLIGHT_PASS->HEALTHY_STATE_CAPTURED",
        "HEALTHY_STATE_CAPTURED->MUTANT_INJECTED",
        "MUTANT_INJECTED->MUTANT_STATE_VERIFIED",
        "MUTANT_STATE_VERIFIED->ORIGINAL_ORACLE_STARTED",
        "ORIGINAL_ORACLE_STARTED->ORIGINAL_ORACLE_EVALUATED",
        f"{MARKER_PREFIX}INITIAL_INVARIANT_EVALUATION",
        "ORIGINAL_ORACLE_EVALUATED->CONTRACT_EVALUATED",
        f"{MARKER_PREFIX}REPLACEMENT_PERSISTENCE_EVALUATION",
        "CONTRACT_EVALUATED->RESTORE_STARTED",
        f"{MARKER_PREFIX}RESTORATION_POSITIVE_CONTROL",
        "RESTORE_STARTED->RESTORE_VERIFIED",
        "RESTORE_VERIFIED->FINALIZED",
    ]
    return [
        {"sequence_number": index, "transition": value}
        for index, value in enumerate(transitions)
    ]


def reference_bundle(policy: dict[str, Any]) -> tuple[
    list[dict[str, Any]], dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, Any]
]:
    """A complete, internally consistent three-evaluation attempt."""
    records = terminal_journal()
    contexts = derive_evaluation_authorizations(policy, records)
    matrix = predicate_matrix(policy)
    evidence: dict[str, dict[str, Any]] = {}
    adjudications: dict[str, dict[str, Any]] = {}
    cited: list[str] = []
    sequence = 100
    for predicate in PREDICATES:
        row = matrix[predicate]
        context = contexts[predicate]
        raw_ids = []
        for role in ("workload_log_bytes", "workload_parse_result"):
            sequence += 1
            evidence_id = f"ev-{predicate}-{role}"
            evidence[evidence_id] = {
                "role": role,
                "payload_sha256": sha256_bytes(evidence_id.encode()),
                "publication_sequence_number": sequence,
                "window_phase": row["phase"],
            }
            raw_ids.append(evidence_id)
        sequence += 1
        identifier = f"adj-{predicate}"
        adjudications[identifier] = {
            "predicate_oracle_or_classification_id": predicate,
            "phase": row["phase"],
            "applicable_deadline": row["deadline_identity"],
            "result_type": "BOOLEAN",
            "publication_sequence_number": sequence,
            "raw_evidence_references": raw_ids,
            "raw_evidence_sha256_per_reference": [
                evidence[identifier_raw]["payload_sha256"] for identifier_raw in raw_ids
            ],
            "authorization_state": context["authorization_state"],
        }
        cited.append(identifier)
    envelope = {
        "document_type": "ATTEMPT_VALIDATION_ENVELOPE_V1",
        "adjudication_references": list(cited),
        "raw_evidence_references": sorted(evidence),
    }
    return records, evidence, adjudications, envelope


def run_reference_tests(policy: dict[str, Any]) -> tuple[int, int]:
    matrix = predicate_matrix(policy)
    positive = 0
    negative = 0

    records, evidence, adjudications, envelope = reference_bundle(policy)
    contexts = derive_evaluation_authorizations(policy, records)

    # P1 - one terminal FINALIZED journal contains all three markers.
    markers = [r["transition"][len(MARKER_PREFIX):] for r in records
               if r["transition"].startswith(MARKER_PREFIX)]
    if sorted(markers) != sorted(PREDICATES) or records[-1]["transition"] != "RESTORE_VERIFIED->FINALIZED":
        raise FreezeError("REFERENCE_JOURNAL_INVALID")
    positive += 1

    # P2 - each marker occurs in its own frozen allowed state.
    for predicate in PREDICATES:
        if contexts[predicate]["authorization_state"] not in matrix[predicate]["allowed_states"]:
            raise FreezeError("REFERENCE_AUTHORIZATION_STATE_INVALID")
    if len({c["authorization_state"] for c in contexts.values()}) != 3:
        raise FreezeError("REFERENCE_AUTHORIZATION_STATE_INVALID")
    positive += 1

    # P3 - all three adjudications validate against that one sealed journal.
    for predicate in PREDICATES:
        validate_adjudication_candidate(
            policy, contexts, evidence, adjudications[f"adj-{predicate}"]
        )
    positive += 1

    # P4 - all three workload windows validate against their own contexts.
    ordinals = policy["workload_evidence_protocol"]["window_identity"]["phase_ordinal_enum"]
    for predicate in PREDICATES:
        phase = matrix[predicate]["phase"]
        validate_workload_window_candidate(
            policy,
            contexts,
            {
                "predicate_oracle_or_classification_id": predicate,
                "workload_window": {"phase": phase, "ordinal": ordinals[phase]},
            },
        )
    positive += 1

    # P5 - the envelope citing all three passes complete closure validation.
    validate_attempt_envelope(policy, contexts, evidence, adjudications, envelope)
    positive += 1

    # P6 - repeated derivation is deterministic and byte-identical.
    again = derive_evaluation_authorizations(policy, terminal_journal())
    if json.dumps(again, sort_keys=True) != json.dumps(contexts, sort_keys=True):
        raise FreezeError("REFERENCE_DERIVATION_NOT_DETERMINISTIC")
    positive += 1

    # N1 - duplicate marker for one predicate.
    duplicated = terminal_journal()
    duplicated.insert(7, {"sequence_number": 7,
                          "transition": f"{MARKER_PREFIX}INITIAL_INVARIANT_EVALUATION"})
    for index, record in enumerate(duplicated):
        record["sequence_number"] = index
    expect_failure("EVALUATION_AUTHORIZATION_DUPLICATE",
                   derive_evaluation_authorizations, policy, duplicated)
    negative += 1

    # N2 - missing marker.
    missing = [r for r in terminal_journal()
               if r["transition"] != f"{MARKER_PREFIX}REPLACEMENT_PERSISTENCE_EVALUATION"]
    for index, record in enumerate(missing):
        record["sequence_number"] = index
    partial = derive_evaluation_authorizations(policy, missing)
    expect_failure("EVALUATION_AUTHORIZATION_CONTEXT_MISSING",
                   validate_adjudication_candidate, policy, partial, evidence,
                   adjudications["adj-REPLACEMENT_PERSISTENCE_EVALUATION"])
    negative += 1

    # N3 - unknown predicate.
    unknown = terminal_journal()
    unknown[6] = {"sequence_number": 6, "transition": f"{MARKER_PREFIX}NOT_A_PREDICATE"}
    expect_failure("EVALUATION_AUTHORIZATION_UNKNOWN_PREDICATE",
                   derive_evaluation_authorizations, policy, unknown)
    negative += 1

    # N4 - marker in a disallowed state.
    disallowed = terminal_journal()
    disallowed[6] = {"sequence_number": 6,
                     "transition": f"{MARKER_PREFIX}RESTORATION_POSITIVE_CONTROL"}
    disallowed[10] = {"sequence_number": 10,
                      "transition": f"{MARKER_PREFIX}INITIAL_INVARIANT_EVALUATION"}
    expect_failure("EVALUATION_AUTHORIZATION_STATE_INVALID",
                   derive_evaluation_authorizations, policy, disallowed)
    negative += 1

    # N5 - wrong phase.
    wrong_phase = deepcopy(adjudications["adj-INITIAL_INVARIANT_EVALUATION"])
    wrong_phase["phase"] = matrix["RESTORATION_POSITIVE_CONTROL"]["phase"]
    expect_failure("ADJUDICATION_EVALUATION_PHASE_MISMATCH",
                   validate_adjudication_candidate, policy, contexts, evidence, wrong_phase)
    negative += 1

    # N6 - wrong deadline.
    wrong_deadline = deepcopy(adjudications["adj-INITIAL_INVARIANT_EVALUATION"])
    wrong_deadline["applicable_deadline"] = matrix["RESTORATION_POSITIVE_CONTROL"]["deadline_identity"]
    expect_failure("ADJUDICATION_DEADLINE_MISMATCH",
                   validate_adjudication_candidate, policy, contexts, evidence, wrong_deadline)
    negative += 1

    # N7 - last-marker substitution for an earlier adjudication.  Under v1.1 the
    # only retained context was the last marker; v1.2 must not accept it.
    substituted = deepcopy(adjudications["adj-INITIAL_INVARIANT_EVALUATION"])
    substituted["predicate_oracle_or_classification_id"] = "RESTORATION_POSITIVE_CONTROL"
    expect_failure("ADJUDICATION_EVALUATION_PHASE_MISMATCH",
                   validate_adjudication_candidate, policy, contexts, evidence, substituted)
    negative += 1

    # N8 - cross-window substitution.
    expect_failure(
        "WORKLOAD_WINDOW_SUBSTITUTION",
        validate_workload_window_candidate, policy, contexts,
        {
            "predicate_oracle_or_classification_id": "INITIAL_INVARIANT_EVALUATION",
            "workload_window": {
                "phase": matrix["RESTORATION_POSITIVE_CONTROL"]["phase"],
                "ordinal": ordinals[matrix["RESTORATION_POSITIVE_CONTROL"]["phase"]],
            },
        },
    )
    negative += 1

    # N9 - raw evidence published before authorization.
    early_evidence = deepcopy(evidence)
    early_evidence["ev-INITIAL_INVARIANT_EVALUATION-workload_log_bytes"][
        "publication_sequence_number"
    ] = contexts["INITIAL_INVARIANT_EVALUATION"]["marker_sequence_number"]
    expect_failure("PUBLICATION_ORDER_INVALID",
                   validate_adjudication_candidate, policy, contexts, early_evidence,
                   adjudications["adj-INITIAL_INVARIANT_EVALUATION"])
    negative += 1

    # N10 - adjudication published before its raw evidence.
    early_adjudication = deepcopy(adjudications["adj-INITIAL_INVARIANT_EVALUATION"])
    early_adjudication["publication_sequence_number"] = 100
    expect_failure("PUBLICATION_ORDER_INVALID",
                   validate_adjudication_candidate, policy, contexts, evidence,
                   early_adjudication)
    negative += 1

    # N11 - omitted adjudication from the envelope.
    omitted = deepcopy(envelope)
    omitted["adjudication_references"] = omitted["adjudication_references"][:2]
    expect_failure("ADJUDICATION_CLOSURE_INCOMPLETE",
                   validate_attempt_envelope, policy, contexts, evidence, adjudications, omitted)
    negative += 1

    # N12 - omitted OR extra raw reference (one requirement, both directions).
    thin = deepcopy(envelope)
    thin["raw_evidence_references"] = thin["raw_evidence_references"][:-1]
    expect_failure("ADJUDICATION_CLOSURE_INCOMPLETE",
                   validate_attempt_envelope, policy, contexts, evidence, adjudications, thin)
    fat = deepcopy(envelope)
    fat["raw_evidence_references"] = fat["raw_evidence_references"] + ["ev-unreferenced"]
    expect_failure("ADJUDICATION_CLOSURE_INCOMPLETE",
                   validate_attempt_envelope, policy, contexts, evidence, adjudications, fat)
    negative += 1

    # N13 - duplicate adjudication reference.
    duplicate_reference = deepcopy(envelope)
    duplicate_reference["adjudication_references"].append("adj-INITIAL_INVARIANT_EVALUATION")
    expect_failure("ADJUDICATION_CLOSURE_INCOMPLETE",
                   validate_attempt_envelope, policy, contexts, evidence, adjudications,
                   duplicate_reference)
    negative += 1

    # N14 - tampered marker sequence.
    tampered = terminal_journal()
    tampered[6]["sequence_number"] = 42
    expect_failure("JOURNAL_SEQUENCE_INVALID",
                   derive_evaluation_authorizations, policy, tampered)
    negative += 1

    # N15 - caller-supplied context conflicting with journal reconstruction.
    expect_failure("CALLER_EVALUATION_CONTEXT_NOT_AUTHORITATIVE",
                   validate_adjudication_candidate, policy, contexts, evidence,
                   adjudications["adj-INITIAL_INVARIANT_EVALUATION"],
                   caller_context={"predicate_id": "RESTORATION_POSITIVE_CONTROL"})
    negative += 1

    if positive != 6 or negative != 15:
        raise FreezeError(f"REFERENCE_TEST_COUNT_INVALID:{positive}:{negative}")
    return positive, negative


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
    counts = run_reference_tests(policy)
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
    print(f"V1_2_REFERENCE_NEGATIVE={counts[1]}/15")


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
    print(f"V1_2_REFERENCE_NEGATIVE={counts[1]}/15")
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

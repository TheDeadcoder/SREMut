"""Immutable local evidence objects, descriptors, and terminal seals."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import re
from pathlib import Path
from typing import Any, Mapping, NoReturn

from sremut.canonical_json import canonical_json_bytes, parse_canonical_json, sha256_hex
from sremut.journal import Journal, JournalError, SafeRoot
from sremut.policy_runtime import AuthenticatedPolicy
from sremut.sensitive import SensitiveCaptureError, detect_sensitive, validate_payload


ZERO_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_EVIDENCE_ID = re.compile(r"^ev-[0-9a-f]{32}$")
_MANIFEST_LINE = re.compile(rb"([0-9a-f]{64})  ([A-Za-z0-9._/-]+)\n")


@dataclass(slots=True)
class EvidenceError(ValueError):
    code: str

    def __str__(self) -> str:
        return self.code


def _reject(code: str) -> NoReturn:
    raise EvidenceError(code) from None


def _safe_relative(value: str, prefix: str) -> bool:
    if not isinstance(value, str) or not value.startswith(prefix) or value.startswith("/") or "\\" in value:
        return False
    return all(part not in ("", ".", "..") for part in value.split("/"))


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    document_type: str
    schema_version: int
    evidence_id: str
    role: str
    producer: str
    source_kind: str
    media_type: str
    storage_class: str
    descriptor_sha256: str
    descriptor_size_bytes: int
    descriptor_relative_path: str
    redaction_status: str
    payload_sha256: str | None = None
    payload_size_bytes: int | None = None
    payload_relative_path: str | None = None
    projection_class: str | None = None

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "document_type": self.document_type,
            "schema_version": self.schema_version,
            "evidence_id": self.evidence_id,
            "role": self.role,
            "producer": self.producer,
            "source_kind": self.source_kind,
            "media_type": self.media_type,
            "storage_class": self.storage_class,
            "descriptor_sha256": self.descriptor_sha256,
            "descriptor_size_bytes": self.descriptor_size_bytes,
            "descriptor_relative_path": self.descriptor_relative_path,
            "redaction_status": self.redaction_status,
        }
        if self.storage_class == "PAYLOAD_WITH_DESCRIPTOR":
            result.update(
                {
                    "payload_sha256": self.payload_sha256,
                    "payload_size_bytes": self.payload_size_bytes,
                    "payload_relative_path": self.payload_relative_path,
                }
            )
        if self.projection_class is not None:
            result["projection_class"] = self.projection_class
        return result


@dataclass(frozen=True, slots=True)
class ExternalAnchor:
    attempt_root_identifier: str
    manifest_relative_path: str
    terminal_manifest_sha256: str
    recorded_by: str = "IMMUTABLE_RUN_INDEX_OUTSIDE_ATTEMPT_ROOT"
    cited_by_result_aggregation: bool = True


@dataclass(frozen=True, slots=True)
class SealResult:
    terminal_outcome: str
    classification: str
    manifest_relative_path: str
    manifest_sha256: str
    manifest_size_bytes: int
    covered_paths: tuple[str, ...]
    validation_level: str = field(default="STRUCTURAL_SCHEMA_VALIDATION", init=False)
    full_admissibility: bool = field(default=False, init=False)
    semantic_validation_status: str = field(
        default="RUNNER_IMPLEMENTATION_INCOMPLETE",
        init=False,
    )


@dataclass(frozen=True, slots=True)
class SealVerification:
    terminal_outcome: str
    classification: str
    manifest_sha256: str
    covered_paths: tuple[str, ...]
    validation_level: str = field(default="STRUCTURAL_SCHEMA_VALIDATION", init=False)
    full_admissibility: bool = field(default=False, init=False)
    semantic_validation_status: str = field(
        default="RUNNER_IMPLEMENTATION_INCOMPLETE",
        init=False,
    )


def descriptor_content_sha256(descriptor: Mapping[str, Any]) -> str:
    material = deepcopy(dict(descriptor))
    material.pop("descriptor_sha256", None)
    material.pop("evidence_id", None)
    return sha256_hex(canonical_json_bytes(material))


def _reference_dict(reference: EvidenceRef | Mapping[str, Any]) -> dict[str, Any]:
    return reference.as_dict() if isinstance(reference, EvidenceRef) else dict(reference)


def validate_evidence_ref(
    policy: AuthenticatedPolicy,
    reference: EvidenceRef | Mapping[str, Any],
    *,
    descriptor_bytes: bytes | None = None,
    payload_bytes: bytes | None = None,
    expected_run_id: str | None = None,
    expected_attempt_id: str | None = None,
) -> EvidenceRef:
    value = _reference_dict(reference)
    try:
        policy.structural_validate(value)
    except Exception:
        _reject("EVIDENCE_REFERENCE_INVALID")
    role_name = value.get("role")
    if not isinstance(role_name, str) or role_name == "terminal_manifest":
        _reject("EVIDENCE_REFERENCE_INVALID")
    try:
        role = policy.role(role_name)
    except Exception:
        _reject("EVIDENCE_REFERENCE_INVALID")
    payload_expected = role.storage_class == "PAYLOAD_WITH_DESCRIPTOR"
    expected = {
        "document_type": "PAYLOAD_EVIDENCE_REF_V1" if payload_expected else "DESCRIPTOR_EVIDENCE_REF_V1",
        "schema_version": 1,
        "producer": role.producer,
        "source_kind": role.source_kind,
        "media_type": role.media_type,
        "storage_class": role.storage_class,
        "redaction_status": "NOT_REDACTED",
    }
    if any(value.get(key) != item for key, item in expected.items()):
        _reject("EVIDENCE_REFERENCE_INVALID")
    digest = value.get("descriptor_sha256")
    size = value.get("descriptor_size_bytes")
    if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
        _reject("EVIDENCE_REFERENCE_INVALID")
    if value.get("evidence_id") != "ev-" + digest[:32] or not _EVIDENCE_ID.fullmatch(value.get("evidence_id", "")):
        _reject("EVIDENCE_REFERENCE_INVALID")
    descriptor_path = f"descriptors/sha256/{digest[:2]}/{digest}.json"
    if value.get("descriptor_relative_path") != descriptor_path or not _safe_relative(descriptor_path, "descriptors/"):
        _reject("EVIDENCE_REFERENCE_INVALID")
    if not isinstance(size, int) or isinstance(size, bool) or not 1 <= size <= role.maximum_bytes:
        _reject("EVIDENCE_REFERENCE_INVALID")
    descriptor: dict[str, Any] | None = None
    if descriptor_bytes is not None:
        if len(descriptor_bytes) != size:
            _reject("DESCRIPTOR_REFERENCE_MISMATCH")
        try:
            parsed = parse_canonical_json(descriptor_bytes)
        except Exception:
            _reject("DESCRIPTOR_CANONICAL_MISMATCH")
        if not isinstance(parsed, dict):
            _reject("DESCRIPTOR_CANONICAL_MISMATCH")
        descriptor = parsed
        if descriptor_content_sha256(descriptor) != digest:
            _reject("DESCRIPTOR_HASH_MISMATCH")
        if "evidence_id" in role.required_metadata and descriptor.get("evidence_id") != value["evidence_id"]:
            _reject("DESCRIPTOR_REFERENCE_MISMATCH")
        for field in ("role", "producer", "source_kind", "media_type", "storage_class"):
            if descriptor.get(field) != value.get(field):
                _reject("DESCRIPTOR_REFERENCE_MISMATCH")
        if expected_run_id is not None and descriptor.get("run_id") != expected_run_id:
            _reject("RUN_ATTEMPT_MISMATCH")
        if expected_attempt_id is not None and descriptor.get("attempt_id") != expected_attempt_id:
            _reject("RUN_ATTEMPT_MISMATCH")
    payload_digest: str | None = None
    payload_size: int | None = None
    payload_path: str | None = None
    if payload_expected:
        payload_digest = value.get("payload_sha256")
        payload_size = value.get("payload_size_bytes")
        if not isinstance(payload_digest, str) or not _SHA256.fullmatch(payload_digest):
            _reject("EVIDENCE_REFERENCE_INVALID")
        if not isinstance(payload_size, int) or isinstance(payload_size, bool) or payload_size < 0 or payload_size > role.maximum_bytes:
            _reject("EVIDENCE_REFERENCE_INVALID")
        payload_path = f"objects/sha256/{payload_digest[:2]}/{payload_digest}"
        if value.get("payload_relative_path") != payload_path or not _safe_relative(payload_path, "objects/"):
            _reject("EVIDENCE_REFERENCE_INVALID")
        if (payload_size == 0) != (payload_digest == ZERO_SHA256):
            _reject("EVIDENCE_REFERENCE_INVALID")
        if payload_size == 0 and not role.zero_byte_payload_allowed:
            _reject("EVIDENCE_REFERENCE_INVALID")
        if payload_bytes is not None:
            if len(payload_bytes) != payload_size:
                _reject("PAYLOAD_SIZE_MISMATCH")
            if sha256_hex(payload_bytes) != payload_digest:
                _reject("PAYLOAD_HASH_MISMATCH")
        if descriptor is not None:
            for field in ("payload_sha256", "payload_size_bytes", "payload_relative_path"):
                if descriptor.get(field) != value.get(field):
                    _reject("DESCRIPTOR_REFERENCE_MISMATCH")
    elif any(name in value for name in ("payload_sha256", "payload_size_bytes", "payload_relative_path")) or payload_bytes is not None:
        _reject("EVIDENCE_REFERENCE_INVALID")
    projection_class = value.get("projection_class")
    if role_name == "kubernetes_object_projection":
        if not isinstance(projection_class, str):
            _reject("EVIDENCE_REFERENCE_INVALID")
    elif projection_class is not None:
        _reject("EVIDENCE_REFERENCE_INVALID")
    return EvidenceRef(
        document_type=expected["document_type"], schema_version=1,
        evidence_id=value["evidence_id"], role=role_name, producer=role.producer,
        source_kind=role.source_kind, media_type=role.media_type, storage_class=role.storage_class,
        descriptor_sha256=digest, descriptor_size_bytes=size,
        descriptor_relative_path=descriptor_path, redaction_status="NOT_REDACTED",
        payload_sha256=payload_digest, payload_size_bytes=payload_size,
        payload_relative_path=payload_path, projection_class=projection_class,
    )


class EvidenceStore:
    """Immutable evidence publisher beneath one caller-provided attempt root."""

    def __init__(
        self,
        root: Path,
        policy: AuthenticatedPolicy,
        run_id: str,
        attempt_id: str,
        *,
        _safe_root: SafeRoot | None = None,
    ):
        if _safe_root is not None and _safe_root.root != Path(root):
            _reject("EVIDENCE_ROOT_INVALID")
        self.fs = _safe_root if _safe_root is not None else SafeRoot(root)
        self._owns_fs = _safe_root is None
        self.policy = policy
        self.run_id = run_id
        self.attempt_id = attempt_id

    def close(self) -> None:
        if self._owns_fs:
            self.fs.close()

    def __enter__(self) -> EvidenceStore:
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        self.close()

    def _ensure_unsealed(self) -> None:
        if self.fs.exists("manifests/terminal.sha256"):
            _reject("ATTEMPT_ALREADY_SEALED")

    def _descriptor(
        self,
        role_name: str,
        metadata: Mapping[str, Any],
        payload: bytes | None,
    ) -> tuple[dict[str, Any], bytes, EvidenceRef]:
        role = self.policy.role(role_name)
        if role_name == "terminal_manifest":
            _reject("EVIDENCE_ROLE_INVALID")
        payload_expected = role.storage_class == "PAYLOAD_WITH_DESCRIPTOR"
        if payload_expected != (payload is not None):
            _reject("EVIDENCE_STORAGE_CLASS_MISMATCH")
        automatic = {
            "document_type", "schema_version", "evidence_id", "role", "producer",
            "source_kind", "media_type", "storage_class", "redaction_status",
        }
        allowed = set(role.required_metadata) | set(role.conditional_required_metadata)
        caller_allowed = allowed - automatic
        if not set(metadata).issubset(caller_allowed):
            _reject("EVIDENCE_METADATA_INVALID")
        required = set(role.required_metadata) - automatic
        if not required.issubset(metadata):
            _reject("EVIDENCE_METADATA_INVALID")
        if metadata.get("run_id", self.run_id) != self.run_id or metadata.get("attempt_id", self.attempt_id) != self.attempt_id:
            _reject("RUN_ATTEMPT_MISMATCH")
        descriptor = dict(metadata)
        descriptor.update(
            {
                "document_type": "PAYLOAD_EVIDENCE_DESCRIPTOR_V1" if payload_expected else "DESCRIPTOR_EVIDENCE_DESCRIPTOR_V1",
                "role": role_name,
                "producer": role.producer,
                "source_kind": role.source_kind,
                "media_type": role.media_type,
                "storage_class": role.storage_class,
            }
        )
        if "schema_version" in role.required_metadata:
            descriptor["schema_version"] = 1
        if "redaction_status" in role.required_metadata:
            descriptor["redaction_status"] = "NOT_REDACTED"
        if payload is not None:
            payload_digest = sha256_hex(payload)
            descriptor.update(
                {
                    "payload_sha256": payload_digest,
                    "payload_size_bytes": len(payload),
                    "payload_relative_path": f"objects/sha256/{payload_digest[:2]}/{payload_digest}",
                }
            )
        digest = descriptor_content_sha256(descriptor)
        evidence_id = "ev-" + digest[:32]
        if "evidence_id" in role.required_metadata:
            descriptor["evidence_id"] = evidence_id
        descriptor_bytes = canonical_json_bytes(descriptor)
        if len(descriptor_bytes) > role.maximum_bytes:
            _reject("EVIDENCE_DESCRIPTOR_SIZE_EXCEEDED")
        finding = detect_sensitive(descriptor_bytes, structured_format="json")
        if finding is not None:
            _reject(finding.rejection_code)
        projection_class = descriptor.get("projection_class") if role_name == "kubernetes_object_projection" else None
        reference = EvidenceRef(
            document_type="PAYLOAD_EVIDENCE_REF_V1" if payload_expected else "DESCRIPTOR_EVIDENCE_REF_V1",
            schema_version=1, evidence_id=evidence_id, role=role_name,
            producer=role.producer, source_kind=role.source_kind, media_type=role.media_type,
            storage_class=role.storage_class, descriptor_sha256=digest,
            descriptor_size_bytes=len(descriptor_bytes),
            descriptor_relative_path=f"descriptors/sha256/{digest[:2]}/{digest}.json",
            redaction_status="NOT_REDACTED",
            payload_sha256=descriptor.get("payload_sha256"),
            payload_size_bytes=descriptor.get("payload_size_bytes"),
            payload_relative_path=descriptor.get("payload_relative_path"),
            projection_class=projection_class,
        )
        try:
            self.policy.structural_validate(descriptor)
            self.policy.structural_validate(reference.as_dict())
        except Exception:
            _reject("EVIDENCE_METADATA_INVALID")
        validate_evidence_ref(
            self.policy, reference, descriptor_bytes=descriptor_bytes, payload_bytes=payload,
            expected_run_id=self.run_id, expected_attempt_id=self.attempt_id,
        )
        return descriptor, descriptor_bytes, reference

    def publish_payload(self, role: str, payload: bytes, metadata: Mapping[str, Any]) -> EvidenceRef:
        self._ensure_unsealed()
        contract = self.policy.role(role)
        try:
            validate_payload(self.policy, role, payload, media_type=contract.media_type)
            if contract.canonical_json_required:
                parse_canonical_json(payload)
        except SensitiveCaptureError as error:
            _reject(error.code)
        except Exception:
            _reject("PAYLOAD_CANONICALIZATION_INVALID")
        _descriptor, descriptor_bytes, reference = self._descriptor(role, metadata, payload)
        with self.fs.lock():
            self._ensure_unsealed()
            self.fs.write_atomic(reference.payload_relative_path or "", payload)
            self.fs.write_atomic(reference.descriptor_relative_path, descriptor_bytes)
        return reference

    def publish_descriptor(self, role: str, metadata: Mapping[str, Any]) -> EvidenceRef:
        self._ensure_unsealed()
        _descriptor, descriptor_bytes, reference = self._descriptor(role, metadata, None)
        with self.fs.lock():
            self._ensure_unsealed()
            self.fs.write_atomic(reference.descriptor_relative_path, descriptor_bytes)
        return reference

    def resolve(self, reference: EvidenceRef | Mapping[str, Any] | None) -> tuple[EvidenceRef, bytes, bytes | None] | None:
        if reference is None:
            return None
        value = _reference_dict(reference)
        descriptor_path = value.get("descriptor_relative_path")
        if not isinstance(descriptor_path, str):
            _reject("EVIDENCE_REFERENCE_INVALID")
        try:
            descriptor_bytes = self.fs.read_bytes(descriptor_path)
            payload_bytes = None
            if value.get("storage_class") == "PAYLOAD_WITH_DESCRIPTOR":
                payload_path = value.get("payload_relative_path")
                if not isinstance(payload_path, str):
                    _reject("EVIDENCE_REFERENCE_INVALID")
                payload_bytes = self.fs.read_bytes(payload_path)
        except JournalError:
            _reject("EVIDENCE_REFERENCE_UNRESOLVED")
        validated = validate_evidence_ref(
            self.policy, value, descriptor_bytes=descriptor_bytes, payload_bytes=payload_bytes,
            expected_run_id=self.run_id, expected_attempt_id=self.attempt_id,
        )
        return validated, descriptor_bytes, payload_bytes

    def write_global_stop(self, document: Mapping[str, Any], *, relative_path: str = "terminal/global-stop.json") -> str:
        expected = {
            "document_type": "TERMINAL_GLOBAL_STOP_V1",
            "schema_version": 1,
            "run_id": self.run_id,
            "attempt_id": self.attempt_id,
            "terminal_outcome": "RESTORATION_BLOCKED",
        }
        if any(document.get(key) != value for key, value in expected.items()):
            _reject("TERMINAL_GLOBAL_STOP_INVALID")
        data = canonical_json_bytes(dict(document))
        try:
            self.policy.structural_validate(dict(document))
        except Exception:
            _reject("TERMINAL_GLOBAL_STOP_INVALID")
        with self.fs.lock():
            self._ensure_unsealed()
            self.fs.write_atomic(relative_path, data)
        return relative_path

    def _coverage(self, outcome: str, journal_relative_path: str, global_stop_relative_path: str | None) -> tuple[str, ...]:
        paths = list(self.fs.list_regular_files(("objects", "descriptors", "journal")))
        if journal_relative_path not in paths:
            _reject("TERMINAL_MANIFEST_COVERAGE_MISMATCH")
        if outcome == "RESTORATION_BLOCKED":
            if global_stop_relative_path is None or not self.fs.exists(global_stop_relative_path):
                _reject("TERMINAL_GLOBAL_STOP_MISSING")
            paths.append(global_stop_relative_path)
        elif global_stop_relative_path is not None:
            _reject("TERMINAL_GLOBAL_STOP_INVALID")
        paths = sorted(set(paths), key=lambda item: item.encode("utf-8"))
        descriptors = [path for path in paths if path.startswith("descriptors/")]
        objects = {path for path in paths if path.startswith("objects/")}
        referenced_objects: set[str] = set()
        for path in descriptors:
            try:
                descriptor_bytes = self.fs.read_bytes(path)
                descriptor = parse_canonical_json(descriptor_bytes)
            except Exception:
                _reject("TERMINAL_MANIFEST_COVERAGE_MISMATCH")
            if not isinstance(descriptor, dict):
                _reject("TERMINAL_MANIFEST_COVERAGE_MISMATCH")
            digest = descriptor_content_sha256(descriptor)
            expected_path = f"descriptors/sha256/{digest[:2]}/{digest}.json"
            if path != expected_path:
                _reject("TERMINAL_MANIFEST_COVERAGE_MISMATCH")
            role = self.policy.role(descriptor.get("role", ""))
            if role.storage_class == "PAYLOAD_WITH_DESCRIPTOR":
                payload_path = descriptor.get("payload_relative_path")
                if not isinstance(payload_path, str) or payload_path not in objects:
                    _reject("TERMINAL_MANIFEST_COVERAGE_MISMATCH")
                payload = self.fs.read_bytes(payload_path)
                if sha256_hex(payload) != descriptor.get("payload_sha256") or len(payload) != descriptor.get("payload_size_bytes"):
                    _reject("TERMINAL_MANIFEST_COVERAGE_MISMATCH")
                referenced_objects.add(payload_path)
        if objects != referenced_objects:
            _reject("TERMINAL_MANIFEST_COVERAGE_MISMATCH")
        return tuple(paths)

    def seal(
        self,
        terminal_outcome: str,
        *,
        journal_relative_path: str = "journal/attempt.jsonl",
        manifest_relative_path: str = "manifests/terminal.sha256",
        global_stop_relative_path: str | None = None,
    ) -> SealResult:
        if terminal_outcome not in ("FINALIZED", "ABORTED_SAFE", "RESTORATION_BLOCKED"):
            _reject("TERMINAL_OUTCOME_INVALID")
        with self.fs.lock():
            self._ensure_unsealed()
            try:
                with Journal(
                    self.fs.root,
                    self.policy,
                    self.run_id,
                    self.attempt_id,
                    relative_path=journal_relative_path,
                    _safe_root=self.fs,
                ) as journal:
                    state = journal.reconstruct()
            except JournalError as error:
                _reject(error.code)
            if not state.terminal or state.state != terminal_outcome:
                _reject("ATTEMPT_FINALITY_INVALID")
            covered = self._coverage(terminal_outcome, journal_relative_path, global_stop_relative_path)
            rows = [(path, sha256_hex(self.fs.read_bytes(path))) for path in covered]
            manifest = b"".join(f"{digest}  {path}\n".encode("utf-8") for path, digest in rows)
            if manifest_relative_path in covered or not _safe_relative(manifest_relative_path, "manifests/"):
                _reject("TERMINAL_MANIFEST_INVALID")
            self.fs.write_atomic(manifest_relative_path, manifest)
        classification = "SEALED_PARTIAL_WITH_GLOBAL_STOP" if terminal_outcome == "RESTORATION_BLOCKED" else "COMPLETE"
        return SealResult(
            terminal_outcome, classification, manifest_relative_path,
            sha256_hex(manifest), len(manifest), covered,
        )


def _parse_manifest(data: bytes) -> tuple[tuple[str, str], ...]:
    if not data or not data.endswith(b"\n"):
        _reject("EXTERNAL_MANIFEST_INVALID")
    rows: list[tuple[str, str]] = []
    for line in data.splitlines(keepends=True):
        match = _MANIFEST_LINE.fullmatch(line)
        if match is None:
            _reject("EXTERNAL_MANIFEST_INVALID")
        digest = match.group(1).decode("ascii")
        path = match.group(2).decode("ascii")
        if not _safe_relative(path, "") or path.startswith("manifests/") or path.startswith("state/") or ".tmp" in path:
            _reject("EXTERNAL_MANIFEST_INVALID")
        rows.append((path, digest))
    if rows != sorted(rows, key=lambda row: row[0].encode("utf-8")) or len({path for path, _digest in rows}) != len(rows):
        _reject("EXTERNAL_MANIFEST_INVALID")
    return tuple(rows)


def revalidate_sealed_attempt(
    root: Path,
    policy: AuthenticatedPolicy,
    run_id: str,
    attempt_id: str,
    anchor: ExternalAnchor,
    *,
    global_stop_relative_path: str = "terminal/global-stop.json",
) -> SealVerification:
    if (
        anchor.recorded_by != "IMMUTABLE_RUN_INDEX_OUTSIDE_ATTEMPT_ROOT"
        or anchor.cited_by_result_aggregation is not True
        or not anchor.attempt_root_identifier
        or not _SHA256.fullmatch(anchor.terminal_manifest_sha256)
    ):
        _reject("EXTERNAL_SEAL_MISSING")
    with SafeRoot(root) as fs:
        try:
            manifest = fs.read_bytes(anchor.manifest_relative_path)
        except JournalError:
            _reject("EXTERNAL_SEAL_MISSING")
        if sha256_hex(manifest) != anchor.terminal_manifest_sha256:
            _reject("EXTERNAL_MANIFEST_HASH_MISMATCH")
        rows = _parse_manifest(manifest)
        for path, digest in rows:
            try:
                data = fs.read_bytes(path)
            except JournalError:
                _reject("EXTERNAL_MANIFEST_COVERAGE_MISMATCH")
            if sha256_hex(data) != digest:
                _reject("EXTERNAL_MANIFEST_COVERAGE_MISMATCH")
        try:
            with Journal(
                fs.root,
                policy,
                run_id,
                attempt_id,
                _safe_root=fs,
            ) as journal:
                state = journal.reconstruct()
        except JournalError as error:
            _reject(error.code)
        if not state.terminal:
            _reject("ATTEMPT_FINALITY_INVALID")
        with EvidenceStore(
            fs.root,
            policy,
            run_id,
            attempt_id,
            _safe_root=fs,
        ) as store:
            expected = store._coverage(
                state.state,
                "journal/attempt.jsonl",
                global_stop_relative_path if state.state == "RESTORATION_BLOCKED" else None,
            )
        if tuple(path for path, _digest in rows) != expected:
            _reject("EXTERNAL_MANIFEST_COVERAGE_MISMATCH")
        classification = "SEALED_PARTIAL_WITH_GLOBAL_STOP" if state.state == "RESTORATION_BLOCKED" else "COMPLETE"
        return SealVerification(state.state, classification, anchor.terminal_manifest_sha256, expected)

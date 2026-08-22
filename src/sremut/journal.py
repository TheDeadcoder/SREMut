"""Append-only, hash-chained journal and beneath-root filesystem substrate."""

from __future__ import annotations

from contextlib import contextmanager
import errno
from copy import deepcopy
from dataclasses import dataclass
import fcntl
import json
import os
from pathlib import Path
import re
import secrets
import stat
from types import MappingProxyType
from typing import Any, Iterator, Mapping, NoReturn

from sremut.canonical_json import canonical_json_bytes, canonical_json_line, parse_canonical_json, sha256_hex
from sremut.policy_runtime import AuthenticatedPolicy


GENESIS_SHA256 = "0" * 64
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RUN_ID = re.compile(r"^sremut-ms-(m01|m02|m03)-r0[1-3]-a0[1-2]-[0-9a-f]{12}$")
_ATTEMPT_ID = re.compile(r"^a0[1-2]$")
_UTC_TIME = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{9}Z$")
_BOOT_IDENTITY = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")


@dataclass(slots=True)
class JournalError(ValueError):
    code: str

    def __str__(self) -> str:
        return self.code


def _reject(code: str) -> NoReturn:
    raise JournalError(code) from None


def _relative_parts(relative: str) -> tuple[str, ...]:
    if not isinstance(relative, str) or not relative or relative.startswith("/") or "\\" in relative:
        _reject("EVIDENCE_PATH_INVALID")
    parts = tuple(relative.split("/"))
    if any(part in ("", ".", "..") for part in parts):
        _reject("EVIDENCE_PATH_INVALID")
    return parts


def _write_all(descriptor: int, data: bytes) -> None:
    offset = 0
    while offset < len(data):
        try:
            written = os.write(descriptor, data[offset:])
        except InterruptedError:
            continue
        if written <= 0:
            _reject("JOURNAL_DURABILITY_FAILURE")
        offset += written


class SafeRoot:
    """An explicit absolute directory root with no symlink component."""

    def __init__(self, root: Path):
        candidate = Path(root)
        if not candidate.is_absolute():
            _reject("EVIDENCE_ROOT_INVALID")
        current = Path(candidate.anchor)
        try:
            for component in candidate.parts[1:]:
                current = current / component
                info = current.lstat()
                if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                    _reject("EVIDENCE_SYMLINK_REFUSED")
            self._fd = os.open(candidate, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        except JournalError:
            raise
        except OSError:
            _reject("EVIDENCE_ROOT_INVALID")
        self.root = candidate

    def close(self) -> None:
        descriptor = getattr(self, "_fd", -1)
        if descriptor >= 0:
            os.close(descriptor)
            self._fd = -1

    def __enter__(self) -> SafeRoot:
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        self.close()

    @contextmanager
    def lock(self, *, blocking: bool = False) -> Iterator[None]:
        operation = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
        try:
            fcntl.flock(self._fd, operation)
        except BlockingIOError:
            _reject("JOURNAL_LOCK_UNAVAILABLE")
        try:
            yield
        finally:
            fcntl.flock(self._fd, fcntl.LOCK_UN)

    def _open_directory(self, parts: tuple[str, ...], *, create: bool) -> int:
        descriptor = os.dup(self._fd)
        try:
            for component in parts:
                try:
                    child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
                except FileNotFoundError:
                    if not create:
                        raise
                    os.mkdir(component, mode=0o700, dir_fd=descriptor)
                    os.fsync(descriptor)
                    child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = child
            return descriptor
        except OSError:
            os.close(descriptor)
            _reject("EVIDENCE_SYMLINK_REFUSED")

    def read_bytes_bounded(
        self,
        relative: str,
        *,
        maximum_bytes: int | None = None,
        exact_size: int | None = None,
    ) -> bytes:
        """Read one regular file after enforcing bounds on its opened descriptor."""

        if (
            maximum_bytes is not None
            and (
                not isinstance(maximum_bytes, int)
                or isinstance(maximum_bytes, bool)
                or maximum_bytes < 0
            )
        ):
            _reject("EVIDENCE_SIZE_INVALID")
        if (
            exact_size is not None
            and (
                not isinstance(exact_size, int)
                or isinstance(exact_size, bool)
                or exact_size < 0
            )
        ):
            _reject("EVIDENCE_SIZE_INVALID")
        parts = _relative_parts(relative)
        parent = self._open_directory(parts[:-1], create=False)
        try:
            descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
            try:
                info = os.fstat(descriptor)
                if not stat.S_ISREG(info.st_mode):
                    _reject("EVIDENCE_TARGET_INVALID")
                if maximum_bytes is not None and info.st_size > maximum_bytes:
                    _reject("EVIDENCE_SIZE_INVALID")
                if exact_size is not None and info.st_size != exact_size:
                    _reject("EVIDENCE_SIZE_INVALID")
                chunks: list[bytes] = []
                total = 0
                limits = tuple(
                    value for value in (maximum_bytes, exact_size) if value is not None
                )
                read_limit = min(limits) if limits else None
                while True:
                    count = 1024 * 1024
                    if read_limit is not None:
                        count = min(count, read_limit - total + 1)
                    try:
                        chunk = os.read(descriptor, count)
                    except InterruptedError:
                        continue
                    if not chunk:
                        break
                    total += len(chunk)
                    if maximum_bytes is not None and total > maximum_bytes:
                        _reject("EVIDENCE_SIZE_INVALID")
                    if exact_size is not None and total > exact_size:
                        _reject("EVIDENCE_SIZE_INVALID")
                    chunks.append(chunk)
                if exact_size is not None and total != exact_size:
                    _reject("EVIDENCE_SIZE_INVALID")
                return b"".join(chunks)
            finally:
                os.close(descriptor)
        except JournalError:
            raise
        except OSError as error:
            if error.errno == errno.ELOOP:
                _reject("EVIDENCE_SYMLINK_REFUSED")
            _reject("EVIDENCE_READ_FAILED")
        finally:
            os.close(parent)

    def read_bytes(self, relative: str) -> bytes:
        return self.read_bytes_bounded(relative)

    def exists(self, relative: str) -> bool:
        parts = _relative_parts(relative)
        try:
            parent = self._open_directory(parts[:-1], create=False)
        except JournalError:
            return False
        try:
            try:
                os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
                return True
            except FileNotFoundError:
                return False
        finally:
            os.close(parent)

    def write_atomic(self, relative: str, data: bytes, *, mode: int = 0o600) -> None:
        parts = _relative_parts(relative)
        parent = self._open_directory(parts[:-1], create=True)
        temporary = f".{parts[-1]}.sremut-{secrets.token_hex(8)}.tmp"
        descriptor = -1
        try:
            try:
                os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                _reject("EVIDENCE_ALREADY_EXISTS")
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                mode,
                dir_fd=parent,
            )
            _write_all(descriptor, data)
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = -1
            try:
                os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                _reject("EVIDENCE_ALREADY_EXISTS")
            try:
                os.link(
                    temporary,
                    parts[-1],
                    src_dir_fd=parent,
                    dst_dir_fd=parent,
                    follow_symlinks=False,
                )
            except FileExistsError:
                _reject("EVIDENCE_ALREADY_EXISTS")
            os.unlink(temporary, dir_fd=parent)
            os.fsync(parent)
        except JournalError:
            raise
        except OSError:
            _reject("EVIDENCE_WRITE_FAILED")
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                os.unlink(temporary, dir_fd=parent)
            except FileNotFoundError:
                pass
            os.close(parent)

    def append_durable(self, relative: str, data: bytes) -> None:
        parts = _relative_parts(relative)
        parent = self._open_directory(parts[:-1], create=True)
        descriptor = -1
        try:
            descriptor = os.open(
                parts[-1],
                os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW,
                0o600,
                dir_fd=parent,
            )
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                _reject("JOURNAL_TARGET_INVALID")
            _write_all(descriptor, data)
            os.fsync(descriptor)
            os.fsync(parent)
        except JournalError:
            raise
        except OSError:
            _reject("JOURNAL_DURABILITY_FAILURE")
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            os.close(parent)

    def list_regular_files(self, prefixes: tuple[str, ...]) -> tuple[str, ...]:
        result: list[str] = []
        for prefix in prefixes:
            parts = _relative_parts(prefix)
            try:
                base = self._open_directory(parts, create=False)
            except JournalError:
                continue
            try:
                self._walk_directory(base, "/".join(parts), result)
            finally:
                os.close(base)
        return tuple(sorted(result, key=lambda item: item.encode("utf-8")))

    def list_all_regular_files(self) -> tuple[str, ...]:
        """Enumerate every regular file beneath the already-open root."""

        descriptor = os.dup(self._fd)
        result: list[str] = []
        try:
            self._walk_directory(descriptor, "", result)
        finally:
            os.close(descriptor)
        return tuple(sorted(result, key=lambda item: item.encode("utf-8")))

    def _walk_directory(self, descriptor: int, prefix: str, result: list[str]) -> None:
        try:
            names = sorted(os.listdir(descriptor), key=lambda item: os.fsencode(item))
        except OSError:
            _reject("EVIDENCE_READ_FAILED")
        for name in names:
            try:
                info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            except OSError:
                _reject("EVIDENCE_READ_FAILED")
            relative = f"{prefix}/{name}" if prefix else name
            if stat.S_ISLNK(info.st_mode):
                _reject("EVIDENCE_SYMLINK_REFUSED")
            if stat.S_ISREG(info.st_mode):
                result.append(relative)
            elif stat.S_ISDIR(info.st_mode):
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
                try:
                    self._walk_directory(child, relative, result)
                finally:
                    os.close(child)
            else:
                _reject("EVIDENCE_TARGET_INVALID")


@dataclass(frozen=True, slots=True)
class JournalState:
    records: tuple[Mapping[str, Any], ...]
    state: str
    sequence_number: int
    terminal: bool
    evaluation: str | None
    operation: Mapping[str, Any] | None


def record_sha256(record: Mapping[str, Any]) -> str:
    material = deepcopy(dict(record))
    material.pop("canonical_current_entry_sha256", None)
    return sha256_hex(canonical_json_bytes(material))


class Journal:
    """Authoritative attempt journal; mutable pointers are never consulted."""

    def __init__(
        self,
        root: Path,
        policy: AuthenticatedPolicy,
        run_id: str,
        attempt_id: str,
        *,
        relative_path: str = "journal/attempt.jsonl",
        _safe_root: SafeRoot | None = None,
    ):
        if _safe_root is not None and _safe_root.root != Path(root):
            _reject("EVIDENCE_ROOT_INVALID")
        self.fs = _safe_root if _safe_root is not None else SafeRoot(root)
        self._owns_fs = _safe_root is None
        self.policy = policy
        self.run_id = run_id
        self.attempt_id = attempt_id
        self.relative_path = relative_path
        _relative_parts(relative_path)
        state_machine = policy.policy["verified_attempt_state_machine"]
        self._states = frozenset(state_machine["states"])
        self._legal = frozenset(state_machine["legal_transitions"])
        self._terminal = frozenset(state_machine["terminal_states"])
        full = policy.policy["full_admissibility_validation"]
        self._evaluations = frozenset(full["adjudication_predicate_raw_role_context_deadline_matrix"])

    def close(self) -> None:
        if self._owns_fs:
            self.fs.close()

    def __enter__(self) -> Journal:
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        self.close()

    def _bytes(self) -> bytes:
        if not self.fs.exists(self.relative_path):
            return b""
        return self.fs.read_bytes(self.relative_path)

    def reconstruct(self, *, mutable_pointer_relative_path: str | None = None) -> JournalState:
        # The pointer argument is intentionally ignored; it is never authority.
        if mutable_pointer_relative_path is not None:
            _relative_parts(mutable_pointer_relative_path)
        return self.reconstruct_bytes(self._bytes())

    @classmethod
    def reconstruct_retained(
        cls,
        policy: AuthenticatedPolicy,
        run_id: str,
        attempt_id: str,
        data: bytes,
    ) -> JournalState:
        """Validate retained bytes without acquiring or reopening a filesystem root."""

        journal = object.__new__(cls)
        journal.policy = policy
        journal.run_id = run_id
        journal.attempt_id = attempt_id
        state_machine = policy.policy["verified_attempt_state_machine"]
        journal._states = frozenset(state_machine["states"])
        journal._legal = frozenset(state_machine["legal_transitions"])
        journal._terminal = frozenset(state_machine["terminal_states"])
        full = policy.policy["full_admissibility_validation"]
        journal._evaluations = frozenset(
            full["adjudication_predicate_raw_role_context_deadline_matrix"]
        )
        return journal.reconstruct_bytes(data)

    def reconstruct_bytes(self, data: bytes) -> JournalState:
        """Reconstruct authority from exact retained journal bytes without rereading."""

        if not isinstance(data, bytes):
            _reject("JOURNAL_CANONICALIZATION_INVALID")
        if data and not data.endswith(b"\n"):
            _reject("JOURNAL_CANONICALIZATION_INVALID")
        records: list[Mapping[str, Any]] = []
        previous = GENESIS_SHA256
        state = "CREATED"
        evaluation: str | None = None
        operation: Mapping[str, Any] | None = None
        terminal_seen = False
        for sequence, line in enumerate(data.splitlines(keepends=True)):
            try:
                record = parse_canonical_json(line, line=True)
            except Exception:
                _reject("JOURNAL_CANONICALIZATION_INVALID")
            if not isinstance(record, dict):
                _reject("JOURNAL_CANONICALIZATION_INVALID")
            self._validate_record_shape(record)
            if record["sequence_number"] != sequence:
                _reject("JOURNAL_CHAIN_INVALID")
            if record["previous_entry_sha256"] != previous:
                _reject("JOURNAL_CHAIN_INVALID")
            current = record_sha256(record)
            if record["canonical_current_entry_sha256"] != current:
                _reject("JOURNAL_CHAIN_INVALID")
            if record["run_id"] != self.run_id or record["attempt_id"] != self.attempt_id:
                _reject("JOURNAL_CONTEXT_MISMATCH")
            if terminal_seen:
                _reject("POST_TERMINAL_OPERATION")
            if record["journal_record_type"] == "STATE_TRANSITION":
                transition = record["transition"]
                if transition.startswith("STATE_VERIFIED:"):
                    proposed = transition.split(":", 1)[1]
                    if proposed not in self._states:
                        _reject("JOURNAL_STATE_DERIVATION_FAILED")
                    state = proposed
                elif transition in self._legal:
                    source, target = transition.split("->", 1)
                    if source != state:
                        _reject("JOURNAL_STATE_DERIVATION_FAILED")
                    state = target
                elif transition.startswith("EVALUATION_AUTHORIZED:"):
                    candidate = transition.split(":", 1)[1]
                    if candidate not in self._evaluations:
                        _reject("JOURNAL_STATE_DERIVATION_FAILED")
                    evaluation = candidate
                elif transition.startswith("OPERATION_AUTHORIZED:"):
                    encoded = transition.split(":", 1)[1]
                    try:
                        raw = bytes.fromhex(encoded)
                        parsed = parse_canonical_json(raw)
                    except Exception:
                        _reject("JOURNAL_STATE_DERIVATION_FAILED")
                    if canonical_json_bytes(parsed).hex() != encoded or not isinstance(parsed, dict):
                        _reject("JOURNAL_STATE_DERIVATION_FAILED")
                    operation = parsed
                else:
                    _reject("JOURNAL_STATE_DERIVATION_FAILED")
            previous = current
            terminal_seen = state in self._terminal
            records.append(MappingProxyType(record))
        return JournalState(tuple(records), state, len(records) - 1, terminal_seen, evaluation, MappingProxyType(operation) if operation is not None else None)

    def _validate_record_shape(self, record: dict[str, Any]) -> None:
        common = {
            "document_type", "schema_version", "journal_record_type", "sequence_number",
            "previous_entry_sha256", "canonical_current_entry_sha256", "run_id", "attempt_id",
            "monotonic_ns", "boot_identity",
        }
        if record.get("document_type") != "JOURNAL_RECORD_V1" or record.get("schema_version") != 1:
            _reject("JOURNAL_CANONICALIZATION_INVALID")
        if not isinstance(record.get("sequence_number"), int) or isinstance(record.get("sequence_number"), bool) or record["sequence_number"] < 0:
            _reject("JOURNAL_CANONICALIZATION_INVALID")
        if not isinstance(record.get("run_id"), str) or _RUN_ID.fullmatch(record["run_id"]) is None:
            _reject("JOURNAL_CANONICALIZATION_INVALID")
        if not isinstance(record.get("attempt_id"), str) or _ATTEMPT_ID.fullmatch(record["attempt_id"]) is None:
            _reject("JOURNAL_CANONICALIZATION_INVALID")
        if (
            not isinstance(record.get("monotonic_ns"), int)
            or isinstance(record.get("monotonic_ns"), bool)
            or record["monotonic_ns"] < 0
            or not isinstance(record.get("boot_identity"), str)
            or _BOOT_IDENTITY.fullmatch(record["boot_identity"]) is None
        ):
            _reject("JOURNAL_CANONICALIZATION_INVALID")
        if not all(isinstance(record.get(name), str) and _SHA256.fullmatch(record[name]) for name in ("previous_entry_sha256", "canonical_current_entry_sha256")):
            _reject("JOURNAL_CANONICALIZATION_INVALID")
        if record.get("journal_record_type") == "STATE_TRANSITION":
            required = common | {"transition", "referenced_intent_receipt_and_adjudication_sha256", "utc_time"}
            allowed = required | {"referenced_descriptor_sha256", "referenced_payload_sha256"}
            if set(record) - allowed or not required.issubset(record):
                _reject("JOURNAL_CANONICALIZATION_INVALID")
            if (
                not isinstance(record.get("transition"), str)
                or not 1 <= len(record["transition"]) <= 16384
                or not isinstance(record.get("utc_time"), str)
                or _UTC_TIME.fullmatch(record["utc_time"]) is None
            ):
                _reject("JOURNAL_CANONICALIZATION_INVALID")
            for field in ("referenced_intent_receipt_and_adjudication_sha256", "referenced_descriptor_sha256", "referenced_payload_sha256"):
                values = record.get(field, [])
                if not isinstance(values, list) or len(values) > 4096 or any(not isinstance(item, str) or not _SHA256.fullmatch(item) for item in values):
                    _reject("JOURNAL_CANONICALIZATION_INVALID")
        elif record.get("journal_record_type") == "CAPTURE_REJECTED":
            required = common | {"intended_role", "rejection_code", "detector_id", "source_kind", "observed_size", "created_utc"}
            if set(record) != required:
                _reject("CAPTURE_REJECTION_PRIVACY_INVALID")
            if (
                not isinstance(record.get("rejection_code"), str)
                or re.fullmatch(r"CAPTURE_REJECTED_[A-Z_]+", record["rejection_code"]) is None
                or not isinstance(record.get("detector_id"), str)
                or re.fullmatch(r"SENSITIVE_[A-Z_]+", record["detector_id"]) is None
                or not isinstance(record.get("observed_size"), int)
                or isinstance(record.get("observed_size"), bool)
                or record["observed_size"] < 0
                or not isinstance(record.get("created_utc"), str)
                or _UTC_TIME.fullmatch(record["created_utc"]) is None
            ):
                _reject("CAPTURE_REJECTION_PRIVACY_INVALID")
            try:
                role = self.policy.role(record.get("intended_role", ""))
            except Exception:
                _reject("CAPTURE_REJECTION_PRIVACY_INVALID")
            if record.get("source_kind") != role.source_kind:
                _reject("CAPTURE_REJECTION_PRIVACY_INVALID")
        else:
            _reject("JOURNAL_CANONICALIZATION_INVALID")

    def _append(self, record: dict[str, Any]) -> Mapping[str, Any]:
        with self.fs.lock():
            state = self.reconstruct()
            if state.terminal:
                _reject("POST_TERMINAL_OPERATION")
            record["sequence_number"] = state.sequence_number + 1
            record["previous_entry_sha256"] = (
                GENESIS_SHA256 if not state.records else state.records[-1]["canonical_current_entry_sha256"]
            )
            record["canonical_current_entry_sha256"] = GENESIS_SHA256
            record["canonical_current_entry_sha256"] = record_sha256(record)
            self._validate_record_shape(record)
            self.fs.append_durable(self.relative_path, canonical_json_line(record))
            rebuilt = self.reconstruct()
            return rebuilt.records[-1]

    def append_state_transition(
        self,
        transition: str,
        *,
        utc_time: str,
        monotonic_ns: int,
        boot_identity: str,
        descriptor_sha256: tuple[str, ...] = (),
        payload_sha256: tuple[str, ...] = (),
        intent_receipt_adjudication_sha256: tuple[str, ...] = (),
    ) -> Mapping[str, Any]:
        if transition.startswith("STATE_VERIFIED:"):
            _reject("JOURNAL_STATE_DERIVATION_FAILED")
        record = {
            "document_type": "JOURNAL_RECORD_V1",
            "schema_version": 1,
            "journal_record_type": "STATE_TRANSITION",
            "sequence_number": 0,
            "previous_entry_sha256": GENESIS_SHA256,
            "canonical_current_entry_sha256": GENESIS_SHA256,
            "run_id": self.run_id,
            "attempt_id": self.attempt_id,
            "transition": transition,
            "referenced_intent_receipt_and_adjudication_sha256": list(intent_receipt_adjudication_sha256),
            "referenced_descriptor_sha256": list(descriptor_sha256),
            "referenced_payload_sha256": list(payload_sha256),
            "utc_time": utc_time,
            "monotonic_ns": monotonic_ns,
            "boot_identity": boot_identity,
        }
        return self._append(record)

    def append_capture_rejected(
        self,
        *,
        intended_role: str,
        rejection_code: str,
        detector_id: str,
        source_kind: str,
        observed_size: int,
        created_utc: str,
        monotonic_ns: int,
        boot_identity: str,
    ) -> Mapping[str, Any]:
        record = {
            "document_type": "JOURNAL_RECORD_V1",
            "schema_version": 1,
            "journal_record_type": "CAPTURE_REJECTED",
            "sequence_number": 0,
            "previous_entry_sha256": GENESIS_SHA256,
            "canonical_current_entry_sha256": GENESIS_SHA256,
            "run_id": self.run_id,
            "attempt_id": self.attempt_id,
            "intended_role": intended_role,
            "rejection_code": rejection_code,
            "detector_id": detector_id,
            "source_kind": source_kind,
            "observed_size": observed_size,
            "created_utc": created_utc,
            "monotonic_ns": monotonic_ns,
            "boot_identity": boot_identity,
        }
        return self._append(record)

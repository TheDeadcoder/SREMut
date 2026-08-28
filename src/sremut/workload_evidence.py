"""Non-executing, frozen-v1.1 workload evidence capture and validation."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import math
import re
import struct
from types import MappingProxyType
from typing import Any, Mapping, NoReturn, Sequence

from sremut.canonical_json import canonical_json_bytes, parse_canonical_json
from sremut.evidence import EvidenceRef, validate_evidence_ref
from sremut.policy_runtime import AuthenticatedPolicy, POLICY_ID, POLICY_MANIFEST_SHA256


EXECUTION_PROFILE_TAG_OBJECT = "7c6493eb7dce68370fd0d5be572edd968654a1d6"
SREGYM_COMMIT = "ba07faf1a322f9b6d4a279643bb796aa2f36f64b"
WORKLOAD_PARSER_MODULE = "sregym/conductor/oracles/workload.py"
WORKLOAD_PARSER_SOURCE_SHA256 = "d1bb10c9230ab1b2bba541df16552013323b0160a928d9854fad41210a356468"
WORKLOAD_PARSE_ALGORITHM = "SREMUT_PINNED_WORKLOAD_ENTRY_JSONL_V1"
WORKLOAD_SOURCE = "StreamWorkloadManager.log_history"
WORKLOAD_WINDOWS = MappingProxyType(
    {
        "INITIAL_MUTANT_CHALLENGE": 1,
        "POST_REPLACEMENT_PERSISTENCE": 2,
        "RESTORATION_POSITIVE_CONTROL": 3,
    }
)
_RUN = re.compile(r"^sremut-ms-(m01|m02|m03)-r0([1-3])-a0([1-2])-[0-9a-f]{12}$")
_ATTEMPT = re.compile(r"^a0[1-2]$")
_TIME = re.compile(r"^[0-9a-f]{16}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MAXIMUM_RAW_BYTES = 262144


@dataclass(slots=True)
class WorkloadEvidenceError(ValueError):
    """Stable workload validation failure without retained attacker content."""

    code: str

    def __str__(self) -> str:
        return self.code


def _reject(code: str) -> NoReturn:
    raise WorkloadEvidenceError(code) from None


def _freeze(value: Any) -> Any:
    if type(value) is dict:
        return MappingProxyType({key: _freeze(child) for key, child in value.items()})
    if type(value) is list:
        return tuple(_freeze(child) for child in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [_thaw(child) for child in value]
    return value


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _validate_policy(policy: AuthenticatedPolicy) -> None:
    try:
        protocol = policy.policy["workload_evidence_protocol"]
        stream = policy.policy["workload_stream_identity_protocol"]
    except Exception:
        _reject("POLICY_BINDING_MISSING")
    if (
        policy.manifest_sha256 != POLICY_MANIFEST_SHA256
        or policy.policy.get("policy_id") != POLICY_ID
        or policy.policy.get("semantic_version") != "1.1"
        or protocol.get("authoritative_raw_representation")
        != "CANONICAL_WORKLOAD_ENTRY_JSONL_V1_1"
        or protocol.get("joined_entry_log_compatibility_mode") is not False
        or stream.get("evidence_policy_id") != POLICY_ID
    ):
        _reject("POLICY_SUPERSEDED")


def binary64_time_identity(value: Any) -> str:
    if type(value) is not float or not math.isfinite(value):
        _reject("WORKLOAD_TIMESTAMP_ORDER_INVALID")
    return struct.pack(">d", value).hex()


def recompute_stream_identity(
    run_id: Any,
    attempt_id: Any,
    *,
    policy_id: str = POLICY_ID,
    source: str = WORKLOAD_SOURCE,
    manager_instance_ordinal: Any = 1,
) -> str:
    if (
        type(run_id) is not str
        or _RUN.fullmatch(run_id) is None
        or type(attempt_id) is not str
        or _ATTEMPT.fullmatch(attempt_id) is None
        or policy_id != POLICY_ID
        or source != WORKLOAD_SOURCE
        or type(manager_instance_ordinal) is not int
        or manager_instance_ordinal != 1
    ):
        _reject("WORKLOAD_STREAM_IDENTITY_MISMATCH")
    return _sha256(
        canonical_json_bytes(
            {
                "schema_version": 1,
                "execution_profile_tag_object": EXECUTION_PROFILE_TAG_OBJECT,
                "evidence_policy_id": POLICY_ID,
                "run_id": run_id,
                "attempt_id": attempt_id,
                "source": WORKLOAD_SOURCE,
                "manager_instance_ordinal": 1,
            }
        )
    )


@dataclass(frozen=True, slots=True)
class WorkloadHistoryEntry:
    time: float
    number: int
    log: str
    ok: bool

    def __post_init__(self) -> None:
        binary64_time_identity(self.time)
        if type(self.number) is not int or self.number < 0:
            _reject("WORKLOAD_PARSE_RECOMPUTATION_MISMATCH")
        if type(self.log) is not str or type(self.ok) is not bool:
            _reject("WORKLOAD_PARSE_RECOMPUTATION_MISMATCH")
        try:
            self.log.encode("utf-8", errors="strict")
        except UnicodeError:
            _reject("WORKLOAD_PARSE_RECOMPUTATION_MISMATCH")


@dataclass(frozen=True, slots=True)
class WorkloadRecord:
    index: int
    time_binary64_hex: str
    number: int
    log: str = field(repr=False)
    ok: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "time_binary64_hex": self.time_binary64_hex,
            "number": self.number,
            "log": self.log,
            "ok": self.ok,
        }


@dataclass(frozen=True, slots=True)
class WorkloadEvidenceCandidate:
    role: str
    payload: bytes | None = field(repr=False)
    _metadata: Mapping[str, Any] = field(repr=False)

    def publication_metadata(self) -> dict[str, Any]:
        return _thaw(self._metadata)


@dataclass(frozen=True, slots=True)
class WorkloadCaptureStamp:
    created_utc: str
    monotonic_ns: int
    boot_identity: str

    def as_dict(self) -> dict[str, Any]:
        if (
            type(self.created_utc) is not str
            or not self.created_utc
            or type(self.monotonic_ns) is not int
            or self.monotonic_ns < 0
            or type(self.boot_identity) is not str
            or not self.boot_identity
        ):
            _reject("WORKLOAD_PARSE_RECOMPUTATION_MISMATCH")
        return {
            "created_utc": self.created_utc,
            "monotonic_ns": self.monotonic_ns,
            "boot_identity": self.boot_identity,
        }


def _record_from_entry(index: int, entry: WorkloadHistoryEntry) -> WorkloadRecord:
    if type(entry) is not WorkloadHistoryEntry:
        _reject("WORKLOAD_PARSE_RECOMPUTATION_MISMATCH")
    return WorkloadRecord(
        index=index,
        time_binary64_hex=binary64_time_identity(entry.time),
        number=entry.number,
        log=entry.log,
        ok=entry.ok,
    )


def serialize_workload_history(history: Any) -> bytes:
    if type(history) is not tuple:
        _reject("WORKLOAD_PARSE_RECOMPUTATION_MISMATCH")
    records = tuple(_record_from_entry(index, entry) for index, entry in enumerate(history))
    payload = b"".join(canonical_json_bytes(record.as_dict()) + b"\n" for record in records)
    if len(payload) > _MAXIMUM_RAW_BYTES:
        _reject("WORKLOAD_PARSE_RECOMPUTATION_MISMATCH")
    return payload


def parse_workload_jsonl(data: Any) -> tuple[WorkloadRecord, ...]:
    if type(data) is not bytes or len(data) > _MAXIMUM_RAW_BYTES:
        _reject("WORKLOAD_PARSE_RECOMPUTATION_MISMATCH")
    if data and not data.endswith(b"\n"):
        _reject("WORKLOAD_PARSE_RECOMPUTATION_MISMATCH")
    records: list[WorkloadRecord] = []
    for line in data.splitlines(keepends=True):
        if line == b"\n" or not line.endswith(b"\n"):
            _reject("WORKLOAD_PARSE_RECOMPUTATION_MISMATCH")
        try:
            value = parse_canonical_json(line, line=True)
        except Exception:
            _reject("WORKLOAD_PARSE_RECOMPUTATION_MISMATCH")
        if type(value) is not dict or set(value) != {
            "index", "time_binary64_hex", "number", "log", "ok"
        }:
            _reject("WORKLOAD_PARSE_RECOMPUTATION_MISMATCH")
        if (
            type(value["index"]) is not int
            or value["index"] < 0
            or type(value["number"]) is not int
            or value["number"] < 0
            or type(value["log"]) is not str
            or type(value["ok"]) is not bool
            or type(value["time_binary64_hex"]) is not str
            or _TIME.fullmatch(value["time_binary64_hex"]) is None
        ):
            _reject("WORKLOAD_PARSE_RECOMPUTATION_MISMATCH")
        try:
            time_value = struct.unpack(">d", bytes.fromhex(value["time_binary64_hex"]))[0]
        except Exception:
            _reject("WORKLOAD_PARSE_RECOMPUTATION_MISMATCH")
        if not math.isfinite(time_value):
            _reject("WORKLOAD_TIMESTAMP_ORDER_INVALID")
        records.append(WorkloadRecord(**value))
    if [record.index for record in records] != list(range(len(records))):
        _reject("WORKLOAD_ENTRY_ORDER_INVALID")
    values = [struct.unpack(">d", bytes.fromhex(record.time_binary64_hex))[0] for record in records]
    if any(later < earlier for earlier, later in zip(values, values[1:])):
        _reject("WORKLOAD_TIMESTAMP_ORDER_INVALID")
    return tuple(records)


def parse_pinned_wrk2_round(log: Any) -> tuple[int, bool]:
    """Recompute the two fields frozen from ``Wrk2WorkloadManager._parse_log``."""

    if type(log) is not str:
        _reject("WORKLOAD_PARSE_RECOMPUTATION_MISMATCH")
    lines = log.split("\n")
    request_counts: list[int] = []
    for index, line in enumerate(lines[:-1]):
        if "-" * 35 not in line or "requests in" not in lines[index + 1]:
            continue
        parts = lines[index + 1].split(" ")
        nonempty = [(position, token) for position, token in enumerate(parts) if token]
        if not nonempty:
            _reject("WORKLOAD_PARSE_RECOMPUTATION_MISMATCH")
        position, token = nonempty[0]
        if position + 1 >= len(parts) or parts[position + 1] != "requests":
            _reject("WORKLOAD_PARSE_RECOMPUTATION_MISMATCH")
        try:
            request_counts.append(int(token))
        except ValueError:
            _reject("WORKLOAD_PARSE_RECOMPUTATION_MISMATCH")
    complete_end = any(
        "Requests/sec:" in lines[index] and "Transfer/sec:" in lines[index + 1]
        for index in range(len(lines) - 1)
    )
    if len(request_counts) != 1 or request_counts[0] < 0 or not complete_end:
        _reject("WORKLOAD_PARSE_RECOMPUTATION_MISMATCH")
    return request_counts[0], not any("Non-2xx or 3xx responses" in line for line in lines)


def validate_record_rounds(records: Sequence[WorkloadRecord]) -> None:
    for record in records:
        number, ok = parse_pinned_wrk2_round(record.log)
        if number != record.number or ok is not record.ok:
            _reject("WORKLOAD_PARSE_RECOMPUTATION_MISMATCH")


def parse_historical_workload_log(data: Any) -> tuple[int, tuple[int, ...], int]:
    """Parser-only validation for pre-v1.1 baseline wrk2 log files."""

    if type(data) is not bytes:
        _reject("WORKLOAD_PARSE_RECOMPUTATION_MISMATCH")
    try:
        lines = data.decode("utf-8", errors="strict").splitlines()
    except UnicodeError:
        _reject("WORKLOAD_PARSE_RECOMPUTATION_MISMATCH")
    contents = [line.split(" ", 1)[1] if " " in line else "" for line in lines]
    requests: list[int] = []
    failures = 0
    for index, content in enumerate(contents[:-1]):
        if "-" * 35 not in content or "requests in" not in contents[index + 1]:
            continue
        tokens = contents[index + 1].split()
        if len(tokens) < 2 or tokens[1] != "requests":
            _reject("WORKLOAD_PARSE_RECOMPUTATION_MISMATCH")
        try:
            count = int(tokens[0])
        except ValueError:
            _reject("WORKLOAD_PARSE_RECOMPUTATION_MISMATCH")
        tail = contents[index + 2 : index + 8]
        if not any("Requests/sec:" in row for row in tail) or not any("Transfer/sec:" in row for row in tail):
            _reject("WORKLOAD_PARSE_RECOMPUTATION_MISMATCH")
        requests.append(count)
        if any("Non-2xx or 3xx responses" in row for row in contents[max(0, index - 80) : index + 8]):
            failures += 1
    return len(requests), tuple(requests), failures


def parser_identity() -> dict[str, Any]:
    return {
        "sregym_commit": SREGYM_COMMIT,
        "module_path": WORKLOAD_PARSER_MODULE,
        "source_sha256": WORKLOAD_PARSER_SOURCE_SHA256,
        "python_runtime": "3.12.3",
        "algorithm": WORKLOAD_PARSE_ALGORITHM,
        "version": 1,
    }


def _reference_dict(reference: EvidenceRef | Mapping[str, Any]) -> dict[str, Any]:
    return reference.as_dict() if isinstance(reference, EvidenceRef) else _thaw(reference)


def _window_core(
    phase: Any,
    ordinal: Any,
    run_id: Any,
    attempt_id: Any,
    mutant_id: Any,
    repetition: Any,
    stream_identity: str,
) -> dict[str, Any]:
    match = _RUN.fullmatch(run_id) if type(run_id) is str else None
    expected_mutant = f"MS-M{match.group(1)[1:]}" if match is not None else None
    expected_repetition = int(match.group(2)) if match is not None else None
    expected_attempt = f"a0{match.group(3)}" if match is not None else None
    if (
        type(phase) is not str
        or WORKLOAD_WINDOWS.get(phase) != ordinal
        or type(ordinal) is not int
        or type(attempt_id) is not str
        or _ATTEMPT.fullmatch(attempt_id) is None
        or attempt_id != expected_attempt
        or expected_mutant != mutant_id
        or type(repetition) is not int
        or repetition != expected_repetition
        or stream_identity != recompute_stream_identity(run_id, attempt_id)
    ):
        _reject("WORKLOAD_WINDOW_MISMATCH")
    return {
        "phase": phase,
        "ordinal": ordinal,
        "run_id": run_id,
        "attempt_id": attempt_id,
        "mutant_id": mutant_id,
        "repetition": repetition,
        "stream_identity": stream_identity,
    }


@dataclass(frozen=True, slots=True)
class WorkloadEvidencePlan:
    _policy: AuthenticatedPolicy = field(repr=False, compare=False)
    prefix_payload: bytes = field(repr=False)
    full_payload: bytes = field(repr=False)
    prefix_records: tuple[WorkloadRecord, ...] = field(repr=False)
    full_records: tuple[WorkloadRecord, ...] = field(repr=False)
    window: Mapping[str, Any]
    pod_projection_reference: Mapping[str, Any] = field(repr=False)
    pod_name: str
    pod_uid: str
    container_restart_count: int

    @property
    def fresh_records(self) -> tuple[WorkloadRecord, ...]:
        return self.full_records[len(self.prefix_records) :]

    @property
    def fresh_request_count(self) -> int:
        return sum(record.number for record in self.fresh_records)

    @property
    def failure_marker_count(self) -> int:
        return sum(1 for record in self.fresh_records if not record.ok)

    @property
    def healthy(self) -> bool:
        return self.fresh_request_count >= 50 and self.failure_marker_count == 0

    def _common(self, stamp: WorkloadCaptureStamp) -> dict[str, Any]:
        result = {
            "run_id": self.window["run_id"],
            "attempt_id": self.window["attempt_id"],
            "workload_window": _thaw(self.window),
            "stream_identity": self.window["stream_identity"],
        }
        result.update(stamp.as_dict())
        return result

    def prefix_log_candidate(self, stamp: WorkloadCaptureStamp) -> WorkloadEvidenceCandidate:
        metadata = self._common(stamp)
        metadata.update(
            {
                "complete_entry_count": len(self.prefix_records),
                "entry_indexes": [record.index for record in self.prefix_records],
                "entry_time_ieee754_binary64_hex": [record.time_binary64_hex for record in self.prefix_records],
            }
        )
        return WorkloadEvidenceCandidate("workload_log_bytes", self.prefix_payload, _freeze(metadata))

    def full_log_candidate(self, stamp: WorkloadCaptureStamp) -> WorkloadEvidenceCandidate:
        metadata = self._common(stamp)
        metadata.update(
            {
                "complete_entry_count": len(self.full_records),
                "entry_indexes": [record.index for record in self.full_records],
                "entry_time_ieee754_binary64_hex": [record.time_binary64_hex for record in self.full_records],
            }
        )
        return WorkloadEvidenceCandidate("workload_log_bytes", self.full_payload, _freeze(metadata))

    def boundary_candidate(
        self,
        prefix_reference: EvidenceRef | Mapping[str, Any],
        stamp: WorkloadCaptureStamp,
    ) -> WorkloadEvidenceCandidate:
        reference = validate_evidence_ref(
            self._policy,
            prefix_reference,
            expected_run_id=self.window["run_id"],
            expected_attempt_id=self.window["attempt_id"],
        )
        if reference.role != "workload_log_bytes" or reference.payload_sha256 != _sha256(self.prefix_payload):
            _reject("WORKLOAD_PARSE_REFERENCE_MISMATCH")
        metadata = self._common(stamp)
        metadata.update(
            {
                "workload_pod_projection_reference": _thaw(self.pod_projection_reference),
                "pod_name": self.pod_name,
                "pod_uid": self.pod_uid,
                "container_restart_count": self.container_restart_count,
                "raw_log_reference": reference.as_dict(),
                "raw_log_byte_length": len(self.prefix_payload),
                "raw_log_sha256": _sha256(self.prefix_payload),
                "complete_entry_count": len(self.prefix_records),
                "request_count": sum(record.number for record in self.prefix_records),
                "entry_time_ieee754_binary64_hex": [record.time_binary64_hex for record in self.prefix_records],
            }
        )
        return WorkloadEvidenceCandidate("workload_boundary", None, _freeze(metadata))

    def parse_result_candidate(
        self,
        boundary_reference: EvidenceRef | Mapping[str, Any],
        raw_log_reference: EvidenceRef | Mapping[str, Any],
        stamp: WorkloadCaptureStamp,
    ) -> WorkloadEvidenceCandidate:
        boundary = validate_evidence_ref(
            self._policy, boundary_reference,
            expected_run_id=self.window["run_id"], expected_attempt_id=self.window["attempt_id"],
        )
        raw = validate_evidence_ref(
            self._policy, raw_log_reference,
            expected_run_id=self.window["run_id"], expected_attempt_id=self.window["attempt_id"],
        )
        if boundary.role != "workload_boundary" or raw.role != "workload_log_bytes" or raw.payload_sha256 != _sha256(self.full_payload):
            _reject("WORKLOAD_PARSE_REFERENCE_MISMATCH")
        payload_value = {
            "document_type": "WORKLOAD_PARSE_RESULT_V1",
            "schema_version": 1,
            "parser_identity": parser_identity(),
            "boundary_reference": boundary.as_dict(),
            "raw_log_reference": raw.as_dict(),
            "workload_window": _thaw(self.window),
            "complete_entry_count": len(self.full_records),
            "entry_indexes": [record.index for record in self.full_records],
            "entry_time_ieee754_binary64_hex": [record.time_binary64_hex for record in self.full_records],
            "prefix_complete_entry_count": len(self.prefix_records),
            "prefix_byte_length": len(self.prefix_payload),
            "prefix_sha256": _sha256(self.prefix_payload),
            "suffix_sha256": _sha256(self.full_payload[len(self.prefix_payload) :]),
            "fresh_request_count": self.fresh_request_count,
            "failure_marker_count": self.failure_marker_count,
            "stream_identity": self.window["stream_identity"],
        }
        payload = canonical_json_bytes(payload_value)
        metadata = self._common(stamp)
        metadata.update(
            {
                "boundary_reference": boundary.as_dict(),
                "raw_log_reference": raw.as_dict(),
                "fresh_request_count": self.fresh_request_count,
                "failure_marker_count": self.failure_marker_count,
            }
        )
        return WorkloadEvidenceCandidate("workload_parse_result", payload, _freeze(metadata))

    def adjudication_window(
        self,
        boundary_reference: EvidenceRef | Mapping[str, Any],
        raw_log_reference: EvidenceRef | Mapping[str, Any],
        parse_result_reference: EvidenceRef | Mapping[str, Any],
    ) -> dict[str, Any]:
        result = _thaw(self.window)
        result.update(
            {
                "boundary_reference": _reference_dict(boundary_reference),
                "raw_log_reference": _reference_dict(raw_log_reference),
                "parse_result_reference": _reference_dict(parse_result_reference),
            }
        )
        return result


def prepare_workload_window(
    policy: AuthenticatedPolicy,
    *,
    before: tuple[WorkloadHistoryEntry, ...],
    after: tuple[WorkloadHistoryEntry, ...],
    phase: str,
    ordinal: int,
    run_id: str,
    attempt_id: str,
    mutant_id: str,
    repetition: int,
    workload_pod_projection_reference: EvidenceRef | Mapping[str, Any],
    pod_name: str,
    pod_uid: str,
    container_restart_count: int,
) -> WorkloadEvidencePlan:
    _validate_policy(policy)
    prefix_payload = serialize_workload_history(before)
    full_payload = serialize_workload_history(after)
    if len(before) > len(after) or after[: len(before)] != before or not full_payload.startswith(prefix_payload):
        _reject("WORKLOAD_RAW_PREFIX_MISMATCH")
    prefix_records = parse_workload_jsonl(prefix_payload)
    full_records = parse_workload_jsonl(full_payload)
    validate_record_rounds(full_records)
    if (
        type(pod_name) is not str or not pod_name
        or type(pod_uid) is not str or not pod_uid
        or type(container_restart_count) is not int or container_restart_count < 0
    ):
        _reject("WORKLOAD_POD_IDENTITY_MISMATCH")
    try:
        pod_reference = validate_evidence_ref(
            policy,
            workload_pod_projection_reference,
            expected_run_id=run_id,
            expected_attempt_id=attempt_id,
        )
    except Exception:
        _reject("WORKLOAD_POD_IDENTITY_MISMATCH")
    if pod_reference.role != "kubernetes_object_projection":
        _reject("WORKLOAD_POD_IDENTITY_MISMATCH")
    stream = recompute_stream_identity(run_id, attempt_id)
    window = _window_core(phase, ordinal, run_id, attempt_id, mutant_id, repetition, stream)
    return WorkloadEvidencePlan(
        policy,
        prefix_payload,
        full_payload,
        prefix_records,
        full_records,
        _freeze(window),
        _freeze(pod_reference.as_dict()),
        pod_name,
        pod_uid,
        container_restart_count,
    )


def _candidate_row(context: Any, candidate: Mapping[str, Any]) -> Any:
    if candidate.get("document_type") in (
        "PAYLOAD_EVIDENCE_DESCRIPTOR_V1", "DESCRIPTOR_EVIDENCE_DESCRIPTOR_V1"
    ):
        return context._candidate_evidence(candidate)
    if candidate.get("document_type") == "WORKLOAD_PARSE_RESULT_V1":
        matches = [
            row for row in context.evidence.values()
            if row.reference.role == "workload_parse_result"
            and row.payload_bytes is not None
            and parse_canonical_json(row.payload_bytes) == _thaw(candidate)
        ]
        if len(matches) == 1:
            return matches[0]
    _reject("HOOK_CONTEXT_MISSING")


def _adjudication_subject(context: Any, candidate: Mapping[str, Any]) -> Mapping[str, Any]:
    if candidate.get("role") == "adjudication" and isinstance(
        candidate.get("workload_window_adjudication_identity"), Mapping
    ):
        return candidate
    core = candidate.get("workload_window")
    if candidate.get("document_type") == "WORKLOAD_PARSE_RESULT_V1":
        core = candidate.get("workload_window")
    if not isinstance(core, Mapping):
        _reject("WORKLOAD_WINDOW_MISMATCH")
    fields = ("phase", "ordinal", "run_id", "attempt_id", "mutant_id", "repetition", "stream_identity")
    matches = [
        row.descriptor for row in context.evidence.values()
        if row.reference.role == "adjudication"
        and isinstance(row.descriptor.get("workload_window_adjudication_identity"), Mapping)
        and all(row.descriptor["workload_window_adjudication_identity"].get(key) == core.get(key) for key in fields)
    ]
    if len(matches) != 1:
        _reject("HOOK_CONTEXT_MISSING")
    return matches[0]


def _resolved_workload(context: Any, candidate: Mapping[str, Any]) -> dict[str, Any]:
    subject = _adjudication_subject(context, candidate)
    window = subject.get("workload_window_adjudication_identity")
    if not isinstance(window, Mapping):
        _reject("WORKLOAD_WINDOW_MISMATCH")
    # The window is bound to the context of the adjudication predicate it belongs
    # to, never to whichever marker happened to be last.
    predicate = subject.get("predicate_oracle_or_classification_id")
    contexts = getattr(context, "evaluation_authorization_contexts", None)
    if not isinstance(contexts, Mapping):
        _reject("JOURNAL_EVALUATION_MARKER_MISSING")
    if not isinstance(predicate, str) or predicate not in contexts:
        _reject("JOURNAL_EVALUATION_MARKER_MISSING")
    expected = contexts[predicate]
    if not isinstance(expected, Mapping):
        _reject("JOURNAL_EVALUATION_MARKER_MISSING")
    if predicate != expected.get("predicate_id"):
        _reject("WORKLOAD_EVALUATION_CONTEXT_MISMATCH")
    if window.get("phase") != expected.get("phase") or WORKLOAD_WINDOWS.get(window.get("phase")) != window.get("ordinal"):
        _reject("WORKLOAD_EVALUATION_CONTEXT_MISMATCH")
    if subject.get("applicable_deadline") != expected.get("deadline_identity"):
        _reject("ADJUDICATION_DEADLINE_MISMATCH")
    if window.get("run_id") != context.run_id or window.get("attempt_id") != context.attempt_id:
        _reject("RUN_ATTEMPT_MISMATCH")
    recomputed_stream = recompute_stream_identity(context.run_id, context.attempt_id)
    if window.get("stream_identity") != recomputed_stream:
        _reject("WORKLOAD_STREAM_IDENTITY_MISMATCH")
    try:
        raw = context.resolve_reference(window.get("raw_log_reference"))
        boundary = context.resolve_reference(window.get("boundary_reference"))
        parsed = context.resolve_reference(window.get("parse_result_reference"))
    except Exception:
        _reject("WORKLOAD_PARSE_REFERENCE_MISMATCH")
    if raw.reference.role != "workload_log_bytes" or boundary.reference.role != "workload_boundary" or parsed.reference.role != "workload_parse_result":
        _reject("WORKLOAD_PARSE_REFERENCE_MISMATCH")
    boundary_descriptor = boundary.descriptor
    try:
        prefix = context.resolve_reference(boundary_descriptor.get("raw_log_reference"))
        pod = context.resolve_reference(boundary_descriptor.get("workload_pod_projection_reference"))
    except Exception:
        _reject("WORKLOAD_PARSE_REFERENCE_MISMATCH")
    if prefix.reference.role != "workload_log_bytes" or pod.reference.role != "kubernetes_object_projection":
        _reject("WORKLOAD_PARSE_REFERENCE_MISMATCH")
    if parsed.descriptor.get("raw_log_reference") != raw.reference.as_dict() or parsed.descriptor.get("boundary_reference") != boundary.reference.as_dict():
        _reject("WORKLOAD_PARSE_REFERENCE_MISMATCH")
    descriptor_core = {key: window[key] for key in ("phase", "ordinal", "run_id", "attempt_id", "mutant_id", "repetition", "stream_identity")}
    for descriptor in (prefix.descriptor, raw.descriptor, boundary.descriptor, parsed.descriptor):
        if _thaw(descriptor.get("workload_window")) != descriptor_core:
            _reject("WORKLOAD_WINDOW_MISMATCH")
        if descriptor.get("stream_identity") != recomputed_stream:
            _reject("WORKLOAD_STREAM_IDENTITY_MISMATCH")
    if prefix.payload_bytes is None or raw.payload_bytes is None or parsed.payload_bytes is None or pod.payload_bytes is None:
        _reject("HOOK_CONTEXT_MISSING")
    if boundary_descriptor.get("raw_log_sha256") != _sha256(prefix.payload_bytes):
        _reject("PAYLOAD_HASH_MISMATCH")
    if boundary_descriptor.get("raw_log_byte_length") != len(prefix.payload_bytes):
        _reject("PAYLOAD_SIZE_MISMATCH")
    if not raw.payload_bytes.startswith(prefix.payload_bytes):
        _reject("WORKLOAD_RAW_PREFIX_MISMATCH")
    prefix_records = parse_workload_jsonl(prefix.payload_bytes)
    records = parse_workload_jsonl(raw.payload_bytes)
    validate_record_rounds(records)
    if records[: len(prefix_records)] != prefix_records:
        _reject("WORKLOAD_RAW_PREFIX_MISMATCH")
    indexes = [record.index for record in records]
    times = [record.time_binary64_hex for record in records]
    if raw.descriptor.get("complete_entry_count") != len(records) or _thaw(raw.descriptor.get("entry_indexes")) != indexes or _thaw(raw.descriptor.get("entry_time_ieee754_binary64_hex")) != times:
        _reject("WORKLOAD_PARSE_RECOMPUTATION_MISMATCH")
    if boundary.descriptor.get("complete_entry_count") != len(prefix_records) or _thaw(boundary.descriptor.get("entry_time_ieee754_binary64_hex")) != [record.time_binary64_hex for record in prefix_records] or boundary.descriptor.get("request_count") != sum(record.number for record in prefix_records):
        _reject("WORKLOAD_PARSE_RECOMPUTATION_MISMATCH")
    fresh = records[len(prefix_records) :]
    expected_parse = {
        "document_type": "WORKLOAD_PARSE_RESULT_V1",
        "schema_version": 1,
        "parser_identity": parser_identity(),
        "boundary_reference": boundary.reference.as_dict(),
        "raw_log_reference": raw.reference.as_dict(),
        "workload_window": descriptor_core,
        "complete_entry_count": len(records),
        "entry_indexes": indexes,
        "entry_time_ieee754_binary64_hex": times,
        "prefix_complete_entry_count": len(prefix_records),
        "prefix_byte_length": len(prefix.payload_bytes),
        "prefix_sha256": _sha256(prefix.payload_bytes),
        "suffix_sha256": _sha256(raw.payload_bytes[len(prefix.payload_bytes) :]),
        "fresh_request_count": sum(record.number for record in fresh),
        "failure_marker_count": sum(1 for record in fresh if not record.ok),
        "stream_identity": recomputed_stream,
    }
    try:
        parsed_payload = parse_canonical_json(parsed.payload_bytes)
        context._policy.structural_validate(parsed_payload)
    except Exception:
        _reject("WORKLOAD_PARSE_SCHEMA_INVALID")
    if parsed_payload.get("parser_identity") != parser_identity():
        _reject("WORKLOAD_PARSER_IDENTITY_MISMATCH")
    if parsed_payload != expected_parse:
        _reject("WORKLOAD_PARSE_RECOMPUTATION_MISMATCH")
    if parsed.descriptor.get("fresh_request_count") != expected_parse["fresh_request_count"] or parsed.descriptor.get("failure_marker_count") != expected_parse["failure_marker_count"]:
        _reject("WORKLOAD_PARSE_RECOMPUTATION_MISMATCH")
    if expected_parse["fresh_request_count"] < 50 or expected_parse["failure_marker_count"] != 0:
        _reject("WORKLOAD_CARDINALITY_INVALID")
    try:
        pod_object = parse_canonical_json(pod.payload_bytes)
    except Exception:
        _reject("WORKLOAD_POD_IDENTITY_MISMATCH")
    if pod_object.get("document_type") == "KUBERNETES_LIST_RESPONSE_V1":
        _reject("WORKLOAD_POD_IDENTITY_MISMATCH")
    metadata = pod_object.get("metadata") if isinstance(pod_object, dict) else None
    if not isinstance(metadata, dict):
        _reject("WORKLOAD_POD_IDENTITY_MISMATCH")
    if boundary.descriptor.get("pod_name") != metadata.get("name") or boundary.descriptor.get("pod_uid") != metadata.get("uid"):
        _reject("WORKLOAD_POD_IDENTITY_MISMATCH")
    status = pod_object.get("status")
    statuses = status.get("containerStatuses", []) if isinstance(status, dict) else []
    restart_count = sum(
        row.get("restartCount", 0) for row in statuses
        if isinstance(row, dict) and type(row.get("restartCount", 0)) is int
    )
    if boundary.descriptor.get("container_restart_count") != restart_count:
        _reject("WORKLOAD_RESTART_COUNT_MISMATCH")
    try:
        adjudication = context._candidate_evidence(_thaw(subject))
    except Exception:
        _reject("HOOK_CONTEXT_MISSING")
    raw_references = subject.get("raw_evidence_references")
    if not isinstance(raw_references, (list, tuple)):
        _reject("WORKLOAD_PARSE_REFERENCE_MISMATCH")
    referenced_ids = {
        row.get("evidence_id")
        for row in raw_references
        if isinstance(row, Mapping)
    }
    if not {
        raw.reference.evidence_id,
        parsed.reference.evidence_id,
        pod.reference.evidence_id,
    }.issubset(referenced_ids):
        _reject("WORKLOAD_PARSE_REFERENCE_MISMATCH")
    if not (
        pod.publication.sequence_number <= prefix.publication.sequence_number
        < boundary.publication.sequence_number <= raw.publication.sequence_number
        <= parsed.publication.sequence_number < adjudication.publication.sequence_number
    ):
        _reject("PUBLICATION_ORDER_INVALID")
    return expected_parse


def validate_resolved_workload_cardinality(context: Any, candidate: Mapping[str, Any]) -> None:
    _resolved_workload(context, candidate)


def validate_resolved_workload_window(context: Any, candidate: Mapping[str, Any]) -> None:
    _resolved_workload(context, candidate)

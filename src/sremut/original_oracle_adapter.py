"""One-shot parent adapter for the unchanged stock SREGym MitigationOracle."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
from types import MappingProxyType
from typing import Any, Mapping, NoReturn

from sremut.canonical_json import canonical_json_bytes, parse_canonical_json
from sremut.policy_runtime import AuthenticatedPolicy
from sremut.sensitive import SensitiveCaptureError, validate_payload


SREMUT_ROOT = Path("/home/sakibbuet2k19/sremut/SREMut")
SREGYM_ROOT = Path("/home/sakibbuet2k19/sremut/SREGym")
STOCK_PYTHON = Path("/home/sakibbuet2k19/sremut/SREGym/.venv/bin/python")
EXPECTED_STOCK_PYTHON_TARGET = Path("/usr/bin/python3.12")
WORKER_PATH = Path(__file__).with_name("original_oracle_worker.py")
EXPECTED_CONTEXT = "kind-kind"
EXPECTED_NAMESPACE = "social-network"
EXPECTED_SREGYM_COMMIT = "ba07faf1a322f9b6d4a279643bb796aa2f36f64b"
EXPECTED_ORACLE_SHA256 = "a087fd38399cfca4c2de764dbbab837d6100350ab9f71b89552cafe2c91fca8b"
ORACLE_RELATIVE_PATH = "sregym/conductor/oracles/mitigation.py"
EXPECTED_PYTHON_VERSION = "3.12.3"
EXPECTED_DEPENDENCIES = MappingProxyType(
    {"PyYAML": "6.0.2", "jsonschema": "4.23.0", "kubernetes": "30.1.0"}
)
EXPECTED_TAGS = MappingProxyType(
    {
        "sremut-missing-service-contract-v1": (
            "378e9e9180438910611e7642402220e76bb302ca",
            "abed58d67e3f91e61f3ad666a47f0101cc680b93",
        ),
        "sremut-missing-service-execution-profile-v1": (
            "7c6493eb7dce68370fd0d5be572edd968654a1d6",
            "35fcaeecd6cca02aaec6ebed63455f662bc28176",
        ),
        "sremut-missing-service-evidence-policy-v1": (
            "40e50e4f6fcaff150a84137ed099a220441e4258",
            "c2f500c9ec6ed46b4f62d989c6c7d2b6743d9471",
        ),
    }
)
EXPECTED_SUBMODULES = MappingProxyType(
    {
        "SREGym-applications": "2b2f9c6c2e97c44abbfcc44af1cf2f994bbb04f8",
        "SREGym-applications/FleetCast": "a2b9c5e5a14cc7892c347e0dd944a4e2ab09a8fb",
        "SREGym-applications/astronomy-shop": "7d7b074714345a0c282b0be75af7a2c504b44c95",
        "SREGym-applications/flight-ticket": "77fe227f7df911ccdbe2f2b514e5090b51a8a169",
        "SREGym-applications/train-ticket": "c9537c1533514bb6ba9bd664b9312c2b9cee413c",
    }
)
INPUT_FIELDS = frozenset(
    {"kubernetes_context", "namespace", "captured_replica_baseline", "evidence_paths"}
)
EVIDENCE_ROLES = (
    "original_oracle_input",
    "original_oracle_stdout",
    "original_oracle_stderr",
    "original_oracle_result",
)
_NAME = re.compile(r"[a-z0-9](?:[-a-z0-9.]*[a-z0-9])?")
_HASH = re.compile(r"[0-9a-f]{64}")
_GIT = "/usr/bin/git"


@dataclass(slots=True)
class OriginalOracleAdapterError(RuntimeError):
    code: str

    def __str__(self) -> str:
        return self.code


def _reject(code: str) -> NoReturn:
    raise OriginalOracleAdapterError(code) from None


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


def _safe_relative_path(value: Any) -> bool:
    if type(value) is not str or not value or value.startswith("/") or "\\" in value:
        return False
    return all(part not in ("", ".", "..") for part in value.split("/"))


@dataclass(frozen=True, slots=True, init=False)
class OriginalOracleInput:
    kubernetes_context: str
    namespace: str
    captured_replica_baseline: Mapping[str, int]
    evidence_paths: Mapping[str, str]

    @classmethod
    def from_mapping(cls, candidate: Any) -> "OriginalOracleInput":
        if type(candidate) is not dict or set(candidate) != INPUT_FIELDS:
            _reject("ORIGINAL_ORACLE_INPUT_INVALID")
        if candidate["kubernetes_context"] != EXPECTED_CONTEXT:
            _reject("ORIGINAL_ORACLE_INPUT_INVALID")
        if candidate["namespace"] != EXPECTED_NAMESPACE:
            _reject("ORIGINAL_ORACLE_INPUT_INVALID")
        baseline = candidate["captured_replica_baseline"]
        if type(baseline) is not dict or not baseline:
            _reject("ORIGINAL_ORACLE_INPUT_INVALID")
        for name, count in baseline.items():
            if (
                type(name) is not str
                or not _NAME.fullmatch(name)
                or len(name) > 253
                or type(count) is not int
                or count < 0
            ):
                _reject("ORIGINAL_ORACLE_INPUT_INVALID")
        paths = candidate["evidence_paths"]
        if type(paths) is not dict or set(paths) != set(EVIDENCE_ROLES):
            _reject("ORIGINAL_ORACLE_INPUT_INVALID")
        if len(set(paths.values())) != len(EVIDENCE_ROLES):
            _reject("ORIGINAL_ORACLE_INPUT_INVALID")
        if any(not _safe_relative_path(path) for path in paths.values()):
            _reject("ORIGINAL_ORACLE_INPUT_INVALID")
        result = object.__new__(cls)
        object.__setattr__(result, "kubernetes_context", candidate["kubernetes_context"])
        object.__setattr__(result, "namespace", candidate["namespace"])
        object.__setattr__(result, "captured_replica_baseline", MappingProxyType(dict(baseline)))
        object.__setattr__(result, "evidence_paths", MappingProxyType(dict(paths)))
        return result

    def as_dict(self) -> dict[str, Any]:
        return {
            "captured_replica_baseline": dict(self.captured_replica_baseline),
            "evidence_paths": dict(self.evidence_paths),
            "kubernetes_context": self.kubernetes_context,
            "namespace": self.namespace,
        }


def extract_returned_boolean(raw_result: Any) -> bool:
    """Apply the strict frozen stock-return mapping extraction."""

    if type(raw_result) is not dict:
        _reject("ORIGINAL_ORACLE_RETURN_SHAPE_INVALID")
    if set(raw_result) != {"success"}:
        _reject("ORIGINAL_ORACLE_RETURN_SHAPE_INVALID")
    if type(raw_result["success"]) is not bool:
        _reject("ORIGINAL_ORACLE_RETURN_SHAPE_INVALID")
    return raw_result["success"]


@dataclass(frozen=True, slots=True)
class OracleEvidenceCandidate:
    role: str
    producer: str
    source_kind: str
    storage_class: str
    media_type: str
    payload: bytes = field(repr=False)
    supplemental_metadata: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class OracleInvocationResult:
    invocation_id: str
    outcome: str
    returned_boolean: bool | None
    raw_result: Mapping[str, Any] | None
    exit_status: int | None
    started_utc: str
    finished_utc: str
    candidates: tuple[OracleEvidenceCandidate, ...]


def _run_checked(argv: list[str], cwd: Path) -> bytes:
    try:
        completed = subprocess.run(
            argv,
            cwd=cwd,
            env={"LC_ALL": "C.UTF-8", "PATH": "/usr/bin:/bin"},
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=20,
            check=True,
        )
    except Exception:
        _reject("ORIGINAL_ORACLE_PROVENANCE_MISMATCH")
    return completed.stdout


def _git(args: list[str], root: Path) -> str:
    return _run_checked([_GIT, *args], root).decode("utf-8", errors="strict").rstrip("\r\n")


def verify_stock_provenance() -> Mapping[str, Any]:
    """Authenticate every frozen source/runtime identity before launch."""

    if _git(["rev-parse", "HEAD"], SREGYM_ROOT) != EXPECTED_SREGYM_COMMIT:
        _reject("ORIGINAL_ORACLE_PROVENANCE_MISMATCH")
    if _git(["status", "--porcelain=v1", "--untracked-files=all"], SREGYM_ROOT):
        _reject("ORIGINAL_ORACLE_PROVENANCE_MISMATCH")
    rows = _git(["submodule", "status", "--recursive"], SREGYM_ROOT).splitlines()
    observed: dict[str, str] = {}
    for row in rows:
        if not row or row[0] != " ":
            _reject("ORIGINAL_ORACLE_PROVENANCE_MISMATCH")
        parts = row[1:].split()
        if len(parts) < 2:
            _reject("ORIGINAL_ORACLE_PROVENANCE_MISMATCH")
        observed[parts[1]] = parts[0]
    if observed != dict(EXPECTED_SUBMODULES):
        _reject("ORIGINAL_ORACLE_PROVENANCE_MISMATCH")
    module_sha = hashlib.sha256((SREGYM_ROOT / ORACLE_RELATIVE_PATH).read_bytes()).hexdigest()
    if module_sha != EXPECTED_ORACLE_SHA256:
        _reject("ORIGINAL_ORACLE_PROVENANCE_MISMATCH")
    for name, (object_id, peeled) in EXPECTED_TAGS.items():
        if _git(["rev-parse", f"refs/tags/{name}"], SREMUT_ROOT) != object_id:
            _reject("ORIGINAL_ORACLE_PROVENANCE_MISMATCH")
        if _git(["rev-parse", f"refs/tags/{name}^{{}}"], SREMUT_ROOT) != peeled:
            _reject("ORIGINAL_ORACLE_PROVENANCE_MISMATCH")
    if (
        not STOCK_PYTHON.is_symlink()
        or not STOCK_PYTHON.is_file()
        or STOCK_PYTHON.resolve() != EXPECTED_STOCK_PYTHON_TARGET
    ):
        _reject("ORIGINAL_ORACLE_PROVENANCE_MISMATCH")
    executable_sha = hashlib.sha256(STOCK_PYTHON.read_bytes()).hexdigest()
    probe = (
        "import importlib.metadata as m,json,platform;"
        "print(json.dumps({'python':platform.python_version(),'dependencies':"
        "{n:m.version(n) for n in ('PyYAML','jsonschema','kubernetes')}},sort_keys=True,separators=(',',':')))"
    )
    raw = _run_checked([str(STOCK_PYTHON), "-I", "-c", probe], SREGYM_ROOT)
    try:
        runtime = json.loads(raw.decode("utf-8", errors="strict"))
    except Exception:
        _reject("ORIGINAL_ORACLE_PROVENANCE_MISMATCH")
    if runtime != {"dependencies": dict(EXPECTED_DEPENDENCIES), "python": EXPECTED_PYTHON_VERSION}:
        _reject("ORIGINAL_ORACLE_PROVENANCE_MISMATCH")
    return MappingProxyType(
        {
            "dependency_versions": EXPECTED_DEPENDENCIES,
            "oracle_module_sha256": module_sha,
            "python_executable_sha256": executable_sha,
            "python_version": EXPECTED_PYTHON_VERSION,
            "sregym_commit": EXPECTED_SREGYM_COMMIT,
        }
    )


def _ensure_safe_destination(root: Path, relative: str) -> Path:
    if (
        not root.is_absolute()
        or root.is_symlink()
        or not root.is_dir()
        or root.resolve() != root
    ):
        _reject("ORIGINAL_ORACLE_EVIDENCE_PATH_INVALID")
    current = root
    parts = relative.split("/")
    for part in parts[:-1]:
        current = current / part
        if current.exists():
            if current.is_symlink() or not current.is_dir():
                _reject("ORIGINAL_ORACLE_EVIDENCE_PATH_INVALID")
        else:
            current.mkdir(mode=0o700)
    destination = current / parts[-1]
    if destination.exists() or destination.is_symlink():
        _reject("ORIGINAL_ORACLE_OUTPUT_REUSE_FORBIDDEN")
    return destination


def _atomic_write(path: Path, data: bytes) -> None:
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        view = memoryview(data)
        while view:
            try:
                written = os.write(descriptor, view)
            except InterruptedError:
                continue
            if written <= 0:
                _reject("ORIGINAL_ORACLE_EVIDENCE_WRITE_FAILED")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary, path)
    parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)


def _validate_worker_result(value: Any, provenance: Mapping[str, Any]) -> tuple[str, bool | None, dict[str, Any] | None]:
    required = {
        "outcome",
        "exception_type",
        "raw_result",
        "raw_result_sha256",
        "returned_boolean",
        "runtime",
        "schema_version",
    }
    if type(value) is not dict or set(value) != required or value["schema_version"] != 1:
        _reject("ORIGINAL_ORACLE_WORKER_OUTPUT_INVALID")
    runtime = value["runtime"]
    expected_runtime = {
        "dependency_versions": dict(EXPECTED_DEPENDENCIES),
        "oracle_module_sha256": provenance["oracle_module_sha256"],
        "python_version": EXPECTED_PYTHON_VERSION,
        "sregym_commit": EXPECTED_SREGYM_COMMIT,
    }
    if type(runtime) is not dict or runtime != expected_runtime:
        _reject("ORIGINAL_ORACLE_WORKER_OUTPUT_INVALID")
    outcome = value["outcome"]
    if outcome in ("RETURNED_TRUE", "RETURNED_FALSE"):
        raw_result = value["raw_result"]
        returned = extract_returned_boolean(raw_result)
        expected_outcome = "RETURNED_TRUE" if returned is True else "RETURNED_FALSE"
        if (
            outcome != expected_outcome
            or value["returned_boolean"] is not returned
            or value["exception_type"] is not None
            or value["raw_result_sha256"] != hashlib.sha256(canonical_json_bytes(raw_result)).hexdigest()
        ):
            _reject("ORIGINAL_ORACLE_WORKER_OUTPUT_INVALID")
        return outcome, returned, raw_result
    if outcome == "ORACLE_EXCEPTION":
        if (
            type(value["exception_type"]) is not str
            or not value["exception_type"]
            or value["raw_result"] is not None
            or value["raw_result_sha256"] is not None
            or value["returned_boolean"] is not None
        ):
            _reject("ORIGINAL_ORACLE_WORKER_OUTPUT_INVALID")
        return outcome, None, None
    if outcome == "ORIGINAL_ORACLE_RETURN_SHAPE_INVALID":
        if any(value[key] is not None for key in ("exception_type", "raw_result", "raw_result_sha256", "returned_boolean")):
            _reject("ORIGINAL_ORACLE_WORKER_OUTPUT_INVALID")
        return outcome, None, None
    _reject("ORIGINAL_ORACLE_WORKER_OUTPUT_INVALID")


def _candidate(policy: AuthenticatedPolicy, role: str, payload: bytes, metadata: Mapping[str, Any]) -> OracleEvidenceCandidate:
    contract = policy.role(role)
    try:
        validate_payload(policy, role, payload, media_type=contract.media_type)
    except SensitiveCaptureError:
        _reject("ORIGINAL_ORACLE_CAPTURE_REJECTED")
    return OracleEvidenceCandidate(
        role=role,
        producer=contract.producer,
        source_kind=contract.source_kind,
        storage_class=contract.storage_class,
        media_type=contract.media_type,
        payload=bytes(payload),
        supplemental_metadata=MappingProxyType(dict(metadata)),
    )


class OriginalOracleAdapter:
    """Bounded, one-shot adapter with no in-process SREGym import."""

    __slots__ = ("_policy", "_timeout_seconds")

    def __init__(self, policy: AuthenticatedPolicy, *, timeout_seconds: int):
        if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 180:
            _reject("ORIGINAL_ORACLE_TIMEOUT_INVALID")
        self._policy = policy
        self._timeout_seconds = timeout_seconds

    def invoke(self, candidate: Any, *, attempt_root: Path) -> OracleInvocationResult:
        request = OriginalOracleInput.from_mapping(candidate)
        provenance = verify_stock_provenance()
        input_bytes = canonical_json_bytes(request.as_dict())
        invocation_id = hashlib.sha256(input_bytes).hexdigest()
        destinations = {
            role: _ensure_safe_destination(attempt_root, request.evidence_paths[role])
            for role in EVIDENCE_ROLES
        }
        started = datetime.now(timezone.utc).isoformat()
        _atomic_write(destinations["original_oracle_input"], input_bytes)
        worker_output = destinations["original_oracle_result"].with_name(
            ".original-oracle-worker-result.json"
        )
        if worker_output.exists() or worker_output.is_symlink():
            _reject("ORIGINAL_ORACLE_OUTPUT_REUSE_FORBIDDEN")
        argv = [str(STOCK_PYTHON), "-I", "-B", str(WORKER_PATH), str(destinations["original_oracle_input"]), str(worker_output)]
        environment = {
            "LC_ALL": "C.UTF-8",
            "PATH": "/usr/bin:/bin",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
        }
        process: subprocess.Popen[bytes] | None = None
        stdout = b""
        stderr = b""
        exit_status: int | None = None
        classification = "INVALID_WORKER_OUTPUT"
        returned: bool | None = None
        raw_result: dict[str, Any] | None = None
        exception_type: str | None = None
        try:
            process = subprocess.Popen(
                argv,
                cwd=SREGYM_ROOT,
                env=environment,
                shell=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
            try:
                stdout, stderr = process.communicate(timeout=self._timeout_seconds)
                exit_status = process.returncode
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                stdout, stderr = process.communicate()
                classification = "TIMEOUT"
            except KeyboardInterrupt:
                os.killpg(process.pid, signal.SIGKILL)
                stdout, stderr = process.communicate()
                classification = "INTERRUPTED"
            if classification not in ("TIMEOUT", "INTERRUPTED"):
                if exit_status != 0:
                    classification = "NONZERO_EXIT"
                elif not worker_output.is_file() or worker_output.is_symlink():
                    classification = "INVALID_WORKER_OUTPUT"
                else:
                    try:
                        worker_value = parse_canonical_json(worker_output.read_bytes())
                        classification, returned, raw_result = _validate_worker_result(worker_value, provenance)
                        exception_type = worker_value["exception_type"]
                    except OriginalOracleAdapterError as error:
                        if error.code == "ORIGINAL_ORACLE_RETURN_SHAPE_INVALID":
                            classification = error.code
                        else:
                            classification = "INVALID_WORKER_OUTPUT"
                    except Exception:
                        classification = "INVALID_WORKER_OUTPUT"
        finally:
            if process is not None and process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            if worker_output.exists() and not worker_output.is_symlink():
                worker_output.unlink()
        finished = datetime.now(timezone.utc).isoformat()
        result_value = {
            "exception_type": exception_type,
            "exit_status": exit_status,
            "finished_utc": finished,
            "invocation_id": invocation_id,
            "outcome": classification,
            "raw_result": raw_result,
            "raw_result_sha256": (
                hashlib.sha256(canonical_json_bytes(raw_result)).hexdigest()
                if raw_result is not None
                else None
            ),
            "returned_boolean": returned,
            "runtime": _thaw(provenance),
            "schema_version": 1,
            "started_utc": started,
            "timeout_seconds": self._timeout_seconds,
        }
        result_bytes = canonical_json_bytes(result_value)
        candidates = (
            _candidate(self._policy, "original_oracle_input", input_bytes, {"invocation_ordinal": 1}),
            _candidate(self._policy, "original_oracle_stdout", stdout, {"invocation_reference": invocation_id}),
            _candidate(self._policy, "original_oracle_stderr", stderr, {"invocation_reference": invocation_id}),
            _candidate(
                self._policy,
                "original_oracle_result",
                result_bytes,
                {
                    "exit_status": exit_status,
                    "input_reference": invocation_id,
                    "returned_boolean_or_exception": (
                        returned if returned is not None else {"classification": classification}
                    ),
                    "stderr_reference": invocation_id,
                    "stdout_reference": invocation_id,
                },
            ),
        )
        _atomic_write(destinations["original_oracle_stdout"], stdout)
        _atomic_write(destinations["original_oracle_stderr"], stderr)
        _atomic_write(destinations["original_oracle_result"], result_bytes)
        return OracleInvocationResult(
            invocation_id=invocation_id,
            outcome=classification,
            returned_boolean=returned,
            raw_result=None if raw_result is None else MappingProxyType(dict(raw_result)),
            exit_status=exit_status,
            started_utc=started,
            finished_utc=finished,
            candidates=candidates,
        )

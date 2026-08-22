"""Frozen canonical JSON primitives for SREMut evidence.

The descriptor and journal hash domains use compact, key-sorted UTF-8 JSON.
Only JSON-native values are accepted; in particular floats and implicit key or
container coercions are forbidden.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, NoReturn


@dataclass(slots=True)
class CanonicalJSONError(ValueError):
    """A non-sensitive, stable canonicalization failure."""

    code: str

    def __str__(self) -> str:
        return self.code


def _reject(code: str) -> NoReturn:
    raise CanonicalJSONError(code) from None


def validate_canonical_value(value: Any) -> None:
    """Require the exact closed JSON value model used by the frozen schema."""

    if value is None or isinstance(value, (str, bool)):
        if isinstance(value, str):
            try:
                value.encode("utf-8", errors="strict")
            except UnicodeEncodeError:
                _reject("CANONICAL_INVALID_UNICODE")
        return
    # bool is deliberately handled before int.
    if isinstance(value, int) and not isinstance(value, bool):
        return
    if isinstance(value, float):
        _reject("CANONICAL_FLOAT_FORBIDDEN")
    if isinstance(value, list):
        for item in value:
            validate_canonical_value(item)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                _reject("CANONICAL_NON_STRING_KEY")
            validate_canonical_value(key)
            validate_canonical_value(item)
        return
    _reject("CANONICAL_UNSUPPORTED_TYPE")


def canonical_json_bytes(value: Any) -> bytes:
    """Return compact sorted UTF-8 JSON with no trailing newline."""

    validate_canonical_value(value)
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8", errors="strict")
    except (TypeError, ValueError, UnicodeEncodeError):
        _reject("CANONICAL_SERIALIZATION_FAILED")


def canonical_json_line(value: Any) -> bytes:
    """Return one canonical JSON-lines record with exactly one LF."""

    return canonical_json_bytes(value) + b"\n"


def parse_canonical_json(data: bytes, *, line: bool = False) -> Any:
    """Parse strict UTF-8 and require byte-for-byte canonical encoding."""

    if not isinstance(data, bytes):
        _reject("CANONICAL_BYTES_REQUIRED")
    material = data
    if line:
        if not data.endswith(b"\n") or data.endswith(b"\n\n"):
            _reject("CANONICAL_NEWLINE_INVALID")
        material = data[:-1]
    elif data.endswith(b"\n"):
        _reject("CANONICAL_NEWLINE_INVALID")
    try:
        text = material.decode("utf-8", errors="strict")
        value = json.loads(text, parse_constant=lambda _value: _reject("CANONICAL_FLOAT_FORBIDDEN"))
    except CanonicalJSONError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError):
        _reject("CANONICAL_INVALID_JSON")
    validate_canonical_value(value)
    expected = canonical_json_line(value) if line else canonical_json_bytes(value)
    if expected != data:
        _reject("CANONICAL_ENCODING_MISMATCH")
    return value


def sha256_hex(data: bytes) -> str:
    """Hash exact supplied bytes with SHA-256."""

    if not isinstance(data, bytes):
        _reject("CANONICAL_BYTES_REQUIRED")
    return hashlib.sha256(data).hexdigest()


def canonical_sha256(value: Any) -> str:
    """Hash the frozen canonical JSON byte representation."""

    return sha256_hex(canonical_json_bytes(value))

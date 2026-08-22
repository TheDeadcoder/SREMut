"""Literal, non-redacting sensitive-material admission checks."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, Iterable, NoReturn
from urllib.parse import urlsplit

import yaml

from sremut.policy_runtime import AuthenticatedPolicy


@dataclass(slots=True)
class SensitiveCaptureError(ValueError):
    code: str
    detector_id: str | None = None

    def __str__(self) -> str:
        return self.code


@dataclass(frozen=True, slots=True)
class SensitiveFinding:
    detector_id: str
    rejection_code: str


def _reject(code: str, detector_id: str | None = None) -> NoReturn:
    raise SensitiveCaptureError(code, detector_id) from None


_AUTHORIZATION = re.compile(r"(?im)^Authorization:[ \t]*[^\r\n]+(?=\r?$)")
_BEARER = re.compile(r"(?i)\bBearer[ \t]+[A-Za-z0-9._~+/=-]{16,4096}\b")
_URL = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://[^\s<>\"']+")
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}=.*$")
_PRIVATE_MARKERS = (
    "-----BEGIN PRIVATE KEY-----",
    "-----BEGIN RSA PRIVATE KEY-----",
    "-----BEGIN EC PRIVATE KEY-----",
    "-----BEGIN OPENSSH PRIVATE KEY-----",
)
_SENSITIVE_ASSIGNMENTS = frozenset(
    {
        "API_KEY",
        "AUTH_TOKEN",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AZURE_CLIENT_SECRET",
        "GITHUB_TOKEN",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "KUBECONFIG",
        "PASSWORD",
        "PRIVATE_KEY",
        "SECRET",
        "TOKEN",
    }
)
_STRUCTURED_KEYS = frozenset(
    {
        "access_token",
        "api_key",
        "bearer_token",
        "client_secret",
        "id_token",
        "refresh_token",
        "token",
        "passphrase",
        "password",
        "client_key_data",
        "private_key",
        "private_key_data",
        "ssh_private_key",
    }
)
_KUBECONFIG_KEYS = frozenset(
    {
        "client-key-data",
        "client-key",
        "client-certificate-data",
        "client-certificate",
        "token",
        "token-file",
        "username",
        "password",
        "auth-provider",
        "exec",
    }
)


def _structured_items(value: Any) -> Iterable[tuple[str, Any]]:
    if isinstance(value, dict):
        for key, child in value.items():
            if isinstance(key, str):
                yield key, child
            yield from _structured_items(child)
    elif isinstance(value, list):
        for child in value:
            yield from _structured_items(child)


def _unique_json_mapping(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _reject("CAPTURE_REJECTED_INVALID_ENCODING")
        result[key] = value
    return result


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _unique_yaml_mapping(
    loader: _UniqueKeyLoader,
    node: yaml.MappingNode,
    deep: bool = False,
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str) or key in result:
            _reject("CAPTURE_REJECTED_INVALID_ENCODING")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _unique_yaml_mapping,
)


def _parse_structured(data: bytes, structured_format: str | None) -> Any | None:
    if structured_format is None:
        return None
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        _reject("CAPTURE_REJECTED_INVALID_ENCODING")
    try:
        if structured_format == "json":
            return json.loads(text, object_pairs_hook=_unique_json_mapping)
        if structured_format == "yaml":
            return yaml.load(text, Loader=_UniqueKeyLoader)
    except (json.JSONDecodeError, yaml.YAMLError):
        _reject("CAPTURE_REJECTED_INVALID_ENCODING")
    _reject("CAPTURE_REJECTED_UNAUTHORIZED_SOURCE")


def detect_sensitive(
    data: bytes,
    *,
    structured_format: str | None = None,
) -> SensitiveFinding | None:
    """Return the first frozen detector finding without returning content."""

    if not isinstance(data, bytes):
        _reject("CAPTURE_REJECTED_UNAUTHORIZED_SOURCE")
    text = data.decode("latin-1")
    if _AUTHORIZATION.search(text):
        return SensitiveFinding("SENSITIVE_AUTHORIZATION_HEADER", "CAPTURE_REJECTED_AUTHORIZATION")
    if _BEARER.search(text):
        return SensitiveFinding("SENSITIVE_BEARER_CREDENTIAL", "CAPTURE_REJECTED_BEARER")
    if any(marker in text for marker in _PRIVATE_MARKERS):
        return SensitiveFinding("SENSITIVE_PEM_PRIVATE_KEY", "CAPTURE_REJECTED_PRIVATE_KEY")
    for candidate in _URL.findall(text):
        try:
            parsed = urlsplit(candidate)
            userinfo = parsed.netloc.rsplit("@", 1)[0] if "@" in parsed.netloc else ""
            username = parsed.username
            password = parsed.password
        except ValueError:
            continue
        if (
            parsed.scheme.lower() in ("http", "https")
            and ":" in userinfo
            and username not in (None, "")
            and password not in (None, "")
        ):
            return SensitiveFinding("SENSITIVE_CREDENTIAL_URL", "CAPTURE_REJECTED_CREDENTIAL_URL")
    consecutive = 0
    for line in text.splitlines():
        if _ASSIGNMENT.fullmatch(line):
            consecutive += 1
            key, value = line.split("=", 1)
            if key.upper() in _SENSITIVE_ASSIGNMENTS and value:
                return SensitiveFinding("SENSITIVE_ENVIRONMENT_ASSIGNMENT", "CAPTURE_REJECTED_ENVIRONMENT")
            if consecutive >= 4:
                return SensitiveFinding("SENSITIVE_DENSE_ENVIRONMENT_DUMP", "CAPTURE_REJECTED_ENVIRONMENT")
        else:
            consecutive = 0

    structured = _parse_structured(data, structured_format)
    root = structured if isinstance(structured, dict) else None
    if root is not None and root.get("apiVersion") == "v1" and root.get("kind") == "Secret":
        return SensitiveFinding("SENSITIVE_KUBERNETES_SECRET_OBJECT", "CAPTURE_REJECTED_KUBERNETES_SECRET")
    if (
        root is not None
        and root.get("apiVersion") == "v1"
        and root.get("kind") == "Config"
        and all(key in root for key in ("clusters", "contexts", "users"))
    ):
        users = root.get("users")
        if isinstance(users, list):
            for entry in users:
                user = entry.get("user") if isinstance(entry, dict) else None
                if isinstance(user, dict) and any(key in user for key in _KUBECONFIG_KEYS):
                    return SensitiveFinding("SENSITIVE_KUBECONFIG_STRUCTURE", "CAPTURE_REJECTED_KUBECONFIG")
    if isinstance(structured, (dict, list)):
        for key, value in _structured_items(structured):
            normalized = key.lower().replace("-", "_")
            if normalized in _STRUCTURED_KEYS and value not in ("", None, False, [], {}):
                return SensitiveFinding("SENSITIVE_STRUCTURED_SECRET", "CAPTURE_REJECTED_STRUCTURED_SECRET")
    return None


def validate_payload(
    policy: AuthenticatedPolicy,
    role: str,
    data: bytes,
    *,
    media_type: str | None = None,
    structured_format: str | None = None,
) -> None:
    """Apply role bounds, encoding, redaction policy, and all detectors."""

    contract = policy.role(role)
    if contract.storage_class != "PAYLOAD_WITH_DESCRIPTOR":
        _reject("CAPTURE_REJECTED_UNAUTHORIZED_SOURCE")
    if media_type is not None and media_type != contract.media_type:
        _reject("CAPTURE_REJECTED_UNAUTHORIZED_SOURCE")
    if len(data) > contract.maximum_bytes or (not data and not contract.zero_byte_payload_allowed):
        _reject("CAPTURE_REJECTED_SIZE")
    if contract.strict_utf8_required:
        try:
            data.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            _reject("CAPTURE_REJECTED_INVALID_ENCODING")
    if structured_format is None and contract.media_type == "application/json":
        structured_format = "json"
    finding = detect_sensitive(data, structured_format=structured_format)
    if finding is not None:
        _reject(finding.rejection_code, finding.detector_id)


def validate_redaction_status(value: str) -> None:
    if value != "NOT_REDACTED":
        _reject("CAPTURE_REJECTED_UNAUTHORIZED_SOURCE")


def validate_projection_allowlist(value: Any, allowed_paths: Iterable[Iterable[str]]) -> None:
    """Reject structured leaf fields outside frozen token paths."""

    allowed = {tuple(path) for path in allowed_paths}
    if any(not path or any(not isinstance(token, str) or token in (".", "..") for token in path) for path in allowed):
        _reject("CAPTURE_REJECTED_UNAUTHORIZED_SOURCE")

    def permitted(path: tuple[str, ...]) -> bool:
        return any(
            len(path) <= len(candidate)
            and all(expected == actual or expected == "[]" for expected, actual in zip(candidate, path))
            for candidate in allowed
        ) or path in allowed

    def walk(current: Any, path: tuple[str, ...]) -> None:
        if isinstance(current, dict):
            for key, child in current.items():
                if not isinstance(key, str):
                    _reject("CAPTURE_REJECTED_UNAUTHORIZED_SOURCE")
                child_path = path + (key,)
                if not permitted(child_path):
                    _reject("CAPTURE_REJECTED_UNAUTHORIZED_SOURCE")
                walk(child, child_path)
        elif isinstance(current, list):
            child_path = path + ("[]",)
            if not permitted(child_path):
                _reject("CAPTURE_REJECTED_UNAUTHORIZED_SOURCE")
            for child in current:
                walk(child, child_path)

    walk(value, ())

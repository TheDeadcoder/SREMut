"""Deterministic runtime identity for the SREMut scaffold."""

import sys
from importlib import metadata
from typing import Final

EXPECTED_PYTHON_VERSION: Final = "3.12.3"
EXPECTED_DISTRIBUTIONS: Final = {
    "PyYAML": "6.0.2",
    "kubernetes": "32.0.1",
    "jsonschema": "4.23.0",
}


def _python_version() -> str:
    return ".".join(str(part) for part in sys.version_info[:3])


def _distribution_version(name: str) -> str | None:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def status_document() -> dict[str, object]:
    """Return the closed, deterministic scaffold status document."""
    observed_python = _python_version()
    dependencies = {
        name: {
            "expected": expected,
            "observed": (observed := _distribution_version(name)),
            "matches": observed == expected,
        }
        for name, expected in EXPECTED_DISTRIBUTIONS.items()
    }
    python_identity = {
        "expected": EXPECTED_PYTHON_VERSION,
        "observed": observed_python,
        "matches": observed_python == EXPECTED_PYTHON_VERSION,
    }
    requirements_match = bool(python_identity["matches"]) and all(
        bool(identity["matches"]) for identity in dependencies.values()
    )
    return {
        "schema_version": 1,
        "component": "sremut-runner",
        "implementation_status": "SCAFFOLD_ONLY",
        "execution_allowed": False,
        "blocker_code": "RUNNER_IMPLEMENTATION_INCOMPLETE",
        "python": python_identity,
        "dependencies": dependencies,
        "runtime_requirements_match": requirements_match,
    }

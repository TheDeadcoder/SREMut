"""Tests for the closed SREMut scaffold boundary."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import sremut
from sremut.runtime_identity import status_document


EXPECTED_STATUS = {
    "schema_version": 1,
    "component": "sremut-runner",
    "implementation_status": "SCAFFOLD_ONLY",
    "execution_allowed": False,
    "blocker_code": "RUNNER_IMPLEMENTATION_INCOMPLETE",
    "python": {
        "expected": "3.12.3",
        "observed": "3.12.3",
        "matches": True,
    },
    "dependencies": {
        "PyYAML": {
            "expected": "6.0.2",
            "observed": "6.0.2",
            "matches": True,
        },
        "kubernetes": {
            "expected": "32.0.1",
            "observed": "32.0.1",
            "matches": True,
        },
        "jsonschema": {
            "expected": "4.23.0",
            "observed": "4.23.0",
            "matches": True,
        },
    },
    "runtime_requirements_match": True,
}
EXPECTED_STDOUT = (
    json.dumps(EXPECTED_STATUS, sort_keys=True, separators=(",", ":")) + "\n"
)


def _environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return environment


def _module(
    *arguments: str,
    environment: dict[str, str] | None = None,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-B", "-m", "sremut", *arguments],
        check=False,
        capture_output=True,
        text=True,
        env=environment or _environment(),
        cwd=cwd,
    )


def _console(*arguments: str) -> subprocess.CompletedProcess[str]:
    executable = Path(sys.executable).with_name("sremut")
    return subprocess.run(
        [str(executable), *arguments],
        check=False,
        capture_output=True,
        text=True,
        env=_environment(),
    )


class ScaffoldTests(unittest.TestCase):
    def test_package_version(self) -> None:
        self.assertEqual(sremut.__version__, "0.1.0.dev0")

    def test_exact_status_document(self) -> None:
        self.assertEqual(status_document(), EXPECTED_STATUS)

    def test_execution_is_explicitly_blocked(self) -> None:
        document = status_document()
        self.assertIs(document["execution_allowed"], False)
        self.assertEqual(document["implementation_status"], "SCAFFOLD_ONLY")
        self.assertEqual(
            document["blocker_code"],
            "RUNNER_IMPLEMENTATION_INCOMPLETE",
        )

    def test_exact_runtime_versions(self) -> None:
        document = status_document()
        self.assertEqual(document["python"]["observed"], "3.12.3")
        expected_versions = {
            "PyYAML": "6.0.2",
            "kubernetes": "32.0.1",
            "jsonschema": "4.23.0",
        }
        for name, version in expected_versions.items():
            with self.subTest(name=name):
                self.assertEqual(
                    document["dependencies"][name]["observed"],
                    version,
                )

    def test_module_status_invocation(self) -> None:
        completed = _module("status", "--json")
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, EXPECTED_STDOUT)
        self.assertEqual(completed.stderr, "")
        self.assertEqual(json.loads(completed.stdout), EXPECTED_STATUS)

    def test_console_status_invocation(self) -> None:
        completed = _console("status", "--json")
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, EXPECTED_STDOUT)
        self.assertEqual(completed.stderr, "")

    def test_repeated_status_is_byte_identical(self) -> None:
        first = _module("status", "--json")
        second = _module("status", "--json")
        self.assertEqual(first.returncode, 0)
        self.assertEqual(second.returncode, 0)
        self.assertEqual(first.stdout.encode(), second.stdout.encode())

    def test_unknown_operational_commands_are_rejected(self) -> None:
        for command in (
            "run",
            "execute",
            "mutate",
            "inject",
            "challenge",
            "restore",
        ):
            with self.subTest(command=command):
                completed = _module(command)
                self.assertNotEqual(completed.returncode, 0)
                self.assertEqual(completed.stdout, "")
                self.assertIn("usage:", completed.stderr)

    def test_imports_do_not_load_kubernetes_modules(self) -> None:
        source = (
            "import json,sys;"
            "import sremut,sremut.cli,sremut.runtime_identity;"
            "print(json.dumps(sorted(name for name in sys.modules "
            "if name == 'kubernetes' or name.startswith('kubernetes.'))))"
        )
        completed = subprocess.run(
            [sys.executable, "-B", "-c", source],
            check=False,
            capture_output=True,
            text=True,
            env=_environment(),
        )
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "[]\n")
        self.assertEqual(completed.stderr, "")

    def test_nonexistent_kubeconfig_does_not_affect_status(self) -> None:
        environment = _environment()
        with tempfile.TemporaryDirectory() as directory:
            environment["KUBECONFIG"] = str(
                Path(directory) / "does-not-exist"
            )
            completed = _module(
                "status",
                "--json",
                environment=environment,
            )
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, EXPECTED_STDOUT)
        self.assertEqual(completed.stderr, "")

    def test_status_creates_no_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            working_directory = Path(directory)
            before = tuple(working_directory.iterdir())
            completed = _module(
                "status",
                "--json",
                cwd=working_directory,
            )
            after = tuple(working_directory.iterdir())
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(before, after)

    def test_sensitive_environment_value_is_not_emitted(self) -> None:
        sentinel = "SREMUT-DO-NOT-EMIT-7d415819"
        environment = _environment()
        environment["SREMUT_SENSITIVE_SENTINEL"] = sentinel
        completed = _module(
            "status",
            "--json",
            environment=environment,
        )
        self.assertEqual(completed.returncode, 0)
        self.assertNotIn(sentinel, completed.stdout)
        self.assertNotIn(sentinel, completed.stderr)

    def test_default_invocation_fails_safely(self) -> None:
        completed = _module()
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "")
        self.assertIn("usage:", completed.stderr)

    def test_status_requires_json_flag(self) -> None:
        completed = _module("status")
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "")
        self.assertIn("usage:", completed.stderr)


if __name__ == "__main__":
    unittest.main()

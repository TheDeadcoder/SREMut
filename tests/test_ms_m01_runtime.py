"""Offline tests for the MS-M01 production runtime composition.

Nothing in this module contacts a cluster, reads a kubeconfig, launches a
subprocess, executes a workload, or invokes the stock oracle.  The read
transport, mutation transport, challenge executor, workload source, and
original-oracle invoker are all injected fakes; the runtime under test is the
real one, wired to the real `Journal`, `EvidenceStore`, `GuardedMutationSession`,
`ReadOnlyKubernetesClient`, workload parser, and sealing path.
"""

from __future__ import annotations

import copy
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest

from sremut.canonical_json import canonical_json_bytes
from sremut.evidence import EvidenceStore
from sremut.journal import Journal
from sremut.kubernetes_mutation import (
    MutationTransportResult,
    VerifiedRunnerBundle,
)
from sremut.kubernetes_readonly import KubernetesConsumer, PodSelector
from sremut.ms_m01_attempt import (
    AttemptIdentity,
    MsM01AttemptError,
    PendingMutation,
    TerminalOutcome,
)
from sremut.ms_m01_runtime import (
    ChallengeExecution,
    ConsumerBinding,
    MsM01RuntimeError,
    OracleExecution,
    WorkloadSnapshot,
    compose_ms_m01_attempt,
    publish_run_identity_start,
    reconstruct_pending_initial_deletion,
)
from sremut.policy_runtime import (
    POLICY_V1_2_ID,
    POLICY_V1_2_MANIFEST_SHA256,
    load_v1_2_policy_bundle,
)
from sremut.workload_evidence import WorkloadHistoryEntry

import tests.test_kubernetes_mutation as mutation_tests


def mod_file() -> str:
    import sremut.ms_m01_runtime as module

    return module.__file__


ROOT = Path(__file__).resolve().parents[1]
RUN_ID = "sremut-ms-m01-r01-a01-abcdef123456"
ATTEMPT_ID = "a01"
BOOT = "123e4567-e89b-12d3-a456-426614174000"
REGISTRY = "sremut/missing-service-social-network/pilot-mutants-v1"


ALIAS_KEYS = (
    "SREMUT_REPOSITORY", "SREGYM_REPOSITORY", "SREGYM_APPLICATIONS_REPOSITORY",
    "EXECUTION_PROFILE", "CONTRACT", "EXECUTION_PROFILE_SCHEMA",
    "EVIDENCE_POLICY_SCHEMA", "PYPROJECT", "UV_LOCK", "ORIGINAL_ORACLE_EXECUTABLE",
    "ATTEMPT_ROOT", "WORKLOAD_HISTORY", "BOOT_ID_SOURCE", "KUBECONFIG_HASH_SOURCE",
    "KUBECTL_CACHE_SNAPSHOT",
)
IDENTITY_HEX = "b" * 64


def load_policy():
    """The committed, authenticated evidence-policy v1.2 bundle."""
    return load_v1_2_policy_bundle(
        ROOT / "policies/missing_service_social_network/evidence-capture-v1.2.yaml",
        ROOT / "schemas/evidence-capture-policy-v1.2.schema.json",
        ROOT / "EVIDENCE_CAPTURE_POLICY_V1_2_SHA256SUMS",
        expected_manifest_sha256=POLICY_V1_2_MANIFEST_SHA256,
    )


def deployment(name: str, replicas: int) -> dict:
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": mutation_tests.metadata(name, "social-network", f"{name}-uid", "30"),
        "spec": {"replicas": replicas},
        "status": {"replicas": replicas, "readyReplicas": replicas},
    }


def workload_pod() -> dict:
    value = mutation_tests.pod("wrk2-job-abc", "wrk2-uid", "50")
    value["metadata"]["labels"] = {"job-name": "wrk2-job"}
    return value


def endpoint_slice() -> dict:
    return {
        "apiVersion": "discovery.k8s.io/v1",
        "kind": "EndpointSlice",
        "metadata": mutation_tests.metadata(
            "user-service-abc", "social-network", "slice-uid", "40",
            labels={"kubernetes.io/service-name": "user-service"},
        ),
        "addressType": "IPv4",
        "endpoints": [{"addresses": ["10.0.0.2"], "conditions": {"ready": True}}],
        "ports": [{"name": "thrift", "port": 9090, "protocol": "TCP"}],
    }


class NotFound(Exception):
    """Shape-compatible with the generated client's 404, without importing it."""

    def __init__(self) -> None:
        super().__init__("not found")
        self.status = 404
        self.body = '{"kind":"Status","code":404}'


class ScenarioReadTransport:
    """A scripted read transport: healthy, then service-absent, then restored.

    Object versions advance the way a real API server advances them: every
    observation of a mutated object carries a strictly higher resourceVersion,
    and a recreated Service gets a fresh UID.  Holding them static would make
    successive projections byte-identical, which is not how a cluster behaves.
    """

    def __init__(self) -> None:
        self.service_present = True
        self.slices_present = True
        self.service_generation = 0
        self.version = 100
        self.pods = mutation_tests.list_result(
            [
                mutation_tests.pod("user-service-z", "pod-z", "11"),
                mutation_tests.pod("user-service-a", "pod-a", "12"),
            ]
        )
        self.deployments = mutation_tests.list_result([deployment("user-service", 1)])
        self.calls: list[str] = []

    def _next_version(self) -> str:
        self.version += 1
        return str(self.version)

    def touch_deployments(self) -> None:
        fresh = copy.deepcopy(self.deployments)
        for item in fresh["items"]:
            item["metadata"]["resourceVersion"] = self._next_version()
        self.deployments = fresh

    # -- the seven frozen generated-client methods --------------------------
    def read_namespaced_deployment(self, name, namespace, timeout_seconds):
        raise AssertionError("unused")

    def list_namespaced_deployment(self, namespace, label_selector, field_selector, timeout_seconds):
        self.calls.append("list_namespaced_deployment")
        self.touch_deployments()
        return copy.deepcopy(self.deployments)

    def read_namespaced_replica_set(self, name, namespace, timeout_seconds):
        raise AssertionError("unused")

    def read_namespaced_pod(self, name, namespace, timeout_seconds):
        self.calls.append("read_namespaced_pod")
        return copy.deepcopy(self.pods["items"][0])

    def list_namespaced_pod(self, namespace, label_selector, field_selector, timeout_seconds):
        self.calls.append("list_namespaced_pod")
        if label_selector == "job-name=wrk2-job":
            entry = workload_pod()
            entry["metadata"]["resourceVersion"] = self._next_version()
            return copy.deepcopy(mutation_tests.list_result([entry]))
        fresh = copy.deepcopy(self.pods)
        for item in fresh["items"]:
            item["metadata"]["resourceVersion"] = self._next_version()
        self.pods = fresh
        return copy.deepcopy(fresh)

    def read_namespaced_service(self, name, namespace, timeout_seconds):
        self.calls.append("read_namespaced_service")
        if not self.service_present:
            raise NotFound()
        value = copy.deepcopy(mutation_tests.service())
        value["metadata"]["uid"] = f"service-uid-{self.service_generation}"
        value["metadata"]["resourceVersion"] = self._next_version()
        return value

    def list_namespaced_endpoint_slice(self, namespace, label_selector, field_selector, timeout_seconds):
        self.calls.append("list_namespaced_endpoint_slice")
        items = []
        if self.slices_present:
            entry = endpoint_slice()
            entry["metadata"]["name"] = f"user-service-{self.service_generation}"
            entry["metadata"]["uid"] = f"slice-uid-{self.service_generation}"
            entry["metadata"]["resourceVersion"] = self._next_version()
            items.append(entry)
        return copy.deepcopy(mutation_tests.list_result(items))


class ScenarioMutationTransport:
    """Applies each guarded mutation to the scripted read transport's state."""

    def __init__(self, attempt_root: Path, reads: ScenarioReadTransport) -> None:
        self.attempt_root = Path(attempt_root)
        self.reads = reads
        self.calls: list[str] = []
        self.response = MutationTransportResult(200, None, None, "Success")
        self.failure: Exception | None = None
        self.replacement_uid = "pod-new"
        self.challenge_created = 0

    def _guard(self, method: str):
        ledger = self.attempt_root / "journal/mutation-events.jsonl"
        if not ledger.exists() or b'"event":"INTENT_DURABLE"' not in ledger.read_bytes():
            raise RuntimeError("intent-not-durable")
        self.calls.append(method)
        if self.failure is not None:
            raise self.failure
        return self.response

    def delete_namespaced_service(self, name, namespace, body, timeout_seconds):
        result = self._guard("delete_namespaced_service")
        self.reads.service_present = False
        self.reads.slices_present = False
        return result

    def delete_namespaced_pod(self, name, namespace, body, timeout_seconds):
        self._guard("delete_namespaced_pod")
        items = [
            item
            for item in self.reads.pods["items"]
            if item["metadata"]["name"] != name
        ]
        fresh = mutation_tests.pod(f"{name}-r", self.replacement_uid, "99")
        self.reads.pods = mutation_tests.list_result(items + [fresh])
        return MutationTransportResult(200, None, None, "Success")

    def create_namespaced_service(self, namespace, body, timeout_seconds):
        self._guard("create_namespaced_service")
        self.reads.service_present = True
        self.reads.slices_present = True
        self.reads.service_generation += 1
        return MutationTransportResult(201, None, None, "Success")

    def create_namespaced_pod(self, namespace, body, timeout_seconds):
        self._guard("create_namespaced_pod")
        self.challenge_created += 1
        return MutationTransportResult(
            201, f"challenge-uid-{self.challenge_created}", "77", "Success"
        )


class RecordingChallenge:
    """Bounded challenge executor; performs no I/O of any kind."""

    def __init__(self, *, completed: bool = True) -> None:
        self.completed = completed
        self.calls: list[tuple[str, int]] = []

    def execute(self, *, run_id, attempt_id, phase, ordinal, pod_name, pod_uid):
        self.calls.append((phase, ordinal))
        return ChallengeExecution(
            completed=self.completed,
            exit_status=0 if self.completed else 1,
            stdout=b"connected\n" if self.completed else b"",
            stderr=b"",
            failure_classification=None if self.completed else "HARNESS_TIMING_FAILURE",
        )


def wrk_log(number: int = 50, failure: bool = False) -> str:
    rows = [
        "-----------------------------------",
        f"  {number} requests in 10.00s, 1.00KB read",
    ]
    if failure:
        rows.append(f"Non-2xx or 3xx responses: {number}")
    rows.extend(["Requests/sec: 5.00", "Transfer/sec: 1.00KB"])
    return "\n".join(rows)


def history(
    count: int, *, failures: int = 0, start: float = 1.0
) -> tuple[WorkloadHistoryEntry, ...]:
    entries = []
    for index in range(count):
        failure = index < failures
        entries.append(
            WorkloadHistoryEntry(
                start + index, 50, wrk_log(50, failure), not failure
            )
        )
    return tuple(entries)


class RecordingWorkload:
    """Bounded workload source; returns pre-captured history, runs nothing."""

    def __init__(self, pod_reference, *, fresh: int = 2, failures: int = 0) -> None:
        self.pod_reference = pod_reference
        self.fresh = fresh
        self.failures = failures
        self.calls: list[tuple[str, int]] = []

    def snapshot(self, *, run_id, attempt_id, phase, ordinal):
        self.calls.append((phase, ordinal))
        before = history(2)
        after = before + history(self.fresh, failures=self.failures, start=3.0)
        return WorkloadSnapshot(
            before=before,
            after=after,
            pod_name="wrk2-job-abc",
            pod_uid="wrk2-uid",
            container_restart_count=0,
            pod_projection_reference=self.pod_reference,
        )


class RecordingOracle:
    """Bounded original-oracle invoker; spawns no subprocess."""

    def __init__(self, input_reference, result_reference, *, returned: bool | None = True) -> None:
        self.input_reference = input_reference
        self.result_reference = result_reference
        self.returned = returned
        self.calls = 0

    def invoke(self, *, run_id, attempt_id, invocation_ordinal, kubernetes_context, namespace, captured_replica_baseline):
        self.calls += 1
        if self.returned is None:
            return OracleExecution(
                execution_classification="TIMEOUT",
                returned_boolean=None,
                raw_result=None,
                exit_status=None,
                input_reference=self.input_reference,
                result_reference=self.result_reference,
            )
        return OracleExecution(
            execution_classification="RETURNED_TRUE" if self.returned else "RETURNED_FALSE",
            returned_boolean=self.returned,
            raw_result={"success": self.returned},
            exit_status=0,
            input_reference=self.input_reference,
            result_reference=self.result_reference,
        )


class RuntimeCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.policy = load_policy()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="sremut-runtime-test-")
        self.base = Path(self.temporary.name)
        self.attempt = self.base / "attempt"
        self.index = self.base / "run-index"
        self.host = self.base / "host-lock"
        for path in (self.attempt, self.index, self.host):
            path.mkdir(mode=0o700)
        self.reads = ScenarioReadTransport()
        self.mutations = ScenarioMutationTransport(self.attempt, self.reads)
        manifest = b"a" * 64 + b"  src/sremut/example.py\n"
        self.runner = VerifiedRunnerBundle.authenticate(
            {
                "manifest_sha256": hashlib.sha256(manifest).hexdigest(),
                "bundle_sha256": hashlib.sha256(manifest).hexdigest(),
                "git_commit": "1" * 40,
                "git_tree": "2" * 40,
                "annotated_tag_name": "fixture-runner-v1",
                "annotated_tag_object": "3" * 40,
                "pyproject_sha256": "4" * 64,
                "uv_lock_sha256": "5" * 64,
            },
            manifest,
        )
        self.counter = 0
        bindings = self.policy.policy["bindings"]
        self.identity = AttemptIdentity(
            run_id=RUN_ID,
            attempt_id=ATTEMPT_ID,
            mutant_id="MS-M01",
            repetition=1,
            attempt_number=1,
            registry_id=REGISTRY,
            execution_profile_tag_object=bindings["execution_profile"]["tag_object"],
            execution_profile_sha256=bindings["execution_profile"]["sha256"],
            contract_sha256=bindings["contract"]["sha256"],
        )
        # The START run identity is published through the production helper and
        # cited on the attempt's first journal publication, exactly as the real
        # preflight will.
        self.run_identity_start = publish_run_identity_start(
            policy=self.policy,
            identity=self.identity,
            attempt_root=self.attempt,
            runner_bundle=self.runner,
            boot_identity=BOOT,
            created_utc=self._utc(),
            monotonic_ns=self._monotonic(),
            source_alias_sha256={key: IDENTITY_HEX for key in ALIAS_KEYS},
            kubeconfig_content_sha256=IDENTITY_HEX,
            kubectl_default_cache_before_sha256=IDENTITY_HEX,
        )
        self._seed_journal()
        self.references = self._seed_references()

    def tearDown(self):
        self.temporary.cleanup()

    def _monotonic(self) -> int:
        self.counter += 1
        return self.counter

    def _utc(self) -> str:
        return "2026-08-20T10:00:00.123456789Z"

    def _seed_journal(self) -> None:
        start = self.run_identity_start
        with Journal(self.attempt, self.policy, RUN_ID, ATTEMPT_ID) as journal:
            for index, transition in enumerate(
                (
                    "CREATED->PREFLIGHT_PASS",
                    "PREFLIGHT_PASS->HEALTHY_STATE_CAPTURED",
                )
            ):
                journal.append_state_transition(
                    transition,
                    utc_time=self._utc(),
                    monotonic_ns=self._monotonic(),
                    boot_identity=BOOT,
                    # START is cited on the initial publication record.
                    descriptor_sha256=(start.descriptor_sha256,) if index == 0 else (),
                )

    def _seed_references(self) -> dict:
        """Publish the pod projection and oracle evidence the fakes hand back."""
        from sremut.kubernetes_readonly import ReadOnlyKubernetesClient

        reader = ReadOnlyKubernetesClient(
            policy=self.policy,
            transport=self.reads,
            context="kind-kind",
            namespace="social-network",
            timeout_seconds=7,
            run_id=RUN_ID,
            attempt_id=ATTEMPT_ID,
        )
        capture = reader.list_pods(
            PodSelector.WORKLOAD, consumer=KubernetesConsumer.LIVE_PREFLIGHT
        )
        common = {
            "run_id": RUN_ID,
            "attempt_id": ATTEMPT_ID,
            "created_utc": self._utc(),
            "monotonic_ns": self._monotonic(),
            "boot_identity": BOOT,
        }
        with EvidenceStore(self.attempt, self.policy, RUN_ID, ATTEMPT_ID) as store:
            request_ref = store.publish_descriptor(
                capture.request_evidence.role,
                capture.request_evidence.publication_metadata(**common),
            )
            request_bytes = store.resolve(request_ref)[1]
            metadata = dict(
                capture.response_evidence.publication_metadata(
                    **{**common, "monotonic_ns": self._monotonic()},
                    request_identity_reference=request_ref,
                    request_identity_descriptor_bytes=request_bytes,
                )
            )
            metadata["capture_state"] = "HEALTHY_STATE_CAPTURED"
            pod_reference = store.publish_payload(
                capture.response_evidence.role,
                capture.response_evidence.payload_bytes,
                metadata,
            )
            oracle_input = store.publish_payload(
                "original_oracle_input",
                canonical_json_bytes({"kubernetes_context": "kind-kind"}),
                {**common, "monotonic_ns": self._monotonic(), "invocation_ordinal": 1},
            )
            stdout = store.publish_payload(
                "original_oracle_stdout",
                b"oracle-stdout\n",
                {
                    **common,
                    "monotonic_ns": self._monotonic(),
                    "invocation_reference": oracle_input.as_dict(),
                },
            )
            stderr = store.publish_payload(
                "original_oracle_stderr",
                b"oracle-stderr\n",
                {
                    **common,
                    "monotonic_ns": self._monotonic(),
                    "invocation_reference": oracle_input.as_dict(),
                },
            )
            oracle_result = store.publish_payload(
                "original_oracle_result",
                canonical_json_bytes({"success": True}),
                {
                    **common,
                    "monotonic_ns": self._monotonic(),
                    "input_reference": oracle_input.as_dict(),
                    "stdout_reference": stdout.as_dict(),
                    "stderr_reference": stderr.as_dict(),
                    "exit_status": 0,
                    "returned_boolean_or_exception": True,
                },
            )
        return {
            "pod": pod_reference,
            "oracle_input": oracle_input,
            "oracle_result": oracle_result,
        }

    def compose(self, **overrides):
        kwargs = dict(
            policy=self.policy,
            identity=self.identity,
            attempt_root=self.attempt,
            run_index_root=self.index,
            host_lock_root=self.host,
            read_transport=self.reads,
            mutation_transport=self.mutations,
            runner_bundle=self.runner,
            challenge_executor=RecordingChallenge(),
            workload_source=RecordingWorkload(self.references["pod"]),
            original_oracle_invoker=RecordingOracle(
                self.references["oracle_input"], self.references["oracle_result"]
            ),
            boot_identity=BOOT,
            utc_clock=self._utc,
            monotonic_clock=self._monotonic,
            timeout_seconds=7,
            run_identity_start=self.run_identity_start,
        )
        kwargs.update(overrides)
        return compose_ms_m01_attempt(**kwargs)


class CompositionTests(RuntimeCase):
    def test_composition_performs_no_external_call(self):
        """Construction must touch no transport and no cluster."""
        before_reads = list(self.reads.calls)
        with self.compose() as composition:
            self.assertEqual(self.reads.calls, before_reads)
            self.assertEqual(self.mutations.calls, [])
            self.assertIsNotNone(composition.attempt)

    def test_ordinary_import_loads_no_kubernetes_module_and_creates_no_file(self):
        """Import must be inert: no kubernetes module, no I/O, no side effects.

        Checked statically rather than by reloading the module: reloading would
        rebind this module's imported classes and silently break every later
        identity assertion.
        """
        import ast

        source = Path(mod_file()).read_text()
        tree = ast.parse(source)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        self.assertNotIn("kubernetes", imported)
        self.assertNotIn("subprocess", imported)
        self.assertNotIn("socket", imported)
        self.assertNotIn("urllib", imported)
        self.assertNotIn("kubernetes", sys.modules)

        # No module-level statement may execute anything but definitions.
        for node in tree.body:
            self.assertIsInstance(
                node,
                (
                    ast.Import, ast.ImportFrom, ast.ClassDef, ast.FunctionDef,
                    ast.Assign, ast.AnnAssign, ast.Expr,
                ),
                f"module-level statement executes at import: {type(node).__name__}",
            )
            if isinstance(node, ast.Expr):
                self.assertIsInstance(node.value, ast.Constant)
            if isinstance(node, ast.Assign):
                self.assertNotIsInstance(node.value, ast.Call)

        entries = sorted(path.name for path in self.base.iterdir())
        import sremut.ms_m01_runtime as reimported  # already in sys.modules

        self.assertIs(reimported.compose_ms_m01_attempt, compose_ms_m01_attempt)
        self.assertEqual(sorted(path.name for path in self.base.iterdir()), entries)

    def test_capabilities_satisfy_every_frozen_protocol(self):
        with self.compose() as composition:
            capabilities = composition.capabilities
            for name in (
                "authority", "prestate", "state", "mutations", "observations",
                "original_oracle", "challenge", "workload", "adjudication",
                "terminalizer",
            ):
                self.assertIsNotNone(getattr(capabilities, name))

    def test_wrong_identity_rejects(self):
        from dataclasses import replace

        other = replace(self.identity, repetition=2, run_id="sremut-ms-m01-r02-a01-abcdef123456")
        with self.compose() as composition:
            with self.assertRaises(Exception) as caught:
                composition.capabilities.authority.claim_once(other, resume=False)
            self.assertEqual(str(caught.exception), "RUNTIME_IDENTITY_MISMATCH")

    def test_wrong_consumer_rejects(self):
        with self.compose() as composition:
            binding = composition.binding
            with self.assertRaises(MsM01RuntimeError) as caught:
                binding.require_consumer(KubernetesConsumer.ADJUDICATOR)
            self.assertEqual(str(caught.exception), "RUNTIME_CONSUMER_MISMATCH")

    def test_wrong_operation_and_mutant_reject(self):
        with self.compose() as composition:
            binding = composition.binding
            with self.assertRaises(MsM01RuntimeError):
                binding.require_operation("SOME_OTHER_OPERATION")
            with self.assertRaises(MsM01RuntimeError):
                binding.require_mutant("MS-M02")

    def test_policy_or_identity_mismatch_refuses_to_compose(self):
        from dataclasses import replace

        broken = replace(self.identity, registry_id="sremut/other/registry")
        with self.assertRaises(MsM01RuntimeError) as caught:
            self.compose(identity=broken)
        self.assertEqual(str(caught.exception), "RUNTIME_POLICY_OR_IDENTITY_INVALID")

    def test_claim_is_single_use_unless_resumed(self):
        with self.compose() as composition:
            authority = composition.capabilities.authority
            authority.claim_once(self.identity, resume=False)
            with self.assertRaises(MsM01AttemptError):
                authority.claim_once(self.identity, resume=False)
            authority.claim_once(self.identity, resume=True)

    def test_global_stop_is_observed_from_the_run_index(self):
        with self.compose() as composition:
            self.assertFalse(composition.capabilities.authority.global_stop_active())
        (self.index / "terminal").mkdir(mode=0o700)
        (self.index / "terminal/global-stop.json").write_bytes(b"{}")
        with self.compose() as composition:
            self.assertTrue(composition.capabilities.authority.global_stop_active())


class LifecycleTests(RuntimeCase):
    def test_complete_attempt_reaches_finalized_admissible_evidence(self):
        with self.compose() as composition:
            result = composition.attempt.run()
        self.assertEqual(result.terminal_outcome, TerminalOutcome.FINALIZED)
        self.assertEqual(result.execution_status, "COMPLETED")
        self.assertEqual(result.original_oracle_verdict, "PASS")
        self.assertEqual(result.failure_classification, "COMPLETED")
        self.assertEqual(result.contract_verdict, "PASS")
        self.assertEqual(
            tuple(result.invariant_outcomes),
            ("MS-I1", "MS-I2", "MS-I3", "MS-I4", "MS-I5", "MS-I6"),
        )
        self.assertEqual(result.terminalization.seal_count, 1)
        self.assertEqual(result.terminalization.anchor_count, 1)
        self.assertIs(result.terminalization.anchor_authenticated, True)
        self.assertIs(result.terminalization.post_terminal_publication_rejected, True)
        self.assertIs(result.terminalization.post_terminal_transition_rejected, True)
        self.assertIs(result.terminalization.post_terminal_mutation_rejected, True)
        self.assertIs(result.terminalization.global_stop_created, False)
        self.assertTrue((self.attempt / "manifests/terminal.sha256").exists())

    def test_external_operations_occur_exactly_once_in_frozen_order(self):
        with self.compose() as composition:
            result = composition.attempt.run()
        self.assertEqual(
            self.mutations.calls,
            [
                "delete_namespaced_service",
                "create_namespaced_pod",
                "delete_namespaced_pod",
                "delete_namespaced_pod",
                "create_namespaced_service",
            ],
        )
        self.assertEqual(result.terminal_outcome, TerminalOutcome.FINALIZED)
        for operation, count in result.operation_counts.items():
            self.assertEqual(count, 1, f"{operation} dispatched {count} times")

    def test_api_acknowledgement_alone_cannot_activate_the_mutant(self):
        """A 200 from the API with the Service still present must not proceed."""
        self.mutations.delete_namespaced_service = (
            lambda name, namespace, body, timeout_seconds: self.mutations._guard(
                "delete_namespaced_service"
            )
        )
        with self.compose() as composition:
            result = composition.attempt.run()
        self.assertEqual(result.terminal_outcome, TerminalOutcome.ABORTED_SAFE)
        self.assertEqual(result.failure_classification, "MUTANT_ACTIVATION_FAILURE")
        self.assertIs(result.mutation_may_have_occurred, False)
        self.assertIn("INITIAL_EFFECT:OBSERVED_NOT_APPLIED_AFTER_RECOVERY", result.trace)

    def test_workload_window_substitution_rejects(self):
        class Substituting(RecordingWorkload):
            def snapshot(self, *, run_id, attempt_id, phase, ordinal):
                return super().snapshot(
                    run_id=run_id,
                    attempt_id="a02" if phase == "INITIAL_MUTANT_CHALLENGE" else attempt_id,
                    phase=phase,
                    ordinal=ordinal,
                )

        with self.compose(
            workload_source=Substituting(self.references["pod"])
        ) as composition:
            result = composition.attempt.run()
        self.assertEqual(result.terminal_outcome, TerminalOutcome.FINALIZED)
        self.assertEqual(result.failure_classification, "PROTOCOL_VIOLATION")

    def test_restoration_and_positive_control_are_mandatory(self):
        with self.compose() as composition:
            result = composition.attempt.run()
        self.assertIn("RESTORED_SERVICE_CREATION", result.trace)
        self.assertIn("RESTORATION_INVARIANTS_VERIFIED", result.trace)
        self.assertIn("RESTORATION_POSITIVE_CONTROL", result.trace)
        self.assertIn("create_namespaced_service", self.mutations.calls)

    def test_restoration_blocked_installs_the_global_stop(self):
        class NeverRestores(ScenarioMutationTransport):
            def create_namespaced_service(self, namespace, body, timeout_seconds):
                result = self._guard("create_namespaced_service")
                return result  # the Service is never actually restored

        transport = NeverRestores(self.attempt, self.reads)
        with self.compose(mutation_transport=transport) as composition:
            result = composition.attempt.run()
        self.assertEqual(result.terminal_outcome, TerminalOutcome.RESTORATION_BLOCKED)
        self.assertEqual(result.failure_classification, "RESTORATION_FAILURE")
        self.assertIs(result.terminalization.global_stop_created, True)
        self.assertTrue((self.index / "terminal/global-stop.json").exists())

    def test_dispatch_crash_before_receipt_is_reopened_without_redispatch(self):
        """A durable intent with no receipt resumes from the stores, not the API."""
        with self.compose() as first:
            first.capabilities.authority.claim_once(self.identity, resume=False)
            prestate = first.capabilities.prestate.authenticate(
                self.policy, self.identity
            )
            with self.assertRaises(Exception):
                first.capabilities.mutations.delete_user_service(
                    prestate.restoration_capability
                )
        ledger = (self.attempt / "journal/mutation-events.jsonl").read_bytes()
        self.assertIn(b'"event":"INTENT_DURABLE"', ledger)
        self.assertNotIn(b'"event":"RECEIPT_DURABLE"', ledger)

    def test_post_terminal_operations_all_reject(self):
        with self.compose() as composition:
            result = composition.attempt.run()
            terminal = result.terminalization
            self.assertIs(terminal.post_terminal_publication_rejected, True)
            self.assertIs(terminal.post_terminal_transition_rejected, True)
            self.assertIs(terminal.post_terminal_mutation_rejected, True)
            with self.assertRaises(Exception):
                composition.attempt.run()


if __name__ == "__main__":
    unittest.main()

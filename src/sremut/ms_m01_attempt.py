"""Closed offline orchestration kernel for one frozen MS-M01 attempt.

The kernel coordinates authenticated capabilities.  It deliberately contains
no Kubernetes, workload, subprocess, kubeconfig, network, or CLI construction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import re
from types import MappingProxyType
from typing import Any, Mapping, NoReturn, Protocol, runtime_checkable

from sremut.kubernetes_mutation import (
    KubernetesMutationError,
    MutationObservation,
    ObservedEffect,
)
from sremut.policy_runtime import (
    AuthenticatedPolicy,
    EXPECTED_HOOK_ORDER,
    POLICY_V1_2_ID,
    POLICY_V1_2_MANIFEST_SHA256,
)
from sremut.workload_evidence import recompute_stream_identity


MUTANT_ID = "MS-M01"
NAMESPACE = "social-network"
SERVICE_NAME = "user-service"
# MS-M01 runs under authenticated evidence-policy v1.2 only.  v1.1 remains the
# authority for everything already built against it; nothing here reads it.
POLICY_ID = POLICY_V1_2_ID
POLICY_MANIFEST_SHA256 = POLICY_V1_2_MANIFEST_SHA256
REGISTRY_ID = "sremut/missing-service-social-network/pilot-mutants-v1"
_RUN_ID = re.compile(r"^sremut-ms-m01-r0[1-3]-a0[1-2]-[0-9a-f]{12}$")
_ATTEMPT_ID = re.compile(r"^a0[1-2]$")

FROZEN_STATES = (
    "CREATED",
    "PREFLIGHT_PASS",
    "HEALTHY_STATE_CAPTURED",
    "MUTANT_INJECTED",
    "MUTANT_STATE_VERIFIED",
    "ORIGINAL_ORACLE_STARTED",
    "ORIGINAL_ORACLE_EVALUATED",
    "CONTRACT_EVALUATED",
    "RESTORE_STARTED",
    "RESTORE_VERIFIED",
    "FINALIZED",
    "ABORTED_SAFE",
    "RESTORATION_BLOCKED",
)

FROZEN_TRANSITIONS = frozenset(
    {
        "CREATED->PREFLIGHT_PASS",
        "CREATED->ABORTED_SAFE",
        "PREFLIGHT_PASS->HEALTHY_STATE_CAPTURED",
        "PREFLIGHT_PASS->ABORTED_SAFE",
        "HEALTHY_STATE_CAPTURED->MUTANT_INJECTED",
        "HEALTHY_STATE_CAPTURED->RESTORE_STARTED",
        "HEALTHY_STATE_CAPTURED->ABORTED_SAFE",
        "MUTANT_INJECTED->MUTANT_STATE_VERIFIED",
        "MUTANT_INJECTED->RESTORE_STARTED",
        "MUTANT_STATE_VERIFIED->ORIGINAL_ORACLE_STARTED",
        "MUTANT_STATE_VERIFIED->RESTORE_STARTED",
        "ORIGINAL_ORACLE_STARTED->ORIGINAL_ORACLE_EVALUATED",
        "ORIGINAL_ORACLE_STARTED->RESTORE_STARTED",
        "ORIGINAL_ORACLE_EVALUATED->CONTRACT_EVALUATED",
        "ORIGINAL_ORACLE_EVALUATED->RESTORE_STARTED",
        "CONTRACT_EVALUATED->RESTORE_STARTED",
        "RESTORE_STARTED->RESTORE_VERIFIED",
        "RESTORE_STARTED->RESTORATION_BLOCKED",
        "RESTORE_VERIFIED->FINALIZED",
    }
)

WORKLOAD_WINDOWS = MappingProxyType(
    {
        "INITIAL_MUTANT_CHALLENGE": 1,
        "POST_REPLACEMENT_PERSISTENCE": 2,
        "RESTORATION_POSITIVE_CONTROL": 3,
    }
)

#: The three frozen adjudication predicates, in the exact order MS-M01 reaches
#: them.  There is no aggregate predicate: `MS_M01_CONTRACT_V1` was never in the
#: frozen matrix, and the contract verdict is derived from these three rather
#: than published as a fourth adjudication.
PREDICATES = (
    "INITIAL_INVARIANT_EVALUATION",
    "REPLACEMENT_PERSISTENCE_EVALUATION",
    "RESTORATION_POSITIVE_CONTROL",
)
#: predicate -> (workload phase, authorizing state)
PREDICATE_PHASES = MappingProxyType(
    {
        "INITIAL_INVARIANT_EVALUATION": (
            "INITIAL_MUTANT_CHALLENGE", "ORIGINAL_ORACLE_EVALUATED"),
        "REPLACEMENT_PERSISTENCE_EVALUATION": (
            "POST_REPLACEMENT_PERSISTENCE", "CONTRACT_EVALUATED"),
        "RESTORATION_POSITIVE_CONTROL": (
            "RESTORATION_POSITIVE_CONTROL", "RESTORE_STARTED"),
    }
)

_TERMINAL = frozenset({"FINALIZED", "ABORTED_SAFE", "RESTORATION_BLOCKED"})
_CLASSIFICATIONS = frozenset(
    {
        "CONTRACT_REJECT",
        "ORIGINAL_ORACLE_REJECT",
        "MUTANT_ACTIVATION_FAILURE",
        "HARNESS_TIMING_FAILURE",
        "INFRASTRUCTURE_FAILURE",
        "RESTORATION_FAILURE",
        "PROTOCOL_VIOLATION",
        "INVALID_OR_EQUIVALENT_MUTANT",
        "COMPLETED",
    }
)
_INVARIANTS = ("MS-I1", "MS-I2", "MS-I3", "MS-I4", "MS-I5", "MS-I6")


@dataclass(slots=True)
class MsM01AttemptError(RuntimeError):
    """Stable orchestration failure without raw evidence or secret content."""

    code: str
    result: Any | None = field(default=None, repr=False, compare=False)

    def __str__(self) -> str:
        return self.code


def _reject(code: str, result: Any | None = None) -> NoReturn:
    raise MsM01AttemptError(code, result) from None


def _reference_digest(reference: Any) -> str | None:
    value = getattr(reference, "descriptor_sha256", None)
    if isinstance(value, str):
        return value
    if isinstance(reference, Mapping):
        value = reference.get("descriptor_sha256")
        return value if isinstance(value, str) else None
    return None


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(child) for key, child in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(child) for child in value)
    return value


class TerminalOutcome(str, Enum):
    FINALIZED = "FINALIZED"
    ABORTED_SAFE = "ABORTED_SAFE"
    RESTORATION_BLOCKED = "RESTORATION_BLOCKED"


@dataclass(frozen=True, slots=True)
class AttemptIdentity:
    run_id: str
    attempt_id: str
    mutant_id: str
    repetition: int
    attempt_number: int
    registry_id: str
    execution_profile_tag_object: str
    execution_profile_sha256: str
    contract_sha256: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.run_id, str)
            or _RUN_ID.fullmatch(self.run_id) is None
            or not isinstance(self.attempt_id, str)
            or _ATTEMPT_ID.fullmatch(self.attempt_id) is None
            or not self.run_id.startswith(
                f"sremut-ms-m01-r0{self.repetition}-a0{self.attempt_number}-"
            )
            or self.mutant_id != MUTANT_ID
            or type(self.repetition) is not int
            or self.repetition not in (1, 2, 3)
            or type(self.attempt_number) is not int
            or self.attempt_number not in (1, 2)
            or self.attempt_id != f"a0{self.attempt_number}"
            or not all(
                isinstance(value, str) and value
                for value in (
                    self.registry_id,
                    self.execution_profile_tag_object,
                    self.execution_profile_sha256,
                    self.contract_sha256,
                )
            )
        ):
            _reject("MS_M01_IDENTITY_INVALID")


@dataclass(frozen=True, slots=True)
class HealthyPrestate:
    policy_manifest_sha256: str
    run_id: str
    attempt_id: str
    namespace: str
    service_name: str
    service_uid: str
    service_resource_version: str
    captured_replica_baseline: Mapping[str, int] = field(repr=False)
    workload_stream_identity: str
    restoration_capability: Any = field(repr=False, compare=False)
    evidence_references: tuple[Any, ...] = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        baseline = dict(self.captured_replica_baseline)
        if (
            self.policy_manifest_sha256 != POLICY_MANIFEST_SHA256
            or _RUN_ID.fullmatch(self.run_id) is None
            or _ATTEMPT_ID.fullmatch(self.attempt_id) is None
            or self.namespace != NAMESPACE
            or self.service_name != SERVICE_NAME
            or not self.service_uid
            or not self.service_resource_version
            or not baseline
            or any(
                type(name) is not str
                or not name
                or type(count) is not int
                or count < 0
                for name, count in baseline.items()
            )
            or self.workload_stream_identity
            != recompute_stream_identity(
                self.run_id, self.attempt_id, policy_id=POLICY_ID
            )
            or not self.evidence_references
        ):
            _reject("HEALTHY_PRESTATE_INVALID")
        object.__setattr__(self, "captured_replica_baseline", _freeze(baseline))
        object.__setattr__(self, "evidence_references", tuple(self.evidence_references))


@dataclass(frozen=True, slots=True)
class PendingMutation:
    operation_id: str
    operation_kind: str
    captured_uid: str

    def __post_init__(self) -> None:
        if (
            self.operation_kind != "INITIAL_USER_SERVICE_DELETION"
            or not self.operation_id.startswith("INITIAL_USER_SERVICE_DELETION:")
            or not self.captured_uid
        ):
            _reject("PENDING_MUTATION_INVALID")


@dataclass(frozen=True, slots=True)
class CapturedPod:
    name: str
    namespace: str
    uid: str
    resource_version: str
    ready: bool
    terminal_deployment: str
    target: Any = field(repr=False, compare=False)
    evidence_references: tuple[Any, ...] = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if (
            not self.name
            or self.namespace != NAMESPACE
            or not self.uid
            or not self.resource_version
            or self.ready is not True
            or self.terminal_deployment != SERVICE_NAME
            or not self.evidence_references
        ):
            _reject("REPLACEMENT_TARGET_INVALID")


@dataclass(frozen=True, slots=True)
class ReplacementObservation:
    captured_uid: str
    replacement_name: str | None
    replacement_uid: str | None
    ready: bool
    ownership_to_user_service: bool
    capacity_preserved: bool
    evidence_references: tuple[Any, ...] = field(repr=False, compare=False)

    @property
    def valid(self) -> bool:
        return (
            bool(self.replacement_name)
            and bool(self.replacement_uid)
            and self.replacement_uid != self.captured_uid
            and self.ready is True
            and self.ownership_to_user_service is True
            and self.capacity_preserved is True
            and bool(self.evidence_references)
        )


@dataclass(frozen=True, slots=True)
class OriginalOracleOutcome:
    raw_result: dict[str, bool] | None
    returned_boolean: bool | None
    exit_status: int | None
    execution_classification: str
    evidence_references: tuple[Any, ...] = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        returned = self.execution_classification in {"RETURNED_TRUE", "RETURNED_FALSE"}
        if returned:
            if (
                type(self.raw_result) is not dict
                or set(self.raw_result) != {"success"}
                or type(self.raw_result["success"]) is not bool
                or self.returned_boolean is not self.raw_result["success"]
                or self.exit_status != 0
            ):
                _reject("ORIGINAL_ORACLE_OUTCOME_INVALID")
        elif (
            self.raw_result is not None
            or self.returned_boolean is not None
            or self.execution_classification
            not in {
                "ORACLE_EXCEPTION",
                "TIMEOUT",
                "INTERRUPTED",
                "NONZERO_EXIT",
                "INVALID_OUTPUT",
            }
        ):
            _reject("ORIGINAL_ORACLE_OUTCOME_INVALID")
        if not self.evidence_references:
            _reject("ORIGINAL_ORACLE_EVIDENCE_MISSING")

    @property
    def verdict(self) -> str:
        if self.returned_boolean is True:
            return "PASS"
        if self.returned_boolean is False:
            return "REJECT"
        if self.execution_classification in {"TIMEOUT", "INTERRUPTED"}:
            return "INTERRUPTED"
        return "NOT_EVALUATED"


@dataclass(frozen=True, slots=True)
class ChallengeRun:
    phase: str
    ordinal: int
    completed: bool
    failure_classification: str | None
    evidence_references: tuple[Any, ...] = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if (
            WORKLOAD_WINDOWS.get(self.phase) != self.ordinal
            or (self.completed is True and self.failure_classification is not None)
            or (
                self.completed is False
                and self.failure_classification
                not in {"HARNESS_TIMING_FAILURE", "INFRASTRUCTURE_FAILURE"}
            )
            or not self.evidence_references
        ):
            _reject("CHALLENGE_OUTCOME_INVALID")


@dataclass(frozen=True, slots=True)
class WorkloadWindowResult:
    phase: str
    ordinal: int
    run_id: str
    attempt_id: str
    stream_identity: str
    fresh_request_count: int
    failure_marker_count: int
    evidence_references: tuple[Any, ...] = field(repr=False, compare=False)
    #: The frozen `workload_window_adjudication_identity` this window produced.
    #: An adjudication for this phase must carry exactly this identity.
    window: Mapping[str, Any] = field(
        default_factory=dict, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        if (
            WORKLOAD_WINDOWS.get(self.phase) != self.ordinal
            or _RUN_ID.fullmatch(self.run_id) is None
            or _ATTEMPT_ID.fullmatch(self.attempt_id) is None
            or self.stream_identity
            != recompute_stream_identity(
                self.run_id, self.attempt_id, policy_id=POLICY_ID
            )
            or type(self.fresh_request_count) is not int
            or self.fresh_request_count < 0
            or type(self.failure_marker_count) is not int
            or self.failure_marker_count < 0
            or not self.evidence_references
        ):
            _reject("WORKLOAD_WINDOW_INVALID")
        object.__setattr__(self, "window", _freeze(dict(self.window)))

    @property
    def healthy(self) -> bool:
        return self.fresh_request_count >= 50 and self.failure_marker_count == 0


@dataclass(frozen=True, slots=True)
class PredicateAdjudication:
    """One published adjudication for one frozen predicate.

    Exactly three of these exist per completed attempt.  Each is backed by the
    payload evidence its own window produced, and each is published after its
    own `EVALUATION_AUTHORIZED` marker and after every raw reference it cites.
    """

    predicate_id: str
    value: bool
    raw_evidence_references: tuple[Any, ...] = field(repr=False, compare=False)
    adjudication_reference: Any = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if (
            self.predicate_id not in PREDICATES
            or type(self.value) is not bool
            or not self.raw_evidence_references
            or self.adjudication_reference is None
        ):
            _reject("PREDICATE_ADJUDICATION_INVALID")


@dataclass(frozen=True, slots=True)
class ContractEvaluation:
    """The derived MS-I1..MS-I6 contract verdict.

    Derived from the three published predicate adjudications and the raw
    observations behind them.  It is NOT itself published as an adjudication:
    the frozen matrix defines three predicates and no aggregate.
    """

    verdict: str
    invariant_outcomes: Mapping[str, str]
    raw_evidence_references: tuple[Any, ...] = field(repr=False, compare=False)
    adjudication_references: tuple[Any, ...] = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        outcomes = dict(self.invariant_outcomes)
        if (
            self.verdict not in {"PASS", "REJECT", "INCOMPLETE"}
            or tuple(outcomes) != _INVARIANTS
            or any(value not in {"PASS", "REJECT", "INCOMPLETE"} for value in outcomes.values())
            or not self.raw_evidence_references
            or not self.adjudication_references
            or (self.verdict == "PASS" and any(value != "PASS" for value in outcomes.values()))
            or (self.verdict == "REJECT" and "REJECT" not in outcomes.values())
        ):
            _reject("CONTRACT_ADJUDICATION_INVALID")
        object.__setattr__(self, "invariant_outcomes", _freeze(outcomes))


@dataclass(frozen=True, slots=True)
class RestorationVerification:
    safe_restored_state: bool
    service_semantics_restored: bool
    all_six_invariants_recovered: bool
    evidence_references: tuple[Any, ...] = field(repr=False, compare=False)

    @property
    def valid(self) -> bool:
        return (
            self.safe_restored_state is True
            and self.service_semantics_restored is True
            and self.all_six_invariants_recovered is True
            and bool(self.evidence_references)
        )


@dataclass(frozen=True, slots=True)
class TerminalizationResult:
    outcome: TerminalOutcome
    seal_count: int
    anchor_count: int
    anchor_authenticated: bool
    full_admissibility: bool
    hook_outcomes: tuple[str, ...]
    global_stop_created: bool
    post_terminal_publication_rejected: bool
    post_terminal_transition_rejected: bool
    post_terminal_mutation_rejected: bool

    def __post_init__(self) -> None:
        if (
            self.seal_count != 1
            or self.anchor_count != 1
            or self.anchor_authenticated is not True
            # The executed hooks, exactly as the dispatcher ran them.  An
            # ATTEMPT_VALIDATION_ENVELOPE_V1 candidate runs the five hooks the
            # frozen applicability matrix declares for it, not all twelve, so
            # the requirement is a non-empty subset in frozen order -- never a
            # claim that every hook ran.
            or (
                self.full_admissibility
                and (
                    not self.hook_outcomes
                    or any(hook not in EXPECTED_HOOK_ORDER for hook in self.hook_outcomes)
                    or list(self.hook_outcomes)
                    != [hook for hook in EXPECTED_HOOK_ORDER if hook in set(self.hook_outcomes)]
                )
            )
            or self.global_stop_created
            is not (self.outcome is TerminalOutcome.RESTORATION_BLOCKED)
            or self.post_terminal_publication_rejected is not True
            or self.post_terminal_transition_rejected is not True
            or self.post_terminal_mutation_rejected is not True
        ):
            _reject("TERMINALIZATION_INVALID")


@dataclass(frozen=True, slots=True)
class AttemptDraft:
    identity: AttemptIdentity
    terminal_outcome: TerminalOutcome
    execution_status: str
    original_oracle_verdict: str
    contract_verdict: str
    failure_classification: str
    invariant_outcomes: Mapping[str, str]
    trace: tuple[str, ...]
    operation_counts: Mapping[str, int]
    mutation_may_have_occurred: bool
    #: The published adjudications this attempt actually authorized, in order,
    #: and the exact union of the raw evidence they cite.  A terminal outcome
    #: that authorized nothing carries empty tuples.
    adjudication_references: tuple[Any, ...] = field(
        default=(), repr=False, compare=False
    )
    raw_evidence_references: tuple[Any, ...] = field(
        default=(), repr=False, compare=False
    )


@dataclass(frozen=True, slots=True)
class AttemptResult:
    identity: AttemptIdentity
    terminal_outcome: TerminalOutcome
    execution_status: str
    original_oracle_verdict: str
    contract_verdict: str
    failure_classification: str
    invariant_outcomes: Mapping[str, str]
    trace: tuple[str, ...]
    operation_counts: Mapping[str, int]
    mutation_may_have_occurred: bool
    terminalization: TerminalizationResult


@runtime_checkable
class AttemptAuthority(Protocol):
    def global_stop_active(self) -> bool: ...

    def claim_once(self, identity: AttemptIdentity, *, resume: bool) -> None: ...


@runtime_checkable
class PrestateAuthority(Protocol):
    def authenticate(
        self, policy: AuthenticatedPolicy, identity: AttemptIdentity
    ) -> HealthyPrestate: ...


@runtime_checkable
class AttemptState(Protocol):
    def current_state(self) -> str: ...

    def transition(self, transition: str, *, evidence_references: tuple[Any, ...]) -> None: ...

    def authorize_evaluation(self, predicate_id: str) -> None: ...

    def publish_evidence(self, label: str, references: tuple[Any, ...]) -> None: ...


@runtime_checkable
class MutationCapability(Protocol):
    def delete_user_service(self, restoration: Any) -> Any: ...

    def reconcile_pending(
        self,
        operation_id: str,
        operation_kind: str,
        captured_uid: str | None,
        observation: MutationObservation,
    ) -> Any: ...

    def reconcile(
        self,
        operation_kind: str,
        captured_uid: str | None,
        observation: MutationObservation,
    ) -> Any: ...

    def create_challenge_pod(self) -> Any: ...

    def delete_replacement_pod(self, target: Any) -> Any: ...

    def delete_challenge_pod(self, target: Any) -> Any: ...

    def restore_user_service(self, restoration: Any) -> Any: ...


@runtime_checkable
class ObservationCapability(Protocol):
    def observe_initial_service_deletion(self, dispatch: Any | None) -> MutationObservation: ...

    def service_structurally_absent(self) -> bool: ...

    def select_replacement_pod(self, prestate: HealthyPrestate) -> CapturedPod: ...

    def observe_replacement(
        self, captured: CapturedPod, dispatch: Any
    ) -> ReplacementObservation: ...

    def verify_restoration(self, prestate: HealthyPrestate) -> RestorationVerification: ...


@runtime_checkable
class OriginalOracleCapability(Protocol):
    def invoke_once(
        self, prestate: HealthyPrestate, identity: AttemptIdentity
    ) -> OriginalOracleOutcome: ...


@runtime_checkable
class ChallengeCapability(Protocol):
    def execute(
        self, phase: str, ordinal: int, challenge_target: Any
    ) -> ChallengeRun: ...


@runtime_checkable
class WorkloadCapability(Protocol):
    def evaluate(
        self,
        phase: str,
        ordinal: int,
        challenge: ChallengeRun | None,
        prestate: HealthyPrestate,
    ) -> WorkloadWindowResult: ...


@runtime_checkable
class AdjudicationCapability(Protocol):
    def adjudicate_initial_invariants(
        self,
        original_oracle: OriginalOracleOutcome,
        initial_challenge: ChallengeRun,
        initial_workload: WorkloadWindowResult,
    ) -> PredicateAdjudication: ...

    def adjudicate_replacement_persistence(
        self,
        captured_pod: CapturedPod,
        replacement: ReplacementObservation,
        replacement_challenge: ChallengeRun | None,
        replacement_workload: WorkloadWindowResult | None,
    ) -> PredicateAdjudication: ...

    def adjudicate_restoration_positive_control(
        self,
        verification: RestorationVerification,
        positive_workload: WorkloadWindowResult,
    ) -> PredicateAdjudication: ...

    def derive_contract(
        self,
        original_oracle: OriginalOracleOutcome,
        initial_challenge: ChallengeRun,
        initial_workload: WorkloadWindowResult,
        captured_pod: CapturedPod,
        replacement: ReplacementObservation,
        replacement_challenge: ChallengeRun | None,
        replacement_workload: WorkloadWindowResult | None,
        adjudications: tuple[PredicateAdjudication, ...],
    ) -> ContractEvaluation: ...


@runtime_checkable
class TerminalizationCapability(Protocol):
    def publish_retained_coverage(self) -> tuple[str, ...]: ...

    def publish_terminal_identity(self, outcome: TerminalOutcome) -> Any: ...

    def finalize(self, draft: AttemptDraft) -> TerminalizationResult: ...


@dataclass(frozen=True, slots=True)
class AttemptCapabilities:
    authority: AttemptAuthority
    prestate: PrestateAuthority
    state: AttemptState
    mutations: MutationCapability
    observations: ObservationCapability
    original_oracle: OriginalOracleCapability
    challenge: ChallengeCapability
    workload: WorkloadCapability
    adjudication: AdjudicationCapability
    terminalizer: TerminalizationCapability

    def __post_init__(self) -> None:
        protocols = (
            AttemptAuthority,
            PrestateAuthority,
            AttemptState,
            MutationCapability,
            ObservationCapability,
            OriginalOracleCapability,
            ChallengeCapability,
            WorkloadCapability,
            AdjudicationCapability,
            TerminalizationCapability,
        )
        values = (
            self.authority,
            self.prestate,
            self.state,
            self.mutations,
            self.observations,
            self.original_oracle,
            self.challenge,
            self.workload,
            self.adjudication,
            self.terminalizer,
        )
        if any(not isinstance(value, protocol) for value, protocol in zip(values, protocols)):
            _reject("ATTEMPT_CAPABILITY_MISSING")


class MsM01Attempt:
    """One-shot state-checked MS-M01 coordinator over injected capabilities."""

    __slots__ = (
        "_policy",
        "_identity",
        "_capabilities",
        "_pending",
        "_trace",
        "_counts",
        "_adjudications",
        "_raw",
        "_authorized",
        "_ran",
        "_state",
        "_mutation_may_have_occurred",
    )

    def __init__(
        self,
        *,
        policy: AuthenticatedPolicy,
        identity: AttemptIdentity,
        capabilities: AttemptCapabilities,
        pending_initial_deletion: PendingMutation | None = None,
    ) -> None:
        if (
            not isinstance(policy, AuthenticatedPolicy)
            or policy.manifest_sha256 != POLICY_MANIFEST_SHA256
            or policy.policy.get("policy_id") != POLICY_ID
            or not isinstance(identity, AttemptIdentity)
            or not isinstance(capabilities, AttemptCapabilities)
            or (
                pending_initial_deletion is not None
                and not isinstance(pending_initial_deletion, PendingMutation)
            )
        ):
            _reject("MS_M01_POLICY_OR_IDENTITY_INVALID")
        binding = policy.policy["bindings"]
        profile = binding["execution_profile"]
        contract = binding["contract"]
        if (
            identity.execution_profile_tag_object != profile["tag_object"]
            or identity.execution_profile_sha256 != profile["sha256"]
            or identity.contract_sha256 != contract["sha256"]
            or identity.registry_id != REGISTRY_ID
        ):
            _reject("MS_M01_POLICY_OR_IDENTITY_INVALID")
        self._policy = policy
        self._identity = identity
        self._capabilities = capabilities
        self._pending = pending_initial_deletion
        self._trace: list[str] = []
        self._counts: dict[str, int] = {}
        self._adjudications: list[PredicateAdjudication] = []
        self._raw: list[Any] = []
        self._authorized: list[str] = []
        self._ran = False
        self._state = ""
        self._mutation_may_have_occurred = False

    def _event(self, value: str) -> None:
        self._trace.append(value)

    def _count(self, operation: str) -> None:
        count = self._counts.get(operation, 0) + 1
        if count != 1:
            _reject("DUPLICATE_OPERATION_FORBIDDEN")
        self._counts[operation] = count

    def _authorize(self, predicate_id: str) -> None:
        """Raise the frozen `EVALUATION_AUTHORIZED` marker for one predicate.

        The marker is raised BEFORE any evidence this predicate will cite is
        published to the journal, and each predicate is authorized at most once.
        """
        if predicate_id not in PREDICATES or predicate_id in self._authorized:
            _reject("EVALUATION_AUTHORIZATION_INVALID")
        expected_state = PREDICATE_PHASES[predicate_id][1]
        if self._state != expected_state:
            _reject("EVALUATION_AUTHORIZATION_STATE_INVALID")
        self._capabilities.state.authorize_evaluation(predicate_id)
        self._authorized.append(predicate_id)
        self._event(f"EVALUATION_AUTHORIZED:{predicate_id}")

    def _publish_window_evidence(
        self,
        predicate_id: str,
        workload: WorkloadWindowResult | None,
        *observations: Any,
    ) -> None:
        """Record the publication of everything this predicate will cite.

        The frozen workload hook requires a strict publication order
        (pod <= prefix < boundary <= raw <= parse < adjudication), which needs
        two distinct records before the adjudication's own.  Both are state
        neutral, so neither invents a transition the frozen machine lacks.
        """
        references: tuple[Any, ...] = ()
        if workload is not None:
            references = tuple(workload.evidence_references)
            if len(references) != 5:
                _reject("WORKLOAD_WINDOW_INVALID")
        # Everything this predicate will cite, recorded before its adjudication:
        # the challenge streams and the Kubernetes observations belong to the
        # predicate just as much as the workload window does.
        observed: tuple[Any, ...] = ()
        for source in observations:
            observed += tuple(getattr(source, "evidence_references", ()) or ())
        self._capabilities.state.publish_evidence(
            f"{predicate_id}:capture", references[:2] + observed
        )
        if references:
            self._capabilities.state.publish_evidence(
                f"{predicate_id}:window", references[2:]
            )

    def _record_adjudication(
        self, predicate_id: str, adjudication: Any
    ) -> PredicateAdjudication:
        if (
            not isinstance(adjudication, PredicateAdjudication)
            or adjudication.predicate_id != predicate_id
        ):
            _reject("PREDICATE_ADJUDICATION_INVALID")
        self._adjudications.append(adjudication)
        seen = {
            digest
            for digest in (_reference_digest(item) for item in self._raw)
            if digest is not None
        }
        for reference in adjudication.raw_evidence_references:
            digest = _reference_digest(reference)
            if digest is None or digest in seen:
                continue
            seen.add(digest)
            self._raw.append(reference)
        self._event(f"ADJUDICATION_PUBLISHED:{predicate_id}")
        return adjudication

    def _transition(self, target: str, *references: Any) -> None:
        observed = self._capabilities.state.current_state()
        if observed != self._state:
            _reject("ATTEMPT_STATE_AUTHORITY_MISMATCH")
        transition = f"{self._state}->{target}"
        if transition not in FROZEN_TRANSITIONS:
            _reject("ATTEMPT_STATE_TRANSITION_INVALID")
        self._capabilities.state.transition(
            transition, evidence_references=tuple(references)
        )
        if self._capabilities.state.current_state() != target:
            _reject("ATTEMPT_STATE_AUTHORITY_MISMATCH")
        self._state = target
        self._event(transition)

    @staticmethod
    def _dispatch_kind(value: Any, expected: str) -> None:
        if getattr(value, "operation_kind", None) != expected:
            _reject("MUTATION_OPERATION_SUBSTITUTION")

    @staticmethod
    def _reconciliation(value: Any) -> tuple[ObservedEffect, bool]:
        classification = getattr(value, "classification", None)
        retry = getattr(value, "retry_permitted", None)
        safe = getattr(value, "safe_to_continue", None)
        if (
            not isinstance(classification, ObservedEffect)
            or type(retry) is not bool
            or type(safe) is not bool
            or (classification is ObservedEffect.OBSERVED_APPLIED_AFTER_RECOVERY)
            is not safe
        ):
            _reject("MUTATION_RECONCILIATION_INVALID")
        return classification, safe

    def _terminalize(
        self,
        outcome: TerminalOutcome,
        *,
        original_verdict: str,
        contract_verdict: str,
        classification: str,
        invariants: Mapping[str, str] | None = None,
    ) -> AttemptResult:
        if self._state != outcome.value:
            _reject("TERMINAL_STATE_MISMATCH")
        if classification not in _CLASSIFICATIONS:
            _reject("FAILURE_CLASSIFICATION_INVALID")
        values = dict(invariants or {name: "INCOMPLETE" for name in _INVARIANTS})
        draft = AttemptDraft(
            identity=self._identity,
            terminal_outcome=outcome,
            execution_status=(
                "COMPLETED" if outcome is TerminalOutcome.FINALIZED else outcome.value
            ),
            original_oracle_verdict=original_verdict,
            contract_verdict=contract_verdict,
            failure_classification=classification,
            invariant_outcomes=_freeze(values),
            trace=tuple(self._trace),
            operation_counts=_freeze(dict(self._counts)),
            mutation_may_have_occurred=self._mutation_may_have_occurred,
            adjudication_references=tuple(
                item.adjudication_reference for item in self._adjudications
            ),
            raw_evidence_references=tuple(self._raw),
        )
        terminal = self._capabilities.terminalizer.finalize(draft)
        if terminal.outcome is not outcome:
            _reject("TERMINALIZATION_INVALID")
        self._event("TERMINAL_SEALED_AND_ANCHORED")
        return AttemptResult(
            identity=draft.identity,
            terminal_outcome=draft.terminal_outcome,
            execution_status=draft.execution_status,
            original_oracle_verdict=draft.original_oracle_verdict,
            contract_verdict=draft.contract_verdict,
            failure_classification=draft.failure_classification,
            invariant_outcomes=draft.invariant_outcomes,
            trace=tuple(self._trace),
            operation_counts=draft.operation_counts,
            mutation_may_have_occurred=draft.mutation_may_have_occurred,
            terminalization=terminal,
        )

    def _block_restoration(
        self,
        *,
        original_verdict: str,
        contract_verdict: str,
        invariants: Mapping[str, str] | None,
    ) -> AttemptResult:
        if self._state != "RESTORE_STARTED":
            self._transition("RESTORE_STARTED")
        self._capabilities.terminalizer.publish_retained_coverage()
        terminal_identity = self._capabilities.terminalizer.publish_terminal_identity(
            TerminalOutcome.RESTORATION_BLOCKED
        )
        self._transition("RESTORATION_BLOCKED", terminal_identity)
        return self._terminalize(
            TerminalOutcome.RESTORATION_BLOCKED,
            original_verdict=original_verdict,
            contract_verdict=contract_verdict,
            classification="RESTORATION_FAILURE",
            invariants=invariants,
        )

    def _restore(
        self,
        prestate: HealthyPrestate,
        challenge_target: Any | None,
        *,
        original_verdict: str,
        contract_verdict: str,
        classification: str,
        invariants: Mapping[str, str] | None,
        replacement_adjudication: PredicateAdjudication | None = None,
    ) -> AttemptResult:
        if self._state != "RESTORE_STARTED":
            # The replacement adjudication, when one exists, is published on the
            # record that enters RESTORE_STARTED.
            references = (
                (replacement_adjudication.adjudication_reference,)
                if replacement_adjudication is not None
                else ()
            )
            self._transition("RESTORE_STARTED", *references)
        try:
            if challenge_target is not None:
                self._count("CHALLENGE_POD_DELETION")
                deletion = self._capabilities.mutations.delete_challenge_pod(
                    challenge_target
                )
                self._dispatch_kind(deletion, "CHALLENGE_POD_DELETION")
                self._event("CHALLENGE_POD_DELETION")
            self._count("RESTORED_SERVICE_CREATION")
            restored = self._capabilities.mutations.restore_user_service(
                prestate.restoration_capability
            )
            self._dispatch_kind(restored, "RESTORED_SERVICE_CREATION")
            self._event("RESTORED_SERVICE_CREATION")
            verification = self._capabilities.observations.verify_restoration(prestate)
            if not isinstance(verification, RestorationVerification) or not verification.valid:
                return self._block_restoration(
                    original_verdict=original_verdict,
                    contract_verdict=contract_verdict,
                    invariants=invariants,
                )
            self._event("RESTORATION_INVARIANTS_VERIFIED")
            # ---- RESTORATION_POSITIVE_CONTROL ----------------------------
            # Authorized at RESTORE_STARTED, before the positive-control
            # window it will cite is captured.
            authorized_positive = False
            if "RESTORATION_POSITIVE_CONTROL" not in self._authorized:
                self._authorize("RESTORATION_POSITIVE_CONTROL")
                authorized_positive = True
            positive = self._capabilities.workload.evaluate(
                "RESTORATION_POSITIVE_CONTROL", 3, None, prestate
            )
            self._validate_workload(positive, "RESTORATION_POSITIVE_CONTROL", prestate)
            self._event("RESTORATION_POSITIVE_CONTROL")
            if not positive.healthy:
                return self._block_restoration(
                    original_verdict=original_verdict,
                    contract_verdict=contract_verdict,
                    invariants=invariants,
                )
            positive_adjudication = None
            if authorized_positive:
                self._publish_window_evidence(
                    "RESTORATION_POSITIVE_CONTROL", positive, verification
                )
                positive_adjudication = self._record_adjudication(
                    "RESTORATION_POSITIVE_CONTROL",
                    self._capabilities.adjudication
                    .adjudicate_restoration_positive_control(verification, positive),
                )
        except (KubernetesMutationError, MsM01AttemptError):
            return self._block_restoration(
                original_verdict=original_verdict,
                contract_verdict=contract_verdict,
                invariants=invariants,
            )
        verified_references = list(verification.evidence_references)
        if positive_adjudication is not None:
            verified_references.append(positive_adjudication.adjudication_reference)
        self._transition("RESTORE_VERIFIED", *verified_references)
        self._capabilities.terminalizer.publish_retained_coverage()
        terminal_identity = self._capabilities.terminalizer.publish_terminal_identity(
            TerminalOutcome.FINALIZED
        )
        self._transition("FINALIZED", terminal_identity)
        return self._terminalize(
            TerminalOutcome.FINALIZED,
            original_verdict=original_verdict,
            contract_verdict=contract_verdict,
            classification=classification,
            invariants=invariants,
        )

    def _validate_workload(
        self,
        value: Any,
        phase: str,
        prestate: HealthyPrestate,
    ) -> None:
        if (
            not isinstance(value, WorkloadWindowResult)
            or value.phase != phase
            or value.ordinal != WORKLOAD_WINDOWS[phase]
            or value.run_id != self._identity.run_id
            or value.attempt_id != self._identity.attempt_id
            or value.stream_identity != prestate.workload_stream_identity
        ):
            _reject("WORKLOAD_WINDOW_SUBSTITUTION")

    def run(self) -> AttemptResult:
        if self._ran:
            _reject("ATTEMPT_REPLAY_FORBIDDEN")
        self._ran = True
        if self._capabilities.authority.global_stop_active():
            _reject("GLOBAL_STOP_ACTIVE")
        self._capabilities.authority.claim_once(
            self._identity, resume=self._pending is not None
        )
        prestate = self._capabilities.prestate.authenticate(
            self._policy, self._identity
        )
        if (
            not isinstance(prestate, HealthyPrestate)
            or prestate.run_id != self._identity.run_id
            or prestate.attempt_id != self._identity.attempt_id
        ):
            _reject("HEALTHY_PRESTATE_AUTHENTICATION_FAILED")
        self._state = self._capabilities.state.current_state()
        if self._state != "HEALTHY_STATE_CAPTURED":
            _reject("ATTEMPT_START_STATE_INVALID")
        self._event("HEALTHY_STATE_AUTHENTICATED")

        dispatch = None
        self._mutation_may_have_occurred = True
        if self._pending is None:
            try:
                self._count("INITIAL_USER_SERVICE_DELETION")
                dispatch = self._capabilities.mutations.delete_user_service(
                    prestate.restoration_capability
                )
                self._dispatch_kind(dispatch, "INITIAL_USER_SERVICE_DELETION")
                self._event("INITIAL_USER_SERVICE_DELETION")
            except KubernetesMutationError as error:
                if error.code not in {
                    "KUBERNETES_MUTATION_TIMEOUT",
                    "KUBERNETES_MUTATION_INTERRUPTED",
                    "KUBERNETES_MUTATION_API_EXCEPTION",
                }:
                    raise
                dispatch = error.result
        observation = self._capabilities.observations.observe_initial_service_deletion(
            dispatch
        )
        if not isinstance(observation, MutationObservation):
            _reject("MUTATION_OBSERVATION_INVALID")
        if self._pending is None:
            reconciliation = self._capabilities.mutations.reconcile(
                "INITIAL_USER_SERVICE_DELETION", prestate.service_uid, observation
            )
        else:
            if self._pending.captured_uid != prestate.service_uid:
                _reject("PENDING_MUTATION_INVALID")
            reconciliation = self._capabilities.mutations.reconcile_pending(
                self._pending.operation_id,
                self._pending.operation_kind,
                self._pending.captured_uid,
                observation,
            )
            self._event("INITIAL_USER_SERVICE_DELETION_RECOVERED")
        effect, safe_to_continue = self._reconciliation(reconciliation)
        self._event(f"INITIAL_EFFECT:{effect.value}")
        if effect is ObservedEffect.OBSERVED_NOT_APPLIED_AFTER_RECOVERY:
            self._mutation_may_have_occurred = False
            self._capabilities.terminalizer.publish_retained_coverage()
            terminal_identity = self._capabilities.terminalizer.publish_terminal_identity(
                TerminalOutcome.ABORTED_SAFE
            )
            self._transition("ABORTED_SAFE", terminal_identity)
            return self._terminalize(
                TerminalOutcome.ABORTED_SAFE,
                original_verdict="NOT_EVALUATED",
                contract_verdict="NOT_EVALUATED",
                classification="MUTANT_ACTIVATION_FAILURE",
            )
        if (
            effect is not ObservedEffect.OBSERVED_APPLIED_AFTER_RECOVERY
            or safe_to_continue is not True
        ):
            return self._block_restoration(
                original_verdict="NOT_EVALUATED",
                contract_verdict="NOT_EVALUATED",
                invariants=None,
            )
        self._transition("MUTANT_INJECTED")
        if self._capabilities.observations.service_structurally_absent() is not True:
            return self._restore(
                prestate,
                None,
                original_verdict="NOT_EVALUATED",
                contract_verdict="NOT_EVALUATED",
                classification="MUTANT_ACTIVATION_FAILURE",
                invariants=None,
            )
        self._transition("MUTANT_STATE_VERIFIED")
        self._event("M01_ACTIVE_SERVICE_ABSENT")

        self._transition("ORIGINAL_ORACLE_STARTED")
        original = self._capabilities.original_oracle.invoke_once(
            prestate, self._identity
        )
        if not isinstance(original, OriginalOracleOutcome):
            _reject("ORIGINAL_ORACLE_OUTCOME_INVALID")
        self._count("ORIGINAL_ORACLE_INVOCATION")
        self._event("ORIGINAL_ORACLE_INVOKED")
        self._transition("ORIGINAL_ORACLE_EVALUATED", *original.evidence_references)
        if original.returned_boolean is None:
            return self._restore(
                prestate,
                None,
                original_verdict=original.verdict,
                contract_verdict="NOT_EVALUATED",
                classification="HARNESS_TIMING_FAILURE",
                invariants=None,
            )

        # ---- INITIAL_INVARIANT_EVALUATION -------------------------------
        # Authorized first, at ORIGINAL_ORACLE_EVALUATED, so the marker
        # precedes every raw reference the predicate will cite.
        self._authorize("INITIAL_INVARIANT_EVALUATION")

        challenge_target = None
        try:
            self._count("CHALLENGE_POD_CREATION")
            challenge_dispatch = self._capabilities.mutations.create_challenge_pod()
            self._dispatch_kind(challenge_dispatch, "CHALLENGE_POD_CREATION")
            challenge_target = getattr(challenge_dispatch, "challenge_target", None)
            if challenge_target is None:
                _reject("CHALLENGE_TARGET_MISSING")
            self._event("CHALLENGE_POD_CREATION")
        except (KubernetesMutationError, MsM01AttemptError):
            return self._restore(
                prestate,
                challenge_target,
                original_verdict=original.verdict,
                contract_verdict="INCOMPLETE",
                classification="HARNESS_TIMING_FAILURE",
                invariants=None,
            )

        initial_challenge = self._capabilities.challenge.execute(
            "INITIAL_MUTANT_CHALLENGE", 1, challenge_target
        )
        self._count("INITIAL_CHALLENGE_EXECUTION")
        if not isinstance(initial_challenge, ChallengeRun) or not initial_challenge.completed:
            return self._restore(
                prestate,
                challenge_target,
                original_verdict=original.verdict,
                contract_verdict="INCOMPLETE",
                classification=(
                    initial_challenge.failure_classification
                    if isinstance(initial_challenge, ChallengeRun)
                    else "HARNESS_TIMING_FAILURE"
                ),
                invariants=None,
            )
        self._event("INITIAL_CHALLENGE_EXECUTED")
        try:
            initial_workload = self._capabilities.workload.evaluate(
                "INITIAL_MUTANT_CHALLENGE", 1, initial_challenge, prestate
            )
            self._validate_workload(
                initial_workload, "INITIAL_MUTANT_CHALLENGE", prestate
            )
        except MsM01AttemptError:
            return self._restore(
                prestate,
                challenge_target,
                original_verdict=original.verdict,
                contract_verdict="INCOMPLETE",
                classification="PROTOCOL_VIOLATION",
                invariants=None,
            )
        self._event("INITIAL_MUTANT_CHALLENGE_WORKLOAD")
        self._publish_window_evidence(
            "INITIAL_INVARIANT_EVALUATION", initial_workload, initial_challenge
        )
        initial_adjudication = self._record_adjudication(
            "INITIAL_INVARIANT_EVALUATION",
            self._capabilities.adjudication.adjudicate_initial_invariants(
                original, initial_challenge, initial_workload
            ),
        )
        self._transition(
            "CONTRACT_EVALUATED", initial_adjudication.adjudication_reference
        )

        # ---- REPLACEMENT_PERSISTENCE_EVALUATION --------------------------
        self._authorize("REPLACEMENT_PERSISTENCE_EVALUATION")

        captured = self._capabilities.observations.select_replacement_pod(prestate)
        if not isinstance(captured, CapturedPod):
            _reject("REPLACEMENT_TARGET_INVALID")
        self._event("REPLACEMENT_POD_CAPTURED")
        try:
            self._count("REPLACEMENT_POD_DELETION")
            pod_dispatch = self._capabilities.mutations.delete_replacement_pod(
                captured.target
            )
            self._dispatch_kind(pod_dispatch, "REPLACEMENT_POD_DELETION")
            self._event("REPLACEMENT_POD_DELETION")
        except KubernetesMutationError:
            return self._restore(
                prestate,
                challenge_target,
                original_verdict=original.verdict,
                contract_verdict="INCOMPLETE",
                classification="INFRASTRUCTURE_FAILURE",
                invariants=None,
            )
        replacement = self._capabilities.observations.observe_replacement(
            captured, pod_dispatch
        )
        if not isinstance(replacement, ReplacementObservation):
            _reject("REPLACEMENT_OBSERVATION_INVALID")
        replacement_challenge = None
        replacement_workload = None
        if replacement.valid:
            self._event("REPLACEMENT_UID_OWNERSHIP_CAPACITY_VERIFIED")
            replacement_challenge = self._capabilities.challenge.execute(
                "POST_REPLACEMENT_PERSISTENCE", 2, challenge_target
            )
            self._count("POST_REPLACEMENT_CHALLENGE_EXECUTION")
            if not isinstance(replacement_challenge, ChallengeRun) or not replacement_challenge.completed:
                return self._restore(
                    prestate,
                    challenge_target,
                    original_verdict=original.verdict,
                    contract_verdict="INCOMPLETE",
                    classification="HARNESS_TIMING_FAILURE",
                    invariants=None,
                )
            try:
                replacement_workload = self._capabilities.workload.evaluate(
                    "POST_REPLACEMENT_PERSISTENCE",
                    2,
                    replacement_challenge,
                    prestate,
                )
                self._validate_workload(
                    replacement_workload,
                    "POST_REPLACEMENT_PERSISTENCE",
                    prestate,
                )
            except MsM01AttemptError:
                return self._restore(
                    prestate,
                    challenge_target,
                    original_verdict=original.verdict,
                    contract_verdict="INCOMPLETE",
                    classification="PROTOCOL_VIOLATION",
                    invariants=None,
                )
            self._event("POST_REPLACEMENT_PERSISTENCE_WORKLOAD")
            self._publish_window_evidence(
                "REPLACEMENT_PERSISTENCE_EVALUATION",
                replacement_workload,
                replacement_challenge,
                captured,
                replacement,
            )
        if replacement_workload is None:
            # The replacement was not valid, so no persistence window ran.  That
            # is a contract REJECT, not an infrastructure failure: the predicate
            # is still adjudicated FALSE, backed by the capture and observation
            # evidence that shows why.
            self._publish_window_evidence(
                "REPLACEMENT_PERSISTENCE_EVALUATION", None, captured, replacement
            )
        replacement_adjudication = self._record_adjudication(
            "REPLACEMENT_PERSISTENCE_EVALUATION",
            self._capabilities.adjudication.adjudicate_replacement_persistence(
                captured, replacement, replacement_challenge, replacement_workload
            ),
        )

        contract = self._capabilities.adjudication.derive_contract(
            original,
            initial_challenge,
            initial_workload,
            captured,
            replacement,
            replacement_challenge,
            replacement_workload,
            tuple(self._adjudications),
        )
        if not isinstance(contract, ContractEvaluation):
            _reject("CONTRACT_ADJUDICATION_INVALID")
        if (
            contract.verdict == "PASS"
            and (
                not initial_workload.healthy
                or not replacement.valid
                or replacement_workload is None
                or not replacement_workload.healthy
            )
        ):
            _reject("CONTRACT_ADJUDICATION_RAW_BACKING_INVALID")
        self._event("CONTRACT_ADJUDICATION_RAW_BACKED")
        if contract.verdict == "REJECT":
            classification = "CONTRACT_REJECT"
        elif original.returned_boolean is False:
            classification = "ORIGINAL_ORACLE_REJECT"
        else:
            classification = "COMPLETED"
        return self._restore(
            prestate,
            challenge_target,
            original_verdict=original.verdict,
            contract_verdict=contract.verdict,
            classification=classification,
            invariants=contract.invariant_outcomes,
            replacement_adjudication=replacement_adjudication,
        )


__all__ = [
    "AdjudicationCapability",
    "AttemptCapabilities",
    "AttemptIdentity",
    "AttemptResult",
    "AttemptState",
    "CapturedPod",
    "ChallengeCapability",
    "ChallengeRun",
    "ContractEvaluation",
    "FROZEN_STATES",
    "FROZEN_TRANSITIONS",
    "HealthyPrestate",
    "MsM01Attempt",
    "MsM01AttemptError",
    "ObservationCapability",
    "OriginalOracleCapability",
    "OriginalOracleOutcome",
    "PREDICATES",
    "PREDICATE_PHASES",
    "PendingMutation",
    "PredicateAdjudication",
    "PrestateAuthority",
    "ReplacementObservation",
    "RestorationVerification",
    "TerminalOutcome",
    "TerminalizationCapability",
    "TerminalizationResult",
    "WORKLOAD_WINDOWS",
    "WorkloadCapability",
    "WorkloadWindowResult",
]

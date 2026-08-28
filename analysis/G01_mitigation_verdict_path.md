# G0.1 — What decides "mitigation success" for `missing_service_social_network`

Task: establish the validity gate for the SREMut study — exactly what computes and
records the mitigation verdict in SREGym for the `missing_service_social_network`
problem.

- Analysis date: 2026-08-26
- SREGym commit: `ba07faf1a322f9b6d4a279643bb796aa2f36f64b` (working tree verified clean)
- Oracle SHA-256: `a087fd38399cfca4c2de764dbbab837d6100350ab9f71b89552cafe2c91fca8b`
- Method: static reading + grep evidence only. No cluster contact, no test runs.
  Absence claims are backed by a grep that returns nothing and are labelled
  "verified absent". Inferences are labelled as inferences.

Paths are relative to `/home/sakibbuet2k19/sremut/SREGym/` unless prefixed `SREMut/`.

---

## A. `sregym/conductor/oracles/mitigation.py` (118 lines)

### A.1 Kubernetes resources touched

Exactly four cluster calls, two resource kinds.

| Line | Call | Kind | Method | Fields inspected |
|---|---|---|---|---|
| 30 | `self.problem.kubectl.list_deployments(ns)` | Deployment | `AppsV1Api.list_namespaced_deployment` (`sregym/service/kubectl.py:75`) | `.metadata.name`, `.spec.replicas` |
| 38 | `kubectl.list_deployments(ns)` | Deployment | same | `.spec.replicas`, `.status.updated_replicas`, `.status.ready_replicas`, `.status.unavailable_replicas` |
| 66 | `kubectl.list_deployments(ns)` | Deployment | same | `.metadata.name`, `.spec.replicas`, `.status.ready_replicas` |
| 86 | `kubectl.list_pods(ns)` | Pod | `CoreV1Api.list_namespaced_pod` (`sregym/service/kubectl.py:44`) | `.metadata.name`, `.status.phase`, `.status.container_statuses[].name` / `.ready` / `.state.waiting.reason` / `.state.terminated.reason` |

Complete machine-extracted attribute inventory (`grep -oE '\b[a-z_]+\.[a-z_.]+' | sort -u`)
yields 29 unique accesses: the Deployment and Pod fields above plus `time.monotonic`,
`time.sleep`, and `self.*` internals. No third resource kind appears.

### A.2 Every `success=False` condition (exhaustive)

| # | Lines | Condition | Message |
|---|---|---|---|
| 1 | 70-73 | Deployment named in `self.replica_count` absent from current listing | `Deployment '{name}' was deleted` |
| 2 | 76-79 | `dep.spec.replicas == 0` (`None` -> 1 at line 75) | `Deployment '{name}' was scaled to 0` |
| 3 | 81-84 | `ready_replicas or 0 < desired` | `Deployment '{name}' has {ready}/{desired} replicas ready` |
| 4 | 88-91 | `pod_list.items` empty | `No pods found in namespace` |
| 5 | 96-99 | Any pod `.status.phase != "Running"` | `Pod {name} is in phase: {phase}` |
| 6 | 101-112 | Per container: `state.waiting` with a `reason` (102), OR `state.terminated` with `reason != "Completed"` (105), OR `not container_status.ready` (110) | three variants |

Conditions 1-3 iterate ONLY over `self.replica_count` (line 69) — the baseline map.
Conditions 4-6 iterate over all pods in the namespace.

Indirect seventh path: line 101 iterates `pod.status.container_statuses` with no `None`
guard. A `None` raises `TypeError`, which propagates to `conductor.py:269-271` and is
recorded as `{"success": False, "error": ...}`. Observed code asymmetry versus the
post-fix sibling `duplicate_pvc_mounts_mitigation.py`, which writes `... or []`.
Whether a `None` actually occurs at runtime is NOT determinable statically.

### A.3 Return shape

`dict` with exactly one key on every path: `{"success": <bool>}`.
Assigned at 72 / 78 / 83 / 90 (`False`) or at 117 (`results["success"] = all_normal`).
`results` initialised empty at line 60; no other key is ever assigned.

SREMut enforces this strictly: `set(raw_result) != {"success"}` -> reject
(`SREMut/src/sremut/original_oracle_worker.py:164-167`).

### A.4 Observed / verified absent

Greps against `sregym/conductor/oracles/mitigation.py`:

| Feature | Status | Evidence |
|---|---|---|
| Services | VERIFIED ABSENT | `grep -c 'service'` -> 0. `grep -c 'Service'` -> 1, sole hit line 26, a docstring: "This is not a full resource baseline: Services and resources created by inject_fault() are outside it." No code reference. |
| Endpoints | VERIFIED ABSENT | `grep -c 'endpoint'` -> 0; `'Endpoint'` -> 0 |
| EndpointSlices | VERIFIED ABSENT | `grep -c 'EndpointSlice'` -> 0; case-insensitive `endpointslice` -> 0 |
| Service selectors | VERIFIED ABSENT | `grep -c 'selector'` -> 0 |
| Ports / targetPorts | VERIFIED ABSENT | `'targetPort'` -> 0; `'target_port'` -> 0. `grep -c 'port'` -> 3, all substrings: line 1 `import time`, line 3 `... import Oracle`, line 12 `importance = 1.0` |
| DNS resolution | VERIFIED ABSENT | `'dns'` -> 0; `'DNS'` -> 0; `'resolve'` -> 0; `nslookup|dig|resolv` -> 0 |
| Network / socket I/O | VERIFIED ABSENT | `'socket'` -> 0; `'connect'` -> 0; `network|net\.|cidr` (-i) -> 0 |
| HTTP requests | VERIFIED ABSENT | `'http'` -> 0; `'HTTP'` -> 0; `'requests'` -> 0; `'urllib'` -> 0; `'curl'` -> 0 |
| Workload / wrk | VERIFIED ABSENT | `'wrk'` -> 0; `'workload'` -> 0; `'Workload'` -> 0 |
| Subprocess / exec | VERIFIED ABSENT | `'subprocess'` -> 0; `'exec'` -> 0 |

Combined case-insensitive sweep `svc|endpoint|ingress|network|net\.|ip|cidr|resolv|nslookup|dig `
returns exactly one line: line 19, matching `ip` inside the word "skipped".

Entire import list: `import time` (1), `from sregym.conductor.oracles.base import Oracle` (3).

### A.5 kubectl subprocess or Python client?

Python client library. `problem.kubectl` is a `KubeCtl` built at `missing_service.py:27`.
`KubeCtl.__init__` (`sregym/service/kubectl.py:30-38`) calls `config.load_kube_config()`
at line 33 with NO arguments — default `~/.kube/config`, default current-context —
logs "Missing kubeconfig. Please set up a cluster." on failure (35), then builds
`core_v1_api` (37) and `apps_v1_api` (38).

(The fault INJECTOR does use `kubectl` subprocesses — see section E — but the oracle
does not.)

### A.6 Waits, timeouts, retries

One wait: `_wait_for_rollouts` (34-53), called unconditionally as the first action of
`evaluate()` at line 64.

- `_ROLLOUT_SETTLE_SECONDS = 60` (line 7); `self.rollout_time` set at 21 and 32
- `_ROLLOUT_POLL_INTERVAL = 5` (line 8), used at 52
- Deadline loop at 36; polls `list_deployments` each iteration (38); returns early when
  every deployment satisfies `updated_replicas >= desired and ready_replicas >= desired
  and unavailable_replicas == 0` (43-49)
- On expiry it does NOT fail: prints a warning (53) and evaluates current state anyway

Documented intent at 62-63: "so we don't evaluate a transient state where old pods are
gone and new ones haven't crashed yet."

For a Service-deletion fault this is effectively a 60 s grace period during which nothing
the oracle measures is perturbed, because deleting a Service does not change Deployment
or Pod status. NOT DETERMINABLE statically: whether the loop converges immediately or
burns the full 60 s under MS-M01.

No `_request_timeout` is passed by the oracle. No retry-on-exception logic exists.

---

## B. `capture_baseline()` trace

### Call chain

| Hop | Location | Code |
|---|---|---|
| 1 | `conductor.py:219` | `def _inject_fault(self):` |
| 2 | `conductor.py:226` | `if getattr(problem, "mitigation_oracle", None):` |
| 3 | `conductor.py:227` | `problem.mitigation_oracle.capture_baseline()` |
| 4 | `conductor.py:229` | `problem.inject_fault()` |

Baseline capture happens TWO LINES before fault injection, in the same function, with no
branch between. `self.fault_injected = True` follows at 231.

`_inject_fault` is reached from `conductor.py:295-297`, inside `_advance_to_next_stage`:
`if start_index == 0 and not self.fault_injected: self._inject_fault()`.

Rationale documented at `conductor.py:223-225` and mirrored at `mitigation.py:16-19`:
the Problem is constructed before `deploy_app()`, so `__init__` is too early.

Other call sites: `compound.py:21-23` (fan-out to children), `base.py:10` (no-op default).

### What the baseline consists of

`mitigation.py:30-32`:

    deployments = self.problem.kubectl.list_deployments(self.problem.namespace)
    self.replica_count = {dep.metadata.name: dep.spec.replicas for dep in deployments.items}
    self.rollout_time = _ROLLOUT_SETTLE_SECONDS

A flat `{deployment_name: spec.replicas}` map, nothing else. For `social-network` that is
27 entries, all `replicas=1` (source: `baselines/missing_service_social_network/run-01/deployments.json`).

Docstring at 26-28 concedes the scope limit explicitly: "This is not a full resource
baseline: Services and resources created by inject_fault() are outside it. Faults that
must preserve or validate those resources need a custom mitigation oracle."

### If it was never captured

`self.replica_count = {}` from `__init__` line 20 -> the loop at line 69 iterates zero
times -> conditions 1-3 are skipped entirely. Evaluation degrades to the pod sweep alone.
The hazard is named in-code at `mitigation.py:16-19`: "every replica check below would be
skipped, letting 'scale to 0' and 'delete the deployment' pass."

This is reachable in practice: `run-oracle.py:57` calls `mitigation_oracle.evaluate()`
with no preceding baseline capture. `grep -n 'capture_baseline' run-oracle.py` returns
nothing — VERIFIED ABSENT.

SREMut closes this hole: an empty baseline is rejected at
`SREMut/src/sremut/original_oracle_worker.py:137-139`.

---

## C. THE CRUX — the mitigation verdict path

### Verdict-path references to `mitigation_oracle`

(`git grep -n 'mitigation_oracle'` returns 131 lines: 1 base declaration, ~100 per-problem
assignments, 2 conductor call sites, 1 standalone runner, plus docs/tests/CodeQL.)

| Hop | file:line | Code |
|---|---|---|
| Declaration | `problems/base.py:18` | `self.mitigation_oracle = None` |
| Assignment | `problems/missing_service.py:40` | `self.mitigation_oracle = MitigationOracle(problem=self)` |
| Stage registration | `conductor.py:204` | gates appending `_evaluate_mitigation` to `stage_sequence` (205-210) |
| Baseline | `conductor.py:227` | `problem.mitigation_oracle.capture_baseline()` |
| INVOCATION | `conductor.py:268` | `r = problem.mitigation_oracle.evaluate()` |
| Standalone | `run-oracle.py:57` | `result = problem.mitigation_oracle.evaluate()` |

### End-to-end trace

1. Agent submission -> `conductor.py:516` `submit()` -> dispatch at `conductor.py:491`
   (`current_stage["evaluation"](sol)`).
2. Stage function `_evaluate_mitigation`, `conductor.py:262-279`:

        262    def _evaluate_mitigation(self, solution):
        264        problem = self.current_problem
        265        # Currently mitigation_oracle.evaluate() does not take the agent solution directly.
        267        try:
        268            r = problem.mitigation_oracle.evaluate()
        269        except Exception as e:
        271            r = {"success": False, "error": f"{type(e).__name__}: {e}"}
        272        self.results["Mitigation"] = r
        273        self.results["TTM"] = time.time() - self.execution_start_time
        279        return r

   Line 265: the agent's submitted text is logged but never used. The oracle takes no
   arguments.
3. Result record: `self.results["Mitigation"] = r` at line 272.
   `grep -n 'self\.results\['` returns exactly 8 lines — 253, 254, 257, 258
   (Diagnosis/TTL) and 272, 273, 276, 277 (Mitigation/TTM). Only 272 assigns
   `Mitigation`; 276 is an f-string read. Other `self.results` touches: `= {}` at 79 and
   413, `setdefault` at 497 (error path for a raising stage), `dict(self.results)` at
   526/531/535. WRITTEN ONCE, NEVER MUTATED.
4. Teardown: `_finish_problem` (`conductor.py:367-386`) is idempotence guard +
   `_cleanup_sync()` + two log lines. No aggregation, no cross-check.
5. Reporting: `main.py:452-461` flattens `snapshot[f"{stage}.{k}"] = v`, producing the
   CSV column `Mitigation.success`. Flat pass-through.

### Is the verdict ever combined, gated, or cross-checked?

NO — not anywhere on the recorded verdict path. `MitigationOracle.evaluate()`'s Boolean
is the whole verdict for `missing_service_social_network`.

Proving path: line 268 produces `r`; line 272 stores it unmodified; `_finish_problem`
does not touch it; `main.py` flattens it verbatim. There is no `and`, no secondary oracle
call, and no predicate between `mitigation.py:118`'s `return results` and the CSV cell.

Three near-misses, stated so they are not mistaken for gating:

- `CompoundedOracle` EXISTS and does combine — `compound.py:25-64`, ANDing children at
  40-41 with weighted `accuracy` at 43-48. Used by four problems
  (`incorrect_port_assignment.py:58`, `feature_flag_latent_bug_hotel_reservation.py:52`,
  `trainticket_f17.py:34`, `multiple_failures.py:49`). `missing_service` is NOT one of
  them — `missing_service.py:40` assigns a bare `MitigationOracle`.
- The diagnosis oracle runs SEPARATELY. `LLMAsAJudgeOracle` attached at
  `missing_service.py:38`, evaluated by `_evaluate_diagnosis` (`conductor.py:242-260`),
  stored to `results["Diagnosis"]` at 253. Independent stages; neither reads the other.
- The visualizer can AND them POST HOC: `visualizer/queries.py:236-243`. With
  `stage=None` it returns `bool(row["Mitigation.success"]) and bool(row["Diagnosis.success"])`;
  with `stage="mitigation"` it returns the bare Boolean (242-243). This is a downstream
  analysis helper reading a published CSV — it does not compute or record the verdict.

### Is `workload.py` evaluated in the mitigation path?

NO — verified absent for this problem.

`git grep -n 'WorkloadOracle\|oracles.workload'` returns 7 lines. Attachment sites:

- `problems/ephemeral_port_range_hotel_reservation.py:37` — `mitigation_oracle = WorkloadOracle(...)`
- `problems/trainticket_f17.py:36` — inside a `CompoundedOracle`
- `problems/rbac_misconfiguration.py:31` — COMMENTED OUT

`missing_service.py` neither imports nor references `WorkloadOracle`; its only oracle
imports are `LLMAsAJudgeOracle` (line 1) and `MitigationOracle` (line 2).

The sharp point: a workload IS running. `missing_service.py:39` calls
`self.app.create_workload()`, and `conductor.py:870-872` starts it
(`if problem.run_default_workload: problem.app.start_workload()`, with
`run_default_workload = True` at `problems/base.py:7`). Load is generated against the app
throughout the attempt, and nothing ever reads its results for this problem.
`WorkloadOracle.evaluate` (`workload.py:19-37`) is the only consumer of `wrk.collect()`.

---

## D. Inventory of `sregym/conductor/oracles/` (seed list)

64 class definitions across 60 files. *(Corrected 2026-08-28, R2 Part B: the grep `^class .*(` behind these figures misses parenthesis-less classes. By AST there are **68** `ClassDef` nodes in `oracles/`, of which **59** derive from `Oracle` and all 59 define `evaluate()`; the other 8 are `llm_as_a_judge` helpers and 1 is `Oracle(ABC)` itself. See `analysis/census/R2_PARTB_ORACLE_COUNT.md`. The seed list below is unchanged and remains a seed, not a census.)* Signals are grep-derived per file, probing:
`read/list_namespaced_service`, `endpoint`, `endpointslice`, `selector`,
`http://|wget|curl|requests.|urlopen`, `exec_command|connect_get_namespaced_pod_exec`,
`prometheus|alert`, `nslookup|getent|dig|dns`, `list_pods|list_namespaced_pod`,
`list_deployments|read/list_namespaced_deployment`, `wrk|workload`,
`llm|judge|anthropic|openai`. This is a seed list for a later census, not the census.

| File | Class | Observation signals |
|---|---|---|
| `base.py` | `Oracle(ABC)` | — (abstract) |
| `compound.py` | `CompoundedOracle` | — (delegates + ANDs children) |
| `detection.py` | `DetectionOracle` | — (string match on agent answer) |
| **`mitigation.py`** | **`MitigationOracle`** | **Pods, Deployments** |
| `workload.py` | `WorkloadOracle` | Workload |
| `admission_webhook_outage_mitigation.py` | `AdmissionWebhookOutageMitigationOracle` | Endpoints, Pods |
| `alert_oracle.py` | `AlertOracle` | Endpoints, HTTP, Prometheus, DNS |
| `assign_non_existent_node_mitigation.py` | `AssignNonExistentNodeMitigationOracle` | Pods |
| `calico_route_reflector_mitigation.py` | `CalicoRouteReflectorMitigationOracle` | Selector, HTTP, DNS, Pods, Deployments |
| `conntrack_mitigation.py` | `ConntrackMitigationOracle(MitigationOracle)` | HTTP |
| `cpu_throttling_mitigation.py` | `CpuThrottlingMitigationOracle(MitigationOracle)` | Deployments |
| `cronjob_sidecar_mitigation.py` | `CronJobSidecarBlocksCompletionMitigationOracle` | Deployments, Workload |
| `cumulative_admission_webhook_timeout_mitigation.py` | `CumulativeAdmissionWebhookTimeoutMitigationOracle` | Endpoints, Selector, Deployments, Workload |
| `data_plane_progress.py` | `DataPlaneProgressOracle` | Selector, Pods, Deployments |
| `deployment_readiness.py` | `DeploymentReadinessOracle` | Pods, Deployments |
| `dev_shm_mitigation_oracle.py` | `DevShmMitigationOracle` | Selector, Pods, Deployments |
| `diagnosis_oracle.py` | `DiagnosisOracle` | Service, Selector, Pods, Deployments |
| `dns_resolution_mitigation.py` | `DNSResolutionMitigationOracle` | Service, Selector, DNS |
| `duplicate_pvc_mounts_mitigation.py` | `DuplicatePVCMountsMitigationOracle` | HTTP, Pods |
| `edge_request_filter_mitigation.py` | `EdgeRequestFilterMitigationOracle` | Service, Endpoints, Selector, HTTP, Pods |
| `env_variable_shadowing_mitigation.py` | `EnvVariableShadowingMitigationOracle` | Service, Endpoints, Selector, HTTP, Pods |
| `expired_tls_mitigation_oracle.py` | `ExpiredTlsMitigationOracle` | — |
| `fd_exhaustion.py` | `FDMitigationOracle(MitigationOracle)` | — |
| `feature_flag_http_probe_mitigation.py` | `FeatureFlagHttpProbeMitigationOracle` | Endpoints, HTTP, PodExec, Prometheus, Pods, Workload |
| `finalizer_deadlock_controller_mitigation.py` | `FinalizerDeadlockControllerMitigationOracle` | Pods, Deployments |
| `hpa_control_plane_mitigation.py` | `HPAControlPlaneMitigationOracle` | Selector, PodExec |
| `imbalance_mitigation.py` | `ImbalanceMitigationOracle` | — |
| `incorrect_image_mitigation.py` | `IncorrectImageMitigationOracle` | — |
| `incorrect_port.py` | `IncorrectPortAssignmentMitigationOracle` | Endpoints, HTTP, DNS |
| `ingress_misroute_oracle.py` | `IngressMisrouteMitigationOracle` | — |
| `internal_traffic_policy_mitigation.py` | `InternalTrafficPolicyMitigationOracle` | Service, Endpoints, Selector |
| `kafka_producer_leak_mitigation.py` | `KafkaProducerLeakOracle(MitigationOracle)` | Pods |
| `kubelet_eviction_threshold_misconfig_mitigation.py` | `KubeletEvictionThresholdMisconfigMitigationOracle(MitigationOracle)` | Deployments |
| `llm_as_a_judge/llm_as_a_judge_oracle.py` | `LLMAsAJudgeOracle` | LLM |
| `llm_as_a_judge/judge.py` | `ChecklistParseError`, `JudgeParseError`, `JudgmentResult` | LLM (support types) |
| `missing_cm_key_mitigation.py` | `MissingCmKeyMitigationOracle` | PodExec |
| `missing_env_variable_mitigation.py` | `MissingEnvVariableMitigationOracle` | Pods |
| `mutating_webhook_resource_limits_mitigation.py` | `MutatingWebhookResourceLimitsMitigationOracle` | Endpoints, HTTP, Pods |
| `namespace_memory_limit_mitigation.py` | `NamespaceMemoryLimitMitigationOracle` | Service, Endpoints, Selector, HTTP, Pods |
| `network_policy_oracle.py` | `NetworkPolicyMitigationOracle` | Service, Endpoints, Selector, HTTP, DNS, Pods, Workload |
| `nightly_rebalance_oom_mitigation.py` | `NightlyRebalanceOOMMitigationOracle(MitigationOracle)` | Selector, Pods |
| `node_clock_drift_mitigation.py` | `NodeClockDriftMitigationOracle` | Selector, Pods |
| `operator_misoperation/invalid_affinity_mitigation.py` | `InvalidAffinityMitigationOracle` | PodExec |
| `operator_misoperation/non_existent_storage_mitigation.py` | `NonExistentStorageClassMitigationOracle` | PodExec |
| `operator_misoperation/overload_replicas_mitigation.py` | `OverloadReplicasMitigationOracle` | PodExec |
| `operator_misoperation/security_context_mitigation.py` | `SecurityContextMitigationOracle` | PodExec |
| `operator_misoperation/wrong_update_strategy_mitigation.py` | `WrongUpdateStrategyMitigationOracle` | PodExec |
| `postgres_lock_mitigation.py` | `PostgresLockMitigationOracle` | Service, HTTP |
| `priority_preemption_mitigation.py` | `PriorityPreemptionMitigationOracle` | Endpoints, HTTP, Pods, Deployments, Workload |
| `readiness_probe_mitigation.py` | `ReadinessProbeMitigationOracle` | Service, Endpoints, Pods |
| `rolling_update_misconfiguration_mitigation.py` | `RollingUpdateMitigationOracle` | PodExec |
| `rpc_retry_storm_mitigation.py` | `RPCRetryStormMitigationOracle` | Pods, Workload |
| `scale_pod_zero_mitigation.py` | `ScalePodZeroMitigationOracle` | Pods |
| `search_rate_retry_mitigation.py` | `SearchRateRetryMitigationOracle` | Endpoints, Deployments, Workload |
| `secret_rotation_stale_env_mitigation.py` | `SecretRotationStaleEnvMitigation` | Service, Endpoints, Selector, HTTP, PodExec, Pods |
| `service_endpoint_mitigation.py` | `ServiceEndpointMitigationOracle` | Endpoints, Selector, Pods |
| `sustained_readiness.py` | `SustainedReadinessOracle` | Pods |
| `target_port_mitigation.py` | `TargetPortMisconfigMitigationOracle` | Pods |
| `valkey_auth_mitigation.py` | `ValkeyAuthMitigation` | PodExec, Pods |
| `wrong_bin_mitigation.py` | `WrongBinMitigationOracle` | — |
| `wrong_pod_selection_mitigation.py` | `WrongPodSelectionMitigationOracle` | Endpoints, EndpointSlice, Selector, DNS, Workload |

Five classes subclass `MitigationOracle` and inherit its replica/pod logic:
`ConntrackMitigationOracle`, `CpuThrottlingMitigationOracle`, `FDMitigationOracle`,
`KafkaProducerLeakOracle`, `NightlyRebalanceOOMMitigationOracle`.

Seed observation for the census: at least 14 oracles here inspect Endpoints and at least
9 inspect Services. The benchmark HAS Service-aware verification machinery.
`missing_service_social_network` — a Service-deletion fault — uses none of it. The
structurally adjacent `wrong_service_selector` DOES, via `ServiceEndpointMitigationOracle`
(`problems/wrong_service_selector.py:42`). There is no `missing_service*` file in
`oracles/` — VERIFIED ABSENT (`ls oracles/ | grep -i missing_service` returns nothing).

### `workload.py` specifically (37 lines)

SHA-256 `d1bb10c9230ab1b2bba541df16552013323b0160a928d9854fad41210a356468`.
Contents: `truncate()` helper (4-9) and `class WorkloadOracle(Oracle)` (12-37),
`importance = 3.0` (13).

- Is it an Oracle subclass? YES — `workload.py:12`.
- Behaviour: `evaluate()` (19) calls `self.wrk.collect(number=1)` (21) then
  `self.wrk.collect(number=50)` (22); returns `{"success": False}` if any `entry.ok` is
  falsy (24-28), `{"success": True}` otherwise (30-32), `{"success": False}` on any
  exception (33-36).
- Attached to any problem? Yes, two — `ephemeral_port_range_hotel_reservation.py:37` and
  `trainticket_f17.py:36`; plus one commented-out site at `rbac_misconfiguration.py:31`.
  NOT attached to `missing_service_social_network`.

---

## E. `sregym/conductor/problems/missing_service.py` (60 lines)

### `inject_fault()` — lines 42-50

Decorated `@mark_fault_injected` (42). Builds
`VirtualizationFaultInjector(namespace=self.namespace)` (45), calls
`injector._inject(fault_type="missing_service", microservices=[self.faulty_service])` (46-49).

Dispatch: `generators/fault/base.py:39-46` `_inject` -> `_invoke_method("inject", ...)` (43)
-> `base.py:58-65` resolves `inject_missing_service` via `getattr`. Note
`time.sleep(6)` at `base.py:46` after every injection.

`inject_missing_service` (`generators/fault/inject_virtual.py:295-307`):

| Line | Action |
|---|---|
| 298 | SAVES: `_get_service_yaml(service)` — runs `kubectl get service {name} -n {ns} -o yaml`, `yaml.safe_load` (`inject_virtual.py:3106-3108`) |
| 299-301 | DELETES: `kubectl delete service {service} -n {namespace}` |
| 303 | PERSISTS: `_write_yaml_to_file(...)` -> `/tmp/{service}_modified.yaml` (`inject_virtual.py:3123-3130`) |
| 306 | RESTARTS ALL PODS: `kubectl delete pods --all -n {namespace}` |
| 307 | WAITS: `self.kubectl.wait_for_stable(namespace=self.namespace)` |

Exactly one object deleted (the `user-service` Service), spec saved to
`/tmp/user-service_modified.yaml`, then a namespace-wide pod restart and stability wait.

Two consequences that matter for the study, both observed:
- The injector uses `kubectl` SUBPROCESSES (`self.kubectl.exec_command(...)`), unlike the
  oracle which uses the Python client.
- Lines 306-307 mean that by the time `evaluate()` runs, all pods have been recreated and
  waited to stability. The Deployment/Pod state the oracle inspects has been actively
  restored to health by the fault injection itself.

### `recover_fault()` — lines 52-59

Decorated `@mark_fault_injected` (52). Calls `injector._recover(...)` (56-58) ->
`recover_missing_service` (`inject_virtual.py:309-320`):

| Line | Action |
|---|---|
| 312-313 | `kubectl delete service {service} -n {namespace}` (idempotence guard) |
| 314-315 | `kubectl apply -f /tmp/{service}_modified.yaml -n {namespace}` |
| 319 | `kubectl delete pods --all -n {namespace}` — comment: "Restart all pods to clear cached DNS failures from the fault period" |
| 320 | `self.kubectl.wait_for_stable(namespace=self.namespace)` |

### `@mark_fault_injected`

`sregym/utils/decorators.py:1-16`. Wraps the method; on exception it RE-RAISES if
`method.__name__ == "inject_fault"` (6-8), otherwise warns and sets `result = None` (9-11)
— injection failures are fatal, recovery failures are not. Then unconditionally sets
`self.fault_injected = method.__name__ == "inject_fault"` (13).

### Oracle wiring at lines 38 and 40 — CONFIRMED

    38        self.diagnosis_oracle = LLMAsAJudgeOracle(problem=self, expected=self.root_cause)
    39        self.app.create_workload()
    40        self.mitigation_oracle = MitigationOracle(problem=self)

Line 40 is the stock, unsubclassed `MitigationOracle`, imported at line 2.
Line 39 (between them) starts the workload that nothing will read.

---

## F. SREMut isolated worker — precise input contract

Files: `SREMut/src/sremut/original_oracle_worker.py` (315 lines),
`SREMut/src/sremut/original_oracle_adapter.py` (529 lines).

### argv

Exactly two positional arguments; anything else returns exit 64 (`worker:296-297`).

    argv[0] = input_path   (canonical-JSON request, read at line 301)
    argv[1] = output_path  (canonical-JSON result, written at line 303)

Adapter construction, `adapter:418`:

    argv = [str(STOCK_PYTHON), "-I", "-B", str(WORKER_PATH),
            str(destinations["original_oracle_input"]), str(worker_output)]

### stdin

`stdin=subprocess.DEVNULL` (`adapter:439`). The worker never reads stdin.

### environment

Exactly four variables (`adapter:419-424`):

    {"LC_ALL": "C.UTF-8", "PATH": "/usr/bin:/bin",
     "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1"}

`cwd=SREGYM_ROOT` (436), `shell=False` (438), `start_new_session=True` (442) so the
process group can be SIGKILLed on timeout (448).

### Files expected

- READS `argv[0]`; READS `SREGym/sregym/conductor/oracles/mitigation.py` for the hash
  check (`worker:175`); READS `/home/sakibbuet2k19/.kube/config` (`worker:21`, loaded
  250-254 with `persist_config=False`).
- WRITES exactly one file: `argv[1]`, via `_atomic_write` (303). That path MUST NOT
  already exist (`worker:221-222`, `ORIGINAL_ORACLE_OUTPUT_REUSE_FORBIDDEN`).
  O_EXCL|O_NOFOLLOW, fsync of file and parent dir (225-241).
- `_atomic_write` is called exactly once in the worker (definition 220, sole call 303):
  the worker does NOT write the `evidence_paths` files. The adapter does (`adapter:517-519`).

### Input document

Canonical JSON: sorted keys, `separators=(",",":")`, UTF-8, no floats, no NaN/Infinity,
no duplicate keys — with byte-for-byte round-trip enforcement at `worker:118-119`.

Exactly four top-level fields (`worker:33-35`, enforced 131):

| Field | Constraint | Lines |
|---|---|---|
| `kubernetes_context` | must equal `"kind-kind"` | 133-134 |
| `namespace` | must equal `"social-network"` | 135-136 |
| `captured_replica_baseline` | dict, NON-EMPTY; keys match `[a-z0-9](?:[-a-z0-9.]*[a-z0-9])?` and <= 253 chars; values `int` >= 0 (strict `type(count) is not int`, so `bool` is rejected) | 137-148 |
| `evidence_paths` | dict with exactly `original_oracle_input`, `original_oracle_result`, `original_oracle_stderr`, `original_oracle_stdout`; all four values distinct; each a safe relative path (non-empty, no leading `/`, no `\`, no empty/`.`/`..` segments) | 149-155 |

### Does it require a captured replica baseline?

YES, mandatorily and non-empty (`worker:137-139`). Format is exactly the stock oracle's
own `{deployment_name: replicas}` map, injected at `worker:257`:
`oracle.replica_count = dict(candidate["captured_replica_baseline"])`.

This is SREMut substituting for `capture_baseline()`, which it cannot call because that
would require a pre-fault cluster. The faithful value is the 27-entry map in
`baselines/missing_service_social_network/run-01/deployments.json` (all `replicas: 1`).

### Standalone invocability under `SREGym/.venv/bin/python`?

YES. Evidence:

- `grep -nE 'from sremut|import sremut' original_oracle_worker.py` -> no output.
  VERIFIED ABSENT.
- Complete import list (machine-extracted): lines 7, 9-17 are `__future__`, `hashlib`,
  `importlib.metadata`, `json`, `os`, `pathlib`, `platform`, `re`, `sys`, `typing` — ALL
  STDLIB. The only non-stdlib imports are lazy, inside `evaluate_once`:
  `from kubernetes import client, config` (247) and
  `from sregym.conductor.oracles.mitigation import MitigationOracle` (248), both reached
  only after `sys.path.insert(0, str(SREGYM_ROOT))` at 246.
- The SREGym venv satisfies the pinned dependency check (`worker:28-32`) — confirmed on
  disk: `PyYAML-6.0.2.dist-info`, `jsonschema-4.23.0.dist-info`, `kubernetes-30.1.0.dist-info`.
- Kubeconfig exists with the required context: `/home/sakibbuet2k19/.kube/config`,
  `current-context: kind-kind` (line 12).
- `SREMut/.venv` is not needed and does not exist.

Guards that fire before any cluster call (`worker:171-177`): Python must be exactly
3.12.3; the three dependency versions must match exactly; `mitigation.py` must hash to
`a087fd38...fca8b`. All verified true on 2026-08-26.

### Exact minimal command for one stock-oracle Boolean — NOT RUN

Step 1 — write the canonical request (keys already sorted; exact bytes, no trailing
newline). Replace the baseline map with the full 27-entry healthy map for a faithful run:

    {"captured_replica_baseline":{"user-service":1},"evidence_paths":{"original_oracle_input":"e/input.json","original_oracle_result":"e/result.json","original_oracle_stderr":"e/stderr.txt","original_oracle_stdout":"e/stdout.txt"},"kubernetes_context":"kind-kind","namespace":"social-network"}

Step 2 — invoke (output path must not already exist):

    cd /home/sakibbuet2k19/sremut/SREGym && \
    env -i LC_ALL=C.UTF-8 PATH=/usr/bin:/bin \
            PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 \
      /home/sakibbuet2k19/sremut/SREGym/.venv/bin/python -I -B \
      /home/sakibbuet2k19/sremut/SREMut/src/sremut/original_oracle_worker.py \
      <ABS_PATH_TO_INPUT.json> <ABS_PATH_TO_OUTPUT.json>

Exit codes (`worker:294-311`): 0 success, 64 wrong argv count, 65 `WorkerFailure` (code on
stderr), 70 unexpected exception. The Boolean lands in the output file as
`returned_boolean`, with `outcome` in {`RETURNED_TRUE`, `RETURNED_FALSE`,
`ORACLE_EXCEPTION`, `ORIGINAL_ORACLE_RETURN_SHAPE_INVALID`}.

CAVEAT: this bypasses the adapter, so it performs no git tag/submodule provenance
verification (`adapter:220-275`), creates no evidence-policy candidates, writes no journal
entry, and enforces no timeout. Adequate for a smoke check; NOT admissible as study
evidence.

---

## G. SREGym commit `ba07faf1` — "Fix duplicate PVC mitigation oracle (#953)"

     .../oracles/duplicate_pvc_mounts_mitigation.py     | 74 ++++++++---------
     .../test_duplicate_pvc_mounts_mitigation.py        | 93 ++++++++++++++++------
     2 files changed, 99 insertions(+), 68 deletions(-)

Shape of the fix: EXTRA AND ALTERED PREDICATES INSIDE AN EXISTING DEDICATED ORACLE — no
new class, no subclass. `DuplicatePVCMountsMitigationOracle(Oracle)` already existed; the
commit rewrote three of its helpers. The inadequacy fixed was twofold. First, rollout
completeness: `_deployment_ready` gained `and (status.replicas or 0) == desired`, closing
a window where a Deployment reported updated/ready/available counts at target while extra
replicas still existed. Second, and more relevant to this study, the oracle's reachability
probe was judged to be testing the wrong thing: `_has_current_ready_endpoint()` — which
read `core_v1_api.read_namespaced_endpoints` and required a ready Endpoints address whose
`target_ref` pointed at a current-ReplicaSet pod — was DELETED and replaced by
`_current_ready_pod_ip()`, which selects a Ready pod owned by the active ReplicaSet and
returns its `status.pod_ip` directly. Correspondingly `_run_query_check` dropped its
`read_namespaced_service` call and its check that the Service exposes TCP `query_port`,
and switched its probe URL from
`http://{service}.{namespace}.svc.cluster.local:{port}/api/services` to
`http://{pod_ip}:{port}/api/services`. The commit thus moved that oracle AWAY from
Service/Endpoints/DNS-mediated verification TOWARD direct pod-IP probing — trading
Service-layer coverage for robustness against Service-layer flakiness. Upstream's framing
is visible in the docstring edit: "its Jaeger query endpoint" -> "its current Jaeger pod".
The commit touches neither `mitigation.py` nor `missing_service.py`.

---

## Verdict

**Based only on code read, the mitigation verdict for `missing_service_social_network` is
determined by:** the single Boolean returned by the stock `MitigationOracle.evaluate()`
(`sregym/conductor/oracles/mitigation.py:55-118`), which after a 60-second rollout settle
window (line 64) checks only two things — (a) that every Deployment recorded in the
pre-fault replica snapshot still exists, has `spec.replicas != 0`, and has
`ready_replicas >= desired`; and (b) that the namespace has at least one pod, every pod is
in phase `Running`, and every container is ready and neither waiting-with-reason nor
terminated-with-reason-other-than-`Completed`. That Boolean is stored unmodified at
`conductor.py:272` and flattened verbatim into `Mitigation.success` at `main.py:456-459`.
No Service, Endpoints, EndpointSlice, selector, port, DNS, socket, or HTTP check
participates — each verified absent by grep. No workload result participates, despite a
workload running throughout the attempt. The deleted `user-service` Service is invisible
to every predicate the verdict depends on.

**Confidence: HIGH** for the claim that the mitigation oracle's Boolean is the entire
recorded verdict, and for the claim that no Service-layer signal enters it. Specifically
supported by:

1. The file's complete 29-entry attribute-access inventory, machine-extracted, containing
   only Deployment and Pod fields.
2. Its two-line import list.
3. Ten independent absence greps, each returning zero code hits, with the single `Service`
   hit isolated to a docstring at line 26 that itself concedes the gap.
4. A single-assignment proof for `results["Mitigation"]`: `grep -n 'self\.results\['`
   returns 8 lines, only line 272 assigns it, and `_finish_problem` (367-386) is
   teardown-only.
5. The full 131-line `mitigation_oracle` grep showing `missing_service.py:40` binds the
   bare stock class, not `CompoundedOracle`.
6. The 7-line `WorkloadOracle` grep showing it is attached to two other problems and one
   commented-out site, never this one.

**What I could NOT determine from static reading alone:**

- Whether `_wait_for_rollouts` converges quickly or consumes the full 60 s under MS-M01.
  That depends on live Deployment status, and the namespace-wide pod restart at
  `inject_virtual.py:306-307` makes the timing genuinely uncertain.
- Whether the tasklist actually used in the study's runs registers both stages. I read
  `sregym/conductor/tasklist.yml.example:108-110`, an EXAMPLE file; the effective tasklist
  is runtime configuration I have not located.
- Whether anything in `clients/` (agent-side harnesses) applies additional gating outside
  the SREGym conductor. I did not survey that tree; the claims above are scoped to the
  SREGym verdict path.
- Whether the un-guarded `pod.status.container_statuses` iteration at `mitigation.py:101`
  is reachable in practice. The CODE asymmetry versus the fixed sibling oracle is
  observed; whether a pod in this namespace ever presents `None` there is a runtime
  question.
- Whether `capture_baseline()` in fact succeeded in any given historical run. The
  empty-baseline degradation is real and reachable via `run-oracle.py`, but code alone
  cannot tell which path produced any particular recorded result.

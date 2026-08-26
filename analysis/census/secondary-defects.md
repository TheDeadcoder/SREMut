# Secondary defects in the SREGym verifier layer

Pinned commit `ba07faf1a322f9b6d4a279643bb796aa2f36f64b`. Every claim below is
supported by file:line in that commit. These are distinct from the primary finding
(the generic mitigation oracle's blindness to Service-layer faults) and each is
independently checkable.

---

## D1 — `run-oracle.py` evaluates with an empty replica baseline

### The defect

`run-oracle.py` obtains a problem instance and calls the mitigation oracle directly:

```
run-oracle.py:47   problem = problem_registry.get_problem_instance(problem_id)
run-oracle.py:50   if not hasattr(problem, "mitigation_oracle") or problem.mitigation_oracle is None:
run-oracle.py:57   result = problem.mitigation_oracle.evaluate()
```

It never calls `capture_baseline()`. Verified absent:
`grep -n 'capture_baseline' run-oracle.py` returns **no output**.

`MitigationOracle.replica_count` is initialised to `{}` at `mitigation.py:20` and is
populated only by `capture_baseline()` (`mitigation.py:23-32`), whose sole production
call site is `conductor.py:227`, immediately before `problem.inject_fault()` at
`conductor.py:229`.

With `replica_count == {}`, the loop at `mitigation.py:69` (`for name in self.replica_count:`)
iterates zero times, so **all three Deployment predicates are skipped**:

| Predicate | Lines | Skipped when baseline empty |
|---|---|---|
| Deployment deleted | mitigation.py:70-73 | yes |
| Deployment scaled to 0 | mitigation.py:76-79 | yes |
| ready_replicas < desired | mitigation.py:81-84 | yes |

Evaluation then reduces to the namespace pod sweep at `mitigation.py:86-115`.

### Upstream already documented the hazard

The oracle's own comment, `mitigation.py:16-19`, states the consequence exactly:

```
mitigation.py:16   # Populated by capture_baseline() once the app is deployed. It cannot be
mitigation.py:17   # filled in here: the Problem is built before deploy_app(), so the
mitigation.py:18   # namespace is still empty and every replica check below would be
mitigation.py:19   # skipped, letting "scale to 0" and "delete the deployment" pass.
```

So the failure mode is known to the authors; `run-oracle.py` simply reproduces the
state the comment warns about.

### Which problem_ids would be misjudged

Any problem whose mitigation oracle relies on the inherited Deployment predicates.
That is the 27 problem_ids on the bare generic `MitigationOracle`, plus the 4 of the 6
`MitigationOracle` subclasses that call `super().evaluate()`
(`fd_exhaustion.py:28`, `kafka_producer_leak_mitigation.py:9`,
`nightly_rebalance_oom_mitigation.py:27`, `conntrack_mitigation.py:106`).

**Bare generic (27 problem_ids):**

- `auth_miss_mongodb`
- `init_container_dependency_hang_astronomy_shop`
- `init_container_dependency_hang_hotel_reservation`
- `init_container_dependency_hang_social_network`
- `liveness_probe_misconfiguration_astronomy_shop`
- `liveness_probe_misconfiguration_hotel_reservation`
- `liveness_probe_misconfiguration_social_network`
- `missing_configmap_hotel_reservation`
- `missing_configmap_social_network`
- `missing_service_astronomy_shop`
- `missing_service_hotel_reservation`
- `missing_service_social_network`
- `operator_wrong_operator_image`
- `persistent_volume_affinity_violation`
- `pod_anti_affinity_deadlock`
- `pod_cidr_exhaustion_hotel_reservation`
- `pvc_claim_mismatch`
- `rbac_misconfiguration`
- `resource_request_too_large`
- `resource_request_too_small`
- `service_port_conflict_astronomy_shop`
- `service_port_conflict_hotel_reservation`
- `service_port_conflict_social_network`
- `sidecar_port_conflict_astronomy_shop`
- `sidecar_port_conflict_hotel_reservation`
- `sidecar_port_conflict_social_network`
- `taint_no_toleration_social_network`

A `scale-to-0` or `delete-the-deployment` non-repair on any of these is accepted by
`run-oracle.py`, because with an empty baseline nothing checks replica counts and the
surviving pods of other services satisfy the pod sweep.

The two subclasses that override `evaluate()` without `super()` are **not** affected
through the inherited path, but `CpuThrottlingMitigationOracle` guards explicitly:
`cpu_throttling_mitigation.py:42-44` returns `None` (-> `{"success": False}`) when
`self.replica_count` is empty, printing "Pre-injection Deployment baseline was not
captured". That is the defensive check the generic oracle lacks.

### Scope limit, stated plainly

`run-oracle.py` is a standalone utility, not the conductor path. The benchmark's own
evaluation flow (`conductor.py:227` then `:229`) does capture the baseline. D1 matters
because the utility is shipped, is documented for direct use
(`run-oracle.py:7-11`), and silently produces a weaker verdict than the benchmark's.
We have not observed a published result computed via `run-oracle.py`.

---

## D2 — namespace-wide pod sweep counts benchmark infrastructure against the verdict

### The defect

`MitigationOracle.evaluate()` sweeps **every pod in the namespace**:

```
mitigation.py:86    pod_list = kubectl.list_pods(namespace)
mitigation.py:95    for pod in pod_list.items:
mitigation.py:96        if pod.status.phase != "Running":
mitigation.py:97            print(f"[X] Pod {pod.metadata.name} is in phase: {pod.status.phase}")
mitigation.py:98            all_normal = False
```

There is no label selector and no exclusion of non-application pods. The benchmark's
own load generator runs in that namespace, so its pods are swept alongside the
application's.

### The workload Job is in the application namespace

`Problem.run_default_workload = True` (`problems/base.py:7`), and the conductor starts
the workload during deploy:

```
conductor.py:870    if problem.run_default_workload:
conductor.py:871        problem.app.start_workload()
```

For social-network, `SocialNetwork.create_workload()` builds a `Wrk2WorkloadManager`
bound to the app namespace (`service/apps/social_network.py:97-109`, `namespace=self.namespace`),
and the resulting Job is `wrk2-job` in that namespace — the name the project's own
baseline capture reads at `harness/capture_healthy_baseline.sh:133`
(`kubectl logs -n social-network job/wrk2-job`).

Only one problem opts out: `search_rate_retry_collapse.py:21` sets
`run_default_workload = False`. Every other problem whose app defines a workload is
exposed. Apps defining `create_workload`: `social_network.py:94`,
`hotel_reservation.py:252`, `astronomy_shop.py:119`, `train_ticket.py:73`,
`fleet_cast.py:288`, `blueprint_hotel_reservation.py:52`.

### Observed persistence — measured, not inferred

In `SREMut/experiments/g02-run-01`, the 2-second sampler recorded wrk2 pods in
`.status.phase == "Failed"` persisting after node disruption:

| Husk | Age when observed | Cleared by |
|---|---|---|
| `wrk2-job-4mx7g` | **9 days** | only by an explicit `undeploy_app()` |
| `wrk2-job-nrjw7` | **17 hours** | only by an explicit `undeploy_app()` |
| `wrk2-job-tdj4m` | **17 hours** | only by an explicit `undeploy_app()` |

These dated to a host reboot (2026-08-25 17:38). They did not self-clear. A separate,
transient husk created by ordinary pod deletion (`wrk2-job-tgn96`) cleared in <= 2.8 s,
so the two classes behave very differently.

Note the display/field distinction: `kubectl get pods` showed STATUS `Unknown`, but
`.status.phase` — the field `mitigation.py:96` actually reads — was `Failed`.

### Consequence

Any evaluation performed while a husk is present returns `success: False` at
`mitigation.py:96-99` regardless of agent behaviour. The verdict is then attributable
to benchmark infrastructure, not to the agent. In `g02-run-01` the treatment oracle
cleared the last not-Running observation by only **8.405 s**; the G1 repetitions,
with the R1 60-second null-agent episode, cleared it by 67.989-69.624 s.

Direction of the error: this defect produces **false negatives** (a correct repair
marked failed), the opposite direction to the primary finding. Both are verifier
defects; they are not the same defect and should not be conflated.

### wrk2 Job restart/backoff configuration

Determined from source: the Job is created by `Wrk2WorkloadManager`/`KubeCtl` job
helpers (`service/kubectl.py:340-392`, `create_job`/`BatchV1Api`). The manager
re-creates a workload pod after disruption — observed directly in `g02-run-01`, where
`wrk2-job-tgn96` failed at 11:03:11.616Z and replacement `wrk2-job-sxx2h` was already
`Running`, so the Job's controller does spawn replacements while leaving the failed
pod object in place.

**UNCERTAIN:** the exact `backoffLimit` and `restartPolicy` values on the created Job
were not pinned to a file:line in this pass. The persistence and replacement behaviour
above are measured facts from `g02-run-01/samples.jsonl`; the precise Job spec fields
should be quoted before the paper states them.

---

## D3 — `WrongUpdateStrategyMitigationOracle.evaluatePods()` is dead code

### Verification performed before writing this up

Repository-wide grep for `evaluatePods` across `*.py`, `*.md`, `*.yml`, `*.yaml`
returns 13 hits in 5 files. Grouped:

| File | Definition | Call site from `evaluate()` |
|---|---|---|
| `operator_misoperation/security_context_mitigation.py` | :15 | **:52** |
| `operator_misoperation/non_existent_storage_mitigation.py` | :17 | **:54** |
| `operator_misoperation/overload_replicas_mitigation.py` | :16 | **:54** |
| `operator_misoperation/invalid_affinity_mitigation.py` | :17 | **:52** |
| `operator_misoperation/wrong_update_strategy_mitigation.py` | :15 | **none** |

Four of five siblings call it. The fifth does not.

Dynamic dispatch was checked as a possible hidden caller. The only `getattr`-based
method dispatch in the fault/oracle layer is `generators/fault/base.py:58-65`, which
builds `inject_<fault_type>` / `recover_<fault_type>` names — it cannot reach an oracle
method. All other `getattr` uses found are attribute lookups with defaults
(`conductor.py:193,:204`; `network_policy_oracle.py:97-98`; and similar), not method
dispatch. No `__getattr__`, no `globals()[...]`, no `eval(` reaching oracle methods.

The only callers of any mitigation oracle's `evaluate()` are `conductor.py:268`,
`run-oracle.py:57`, and `compound.py:36` — none of which reach `evaluatePods` except
through the oracle's own `evaluate()`.

**Conclusion: `evaluatePods` in `wrong_update_strategy_mitigation.py` is dead code.**

### What the oracle therefore checks

`evaluate()` (`wrong_update_strategy_mitigation.py:49-73`) reads the TiDBCluster CR
(`:53`) and the StatefulSet (`:59`), and returns
`{"success": not fault_applied}` where `fault_applied` is
`cr_strategy == "SomeStrategyForUpdate"` (`:64-69`).

It verifies only that the injected sentinel string is no longer present in the CR. It
performs **no pod readiness check at all**, so an agent that removes the bad strategy
value while leaving the cluster unhealthy is accepted.

### Affected problem_ids

- `operator_wrong_update_strategy_fault`

### The one-line fix

Mirror the four siblings — call the already-written helper and gate on it, e.g. at the
top of `evaluate()` (`:49`):

```python
if not self.evaluatePods().get("success"):
    return {"success": False, "reason": "pods not ready"}
```

The method body at `:15-47` is already correct and needs no change; only the call is
missing.

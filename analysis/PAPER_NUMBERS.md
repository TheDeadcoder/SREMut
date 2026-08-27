# PAPER_NUMBERS — canonical figures with sources

Every number the write-up cites, with the exact file it came from. Quote from here;
do not retype from memory. Anything not traceable to a file is marked **UNSOURCED**.

Generated 2026-08-26. Paths are relative to `/home/sakibbuet2k19/sremut/`.

---

## 1. Environment

| Item | Value | Source |
|---|---|---|
| SREGym commit | `ba07faf1a322f9b6d4a279643bb796aa2f36f64b` | `SREGym/` HEAD (detached); `SREMut/profiles/missing_service_social_network/pilot-v1.yaml:21` |
| SREGym-applications commit | `2b2f9c6c2e97c44abbfcc44af1cf2f994bbb04f8` | `SREGym/` submodule; `pilot-v1.yaml:23` |
| Stock oracle SHA-256 | `a087fd38399cfca4c2de764dbbab837d6100350ab9f71b89552cafe2c91fca8b` | `pilot-v1.yaml:56`; re-verified by `sha256sum SREGym/sregym/conductor/oracles/mitigation.py` |
| Workload parser SHA-256 | `d1bb10c9230ab1b2bba541df16552013323b0160a928d9854fad41210a356468` | `SREMut/src/sremut/workload_evidence.py:21` |
| kind | `kind v0.32.0 go1.26.3 linux/amd64` | `manifests/host-environment.txt` |
| Kubernetes server | `v1.32.0` | `manifests/host-environment.txt`; confirmed `kubectl get nodes` |
| Node count | 4 (1 control-plane, 3 workers), all Ready | `manifests/cluster-smoke-20260815T140837Z.txt` (`node_count=4`, `ready_node_count=4`) |
| kubectl pin | `/home/sakibbuet2k19/.local/lib/sremut/kubectl/v1.32.0/kubectl` | `pilot-v1.yaml:25`; `kubectl version --client` -> `v1.32.0` |
| Host kubectl (NOT used) | `v1.36.3` | `manifests/host-environment.txt` |
| Oracle runtime | CPython 3.12.3, kubernetes **30.1.0**, PyYAML 6.0.2, jsonschema 4.23.0 | `SREGym/.venv/lib/python3.12/site-packages/*.dist-info`; `pilot-v1.yaml:36-43` |
| Runner runtime (declared) | CPython 3.12.3, kubernetes **32.0.1** | `SREMut/pyproject.toml`; `pilot-v1.yaml:86-96` |

---

## 2. Oracle observation surface

Source: `SREGym/sregym/conductor/oracles/mitigation.py` (118 lines). Full analysis:
`SREMut/analysis/G01_mitigation_verdict_path.md`.

### What it reads — the complete list

| Line | Call | Kind | Fields |
|---|---|---|---|
| 30 | `kubectl.list_deployments(ns)` | Deployment | `.metadata.name`, `.spec.replicas` |
| 38 | `kubectl.list_deployments(ns)` | Deployment | `.spec.replicas`, `.status.updated_replicas`, `.status.ready_replicas`, `.status.unavailable_replicas` |
| 66 | `kubectl.list_deployments(ns)` | Deployment | `.metadata.name`, `.spec.replicas`, `.status.ready_replicas` |
| 86 | `kubectl.list_pods(ns)` | Pod | `.metadata.name`, `.status.phase`, `.status.container_statuses[].name`/`.ready`/`.state.waiting.reason`/`.state.terminated.reason` |

Complete machine-extracted attribute inventory: 29 unique accesses, all Deployment/Pod
fields plus `time.monotonic`, `time.sleep`, `self.*`. Import list is two lines
(`import time`, `from ...base import Oracle`).

### The ten verified-absent greps (zero counts)

Source: `SREMut/analysis/G01_mitigation_verdict_path.md` §A.4, re-runnable against
`mitigation.py`.

| Pattern | Count | Note |
|---|---:|---|
| `service` | **0** | — |
| `Service` | **1** | docstring only, line 26 — no code reference |
| `endpoint` / `Endpoint` | **0** | — |
| `EndpointSlice` | **0** | — |
| `selector` | **0** | — |
| `targetPort` / `target_port` | **0** | `port` = 3 hits, all substrings of `import`/`importance` |
| `dns` / `DNS` / `resolve` | **0** | — |
| `socket` / `connect` | **0** | — |
| `http` / `HTTP` / `requests` / `urllib` / `curl` | **0** | — |
| `wrk` / `workload` / `Workload` | **0** | — |
| `subprocess` / `exec` | **0** | — |

### The docstring at mitigation.py:26, verbatim

```
mitigation.py:26        This is not a full resource baseline: Services and resources created by
mitigation.py:27        inject_fault() are outside it. Faults that must preserve or validate
mitigation.py:28        those resources need a custom mitigation oracle.
```

### Verdict path

`results["Mitigation"]` assigned once at `conductor.py:272`; `_finish_problem`
(`conductor.py:367-386`) is teardown-only; `main.py:456-459` flattens verbatim into
`Mitigation.success`.

---

## 3. G1 — three three-state repetitions, driver (ii)

Source: `SREMut/experiments/g1-run-0{1,2,3}/three-state.json`, summarised in
`SREMut/experiments/G1_SUMMARY.md`. Protocol: `SREMut/experiments/PROTOCOL_G1.md`.

### 3x3 verdict matrix — in-process oracle

| Run | HEALTHY | FAULTED | RESTORED |
|---|---|---|---|
| `g1-run-01` | `{"success": true}` | `{"success": true}` | `{"success": true}` |
| `g1-run-02` | `{"success": true}` | `{"success": true}` | `{"success": true}` |
| `g1-run-03` | `{"success": true}` | `{"success": true}` | `{"success": true}` |

### 3x3 verdict matrix — SREMut worker

| Run | HEALTHY | FAULTED | RESTORED |
|---|---|---|---|
| `g1-run-01` | `RETURNED_TRUE` | `RETURNED_TRUE` | `RETURNED_TRUE` |
| `g1-run-02` | `RETURNED_TRUE` | `RETURNED_TRUE` | `RETURNED_TRUE` |
| `g1-run-03` | `RETURNED_TRUE` | `RETURNED_TRUE` | `RETURNED_TRUE` |

**9 of 9 in-process verdicts `{"success": true}`; 9 of 9 worker verdicts
`RETURNED_TRUE`, exit 0.**

### Worker `raw_result_sha256` — 1 distinct value across all 9

`c955e57777ec0d73639dca6748560d00aa5eb8e12f13ebb2ed9656add3908f97`

Identical in all nine invocations, from a byte-identical 887-byte canonical input and
an identical 27-entry replica baseline (`8e42c0db98368a0a04c582e5f0c40ee2…`).

### Workload per state per run

| Run | State | Rounds | Requests | Non-2xx | Rate |
|---|---|---:|---:|---:|---:|
| `g1-run-01` | HEALTHY | 10 | 10240 | 0 | **0.0000 %** |
| `g1-run-01` | FAULTED | 10 | 10240 | 1036 | **10.1172 %** |
| `g1-run-01` | RESTORED | 10 | 10240 | 0 | **0.0000 %** |
| `g1-run-02` | HEALTHY | 10 | 10240 | 0 | **0.0000 %** |
| `g1-run-02` | FAULTED | 10 | 10240 | 1044 | **10.1953 %** |
| `g1-run-02` | RESTORED | 10 | 10240 | 0 | **0.0000 %** |
| `g1-run-03` | HEALTHY | 10 | 10240 | 0 | **0.0000 %** |
| `g1-run-03` | FAULTED | 10 | 10238 | 950 | **9.2792 %** |
| `g1-run-03` | RESTORED | 10 | 10240 | 0 | **0.0000 %** |

### Pooled

| State | Rounds | Requests | Non-2xx | Rate |
|---|---:|---:|---:|---:|
| HEALTHY | 30 | 30720 | 0 | **0.0000 %** |
| FAULTED | 30 | 30718 | 3030 | **9.8639 %** |
| RESTORED | 30 | 30720 | 0 | **0.0000 %** |

### Timings (seconds)

| Run | deploy | stabilize | inject | recover | total | HEALTHY ip/wk | FAULTED ip/wk | RESTORED ip/wk |
|---|---:|---:|---:|---:|---:|---|---|---|
| `g1-run-01` | 131.268 | 4.511 | 41.952 | 35.015 | 559.211 | 0.315/7.568 | 0.371/7.421 | 0.329/7.496 |
| `g1-run-02` | 129.065 | 4.487 | 40.919 | 35.057 | 566.245 | 0.315/7.375 | 0.635/7.387 | 0.366/7.563 |
| `g1-run-03` | 127.19 | 4.49 | 41.255 | 35.246 | 554.503 | 0.307/7.77 | 0.596/7.4 | 0.354/7.23 |

### R1 intervals (injection returning -> treatment oracle start)

| Run | injection_finished_utc | oracle started_utc | interval |
|---|---|---|---:|
| `g1-run-01` | 2026-08-26T19:33:11.991257+00:00 | 2026-08-26T19:34:11.991797+00:00 | **60.001 s** |
| `g1-run-02` | 2026-08-26T19:43:00.151040+00:00 | 2026-08-26T19:44:00.151547+00:00 | **60.001 s** |
| `g1-run-03` | 2026-08-26T19:52:40.603242+00:00 | 2026-08-26T19:53:40.603819+00:00 | **60.001 s** |

### Margins to the last not-Running pod observation

Source: `G1_SUMMARY.md` §6, computed from each run's `samples.jsonl`.

| Run | Last not-Running observation | Margin |
|---|---|---:|
| `g1-run-01` | 2026-08-26T19:33:02.368Z (`unique-id-service-655cc57f6d-9xkgk`/Failed) | **69.624 s** |
| `g1-run-02` | 2026-08-26T19:42:52.163Z (`user-mention-service`, `user-timeline-service`) | **67.989 s** |
| `g1-run-03` | 2026-08-26T19:52:32.550Z (2 app pods + `wrk2-job-b9jgr`/Failed) | **68.054 s** |

Minimum **67.989 s**. For contrast, `g02-run-01` was **8.405 s**
(`SREMut/experiments/g02-run-01/`).

---

## 4. G3 — production path, real Conductor

Source: `SREMut/experiments/g3-run-01/conductor-path.json` and `conductor-path.md`.

### `self.results` verbatim

```json
{
  "Diagnosis": {
    "accuracy": null,
    "checklist": [],
    "judgment": null,
    "reasoning": "LLM judge backend is not initialized - skipping evaluation",
    "submission": null,
    "success": null
  },
  "Mitigation": {
    "success": true
  },
  "TTL": 0.005100250244140625,
  "TTM": 60.29948878288269
}
```

| Item | Value | Source |
|---|---|---|
| `results["Mitigation"]` | `{"success": true}` | assigned `conductor.py:272` |
| `results["TTM"]` | `60.29948878288269` | assigned `conductor.py:273` |
| `results["TTL"]` | `0.005100250244140625` | assigned `conductor.py:254` |
| `results["Diagnosis"]["success"]` | `None` (**null, not False**) | `conductor.py:253`; judge returns null via `judge.py:58-69` |
| injection returned | 2026-08-26T21:13:16.938364+00:00 (took 41.678 s) | instrumented wrapper |
| injection -> evaluation interval | **60.002 s** | 0.006 s elapsed + 59.994 s protocol wait |
| `start_problem()` | `['diagnosis', 'mitigation']` -> stages `['diagnosis', 'mitigation']`, 175.579 s | `conductor.py:388` |
| `_finish_problem` | reached and **suppressed** (True) | disclosed intervention |
| recovery | 35.474 s | `missing_service.py:52` |

### Cluster state at the verdict instant

Evaluation 21:14:16.940351Z -> 21:14:17.238360Z, bracketed by sampler ticks 2.654 s
apart (`g3-run-01/samples.jsonl`, 321 samples):

| Sample | user_service_present | EndpointSlices | Services | Pods | not Running |
|---|---|---:|---:|---:|---:|
| 21:14:16.320Z | **false** | **0** | 29 | 28 | **0** |
| 21:14:18.974Z | **false** | **0** | 29 | 28 | **0** |

R3 does not apply: zero samples between injection returning and evaluation showed any
pod outside `Running`.

### G02 §C deviations closed

**5 of 9 closed** — (1) stage machinery, (2) `submit()` handshake, (3)
`results["Mitigation"]`/`TTM`, (4) diagnosis stage, (5) the real `_evaluate_mitigation`.
**4 remain open, all deliberate**: (6) `_finish_problem` suppressed (post-verdict), (7)
noise disabled, (8) Loki disabled, (9) no agent container / CSV publishing.

---

## 5. Workload analysis

Source: `SREMut/experiments/g02-run-01/post/workload-analysis.md` and
`workload-rounds.csv` (1132 rows).

### Binomial comparison

| Quantity | Predicted Binomial(1024, 0.10) | Observed |
|---|---:|---:|
| Mean rate | 10.0000 % | **9.9689 %** |
| Mean count | 102.4 / 1024 | 102.1 / 1024 |
| Per-round stdev | **0.9375 pp** | **0.9347 pp** |
| Deviation from 10 % | — | **z = -1.11** |
| n (completed rounds) | — | 1126 |
| min / median / max | — | 6.9336 % / 9.9609 % / 12.8906 % |

### The compose-post ratio

```
SREGym/SREGym-applications/socialNetwork/wrk2/scripts/social-network/mixed-workload.lua
  113      local read_home_timeline_ratio = 0.60
  114      local read_user_timeline_ratio = 0.30
  115      local compose_post_ratio       = 0.10
```

Script selected by SREGym at `SREGym/sregym/service/apps/social_network.py:24`.
Compose-post calls user-service unconditionally: `ComposePostHandler.h:575-577`
(`_ComposeCreaterHelper` launched), blocked on at `:594`; zero `if/else/switch` in
lines 570-600. The 60 % home-timeline path reaches SocialGraphService but only via
`GetFollowers` (`HomeTimelineHandler.h:126`), which does not touch the user-service pool
(`SocialGraphHandler.h`: only `FollowWithUsername` :824-897 and `UnfollowWithUsername`
:898-977 do).

### Healthy comparator

| Run | Completed rounds | `Non-2xx` lines |
|---|---:|---:|
| run-01 | 30 | **0** |
| run-02 | 14 | **0** |
| run-03 | 26 | **0** |
| **Total** | **70** | **0** |

Source: `baselines/missing_service_social_network/run-0{1,2,3}/workload.log`. Enforced
by the gate at `harness/capture_healthy_baseline.sh:194-208`:

```bash
194  echo "Validating workload output..."
195
196  if grep -q "Non-2xx or 3xx responses" "${run_path}/workload.log"; then
197    echo "ERROR: Workload log contains failed HTTP responses."
198    exit 1
199  fi
```

---

## 6. Census

Source: `SREMut/analysis/census/` — `CENSUS.md`, `coverage.csv`, `oracle-surfaces.csv`,
`problem-oracle.csv`, `problem-fault.csv`.

### Denominators

| Denominator | Value | Source |
|---|---:|---|
| **Primary (used)** | **118** | unique keys in `PROBLEM_REGISTRY`, `registry.py:124-356` |
| Conservative secondary | 99 | intersection of registry with `Problem List.md` (116 rows) |
| Paper (May 2026) | 90 | `SREGym/README.md:22` |

**Framing:** the paper reports 90; the pinned commit is from August 2026 and registers
118. Registry growth between publication and this commit is the plain explanation. Not
a defect. Registry/docs drift is one footnote in `CENSUS.md`.

**Paper's evaluated subset: NOT DETERMINABLE from the repository.** Candidates named
and set aside: `tests/e2e-testing-scripts/registry.txt` (91 entries, 9 absent from the
registry), `docs/SREGym-Lite.md` (21), `sregym/conductor/tasklist.yml.example` (87, an
example file).

### Headline counts

| Quantity | Count |
|---|---:|
| Oracle classes with `evaluate()` | 60 |
| problem_ids | 118 |
| ... bare generic `MitigationOracle` | 27 |
| ... ... of those BLIND | 4 |
| ... ... of those ADEQUATE | 15 |
| ... ... of those UNCERTAIN | 8 |
| ADEQUATE (all oracle types) | 80 |
| **BLIND (all oracle types)** | **6** |
| UNCERTAIN (all oracle types) | 32 |
| Injectors that restart pods or wait for stability | 16 |
| Oracles whose surface is pods+deployments only | 34 |
| **THE HEADLINE: both of the above** | **8** |

### The 8-problem headline table

Injector restarts pods or waits for stability AND oracle surface is pods+deployments
only — the structure confirmed end-to-end in `g1-run-0{1,2,3}`.

| problem_id | oracle |
|---|---|
| `missing_service_astronomy_shop` | `MitigationOracle` |
| `missing_service_hotel_reservation` | `MitigationOracle` |
| `missing_service_social_network` | `MitigationOracle` |
| `pod_cidr_exhaustion_hotel_reservation` | `MitigationOracle` |
| `sidecar_port_conflict_astronomy_shop` | `MitigationOracle` |
| `sidecar_port_conflict_hotel_reservation` | `MitigationOracle` |
| `sidecar_port_conflict_social_network` | `MitigationOracle` |
| `taint_no_toleration_social_network` | `MitigationOracle` |

= 8/118 = 6.8 %; against the 99 denominator, 8.1 %.

Three (`missing_service_*`) are confirmed; the other five are **predictions the census
makes, not results** — not executed.

### The 6 BLIND problem_ids

| problem_id | oracle | what it would need to observe |
|---|---|---|
| `missing_service_social_network` | `MitigationOracle` | a Service read, an EndpointSlice/Endpoints read, DNS of the Service FQDN, or any request routed through it |
| `missing_service_hotel_reservation` | `MitigationOracle` | same |
| `missing_service_astronomy_shop` | `MitigationOracle` | same |
| `pvc_claim_mismatch` | `MitigationOracle` | a PersistentVolumeClaim read (bind status / claimRef), or a persistence check |
| `assign_to_non_existent_node` | `AssignNonExistentNodeMitigationOracle` | Deployment `nodeSelector`/`nodeName`, or Node existence; it reads only pods (`:16`) and a name-prefix existence test (`:20`) |
| `workload_imbalance` | `ImbalanceMitigationOracle` | the mutated container command/env; it reads only pod CPU via `kubectl top` (`:25`), a derived metric |

### The 32 UNCERTAIN

Reason: the perturbed resource kind is not determinable from the problem class — the
injection is delegated to Khaos, `inject_tt.py`, or a kernel/hardware injector whose
concrete mutation was not traced to file:line at the uniform depth applied to all 118.
These are **UNCERTAIN, not PENDING**: attempted at the same depth as every other row
and unresolved.

### Two call-graph method corrections

1. `KubeCtl.exec_command` is a **host kubectl subprocess** (`kubectl.py:719`,
   `subprocess.run(command, shell=True)`), not a pod exec.
   `connect_get_namespaced_pod_exec` and `stream(` are **verified absent from
   `kubectl.py` entirely**. Only 4 oracles do genuine pod exec: `alert_oracle.py:51`,
   `valkey_auth_mitigation.py:37,:44`, `feature_flag_http_probe_mitigation.py:58`,
   `secret_rotation_stale_env_mitigation.py:165`.
2. Two of six `MitigationOracle` subclasses do **not** inherit the parent pod sweep:
   `CpuThrottlingMitigationOracle` (`:81-113`) and
   `KubeletEvictionThresholdMisconfigMitigationOracle` (`:100-118`) override
   `evaluate()` without `super().evaluate()`. The other four do
   (`fd_exhaustion.py:28`, `kafka_producer_leak:9`, `nightly_rebalance_oom:27`,
   `conntrack:106`).

**One classification correction, disclosed in `CENSUS.md`:** the first pass counted the
injector's incidental pod deletion as an observable perturbation, wrongly marking
`missing_service_*` ADEQUATE. Corrected: when an injector deletes pods *and then waits
for stability*, the churn is transient and self-healing and does not make an oracle
adequate. The known-answer case caught it.

---

## 7. Secondary defects

Source: `SREMut/analysis/census/secondary-defects.md`.

| | Defect | Affected | Fix size |
|---|---|---|---|
| **D1** | `run-oracle.py` calls `evaluate()` with no `capture_baseline()`, so `replica_count == {}` and all three Deployment predicates are skipped (`mitigation.py:69`) | **27** bare-generic problem_ids + 4 of 6 subclasses that call `super().evaluate()` | one line: call `capture_baseline()` before `run-oracle.py:57` |
| **D2** | namespace-wide pod sweep counts benchmark infrastructure; wrk2 husks in phase `Failed` persisted **9 days** and **17 hours** and never self-cleared | every problem whose app starts a workload Job in the app namespace; only `search_rate_retry_collapse.py:21` opts out (`run_default_workload = False`) | a label selector or an exclusion at `mitigation.py:95` |
| **D3** | `WrongUpdateStrategyMitigationOracle.evaluatePods()` defined at `:15`, **never called** — `evaluate()` (`:49-73`) omits it, unlike its 4 siblings | **1** problem_id: `operator_wrong_update_strategy_fault` | one line: `if not self.evaluatePods().get("success"): return {"success": False}` |

D1 direction: false **accept**. D2 direction: false **reject**. Opposite directions;
do not conflate.

---

## 8. Item A and Item B (this session)

### Item A — what TTM is anchored to

| Item | Finding | Source |
|---|---|---|
| `execution_start_time` declared | `= 0.0` | `conductor.py:74` |
| set on `start_problem()` entry | `time.time()` | `conductor.py:409` |
| **reset after injection** | `time.time()  # Reset: measure agent time only` | **`conductor.py:458`**, immediately after `_advance_to_next_stage(0)` at `:456`, which performs `_inject_fault()` |
| `TTL` | `time.time() - execution_start_time` | `conductor.py:254` |
| `TTM` | `time.time() - execution_start_time` | `conductor.py:273` |

**Anchor: the moment after injection completes and the first stage is armed.** So TTM
measures agent-episode time, not time-since-injection-start. g3-run-01 confirms:
TTM = 60.29948878288269 against a 60.002 s injection->evaluation interval.

**Is TTM reported anywhere?** It reaches the results CSV. `main.py:456-461` iterates
`conductor.results.items()`; `TTM`/`TTL` are top-level float values, not dicts, so the
`else` branch writes them as bare columns:

```python
main.py:456    for stage, outcome in conductor.results.items():
main.py:457        if isinstance(outcome, dict):
main.py:458            for k, v in outcome.items():
main.py:459                snapshot[f"{stage}.{k}"] = v
main.py:460        else:
main.py:461            snapshot[stage] = outcome
```

**Is it consumed?** No. Repo-wide, `TTM`/`TTL` appear only at their assignment
(`conductor.py:254`, `:273`) and in the adjacent log f-strings (`:258`, `:277`). The
`visualizer/` package references only `Mitigation.success` and `Diagnosis.success`
(10 occurrences each in `visualizer/queries.py`; `visualizer/process.py:107`). There is
**no ranking, sorting, aggregation, or comparison on TTM anywhere in the repository**.
The only other `TTL` hits are an unrelated cache in
`mcp_server/kubectl_server_helper/sliding_lru_session_cache.py`, and the single `TTM`
hit in `visualizer/interactive_deployment/go.sum:40` is a coincidental substring inside
a Go module hash.

**Stated plainly: TTM is recorded and written to the CSV, but never consumed by any
analysis in this repository.** How the paper's authors used it is not determinable from
source and is not speculated on here.

### Item B — H2, submission latency

Protocol registered before execution: `SREMut/experiments/PROTOCOL_W1.md`.
Runs: `SREMut/experiments/w1-delay0-0{1,2}/three-state.json`.

**H2 is NOT supported.** Both zero-delay runs returned `true`.

| Run | FAULTED in-process | FAULTED worker | injection -> oracle interval | pods at oracle instant | R3 |
|---|---|---|---:|---|---|
| `w1-delay0-01` | `{"success": true}` | `RETURNED_TRUE` | **0.000145 s** | 28/28 `Running`, 0 not ready | **RESEARCH VERDICT** |
| `w1-delay0-02` | `{"success": true}` | `RETURNED_TRUE` | **0.000154 s** | 28/28 `Running`, 0 not ready | **RESEARCH VERDICT** |

| Run | HEALTHY | FAULTED | RESTORED | deploy | inject | recover |
|---|---:|---:|---:|---:|---:|---:|
| `w1-delay0-01` | 0.0000 % | **10.3125 %** | 0.0000 % | 128.467 s | 41.474 s | 35.406 s |
| `w1-delay0-02` | 0.0000 % | **10.0098 %** | 0.0000 % | 127.917 s | 41.316 s | 35.808 s |

All worker `raw_result_sha256` values match the G1 value
`c955e57777ec0d73639dca6748560d00…`.

**Interpretation, per the pre-registered statement that either outcome is reportable:**
this is the *stronger* result for the primary claim. At an interval of ~0.15 ms — the
most adversarial timing obtainable — the oracle still accepts the unrepaired system,
and every pod was already `Running` because `inject_fault()`'s own `wait_for_stable`
(`inject_virtual.py:307`) had absorbed the churn before returning. The mechanism H2
predicted (dying pods still listed at `inject_fault()` return) did not occur in either
run. The "you waited for it to settle" objection to G1 and G3 is removed.

Total verdicts now: **13 of 13** measurements `true` on faulted or unrepaired states
across G1 (9), G3 (1), W1 (2 faulted) plus g02-run-01 (1).

---

## 9. KNOWN GAPS

Stated plainly. Each is a limit on what the numbers above support.

**1. g3-run-01's faulted workload rate is PENDING and unobtainable.** The pod-listing
jsonpath in `conductor_path_run.py` contained a literal newline instead of the
two-character escape, so kubectl returned `error: ... unterminated quoted string`,
exit 1, empty stdout, and zero rounds were ever counted. This is an instrument defect,
not a system property; reproduced and documented in
`SREMut/experiments/g3-run-01/conductor-path.md` §6. The faulted-window pod
(`wrk2-job-rmvs7`) ran for the full 11 minutes but was deleted by recovery and its logs
are unrecoverable. No re-run was performed. The script has been corrected (run sha
`00e7916f9da4fcc1…`, corrected sha `9f0c90fa54f14e6e…`). G1's and W1's workload
numbers used a different, correct script and are unaffected.

**2. 32 of 118 census rows are UNCERTAIN** — the perturbed resource kind could not
be traced to file:line at the uniform depth applied. Not PENDING; attempted and
unresolved.

**3. Noise disabled and Loki disabled in every run.** All runs used
`ConductorConfig(deploy_loki=False, enable_noise=False)`, matching the three frozen
healthy baselines. **Direction of bias:** disabling noise makes the cluster *quieter*
and a `true` verdict *more* likely, so it works in favour of the primary claim rather
than against it. This is a real limitation and should be stated in the paper: the
result has not been shown to hold with SREGym's noise manager active. Loki is deployed
outside the application namespace and is not read by the oracle, so its absence cannot
affect the verdict.

**4. n = 3** for the full three-state repetition (G1), plus 1 production-path run (G3),
2 zero-delay runs (W1) and 1 pilot (g02-run-01). The three-state *pattern* is
established; no rate claim is made.

**5. One problem, one fault, one cluster.** Everything measured end-to-end concerns
`missing_service_social_network` under MS-M01 (no-op after Service deletion) on a
single 4-node kind cluster. The census extends *structurally* to 118 problem_ids but
only 3 of the 8 headline ids have been executed; the other 5 are predictions.

**6. ADEQUATE in the census is structural, not behavioural.** It means the oracle reads
the perturbed kind or a functional signal — not that it has been shown to reject a
non-repair. Only `missing_service_social_network` has been tested end-to-end.

**7. Perturbed-kind detection uses regex classification over injector bodies.**
Spot-checked against `missing_service`, `target_port` and `sidecar_port_conflict`; not
exhaustively verified for all 118.

**8. The paper's own evaluated subset is unknown**, so the census denominator cannot be
aligned with the paper's 90. Both 118 and 99 are given.

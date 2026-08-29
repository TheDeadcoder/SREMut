# PAPER_NUMBERS — canonical figures with sources

> ## Addendum — 2026-08-29
>
> **The text below this addendum predates the mutant execution and is preserved unchanged
> as the historical record. Where it says the MS-M01/M02/M03 matrix "has not been run",
> read it as referring to the sealed v1.2 matrix, which is still unexecuted.**
>
> Two distinct studies now exist and must not be conflated:
>
> | Study | Status |
> |---|---|
> | Pre-registered MS-M01/M02/M03 **MS-I1..MS-I5** study — RFC 3161 timestamped pre-registration and pre-execution commit, historical-style evidence (per-run JSON plus raw artifacts) | **9/9 complete, 2026-08-29** |
> | Sealed **v1.2** matrix — authenticated run identities, hash-chained journal, external anchor, all six invariants | **0/9, unexecuted** |
>
> "Confirmatory" as used in `README.md` refers to the **first** of these. The word
> "official" and the status `OFFICIAL_FROZEN_ATTEMPT` remain reserved for the **second**,
> and no artifact in this repository may use them for the executed study.
>
> Deviations from the frozen pre-registration and the limits on the nine records are in
> [`DEVIATIONS_AND_LIMITS.md`](../DEVIATIONS_AND_LIMITS.md).


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
| **Primary (used)** | **123** | unique keys in `PROBLEM_REGISTRY`, `registry.py:124-356`, read from the runtime registry |
| Conservative secondary | 104 | intersection of registry with `Problem List.md` (116 rows) |
| Paper (May 2026) | 90 | `SREGym/README.md:22` |

The previously published 118/99 were computed with a regex whose character class excluded
`-`, silently dropping five hyphenated IDs. Corrected in R1; see `CENSUS.md` and
`METHOD_AUDIT.md`.

**Framing:** the paper reports 90; the pinned commit is from August 2026 and registers
123. Registry growth between publication and this commit is the plain explanation. Not
a defect. Registry/docs drift is one footnote in `CENSUS.md`.

**Paper's evaluated subset: selected by `tasklist.yml`.** `get_problem_ids()` reads
`sregym/conductor/tasklist.yml` and returns `list(tasklist["all"]["problems"])`
(`registry.py:388-390`); only when that file is absent does it fall back to the whole
registry (`registry.py:384-386`). This supersedes the earlier statement that the evaluated
subset was NOT DETERMINABLE. The file itself is absent from the pinned repository — only
`tasklist.yml.example` (87 entries) is committed — so the *mechanism* is now determined
even though the specific list the authors ran is not recoverable from this checkout.

### Headline counts

Source of record: `analysis/census/ledger.json`, emitted by
`analysis/census/generate_census.py`. Set-equality against the runtime registry is
enforced by `SREMut/tests/test_census_completeness.py` (6 tests, verified non-vacuous).

The **counts** are generated; the **verdicts** they tally are hand-read from each oracle's
`evaluate()`. No mechanical rule reproduces them — a reconstructed classifier disagrees
with the published verdict on 48 of 123 rows. See `CENSUS.md`, "Other headline counts".

BLIND rose from 6 to 8 on 2026-08-28 (R2 Part A) after hand-verifying rows scored ADEQUATE
on a prose-attributed resource kind. **The 123 rows were not verified at the same depth:**
28 received deeper injector-and-oracle verification and 95 did not, so the headline is a
lower bound — *at least 8 of 123, from 28 rows hand-verified*. `Oracle classes with evaluate()` fell from 60 to 59
(R2 Part B): every route to 60 counted the abstract base `Oracle` itself, whose `evaluate`
is an `@abstractmethod` (`base.py:19-22`). Derivations:
`analysis/census/R2_PARTA_CODE_ONLY_RECLASSIFICATION.md`,
`analysis/census/R2_PARTB_ORACLE_COUNT.md`.

| Quantity | OLD (published) | **NEW** |
|---|---:|---:|
| **Denominator (registry problem_ids)** | 118 | **123** |
| Conservative secondary denominator | 99 | **104** |
| Oracle classes with `evaluate()` | 60 | **59** |
| ADEQUATE | 80 | **83** |
| **BLIND** | 6 | **8** |
| UNCERTAIN | 32 | 32 |
| oracle_kind DEDICATED | 82 | **83** |
| oracle_kind BARE_GENERIC | 27 | **31** |
| oracle_kind MITIGATIONORACLE_SUBCLASS | 6 | 6 |
| oracle_kind COMPOUND | 3 | 3 |
| bare-generic BLIND | 4 | **6** |
| bare-generic ADEQUATE | 15 | **17** |
| bare-generic UNCERTAIN | 8 | 8 |
| injectors restart pods / wait | 16 | 16 |
| oracles pods+deployments only | 34 | **38** |
| structural-shape rows | 8 | 8 |
| BLIND % of denominator | 5.0847 % | **6.5041 %** |
| structural-shape % of denominator | 6.7797 % | **6.5041 %** |
| `registry.txt` entries absent from registry | 9 | **4** |

The two columns above compose two separate corrections. **R1** (denominator 118 -> 123)
added five rows, all ADEQUATE, which moved the denominator but not the BLIND count.
**R2 Part A** then reclassified two previously-ADEQUATE rows as BLIND, taking the count
from 6 to 8. An earlier draft of this section stated that the BLIND set remained
unchanged at six; that was true only of R1 and is **withdrawn** — the current set is the
eight in `analysis/census/ledger.json`.

**8 of 123 = 6.5041 % is the fraction currently identified**, on rows verified at unequal
depth. It is a lower-bound discovery count, not an estimate of benchmark-wide prevalence,
and must never be quoted as one.

Within the **bare generic subset alone** — 31 problem_ids, 6 BLIND / 17 ADEQUATE /
8 UNCERTAIN — resolving the 8 UNCERTAIN rows in either direction gives a mechanical range
of **6 to 14 for that subset**. That range is scoped to the bare generic subset; it is not
a global range over the 123 registered IDs.

### Denominator correction and its cause

The prior denominator of 118 was wrong. The census parsed registry keys with
`r'"([a-z0-9_]+)":'`, whose character class excludes `-`; the five hyphenated IDs at
`registry.py:136-139,164` never matched. All five are ordinary literal dict keys — AST
confirms one dict literal, 123 keys, no programmatic addition, no duplicates. Full
analysis in `analysis/census/METHOD_AUDIT.md`.

**A second, distinct error: a confabulation.** The prior CENSUS.md asserted that
`k8s_target_port-misconfig` was *"not even a valid registry key form"*. That is false — a
Python dict key may contain a hyphen, and the ID is a literal key at `registry.py:164`.
Having observed a discrepancy caused by its own parser, the analysis invented a property
of SREGym to explain it and published that invention as an observation. The parser defect
caused an undercount; the confabulation manufactured a false finding about the artefact
under study.

### The five newly classified problem_ids

| problem_id | oracle (file:line) | injector | verdict |
|---|---|---|---|
| `k8s_target_port-misconfig` | `TargetPortMisconfigMitigationOracle` (`target_port.py:30`) | `inject_misconfig_k8s` patches Service `spec.ports[].targetPort` 9090->9999 (`inject_virtual.py:38-48`) | **ADEQUATE** — reads the exact mutated field (`target_port_mitigation.py:17-19`) |
| `revoke_auth_mongodb-1` | `MitigationOracle` (`revoke_auth.py:37`) | revokes the MongoDB admin role in-container, deletes the dependent pod (`inject_app.py:31-57`) | **ADEQUATE (incidental)** |
| `revoke_auth_mongodb-2` | `MitigationOracle` (`revoke_auth.py:37`) | same, `mongodb-rate` | **ADEQUATE (incidental)** |
| `storage_user_unregistered-1` | `MitigationOracle` (`storage_user_unregistered.py:37`) | drops the MongoDB admin user, deletes the dependent pod (`inject_app.py:85-105`) | **ADEQUATE (incidental)** |
| `storage_user_unregistered-2` | `MitigationOracle` (`storage_user_unregistered.py:37`) | same, `mongodb-rate` | **ADEQUATE (incidental)** |

"Incidental" is load-bearing: the oracle never observes the perturbed thing (in-container
database state). It detects these faults only because the dependent service calls
`initializeDatabase`, which dials as `admin:admin` and `log.Panic()`s on failure
(`cmd/rate/db.go:38-41`, `cmd/geo/db.go:28-30`, called from `main.go` before serving), so
the pod CrashLoopBackOffs persistently and the pod sweep at `mitigation.py:96,102-104`
catches it. Had the services degraded per-request instead of crashing, these four would be
BLIND.

**Note on `k8s_target_port-misconfig` and MS-M03.** SREMut's frozen mutant registry
defines MS-M03 as a targetPort misconfiguration. The shipped SREGym problem for that fault
is verified by a dedicated oracle that reads the mutated field, and is **ADEQUATE**. This
was classified on source evidence before the MS-M03 relationship was considered.

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
| **D1** | `run-oracle.py` calls `evaluate()` with no `capture_baseline()`, so `replica_count == {}` and all three Deployment predicates are skipped (`mitigation.py:69`) | **31** bare-generic problem_ids (**35** including the 4 of 6 subclasses that call `super().evaluate()`) — corrected 2026-08-29 from 27, which predated the registry-enumeration fix | one line: call `capture_baseline()` before `run-oracle.py:57` |
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

Governing protocol: `SREMut/experiments/PROTOCOL_W1.md` — a historical documented
protocol; its text states it was written before execution and git does not independently
corroborate that. Runs: `SREMut/experiments/w1-delay0-0{1,2}/three-state.json`.

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

**Interpretation, per the protocol's advance statement that either outcome is reportable
(a historical documented protocol; git does not corroborate the ordering):**
this is the *stronger* result for the primary claim. At an interval of ~0.15 ms — the
most adversarial timing obtainable — the oracle still accepts the unrepaired system,
and every pod was already `Running` because `inject_fault()`'s own `wait_for_stable`
(`inject_virtual.py:307`) had absorbed the churn before returning. The mechanism H2
predicted (dying pods still listed at `inject_fault()` return) did not occur in either
run. The "you waited for it to settle" alternative explanation for G1 and G3 is
substantially weakened — not removed; a null result on one named alternative does not
close the space of alternatives.

Totals now: **18 of 18 faulted-state instrument readings** across the 10 included
historical runs returned `true` on unrepaired states — 21 of 21 once the excluded runs are
counted. Of the three excluded runs, **two** contributed faulted readings
(`w2-noise-00-INERT` 2, `w3-hotel-01` 1), all `true`; `w2-hotel-01` was abandoned at the
healthy gate and contributed **none**. Recomputed from the run records into
`experiments/RESULT_LEDGER.json` (`experiments/build_result_ledger.py`); the earlier
figure of 13 was an undercount that omitted the g02 sidecar instrument artifacts and the
later runs.

| instrument | included historical | all runs |
|---|---:|---:|
| in-process `MitigationOracle` | 9 | 11 |
| isolated SREMut worker | 8 | 9 |
| real `Conductor` (G3) | 1 | 1 |
| **total** | **18** | **21** |

**Counting rule.** A reading is one faulted-state verdict from one provenance category in
one run. `RESULT_LEDGER.json` has three provenance categories — `in_process`, `worker`,
`conductor` — and all counts here are in those terms. They rest on two underlying
execution mechanisms (oracle called in-process, whether by the driver or the real
`Conductor`, versus oracle run as an isolated subprocess); that conceptual pair is not the
ledger's three categories and the two groupings are not interchangeable.

**18 readings are not 18 independent observations** — two categories reading the same
faulted cluster in one run share one deployment, one injection and one cluster state, so
the pair is one observation read twice.

**Exact accounting.** 18 readings from the 10 included historical runs (`in_process` 9,
`worker` 8, `conductor` 1); 3 further faulted readings from **two** of the three excluded
runs (`w2-noise-00-INERT` 2, `w3-hotel-01` 1); `w2-hotel-01` was abandoned at the healthy
gate and produced **no faulted reading**. All-run totals `in_process` 11, `worker` 9,
`conductor` 1 = 21.

**On the worker hashes.** All 9 faulted worker readings carry `raw_result_sha256`
`c955e57777ec0d73…` — the SHA-256 of the canonical *output* bytes `{"success":true}`. The
*invocation descriptor* hashes to `8356ab1d92fada2ae856a3da26e5204beb375431fd12200f5ae2e00a2369171a`
(887 bytes) across the 8 included faulted worker evaluations and the excluded 9th. These
are **identical invocation descriptor bytes, not an identical input**: the descriptor
carries `kubernetes_context`, `namespace`, `captured_replica_baseline` and
`evidence_paths` only, and the live Kubernetes state the worker reads is external to it
and differed by state and by run.

**The run is the experimental unit:** 13 historical run directories, 10 included
(9 social-network, 1 hotel-reservation), of which G1 contributes three **separate**
repeated full three-state runs — separate in having their own deploy, injection and
recovery, not established as statistically independent — and hotel-reservation one
included single-category run. The four historical statuses are `HISTORICAL_INCLUDED`,
`HISTORICAL_EXCLUDED_INERT`, `HISTORICAL_EXCLUDED_ABANDONED` and
`HISTORICAL_EXCLUDED_SUPERSEDED`; `OFFICIAL_FROZEN_ATTEMPT` is reserved for the future
authenticated MS-M01/MS-M02/MS-M03 matrix.

### Item A (W1) — TTM anchor, restated

`execution_start_time` is reset at **`conductor.py:458`**, immediately after
`_advance_to_next_stage(0)` performs the injection, comment `# Reset: measure agent time
only`. TTM (`:273`) and TTL (`:254`) therefore measure the **agent episode**, not
time-since-injection-start.

TTM/TTL **do reach the results CSV** as bare columns via the `else` branch at
`main.py:460-461`. They are **never consumed**: `visualizer/` reads only
`Mitigation.success` and `Diagnosis.success`; there is no ranking, sorting, aggregation or
comparison on TTM anywhere in the repository.

### Part A (W2) — what the SREGym paper claims

Source: arXiv 2605.07161, "SREGym: A Live Benchmark for AI SRE Agents with High-Fidelity
Failure Scenarios" (Clark, Su, Pial, Tian, Gniedziejko, Jacobsen, Chen, Xu).

| Claim | Verbatim | Our measurement |
|---|---|---|
| §2.5 Mitigation Oracle | "The mitigation oracle is problem-specific to accurately reflect whether the target failure is truly mitigated." "The oracle checks whether the target fault is resolved and whether the target system has recovered to a healthy state." "**The mitigation oracle uses both client-side observability such as user request success rate and system-side observability of application processes, Kubernetes cluster, etc.**" | For `missing_service_social_network` the attached oracle uses **only** system-side signals. Client-side observability is verified absent (`http` 0, `workload` 0, `wrk` 0). Measured user request success rate ~90 % while the oracle reported success, in every faulted-state instrument reading of every included historical run. |
| §2.5 Diagnosis Oracle | validated at **Cohen's κ = 0.90** vs human experts (κ = 0.94 inter-LLM), Table 2 | No comparable validation is reported for mitigation oracles. |
| §3.1 | "Mitigation success rate measures whether the agent successfully mitigates failures (verified by the mitigation oracle)." Table 3: 78.5 % / 65.5 % / 57.3 % | Problem subset over which these were computed is **not stated** in the paper. |
| §2 design principle | "**Simulating faults, not symptoms.** We reject a common practice of existing benchmarks that use chaos engineering tools to create failure symptoms, which can only be mitigated by stopping the tools. Instead, we focus on simulating fine-grained faults." | **Different threat.** Concerns agents gaming the *injector*. Our finding is not that — our agent did nothing at all. **No tension claimed.** |

**The one contradiction, conservatively scoped:** the §2.5 description of client-side
observability does not hold for the **31 problem_ids on the bare generic
`MitigationOracle`** (the count after the R1 denominator correction), one of which we
measured end-to-end. It does hold for much of the
family (18/59 oracle classes reach TCP/HTTP, 3 consume workload).

**NOT VERIFIED:** no paragraph headed "Protection against reward hacking" was located.
Six fetch attempts; an enumeration of all bolded lead-ins in §2 and Appendix B found none
containing that phrase, and retrieval of the one paragraph reported to contain it was
inconsistent between fetches. **Not quoted here. Verify against the PDF before citing.**
Reference [76] resolved (single fetch, moderate confidence) to Wang, Mang, Cheung, Sen &
Song, 2026, "How We Broke Top AI Agent Benchmarks: And What Comes Next", RDI Berkeley Blog.

### Part D / D1 (W2 H3) — noise enabled

Protocol: `SREMut/experiments/PROTOCOL_W2.md`. Runs:
`SREMut/experiments/w2-noise-0{1,2}/three-state.json`.

**What `enable_noise=True` does** (`sregym/generators/noise/manager.py`): Chaos Mesh
experiments, auto-installed via helm if absent (`manager.py:249-263`). Catalog of **four**
(`catalog.py:14`), all `mode: one` (single random pod in the target namespace):
`pod-kill` and `pod-failure` (PodChaos), `network-delay` and `network-loss`
(NetworkChaos). **2 per cycle** (`manager.py:28`), each living **120 s**
(`manager.py:29`), **300 s cooldown** between cycles (`manager.py:30`), 5 s poll loop.

**Critical design finding: the conductor stops noise before EVERY evaluation.**
`conductor.py:476-483`, comment "Stop noise before evaluation to ensure clean
environment"; `NoiseManager.stop()` (`manager.py:84-95`) also **removes all active chaos
experiments** and force-strips finalizers. So in the production path, noise is not active
at the graded instant, by design.

| Run | HEALTHY | FAULTED | RESTORED | faulted workload | inj->oracle | R3 |
|---|---|---|---|---:|---:|---|
| `w2-noise-01` | `{"success": true}` | **`{"success": true}`** | `{"success": true}` | 10.3223 % (1057/10240) | 65.931 s | RESEARCH VERDICT |
| `w2-noise-02` | `{"success": true}` | **`{"success": true}`** | `{"success": true}` | 9.8340 % (1007/10240) | 65.712 s | RESEARCH VERDICT |

Both worker verdicts `RETURNED_TRUE`; all `raw_result_sha256` match the G1 value
`c955e57777ec0d73…`. At each faulted oracle instant: Service absent, 0 EndpointSlices, 29
services, 28 pods, **0 not Running**. Chaos Mesh confirmed installed and experiments
applied (`network-delay`, `network-loss` in both runs; `pod-kill` was not selected by
`random.sample` in either).

**H3 is not supported.** The verdict was `true` with noise active during the run. This is
the stronger outcome, and it is largely explained by design: the conductor quiesces noise
before grading.

### Part D / D2 (W2 H4) — second application: ABANDONED

Run: `SREMut/experiments/w2-hotel-01/three-state.json`, status
`STOPPED_AT_HEALTHY_GATE`.

`missing_service_hotel_reservation` deployed successfully (109.482 s) and was healthy:
10 rounds, **0/29447 non-2xx**, in-process oracle `{"success": true}`. The healthy gate
tripped because the **SREMut worker** exited 65 with `ORIGINAL_ORACLE_INPUT_INVALID`: it
pins `EXPECTED_NAMESPACE = "social-network"` (`original_oracle_worker.py:23`, enforced at
`:135`) and the hotel-reservation namespace differs.

**This is a scope boundary of our instrument, not a property of the application.** The
pinned namespace is fixed by the frozen evidence policy and was not relaxed. Per protocol
the item was abandoned and not debugged. **H4 remains untested.**

### Item A (W3) — Appendix B reward hacking, Appendix H limitations

Source: arXiv 2605.07161. Retrieved via ar5iv/arXiv HTML; **both renderings truncate the
appendices**, so parts below are marked NOT RETRIEVED rather than paraphrased.

**A1 — the paragraph exists.** Confirmed across two independent renderings, Appendix B
carries the bolded lead-in and opening sentence:

> "**Protection against reward hacking.** AI agents can exploit benchmark infrastructure
> to inflate scores without solving the underlying tasks"

**The threat described is agents tampering with the harness** — specifically "an agent
that discovers and disables the fault-injection services rather than reasoning about
actual faults". The paragraph then begins a comparison, "Neither AIOpsLab [14] nor
ITBench [41] protects a…", and is **truncated at that point in every rendering obtained**.

- **SREGym's concrete claimed protection: NOT RETRIEVED.** The paragraph cuts off before
  it is stated. Do not cite a protection mechanism from this analysis.
- **Citation numbering is ambiguous between renderings.** The citing sentence carries
  **[74]** in the ar5iv rendering; an earlier arXiv-HTML fetch attributed the
  reward-hacking material to **[76]**. Reference [76]/[74] resolves to Wang, Mang,
  Cheung, Sen & Song, 2026, "How We Broke Top AI Agent Benchmarks: And What Comes Next",
  RDI Berkeley Blog. **Verify the number against the PDF before citing.**

**The threat is not ours, and this must be stated plainly in the write-up.** SREGym's
reward-hacking concern is an agent *acting on* the benchmark infrastructure. Our finding
involves an agent that acted on nothing at all: a null submission, an untouched broken
system, and an oracle that accepted it. These are different failure modes and the paper
should not be represented as claiming protection against ours.

**No null-agent baseline is reported.** Across every section retrieved, no null, empty,
or do-nothing agent control appears. Given that [74]/[76] is the Berkeley RDI null-agent
work — whose method is precisely to run a do-nothing agent against a benchmark — the
paper cites that work while, on the retrieved text, not reporting the control it
describes. **Stated as an observation about retrieved text only**, since the appendices
are truncated and a control could appear in the unretrieved portion.

**A2 — Appendix H: NOT RETRIEVED.** No rendering obtained included Appendix H
"Limitations". Whether it acknowledges any mitigation-oracle limitation is **unknown from
this analysis**. This is a gap to close from the PDF before the write-up asserts anything
about what the paper does or does not concede.

**A3 — Table 4 bucket mapping: NOT ESTABLISHABLE from source.** The repo's only origin
taxonomy is the `Origin` column of `Problem List.md`, whose values are **`New` (95)** and
**`AIOpsLab` (21)** across 116 rows. The paper's Table 4 uses **Ported (34) / Similar (43)
/ New (13)** across 90. Different taxonomies, different totals, and no join key. Per the
method rule, **no mapping is attempted and no distribution of the 31 bare-generic
problem_ids across Table 4 buckets is reported.**

**A4 — Figure 3's `K8sNetworkPortMisconfig`: NOT DETERMINABLE from source.**
`grep -rni 'networkportmisconfig'` over the entire repository returns **no match**. The
name does not appear in `registry.py`, any problem module, or any documentation file. The
repo has several port-related problems, and **they do not share an oracle**, so the answer
genuinely depends on which one Figure 3 refers to:

| candidate problem_id | oracle kind | attachment |
|---|---|---|
| `service_port_conflict_{social_network,hotel_reservation,astronomy_shop}` | **BARE_GENERIC** | `problems/service_port_conflict.py:51` |
| `sidecar_port_conflict_{social_network,hotel_reservation,astronomy_shop}` | **BARE_GENERIC** | `problems/sidecar_port_conflict.py:40` |
| `incorrect_port_assignment` | DEDICATED | `problems/incorrect_port_assignment.py:51` |
| `unschedulable_incorrect_port_assignment` | COMPOUND | `problems/incorrect_port_assignment.py:58` |
| `ephemeral_port_range_hotel_reservation` | DEDICATED (`WorkloadOracle`) | `problems/ephemeral_port_range_hotel_reservation.py:37` |

Because the candidates split across bare-generic and dedicated oracles, **guessing the
mapping would materially change the claim**. Not asserted.

### Item B (W3) — H4-lite, second application: PARTIAL, primary result obtained

Protocol: `SREMut/experiments/PROTOCOL_W3.md`. Record:
`SREMut/experiments/w3-hotel-01/w3-result.json`.

**Registered deviation: SINGLE INSTRUMENT.** The in-process oracle only, inside
`conductor.py:269-271`'s try/except. The SREMut worker was skipped because it pins
`EXPECTED_NAMESPACE = "social-network"` (`original_oracle_worker.py:23`, enforced `:135`),
a frozen artifact that was not relaxed. **This run therefore carries one recorded verdict
reading, not two, and is weaker evidence than any G1 run.**

| State | in-process verdict | workload |
|---|---|---|
| HEALTHY | `{"success": true}` | 10 rounds, **0 / 29,602 non-2xx = 0.0000 %** |
| **FAULTED** | **`{"success": true}`** | 6 rounds, **10,625 / 17,700 = 60.0282 %** |
| RESTORED | not reached | not reached |

Deploy 85.299 s; injection 40.19 s; R1 interval **60.0006 s**; faulted oracle call
**40.863 s** (versus ~0.5 s on social-network — `_wait_for_rollouts` polled longer).

Live cluster check at ~05:23Z confirmed the faulted state persisted:
`Service/user-service` **NotFound**, 22 services, **0** EndpointSlices for user-service.

**What was observed, recorded descriptively.** In `w3-hotel-01` the same stock oracle
returned `{"success": true}` on a second application while the functional workload was
failing **60 %** of requests — six times the social-network rate, because
hotel-reservation's request mix depends far more heavily on the deleted Service's path.

**This run is not used as support for H4 or for cross-application generality.** It is
**incomplete** (killed mid-flight, RESTORED never measured, `three-state.json` never
written), it is **below R2** (6 faulted workload rounds against the 10 required, captured
post hoc), its **sampler observed the wrong namespace** (`sample_state.sh` hardcoded
`social-network`, so its `samples.jsonl` describes an unrelated namespace), and it is
**superseded** by `w4-hotel-01`, which repeated the measurement with both instrument
defects fixed. It is retained as a historical record, not as evidence for a generality
claim. The three limitations are itemised below.

**Three limitations of this run, all disclosed:**

1. **The run was killed mid-flight.** The supervising shell command hit a 10-minute tool
   timeout during STEP 12 and terminated the driver. **Operator/instrument failure, not
   system behaviour.** The faulted verdict had already completed; `three-state.json` was
   never written; the RESTORED state was never measured.
2. **Faulted workload is 6 rounds, not the >= 10 that R2 requires**, and was captured
   post-hoc from the live job rather than by the driver. Reported as an R2 shortfall.
3. **Cluster state at the oracle instant: NOT MEASURED.** `sample_state.sh` hardcodes
   `NAMESPACE="social-network"` (`sample_state.sh:35`); the `--problem-id` patch set the
   driver's Python global but not the separate bash sampler. `w3-hotel-01/samples.jsonl`
   therefore describes **social-network** (healthy, unrelated) and **must not be read as
   evidence about this run**. No clean-observation check at the verdict instant was
   possible. R3 is not applicable regardless, since it governs FALSE verdicts and this
   verdict is TRUE.

### Item C (W3) — final teardown

`hotel-reservation` and `chaos-mesh` namespaces deleted. Deleting `hotel-reservation`
removed the injected fault along with the application, so no separate `recover_fault()`
was performed on it.

Remaining namespaces: `default`, `kube-node-lease`, `kube-public`, `kube-system`,
`local-path-storage`, `observe`, `openebs`, `social-network`, `sregym`.

`social-network` confirmed restored: `user-service` present (ClusterIP 10.96.114.38,
9090/TCP), **30 services**, **1 EndpointSlice**, **27/27 deployments ready**, **28 pods
all Running**. All 4 kind nodes Ready.

**Residue:** 23 Chaos Mesh CRDs remain. They are cluster-scoped and survive namespace
deletion; they are inert with no controller running and no CRs present. Not deleted, since
CRD removal was not requested and is more invasive than namespace deletion.

### W4 — clean hotel-reservation three-state run (supersedes W3)

Protocol: `SREMut/experiments/PROTOCOL_W4.md` incl. **amendment R3-A**. Record:
`SREMut/experiments/w4-hotel-01/three-state.json`, status `COMPLETE`.

**Instrument fixes applied first** (neither touches the measurement path):
FIX 1 — `sample_state.sh` now takes `OUTPUT_JSONL [NAMESPACE]`, default `social-network`
so all prior invocations are unchanged; the driver resolves the namespace from the
registry and passes it. FIX 2 — driver launched under `setsid` (PPID=1, own session) and
supervised by short log reads, never a blocking wait; W3 died because a supervising
command hit a 10-minute tool timeout.

**Registered deviation (unchanged from W3): SINGLE INSTRUMENT.** In-process oracle only,
inside `conductor.py:269-271`. The SREMut worker pins
`EXPECTED_NAMESPACE = "social-network"` (`original_oracle_worker.py:23`, enforced `:135`),
a frozen artifact, not relaxed. **One provenance-category reading, not two — weaker than
any G1 run.**

#### Verdicts (raw dicts, UTC)

| State | verdict | started | finished |
|---|---|---|---|
| HEALTHY | `{"success": true}` | 2026-08-27T10:00:55.179875+00:00 | 10:00:55.428630+00:00 |
| **FAULTED** | **`{"success": true}`** | **2026-08-27T10:02:44.834097+00:00** | **10:03:10.422913+00:00** |
| RESTORED | `{"success": true}` | 2026-08-27T10:12:47.498426+00:00 | 10:12:47.830108+00:00 |

#### Workload

| State | Rounds | Requests | Non-2xx | Rate |
|---|---:|---:|---:|---:|
| HEALTHY | 10 | 29,565 | **0** | **0.0000 %** |
| **FAULTED** | 10 | 29,555 | 17,657 | **59.7429 %** |
| RESTORED | 10 | 29,605 | **0** | **0.0000 %** |

R2 satisfied in all three states (>= 10 rounds, same run).

#### R1 and timings

`inject_fault()` returned 10:01:44.833684+00:00; oracle started 10:02:44.834079+00:00 —
**interval 60.000395 s**. Faulted oracle call took **25.589 s**. Deploy 42.412 s, injection
48.653 s, recovery 33.110 s, total 1073.843 s.

#### Cluster state at the faulted oracle instant (sampler, now on the right namespace)

368 samples, `sampler_namespace: "hotel-reservation"`. Faulted steady window
10:01:12.963Z -> 10:06:57.799Z: **127 samples, ZERO with any pod not `Running`**.
`service_count == 22` in all 127 (healthy is 23) — one Service missing, the fault applied.
Pod count 20-21 throughout.

**Note on two sampler fields.** `user_service_present` and
`user_service_endpointslice_count` are named for the social-network target and are
**meaningless for hotel-reservation**, whose deleted Service is `mongodb-rate`. The valid
signal here is the `service_count` delta 23 -> 22. (This corrects W3, where
"`user-service` NotFound" was reported as evidence the fault had been applied; that
Service never existed in hotel-reservation and the check proved nothing.)

#### R3-A classification: **CASE (d)**

Verdict TRUE with every pod `Running` -> case (d), as in G1. Cases (a), (b) and (c) are
excluded by evidence, not by assumption.

Case (c) — a persistent pod failure making the oracle detect the fault *incidentally* —
was the live possibility, because `rate/server.go:267` calls `log.Panic()` on a Mongo
error. **It did not occur.** The rate pod trajectory across the whole run:

| pod | phase | window |
|---|---|---|
| `rate-b7766559f-pkmf2` | Running | 09:55:26.566Z -> 10:01:00.198Z |
| `rate-b7766559f-pkmf2` | **Failed** | 10:01:03.783Z only (1 sample) |
| `rate-b7766559f-ckqls` | Pending | 10:01:00.198Z -> 10:01:03.783Z |
| **`rate-b7766559f-ckqls`** | **Running** | **10:01:07.175Z -> 10:06:57.799Z** |

The only `Failed` rate pod is the **old** pod terminating under the injector's
`kubectl delete pods --all` churn, present for a single sample and gone by 10:01:07 — 97
seconds before the oracle started. Its replacement was `Running` at every two-second
sample spanning the oracle window; unsampled transients cannot be excluded. So `log.Panic()` did not produce a persistent crash, and the oracle
had no incidental pod-health signal to detect the fault by.

#### H4 result

**The complete W4 historical observation matched the source-derived prediction on one
second application.** The same stock `MitigationOracle` returned `{"success": true}` on a
second application, a different deleted Service (`mongodb-rate`, not `user-service`),
while **59.74 %** of user requests failed with HTTP 500 and every pod was healthy. The
predicted 60.0000 % and the observed 59.7429 % agree.

**The same false-acceptance behavior was observed on this second application.** That is
the whole of what the run establishes.

**This is suggestive exploratory evidence, not confirmation that blindness generally
travels with the oracle.** H4 was not corroborated as specified in advance
(`PROTOCOL_W4.md` is a historical documented protocol), and one run on one second
application is a single additional data point. A mechanism shared across the
`missing_service_*` family remains the plausible reading of the source, but it is not
established by this measurement.

### The 60 % explained from source

Deleted Service: **`mongodb-rate`** — `registry.py:177`,
`MissingService(app_name="hotel_reservation", faulty_service="mongodb-rate")`.

Request mix, `SREGym-applications/hotelReservation/wrk2/scripts/hotel-reservation/mixed-workload_type_1.lua:114-117`:

| Endpoint | Ratio | Backend Mongo | Reaches `mongodb-rate`? |
|---|---:|---|---|
| search | **0.600** | via rate service -> `RateMongoAddress` | **YES** |
| recommend | 0.390 | `RecommendMongoAddress` | no |
| user | 0.005 | `UserMongoAddress` | no |
| reserve | 0.005 | `ReserveMongoAddress` | no |

Chain, unconditional at every hop: `/hotels` -> `frontend.searchHandler`
(`services/frontend/server.go:71,155`) -> `search.Nearby` -> **`getRates`**
(`services/search/server.go:246`, no branch guards it) -> rate service ->
`"RateMongoAddress": "mongodb-rate:27017"` (`config.json:11`). On failure search returns
`codes.Unavailable "search dependency unavailable"` (`search/server.go:251-254`) and the
frontend converts it to **HTTP 500** (`frontend/server.go:196`) — fails closed, no partial
success. Each other service has its own database; the `HRate` in the recommendation
service is a `bson:"rate"` field of its own collection, not the rate service.

**Consistency:**

| | Predicted | Observed |
|---|---:|---:|
| W4 (10 rounds, 29,555 req) | 60.0000 % | **59.7429 %** (z = **-0.90**) |
| W3 (6 rounds, 17,700 req) | 60.0000 % | 60.0282 % |

Two independent runs bracket 60 %. Same structure as social-network, where
`compose_post_ratio = 0.10` predicted 10.0000 % and 9.8639 % was observed. **Nothing
unexplained.**

Two mechanisms worth recording. The rate service sits behind `memcached-rate` and takes
the Mongo path only on a cache miss — but the injector's `kubectl delete pods --all`
restarts memcached, so the cache is cold and every search takes it. And that path ends in
`log.Panic()` (`rate/server.go:267`), which *could* have crashed the rate pod
persistently; the W4 sampler shows it did not.

### W4 teardown

`hotel-reservation` deleted. Remaining namespaces: `default`, `kube-node-lease`,
`kube-public`, `kube-system`, `local-path-storage`, `observe`, `openebs`,
`social-network`, `sregym`. `social-network` restored: `user-service` present
(10.96.114.38, 9090/TCP), **30 services, 1 EndpointSlice, 27/27 deployments, 28 pods all
Running**. All 4 kind nodes Ready. No sampler or driver processes remain.

**Residue, complete list.** Nothing below has been deleted; teardown was not authorised
in the tasks that created them.

| residue | origin | state |
|---|---|---|
| **23 Chaos Mesh CRDs** | H3 noise runs (W2) | Cluster-scoped, so they survived the `chaos-mesh` namespace deletion in W3. Inert — no controller running, no CRs present. |
| **namespace `astronomy-shop`** | R2 Part A, 2026-08-28 | Empty. Created as a side effect of `ProblemRegistry().get_problem_instance()`. |
| **namespace `fleetcast`** | R2 Part A, 2026-08-28 | Empty. Same cause. |

The two namespaces were created unintentionally during a task specified as "no cluster
contact." `Problem.__init__` constructs an `Application`, whose constructor calls kubectl
and creates the application namespace if absent; ten `get_problem_instance()` calls made
to map problem_ids to oracle classes therefore created two namespaces. No workload was
deployed, no fault injected, nothing mutated in `social-network` or `hotel-reservation`,
and no evidence or experiment was affected. Disclosed in full at
`analysis/census/R2_PARTA_CODE_ONLY_RECLASSIFICATION.md`.

**Tooling status after the fact.** `analysis/census/generate_census.py` is **inert** with
respect to the cluster, confirmed from source: its `registry_ids()` calls only
`list(reg.PROBLEM_REGISTRY)` and `reg.get_problem_ids()`. `ProblemRegistry.__init__`
(`registry.py:123-359`) builds a dict of class references and lambdas and constructs
`KubeCtl()` (`registry.py:358`), whose `__init__` reads the local kubeconfig
(`kubectl.py:33`) and instantiates API client objects (`kubectl.py:37-38`) — it issues no
API request. `get_problem_ids()` (`registry.py:374-390`) reads `tasklist.yml` from disk.
Namespace creation happens only in `get_problem_instance()` (`registry.py:361-369`), which
calls `self.PROBLEM_REGISTRY.get(problem_id)()` and thereby constructs the Problem and its
Application. The census generator never calls it. `analysis/census/pid2class.json` was
rebuilt by AST-parsing the registry dict literal so that it makes no such call either.

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

**2. 32 of 123 census rows are UNCERTAIN** — the perturbed resource kind could not
be traced to file:line at the first-pass depth applied to every row. Not PENDING;
attempted and unresolved. The census as a whole is not verified to one uniform depth:
28 rows were re-read against injector source and oracle `evaluate()`, 95 were not.

**3. Noise: now partially addressed, and the caveat is weaker than first stated.**
G1, G3 and W1 ran with `enable_noise=False`. W2's two runs ran with noise genuinely
active (Chaos Mesh installed, experiments applied) and returned `true` in both. Moreover
the conductor **stops noise before every evaluation by design** (`conductor.py:476-483`;
`NoiseManager.stop()` removes all active experiments, `manager.py:84-95`), so the graded
instant is quiescent whether or not noise is enabled. The original caveat — that
disabling noise biases toward the claim — is therefore **substantially weakened**, though
not eliminated: `pod-kill` was never selected by `random.sample` in either W2 run, so the
most disruptive catalog entry remains untested at the graded instant. Loki is deployed
outside the app namespace and unread by the oracle.

**3b. Two W2 fidelity gaps, disclosed.** (i) The first noise run was **inert**:
`three_state_run.py` never calls `start_problem()`, so `nm.start()` at `conductor.py:451`
never fired and `enable_noise=True` had no effect. Detected before reporting, the run was
preserved as `w2-noise-00-INERT-noise-never-started`, the driver was patched to replicate
`conductor.py:442-451`, and both reported runs use the patched driver. (ii) The driver
stops noise before each evaluation (`conductor.py:476-483`) but does **not** replicate the
conductor's restart afterwards (`conductor.py:508-514`), so noise was active only between
deploy and the healthy evaluation, not during the faulted window. Both runs are reported
with this limitation stated.

**4. n = 3** for the full three-state repetition (G1), plus 1 production-path run (G3),
2 zero-delay runs (W1) and 1 pilot (g02-run-01). The three-state *pattern* is
established; no rate claim is made.

**5. Two problems, one fault type, one cluster.** *(Amended: this gap previously read
"One problem, one fault, one cluster." W4 expanded the measured scope from one application
to two; it did not establish cross-application generality, so the gap is narrowed rather
than retired.)*
Everything measured end-to-end concerns the `missing_service` fault type under MS-M01
(no-op after Service deletion) on a single 4-node kind cluster — but now across **two
applications**: `missing_service_social_network` (9 included historical runs) and
`missing_service_hotel_reservation` (`w4-hotel-01`, n=1, single-instrument). The census
extends *structurally* to 123 problem_ids; only 2 of the 8 BLIND ids have been executed,
and the other 6 are predictions.

**6. ADEQUATE in the census is structural, not behavioural.** It means the oracle reads
the perturbed kind or a functional signal — not that it has been shown to reject a
non-repair. *(Superseded: this gap previously ended "Only `missing_service_social_network`
has been tested end-to-end.")* Two problem_ids have now been tested end-to-end,
`missing_service_social_network` and `missing_service_hotel_reservation`.

**6b. Census verification coverage.** 28 of 123 rows are hand-verified against injector
source and oracle `evaluate()` — the 6 originally BLIND, the 5 added in R1, and the 17
audited in R2 Part A3, three pairwise-disjoint sets. 72 of 123 rows carry at least one
resource kind attributed from prose rather than code. The BLIND headline is therefore a
**lower bound**: at least 8 of 123, from 28 rows hand-verified.

**7. Perturbed-kind detection uses regex classification over injector bodies.**
Spot-checked against `missing_service`, `target_port` and `sidecar_port_conflict`; not
exhaustively verified for all 123.

**8. H4 (second application) was MEASURED once — see W4, which supersedes W3.**
`w4-hotel-01` is a complete three-state run with both instrument defects fixed:
`{"success": true}` in all three states, 59.7429 % faulted workload failure, R1 60.0004 s,
127 consecutive samples with zero not-`Running` pods, R3-A case (d). It remains
**single-category** (one recorded verdict reading, not two) and **n=1** for this
application, and it is exploratory rather than confirmatory evidence. The superseded W3
note follows:

**8b. (superseded) H4 was partially measured in W3.** `w3-hotel-01` obtained
a single-category faulted verdict of `{"success": true}` on
`missing_service_hotel_reservation` with a 60.03 % functional failure rate. That run is
**not used as support for H4-lite**: it is incomplete, below R2, and its sampler observed
the wrong namespace. It is weaker than a G1 run: one provenance category, 6 workload
rounds instead of 10, no RESTORED state, and no cluster-state measurement at the verdict instant (the sampler
watched the wrong namespace). The original constraint still stands: the SREMut worker pins
`EXPECTED_NAMESPACE = "social-network"`, so `missing_service_hotel_reservation` could not
be measured with both instruments. The app itself deployed healthy (0/29447 non-2xx) and
the in-process oracle returned `true`; only the worker leg is missing. Generality across
applications therefore rests on the census's structural argument, not on measurement.

**9. The paper's evaluated subset is selected by `tasklist.yml`** (`registry.py:388-390`),
which is absent from the pinned repository — only `tasklist.yml.example` (87 entries) is
committed. *(Superseded: this gap previously read "The paper's own evaluated subset is
unknown."* The selection *mechanism* is now determined; the specific list the authors ran
is still not recoverable from this checkout.) The census denominator therefore still
cannot be aligned with the paper's 90. Both 123 and 104 are given.

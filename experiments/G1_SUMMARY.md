# G1 — three three-state repetitions, driver (ii)

Governing protocol: `SREMut/experiments/PROTOCOL_G1.md` (R1 null-agent episode 60 s;
R2 >= 10 complete wrk2 rounds per state, same run; R3 interpretation rule for a `False`
verdict, carried forward from `SREMut/analysis/G02_null_agent_plan.md`). That file is a
**historical documented protocol**: its text states it was written before execution, and
git does not independently corroborate the ordering — see its own closing note and
`SREMut/analysis/PREREGISTRATION_TIMELINE.md`.

Driver: `SREMut/experiments/three_state_run.py`, sha256
`90488567faa3e7dd991fcab47c52f4e4364cc7cfba857d51060064555e5f3f3e` — identical across all three runs:
1 distinct value(s).

All three runs executed sequentially on kind cluster `kind-kind`, namespace
`social-network`, 2026-08-26. Each began with its own `undeploy_app()` /
`deploy_app()`, so husks from the prior run were cleared before the healthy
checkpoint. No git command was run. Nothing was written into `SREGym/`.

---

## 1. The 3 x 3 verdict matrix

Every cell is the raw dict as returned, never coerced.

### In-process oracle (`mitigation_oracle.evaluate()`, inside conductor.py:269-271's try/except)

| Run | HEALTHY | FAULTED | RESTORED |
|---|---|---|---|
| `g1-run-01` | `{"success": true}` | `{"success": true}` | `{"success": true}` |
| `g1-run-02` | `{"success": true}` | `{"success": true}` | `{"success": true}` |
| `g1-run-03` | `{"success": true}` | `{"success": true}` | `{"success": true}` |

### SREMut worker (isolated subprocess, own provenance and hash guards)

| Run | HEALTHY | FAULTED | RESTORED |
|---|---|---|---|
| `g1-run-01` | `RETURNED_TRUE` / `returned_boolean: True` / exit 0 | `RETURNED_TRUE` / `returned_boolean: True` / exit 0 | `RETURNED_TRUE` / `returned_boolean: True` / exit 0 |
| `g1-run-02` | `RETURNED_TRUE` / `returned_boolean: True` / exit 0 | `RETURNED_TRUE` / `returned_boolean: True` / exit 0 | `RETURNED_TRUE` / `returned_boolean: True` / exit 0 |
| `g1-run-03` | `RETURNED_TRUE` / `returned_boolean: True` / exit 0 | `RETURNED_TRUE` / `returned_boolean: True` / exit 0 | `RETURNED_TRUE` / `returned_boolean: True` / exit 0 |

**9 of 9 in-process verdicts are `{"success": true}`. 9 of 9 worker verdicts are**
**`RETURNED_TRUE` with exit 0.** No cell differs from any other, in either instrument.

---

## 2. Worker `raw_result_sha256` — all nine

| Run | State | `raw_result_sha256` |
|---|---|---|
| `g1-run-01` | HEALTHY | `c955e57777ec0d73639dca6748560d00aa5eb8e12f13ebb2ed9656add3908f97` |
| `g1-run-01` | FAULTED | `c955e57777ec0d73639dca6748560d00aa5eb8e12f13ebb2ed9656add3908f97` |
| `g1-run-01` | RESTORED | `c955e57777ec0d73639dca6748560d00aa5eb8e12f13ebb2ed9656add3908f97` |
| `g1-run-02` | HEALTHY | `c955e57777ec0d73639dca6748560d00aa5eb8e12f13ebb2ed9656add3908f97` |
| `g1-run-02` | FAULTED | `c955e57777ec0d73639dca6748560d00aa5eb8e12f13ebb2ed9656add3908f97` |
| `g1-run-02` | RESTORED | `c955e57777ec0d73639dca6748560d00aa5eb8e12f13ebb2ed9656add3908f97` |
| `g1-run-03` | HEALTHY | `c955e57777ec0d73639dca6748560d00aa5eb8e12f13ebb2ed9656add3908f97` |
| `g1-run-03` | FAULTED | `c955e57777ec0d73639dca6748560d00aa5eb8e12f13ebb2ed9656add3908f97` |
| `g1-run-03` | RESTORED | `c955e57777ec0d73639dca6748560d00aa5eb8e12f13ebb2ed9656add3908f97` |

**Distinct values: 1. All nine are identical:** `c955e57777ec0d73639dca6748560d00aa5eb8e12f13ebb2ed9656add3908f97`

The worker **invocation descriptor** was byte-identical in all nine invocations
(distinct `input_sha256`: 1, `8356ab1d92fada2ae856a3da26e5204beb375431fd12200f5ae2e00a2369171a`,
887 bytes), and the replica baseline was identical across all three runs
(distinct baseline sha256: 1, 27 entries each).

**Read these two hashes correctly.** `c955e577…` is the SHA-256 of the canonical *output*
bytes `{"success":true}`. `8356ab1d…` is the SHA-256 of the *invocation descriptor* — the
`kubernetes_context`, `namespace`, `captured_replica_baseline` and `evidence_paths` handed
to the worker. These are **identical invocation descriptor bytes, not an identical input.**
The descriptor holds no cluster state; the live Kubernetes state the worker reads is
external to it, and it differed materially between the healthy, faulted and restored
states of every run.

So the finding is this: the oracle returned **the same Boolean, and the same output
bytes**, across three cluster states that differed materially — `Service/user-service`
present or absent, its EndpointSlice present or absent, the functional workload at
0.0000 % or 9.8639 % non-2xx. The same descriptor was passed each time, but what the
oracle went on to read from the cluster was not the same.

---

## 3. Workload non-2xx rate per state per run

| Run | State | Rounds | Requests | Non-2xx | Rate | Rounds with non-2xx |
|---|---|---:|---:|---:|---:|---:|
| `g1-run-01` | HEALTHY | 10 | 10240 | 0 | **0.0000 %** | 0 |
| `g1-run-01` | FAULTED | 10 | 10240 | 1036 | **10.1172 %** | 10 |
| `g1-run-01` | RESTORED | 10 | 10240 | 0 | **0.0000 %** | 0 |
| `g1-run-02` | HEALTHY | 10 | 10240 | 0 | **0.0000 %** | 0 |
| `g1-run-02` | FAULTED | 10 | 10240 | 1044 | **10.1953 %** | 10 |
| `g1-run-02` | RESTORED | 10 | 10240 | 0 | **0.0000 %** | 0 |
| `g1-run-03` | HEALTHY | 10 | 10240 | 0 | **0.0000 %** | 0 |
| `g1-run-03` | FAULTED | 10 | 10238 | 950 | **9.2792 %** | 10 |
| `g1-run-03` | RESTORED | 10 | 10240 | 0 | **0.0000 %** | 0 |

### Pooled across the three runs

| State | Rounds | Requests | Non-2xx | Rate |
|---|---:|---:|---:|---:|
| HEALTHY | 30 | 30720 | **0** | **0.0000 %** |
| FAULTED | 30 | 30718 | 3030 | **9.8639 %** |
| RESTORED | 30 | 30720 | **0** | **0.0000 %** |

The pooled faulted rate is 9.8639 %, against the 10.0000 % predicted by the
request mix (`mixed-workload.lua:115`, `compose_post_ratio = 0.10`): z = -0.79, consistent.

R2 is satisfied: every rate above was measured over >= 10 complete rounds captured
in that same run, on that same deployment. The HEALTHY column no longer borrows from
the frozen baselines, which was the gap in `g02-run-01`.

---

## 4. Per-run timings (seconds)

| Run | deploy | stabilize | inject | recover | total | HEALTHY ip / wk | FAULTED ip / wk | RESTORED ip / wk |
|---|---:|---:|---:|---:|---:|---|---|---|
| `g1-run-01` | 131.268 | 4.511 | 41.952 | 35.015 | 559.211 | 0.315 / 7.568 | 0.371 / 7.421 | 0.329 / 7.496 |
| `g1-run-02` | 129.065 | 4.487 | 40.919 | 35.057 | 566.245 | 0.315 / 7.375 | 0.635 / 7.387 | 0.366 / 7.563 |
| `g1-run-03` | 127.19 | 4.49 | 41.255 | 35.246 | 554.503 | 0.307 / 7.77 | 0.596 / 7.4 | 0.354 / 7.23 |

### R1 compliance — injection returning to treatment oracle start

| Run | `injection_finished_utc` | faulted oracle `started_utc` | delta |
|---|---|---|---:|
| `g1-run-01` | 2026-08-26T19:33:11.991257+00:00 | 2026-08-26T19:34:11.991797+00:00 | **60.001 s** |
| `g1-run-02` | 2026-08-26T19:43:00.151040+00:00 | 2026-08-26T19:44:00.151547+00:00 | **60.001 s** |
| `g1-run-03` | 2026-08-26T19:52:40.603242+00:00 | 2026-08-26T19:53:40.603819+00:00 | **60.001 s** |

For comparison, `g02-run-01` was **0.638 s**. R1 held to within 1 ms in all three runs.

---

## 5. Repetitions excluded under R3

**None.** All three repetitions passed the healthy gate and completed.

| Run | status | in-process True | worker True | zero healthy non-2xx | gate |
|---|---|---|---|---|---|
| `g1-run-01` | `COMPLETE` | True | True | True (n=0) | **PASSED** |
| `g1-run-02` | `COMPLETE` | True | True | True (n=0) | **PASSED** |
| `g1-run-03` | `COMPLETE` | True | True | True (n=0) | **PASSED** |

R3 governs the classification of a `False` verdict. **No `False` verdict was
produced in any of the nine instrument readings**, so R3 never had to be applied. It
remains recorded and unused, so no exclusion or reclassification in this project rests
on it.

---

## 6. Margin: last not-Running pod observation before the treatment oracle

From each run's own 2 s sampler. This is the quantity that was 8.405 s in
`g02-run-01` and that motivated R1.

| Run | Last not-Running observation | Pods then not Running | Treatment oracle start | **Margin** |
|---|---|---|---|---:|
| `g1-run-01` | 2026-08-26T19:33:02.368Z | `unique-id-service-655cc57f6d-9xkgk`/Failed | 2026-08-26T19:34:11.991797+00:00 | **69.624 s** |
| `g1-run-02` | 2026-08-26T19:42:52.163Z | `user-mention-service-69f4955bbc-tk2wp`/Failed, `user-timeline-service-6c79dd65d6-snv2k`/Failed | 2026-08-26T19:44:00.151547+00:00 | **67.989 s** |
| `g1-run-03` | 2026-08-26T19:52:32.550Z | `user-mention-service-69f4955bbc-lt5b7`/Failed, `user-timeline-service-6c79dd65d6-nwq5k`/Failed, `wrk2-job-b9jgr`/Failed | 2026-08-26T19:53:40.603819+00:00 | **68.054 s** |

**Minimum observed margin across the three runs: 67.989 s** (max 69.624 s).

Against `g02-run-01`'s 8.405 s, R1 widened the margin by roughly 8x. Every
not-Running pod in these three runs was a terminating old pod from the injector's own
`kubectl delete pods --all` (`inject_virtual.py:306`), and all had been garbage
collected well before the oracle ran.

### Cluster state at each treatment oracle

| Run | Sample | user-service | EndpointSlices | Services | Pods | not Running | Deployments below desired |
|---|---|---|---:|---:|---:|---:|---:|
| `g1-run-01` | 2026-08-26T19:34:10.568Z | **absent** | 0 | 29 | 28 | 0 | 0 |
| `g1-run-01` | 2026-08-26T19:34:13.284Z | **absent** | 0 | 29 | 28 | 0 | 0 |
| `g1-run-02` | 2026-08-26T19:43:57.762Z | **absent** | 0 | 29 | 28 | 0 | 0 |
| `g1-run-02` | 2026-08-26T19:44:00.431Z | **absent** | 0 | 29 | 28 | 0 | 0 |
| `g1-run-03` | 2026-08-26T19:53:40.441Z | **absent** | 0 | 29 | 28 | 0 | 0 |
| `g1-run-03` | 2026-08-26T19:53:43.139Z | **absent** | 0 | 29 | 28 | 0 | 0 |

In every run, at the moment the oracle returned `true`, the Service was absent, no
EndpointSlice existed, and all 27 deployments and 28 pods were fully healthy — which
is precisely the state the oracle is constitutionally unable to distinguish from a
repaired one.

---

## Conclusion

Across three separate repetitions, each with its own deploy, injection, 60 s
null-agent episode, and recovery — separate in that sense, not established as
statistically independent:

- The stock `MitigationOracle` returned `{"success": true}` in **9 of 9**
  instrument readings, in the `in_process` and `worker` provenance categories, across all
  three states. The experimental unit is the run: these are **three repeated full
  three-state runs**, each read in two provenance categories in three states — not nine
  independent observations.
- All nine worker results carry **byte-identical output**, sha256
  `c955e57777ec0d73639dca6748560d00aa5eb8e12f13ebb2ed9656add3908f97`, observed despite
  materially different healthy, faulted and restored cluster states.
- The workload separated the states completely and reproducibly: 0.0000 % healthy,
  9.8639 % faulted, 0.0000 % restored, over 30 rounds per state.
- No repetition was excluded; R3 was never invoked.

The 60 s null-agent episode weakens the strongest remaining alternative explanation for
`g02-run-01` — that the oracle was catching a transient. After a full minute of
quiescence, with a minimum margin of 68.0 s from the last unsettled pod, it still
reports success on a system that fails ~10 % of its functional workload.

Scope: n=3, one fault (MS-M01 no-op after Service deletion), one problem
(`missing_service_social_network`), one cluster. These results do not speak to other
faults, other problems, or other oracles.

# SREMut

SREMut audits the **verifier** of the SREGym benchmark, not the agents it evaluates.

It asks one question: can SREGym's mitigation oracle be satisfied by a system that was never repaired?

For the `missing_service` family, the answer is yes.

---

## Headline result

A **null agent scores 100%** on `missing_service_social_network` and on `missing_service_hotel_reservation`.

The stock `MitigationOracle` returned `{"success": true}` on every faulted, unrepaired system it was asked about, across two applications, two instruments, and five experiment families.

### The verdict is invariant to the amount of damage

Two applications, two different deleted Services, two predictions derived from source before measurement:

| Application | Deleted Service | Predicted failure rate | Observed | Oracle verdict |
|---|---|---:|---:|---|
| social-network | `user-service` | 10.0000% | 9.8639% (z = -0.79) | `{"success": true}` |
| hotel-reservation | `mongodb-rate` | 60.0000% | 59.7429% (z = -0.90) | `{"success": true}` |

The oracle returned the same answer on a system failing 9.86% of user requests and on a system failing 59.74%. Its output carries no information about whether the fault is present, and none about how much damage it is doing.

### At the moment of each verdict

| Signal | social-network |
|---|---|
| `Service/user-service` | absent |
| EndpointSlices for it | 0 |
| DNS for its FQDN | NXDOMAIN |
| TCP to it from inside the cluster | fails |
| Deployments ready | 27 / 27 |
| Pods Running | 28 / 28 |
| SREGym's own workload | 9.86% failing |
| **Mitigation verdict** | **`{"success": true}`** |

### Three-state separation, reproducible

| State | Rounds | Requests | Non-2xx | Rate |
|---|---:|---:|---:|---:|
| Healthy | 30 | 30,720 | 0 | 0.0000% |
| Faulted | 30 | 30,718 | 3,030 | 9.8639% |
| Restored | 30 | 30,720 | 0 | 0.0000% |

Pooled across three social-network repetitions. Recovery through SREGym's own `recover_fault()` returns the rate to zero, which rules out any explanation of the failures other than the injected fault.

---

## Why it happens

Two facts compose.

**1. The oracle observes Deployments and Pods only.**

`sregym/conductor/oracles/mitigation.py` reads Deployment replica counts and Pod/container readiness. Services, Endpoints, EndpointSlices, selectors, ports, DNS, sockets, HTTP, and workload are each verified absent by grep. Its entire import list is two lines.

Upstream documents the gap in the oracle's own docstring at `mitigation.py:26`.

**2. The fault injector restores that exact surface.**

`inject_missing_service` (`inject_virtual.py:295-307`) deletes the Service, then runs `kubectl delete pods --all` and waits for stability. By the time the oracle runs, every Deployment and Pod is healthy again.

The verifier and the injector are structurally unable to disagree.

That single `delete pods --all` has three consequences for what gets measured:

1. It restores the surface the oracle reads, enabling the false accept.
2. It clears the caches in front of the deleted backend, so the fault becomes fully visible functionally.
3. It creates churn that can leave pods non-Running at grading time, enabling a false reject.

One action, three measurement effects, none of them about the agent.

---

## Census: how widespread is this?

All 123 problem IDs registered at the pinned commit, classified by whether the attached oracle can observe what its injector perturbs.

| Verdict | Count |
|---|---:|
| ADEQUATE | 83 |
| BLIND | 8 |
| UNCERTAIN | 32 |

Counts are generated from `analysis/census/ledger.json`; the verdicts they tally are hand-read from each oracle's `evaluate()`.

Of the 31 problem IDs on the bare generic `MitigationOracle`: 17 ADEQUATE, 6 BLIND, 8 UNCERTAIN. Because the UNCERTAIN rows could not be resolved from source, the honest range is **8 to 16 blind problem IDs**.

**8 is a lower bound, not a point estimate.** 28 of the 123 rows are hand-verified against injector source and oracle `evaluate()`; 72 carry at least one resource kind attributed from injector prose rather than code. The headline is stated as *at least 8 of 123, from 28 rows hand-verified*. The count rose from 6 to 8 on 2026-08-28 by auditing our own attribution method — see `analysis/census/R2_PARTA_CODE_ONLY_RECLASSIFICATION.md`.

3 of the 8 BLIND are the `missing_service` family. Two of those three have been confirmed end to end. The remaining BLIND classifications are structural predictions.

BLIND does not correlate with the generic oracle. Two of the eight carry a dedicated oracle written for that fault, and 17 of the 31 generic-oracle problems are ADEQUATE.

---

## Secondary defects

| ID | Defect | Affected | Direction | Fix |
|---|---|---:|---|---|
| D1 | `run-oracle.py` calls `evaluate()` with no `capture_baseline()`, so all three Deployment predicates are skipped | 27 problem IDs | false accept | 1 line |
| D2 | Namespace-wide pod sweep counts benchmark infrastructure. wrk2 husks in phase `Failed` persisted 9 days and 17 hours and never self-cleared | most problems with a workload Job | false reject | label selector |
| D3 | `WrongUpdateStrategyMitigationOracle.evaluatePods()` defined but never called, unlike its 4 siblings | 1 problem ID | false accept | 1 line |

Full writeups with citations in `analysis/census/secondary-defects.md`.

---

## Proposed fix

4 added lines and 1 changed line in `sregym/conductor/problems/missing_service.py`.

Compose rather than substitute. `ServiceEndpointMitigationOracle` already exists in the repo and is used for the adjacent `wrong_service_selector` problem, but it is not a superset of the generic oracle, and it needs an `expected_service_port` attribute that `MissingService` does not define.

```python
self.expected_service_port = <port of the deleted Service>

self.mitigation_oracle = CompoundedOracle(
    self,
    MitigationOracle(problem=self),
    ServiceEndpointMitigationOracle(problem=self),
)
```

`CompoundedOracle` ANDs its children and fans out `capture_baseline()`, so the composed verdict is strictly stronger than today.

**This patch has not been executed.** It is a prediction. Validating it requires patching `SREGym/`, which the standing rules forbid for this study. Before it is submitted upstream, the port must be derived from the deleted Service rather than from the application (the three `missing_service` problems delete Services on different ports), and the composed oracle must be shown to return `false` on a faulted system and `true` on both a healthy and a correctly repaired one. Full analysis in `analysis/PR_PLAN.md`.

---

## Repository layout

```
SREMut/
  src/sremut/          18 modules, ~9,600 lines. Evidence capture,
                       guarded mutation, adjudication, sealing.
  contracts/           Frozen operational contract (invariants MS-I1..MS-I6)
  mutants/             Frozen mutant registry (MS-M01..)
  profiles/            Frozen execution profile (pins, two-runtime split)
  policies/            Frozen evidence capture policy v1 and v1.1
  schemas/             JSON Schema for the above
  tools/               Generators that produced each frozen artifact
  analysis/            Source analyses and the census
  experiments/         Protocols, drivers, and run evidence
```

Outside `SREMut/`, in the parent directory:

```
SREGym/       Pinned read-only reference copy. Never modified.
baselines/    Three healthy-cluster baselines from 2026-08-15/16
harness/      Scripts that produced the baselines
manifests/    Host and cluster provenance logs
```

### Key files

| Path | What it holds |
|---|---|
| `analysis/PAPER_NUMBERS.md` | Every number cited, with its source file. Start here. |
| `analysis/G01_mitigation_verdict_path.md` | What decides the mitigation verdict, traced to file:line |
| `analysis/G02_null_agent_plan.md` | The experiment plan and the pre-registered interpretation rule |
| `analysis/census/CENSUS.md` | The 123-problem coverage census |
| `analysis/census/secondary-defects.md` | D1, D2, D3 |
| `analysis/PR_PLAN.md` | The proposed patch and why a straight swap does not work |
| `experiments/PROTOCOL_G1.md` | R1, R2, R3. States it was registered before any repetition ran; git does not corroborate the ordering — see Pre-registration |
| `experiments/PROTOCOL_W4.md` | W4 method and amendment R3-A |

---

## Experiment index

| Run ID | Application | What it tests | Faulted verdict |
|---|---|---|---|
| `g02-run-01` | social-network | Pilot. 0.638s from injection to verdict | true |
| `g1-run-01/02/03` | social-network | Three-state, 60s null-agent episode, both instruments | true (3 runs) |
| `g3-run-01` | social-network | Production path through the real `Conductor` | true, `TTM` 60.299 |
| `w1-delay0-01/02` | social-network | Zero delay, most adversarial timing | true (2 runs) |
| `w2-noise-01/02` | social-network | Chaos Mesh noise active | true (2 runs) |
| `w2-noise-00-INERT` | social-network | Preserved failed run. Noise never started | excluded |
| `w3-hotel-01` | hotel-reservation | Second application, partial. Superseded by W4 | true |
| `w4-hotel-01` | hotel-reservation | Second application, complete three-state | true, 59.74% failing |

Each run directory holds `three-state.json`, `samples.jsonl` (2 second cluster sampling), full JSON dumps per state, and workload logs.

### Faulted-state measurements

Only faulted states are evidence of blindness. Healthy and restored states are included in the runs as controls, where `true` is the correct answer.

| Run | In-process oracle | SREMut worker | Real `Conductor` |
|---|---:|---:|---:|
| `g02-run-01` | 1 | 1 | 0 |
| `g1-run-01/02/03` | 3 | 3 | 0 |
| `g3-run-01` | 0 | 0 | 1 |
| `w1-delay0-01/02` | 2 | 2 | 0 |
| `w2-noise-01/02` | 2 | 2 | 0 |
| `w4-hotel-01` | 1 | 0 | 0 |
| **official total** | **9** | **8** | **1** |

Excluded runs, listed so the exclusions are visible rather than silent:

| Run | In-process oracle | SREMut worker | Real `Conductor` | Excluded because |
|---|---:|---:|---:|---|
| `w2-noise-00-INERT` | 1 | 1 | 0 | `enable_noise=True` had no effect: the driver never called `start_problem()`, where `NoiseManager.start()` lives (`conductor.py:451`). No noise was ever injected. |
| `w2-hotel-01` | 0 | 0 | 0 | Run stopped at the healthy gate; the faulted state was never reached. |
| `w3-hotel-01` | 1 | 0 | 0 | Two instrument defects: the state sampler was hardcoded to namespace 'social-network' so its samples describe the wrong namespace, and the supervising loop hit a 10-minute tool timeout and killed the driver during the faulted window. |

**18 official faulted-state measurements; 21 including the excluded runs. Every one returned `{"success": true}`** — the excluded runs point the same way as the included ones, so the exclusions are conservative, not selective.

This table is generated. Regenerate it with `python3 experiments/build_result_ledger.py`, which writes `experiments/RESULT_LEDGER.json` from the run records; do not hand-edit the counts.

---

## Pre-registration

**Two classes, and git supports only one of them.**

The **four frozen artifacts** — contract, execution profile, and both evidence policies, plus the mutant registry — carry annotated Git tags dated 2026-08-16 to 2026-08-24, in trees containing zero evidence files. The earliest execution anywhere in the project is 2026-08-26T11:00:19Z. Their pre-registration is corroborated by git.

The **six experiment protocols** are not. Every one was introduced by a commit that *postdates* the runs it governs, and five share a commit with their own evidence. Their content says they were written first, and the session transcript records that, but git provides no independent corroboration for any of them, and SREMut has no remote, so every timestamp originates on one machine with a user-writable clock. Each protocol file now carries a note stating this; the full forensic record is `analysis/PREREGISTRATION_TIMELINE.md`.

**Does any conclusion depend on this? No.** The rules whose pre-registration could bias a result are the ones that *exclude or classify* — R3, R3-A, R2's round minimum. R3 was never invoked: all 21 faulted-state measurements returned `true`, and R3 governs `false` verdicts only. R3-A resolved to case (d), the branch that changes nothing. R2 disqualifies only `w3-hotel-01`, which is superseded anyway and whose 60.03 % agrees with w4's 59.74 %. The rest — R1's interval, H2, H3, H4 — are design parameters, and H2 tested R1 directly: runs at 0 s and 60 s give the same verdict.

Rules as stated in their protocol files. The "claimed registered before" column reports
what each file says, not what git attests — see above.

| Rule | Claimed registered before | What it fixes |
|---|---|---|
| R1 | any G1 repetition | 60s between injection returning and the oracle. Matches the poll interval of SREGym's own `autosubmit` agent. |
| R2 | any G1 repetition | At least 10 complete wrk2 rounds per state, same run |
| R3 | `g02-run-01` | How a `false` verdict is classified. Never invoked, because no measurement returned false. |
| H2, H3, H4 | their runs | Each states in advance that either outcome is reportable |
| R3-A | see below | Scope of the `harness_timing_failure` classification |

R3-A is the one rule whose *own text* discloses that it was written after its measurement existed. It was registered at 2026-08-27T10:11:11.748Z, after the W4 faulted verdict was written to disk at 10:03:10.423Z but before its value was read; `PROTOCOL_W4.md` records both timestamps and the method used to establish existence without observing the value. Its discriminator is defined entirely on the sampler timeline, which is independent of the verdict. It resolved to case (d), and cases (a)-(c) govern `false` verdicts, so no discriminating branch was ever applied.

For the other five protocols the gap is different in kind: their text claims prior registration and nothing in their content admits otherwise, but the commit that introduced each one postdates its runs. The distinction matters for how the claim is worded, not for any result — see the paragraph above.

Tags: `sremut-missing-service-contract-v1`, `sremut-missing-service-execution-profile-v1`, `sremut-missing-service-evidence-policy-v1`, `sremut-missing-service-evidence-policy-v1.1`.

---

## The two failure-rate derivations

Both were derived from source before the measurement, and both matched.

### social-network, 10%

`mixed-workload.lua:113-115` sets the mix at 60% home-timeline, 30% user-timeline, 10% compose-post. Only compose-post reaches `user-service`, and it does so unconditionally (`ComposePostHandler.h:575-577`, blocked on at `:594`, no branch in lines 570-600). The home-timeline path reaches SocialGraphService but only via `GetFollowers`, which does not touch the user-service pool.

Predicted 10.0000%, observed 9.9689% over 1,126 rounds. Per-round standard deviation predicted 0.9375pp, observed 0.9347pp.

### hotel-reservation, 60%

`mixed-workload_type_1.lua:114-117` sets the mix at 60% search, 39% recommend, 0.5% user, 0.5% reserve. Only search reaches `mongodb-rate`, via `getRates` (`search/server.go:246`, unguarded) to `RateMongoAddress: "mongodb-rate:27017"` (`config.json:11`). Every other endpoint has its own database. On failure, search returns `codes.Unavailable` and the frontend converts it to HTTP 500 (`frontend/server.go:196`), so it fails closed.

Predicted 60.0000%, observed 59.7429%.

For hotel-reservation the workload rate is also the primary evidence that the intended fault was applied. The sampler shows `service_count` dropping from 23 to 22, which proves a Service is missing but not which one. The 59.74% signature matches the search share exactly, and search is the only path to `mongodb-rate`. No other deleted Service produces that signature.

---

## An alternative mechanism, named in advance and excluded

`rate/server.go:267` calls `log.Panic()` on a Mongo error, and the injector's pod restart empties `memcached-rate`, so every search takes the Mongo path. A persistently crashed rate pod would let the oracle detect the fault incidentally through pod health rather than by observing Services, which would weaken the claim.

Amendment R3-A defined, before the verdict was read, what would distinguish that case from transient churn. The W4 sampler excludes it. The only `Failed` rate pod was the old pod terminating under the injector's own `delete pods --all`, present for a single sample at 10:01:03.783Z and gone by 10:01:07. Its replacement was `Running` continuously across the entire oracle window, which began 97 seconds later. 127 consecutive samples across the faulted window show zero pods outside `Running`.

---

## Environment

| Item | Value |
|---|---|
| SREGym | `ba07faf1a322f9b6d4a279643bb796aa2f36f64b` |
| SREGym-applications | `2b2f9c6c2e97c44abbfcc44af1cf2f994bbb04f8` |
| Stock oracle SHA-256 | `a087fd38399cfca4c2de764dbbab837d6100350ab9f71b89552cafe2c91fca8b` |
| Cluster | kind v0.32.0, Kubernetes v1.32.0, 4 nodes |
| kubectl | pinned v1.32.0 |
| Oracle runtime | CPython 3.12.3, kubernetes 30.1.0 |
| Runner runtime | CPython 3.12.3, kubernetes 32.0.1 |
| Host | GCP e2-standard-8, Ubuntu 24.04 |

The two Python runtimes are deliberately different. The stock oracle runs in SREGym's own venv, in an isolated subprocess, and is never imported in-process by the runner.

---

## Reproducing

### Prerequisites

A kind cluster matching the versions above, SREGym checked out at the pinned commit with its venv built, and the pinned kubectl.

### One three-state run

```bash
cd ~/sremut/SREMut
~/sremut/SREGym/.venv/bin/python experiments/three_state_run.py --run-id <run-id>
```

Roughly 9 minutes for social-network, 18 for hotel-reservation. The driver starts the sampler on the correct namespace, deploys clean, gates on a healthy verdict, injects, waits 60 seconds, evaluates, recovers, and evaluates again. Writes `three-state.json`.

Add `--problem-id missing_service_hotel_reservation` for the second application.

### One oracle reading, isolated

```bash
cd ~/sremut/SREGym
env -i LC_ALL=C.UTF-8 PATH=/usr/bin:/bin \
    PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 \
  ~/sremut/SREGym/.venv/bin/python -I -B \
  ~/sremut/SREMut/src/sremut/original_oracle_worker.py \
  <input.json> <output.json>
```

The input is canonical JSON with `kubernetes_context`, `namespace`, `captured_replica_baseline`, and `evidence_paths`. The output path must not already exist. The worker verifies the Python version, three dependency versions, and the oracle module hash before touching the cluster. It pins the namespace to `social-network`, so it cannot be used for hotel-reservation.

---

## Known gaps

Stated plainly. Each limits what the numbers above support.

1. **Two applications confirmed, not the whole family.** `missing_service_social_network` with two instruments across n=3 three-state repetitions plus four other run families. `missing_service_hotel_reservation` with one instrument, n=1. `missing_service_astronomy_shop` is untested. The other six BLIND classifications are structural predictions with no measurement behind them.
2. **The hotel-reservation runs are single-instrument.** The SREMut worker pins `EXPECTED_NAMESPACE = "social-network"`, a frozen artifact that was not relaxed. Those runs carry one independent measurement, not two, and are weaker than any social-network run.
3. **32 of 123 census rows are UNCERTAIN.** The perturbed resource kind could not be traced to file:line at uniform depth, mostly where injection is delegated to Khaos, `inject_tt.py`, or a kernel injector.
4. **ADEQUATE is structural, not behavioural.** It means the oracle reads the perturbed kind or a functional signal. It does not mean the oracle has been shown to reject a non-repair.
5. **The proposed fix is unexecuted.** See the note under Proposed fix.
6. **Noise is partially addressed.** Two runs had Chaos Mesh active, but not during the faulted window. The conductor stops noise before every evaluation by design (`conductor.py:476-483`), so the graded instant is quiescent either way. `pod-kill` was never selected in either run.
7. **`g3-run-01`'s faulted workload rate is unrecoverable.** An instrument bug, recorded below.
8. **One cluster.**

### Disclosed errors

Five errors were caught and are recorded rather than silently fixed.

| Error | How it was caught | Effect |
|---|---|---|
| Census first pass marked `missing_service_*` ADEQUATE | The known-answer case contradicted it | Rule corrected and recorded. No other row changed. |
| jsonpath written with a literal newline in `conductor_path_run.py` | kubectl returned exit 1 with empty stdout | `g3-run-01` faulted workload rate lost. Other runs used a correct script. |
| First noise run was inert, `nm.start()` never fired | Checked for the chaos namespace before reporting | Run preserved as `w2-noise-00-INERT`, driver patched, both reported runs use the patched driver. |
| `sample_state.sh` hardcoded the social-network namespace | Sampler output contradicted the run's namespace | `w3-hotel-01` has no valid cluster-state evidence. Fixed for W4. All earlier runs targeted social-network, so they are unaffected. |
| `w3-hotel-01` reported `user-service` NotFound as evidence the fault was applied | That Service never existed in hotel-reservation | The check proved nothing either way. W4 uses `service_count` 23 to 22 instead. |

---

## Standing rules

- `SREGym/` is read-only reference code. Its cleanliness is a scientific claim, verified beyond `git status` including assume-unchanged and skip-worktree bits.
- `contracts/`, `profiles/`, `policies/`, `schemas/`, `mutants/` and the four annotated tags are frozen pre-registration artifacts. If something contradicts them, report it rather than fix it.
- Every claim about SREGym carries a file:line citation. Absences are proven with a grep that returns nothing.
- Inference is labelled as inference. Predictions are never reported as results.
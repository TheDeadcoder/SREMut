<p align="center">
  <img src="https://ik.imagekit.io/sakib61/SREMut/SREMut.png" width="440" alt="SREMut logo">
</p>

<p align="center"><b>Who grades the grader?</b> Mutation testing for the verifier of a live SRE benchmark.</p>

---

SREMut audits the **verifier** of the SREGym benchmark, not the agents it evaluates. It asks one question: can SREGym's mitigation oracle be satisfied by a system that was never repaired?

For the `missing_service` family, the answer is yes. The method is classic mutation testing pointed at a grader: apply a controlled operational mutant to a healthy deployment, run the unchanged stock oracle, and run a stronger operational contract that was frozen and timestamped before execution. The disagreement between the two graders is the finding.

## The mutant study

Three Service-level mutants, three repetitions each, executed 2026-08-29 on a live kind cluster. Every prediction was frozen and RFC 3161 timestamped before the first mutant was applied.

<p align="center">
  <img src="https://ik.imagekit.io/sakib61/SREMut/fig-verdicts.png" width="900" alt="Verdict matrix: stock oracle passes everything, the frozen contract rejects all nine faulted states">
</p>

| Mutant | What it does | Faulted non-2xx | Successful-response volume retained | Stock oracle | Contract |
|---|---|---:|---:|---|---|
| MS-M01 | delete `Service/user-service` | 9.88 to 9.97% | 90.1% | success 6/6 faulted | REJECT 3/3 (I1 I2 I3 I4) |
| MS-M02 | selector matches no pods | 6.81 to 9.84% | **13.3%** | success 6/6 faulted | REJECT 3/3 (I2 I3 I4) |
| MS-M03 | `targetPort` set to 65535 | 9.75 to 10.05% | 90.1% | success 6/6 faulted | REJECT 3/3 (I2 I4) |

**54 of 54 stock oracle readings returned `{"success": true}`. The contract rejected 9 of 9 faulted states and passed all 18 healthy and restored controls on the five invariants evaluated. Mutation score: contract 3/3, stock oracle 0/3.** Violated invariant sets were identical across all three repetitions of each mutant. Nine completed records, with no recorded exclusions or retries, 93.3 minutes of driver wall clock — see [`DEVIATIONS_AND_LIMITS.md`](DEVIATIONS_AND_LIMITS.md).

This study evaluated **MS-I1 through MS-I5**; MS-I6 (repair persistence) was declared out of scope in advance by §6 of the pre-registration. A REJECT is unaffected by that — a sixth invariant can only add violations — so "REJECT 9 of 9" and the 3/3 mutation score hold for the full contract. The control results do not carry over: the six-invariant contract has never been shown to pass a genuine repair.

Every categorical prediction in the pre-registration matched, including the per-mutant invariant sets. The one quantitative miss: about 10% non-2xx was predicted for all three mutants, but MS-M02 collapsed throughput to roughly 12 to 16% of healthy request volume, so its error rate sat on a much smaller denominator. Successful-response volume retained (successful requests versus healthy, window-matched 99 s) tells that story honestly: about 13% for M02 versus about 90% for the others.

Attestation chain, all on 2026-08-29, tokens from Free TSA, verifiable offline with `openssl ts`: pre-registration document hashed and timestamped at **09:08:34 UTC**, pre-execution commit SHA timestamped at **10:15:38 UTC**, first mutant applied at **10:22:09 UTC**. See [`PREREGISTRATION_MS_MUTANTS.md`](PREREGISTRATION_MS_MUTANTS.md) and [`attestation/`](attestation/).

## No mutant needed: SREGym's own fault does it too

Before the mutant study, 13 exploratory runs used SREGym's own `missing_service` injector with a null agent that does nothing. Every faulted state that was ever read, including the runs later excluded for instrument defects, got the same verdict: **21 of 21 faulted readings returned `{"success": true}`**, across two applications and the production path through the real `Conductor`.

<p align="center">
  <img src="https://ik.imagekit.io/sakib61/SREMut/fig-damage.png" width="900" alt="Functional damage at the moment of each verdict">
</p>

| Application | Deleted Service | Predicted failure rate | Observed | Verdict |
|---|---|---:|---:|---|
| social-network | `user-service` | 10.0000% | 9.8639% | `{"success": true}` |
| hotel-reservation | `mongodb-rate` | 60.0000% | 59.7429% | `{"success": true}` |

Both rates were derived from workload source before measurement (compose-post is 10% of the mix and the only path to `user-service`; search is 60% and the only path to `mongodb-rate`). Recovery through SREGym's own `recover_fault()` returns both to 0.0000%, which pins the failures on the injected fault. The verdict did not move at either damage level.

## Why it happens

Two facts compose. The stock `MitigationOracle` reads Deployments and Pods only: no Services, Endpoints, selectors, ports, DNS, sockets, HTTP, or workload, each absence provable by grep, and the gap is admitted in the oracle's own docstring at `mitigation.py:26`. And the fault injector restores exactly that surface: `inject_missing_service` deletes the Service, then runs `kubectl delete pods --all` and waits for stability, so by grading time every Deployment and Pod the oracle can see is healthy again while the Service is still gone.

The same mechanism recurs in a second fault class: `auth_miss_mongodb`'s injector scales its client back up itself, restoring the replica count the oracle checks, while the client sits in an unbounded retry loop and never starts serving. With no readiness probes anywhere in the chart, that pod still looks fine (source-level finding, not yet executed).

## How widespread

A census of all 123 problem IDs registered at the pinned commit, classified by whether the attached oracle can observe what its injector perturbs.

<p align="center">
  <img src="https://ik.imagekit.io/sakib61/SREMut/fig-census.png" width="900" alt="Census: 83 adequate, 32 uncertain, 8 blind of 123">
</p>

The 8 BLIND: `missing_service_social_network`, `missing_service_hotel_reservation`, `missing_service_astronomy_shop`, `assign_to_non_existent_node`, `auth_miss_mongodb`, `operator_wrong_operator_image`, `pvc_claim_mismatch`, `workload_imbalance`. Two carry a dedicated oracle, so blindness is not just a generic-oracle problem. The count is a lower bound from 28 hand-verified rows, not a prevalence estimate, and 32 rows remain UNCERTAIN. Full method, corrections, and disclosed errors in [`analysis/census/CENSUS.md`](analysis/census/CENSUS.md).

Three secondary defects, each with file:line citations in [`analysis/census/secondary-defects.md`](analysis/census/secondary-defects.md):

| ID | Defect | Direction |
|---|---|---|
| D1 | `run-oracle.py` evaluates without `capture_baseline()`, silently skipping all Deployment predicates for 31 bare-generic problem IDs, 35 including the four subclasses | false accept |
| D2 | namespace-wide pod sweep counts benchmark infrastructure; failed wrk2 husks persisted 9+ days and never self-clear | false reject |
| D3 | `WrongUpdateStrategyMitigationOracle.evaluatePods()` defined but never called | false accept |

## The fix

Four added lines and one changed line in `missing_service.py`: compose the existing oracle with `ServiceEndpointMitigationOracle`, which already lives upstream and is already used for the adjacent `wrong_service_selector` problem.

```python
self.expected_service_port = <port of the deleted Service>

self.mitigation_oracle = CompoundedOracle(
    self,
    MitigationOracle(problem=self),
    ServiceEndpointMitigationOracle(problem=self),
)
```

This patch is a prediction, not a result: the standing rules forbid modifying `SREGym/` inside this study, so it has not been executed. Analysis and the port caveat in [`analysis/PR_PLAN.md`](analysis/PR_PLAN.md). An upstream disclosure and PR are planned.

## Reproducing

| Item | Value |
|---|---|
| SREGym / applications | `ba07faf1` / `2b2f9c6c`, read-only, never modified |
| Cluster | kind v0.32.0, Kubernetes v1.32.0, 4 nodes, kubectl pinned 1.32.0 |
| Runtimes | both instruments ran under `SREGym/.venv` (CPython 3.12.3, kubernetes 30.1.0) — the in-process oracle and the isolated worker subprocess differ in call path, not in client library. The CPython 3.12.3 + kubernetes 32.0.1 runner is the sealed SREMut runtime: specified, not materialised, and not used by any run reported here |
| Host | GCP e2-standard-8, Ubuntu 24.04 |

```bash
# one three-state null-agent run (~9 min social-network, ~18 min hotel-reservation)
~/sremut/SREGym/.venv/bin/python experiments/three_state_run.py --run-id my-run-01

# one pre-registered mutant run
~/sremut/SREGym/.venv/bin/python experiments/mutant_run.py --run-id ms-m01-rXX --mutant MS-M01
```

Every number above traces to a file in this repo. Start at [`analysis/PAPER_NUMBERS.md`](analysis/PAPER_NUMBERS.md); the mutant evidence lives in `experiments/ms-m0*-r0*/mutant-run.json`, the historical ledger in `experiments/RESULT_LEDGER.json` (regenerate with `build_result_ledger.py --check`).

```
src/sremut/     evidence capture, guarded mutation, adjudication, sealing
contracts/      frozen operational contract (invariants MS-I1..MS-I6)
mutants/        frozen mutant registry
attestation/    RFC 3161 tokens and verification steps
analysis/       verdict-path trace, census, PR plan, pre-registration forensics
experiments/    protocols, drivers, and all run evidence
```

## Scope and limits

1. Everything here is a **mitigation verdict** and only that: not a diagnosis score, not an agent score, not an overall SREGym result.
2. Only the mutant matrix is confirmatory. The 13 historical runs are exploratory: their protocols lack independent pre-execution corroboration, and [`analysis/PREREGISTRATION_TIMELINE.md`](analysis/PREREGISTRATION_TIMELINE.md) documents exactly what git does and does not attest.
3. Two applications measured, one cluster. `missing_service_astronomy_shop` and the other BLIND rows outside `missing_service` are structural predictions, never executed. Hotel-reservation is n=1, single instrument.
4. The census was verified to unequal depth (28 of 123 rows hand-checked), and ADEQUATE is structural: the oracle reads the perturbed kind, which is not proof it rejects non-repairs.
5. The proposed fix is unexecuted.
6. Every deviation from the frozen pre-registration and every limit on the nine records — including the first faulted workload round straddling the mutation, the unretained wrk2 logs, and the shared Kubernetes client — is recorded in [`DEVIATIONS_AND_LIMITS.md`](DEVIATIONS_AND_LIMITS.md).
7. Two distinct studies exist and should not be conflated: the pre-registered nine-repetition MS-I1..MS-I5 mutant study is **complete (9/9)**, while the sealed v1.2 six-invariant matrix with authenticated run identities, a hash-chained journal and an external anchor remains **0/9 and unexecuted**.

A write-up is under review at a NeurIPS 2026 workshop. Errors found along the way, including our own, are disclosed rather than silently fixed: see the census corrections and the disclosed-errors table in [`analysis/census/CENSUS.md`](analysis/census/CENSUS.md).
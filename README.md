![SREMut-logo](https://ik.imagekit.io/sakib61/SREMut/SREMut.png)
</br>
# SREMut
SREMut audits the **verifier** of the SREGym benchmark, not the agents it evaluates.
 
It asks one question: can SREGym's mitigation oracle be satisfied by a system that was never repaired?
 
For `missing_service_social_network`, the answer is yes.
 
---
 
## Headline result
 
A **null agent scores 100%** on `missing_service_social_network`.
 
The stock `MitigationOracle` returned `{"success": true}` in **13 of 13** measurements on an unrepaired system, across two independent instruments and four experiment families.
 
At the moment of each verdict:
 
| Signal | Value |
|---|---|
| `Service/user-service` | absent |
| EndpointSlices for it | 0 |
| DNS for its FQDN | NXDOMAIN |
| TCP to it from inside the cluster | fails |
| Deployments ready | 27 / 27 |
| Pods Running | 28 / 28 |
| SREGym's own workload | 9.86% of requests failing |
| **Mitigation verdict** | **`{"success": true}`** |
 
The workload rate separates the states completely and reproducibly:
 
| State | Rounds | Requests | Non-2xx | Rate |
|---|---:|---:|---:|---:|
| Healthy | 30 | 30,720 | 0 | 0.0000% |
| Faulted | 30 | 30,718 | 3,030 | 9.8639% |
| Restored | 30 | 30,720 | 0 | 0.0000% |
 
9.8639% matches the share of requests that depend on the deleted Service. The workload mix is 10% compose-post (`mixed-workload.lua:115`), and compose-post calls `user-service` unconditionally. Predicted per-round standard deviation 0.9375pp, observed 0.9347pp.
 
---
 
## Why it happens
 
Two facts compose.
 
**1. The oracle observes Deployments and Pods only.**
 
`sregym/conductor/oracles/mitigation.py` reads Deployment replica counts and Pod/container readiness. Services, Endpoints, EndpointSlices, selectors, ports, DNS, sockets, HTTP, and workload are each verified absent by grep. Its entire import list is two lines.
 
Upstream documents the gap in the oracle's own docstring at `mitigation.py:26`.
 
**2. The fault injector restores that exact surface.**
 
`inject_missing_service` (`inject_virtual.py:295-307`) deletes the Service, then runs `kubectl delete pods --all` and waits for stability. By the time the oracle runs, every Deployment and Pod is healthy again.
 
The verifier and the injector are structurally unable to disagree.
 
---
 
## Census: how widespread is this?
 
All 118 problem IDs registered at the pinned commit, classified by whether the attached oracle can observe what its injector perturbs.
 
| Verdict | Count |
|---|---:|
| ADEQUATE | 80 |
| BLIND | 6 |
| UNCERTAIN | 32 |
 
Of the 27 problem IDs on the bare generic `MitigationOracle`: 15 ADEQUATE, 4 BLIND, 8 UNCERTAIN. Because the UNCERTAIN rows could not be resolved from source, the honest range is **6 to 14 blind problem IDs**.
 
3 of the 6 BLIND are the `missing_service` family. Only `missing_service_social_network` has been confirmed end to end. The rest are structural predictions.
 
BLIND does not correlate with the generic oracle. Two of the six carry a dedicated oracle written for that fault.
 
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
self.expected_service_port = 9090 if app_name == "social_network" else app.frontend_port
 
self.mitigation_oracle = CompoundedOracle(
    self,
    MitigationOracle(problem=self),
    ServiceEndpointMitigationOracle(problem=self),
)
```
 
`CompoundedOracle` ANDs its children and fans out `capture_baseline()`, so the composed verdict is strictly stronger than today. Full analysis in `analysis/PR_PLAN.md`.
 
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
| `analysis/census/CENSUS.md` | The 118-problem coverage census |
| `analysis/census/secondary-defects.md` | D1, D2, D3 |
| `analysis/PR_PLAN.md` | The proposed patch and why a swap does not work |
| `experiments/PROTOCOL_G1.md` | R1, R2, R3, registered before any repetition ran |
 
---
 
## Experiment index
 
| Run ID | What it tests | Result |
|---|---|---|
| `g02-run-01` | Pilot. 0.638s from injection to verdict | true |
| `g1-run-01/02/03` | Three-state, 60s null-agent episode, both instruments | 9/9 true |
| `g3-run-01` | Production path through the real `Conductor` | true, `TTM` 60.299 |
| `w1-delay0-01/02` | Zero delay, most adversarial timing | 2/2 true |
| `w2-noise-01/02` | Chaos Mesh noise active | 2/2 true |
| `w2-noise-00-INERT` | Preserved failed run. Noise never started | excluded |
| `w2-hotel-01` | Second application | stopped at healthy gate |
 
Each run directory holds `three-state.json`, `samples.jsonl` (2 second cluster sampling), full JSON dumps per state, and workload logs.
 
---
 
## Pre-registration
 
Protocol rules were committed before the runs they govern, and the frozen artifacts carry annotated Git tags.
 
| Rule | Registered before | What it fixes |
|---|---|---|
| R1 | any G1 repetition | 60s between injection returning and the oracle. Matches the poll interval of SREGym's own `autosubmit` agent. |
| R2 | any G1 repetition | At least 10 complete wrk2 rounds per state, same run |
| R3 | `g02-run-01` | How a `false` verdict is classified. Never invoked, because no measurement returned false. |
| H2, H3, H4 | their runs | Each states in advance that either outcome is reportable |
 
Tags: `sremut-missing-service-contract-v1`, `sremut-missing-service-execution-profile-v1`, `sremut-missing-service-evidence-policy-v1`, `sremut-missing-service-evidence-policy-v1.1`.
 
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
./experiments/sample_state.sh > experiments/<run-id>/samples.jsonl &
~/sremut/SREGym/.venv/bin/python experiments/three_state_run.py --run-id <run-id>
```
 
Roughly 9 minutes. Deploys clean, gates on a healthy verdict, injects, waits 60 seconds, evaluates, recovers, and evaluates again. Writes `three-state.json`.
 
### One oracle reading, isolated
 
```bash
cd ~/sremut/SREGym
env -i LC_ALL=C.UTF-8 PATH=/usr/bin:/bin \
    PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 \
  ~/sremut/SREGym/.venv/bin/python -I -B \
  ~/sremut/SREMut/src/sremut/original_oracle_worker.py \
  <input.json> <output.json>
```
 
The input is canonical JSON with `kubernetes_context`, `namespace`, `captured_replica_baseline`, and `evidence_paths`. The output path must not already exist. The worker verifies the Python version, three dependency versions, and the oracle module hash before touching the cluster.
 
---
 
## Known gaps
 
Stated plainly. Each limits what the numbers above support.
 
1. **One problem confirmed end to end.** `missing_service_social_network` only. The census extends structurally to 118 problem IDs, but the other BLIND classifications are predictions.
2. **32 of 118 census rows are UNCERTAIN.** The perturbed resource kind could not be traced to file:line at uniform depth, mostly where injection is delegated to Khaos, `inject_tt.py`, or a kernel injector.
3. **ADEQUATE is structural, not behavioural.** It means the oracle reads the perturbed kind or a functional signal. It does not mean the oracle has been shown to reject a non-repair.
4. **n = 3** for the full three-state repetition. The pattern is established. No rate claim is made.
5. **Noise is partially addressed.** Two runs had Chaos Mesh active, but not during the faulted window. The conductor stops noise before every evaluation by design (`conductor.py:476-483`), so the graded instant is quiescent either way. `pod-kill` was never selected in either run.
6. **`g3-run-01`'s faulted workload rate is unrecoverable.** An instrument bug, documented below.
7. **One cluster, one fault.**
### Disclosed errors
 
Three errors were caught and are recorded rather than silently fixed.
 
| Error | How it was caught | Effect |
|---|---|---|
| Census first pass marked `missing_service_*` ADEQUATE | The known-answer case contradicted it | Rule corrected and recorded. No other row changed. |
| jsonpath written with a literal newline in `conductor_path_run.py` | kubectl returned exit 1 with empty stdout | `g3-run-01` faulted workload rate lost. Other runs used a different, correct script. |
| First noise run was inert, `nm.start()` never fired | Checked for the chaos namespace before reporting | Run preserved as `w2-noise-00-INERT`, driver patched, both reported runs use the patched driver. |
 
---
 
## Standing rules
 
- `SREGym/` is read-only reference code. Its cleanliness is a scientific claim, verified beyond `git status` including assume-unchanged and skip-worktree bits.
- `contracts/`, `profiles/`, `policies/`, `schemas/`, `mutants/` and the four annotated tags are frozen pre-registration artifacts. If something contradicts them, report it rather than fix it.
- Every claim about SREGym carries a file:line citation. Absences are proven with a grep that returns nothing.
- Inference is labelled as inference. Predictions are never reported as results.

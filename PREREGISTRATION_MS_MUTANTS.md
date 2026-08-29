# Pre-registration: MS-M01/M02/M03 wrong-repair mutant runs

**Status:** `FROZEN_BEFORE_EXECUTION`
**Written:** 2026-08-29, before the first mutant was applied. Read-only cluster
checks may precede it; no mutation of any kind does.
**Executed repetitions at freeze time:** 0.

This document fixes, in advance, what will be done, what is predicted, what would
falsify the prediction, and which runs may be excluded. It is pushed to
`https://github.com/TheDeadcoder/SREMut` and independently timestamped before the
first mutant is applied, so that the predictions below carry a clock that is not
the author's own.

---

## 1. What this is, and what it is not

These runs execute the three mutants already frozen in
`mutants/missing_service_social_network/registry.yaml` (tagged
`sremut-missing-service-contract-v1`, 2026-08-16) against the live kind cluster,
using an extension of `experiments/three_state_run.py` — the driver with thirteen
recorded historical runs.

**This is not the official frozen matrix.** The v1.2 sealed-evidence runtime
(`src/sremut/`), the runner release bundle, the authenticated run identities and
the external anchor are **not** used and are not required by anything below. The
status `OFFICIAL_FROZEN_ATTEMPT` and the word "official" remain reserved for that
future study, which has not been run. These runs are labelled
`PREREGISTERED_MUTANT_RUN`.

What they add over the historical null-agent evidence is the one thing a null
agent cannot produce: **an agent that attempts a repair and gets it wrong.**

**Standing rule, restated:** predictions are never reported as results. Every
table in Section 4 is a prediction until an executed table exists beside it.

---

## 2. The three mutants

Each mutant differs from the captured healthy `Service/user-service` in **exactly
one field**. The captured original (verified in
`experiments/g1-run-01/healthy/user-service.json`) is:

```
spec.type          = ClusterIP
spec.selector      = {service: user-service}
spec.ports         = [{name: "9090", port: 9090, protocol: TCP, targetPort: 9090}]
```

Mutant bodies are derived from the Service captured live in the same run,
stripping only the server-managed fields `metadata.uid`,
`metadata.resourceVersion`, `metadata.creationTimestamp`, `spec.clusterIP`,
`spec.clusterIPs`, and `status`. Helm labels and annotations are preserved, so the
diff against the original really is one field.

| Mutant | Agent behaviour modelled | Frozen action |
|---|---|---|
| **MS-M01** | agent does nothing | leave `Service/user-service` absent after SREGym's injector deletes it |
| **MS-M02** | agent recreates the Service, selector wrong | recreate with `spec.selector = {sremut-mutant-backend: ms-m02}` |
| **MS-M03** | agent recreates the Service, port mapping wrong | recreate with `spec.ports[0].targetPort = 65535`, selector left correct |

### Activation criteria — verified structurally, before the oracle is invoked

An API acknowledgement is never treated as observed activation.

- **MS-M01:** `Service/user-service` returns NotFound.
- **MS-M02:** `spec.selector` equals `{sremut-mutant-backend: ms-m02}` exactly;
  zero pods match that selector; zero eligible endpoints.
- **MS-M03:** `spec.selector` equals `{service: user-service}` exactly;
  `spec.ports[0].port == 9090` and `spec.ports[0].targetPort == 65535`;
  at least one eligible endpoint backed by a `Deployment/user-service` pod.

A run whose activation check fails is classified `MUTANT_ACTIVATION_FAILURE`,
excluded from the result table, reported in the exclusion table, and **not
retried** — an activation failure is not a research verdict.

---

## 3. Procedure, fixed in advance

Per repetition, in this order:

1. **Preflight (read-only).** Confirm context `kind-kind`, namespace
   `social-network`, four Ready nodes, pinned kubectl
   `v1.32.0` (sha256 `646d58f6d98ee670a71d9cdffbf6625aeea2849d567f214bc43a35f8ccb7bf70`),
   SREGym at `ba07faf1a322f9b6d4a279643bb796aa2f36f64b`, applications at
   `2b2f9c6c2e97c44abbfcc44af1cf2f994bbb04f8`, oracle module sha256
   `a087fd38399cfca4c2de764dbbab837d6100350ab9f71b89552cafe2c91fca8b`. Delete any
   pod in phase `Failed` in the namespace (defect D2: stale wrk2 husks cause false
   rejections and are unrelated to the mutant).
2. **Deploy.** `fix_kubernetes(); undeploy_app(); deploy_app(); wait_for_ready()`.
   A full redeploy per repetition, for comparability with the historical G1 runs.
3. **Healthy state.** Workload window (R2: ≥10 complete wrk2 rounds), both oracle
   instruments, contract evaluation. **Healthy gate:** both oracle instruments
   `true` and zero non-2xx in the healthy window, else stop without injecting and
   classify `STOPPED_AT_HEALTHY_GATE`.
4. **Capture.** `capture_baseline()` (27-entry replica floor) and the live
   `Service/user-service` body.
5. **Inject.** `problem.inject_fault()` — SREGym's own injector.
6. **Apply the mutant** immediately on return, then wait so that the interval from
   `inject_fault()` returning to the start of the original-oracle call is
   **60 s in every run**, matching rule R1 of the historical G1 protocol.
7. **Verify activation** structurally (Section 2).
8. **Original oracle**, both instruments: the in-process
   `problem.mitigation_oracle.evaluate()` inside a verbatim copy of
   `conductor.py:269-271`, and the isolated worker subprocess in
   `SREGym/.venv`. Raw dicts are recorded, never coerced.
9. **Contract evaluation** of the faulted state (Section 4).
10. **Faulted workload window** (≥10 complete rounds after the mutant timestamp).
11. **Restore.** `problem.recover_fault()`. SREGym's own recovery deletes the
    Service before re-applying the captured original, so the mutant Service is
    removed by the same code path that restores the system; the runner performs no
    restoration mutation of its own.
12. **Restored state.** Workload window, both oracle instruments, contract
    evaluation. This is the positive control for the contract: a genuine repair
    must pass it.

### Ordering constraint that must not be violated

The stock oracle sweeps **every** pod in the namespace
(`mitigation.py:86,95`) and returns `false` if any is not `Running` with all
containers ready. The busybox probe pod used for the DNS and TCP predicates lives
in that namespace and becomes `Succeeded` when its sleep ends. Therefore, in every
state: **the probe pod is created only after both oracle instruments have returned
and been recorded, and is deleted before any later oracle invocation.** This
mirrors `challenge_creation_minimum_state: ORIGINAL_ORACLE_EVALUATED` in the
frozen execution profile.

---

## 4. The contract evaluated in these runs

Five of the six frozen invariants are evaluated. Definitions are unchanged from
`contracts/missing_service_social_network.yaml`.

| ID | Predicate | Observation |
|---|---|---|
| **MS-I1** | stable service identity | `Service/user-service` exists with a non-empty, non-headless `spec.clusterIP`; `nslookup user-service.social-network.svc.cluster.local` from the probe pod returns an A record equal to that ClusterIP. Deadline 30 s. |
| **MS-I2** | correct backend routing | every eligible EndpointSlice address maps to a Ready, non-terminating pod in `social-network` whose controller chain is Pod → ReplicaSet → `Deployment/user-service`; and `nc -z -w 3 <fqdn> 9090` from the probe pod succeeds. Deadline 30 s, TCP timeout 3 s. |
| **MS-I3** | ready endpoint availability | at least one eligible endpoint (`addressType IPv4`, `conditions.ready == true`, not terminating). Deadline 30 s. |
| **MS-I4** | functional workload | ≥50 requests in the fresh window with zero non-2xx/3xx responses, parsed from SREGym's own wrk2 job. |
| **MS-I5** | capacity preservation | every deployment in the captured 27-entry baseline present, with desired, ready and available replicas at or above its floor. |
| **MS-I6** | repair persistence | **not evaluated in this pass.** Recorded as `NOT_EVALUATED`. Rationale in Section 6. |

**Contract verdict** = PASS iff MS-I1..MS-I5 all pass; otherwise REJECT, with the
list of violated invariants recorded.

**A timeout on a target predicate is REJECT, not an infrastructure failure.**
Infrastructure failure is scoped to conditions independent of the mutant: the
Kubernetes API unavailable, CoreDNS unhealthy before mutation, a non-target
baseline deployment failing to recover, the probe pod unschedulable for unrelated
reasons, or node loss.

---

## 5. Predictions

Frozen before execution. **These are predictions, not results.**

| Row | Service | MS-I1 DNS | MS-I2 route | MS-I3 endpoints | MS-I4 workload | MS-I5 capacity | Stock oracle | Contract |
|---|---|---|---|---|---|---|---|---|
| healthy | present | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| **MS-M01** | absent | FAIL | FAIL | FAIL | FAIL | PASS | **PASS** | **REJECT** (I1,I2,I3,I4) |
| **MS-M02** | present | PASS | FAIL | FAIL | FAIL | PASS | **PASS** | **REJECT** (I2,I3,I4) |
| **MS-M03** | present | PASS | FAIL | PASS | FAIL | PASS | **PASS** | **REJECT** (I2,I4) |
| restored | present | PASS | PASS | PASS | PASS | PASS | PASS | PASS |

Predicted faulted workload non-2xx rate for all three mutants: **≈10%**, derived
from `compose_post_ratio = 0.10` in `mixed-workload.lua:113-115` and the fact that
compose-post calls `user-service` unconditionally. Predicted healthy and restored
rates: 0.0000%.

Predicted mutation score: **3/3 non-equivalent mutants detected by the contract,
0/3 by the stock oracle.**

Note that MS-M02 and MS-M03 passing the stock oracle is *implied* by the source
analysis in `analysis/G01_mitigation_verdict_path.md`: the oracle makes four
cluster calls, all for Deployments and Pods, and never reads a Service. The value
of executing them is not surprise. It is that the prediction is executed under a
realistic agent model, and that the contract is shown to discriminate three states
the stock oracle cannot tell apart.

---

## 6. What is deliberately not done, and why

- **MS-I6 (repair persistence) is not evaluated.** All three mutants already fail
  MS-I2, so deleting a user-service pod and re-checking cannot add information
  about them. MS-I6 exists to catch a repair that is correct at grading time and
  breaks under pod churn — for example a selector-less Service with statically
  managed Endpoints pinned to current pod IPs. No such mutant is registered. Both
  the invariant and that mutant are reported as future work.
- **The sealed evidence runtime is not used.** No authenticated run identity, no
  hash-chained journal, no terminal manifest, no external anchor. Evidence is the
  same JSON-plus-raw-artifact form used by the thirteen historical runs.
- **No AI agent is involved.** The mutants stand in for agent behaviour; no agent
  is run, and nothing here measures agent performance or changes any ranking.

---

## 7. Schedule, repetitions, exclusions and retries

Round-robin, one active mutation at a time, matching the order frozen in
`profiles/missing_service_social_network/pilot-v1.yaml`:

```
ms-m01-r01, ms-m02-r01, ms-m03-r01,
ms-m01-r02, ms-m02-r02, ms-m03-r02,     (if time permits)
ms-m01-r03, ms-m02-r03, ms-m03-r03      (if time permits)
```

Run IDs use the form `ms-m{01|02|03}-r{01|02|03}`, deliberately distinct from the
`sremut-ms-m01-r01-a01-<12hex>` grammar reserved for the sealed runtime.

**The minimum reportable unit is one repetition of each mutant.** If only the r01
round completes, the r01 round is reported as three single repetitions and the
scope is stated as such. Stopping early for lack of time is recorded as such and
is not an exclusion.

**A run may be excluded only for a reason on this list**, declared now:

| Classification | Retryable | Meaning |
|---|---|---|
| `STOPPED_AT_HEALTHY_GATE` | yes, after the cause is fixed | the cluster was not healthy before injection; no mutant applied |
| `MUTANT_ACTIVATION_FAILURE` | no | the mutant did not take structurally |
| `HARNESS_TIMING_FAILURE` | once, after verified restoration | a pod was outside `Running` at grading time for reasons unrelated to the mutant |
| `INFRASTRUCTURE_FAILURE` | once, after verified restoration | as scoped in Section 4 |
| `DRIVER_ABORTED` | once | the driver died before writing its record |
| `RESTORATION_FAILURE` | no; stops the schedule | the system could not be returned to a verified healthy state |

**A completed research verdict is never retried, whatever it says.** Every attempt,
including retries and excluded runs, appears in the reported exclusion table.

---

## 8. What would falsify the prediction, and what happens then

| Observed | Interpretation |
|---|---|
| stock oracle REJECT with every pod `Running` and the rejection traced to a Deployment predicate | **The prediction is falsified for that mutant.** Report it. Do not re-run because the result is unfavourable. |
| stock oracle REJECT with any pod outside `Running` | `HARNESS_TIMING_FAILURE`; one retry permitted after verified restoration. This is rule R3 of the historical G1 protocol, reused unchanged. |
| stock oracle PASS and contract PASS | check activation first; if activation was valid, the contract is insufficient for that mutant and must be reported as such. |
| stock oracle REJECT and contract PASS | inconsistent semantics or an evidence-binding error; preserve the run and investigate before reporting anything. |
| verdicts differ across repetitions of one mutant | report the instability; do not report only the majority. |
| restored state fails the contract | restoration failure; the schedule stops. |

---

## 9. Attestation

1. This document is committed and **pushed to GitHub** before any mutant is
   applied. GitHub's push event timestamp is not written by the author's host.
2. An RFC 3161 timestamp is taken over the commit SHA of this document and the
   `.tsr` response is committed afterwards, at
   `attestation/PREREGISTRATION_MS_MUTANTS.<sha>.tsr`.
3. Neither of these is claimed to be more than it is: they establish that this
   text existed at that time. They do not establish that the cluster was untouched
   before it, and the historical runs recorded in `experiments/RESULT_LEDGER.json`
   remain, as `analysis/PREREGISTRATION_TIMELINE.md` states, exploratory evidence
   with no independently corroborated pre-execution artifact.

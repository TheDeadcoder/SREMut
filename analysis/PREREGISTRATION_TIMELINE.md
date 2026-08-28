# Pre-registration timeline — forensic record

Facts only. No wording is proposed for the paper; this document establishes what the
timestamps do and do not support.

All data from `git log`, `git for-each-ref` and `git ls-tree` in `SREMut/` (read-only),
plus execution timestamps recorded inside each run's own JSON records. SREMut has no
remote, so every timestamp below is local-machine only and is not independently
corroborated by any third party.

---

## C1 / C2 / C3 — protocol commit vs evidence commit

| Artifact | Introducing commit | Author date | Commit date | Evidence it governs | Evidence introducing commit | C3 verdict |
|---|---|---|---|---|---|---|
| `experiments/PROTOCOL_G1.md` | `9314bda3` | 2026-08-26T20:11:56Z | 2026-08-26T20:11:56Z | `g1-run-01/02/03` | `9314bda3` | **SAME COMMIT** |
| `experiments/PROTOCOL_W1.md` | `7820e937` | 2026-08-27T03:47:32Z | 2026-08-27T03:47:32Z | `w1-delay0-01/02` | `7820e937` | **SAME COMMIT** |
| `experiments/PROTOCOL_W2.md` | `3d46355a` | 2026-08-27T05:07:53Z | 2026-08-27T05:07:53Z | `w2-noise-01/02` | `3d46355a` | **SAME COMMIT** |
| `experiments/PROTOCOL_W3.md` | `96f5b1e6` | 2026-08-27T09:51:15Z | 2026-08-27T09:51:15Z | `w3-hotel-01` | `96f5b1e6` | **SAME COMMIT** |
| `experiments/PROTOCOL_W4.md` | `0a3a0e59` | 2026-08-27T10:23:01Z | 2026-08-27T10:23:01Z | `w4-hotel-01` | `0a3a0e59` | **SAME COMMIT** |
| `analysis/G02_null_agent_plan.md` | `9314bda3` | 2026-08-26T20:11:56Z | 2026-08-26T20:11:56Z | `g02-run-01` | `388872a3` (2026-08-26T14:27:43Z) | **evidence commit PRECEDES the plan commit** |

**C3, stated plainly: no protocol commit strictly precedes the commit introducing the
evidence it governs.** Five of six are the same commit. The sixth is worse — the
`g02-run-01` evidence was committed at 14:27:43Z, nearly six hours *before* the plan
document that governs it was committed at 20:11:56Z.

Author date equals commit date for every commit above; there is no rebase or amend
skew to appeal to.

## C4 — protocol commit date vs earliest recorded execution

| Protocol | Commit date | Run | Earliest execution timestamp in the run's own record | Commit predates execution? |
|---|---|---|---|---|
| `PROTOCOL_G1.md` | 2026-08-26T20:11:56Z | `g1-run-01` | 2026-08-26T19:28:10.037861Z | **NO** |
| `PROTOCOL_G1.md` | 2026-08-26T20:11:56Z | `g1-run-02` | 2026-08-26T19:38:01.776896Z | **NO** |
| `PROTOCOL_G1.md` | 2026-08-26T20:11:56Z | `g1-run-03` | 2026-08-26T19:47:43.375448Z | **NO** |
| `PROTOCOL_W1.md` | 2026-08-27T03:47:32Z | `w1-delay0-01` | 2026-08-26T21:51:21.792023Z | **NO** |
| `PROTOCOL_W1.md` | 2026-08-27T03:47:32Z | `w1-delay0-02` | 2026-08-26T22:01:18.835253Z | **NO** |
| `PROTOCOL_W2.md` | 2026-08-27T05:07:53Z | `w2-noise-01` | 2026-08-27T04:07:04.382061Z | **NO** |
| `PROTOCOL_W2.md` | 2026-08-27T05:07:53Z | `w2-noise-02` | 2026-08-27T04:16:48.105650Z | **NO** |
| `PROTOCOL_W3.md` | 2026-08-27T09:51:15Z | `w3-hotel-01` | 2026-08-27T05:12:33.384Z | **NO** |
| `PROTOCOL_W4.md` | 2026-08-27T10:23:01Z | `w4-hotel-01` | 2026-08-27T09:54:56.706800Z | **NO** |
| `G02_null_agent_plan.md` | 2026-08-26T20:11:56Z | `g02-run-01` | 2026-08-26T11:00:19.367292Z | **NO** |

**C4, stated plainly: for every run, the protocol's introducing commit POSTDATES the
execution it governs.** In no case does a commit timestamp corroborate pre-registration.

## C5 — the four annotated tags

| Tag | Tag object | Tagger date | Peels to | Commit date | Evidence files under `experiments/` or `analysis/` in the tree at that commit |
|---|---|---|---|---|---:|
| `sremut-missing-service-contract-v1` | `378e9e91` | 2026-08-16T17:54:21Z | `abed58d6` | 2026-08-16T16:45:36Z | **0** |
| `sremut-missing-service-execution-profile-v1` | `7c6493eb` | 2026-08-20T05:52:20Z | `35fcaeec` | 2026-08-20T04:52:48Z | **0** |
| `sremut-missing-service-evidence-policy-v1` | `40e50e4f` | 2026-08-22T12:59:30Z | `c2f500c9` | 2026-08-22T12:49:50Z | **0** |
| `sremut-missing-service-evidence-policy-v1.1` | `8e44e66c` | 2026-08-24T16:33:54Z | `560e8e81` | 2026-08-24T16:28:24Z | **0** |

All four tag objects carry tagger dates between 2026-08-16 and 2026-08-24. The earliest
execution timestamp anywhere in the project is `g02-run-01` at
**2026-08-26T11:00:19Z**. Every tag therefore predates all execution by at least two
days, and no evidence file existed in the tree at any of the four tagged commits.

## C6 — summary

| Registered artifact | Timestamp provably predates the execution it governs? | Basis |
|---|---|---|
| `contracts/missing_service_social_network.yaml` (tag `…contract-v1`) | **YES** | tagger date 2026-08-16, 10 days before first execution; 0 evidence files in tree |
| `profiles/…/pilot-v1.yaml` (tag `…execution-profile-v1`) | **YES** | tagger date 2026-08-20; 0 evidence files in tree |
| `policies/…/evidence-capture-v1.yaml` (tag `…evidence-policy-v1`) | **YES** | tagger date 2026-08-22; 0 evidence files in tree |
| `policies/…/evidence-capture-v1.1.yaml` (tag `…evidence-policy-v1.1`) | **YES** | tagger date 2026-08-24; 0 evidence files in tree |
| `mutants/…/registry.yaml` (in the contract tag's tree) | **YES** | same commit as `…contract-v1` |
| `analysis/G02_null_agent_plan.md` (incl. the R3 rule) | **NO** | committed 2026-08-26T20:11:56Z; `g02-run-01` executed from 11:00:19Z the same day |
| `experiments/PROTOCOL_G1.md` (R1, R2, R3) | **NO** | committed 20:11:56Z; G1 runs executed 19:28-19:57Z |
| `experiments/PROTOCOL_W1.md` (H2) | **NO** | committed 2026-08-27T03:47:32Z; runs executed 2026-08-26T21:51-22:11Z |
| `experiments/PROTOCOL_W2.md` (H3, H4) | **NO** | committed 05:07:53Z; runs executed 04:07-04:26Z |
| `experiments/PROTOCOL_W3.md` (H4-lite) | **NO** | committed 09:51:15Z; run executed from 05:12:33Z |
| `experiments/PROTOCOL_W4.md` (+ amendment R3-A) | **NO** | committed 10:23:01Z; run executed from 09:54:56Z |

**Two distinct classes.**

The **four frozen pre-registration artifacts** — contract, execution profile, and both
evidence policies, plus the mutant registry — have annotated-tag timestamps that predate
all execution by days, in trees containing no evidence. Their pre-registration is
supported by git.

The **six experiment protocols** have no such support. Every one was committed after the
runs it governs had executed, and five share a commit with their own evidence. Their
content states they were written before execution, and the session transcript records
them being written first, but **git provides no independent corroboration of that
ordering for any of them.** Amendment R3-A is a documented special case: it was written
after the verdict existed on disk and discloses this in its own text.

A further limitation applying to all rows: SREMut has no git remote, so every timestamp
here originates on a single machine with a user-writable clock and is not attested by any
external service.

---

# Does any conclusion depend on these?

Added 2026-08-28 (R2 Part D2). Determined from the run records, not from the protocol
text. All figures below are read from `experiments/RESULT_LEDGER.json`, which is generated
from the run records by `experiments/build_result_ledger.py`.

**Answer: no. No conclusion in this project depends on any of the six experiment
protocols having been written before its run.**

The reasoning turns on a distinction between two kinds of rule.

- **Selection rules** — rules that *exclude* or *classify* a result (R3, R3-A, and R2's
  round minimum). These are the rules whose pre-registration matters, because a rule
  chosen after seeing the data could be shaped to discard inconvenient outcomes.
- **Design parameters** — rules that fix *what was run* (R1's episode length, H2, H3,
  H4). Writing these down later cannot bias anything: the run either happened that way or
  it did not, and the record says which.

## Selection rules

### R3 — `harness_timing_failure` — NOT load-bearing. Never invoked.

R3 governs **FALSE** verdicts only: it excuses a false verdict caused by transient pod
churn from the injector's own `kubectl delete pods --all`.

**All 21 faulted-state measurements in the project returned `success=true`.** There has
never been a false faulted verdict, so R3 has had nothing to act on.

Proving command over every run artifact, which returns only a line explicitly *declining*
the classification and no line applying it:

```
grep -rn 'harness_timing_failure' experiments/ | grep -v PROTOCOL_
  experiments/g3-run-01/conductor-path.md:119: "The verdict is a research verdict,
                                                not a harness_timing_failure."
```

Zero runs were classified as a harness timing failure, zero were excluded under R3, and
zero were re-run under it. The only `r3_classification` field ever populated is
`w3-hotel-01`, which records **"Not applicable — R3 governs FALSE verdicts; this verdict
is TRUE."**

### R3-A — the case (a)/(b)/(c)/(d) discriminator — NOT load-bearing. Resolved to the no-op branch.

R3-A exists to stop a *persistent* fault-caused pod failure being misfiled as a harness
timing failure. It classified `w4-hotel-01` as **case (d)** — verdict TRUE with every pod
`Running`, "as in G1. Report." (`PAPER_NUMBERS.md:754`; 127 consecutive samples with zero
not-`Running` pods.)

Case (d) is the branch that requires no special handling and is identical to how the G1
runs were treated. The discriminating branches (a), (b) and (c) never applied.

This matters because R3-A is the one rule in the project **registered after its
measurement already existed on disk** — disclosed in its own text at
`experiments/PROTOCOL_W4.md`, written 2026-08-27T10:11:11.748Z when the faulted verdict
had been on disk since 10:03:10.423Z but had not been read. Since the amendment resolved
to the branch that changes nothing, that weaker guarantee has no consequence for any
reported result. The disclosure stands; the exposure is nil.

### R2 — at least 10 workload rounds per state — NOT outcome-bearing.

Every official run's faulted workload satisfies R2. Rounds per faulted state:

| run | rounds | requests | failure rate |
|---|---:|---:|---:|
| `g02-run-01` | 1126 | 1,152,844 | 9.9689 % |
| `g1-run-01/02/03` | 10 each | 10,240 / 10,240 / 10,238 | 10.1172 / 10.1953 / 9.2792 % |
| `w1-delay0-01/02` | 10 each | 10,240 each | 10.3125 / 10.0098 % |
| `w2-noise-01/02` | 10 each | 10,240 each | 10.3223 / 9.8340 % |
| `w4-hotel-01` | 10 | 29,555 | 59.7429 % |

Exactly one run violates R2: `w3-hotel-01`, with 6 rounds captured post hoc after the
driver was killed. It is excluded — but **not** under R2; it is superseded by
`w4-hotel-01` because of two instrument defects (sampler on the wrong namespace, driver
killed mid-window). And admitting it would change nothing: w3 measured **60.03 %** where
w4 measured **59.74 %**, agreeing to within 0.3 percentage points.

## Design parameters

### R1 — the 60-second null-agent episode — NOT load-bearing, and shown so directly.

H2 was run precisely to test whether the verdict depends on this interval.
`w1-delay0-01` and `w1-delay0-02` ran with **R1 = 0 s**; `g1-run-01/02/03` and
`w2-noise-01/02` ran with **R1 = 60 s**. Every one returns `success=true` on both
instruments. The faulted verdict is invariant to the interval across the range tested, so
the choice of 60 s carries no weight.

### H2 (submission latency), H3 (noise), H4/H4-lite (second application)

H2 and H3 are robustness checks that returned null results: the verdict did not change
with submission delay, nor under Chaos Mesh noise. Nothing rests on them; their function
is to remove alternative explanations, not to support a claim.

H4-lite is different in importance but not in kind. `w4-hotel-01` is the sole official
hotel-reservation measurement, and it is what retires "one problem, one fault, one
cluster" and supports the claim that the blindness travels with the oracle rather than
with the application. But that claim rests on a **direct observation**, not on a selection
rule: the oracle returned `success=true` while 59.74 % of requests failed. The observation
is exactly as strong whether the hypothesis was written before or after it.

## The exclusions that were *not* made under any pre-registered rule

Three runs are excluded, and none of the three exclusions was authorised by a rule
registered in advance. They were post-hoc judgements, and they are recorded as such in
`RESULT_LEDGER.json`:

| run | status | ground for exclusion | faulted verdict |
|---|---|---|---|
| `w2-noise-00-INERT-noise-never-started` | inert | `enable_noise=True` had no effect; the driver never called `start_problem()`, where `NoiseManager.start()` lives (`conductor.py:451`) | `in_process` TRUE, `worker` TRUE |
| `w2-hotel-01` | abandoned | stopped at the healthy gate; the faulted state was never reached | **no measurement exists** |
| `w3-hotel-01` | superseded | sampler hardcoded to the wrong namespace; driver killed mid-window | `in_process` TRUE |

**These exclusions cannot have manufactured the result.** Every excluded run that produced
a faulted measurement returned `success=true` — the same direction as every included run.
Restoring all three to the official set would raise the count from 18 to 21 measurements
and leave every conclusion unchanged. The exclusions are conservative, not selective.

## What remains genuinely exposed

Not the conclusions, but the *framing*. The six experiment protocols describe themselves
as pre-registered, and git does not corroborate that for any of them (sections C3-C6
above). The honest statement for the paper is that the **frozen artifacts** — contract,
execution profile, both evidence policies, mutant registry — are pre-registered with
tag-timestamp support, while the **experiment protocols** are documented-in-advance by
transcript only. Since no protocol rule excluded or reclassified any result, the
distinction costs nothing in evidential terms; it costs only the word "pre-registered"
applied to the second group.

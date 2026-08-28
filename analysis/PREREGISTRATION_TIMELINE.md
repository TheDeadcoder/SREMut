# Registration-timeline forensic record

Facts only. No wording is proposed for the paper; this document establishes what the
timestamps do and do not support, and therefore which artifacts may be described as
independently corroborated pre-execution artifacts and which may not.

All data from `git log`, `git for-each-ref`, `git ls-tree` and the push reflog in
`SREMut/` (read-only), plus execution timestamps recorded inside each run's own JSON
records. Every timestamp below is written by one local machine with a user-writable clock.

> **Correction (2026-08-28).** Earlier versions of this document asserted that *"SREMut
> has no remote"*. **That is false.** `origin` is
> `https://github.com/TheDeadcoder/SREMut.git`, and `refs/remotes/origin/main` tracks the
> same commit as local `main`. The assertion appeared here and in all six protocol notes
> and was carried forward unchecked. Section C7 records what the remote does and does not
> establish; it changes no verdict in C3-C6.

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

| Specification artifact | Independently corroborated as predating the execution it governs? | Basis |
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
all execution by days, in trees containing no evidence. They are the only artifacts in
this project that qualify as **independently corroborated pre-execution artifacts**.

The **six experiment protocols** are **historical documented protocols**. Their contents
may well have been specified before execution — their text states it, and the session
transcript records them being written first — but **git provides no independent
corroboration of that ordering for any of them.** Every one was committed after the runs
it governs had executed, and five share a commit with their own evidence. Amendment R3-A
is a documented special case: it was written after the verdict existed on disk and
discloses this in its own text.

No protocol decision, threshold or recorded run behaviour is withdrawn by this
classification. What it changes is the terminology and the evidential standing of the
documents, not their content.

A further limitation applying to all rows: every timestamp here is written by a single
machine with a user-writable clock. SREMut *does* have a remote — see C7 — but nothing in
C3-C6 rests on it, and C7 shows it corroborates no protocol.

## C7 — the git remote, and what it does and does not establish

Added 2026-08-28, correcting the "no remote" assertion above.

`origin` = `https://github.com/TheDeadcoder/SREMut.git`. `refs/remotes/origin/main` is at
`66f0b79b`, the same commit as local `main`. The local push reflog
(`.git/logs/refs/remotes/origin/main`) records 14 pushes:

| pushed ref | push time (local reflog) | commit date | subject (truncated) |
|---|---|---|---|
| `4032f64b` | 2026-08-26T10:06:08Z | 2026-08-26T10:00:26Z | Implement offline MS-M01 attempt orchestration |
| `388872a3` | 2026-08-26T14:28:01Z | 2026-08-26T14:27:43Z | Add verdict-path analysis and null-agent evidence |
| `72b8dbea` | 2026-08-26T14:49:35Z | 2026-08-26T14:49:20Z | Add faulted-state harvest |
| `65247709` | 2026-08-26T18:43:29Z | 2026-08-26T18:36:13Z | Add workload binomial analysis, recovery positive control |
| `9314bda3` | 2026-08-26T20:12:09Z | 2026-08-26T20:11:56Z | Add G1 — introduces `PROTOCOL_G1.md` and `G02_null_agent_plan.md` |
| `95b50479` | 2026-08-26T21:06:08Z | 2026-08-26T21:05:52Z | Add census |
| `6cdad157` | 2026-08-26T21:47:22Z | 2026-08-26T21:47:12Z | Add production-path verdict |
| `7820e937` | 2026-08-27T03:47:44Z | 2026-08-27T03:47:32Z | Add zero-delay runs — introduces `PROTOCOL_W1.md` |
| `3d46355a` | 2026-08-27T05:08:07Z | 2026-08-27T05:07:53Z | Add noise runs — introduces `PROTOCOL_W2.md` |
| `96f5b1e6` | 2026-08-27T09:51:55Z | 2026-08-27T09:51:15Z | Add hotel-lite — introduces `PROTOCOL_W3.md` |
| `0a3a0e59` | 2026-08-27T10:23:08Z | 2026-08-27T10:23:01Z | Add hotel-reservation-complete — introduces `PROTOCOL_W4.md` |
| `008d46ed` | 2026-08-27T10:34:42Z | 2026-08-27T10:34:32Z | readme |
| `0eaef4d4` | 2026-08-28T04:35:43Z | 2026-08-28T04:35:36Z | Census corrected to 123 |
| `66f0b79b` | 2026-08-28T09:39:31Z | 2026-08-28T09:39:20Z | R2: BLIND 8 of 123 |

**What this does NOT establish.** The reflog is a *local* file, written by the same
user-writable clock as every commit date above. It records that a push was attempted; it
does not record what GitHub received, or when. Confirming the remote's contents requires
contacting GitHub, which this offline task did not do. **Nothing here is third-party
attestation and it must not be reported as such.**

**No protocol gains corroboration.** Even taking the reflog at face value, the push
carrying each protocol postdates every run that protocol governs:

| protocol | its push | earliest run it governs | push predates that run? |
|---|---|---|---|
| `G02_null_agent_plan.md`, `PROTOCOL_G1.md` | 2026-08-26T20:12:09Z | 2026-08-26T11:00:19Z (`g02-run-01`) | **NO** |
| `PROTOCOL_W1.md` | 2026-08-27T03:47:44Z | 2026-08-26T21:51:21Z | **NO** |
| `PROTOCOL_W2.md` | 2026-08-27T05:08:07Z | 2026-08-27T04:07:04Z | **NO** |
| `PROTOCOL_W3.md` | 2026-08-27T09:51:55Z | 2026-08-27T05:12:33Z | **NO** |
| `PROTOCOL_W4.md` | 2026-08-27T10:23:08Z | 2026-08-27T09:54:56Z | **NO** |

C3-C6 stand unchanged, and the six protocols remain historical documented protocols.

**One observation about the frozen artifacts, recorded as an open lead, not a result.**
The first push, `4032f64b` at 2026-08-26T10:06:08Z, precedes the earliest execution
anywhere in the project (2026-08-26T11:00:19Z) by about 54 minutes, and all four frozen
tag commits — `abed58d6`, `35fcaeec`, `c2f500c9`, `560e8e81` — are ancestors of
`4032f64b`, verified by `git merge-base --is-ancestor`. *If* that push reached GitHub,
the frozen artifacts' content was on a third-party server before any run executed, which
would be stronger corroboration than a local annotated tag.

**That is not claimed here.** Two things block it and both need network access: whether
the push reached the remote at all, and whether the four *annotated tag objects* were ever
pushed — no tag refs exist under `refs/remotes`, and the reflog covers only `origin/main`.
Until both are checked against GitHub, the corroboration for the frozen artifacts remains
exactly what C5 states: local annotated tags whose tagger dates predate all execution, in
trees containing zero evidence files.

---

# Does any conclusion depend on these?

Added 2026-08-28 (R2 Part D2). Determined from the run records, not from the protocol
text. All figures below are read from `experiments/RESULT_LEDGER.json`, which is generated
from the run records by `experiments/build_result_ledger.py`.

**Answer: no. No conclusion in this project depends on any of the six experiment
protocols having been written before its run.**

The reasoning turns on a distinction between two kinds of rule.

- **Selection rules** — rules that *exclude* or *classify* a result (R3, R3-A, and R2's
  round minimum). These are the rules whose prior specification matters, because a rule
  chosen after seeing the data could be shaped to discard inconvenient outcomes.
- **Design parameters** — rules that fix *what was run* (R1's episode length, H2, H3,
  H4). Writing these down later cannot bias anything: the run either happened that way or
  it did not, and the record says which.

## Selection rules

### R3 — `harness_timing_failure` — NOT load-bearing. Never invoked.

R3 governs **FALSE** verdicts only: it excuses a false verdict caused by transient pod
churn from the injector's own `kubectl delete pods --all`.

**All 21 faulted-state instrument readings in the project returned `success=true`.**
There has never been a false faulted verdict, so R3 has had nothing to act on.

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

Every HISTORICAL_INCLUDED run's faulted workload satisfies R2. Rounds per faulted state:

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
instruments. The faulted verdict did not change between the two intervals tested, so the
choice of 60 s carries no weight here. Two intervals are two points, not a demonstration
of invariance over the interval range.

### H2 (submission latency), H3 (noise), H4/H4-lite (second application)

H2 and H3 are robustness checks that returned null results: the verdict did not change
with submission delay, nor under Chaos Mesh noise. Nothing rests on them; their function
is to remove alternative explanations, not to support a claim.

H4-lite is different in importance but not in kind. `w4-hotel-01` is the sole included
hotel-reservation run — one run, one instrument, n=1 — and it is what retires "one problem, one fault, one
cluster" and supports the claim that the blindness travels with the oracle rather than
with the application. But that claim rests on a **direct observation**, not on a selection
rule: the oracle returned `success=true` while 59.74 % of requests failed. The observation
is exactly as strong whether the hypothesis was written before or after it.

## The exclusions that were *not* made under any rule specified in advance

Three of the thirteen historical runs are excluded, and none of the three exclusions was
authorised by a rule written in advance. They were post-hoc judgements, and they are
recorded as such in `RESULT_LEDGER.json`:

| run | status | ground for exclusion | faulted verdict |
|---|---|---|---|
| `w2-noise-00-INERT-noise-never-started` | `HISTORICAL_EXCLUDED_INERT` | `enable_noise=True` had no effect; the driver never called `start_problem()`, where `NoiseManager.start()` lives (`conductor.py:451`) | `in_process` TRUE, `worker` TRUE |
| `w2-hotel-01` | `HISTORICAL_EXCLUDED_ABANDONED` | stopped at the healthy gate; the faulted state was never reached | **no reading exists** |
| `w3-hotel-01` | `HISTORICAL_EXCLUDED_SUPERSEDED` | sampler hardcoded to the wrong namespace; driver killed mid-window | `in_process` TRUE |

**These exclusions cannot have manufactured the result.** Every excluded run that produced
a faulted reading returned `success=true` — the same direction as every included run.
Restoring all three would raise the count from 18 to 21 faulted-state instrument readings
and leave every conclusion unchanged. The exclusions are conservative, not selective.

On counting: the 10 included runs yield 18 faulted-state instrument readings, and those
18 are **not** 18 independent repetitions — two instruments reading the same faulted
cluster in one run share one deployment, one injection and one cluster state. The run is
the experimental unit.

## What remains genuinely exposed

Not the conclusions, but the *framing*. The six experiment protocols describe themselves
as written before execution, and git does not corroborate that for any of them (sections
C3-C6 above). The honest statement for the paper is that the **frozen artifacts** —
contract, execution profile, both evidence policies, mutant registry — are independently
corroborated pre-execution artifacts, their tag timestamps predating all execution, while
the **experiment protocols** are **historical documented protocols**, attested by
transcript only. Since no protocol rule excluded or reclassified any result, the
distinction costs nothing in evidential terms; it costs only the word "pre-registered"
applied to the second group.

# Registration-timeline forensic record

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


Facts only. No wording is proposed for the paper; this document establishes what the
timestamps do and do not support, and therefore how each artifact may be described.

**Nothing in this project is established as an independently corroborated
pre-execution artifact.** The frozen artifacts are **locally frozen before the recorded
runs**; the six experiment protocols are **historical documented protocols**. The
difference between the two classes is real but it is a difference in local evidence, not
a difference between corroborated and uncorroborated.

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
execution it governs.** No commit timestamp corroborates a claim of prior specification
for any protocol.

## C5 — the four annotated tags

| Tag | Tag object | Tagger date | Peels to | Commit date | Evidence files under `experiments/` or `analysis/` in the tree at that commit |
|---|---|---|---|---|---:|
| `sremut-missing-service-contract-v1` | `378e9e91` | 2026-08-16T17:54:21Z | `abed58d6` | 2026-08-16T16:45:36Z | **0** |
| `sremut-missing-service-execution-profile-v1` | `7c6493eb` | 2026-08-20T05:52:20Z | `35fcaeec` | 2026-08-20T04:52:48Z | **0** |
| `sremut-missing-service-evidence-policy-v1` | `40e50e4f` | 2026-08-22T12:59:30Z | `c2f500c9` | 2026-08-22T12:49:50Z | **0** |
| `sremut-missing-service-evidence-policy-v1.1` | `8e44e66c` | 2026-08-24T16:33:54Z | `560e8e81` | 2026-08-24T16:28:24Z | **0** |

There are **four annotated tag objects**, i.e. four frozen checkpoints: the contract, the
execution profile, evidence policy v1, and evidence policy v1.1. The **mutant registry is
not a fifth checkpoint** — it is contained in the tree of the contract-tagged commit.

All four tag objects carry tagger dates between 2026-08-16 and 2026-08-24. The earliest
execution timestamp anywhere in the project is `g02-run-01` at
**2026-08-26T11:00:19Z**. Every tagger date therefore precedes all recorded execution by
at least two days, and no evidence file existed in the tree at any of the four tagged
commits.

> **What a tagger date is, and is not.** A tagger date is a field written into the tag
> object by the machine that created it. It is **user-controlled**: `git tag` accepts an
> arbitrary date, and the host clock is writable. A tagger date is therefore evidence
> that the artifact was **locally frozen before the recorded runs** — it is not
> third-party attestation, and it does not by itself corroborate anything. Every "YES"
> in C6 below means "locally frozen first", never "independently corroborated".

## C6 — summary

| Specification artifact | Locally frozen before the execution it governs? | Basis (local evidence only) |
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

**Two distinct classes — neither of them independently corroborated.**

The **four frozen checkpoints** — contract, execution profile, evidence policy v1 and
evidence policy v1.1, with the mutant registry contained in the contract-tagged tree —
carry annotated-tag tagger dates that precede all recorded execution by days, in trees
containing no evidence. They are **locally frozen before the recorded runs**. That is the
strongest statement the local evidence supports: the tagger dates are user-controlled, so
they are not third-party attestation and do not make these artifacts independently
corroborated pre-execution artifacts.

The **six experiment protocols** are **historical documented protocols**. Their contents
may well have been specified before execution — their text states it, and the session
transcript records them being written first — but **git provides no independent
corroboration of that ordering for any of them**, and neither does it for the four
checkpoints. Every protocol was committed after the runs it governs had executed, and five
share a commit with their own evidence. Amendment R3-A is a documented special case: it
was written after the verdict existed on disk and discloses this in its own text.

The distinction between the two classes is genuine and worth keeping — one class was
frozen and tagged before any run, the other was committed afterwards — but it is a
distinction between two grades of *local* evidence, not between corroborated and
uncorroborated.

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

## C8 — server-side evidence, and precisely what it does and does not bind

Two classes of server-side observation exist. Both are recorded here in full, including
their limits, because each is weaker than it first looks.

**(a) Current tag presence.** Remote inspection confirms that **all four tag refs exist on
the remote now**. That is a statement about the present. It establishes **nothing about
when any of them was first pushed**: a tag pushed today and a tag pushed on 2026-08-16
are indistinguishable from current presence alone. No first-push time for any tag object
has been established.

**(b) GitHub server events.** Two events are recorded by GitHub, not by this machine:

| event | server timestamp |
|---|---|
| repository became public | **2026-08-26T10:01:26Z** |
| `refs/heads/main` created | **2026-08-26T10:06:08Z** |

Both precede the earliest recorded execution anywhere in the project
(`g02-run-01`, 2026-08-26T11:00:19Z), the first by about 59 minutes and the second by
about 54 minutes. The second also coincides with the first entry in the local push reflog
above, so the two records are mutually consistent.

**The exact limitation, stated plainly.** The main-branch `CreateEvent` **contains no
commit SHA**. It records that a branch named `main` came into existence at that time; it
does not record *what* `main` pointed at. So this evidence supports exactly one claim:

> A public repository and a `main` branch existed before the earliest recorded execution.

It does **not** bind any particular artifact commit, tree or tag object to that time. It
cannot show that the contract, the execution profile, either evidence policy, or the
mutant registry was on the server at 10:06:08Z, because the event names no object. It is
**consistent with** the local push record and with the four tagger dates, and consistency
is not corroboration.

**Net position.** The four frozen checkpoints are **locally frozen before the recorded
runs**, on user-controlled tagger dates, in trees containing zero evidence files, in a
repository that server events show was public with a `main` branch before the earliest
recorded run. No server-side record binds a specific artifact to a specific time.
**They are not independently corroborated pre-execution artifacts and must not be
described as such.**

For completeness: all four frozen tag commits — `abed58d6`, `35fcaeec`, `c2f500c9`,
`560e8e81` — are ancestors of `4032f64b`, the first pushed commit, verified locally by
`git merge-base --is-ancestor`. Combined with the `CreateEvent` this remains suggestive
and no more, for the reason above: the event names no SHA, so the ancestry chain has
nothing on the server end to attach to.

---

# Does any conclusion depend on these?

Added 2026-08-28 (R2 Part D2). Determined from the run records, not from the protocol
text. All figures below are read from `experiments/RESULT_LEDGER.json`, which is generated
from the run records by `experiments/build_result_ledger.py`.

**Answer: the descriptive findings stand; the confirmatory status of every hypothesis
does not.**

Two things must be separated, and the earlier version of this section ran them together.

- **What was observed** — the recorded verdicts and workload measurements. These remain
  **descriptive evidence** and are unaffected by when any protocol was written. The
  oracle returned `success=true` on unrepaired states while the workload failed; the run
  records say so.
- **What may be inferred** — whether those observations *confirm* a hypothesis stated in
  advance. Because the protocols lack independent pre-execution corroboration, **their
  confirmatory status is limited.** H1, H2, H3 and H4 are historical exploratory
  robustness evidence, not confirmatory tests.

Within that limit, one specific worry can be bounded and one cannot.

**Bounded: the direction of the exclusions.** Post-hoc exclusions cannot have reversed
the direction of the available faulted verdicts, because every excluded run that produced
a faulted reading also returned `true`. Removing them removed nothing that pointed the
other way.

**Not bounded: selective analysis and incomplete reporting.** That risk **cannot be
eliminated retrospectively.** No amount of after-the-fact auditing establishes that the
analysis path, the exclusion grounds, or the set of reported quantities were fixed before
the data were seen. It is recorded as an open exposure, not resolved.

The old distinction between "selection rules" and "design parameters" is retained below
because it is still useful for locating *where* the exposure is concentrated — but the
claim that writing a design parameter down later "cannot bias anything" is **withdrawn**.
Recording a parameter after the fact still permits selective reporting of which
parameters, and which runs, were written up at all.

## Selection rules

### R3 — `harness_timing_failure` — NOT load-bearing. Never invoked.

R3 governs **FALSE** verdicts only: it excuses a false verdict caused by transient pod
churn from the injector's own `kubectl delete pods --all`.

**All 21 faulted-state instrument readings in the project returned `success=true`** — 18
from the 10 included historical runs, plus 3 more from two of the three excluded runs
(`w2-noise-00-INERT`: `in_process` and `worker`; `w3-hotel-01`: `in_process`). The third
excluded run, `w2-hotel-01`, was abandoned at the healthy gate and produced **no faulted
reading at all**. There has never been a false faulted verdict, so R3 has had nothing to
act on.

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

This matters because R3-A is the one rule in the project **written after its measurement
already existed on disk** — disclosed in its own text at `experiments/PROTOCOL_W4.md`,
written 2026-08-27T10:11:11.748Z when the faulted verdict had been on disk since
10:03:10.423Z.

**The protocol self-reports that the verdict's value had not been read** at that point,
and describes the method used to establish the file's existence without printing its
contents. **That self-report is not independently corroborated.** Nothing outside the
protocol's own text establishes what was or was not observed before the amendment was
written, and absence of observation is not the kind of thing this record can prove.

What can be stated from the record: the amendment resolved to **case (d)**, so it **did
not change the recorded classification** of `w4-hotel-01` — that run is treated exactly as
the G1 runs were, and branches (a), (b) and (c) were never applied.

**That observed no-op does not remove the procedural exposure.** Writing a classification
rule after the measurement it governs already exists is a procedural weakness regardless
of which branch the rule later selects: the branch taken is an outcome, not a safeguard.
The exposure is disclosed and bounded in effect, not eliminated.

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

H2 and H3 are **historical exploratory robustness checks** that returned null results:
the verdict did not change with submission delay, nor under Chaos Mesh noise. Their
function is to **test, and thereby weaken, two specific alternative explanations** — "the
verdict is an artifact of submission timing" and "the verdict is an artifact of a
quiescent cluster". Weakening a named alternative is not the same as removing the space
of alternatives, and neither check is confirmatory.

H4-lite is different in importance but not in kind. `w4-hotel-01` is the sole included
hotel-reservation run — one run, one instrument, n=1 — and it is what bounds the earlier
"one problem, one fault, one cluster" scope and motivates the claim that the blindness
travels with the oracle rather than with the application.

What that run establishes descriptively is exact and narrow: the oracle returned
`success=true` while 59.74 % of requests failed. **What it does not establish is
confirmatory support for H4.** H4 was not corroborated as specified in advance, so this is
**historical exploratory robustness evidence**, and a single run on a second application
is a weak base for a generality claim regardless of timing. Confirmatory language is
reserved for the future frozen MS-M01/MS-M02/MS-M03 matrix, which has not been run.

## The exclusions that were *not* made under any rule specified in advance

Three of the thirteen historical runs are excluded, and none of the three exclusions was
authorised by a rule written in advance. They were post-hoc judgements, and they are
recorded as such in `RESULT_LEDGER.json`:

| run | status | ground for exclusion | faulted verdict |
|---|---|---|---|
| `w2-noise-00-INERT-noise-never-started` | `HISTORICAL_EXCLUDED_INERT` | `enable_noise=True` had no effect; the driver never called `start_problem()`, where `NoiseManager.start()` lives (`conductor.py:451`) | `in_process` TRUE, `worker` TRUE |
| `w2-hotel-01` | `HISTORICAL_EXCLUDED_ABANDONED` | stopped at the healthy gate; the faulted state was never reached | **no reading exists** |
| `w3-hotel-01` | `HISTORICAL_EXCLUDED_SUPERSEDED` | sampler hardcoded to the wrong namespace; driver killed mid-window | `in_process` TRUE |

**These exclusions cannot have reversed the direction of the available faulted verdicts.**
Every excluded run that produced a faulted reading returned `success=true` — the same
direction as every included run. Two of the three excluded runs produced faulted readings
(3 between them); `w2-hotel-01` produced none. Restoring all three runs would raise the
count from 18 to 21 faulted-state instrument readings, all `true`.

That bounds one worry and not the general one. It shows the exclusions did not discard
contrary evidence, because none of the excluded readings was contrary. It does **not**
show that the exclusion grounds, the analysis path, or the set of quantities reported were
fixed before the data were seen — **selective-analysis and incomplete-reporting risk
cannot be eliminated retrospectively.**

On counting: the 10 included runs yield 18 faulted-state instrument readings, and those
18 are **not** 18 independent observations — two provenance categories reading the same
faulted cluster in one run share one deployment, one injection and one cluster state. The
run is the experimental unit, and the G1 runs are **separate repetitions** in the sense
that each had its own deploy, injection and recovery; statistical independence is not
established and is not claimed.

## What remains genuinely exposed

The descriptive findings are not exposed. The **inferential framing** is.

The honest statement for the paper has three parts:

1. The **four frozen checkpoints** — contract, execution profile, evidence policy v1,
   evidence policy v1.1, with the mutant registry inside the contract-tagged tree — were
   **locally frozen before the recorded runs**, on user-controlled tagger dates. Server
   events place a public repository and a `main` branch before the earliest recorded run,
   but name no commit SHA, so no artifact is bound to a server-side time (C8). They are
   **not independently corroborated pre-execution artifacts.**
2. The **six experiment protocols** are **historical documented protocols**, attested by
   transcript only, every one committed after the runs it governs.
3. Consequently every hypothesis in the historical study — H1, H2, H3, H4 — is
   **historical exploratory robustness evidence**. The measured verdicts and workload
   observations remain descriptive evidence and are unaffected; what is limited is their
   confirmatory status.

No protocol rule excluded or reclassified any result, and no excluded reading pointed the
other way, so the direction of the finding is not in question. What the framing costs is
the word "confirmatory" — and that word is reserved for the future frozen
MS-M01/MS-M02/MS-M03 matrix, which has not been run.

# W1 protocol — submission-latency hypothesis

Written 2026-08-26, stating that it was written **before any W1 run executed** and before
any W1 result existed. That ordering is not independently corroborated: this is a
**historical documented protocol** — see the note at the end of this file.

## H2 — hypothesis under test

> The mitigation verdict depends on how long after injection the agent submits, not on
> repair state. `wait_for_stable`'s `is_ready()` accepts phase `Failed`
> (`sregym/service/kubectl.py:260-261`), so `inject_fault()` can return while dying pods
> are still listed, and the oracle rejects any pod not in phase `Running`
> (`sregym/conductor/oracles/mitigation.py:96-99`). Therefore the SAME unrepaired system
> may yield `false` at zero delay and `true` at 60 s delay.

## Both outcomes are reportable — registered in advance

- If zero delay yields **`false`**: H2 is supported. The verdict is latency-dependent,
  and the benchmark's outcome for an identical unrepaired system is decided by agent
  submission timing rather than by repair.
- If zero delay yields **`true`**: H2 is not supported, and this is a **stronger** result
  for the primary claim, not a failure. It would mean the oracle accepts the unrepaired
  system even at the most adversarial timing available, removing the last "you waited for
  it to settle" objection to G1 and G3.

Neither outcome is preferred. Both are recorded.

> *Editorial note added 2026-08-28, no protocol decision changed.* "Removing the last
> objection" is an **unsupported historical assertion** as written, on two counts: a null
> result **weakens** the named alternative rather than removing it, and no enumeration
> establishes that it was the last one. H2 is **historical exploratory robustness
> evidence**; its hypothesis was not corroborated as specified in advance.

## Interaction with R3 (carried forward from PROTOCOL_G1)

R3 is unchanged and applies:

> A False verdict falsifies H1 only if every pod in the namespace was in
> phase Running with all containers ready at the moment of evaluation, and
> the False is attributable to a Deployment predicate (mitigation.py
> conditions 1-3). A False attributable to any pod not in phase Running --
> including the wrk2 workload pod -- is classified harness_timing_failure,
> is excluded from the result set, is preserved, and the repetition is
> re-run. This rule is registered before execution.

**Registered clarification, specific to W1.** For H2 the `harness_timing_failure`
classification is itself the finding, not an exclusion to be re-rolled. If a zero-delay
run returns `false` because pods were not yet `Running`, that IS the mechanism H2
predicts, and it is recorded as such. **No re-run will be performed to obtain a different
outcome.** R3 continues to govern H1: such a run does not count as a falsification of H1
and is excluded from the H1 result set, while being reported in full for H2.

## Method

- Driver: `SREMut/experiments/three_state_run.py` with a new `--null-agent-delay`
  argument. Default remains 60 (PROTOCOL_G1 R1). W1 runs pass `0`.
- Two runs: `w1-delay0-01`, `w1-delay0-02`.
- Everything else identical to G1: own `undeploy_app()`/`deploy_app()`, healthy gate
  (both oracles `true` and zero non-2xx), 2 s state sampler throughout, >= 10 complete
  wrk2 rounds per state where obtainable, both instruments (in-process and SREMut worker).
- Recorded per run: faulted verdict from both instruments, the exact interval from
  `inject_fault()` returning to oracle start, every pod phase at the oracle instant, and
  the R3 classification.

## Time box

30 minutes total. If exceeded, or if anything goes wrong, the item is abandoned, the
cluster is restored, and it is reported as incomplete. W1 Item B is a bonus, not a
requirement of the study.

---

## Note added 2026-08-28 — evidential status: historical documented protocol

**This is a historical documented protocol.** It is not corroborated as
pre-execution — and neither, on the evidence, is any artifact in this project. Its contents may well have been specified before execution —
the statement above says so — but git does not independently corroborate that ordering.
Nothing in this file's decisions, thresholds or recorded run behaviour is changed by this
note; only its evidential classification is.

**Git does not corroborate the ordering.** The commit that introduced this file
**postdates every execution it governs.**

- Introducing commit: `7820e937`, commit date **2026-08-27T03:47:32Z** (author date identical; no rebase or amend skew).
- Earliest execution timestamp recorded inside each run's own JSON record:

| run | earliest execution timestamp |
|---|---|
| `w1-delay0-01` | 2026-08-26T21:51:21.792023Z |
| `w1-delay0-02` | 2026-08-26T22:01:18.835253Z |

- Relation to its own evidence: **SAME COMMIT as its evidence**.

The session transcript records this protocol being written before the run started, and
the file's content is consistent with that. But the transcript is not a timestamping
authority, and every timestamp above originates on a single machine with a user-writable
clock.

**Correction (2026-08-28): SREMut does have a git remote.** Earlier versions of this note
stated it had none; that is wrong — `origin` is
`https://github.com/TheDeadcoder/SREMut.git`. It does not change the verdict. Per the
local push reflog, the push carrying this file postdates every run it governs, and those
reflog timestamps come from the same local clock. What the remote actually holds, and when
it received each push, was not checked: that requires network access. Full record:
`analysis/PREREGISTRATION_TIMELINE.md` §C7.

**Nothing in this project is an independently corroborated pre-execution artifact,
including the frozen ones.** The four frozen checkpoints — contract, execution profile,
evidence policy v1 and evidence policy v1.1, with the mutant registry contained in the
contract-tagged tree — are **locally frozen before the recorded runs**: annotated tags
whose tagger dates precede all recorded execution, in trees holding zero evidence files.
Tagger dates are user-controlled. GitHub server events place a public repository
(2026-08-26T10:01:26Z) and the creation of `refs/heads/main` (2026-08-26T10:06:08Z) before
the earliest recorded run, but the main-branch `CreateEvent` carries **no commit SHA**, so
it binds no artifact to that time; and current tag-ref presence on the remote says nothing
about when any tag was first pushed. Full forensic record:
`analysis/PREREGISTRATION_TIMELINE.md`, §C5 and §C8.


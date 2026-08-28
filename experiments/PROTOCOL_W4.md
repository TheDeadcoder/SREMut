# W4 protocol — one clean hotel-reservation three-state run

Written 2026-08-27, stating that it was written **before the run executed**. That ordering
is not independently corroborated: this is a **historical documented protocol** — see the
note at the end of this file. Either outcome is reportable, as with H2 (`PROTOCOL_W1.md`),
H3 (`PROTOCOL_W2.md`) and H4-lite (`PROTOCOL_W3.md`).

## Purpose

`w3-hotel-01` obtained the primary result (faulted verdict `{"success": true}` on
`missing_service_hotel_reservation`) but was compromised by two instrument defects: the
driver was killed mid-run, and the state sampler watched the wrong namespace. W4 repeats
the run once with both defects fixed, to obtain a complete three-state record.

## Instrument fixes applied before this run

**FIX 1 — sampler namespace.** `sample_state.sh` hardcoded `NAMESPACE="social-network"`.
It now takes an optional second argument, `sample_state.sh OUTPUT_JSONL [NAMESPACE]`,
defaulting to `social-network` so every pre-W4 invocation behaves identically. No existing
`samples.jsonl` was altered. `three_state_run.py` now resolves the namespace from the
registry before starting the sampler and passes it explicitly.

**FIX 2 — supervision.** `w3-hotel-01` died because the supervising shell command blocked
for ~14 minutes and hit a 10-minute tool timeout, taking the driver with it. The driver is
now launched under `setsid` so it leads its own session, and progress is judged by short
polls that read the log file and exit immediately. No supervising command blocks on the
process.

Neither fix touches the measurement path: the oracle call, the injector, the workload, and
the evidence dumps are unchanged.

## REGISTERED DEVIATION — single instrument (unchanged from W3)

The in-process oracle only, inside `conductor.py:269-271`'s try/except. The SREMut worker
is skipped because it pins `EXPECTED_NAMESPACE = "social-network"`
(`SREMut/src/sremut/original_oracle_worker.py:23`, enforced at `:135`, rejecting any other
namespace with `ORIGINAL_ORACLE_INPUT_INVALID` / exit 65). That constant is fixed by the
frozen evidence-capture policy v1.1 and the execution profile; the hard rules forbid
editing frozen pre-registration artifacts, so it is **not relaxed**.

Consequence, stated in advance: this run carries **one** independent measurement of the
verdict, not two. It is **weaker evidence than any G1 run** and does not inherit G1's
cross-instrument agreement.

## Rules carried forward

- **R1** (PROTOCOL_G1): 60 s between `inject_fault()` returning and the treatment oracle.
- **R2** (PROTOCOL_G1): >= 10 complete wrk2 rounds measured per state, in this run.
- **R3** (PROTOCOL_G1, unchanged, restated):

> A False verdict falsifies H1 only if every pod in the namespace was in
> phase Running with all containers ready at the moment of evaluation, and
> the False is attributable to a Deployment predicate (mitigation.py
> conditions 1-3). A False attributable to any pod not in phase Running --
> including the wrk2 workload pod -- is classified harness_timing_failure,
> is excluded from the result set, is preserved, and the repetition is
> re-run. This rule is registered before execution.

## Method

One run, id `w4-hotel-01`, problem `missing_service_hotel_reservation`, noise disabled,
sampler on the **hotel-reservation** namespace throughout, all three states
(HEALTHY / FAULTED / RESTORED). The run does **not** stop after FAULTED.

## Either outcome reportable

A faulted verdict of `true` supports H4-lite and shows the blindness travels with the
oracle rather than the application. A faulted verdict of `false` would bound the claim to
social-network and is equally reportable. A gate trip or failure is abandoned and reported,
not debugged.

---

# AMENDMENT R3-A — scope of the harness_timing_failure classification

**Written 2026-08-27T10:11:11.748Z (UTC), timestamp recorded at the moment of writing —
by this file, not by any independent authority.**

## Disclosure of what was on disk when this was written

At 2026-08-27T10:11:11.748Z, when this amendment was written:

- The faulted in-process verdict **HAD ALREADY BEEN WRITTEN TO DISK**, at
  **2026-08-27T10:03:10.423002+00:00** — approximately 8 minutes earlier — in
  `SREMut/experiments/w4-hotel-01.log`.
- **Its value had NOT been read.** Existence was established by a line count
  (`grep -c 'FAULTED in-process raw verdict'` -> 1) and its timestamp extracted with a
  `sed` expression that discards everything after the bracketed timestamp. The verdict
  value was not printed, not displayed, and did not enter the analysis context.
- `w4-hotel-01/three-state.json` did **not** yet exist; the driver was still running
  (RESTORED state pending).

This amendment was therefore written **after the measurement existed but before it was
observed**. That is a weaker guarantee than R1/R2/R3, whose own text says they were
written before their runs executed — though for none of them, including this one, does
git corroborate the ordering. It is stated plainly here rather than implied. The discriminator below
is defined entirely in terms of the **sampler timeline**, which is independent of the
verdict value, precisely so that the classification cannot be steered by the outcome.

## Rationale

R3 was registered to exclude **transient** pod churn caused by the injector's own
`kubectl delete pods --all` (`inject_virtual.py:306`) — pods that are dying or being
replaced at the moment of evaluation and that recover on their own. It was **not**
intended to excuse a **persistent** pod failure caused by the fault itself. Treating the
latter as a harness failure would discard a real result.

`missing_service_hotel_reservation` makes this distinction load-bearing: the rate service
reaches `mongodb-rate` on every memcached miss and calls `log.Panic()` on a Mongo error
(`SREGym-applications/hotelReservation/services/rate/server.go:267`). A deleted
`mongodb-rate` Service can therefore crash the rate pod persistently, which the generic
oracle's pod sweep (`mitigation.py:95-112`) would detect — **incidentally**, through pod
health, rather than by observing Services.

## Discriminator — decided from the sampler timeline, not from the verdict

| Case | Verdict | Pod state at evaluation | Persistence across the sampler window | Classification |
|---|---|---|---|---|
| **(a)** | FALSE | every pod `Running` | — | **H1 FALSIFIED** for this problem, attributable to a Deployment predicate (`mitigation.py` conditions 1-3, lines 70-84). Report it. |
| **(b)** | FALSE | a pod not `Running` | that pod **recovers** within the window without intervention (terminating, or replaced by a new pod that becomes Ready) | **harness_timing_failure** per R3. Excluded from the H1 result set, preserved, re-run permitted. |
| **(c)** | FALSE | a pod not `Running` | that pod **does NOT recover** across the window (CrashLoopBackOff, repeated Error, restart count climbing) | **RESEARCH VERDICT, not a harness failure.** The oracle detects this fault *incidentally* via pod health rather than by observing Services. Report as such. **DO NOT re-run.** |
| **(d)** | TRUE | every pod `Running` | — | As in G1. Report. |

## Evidence required for the classification

- Per-sample pod phases across the whole faulted window from
  `w4-hotel-01/samples.jsonl` (sampler now correctly on `hotel-reservation`, FIX 1).
- For the **rate** pod specifically: phase trajectory, whether a replacement appears, and
  restart counts if obtainable.
- The pod state in the samples bracketing the oracle window, and whether any not-`Running`
  pod persists beyond it.

Case (c) is the one this amendment exists to name. Under the unamended R3 it would have
been misfiled as a harness timing failure and re-run, discarding a genuine finding.

---

## Note added 2026-08-28 — evidential status: historical documented protocol

**This is a historical documented protocol, not an independently corroborated
pre-execution artifact.** Its contents may well have been specified before execution —
the statement above says so — but git does not independently corroborate that ordering.
Nothing in this file's decisions, thresholds or recorded run behaviour is changed by this
note; only its evidential classification is.

**Git does not corroborate the ordering.** The commit that introduced this file
**postdates every execution it governs.**

- Introducing commit: `0a3a0e59`, commit date **2026-08-27T10:23:01Z** (author date identical; no rebase or amend skew).
- Earliest execution timestamp recorded inside each run's own JSON record:

| run | earliest execution timestamp |
|---|---|
| `w4-hotel-01` | 2026-08-27T09:54:56.706800Z |

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

Only the four frozen artifacts — contract, execution profile and both evidence policies,
bound by annotated tags that predate all execution in trees holding zero evidence files —
qualify as independently corroborated pre-execution artifacts. Full forensic record:
`analysis/PREREGISTRATION_TIMELINE.md`.


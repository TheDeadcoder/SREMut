# W3 protocol — H4-lite, second application, SINGLE INSTRUMENT

Written 2026-08-27, stating that it was written **before the run executed**. That ordering
is not independently corroborated: this is a **historical documented protocol** — see the
note at the end of this file. Either outcome is reportable, as with H2 (`PROTOCOL_W1.md`)
and H3 (`PROTOCOL_W2.md`).

## H4-lite

> The oracle (`MitigationOracle`) and the injector (`inject_missing_service`) are shared
> across the `missing_service_*` family. If the blindness measured for
> `missing_service_social_network` is a property of the oracle rather than of the
> social-network application, `missing_service_hotel_reservation` should behave the same
> way: `{"success": true}` on an unrepaired system while the functional workload fails.

Both outcomes reportable. A `false` verdict would show the result is application-specific
and would bound the claim accordingly.

> *Editorial note added 2026-08-28, no protocol decision changed.* The blockquote above is
> the **hypothesis under test**, stated conditionally — it is not a finding, and "the
> blindness is a property of the oracle" is not asserted here. H4-lite was never
> corroborated as specified in advance, and **`w3-hotel-01` is not used as support for it
> or for cross-application generality**: the run is incomplete, below R2 on workload
> rounds, sampled the wrong namespace, and is superseded by `w4-hotel-01`.

## REGISTERED DEVIATION — single instrument

PROTOCOL_G1 requires **both** instruments (the in-process oracle and the SREMut worker)
to return `true` at the healthy gate. **This run uses the in-process oracle only.**

Reason: the SREMut worker hard-pins `EXPECTED_NAMESPACE = "social-network"`
(`SREMut/src/sremut/original_oracle_worker.py:23`, enforced at `:135`, rejecting any other
namespace with `ORIGINAL_ORACLE_INPUT_INVALID` / exit 65). That constant is fixed by the
frozen evidence-capture policy v1.1 and the execution profile; the hard rules forbid
editing frozen artifacts, so it is **not relaxed**. This was observed
directly in `w2-hotel-01`, which tripped the two-instrument gate for exactly this reason
while the application itself was healthy (10 rounds, 0/29447 non-2xx, in-process oracle
`{"success": true}`).

Consequence for the result, stated in advance: this run carries **one** recorded verdict
reading, not two. It is therefore **weaker evidence than any G1 run**
and must be reported as such. It does not inherit G1's cross-instrument agreement.

The instrument used is the conductor's own call path: `mitigation_oracle.evaluate()`
wrapped in the exact `try/except` of `conductor.py:269-271`.

## Method

- One run, id `w3-hotel-01`, problem `missing_service_hotel_reservation`.
- Noise disabled (`enable_noise=False`), to match G1.
- 60 s null-agent delay (PROTOCOL_G1 R1).
- 2 s state sampler throughout.
- >= 10 complete wrk2 rounds per state where obtainable.
- Three states: HEALTHY / FAULTED / RESTORED.
- Healthy gate: in-process oracle `true` AND zero non-2xx in the healthy window.

R3 (`PROTOCOL_G1.md`) continues to govern classification of any `false` verdict.

## Time box

30 minutes. On overrun or any failure the run is abandoned and reported; it is not
debugged.

---

## Note added 2026-08-28 — evidential status: historical documented protocol

**This is a historical documented protocol.** It is not corroborated as
pre-execution — and neither, on the evidence, is any artifact in this project. Its contents may well have been specified before execution —
the statement above says so — but git does not independently corroborate that ordering.
Nothing in this file's decisions, thresholds or recorded run behaviour is changed by this
note; only its evidential classification is.

**Git does not corroborate the ordering.** The commit that introduced this file
**postdates every execution it governs.**

- Introducing commit: `96f5b1e6`, commit date **2026-08-27T09:51:15Z** (author date identical; no rebase or amend skew).
- Earliest execution timestamp recorded inside each run's own JSON record:

| run | earliest execution timestamp |
|---|---|
| `w3-hotel-01` | 2026-08-27T05:12:33.384Z |

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


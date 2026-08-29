# Deviations and limits — the executed MS-M01/M02/M03 study

Every way the executed study differs from `PREREGISTRATION_MS_MUTANTS.md`, and every
limit on what the nine records support. Written 2026-08-29, after all nine repetitions
completed.

The pre-registration itself is **never amended**: its bytes carry an RFC 3161 token
(sha256 `75cd66ededb63fec896c56e8a21c094425ee5ea9b23b5cdece8654becfd1be04`, Free TSA
`Aug 29 09:08:34 2026 GMT`). Every departure from it is disclosed here instead.

Scope note: this file concerns the **pre-registered nine-repetition MS-I1..MS-I5 study**.
The sealed v1.2 matrix — authenticated run identities, hash-chained journal, external
anchor, all six invariants — remains **0/9 and unexecuted**, and the words "official" and
`OFFICIAL_FROZEN_ATTEMPT` stay reserved for it.

---

## A. Deviations from the pre-registration

### 1. Execution order of §3 steps 9 and 10

§3 lists the faulted **contract evaluation** as step 9 and the faulted **workload window**
as step 10. The driver performs the window first.

It has to. MS-I4 is defined in §4 over "≥50 requests in the fresh window with zero
non-2xx/3xx responses" — the contract *consumes* that window, so it cannot be evaluated
before the window exists. The numbered list in §3 and the predicate definition in §4 are
internally inconsistent, and the implemented order is the only one that satisfies §4.

Recorded here as a **prospective protocol amendment**, not a silent change. The same
window object is passed to MS-I4 and recorded in the run record, so the ordering is
visible in the evidence.

### 2. "Exactly one field" is false of MS-M01

§2 states that each mutant differs from the captured healthy Service "in **exactly one
field**". That holds for two of the three:

| Mutant | Difference from the captured Service |
|---|---|
| MS-M02 | one field — `spec.selector` replaced |
| MS-M03 | one field — `spec.ports[0].targetPort` set to 65535 |
| **MS-M01** | **not a field difference at all** — no Service is created |

**MS-M01 is an absence mutant, not a one-field mutation.** It models an agent that does
nothing, so after SREGym's injector deletes `Service/user-service` the object simply stays
absent; the driver records `action: NONE` and `body_sha256: null`, and its activation
criterion is `service_not_found`. The §2 sentence is accurate for M02 and M03 and wrong
for M01.

### 3. MS-I6 was not evaluated (declared in advance, restated here)

§6 declares in advance that MS-I6 (repair persistence) is not evaluated in this pass, so
this is a **declared scope limit rather than a deviation**. It belongs here because its
consequences are asymmetric, and the asymmetry is easy to get wrong:

- **The REJECT verdicts are unaffected.** Adding a sixth invariant can only add
  violations, never remove them. The nine faulted REJECTs and the 3/3 mutation score
  therefore hold for the **full six-invariant contract**, not merely for the five
  evaluated.
- **The control results do not carry over.** "Passed all 18 healthy and restored controls"
  is a statement about **MS-I1..MS-I5 only**. The full six-invariant contract has **never
  been shown to pass a genuine repair.** Any claim that the contract is sound on correct
  repairs is, at present, unevidenced for MS-I6.

---

## B. Measurement limits

### 4. The first faulted workload round straddles the mutation

`wait_for_rounds` selects rounds by `report_ts`, which is the **end** of a wrk2 round, and
a round spans about eleven seconds. In all nine runs the first selected round reported
between **1.43 s and 10.02 s** after the mutant was applied, so **every one of the nine
first rounds began before the mutation** and may contain pre-mutation requests.

Excluding the first round changes **no categorical result** — MS-I4 remains FAIL in all
nine, and every contract verdict is unchanged. It does change the rates. These
fully-post-mutation figures should be preferred for any quantitative claim:

| Mutant | r01 | r02 | r03 |
|---|---|---|---|
| MS-M01 | 9.9284% | 10.0803% | 9.9284% |
| MS-M02 | 8.6275% | 7.6397% | 6.7690% |
| MS-M03 | 9.7005% | 10.2539% | 9.7222% |

These are recomputed from the committed per-round detail by
`experiments/build_mutant_ledger.py`, which emits them as
`faulted_rate_excluding_first_round`. The as-recorded rates remain in the records
unaltered; nothing is regenerated.

### 5. Raw wrk2 logs are not retained

The driver records per-round summaries — `report_ts`, `requests`, `non2xx` — but not the
log bytes they were parsed from, and those bytes are destroyed by the next run's redeploy.

So the **arithmetic is fully recomputable** from the records, and is recomputed by the
ledger and its tests; the **parsing is not independently checkable.** A defect in
`wrk2_rounds()`'s log parsing would not be visible in the committed evidence. That parser
is the same one used by the thirteen historical runs, which is provenance, not proof.

### 6. Both oracle instruments shared one Kubernetes client

The in-process oracle and the isolated worker subprocess **both** run under
`SREGym/.venv` — CPython 3.12.3, kubernetes **30.1.0**. The two instruments differ in
call path (in-process inside the driver vs. an isolated subprocess under the SREGym
interpreter), not in client library.

The 32.0.1 figure that appears in the execution profile describes the **sealed SREMut
runner**, which is **not materialised on this host and was not used by any run reported
here**.

Consequently: agreement between the two instruments rules out a defect in either call
path, but **not a defect in the client library they share**. Two readings of one library
are not two independent measurements of the cluster.

---

## C. Harness weaknesses

Each of these is a real weakness in `experiments/mutant_run.py` or
`experiments/contract_check.py`. **None affected the nine committed records**, and each
verification below was performed against those records rather than assumed. They are
listed because they are *not* guarantees, and must not be described as such.

### 7. `preflight()` computes `passed` but nothing enforces it

The preflight checks context, namespace, node count, kubectl hash, SREGym commit,
applications commit and oracle module hash, and records `passed`. The driver never reads
it — a failing preflight would not stop the run.

*Verified: all nine runs recorded `preflight.passed == true` with zero failed checks.*

### 8. `_kjson` conflates absence with failure

`_kjson` returns `None` for a genuine NotFound **and** for a failed or unparseable read.
An infrastructure failure — API unavailable, malformed JSON — could therefore be scored as
resource absence, i.e. as a predicate failure or as MS-M01 activation.

This contradicts the frozen contract's own `infrastructure_failure_scope:
conditions_independent_of_the_tested_mutant`, which requires such conditions to be
separated from verdicts about the system under test.

*Verified: all 48 activation reads across the nine runs returned exit 0, as did every
contract observation. No `None` in the nine records arose from a failed read.*

### 9. `status: COMPLETE` does not assert the restored state recovered

The driver sets `COMPLETE` at the end of the happy path without asserting that the
restored oracles returned true, the restored workload was clean, or the restored contract
passed. A run that failed to restore could still be labelled `COMPLETE`.

*Verified: in all nine runs the restored in-process verdict, the restored worker verdict,
the restored workload (zero non-2xx) and the restored contract verdict all passed.*

### 10. The healthy gate does not include the healthy contract

The gate requires both oracle instruments to return true and the healthy workload to have
zero non-2xx. It does **not** require the healthy contract to pass — that is evaluated
after the gate, so a run could inject into a state the contract would have rejected.

*Verified: all nine healthy contracts returned PASS.*

### 11. A re-used run id would overwrite evidence

The run directory is created with `exist_ok=True`, so re-using a run id would overwrite
`replica-baseline.json` and the sampler log.

It could **not** silently overwrite a completed record: `run_worker` refuses to write to an
existing worker output path and raises
(`experiments/three_state_run.py:183`). That happens at the **healthy** oracle step, which
is **before any injection**, so the failure mode is a loud abort on an unmutated cluster,
not a corrupted record.

### 12. No run-id/mutant validation, and no durable attempt ledger

Nothing checks that a run id such as `ms-m02-r01` agrees with the `--mutant M02`
argument — the two are independent inputs. And no attempt ledger is written outside the
per-run directories, so an attempt that never produced a `mutant-run.json` would leave no
trace in the repository.

The defensible statement is therefore:

> **The repository contains nine completed records with no recorded exclusions or
> retries.**

Not: "no unrecorded attempt ever existed." The evidence cannot support the stronger
claim, and the stronger claim should not be made. (The run id and mutant id do agree in
all nine records, and each mutant's body sha256 is identical across its three
repetitions — but that is a property of the records, not an enforcement by the harness.)

---

Items 7 to 12 are scheduled for a hardening pass **after the current submission**. The
nine attested records will **not** be regenerated: they were produced by one unmodified
instrument whose hashes are recorded in every record, and that property is worth more than
the fixes.

---

# Deviations and limits — the fix-oracle study

A separate study, executed 2026-08-29: `fix-m01-r01`, `fix-m02-r01`, `fix-m03-r01`, driven
by `experiments/fix_oracle_run.py` against the prediction table in
`PREREGISTRATION_FIX_ORACLE.md` (RFC 3161 token `0x075A69B3`). Adjudication in
`ADJUDICATION_FIX_ORACLE.md`. The section above, on the MS-M01/M02/M03 matrix, is
unaffected by anything here.

That pre-registration is never amended either. Its bytes carry a token; every departure is
recorded here.

## 13. The probe-pod barrier is instrumentation we added

`ServiceEndpointMitigationOracle` creates a **real pod** on every evaluation that reaches
its connectivity probe — `create_namespaced_pod` at `service_endpoint_mitigation.py:94`,
labelled `app=service-connectivity-check` at `:72` — and deletes it in a `finally` at
`:111-115`. That delete is **fire-and-forget**: `delete_namespaced_pod` returns when the
API accepts the deletion, not when the object is gone.

A residual pod would sit in phase `Succeeded` (it runs `nc` and exits), and
`mitigation.py:96` rejects any pod not in phase `Running`. So one leftover would flip the
stock oracle — and therefore O1, and O4's first child — to `false` on a healthy cluster.
Our own `assert_no_probe_pods` could not have caught it: that matches only the `sremut-*`
selectors.

The driver therefore waits, before each of the twelve evaluations per run, for the
namespace to be free of that label, using the pattern from
`contract_check.delete_probe_pod`: `kubectl wait --for=delete` plus an **independent**
`kubectl get` re-check. It never deletes the pod — if SREGym's own `finally` failed, that
is a fact to surface, not to paper over. Failure to clear within 30 s aborts the run as
`INFRASTRUCTURE_FAILURE`.

**It never fired.** All 36 invocations cleared on the first attempt, 0.158-0.195 s, zero
pods ever observed, zero `kubectl wait` calls issued. That measures how fast the delete
settles in this cluster; it does not show the hazard was imaginary. The barrier was
self-tested against a deliberately planted pod with the same label and blocked for the
full 30 s across eight attempts, then cleared in 0.173 s once the pod was removed.

**O2 creates no pod at all.** `port = self.problem.expected_service_port` at `:65`
precedes the `try:` at `:93`, so with the attribute absent the `AttributeError` fires
before `create_namespaced_pod` is ever reached. The un-patched configuration has no
instrumentation footprint whatsoever.

## 14. O1 and O4 use the healthy baseline, not one captured at evaluation time

`MitigationOracle` compares the graded cluster against the replica counts captured by
`capture_baseline()`, so *when* that capture happens decides what is being measured:

| | baseline captured | compared against |
|---|---|---|
| `conductor.py:227`, production | once, healthy | the graded state |
| `mutant_run.py:655`, the nine runs | once, healthy, at STEP 4 | the graded state |
| a capture at evaluate time | in the graded state | **itself** |

The third form makes the three Deployment predicates (`mitigation.py:70-84`) trivially
satisfiable: every baseline Deployment necessarily still exists, none is scaled to zero,
and each has `ready >= desired` by construction.

So O1 evaluates `problem.mitigation_oracle` directly — it already holds the healthy
baseline from STEP 4, which makes O1 byte-for-byte the call
`three_state_run.evaluate_in_process` makes, the call the nine attested runs used. O4
builds a **fresh** `MitigationOracle` (never the O1 instance) and seeds it with
`replica_count = dict(captured_replica_baseline)` rather than re-capturing. Both record
`baseline_source` and `baseline_deployment_count` in every cell, so provenance is evidenced
rather than assumed; all 18 recorded 27 deployments from the healthy capture.

§2 of the pre-registration names the oracle **class** for each configuration and says
nothing about baseline timing, so this choice does not contradict it. The healthy baseline
is chosen because it is what `conductor.py:227` and the nine-repetition matrix both do.

An earlier revision of the driver called `capture_baseline()` inside the evaluation. That
was corrected before any run, and `tests/test_fix_oracle_record.py` now AST-checks that no
`capture_baseline()` call exists inside `_evaluate_one`.

## 15. Single provenance, one repetition, and what the study is about

- **No isolated-worker leg.** Every one of the 36 verdicts is an in-process call inside
  the driver's own process. The nine-repetition matrix read each state twice, through two
  call paths; this study reads it once. A reading here is one reading, not two.
- **One repetition per mutant.** Three runs. The three agreed cell-for-cell, which is
  consistency, not a measured variance, and no claim is made about stability across
  repetitions.
- **The claim is about oracle behaviour, not about cluster state.** What state the cluster
  was in is established independently, by the frozen contract (MS-I1..MS-I5) and the
  workload window, both evaluated in every state of every run. The oracle verdicts are read
  against that independently established state rather than being the evidence for it.

Items 13 to 15 are properties of a completed study, not defects scheduled for repair. The
three attested fix-oracle records will not be regenerated.

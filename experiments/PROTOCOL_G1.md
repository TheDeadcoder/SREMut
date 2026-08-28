# G1 protocol — pre-registered decisions

Registered 2026-08-26, **before any G1 repetition was executed** and before any G1
result existed. None of these rules was tuned to an observed outcome.

Scope: three complete three-state (HEALTHY / FAULTED / RESTORED) repetitions of the
null-agent experiment using driver (ii), run ids `g1-run-01`, `g1-run-02`, `g1-run-03`.

Prior context: `SREMut/analysis/G01_mitigation_verdict_path.md`,
`SREMut/analysis/G02_null_agent_plan.md`, `SREMut/experiments/g02-run-01/`.

---

## R1 — NULL-AGENT EPISODE

> 60 seconds shall elapse between `inject_fault()` returning and the treatment oracle
> being invoked.

**Rationale.** The real conductor path has an entire agent episode between
`_inject_fault()` (`sregym/conductor/conductor.py:219-240`) and `_evaluate_mitigation`
(`conductor.py:262-279`): the conductor advances to the mitigation stage, waits on
`waiting_for_agent`, and only evaluates once a submission arrives via `submit()`
(`conductor.py:516-568`). In `g02-run-01` the driver went from injection returning
(`11:03:19.383650Z`) to the treatment oracle starting (`11:03:20.021093Z`) in
**0.638 s**, which is far shorter than any real episode and leaves the measurement
close to the pod-churn window that the injector's own
`kubectl delete pods --all` (`sregym/generators/fault/inject_virtual.py:306`) creates.

60 s is chosen because it is the poll interval of SREGym's own null agent,
`clients/autosubmit/autosubmit_agent.py` (`sleep(60)`), which is the closest thing the
benchmark ships to a lower bound on a real agent episode. It is not chosen to produce
any particular verdict.

**Consequence to be reported, not avoided.** A 60 s delay gives the cluster more time
to settle, which makes a `True` verdict *more* likely, not less. If the oracle returns
`True` after a full 60 s of quiescence, that is a stronger result than the 0.64 s
measurement, because the "you measured a transient" objection is removed. If it returns
`False`, that is a genuine finding and must be reported as such.

---

## R2 — WORKLOAD WINDOW

> Each of the three states shall be measured over at least 10 complete wrk2 rounds,
> captured in the same run.

**Rationale.** In `g02-run-01` the HEALTHY workload rate (0.0000 %) came from the three
frozen baselines in `baselines/missing_service_social_network/run-0{1,2,3}/`, captured
in earlier sessions on earlier deployments — not from that run's own healthy window.
The `positive-control.md` three-state table marks it accordingly. R2 closes that gap:
all three rates must come from the same cluster, same deployment, same run.

A "complete round" is a wrk2 round that emitted a `requests in` summary line. Rounds
that abort with `unable to connect ... Connection refused` before completing are
startup artifacts (the injector and the recovery both restart every pod, and
`nginx-thrift` takes ~15-20 s to return) and are counted separately, never as complete
rounds.

10 rounds at ~11 s each is ~110 s of measurement per state. At the healthy rate this
covers ~10,240 requests; at the faulted rate it covers ~1,024 expected failures — ample
to separate 0 % from ~10 % without relying on a single round.

---

## R3 — PRE-REGISTERED INTERPRETATION RULE (restated verbatim)

Carried forward unchanged from `SREMut/analysis/G02_null_agent_plan.md`, section
"Pre-registered interpretation rule", where it was registered on 2026-08-26 before
`g02-run-01` executed. Reproduced here verbatim so all three rules sit together:

> A False verdict falsifies H1 only if every pod in the namespace was in
> phase Running with all containers ready at the moment of evaluation, and
> the False is attributable to a Deployment predicate (mitigation.py
> conditions 1-3). A False attributable to any pod not in phase Running --
> including the wrk2 workload pod -- is classified harness_timing_failure,
> is excluded from the result set, is preserved, and the repetition is
> re-run. This rule is registered before execution.

Cross-reference, also carried forward: "mitigation.py conditions 1-3" are the
Deployment predicates enumerated in `SREMut/analysis/G01_mitigation_verdict_path.md`
§A.2 — (1) a baseline Deployment is absent, `mitigation.py:70-73`; (2)
`spec.replicas == 0`, `mitigation.py:76-79`; (3) `ready_replicas < desired`,
`mitigation.py:81-84`. Conditions 4-6 are the pod-sweep predicates at
`mitigation.py:88-112`.

---

## Healthy gate (operational, applies per repetition)

A repetition proceeds to injection only if **both** hold at the healthy checkpoint:

1. Both healthy oracle verdicts (in-process and SREMut worker) are `True`.
2. Zero `Non-2xx or 3xx responses` lines appear across the healthy rounds.

Condition 2 is the same standard the project's own healthy-baseline capture enforces at
`harness/capture_healthy_baseline.sh:196-199`, where a single such line fails the
baseline.

If the gate trips, the repetition is recorded, is **not counted**, no fault is
injected, and the next repetition is started. This is a gate on whether a repetition is
admissible, never a filter on results after the fact.

---

## Measurement invariants held constant across all three states and all three runs

- The same 27-entry replica baseline, captured by `capture_baseline()`
  (`mitigation.py:23-32`) at the conductor's own position — immediately before
  `inject_fault()`, mirroring `conductor.py:227` then `:229`.
- Every oracle call wrapped in the exact `try/except` of `conductor.py:269-271`.
- Raw verdict dicts recorded as returned. Never coerced to bool, never normalised.
- The SREMut worker invoked with canonical JSON input (sorted keys, compact
  separators, no trailing newline) and a fresh, non-existent output path.
- Continuous 2 s state sampling for the whole run via
  `SREMut/experiments/sample_state.sh`, using the pinned kubectl
  `/home/sakibbuet2k19/.local/lib/sremut/kubectl/v1.32.0/kubectl`.

## Standing constraints

Nothing is written into `SREGym/`. No `tasklist.yml` is created. No frozen
pre-registration artifact is modified. No git command is run. Each repetition begins
with its own `undeploy_app()` / `deploy_app()`, so husks from the prior run are cleared
before the healthy checkpoint.

---

## Note added 2026-08-28 (R2 Part D1) — git does not corroborate the ordering claim

The statement above that this protocol was registered before execution is **left
unmodified and is not withdrawn**. What follows is the independent-corroboration status,
recorded so that no reader takes the statement as attested by version control.

**Git does not corroborate it.** The commit that introduced this file **postdates every
execution it governs.**

- Introducing commit: `9314bda3`, commit date **2026-08-26T20:11:56Z** (author date identical; no rebase or amend skew).
- Earliest execution timestamp recorded inside each run's own JSON record:

| run | earliest execution timestamp |
|---|---|
| `g1-run-01` | 2026-08-26T19:28:10.037861Z |
| `g1-run-02` | 2026-08-26T19:38:01.776896Z |
| `g1-run-03` | 2026-08-26T19:47:43.375448Z |

- Relation to its own evidence: **SAME COMMIT as its evidence**.

The session transcript records this protocol being written before the run started, and
the file's content is consistent with that. But the transcript is not a timestamping
authority, and SREMut has no git remote, so every timestamp here originates on a single
machine with a user-writable clock and is attested by no external service.

Full forensic record, including the four annotated tags whose pre-registration *is*
supported by git: `analysis/PREREGISTRATION_TIMELINE.md`.


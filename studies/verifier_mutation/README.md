# Verifier mutation

Whether SREGym's mitigation oracles reject plausible wrong repairs, and whether they see the whole
system, on every problem whose oracle rejects the null agent in the registry sweep. Each episode
stands an operational mutant in for the agent's repair and compares the problem's own verdict with a
reference verifier that judges the system against its own healthy baseline.

## Operators

Each operator acts on the application namespace without knowing the fault.

| Operator | Action | What a verifier needs to reject it |
|---|---|---|
| `RESTART` | rollout-restart every Deployment and StatefulSet | observe the fault, not pod health |
| `SCALE0` | scale every workload that is not fully ready after injection to 0 | check capacity against the baseline |
| `DELETE` | delete every workload that is not fully ready after injection | check that workloads still exist |
| `COLLAT` | SREGym's own `recover_fault()`, then scale to 0 the first healthy workload, by name, that injection did not change | check the whole application, not only the faulted part |

`SCALE0` and `DELETE` do not apply when every workload is ready after injection; `COLLAT` does not
apply when the oracle never accepts the recovered system.

## Episode

`mutation_probe.py` follows SREGym's validator lifecycle: deploy, capture the oracle baseline, record
the healthy state and workload traffic, inject. In `RESTART` episodes the oracle is then polled every
15 s for 300 s with no action (the verdict over time of a no-op); in the others until it first
rejects, for at most 300 s. The operator is applied, the application is given up to 120 s to roll
out, and the oracle is polled for 90 s. The first of those verdicts is the graded verdict. The
reference verifier then runs, SREGym's `recover_fault()` restores the fault and the application is
removed. In `COLLAT` episodes the recovery comes before the operator, and the oracle is polled until
it accepts, for at most 600 s.

## Reference verifier

`reference.py` compares the system with its own healthy state, captured before injection:

- capacity: every workload is present with at least as many ready replicas as when healthy;
- routing: every Service that had ready endpoints still exists with the same selector and ports and
  has ready endpoints;
- user requests: over 45 s of SREGym's own load generator, no round reports a failed request and at
  least half of the healthy request volume completes (not judged when the problem runs no workload).

The system is healthy when all three hold. Separately, `footprint_left` lists objects the injection
changed that still differ from the healthy state.

## Labels

| Label | Oracle | Reference verifier |
|---|---|---|
| `killed` | rejects | unhealthy |
| `survived` | accepts | unhealthy |
| `equivalent` | accepts | healthy: the generic action repaired the system |
| `false_reject` | rejects | healthy |
| `unknown` | changes its verdict within the 90 s | - |

The mutation score of a problem or operator is killed / (killed + survived).

## Hypotheses

Fixed before the first scheduled episode, from each problem's oracle composition at the pin
(`studies/oracle_history/timeline.json`) and its census verdict (`studies/registry_sweep/plan.csv`):

- H1: `SCALE0` and `DELETE` are killed when the oracle includes `MitigationOracle` or one of its
  subclasses (a capacity check), and survive otherwise.
- H2: `COLLAT` is killed when the oracle includes a capacity check, `WorkloadOracle` or `AlertOracle`,
  and survives otherwise.
- H3: `RESTART` is killed on census-adequate problems and survives on census-blind ones, unless it is
  equivalent.
- H4: some problems that the validator counts as detecting the fault reject the no-op only
  transiently within 300 s.

The prediction for each episode is in `schedule.csv`.

## Schedule

`make_schedule.py` lists every problem whose oracle rejected the null agent in the registry sweep
under each operator in turn, `RESTART` first, and assigns servers A, B and C in turn. The pilot runs every operator
once on five problems and is kept under `pilot/`.

## Running

On each server, from the repository root:

```bash
python3 studies/verifier_mutation/run_mutation.py --server C --pilot
python3 studies/verifier_mutation/make_schedule.py
python3 studies/verifier_mutation/run_mutation.py --server A
python3 studies/verifier_mutation/mutation_ledger.py
python3 studies/verifier_mutation/mutation_ledger.py --check
```

Each episode is written to `runs/<operator>/<problem_id>/attempt-<n>/` (or `pilot/`): `record.json`,
`result.json` and the probe's logs, with the same reset and layout as the registry sweep. The runner
stops after any episode that did not finish or whose fault recovery failed.

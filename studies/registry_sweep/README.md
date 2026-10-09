# Registry sweep

SREGym ships a lifecycle validator, `tests/integration/validate_problem.py`: deploy the
application, inject the fault, require the mitigation oracle to report failure, recover, require
it to report success. Upstream runs it only for a problem named in a pull request. This study runs
it, unmodified, on every problem registered at the pinned commit (`infra/PINS.md`).

## Predictions

`plan.csv` is written by `make_plan.py` and committed before any sweep attempt runs.

- `predicted_detection` comes from the static census (`analysis/census/coverage.csv`):
  `no` for BLIND (the oracle cannot observe what the injector perturbs), `yes` for ADEQUATE,
  empty for UNCERTAIN and for problems added after the census.
- The census predicts only whether the oracle detects the fault, not whether recovery is accepted.
- `retired` marks the problems that upstream later removed as saturated.
- `priority` 1 is every retired, BLIND or UNCERTAIN problem; 2 is the rest.
- `server` assigns the first attempt. The ten `pilot` problems come first and span applications
  and injector types.

## Outcome classes

Each attempt is classified from the validator's own stage results by `build_ledger.py`.

| Class | Meaning |
|---|---|
| `LIFECYCLE_PASS` | the oracle reported failure after injection and success after recovery |
| `NULL_ACCEPT` | the oracle never reported failure within the injection window |
| `ORACLE_ERROR` | as above, but the oracle raised instead of returning a verdict |
| `RECOVERY_REJECT` | the oracle detected the fault but never accepted the recovered system |
| `RECOVER_FAILED` | `recover_fault()` raised |
| `DEPLOY_FAILED`, `INJECT_FAILED` | the stage raised |
| `VALIDATOR_ERROR` | the validator crashed in an oracle stage |
| `TIMED_OUT`, `INTERRUPTED` | the attempt did not finish; infrastructure, not an outcome |

A problem's outcome is its class across attempts at the default 300 s window. It is `UNSTABLE` if
the attempts disagree, and `NOT_RUNNABLE` after two deployment or injection failures.

## Attempts

- Attempt 1 runs on the planned server, pilot first.
- Every problem whose attempt-1 outcome is not `LIFECYCLE_PASS` gets attempts 2 and 3, each on the
  other server from the previous attempt; a second deployment or injection failure ends it as
  `NOT_RUNNABLE`.
- Every `NULL_ACCEPT` problem gets one more attempt with `--inject-timeout 1800`, to separate slow
  onset from blindness; it is reported separately and never changes the outcome above.
- An attempt that did not finish is retried once under the next attempt number.
- If the pilot changes a runner parameter, the pilot problems are run again under the final
  parameters, and both sets of attempts are kept.

## Triage

A `NULL_ACCEPT` does not by itself show a blind oracle, and a pass does not show an adequate one:
the validator stops at the first failing check, so a transient state during injection (a rollout,
a restarting pod) counts as detection. `triage_probe.py` runs the same lifecycle without stopping.
It records the stock oracle every 15 s over the full 300 s window, upstream's `WorkloadOracle` in
each state, and object-level differences between the healthy, faulted and recovered states.

Triage runs on every problem whose outcome is not `LIFECYCLE_PASS`, and on every `LIFECYCLE_PASS`
whose first failing check reports a generic readiness reason (`deployment_replicas_unready`,
`pods_not_ready`, `no_pods_found`); priority 1 first. The faulted window is `never` (no failing
check), `transient` (failing checks, but the oracle accepts the unrepaired system at the end of
the window) or `persistent`. Each triaged problem is assigned one mechanism:

| Mechanism | Meaning |
|---|---|
| surface mismatch | the oracle reads kinds or namespaces disjoint from the perturbation |
| injector-restored surface | the injector itself re-establishes what the oracle reads |
| ready without serving | no probes, so Ready only means started |
| inert fault | the injection has no effect in this environment |
| transient fault | the system heals before grading |
| slow onset | detectable only after the 300 s window |
| incidental detection | the oracle fails for a reason unrelated to the fault |
| incomplete recovery | recovery leaves the perturbed state partly in place |
| over-strict oracle | the oracle rejects a recovered system |
| slow convergence | recovery completes after the 600 s window |

## Running

On each server, as the work user, from the repository root:

```bash
python3 studies/registry_sweep/run_sweep.py --server A --pilot
python3 studies/registry_sweep/run_sweep.py --server A
python3 studies/registry_sweep/run_sweep.py --server B --attempt 2 --ids <problem_id> ...
```

Before every attempt the runner returns the cluster to SREGym's recorded baseline, as SREGym's
Conductor does between problems: `reset_cluster.py` runs SREGym's own `reconcile_to_baseline()`,
removes the Deployments, DaemonSets, StatefulSets and Services that problems add to `kube-system`,
which that reconciliation leaves alone, and deletes Failed pods. The validator itself removes only
the application namespace, so namespaces, admission webhooks and other objects left by one problem
would otherwise carry into the next.

The runner skips attempts that already exist. It stops if the reset leaves a namespace, webhook
configuration or `kube-system` addition behind, if the cluster is unhealthy, if an attempt did not
finish, or if fault recovery failed during the validator's cleanup; any other cleanup failure is
undone by the next reset. Each attempt is written to `runs/<problem_id>/attempt-<n>/`:

- `record.json`: status, server, window, duration, stage results, what the reset removed before the
  attempt, namespaces left afterwards
- `summary.json`: the validator's own stage summary
- `stdout.log.gz`, `debug.log.gz`: the validator's logs with dates and times removed

Triage attempts use the same layout under `triage/<problem_id>/attempt-<n>/`, with `triage.json`
in place of `summary.json`:

```bash
python3 studies/registry_sweep/run_triage.py --server A --ids <problem_id> ...
```

Results are self-contained, so `runs/` and `triage/` can be copied between servers or to a laptop
at any time. `scrub_results.py` applies the same date and time removal to stored results; with
`--check` it changes nothing and lists any file that still has date or time information:

```bash
python3 studies/registry_sweep/scrub_results.py studies/registry_sweep/runs studies/registry_sweep/triage
python3 studies/registry_sweep/scrub_results.py --check studies
```

Then build the ledger:

```bash
python3 studies/registry_sweep/build_ledger.py
python3 studies/registry_sweep/build_ledger.py --check
```

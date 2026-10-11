# Alert baseline

Whether SREGym's `AlertOracle` rejects healthy and correctly recovered systems because it captures its
alert baseline before alerts that are always present can fire.

The `AlertOracle` passes when no alert fires in the problem's namespace for 120 s. It ignores alerts
that were already firing in a baseline captured before injection (SREGym#745). The Conductor captures
that baseline right before injection, which, with its default of no steady-state period
(`main.py --baseline`), is right after deploy; SREGym's validator and
`studies/verifier_mutation/mutation_probe.py` capture it at the same point. `ContainerCPUThrottling`
fires only after more than 25% of a pod's CPU periods over 5 minutes have been throttled for
2 minutes. In every verifier-mutation episode of the problems below, the captured baseline was empty
and the oracle rejected the healthy system with `ContainerCPUThrottling` on the `grafana` or
`load-generator` pod, both part of SREGym's deployment rather than of the faulted services.

## Episode

`probe.py` deploys the application and captures the oracle baseline, as SREGym does by default. It
records the alerts firing in the namespace every 30 s for 300 s, judges the healthy system with that
baseline, then captures the baseline again and judges the healthy system with the new one. SREGym
injects the fault and the oracle is polled until it rejects, for at most 600 s. After SREGym's
`recover_fault()` the oracle is polled with the later baseline for 600 s, then judged once with the
earlier one. Every poll records the firing alerts and which of them each
baseline would leave in. The application is then removed.

## Problems

The 13 problems whose oracle rejected the healthy system with `alerts_still_firing` in the
verifier-mutation runs (`run_alert_baseline.py`). The pilot runs two of them and is kept under
`pilot/`.

## Predictions

Fixed before the first episode:

- The baseline captured right after deploy is empty, and with it the oracle rejects the healthy
  system in every episode, with `ContainerCPUThrottling` on `grafana` or `load-generator`.
- With the baseline captured after 300 s, it accepts the healthy system, unless a throttling alert
  that was not firing at that moment fires later.
- With the later baseline, it accepts SREGym's recovery, unless the fault or the recovery replaces a
  throttled pod: the baseline matches alerts by all their labels, including the pod name.

## Running

On a server provisioned with `infra/setup_server.sh`, from the repository root:

```bash
python3 studies/alert_baseline/run_alert_baseline.py --server B --pilot
python3 studies/alert_baseline/run_alert_baseline.py --server B
python3 studies/alert_baseline/ledger.py
python3 studies/alert_baseline/ledger.py --check
```

Each episode is written to `runs/<problem_id>/attempt-<n>/` (or `pilot/`): `record.json`,
`result.json` and the probe's logs, with the same reset and layout as the registry sweep. The runner
stops after any episode that did not finish or whose fault recovery failed.

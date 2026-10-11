# ITBench audit

Whether ITBench's mitigation criterion, that a scenario's ground-truth alerts are cleared, rejects the null agent and
plausible wrong repairs, measured live on seven of its library scenarios on the OpenTelemetry demo. ITBench is pinned
at `333afa05c421b4e02a47b6c1b51686d20ce40cdd` (`infra/setup_itbench.sh`).

## Verifier under test

ITBench's leaderboard computes mitigation on a hosted service that is not in the repository. The repository's
alert-absent check (`scenarios/project/roles/evaluation/tasks/evaluate_prometheus_alert_absent.yaml` on the
`ciso-sre-convergence` branch, at `d6df01f9^`) passes when no firing alert has the ground-truth alert's name, polled
every 30 s for at most 300 s, after two consecutive passing polls. `probe.py` applies the same rule to each
scenario's ground-truth alerts (`library/scenarios/<id>/ground_truth.yaml`). The alert rules are ITBench's own: its
OpenTelemetry demo PrometheusRule and the kube-prometheus-stack defaults with `for: 5m`.

## Environment

`environment.py up` creates the kind cluster from ITBench's `clusters/kind/configs/simple.yaml` (one control plane, two
workers, `kindest/node:v1.37.0`), starts cloud-provider-kind, runs `itbench prepare cluster` and `make deploy-tools`.
ITBench needs Kubernetes 1.34 or later, so its kind, kubectl and cloud-provider-kind come from `/opt/itbench/bin`. At
the pinned commit `make deploy-applications` no longer maps a scenario to its application, so the probe enables the
OpenTelemetry demo with `applications_opentelemetry_demo_enabled`.

## Episode

`probe.py` deploys the OpenTelemetry demo, records the firing alerts of the healthy system and its workloads and
Services, and injects the scenario's faults with ITBench's `manage_faults.yaml`. It polls until every ground-truth
alert fires, for at most 900 s, applies one action, and polls the alerts every 30 s for 600 s. The alert-absent
verdict is read from the polls in the first 300 s. The reference verifier then compares the workloads and Services
with the healthy system (capacity and routing; the demo runs no wrk2 workload). ITBench removes the faults and the
application.

Actions: `NULL` (nothing), `RESTART`, `SCALE0` (the operators of `studies/verifier_mutation`), and `SILENCE` (scale
`frontend` and `frontend-proxy`, the services whose spans feed the error-rate alert, to zero).

## Labels

| Action | Alert-absent check | Reference verifier | Label |
|---|---|---|---|
| `NULL` | passes | - | `null_pass` |
| any | fails | unhealthy | `killed` |
| other | passes | unhealthy | `survived` |
| other | passes | healthy | `equivalent` |
| other | fails | healthy | `false_reject` |

## Predictions

Fixed before the first episode, in `schedule.csv` (`predicted`: whether the alert-absent check passes):

- The no-op fails where the ground-truth alert fires, and passes on scenario 49 (failing readiness probe) and 102
  (insufficient resource quota): `KubePodNotReady` counts only pods in phase Pending, Unknown or Failed, and these
  faults leave a Running pod that is not ready or no pod at all.
- `RESTART` passes everywhere: the pod-state alerts are keyed by pod and must hold for 5 minutes, so replacement pods
  clear them for at least that long, and the error-rate alert restarts with the demo's own collector.
- `SCALE0` passes the pod-state scenarios (no pod, no alert) and `SILENCE` the error-rate ones (no frontend spans).

## Running

On a host provisioned with `infra/setup_server.sh`, from the repository root:

```bash
sudo bash infra/setup_itbench.sh
python3 studies/itbench_audit/environment.py up
python3 studies/itbench_audit/run_audit.py --server D --pilot
python3 studies/itbench_audit/run_audit.py --server D
python3 studies/itbench_audit/ledger.py
```

Each episode is written to `runs/<scenario>/<action>/attempt-<n>/` (or `pilot/`): `record.json`, `result.json` with
every alert poll and the tail of every playbook's output, and the probe's log. The runner stops after any episode
that did not complete.

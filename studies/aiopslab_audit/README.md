# AIOpsLab audit

Whether the mitigation verifiers of AIOpsLab reject plausible wrong repairs, accept the healthy system
and give stable verdicts, measured on all 14 of its mitigation problems with the lifecycle, operators
and reference verifier of `studies/verifier_mutation`. AIOpsLab and its environment are pinned in
`infra/PINS.md`.

## Verifier under test

Each problem's own `eval()`. Most require every pod in the application namespace to be healthy; some
first check one setting (a Service's `targetPort`, a replica count, a container command). AIOpsLab
waits up to `AIOPSLAB_MITIGATION_SETTLE_SECONDS` (120 s by default) and accepts the first healthy
poll. The probe sets it to 0 and polls every 15 s itself, so both a single-poll verdict and the
default first-healthy-poll verdict can be read from the same series.

## Episode

As in `studies/verifier_mutation`, with AIOpsLab's own setup: OpenEBS and Prometheus installed as
AIOpsLab's `init_problem` installs them, the application redeployed, the fault injected and
AIOpsLab's workload started. AIOpsLab's load generator is a single wrk2 job, so for user requests the
reference verifier runs that job to completion on demand and reads its summary; Astronomy Shop runs
no such job and is judged on capacity and routing only.

AIOpsLab does not reset the cluster between problems. Before each episode `cluster_reset.py` deletes
the namespaces, admission webhook configurations, persistent volumes, and default-namespace jobs and
config maps added since the cluster was created (recorded at the first reset), and Failed pods. What
it removed is kept in `record.json`.

## Hypotheses

Fixed before the first scheduled episode from the problems' source; the predicted label of each
episode is in `schedule.csv`, blank where the source does not decide it.

- The null agent is accepted on `assign_to_non_existent_node_social_net-mitigation-1` (its Pending
  pod has no container statuses, which the health check skips) and on
  `astronomy_shop_kafka_queue_problems-mitigation-1` (the fault is a feature flag that leaves every pod
  healthy). The `RESTART` episodes' no-op windows test this.
- `COLLAT` survives on all 14 problems: no verifier compares the system with its healthy state.
- `SCALE0` and `DELETE` survive where the verifier checks pod health only.

## Running

On a host provisioned with `infra/setup_aiopslab.sh`, from the repository root:

```bash
python3 studies/aiopslab_audit/run_audit.py --server D --pilot
python3 studies/aiopslab_audit/run_audit.py --server D
python3 studies/aiopslab_audit/ledger.py
python3 studies/aiopslab_audit/ledger.py --check
```

Each episode is written to `runs/<operator>/<problem_id>/attempt-<n>/` (or `pilot/`) in the layout of
`studies/verifier_mutation`, and labelled by its ledger.

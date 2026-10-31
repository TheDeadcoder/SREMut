# AIOpsLab audit

Whether the mitigation verifiers of AIOpsLab reject plausible wrong repairs, accept the healthy system
and give stable verdicts, measured on all 14 of its mitigation problems with the lifecycle, operators
and reference verifier of `studies/verifier_mutation`. AIOpsLab and its environment are pinned in
`infra/PINS.md`.

## Verifier under test

Each problem's own `eval()`. Most require every pod in the application namespace to be healthy; some
first check one setting (a Service's `targetPort`, a replica count, a container command). On ten
problems, AIOpsLab waits up to `AIOPSLAB_MITIGATION_SETTLE_SECONDS` (120 s by default) and accepts the
first healthy poll. The probe sets it to 0 and polls itself, with 15 s between evaluations, for 120 s
after each operator. The other four (`NO_SETTLE` in `ledger.py`) do not use the setting:
`redeploy_without_PV-mitigation-1`, `scale_pod_zero_social_net-mitigation-1` and
`wrong_bin_usage-mitigation-1` judge once, and `misconfig_app_hotel_res-mitigation-1` waits up to 60 s
for every pod to be ready. The ledger labels every episode twice: by the first poll, as in
`studies/verifier_mutation`, and by AIOpsLab's default (`default_label`). On the ten, the default
accepts when any poll in those 120 s accepts. On the four, each poll is a whole verdict, so the default
is the first-poll label, `unknown` when the polls differ. With the proposed fix every problem uses the
setting, and `--patched` labels its runs by any poll.

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

## Proposed fix

`workload_check.patch` changes AIOpsLab's mitigation verifiers so that a pod without container statuses is
unhealthy unless it has succeeded, and records the application's Deployments and StatefulSets after it is
deployed and before the fault is injected; `eval()` then fails while a recorded workload no longer exists or is
scaled to zero. The four problems with their own pod checks use the shared one, and
`astronomy_shop_kafka_queue_problems-mitigation-1` also requires its feature flag to be off. The probe records the
workloads where the problem supports it, as the patched `init_problem` does.

Predictions for the full schedule on the patched checkout (`runs-patched/`), fixed before it runs:

- `SCALE0`, `DELETE` and `COLLAT` are killed wherever they apply, including the 23 episodes they survive today.
- The null agent is rejected on `assign_to_non_existent_node_social_net-mitigation-1` and
  `astronomy_shop_kafka_queue_problems-mitigation-1`. It is still accepted on `redeploy_without_PV-mitigation-1`,
  whose data loss leaves every pod healthy, and on the early polls of `misconfig_app_hotel_res-mitigation-1`.
- `RESTART` no longer survives on `assign_to_non_existent_node_social_net-mitigation-1`; its other verdicts do not
  change.
- AIOpsLab's own recovery is still accepted on every problem.

## Running

On a host provisioned with `infra/setup_aiopslab.sh`, from the repository root:

```bash
python3 studies/aiopslab_audit/run_audit.py --server D --pilot
python3 studies/aiopslab_audit/run_audit.py --server D
python3 studies/aiopslab_audit/run_audit.py --server D --attempt 2 --episodes <problem_id>:<operator> ...
python3 studies/aiopslab_audit/ledger.py
python3 studies/aiopslab_audit/ledger.py --check
```

With the proposed fix applied to the same checkout:

```bash
git -C ~/AIOpsLab apply "$PWD/studies/aiopslab_audit/workload_check.patch"
python3 studies/aiopslab_audit/run_audit.py --server D --runs studies/aiopslab_audit/runs-patched
git -C ~/AIOpsLab apply -R "$PWD/studies/aiopslab_audit/workload_check.patch"
python3 studies/aiopslab_audit/ledger.py --patched --runs studies/aiopslab_audit/runs-patched --ledger studies/aiopslab_audit/ledger-patched.json
```

Each episode is written to `runs/<operator>/<problem_id>/attempt-<n>/` (or `pilot/`, `runs-patched/`) in the
layout of `studies/verifier_mutation`, and labelled by its ledger.

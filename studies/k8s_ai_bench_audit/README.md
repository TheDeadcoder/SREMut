# k8s-ai-bench audit

Whether the verifiers of k8s-ai-bench reject the null agent and plausible wrong repairs and accept correct
alternatives, measured live on the tasks of its published leaderboard, and which of its published failures come
from the benchmark rather than the agent. k8s-ai-bench is pinned at `ac9ac9c4c4d975a4737f2f55b8d3e363320d3f52`
(`infra/setup_k8s_ai_bench.sh`).

## Verifier under test

A task passes when its `verify.sh` exits 0, or when the agent's final output matches the task's `expect` patterns.
Two of the 24 leaderboard tasks, `debug-app-logs` and `list-images-for-pods`, are judged on output only and have no
episodes. The harness runs the verifier right after the agent finishes.

## Episode

`probe.py` creates a fresh kind cluster (`kind create cluster --wait 5m` with kind's default node image, as the
harness does), runs the task's `setup.sh` from the task directory, applies one action, and runs `verify.sh` from the
repository root right after the action and again once the workloads in the task's namespaces have rolled out, for at
most 120 s. The cluster is then deleted.

Actions:

- `NULL`: nothing, on all 22 tasks with a verifier.
- `RESTART`, `SCALE0`, `DELETE`: the operators of `studies/verifier_mutation`, applied to the namespaces the setup
  created, on the 11 tasks whose setup deploys workloads.
- The task-specific scripts in `actions.py`: wrong repairs that leave the task undone (replacing a crashing command
  with `sleep`, editing a PersistentVolume's declared capacity, recreating a deployment the prompt asks to update
  without downtime) and correct alternatives (the `nginx:latest` image for an nginx pod, an equivalent NetworkPolicy
  written with `matchExpressions`, a StatefulSet recreated with one replica).

## Labels

| Action | `verify.sh` accepts | `verify.sh` rejects |
|---|---|---|
| `NULL` | `null_pass` | `killed` |
| generic or wrong repair | `survived` | `killed` |
| correct alternative | `accepted` | `false_reject` |

`label` uses the verdict right after the action, as the harness grades; `settled_label` uses the verdict after
the roll-out.

## Predictions

Fixed before the first episode from each task's prompt, setup and verifier, in `schedule.csv` (`predicted`:
`accept`, `reject`, or blank where the action is not expected to apply). Among them: `SCALE0` passes
`fix-image-pull`, because a deployment scaled to zero is Available and the restart check then compares two empty
reads; `create-pod` rejects any image but the exact string `nginx`; the NetworkPolicy check rejects equivalent
policies written differently; editing the PersistentVolume passes `resize-pvc` while expanding the claim does not,
because kind's storage provisioner cannot expand volumes; a StatefulSet recreated with one replica is rejected right
after the action, because the verifier runs `kubectl exec` before `db-0` is scheduled.

## Published results

`published.py` groups the failed runs of the published leaderboard (`site/combined_results.jsonl` in the pinned
checkout: 10 model configurations, 24 tasks, 5 runs each) by the step that failed, from each failure's message.

`rescore.py` scores each model again without the failed runs that come from the benchmark or its harness: the exact
image check, the verifier that runs before `db-0` is scheduled, the namespace that setup does not create, and the two
harness failures. One variant counts the runs that the exact image check rejected as passes; another also leaves out
the timed-out runs. Each ranking is compared with the published one by Kendall's tau.

## Cross-task check

With its defaults (`--cluster-provider kind`, `--concurrency 0`), k8s-ai-bench runs every task of a run at the same
time on one shared cluster; no task asks for a cluster of its own. `create-canary-deployment/setup.sh` deletes the
namespace `limits-test`, in which `create-pod-resources-limits` is graded. `interference.py` tests whether that
changes the other task's verdict: on a fresh kind cluster it runs the setup of `create-pod-resources-limits`, a
correct fix and its verifier, then the setup of `create-canary-deployment` (or nothing, as the control) and the
verifier again. Runs go to `interference/<victim>/<other task or none>/`:

```bash
python3 studies/k8s_ai_bench_audit/interference.py
```

## Running

On a host provisioned with `infra/setup_server.sh`, from the repository root:

```bash
bash infra/setup_k8s_ai_bench.sh
python3 studies/k8s_ai_bench_audit/run_audit.py --server D --pilot
python3 studies/k8s_ai_bench_audit/run_audit.py --server D
python3 studies/k8s_ai_bench_audit/run_audit.py --server D --attempt 2 --episodes <task>:<action> ...
python3 studies/k8s_ai_bench_audit/ledger.py
python3 studies/k8s_ai_bench_audit/published.py
python3 studies/k8s_ai_bench_audit/rescore.py
```

Each episode is written to `runs/<task>/<action>/attempt-<n>/` (or `pilot/`): `record.json`, `result.json` with the
tail of every script's output, and the probe's log. The runner stops after any episode whose cluster could not be
created or that raised an error.

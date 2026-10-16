# Agent study

Whether the verifier defects found by the audits change the verdicts of a real agent. Claude Code works each task as
the SRE agent; its final state is graded by the benchmark's own verifier and by the reference verifier of
`studies/verifier_mutation`. On AIOpsLab it is also graded by the same verifier with the proposed fix
(`studies/aiopslab_audit/workload_check.patch`). SREGym runs at the pin and, for its alert problems, at its HEAD.

## Agent

`agent.py` runs Claude Code headless, at the version and model pinned in `infra/PINS.md`, inside a container that
`infra/setup_agent.sh` builds with the kubectl of the benchmark's cluster. Every episode gets a fresh container that
mounts nothing but the cluster's kubeconfig, so the agent works the cluster through a shell, without permission
prompts, and sees no file of the host or of this repository. It authenticates with a Claude plan token and has no
API key in its environment. Each episode allows 20 minutes and 100 turns, and keeps the transcript as
`agent.jsonl.gz` and the reference verifier's captures of the healthy and the final system.

## AIOpsLab episode

`aiopslab_agent.py` deploys the application as AIOpsLab's `init_problem` does and records the Deployments and
StatefulSets with replicas, as the fixed verifier records them in `init_problem`. It captures the healthy system for
the reference verifier, injects the fault and starts AIOpsLab's workload. The agent receives the problem's own task
description, told that kubectl is available and to stop when the anomaly is mitigated. Once the agent stops and the
workloads settle, both verifiers are polled every 15 s for 120 s, AIOpsLab's settle window, and a verifier passes if
any poll passes. The reference verifier and the injector's footprint follow.

## ITBench episode

`itbench_agent.py` follows the episode of `studies/itbench_audit`: it deploys the OpenTelemetry demo with the
scenario's variables, captures the healthy system, injects the scenario's faults and waits for its ground-truth alerts
(at most 900 s). The agent is told which alerts are firing for the application, as an ITBench agent would see them.
After the agent stops, the alerts are polled every 30 s for 600 s: ITBench's alert-absent check reads the first
300 s, and a ground-truth alert that fires again later in the 600 s counts as a relapse. The reference verifier
follows. `run_itbench.py` runs ten scenarios: three where the fault leaves a pod not ready or waiting (20, 49, 59), one
with misconfigured autoscalers (38), two where requests fail (30, 43), two whose ground-truth alert cannot or need not
fire (102, 114), and two whose ground truth includes `NoRequestsReceived`, which the demo's idle services keep firing
(58, 31).

## SREGym episode

`sregym_agent.py` deploys the application with SREGym's Conductor and, as SREGym does by default, captures the
mitigation oracle's baseline right before it injects the fault. The agent receives the mitigation part of the task
text of SREGym's Claude Code client (`clients/claudecode`), built from the same application name, namespaces and
description. Once the agent stops, the mitigation oracle is evaluated at once, as SREGym evaluates it when the agent
submits, and polled for 90 s more; the first evaluation is the verdict. The workloads then settle and the reference
verifier follows. It judges the problem's namespace and the namespace that SREGym's root cause names, and on the
astronomy shop it also compares the feature flags in `flagd-config` with the healthy system, because the alert
problems inject their faults as flags. For alert oracles the episode records the captured baseline and the alerts
firing when the agent starts and after the verdict.

`run_sregym.py` runs five alert problems whose fault is a feature flag and three problems the oracle accepts without
any repair at the pin (`missing_service_astronomy_shop`, `missing_service_social_network`,
`operator_wrong_operator_image`). The alert problems also run at SREGym's HEAD, from a second checkout with its own
cluster (`infra/PINS.md`), where the CPU limits that made `ContainerCPUThrottling` fire were raised.

## Second agent

The same episodes also run with `claude-haiku-5-5`, a smaller model, kept under `runs-haiku/` with its own
`ledger-haiku.json`: the SREGym alert problems and `operator_wrong_operator_image` at HEAD, the ten ITBench
scenarios, the 14 AIOpsLab problems, and the two `missing_service` problems at the pin. A weaker agent leaves more
faults unrepaired or repaired in part, which is where a verifier's errors change a score. The first agent also runs
`operator_wrong_operator_image` at HEAD, ITBench scenario 38 a second time, and the five SREGym alert problems a
second time at the pin.

## Labels

For each verifier, its verdict against the reference verifier: `true_pass`, `false_pass` (passes an unhealthy
system), `true_fail`, `false_fail` (fails a healthy system). On ITBench a relapse also counts as unhealthy. The
ledger also lists the episodes whose verdict the fix changes, and passes that leave the injected change in place,
which the reference verifier cannot see for a feature flag or lost data.

## Predictions

Fixed before the first episode. They concern the verifiers, not what the agent does:

- The fixed verifier accepts every healthy system before injection.
- The fixed verifier passes no final state that the original verifier fails.
- Where the agent leaves a recorded workload deleted or at zero replicas, or a pod that cannot be scheduled, the fixed
  verifier fails and the original verifier can pass.
- On ITBench, scenarios 58 and 31 fail the alert-absent check whatever the agent does, unless it restarts the idle
  services, and scenario 102 passes it whatever the agent does.

Fixed before the first SREGym episode:

- At the pin, the mitigation oracle fails the agent's final state on all five alert problems, with
  `ContainerCPUThrottling` on `grafana` or `load-generator` among the firing alerts, unless the agent changes those
  two workloads: their baseline, captured right after deploy, is empty.
- At the pin, it passes the agent's final state on the three other problems, whether or not the reference verifier
  accepts it.

Fixed before the first SREGym episode at HEAD and the first episode of the second agent, for both agents:

- The AIOpsLab and ITBench predictions above hold.
- At HEAD, in every alert problem whose fault fires no alert at HEAD in `studies/alert_baseline`, the mitigation
  oracle passes a final state that leaves the fault's flag on, unless another alert fires.

## Running

On the AIOpsLab host, from the repository root:

```bash
bash infra/setup_agent.sh
(umask 077 && cat > ~/.claude-agent.env)
git -C ~/AIOpsLab worktree add --detach ~/AIOpsLab-fix
git -C ~/AIOpsLab-fix apply "$PWD/studies/aiopslab_audit/workload_check.patch"
cp ~/AIOpsLab/aiopslab/config.yml ~/AIOpsLab-fix/aiopslab/
python3 studies/agent_loop/run_aiopslab.py --server D --problems revoke_auth_mongodb-mitigation-1
python3 studies/agent_loop/run_aiopslab.py --server D
python3 studies/agent_loop/ledger.py
```

On the ITBench host (`studies/itbench_audit/environment.py up`), with Claude Code and the token set up the same way:

```bash
python3 studies/agent_loop/run_itbench.py --server D
```

On a SREGym host (`infra/setup_server.sh`), with Claude Code and the token set up the same way, and for HEAD a second
checkout in `~/SREGym-head` whose cluster was made by its own `kind/setup_kind_cluster.sh`:

```bash
python3 studies/agent_loop/run_sregym.py --server D
python3 studies/agent_loop/run_sregym.py --server D --sregym ~/SREGym-head --benchmark sregym-head --problems \
  astronomy_shop_ad_service_failure astronomy_shop_ad_service_high_cpu astronomy_shop_cart_service_failure \
  astronomy_shop_payment_service_failure astronomy_shop_product_catalog_service_failure
```

The second agent uses the same runners with `--model claude-haiku-5-5 --runs studies/agent_loop/runs-haiku`, and
`ledger.py --runs studies/agent_loop/runs-haiku --ledger studies/agent_loop/ledger-haiku.json` builds its ledger.

`~/.claude-agent.env` holds one line, `CLAUDE_CODE_OAUTH_TOKEN=<token>`, with the token from `claude setup-token`.
Each episode is written to `runs/<benchmark>/<problem_id or scenario>/attempt-<n>/`, with `sregym-head` for SREGym's
HEAD: `record.json`, `result.json`, the transcript, the logs and, on AIOpsLab, `deployed_workloads.json`. The runners
stop after any episode that did not complete. An episode in which the agent reached the plan's usage limit is renamed
`limit-attempt-<n>`, and the runner waits until the plan answers again and runs it again.

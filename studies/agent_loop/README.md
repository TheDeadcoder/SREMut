# Agent study

Whether the verifier defects found by the audits change the verdicts of a real agent. Claude Code works each task as
the SRE agent; its final state is graded by the benchmark's own verifier and by the reference verifier of
`studies/verifier_mutation`. On AIOpsLab it is also graded by the same verifier with the proposed fix
(`studies/aiopslab_audit/workload_check.patch`).

## Agent

`agent.py` runs Claude Code headless, at the version and model pinned in `infra/PINS.md`, with a fresh configuration
directory and an empty working directory for every episode. Bash is its only tool, in a mode that denies every command
not on its list, kubectl and sleep (Claude Code also lets read-only filters such as grep run in a pipe), so it sees the
cluster and nothing of this repository. It authenticates with a Claude plan token and
has no API key in its environment. Each episode allows 20 minutes and 100 turns, and keeps the transcript as
`agent.jsonl.gz`.

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
follows. `run_itbench.py` runs ten scenarios: four where the fault leaves a pod not ready or crash-looping (20, 49,
59, 38), two where requests fail (30, 43), two whose ground-truth alert cannot or need not fire (102, 114), and two
whose ground truth includes `NoRequestsReceived`, which the demo's idle services keep firing (58, 31).

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

`~/.claude-agent.env` holds one line, `CLAUDE_CODE_OAUTH_TOKEN=<token>`, with the token from `claude setup-token`.
Each episode is written to `runs/aiopslab/<problem_id>/attempt-<n>/` or `runs/itbench/<scenario>/attempt-<n>/`:
`record.json`, `result.json`, the transcript, the logs and, on AIOpsLab, `deployed_workloads.json`. The runners stop
after any episode that did not complete, including one that reached the plan's usage limit.

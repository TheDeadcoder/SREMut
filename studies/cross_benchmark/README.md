# Cross-benchmark analysis

Offline analyses over the ledgers and runs of the other studies: which validity property each verifier defect
violates and which audit instrument found it, what the agents changed, the headline rates with intervals and exact
tests, and a sample of episodes for human raters. Nothing here needs a cluster.

## Properties and instruments

`conformance.py` gathers every defect found on SREGym, AIOpsLab, k8s-ai-bench and ITBench, by task, validity
property and instrument, and counts the tasks measured for each property.

| Property | The verifier... |
|---|---|
| P1 | rejects the unrepaired system |
| P2 | accepts the healthy system |
| P3 | accepts a correct repair |
| P4 | rejects wrong repairs |
| P5 | gives the same verdict for the same repair |
| P6 | leaves no state behind and does not depend on other tasks |
| P7 | accepts only repairs that last |
| T | the task itself is valid: its setup takes effect, its text points at it, and neither its answer nor its verdict is in the agent's reach (a fault the verifier cannot see counts against P1) |

Instruments: `noop` (a no-op agent), `golden` (the benchmark's own repair), `healthy` (the system before
injection, except for the tasks in `HEALTHY_NOT_APPLICABLE` of `conformance.py`, whose correct end state by the
benchmark's own repair differs from it), `operators` (generic and task-specific wrong or alternative repairs), `scope` (an untouched workload
scaled to zero), `window` (verdicts over time), `repeats`, `isolation`, `durability`, `reference` (the reference
verifier), `published` (published results), `agents` and `static` (code reading). Prior audit practice is the no-op
agent and the benchmark's own repair; the comparison counts the tasks with a defect that only the other instruments
find, with and without `scope`, `isolation` and `static`. The code-reading predictions of each study are scored per
episode against the observed outcome. On an ITBench scenario where the check passed only in the agent episodes that
wrote what it reads (`agent_actions.json`) and failed every other, the agents set the verdict, a violation of T.

## Agent actions

`agent_actions.py` classes every `kubectl` call in the agent episodes as a read, a pod restart, a scale (to zero
or not), a deletion, a configuration change, an exec into a pod (and whether it ran one of the mitigation scripts
that AIOpsLab leaves in its pods), or a test pod, and counts per benchmark and model the episodes with each action,
those that changed nothing and those that only restarted pods. From kubectl's confirmations in each transcript (such
as `prometheusrule.monitoring.coreos.com/<name> patched`), it also lists the objects the agent wrote that alert-based
verifiers read: Prometheus Operator objects, such as alert rules, and the OpenTelemetry collector's configuration. It
counts the episodes with such a write and those the benchmark's verifier passed after a write to the monitoring
objects.

## Headline numbers

`headline.py` gives a Wilson 95% interval for each cell of the property matrix and each headline rate (mutation
scores, agent verdicts that disagree with the reference, code reading, defects beyond prior practice), McNemar's
exact test for the paired comparisons (AIOpsLab's default verdict before and after the fix of
`studies/aiopslab_audit`, SREGym's alert oracle on healthy systems at the pin and at HEAD, and verdicts that
disagree with the reference for each pair of agent models on the tasks both ran), agreement with Cohen's kappa
between first attempts and their repeats, and the share of repeated agent tasks with the same label in every
attempt. It also gives the acceptance rates of the settle study of `studies/alert_baseline`, and the share of agent
episodes that wrote the monitoring objects, per benchmark and model. Agent episodes on the tasks in
`REFERENCE_BLIND` of `conformance.py`, and undecided ones, are not counted.

## Rating sample

`rating.py sample` draws up to four episodes from each stratum of source (operator episodes and the benchmark's own
repairs in the SREGym and AIOpsLab audits, agent episodes) by reference verdict by benchmark verdict, one per task
and stratum, with a fixed seed. It writes `rating/sheet.md` with the evidence of each item and no verdict (the
task's fault from `rating/faults.csv`, which cites the upstream file of each description, the fault's effect, the
action or the agent's commands and closing message, and the state after the action against the healthy state),
`rating/blank.csv` to fill with `accept`, `reject` or `unsure`, and `rating/key.json` with each item's source and the
reference verdict. Two raters each fill a copy of `blank.csv`, following `rating/instructions.md`, without the key;
`rating.py score` compares the copies with each other and with the reference verifier (Cohen's kappa, the items each
rater left unsure, and the items where a rater and the reference disagree) and writes `rating/agreement.json`.
The two raters' filled copies are `rating/rater-1.csv` and `rating/rater-2.csv`.
`sample` keeps an existing `rating/sheet.md`, since the raters work from it; `--force` draws a new sample from the
current runs.

## Running

From the repository root, once the studies' ledgers and runs are in place:

```bash
python3 studies/cross_benchmark/agent_actions.py
python3 studies/cross_benchmark/conformance.py
python3 studies/cross_benchmark/headline.py
python3 studies/cross_benchmark/rating.py sample
python3 studies/cross_benchmark/rating.py score studies/cross_benchmark/rating/rater-1.csv \
    studies/cross_benchmark/rating/rater-2.csv
```

`--check` on the first three verifies the committed output against its inputs.

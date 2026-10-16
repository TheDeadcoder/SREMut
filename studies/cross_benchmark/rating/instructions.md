# Instructions for annotators

Thank you for helping. This takes about 1 to 2 hours.

## The task

`sheet.md` has 58 cases, R01 to R58. In each case, a fault was injected into an application running on Kubernetes.
Then one action was taken: the benchmark's own repair, a deliberately wrong or partial action, or the work of an AI
agent. You judge the system after that action.

For each case, answer one question: **should a correct grader for this task accept the state after the action?**

- `accept`: the fault is repaired and the application serves its users.
- `reject`: the fault is not repaired, or the application does not fully serve its users, for example because a
  workload or a service is down.
- `unsure`: the evidence is not enough to decide.

Write your answers in `answers.csv`: one row per case, with `id`, `decision` (`accept`, `reject` or `unsure`) and an
optional `note`. A short note helps most when you choose `unsure` or when a case was hard.

## How to read a case

- **Task**: the benchmark and the name of the task.
- **Fault**: what the injected fault does. For SREGym this is the benchmark's own description of the fault. For
  AIOpsLab and ITBench it is taken from the benchmark's fault code and scenario files.
- **Not fully ready after the fault** (some cases): workloads that were not fully ready right after the fault.
- **Action**: what was done after the fault. For agent cases, these are the agent's shell commands and its closing
  message. The closing message is the agent's own claim, and it may be wrong.
- **State after the action**, compared with the healthy system measured before the fault:
  - Workloads with fewer ready replicas than in the healthy state.
  - Services that had ready endpoints in the healthy state and now have none, or have a changed selector or ports.
  - User requests: requests per round sent by the application's load generator, after the action and before the
    fault, and how many rounds had failed requests. "not measured" means there was no load generator, or it gave
    no data.
  - Objects the fault changed that still differ from the healthy state.
  - Feature flags changed from the healthy state (some cases).
- `<time>` stands for a clock time that was removed from the record.

## Please

- Work on your own. Do not discuss the cases with the other annotator until you have both sent your answers.
- Judge each case from its own evidence. You may look up general Kubernetes behavior, but please do not look up
  these benchmarks' code or results.
- There is no target number of accepts or rejects.
- When you are done, send back `answers.csv` only.

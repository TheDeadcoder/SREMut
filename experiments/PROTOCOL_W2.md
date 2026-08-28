# W2 protocol — noise enabled, and a second application

Registered 2026-08-26, **before any W2 run executed** and before any W2 result existed.
Either outcome of each hypothesis is reportable, as with H2 in `PROTOCOL_W1.md`.

## What `enable_noise=True` actually does

Determined from source before running, so the paper can describe it precisely.

`ConductorConfig.enable_noise` (`sregym/conductor/conductor.py:40`) gates
`get_noise_manager()` calls at `conductor.py:309-314` (stage set), `:442-451` (problem
context + start), `:477-483` (stop before evaluation), `:508-514` (restart after), and
`:332-338` (stop at cleanup).

`NoiseManager` lives in `sregym/generators/noise/manager.py` (314 lines). Mechanics:

| Parameter | Value | Source |
|---|---:|---|
| Experiments per injection cycle | **2** | `manager.py:28` `MAX_CONCURRENT = 2` |
| Lifetime of each experiment | **120 s** | `manager.py:29` `DURATION = 120` |
| Cooldown between cycles | **300 s** | `manager.py:30` `COOLDOWN = 300` |
| Background poll interval | 5 s | `manager.py:105` |
| Selection | `random.sample(EXPERIMENT_CATALOG, 2)` | `manager.py:116` |

The catalog (`sregym/generators/noise/catalog.py:14`) has **four** Chaos Mesh experiments,
all `mode: one` (a single random pod in the target namespace):

| Name | CRD kind | Action |
|---|---|---|
| `pod-kill` | PodChaos | `pod-kill` |
| `pod-failure` | PodChaos | `pod-failure` |
| `network-delay` | NetworkChaos | `delay` |
| `network-loss` | NetworkChaos | `loss` |

Noise requires Chaos Mesh; `manager.py:249-263` checks for it and **installs it via helm
if absent**. Chaos Mesh runs in its own namespace, which the conductor's API proxy hides
from agents (`conductor.py:64-67`).

## H3 — noise and the mitigation verdict

> With noise enabled, the stock oracle's namespace-wide pod sweep
> (`mitigation.py:95-99`, which rejects any pod not in phase `Running`) is exposed to
> `pod-kill` and `pod-failure` experiments targeting a random pod in the same namespace.
> The mitigation verdict may therefore become unstable, or may be driven to `false` by
> noise rather than by repair state.

**Both outcomes reportable, registered in advance:**

- Verdict still `true` on the unrepaired system with noise active -> the primary finding
  survives the benchmark's own realism feature, which is a **stronger** result.
- Verdict `false` because a noise-killed pod was not `Running` at evaluation -> that is a
  **finding about noise**, recorded under R3 as a `harness_timing_failure` for H1
  purposes. Per PROTOCOL_W1's registered clarification, such a run is reported in full and
  **not re-run to obtain a different outcome**.
- Healthy gate trips because noise perturbs the pre-injection state -> also a reportable
  finding about noise; recorded, not retried.

## H4 — second application

> The oracle (`MitigationOracle`) and the injector (`inject_missing_service`) are shared
> across `missing_service_*`. If the blindness is a property of the oracle rather than of
> the social-network application, `missing_service_hotel_reservation` should behave the
> same way.

Either outcome reportable. If the hotel-reservation app fails to deploy or trips the
healthy gate, the item is **abandoned and reported** — the deployment is not debugged.

## Method

- D1: two runs, `w2-noise-01`, `w2-noise-02`, `three_state_run.py --noise`, 60 s
  null-agent delay (PROTOCOL_G1 R1), everything else identical to G1.
- D2: one run, `w2-hotel-01`, problem `missing_service_hotel_reservation`, noise disabled
  to match G1. Only if D1 completes inside the budget.
- All runs: own undeploy/deploy, healthy gate, 2 s sampler throughout, >= 10 wrk2 rounds
  per state where obtainable, both instruments.

## Time box

**90 minutes total for Part D.** On exceeding it, or on any failure, the remaining items
are abandoned, the cluster is restored, and the shortfall is reported. D2 is contingent on
D1 finishing early enough.

---

## Note added 2026-08-28 (R2 Part D1) — git does not corroborate the ordering claim

The statement above that this protocol was registered before execution is **left
unmodified and is not withdrawn**. What follows is the independent-corroboration status,
recorded so that no reader takes the statement as attested by version control.

**Git does not corroborate it.** The commit that introduced this file **postdates every
execution it governs.**

- Introducing commit: `3d46355a`, commit date **2026-08-27T05:07:53Z** (author date identical; no rebase or amend skew).
- Earliest execution timestamp recorded inside each run's own JSON record:

| run | earliest execution timestamp |
|---|---|
| `w2-noise-01` | 2026-08-27T04:07:04.382061Z |
| `w2-noise-02` | 2026-08-27T04:16:48.105650Z |

- Relation to its own evidence: **SAME COMMIT as its evidence**.

The session transcript records this protocol being written before the run started, and
the file's content is consistent with that. But the transcript is not a timestamping
authority, and SREMut has no git remote, so every timestamp here originates on a single
machine with a user-writable clock and is attested by no external service.

Full forensic record, including the four annotated tags whose pre-registration *is*
supported by git: `analysis/PREREGISTRATION_TIMELINE.md`.


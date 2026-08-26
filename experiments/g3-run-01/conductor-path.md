# G3 — production-path verdict from the real Conductor (g3-run-01)

Driver (i), driven in-process. `main.py` was **not** run, no Docker image was built, no
LLM credential was set. Executed 2026-08-26, kind cluster `kind-kind`, namespace
`social-network`.

Artifacts: `conductor-path.json`, `stdout.log`, `samples.jsonl` (321 samples),
`faulted/`, `restored/`, `wrk2-restored.log`.

---

## 1. What the real conductor recorded

`self.results` exactly as the conductor holds it, after a null submission
(`submit(None)`) at each stage:

```json
{
  "Diagnosis": {
    "accuracy": null,
    "checklist": [],
    "judgment": null,
    "reasoning": "LLM judge backend is not initialized - skipping evaluation",
    "submission": null,
    "success": null
  },
  "Mitigation": {
    "success": true
  },
  "TTL": 0.005100250244140625,
  "TTM": 60.29948878288269
}
```

**`results["Mitigation"] == {"success": true}`**, assigned by `_evaluate_mitigation` at
`conductor.py:272`, with `results["TTM"] == 60.299 s` at `conductor.py:273`.

This is the dict that `main.py:456-459` flattens into the `Mitigation.success` CSV cell.
The flattening is a pure pass-through (`snapshot[f"{stage}.{k}"] = v`), so the cell this
run would have produced is `Mitigation.success = True`.

### Diagnosis — one correction to an earlier prediction

G0.2 §C predicted `Diagnosis.success = False` without credentials. The actual value is
**`null`, not `False`**. The judge backend fails to initialise and is caught at
`judge.py:58-65`, so `judge()` returns `(None, "LLM judge backend is not initialized -
skipping evaluation")` at `judge.py:68-69`; the oracle records a null judgment rather
than raising. `_evaluate_diagnosis`'s exception handler at `conductor.py:249-251` was
therefore never reached. The prediction was directionally right — diagnosis does not
block mitigation — but wrong on the recorded value.

`TTL = 0.0051 s` confirms the diagnosis stage completed essentially instantly.

---

## 2. Conductor path actually taken

| Step | Call | Evidence |
|---|---|---|
| construct | `Conductor(ConductorConfig(deploy_loki=False, enable_noise=False))` | — |
| entry | `await conductor.start_problem()` (`conductor.py:388`) | returned `success` in 175.579 s |
| stages | `get_problem_stages()` -> no `tasklist.yml` -> default | `tasklist = ['diagnosis','mitigation']` |
| build | `_build_stage_sequence()` | `stage_sequence = ['diagnosis','mitigation']` |
| deploy | `undeploy_app()` / `deploy_app()` | inside `start_problem` |
| **inject** | `_advance_to_next_stage(0)` -> **`_inject_fault()`** (`conductor.py:296-297`) | entered 21:12:35.260312Z, returned 21:13:16.938364Z, **41.678 s**; `fault_injected=True` |
| stage 1 | `submit(None)` -> `_evaluate_diagnosis` | ack `{'status':'ok','message':'Submission received'}`; stage advanced to `mitigation` |
| **R1 wait** | protocol-mandated | see below |
| stage 2 | `submit(None)` -> **`_evaluate_mitigation`** (`conductor.py:262-279`) | 21:14:16.940351Z -> 21:14:17.238360Z |
| teardown | `_advance_to_next_stage(2)` -> `:317` -> `_finish_problem()` | reached 21:14:17.238360Z, **suppressed** |

Nothing unauthorised was required: no `register_agent` (it only sets `self.agent_name`),
no `start_k8s_proxy` (a `main.py` call), no credentials, no container, and nothing was
written into `SREGym/`.

### R1 — and why it mattered here

| Quantity | Value |
|---|---:|
| `_inject_fault()` returned | 2026-08-26T21:13:16.938364Z |
| elapsed before the wait | **0.006 s** |
| protocol wait inserted | 59.994 s |
| mitigation evaluation began | 2026-08-26T21:14:16.940351Z |
| **actual interval, injection -> evaluation** | **60.002 s** |

The conductor's own path put **6 milliseconds** between injection returning and the
diagnosis stage completing, because the judge fails instantly without credentials. Left
alone, the mitigation verdict would have been taken ~6 ms after injection — deep inside
the pod-churn window the injector itself creates. PROTOCOL_G1 R1, registered before any
G1 repetition, is what prevented that.

### Disclosed interventions

Two, both strictly outside the verdict path:

1. `_inject_fault` wrapped to timestamp entry/exit; the original is called unchanged.
2. **`_finish_problem` neutralised.** `_advance_to_next_stage(2)` (`conductor.py:505` ->
   `:317`) calls `_finish_problem` -> `_cleanup_sync`, which at `conductor.py:341-350`
   runs `problem.recover_fault()`, `problem.app.cleanup()` and `reconcile_to_baseline()`
   — a full teardown. It executes **after** `conductor.py:272` has assigned the verdict
   (call order `:491` evaluation, then `:505` advance), so it cannot influence it. It was
   suppressed because the protocol forbids teardown and the faulted state was required
   afterwards. The suppression is recorded in `conductor-path.json`
   (`finish_problem_suppressed: true`).

---

## 3. Cluster state at the verdict instant

The evaluation lies wholly between two sampler ticks 2.654 s apart:

| Sample | api_ok | user_service_present | EndpointSlices | Services | Pods | not Running | not ready | deployments below desired |
|---|---|---|---:|---:|---:|---:|---:|---:|
| 21:14:16.320Z (last before submit) | true | **false** | **0** | 29 | 28 | **0** | 0 | 0 |
| *evaluation 21:14:16.940 -> 21:14:17.238* | | | | | | | | |
| 21:14:18.974Z (first after finish) | true | **false** | **0** | 29 | 28 | **0** | 0 | 0 |

**R3 does not apply.** Zero samples between injection returning and the evaluation showed
any pod not in phase `Running` (checked across the whole interval, not just the
brackets). The verdict is a research verdict, not a `harness_timing_failure`.

So at the moment the real conductor recorded `{"success": true}`, `Service/user-service`
was absent, no EndpointSlice existed for it, and 27/27 deployments and 28/28 pods were
fully healthy.

---

## 4. Agreement with driver (ii)

| | driver (ii), G1 x3 | driver (i), this run |
|---|---|---|
| Instrument | `three_state_run.py` | **real `Conductor`** |
| Mitigation verdict | `{"success": true}` x 9/9 (3 runs x 3 states) | **`{"success": true}`** |
| Recorded where | driver variable | **`conductor.results["Mitigation"]`, `conductor.py:272`** |
| Service absent at evaluation | yes, all 3 faulted evaluations | **yes** |
| Injection -> evaluation | 60.001 s x3 | **60.002 s** |
| Pods not Running at evaluation | 0 | **0** |

**They agree.** The verdict the real conductor records for an un-mitigated system is the
same `{"success": true}` that driver (ii) produced nine times. Driver (ii) was not
producing an artefact of its own construction.

---

## 5. Which G02 §C deviations this run closes

The nine enumerated deviations of driver (ii) from the conductor path
(`analysis/G02_null_agent_plan.md`, section C):

| # | Deviation | Status after g3-run-01 |
|---|---|---|
| 1 | No `get_problem_stages()` / `_build_stage_sequence()` | **CLOSED** — both ran; `tasklist=['diagnosis','mitigation']`, `stage_sequence=['diagnosis','mitigation']` |
| 2 | No `submit()` / `waiting_for_agent` handshake | **CLOSED** — two real `submit(None)` calls through `conductor.py:516-568` |
| 3 | No `results["Mitigation"]` / `TTM` | **CLOSED** — both recorded: `{"success": true}`, `TTM=60.299` |
| 4 | No diagnosis stage | **CLOSED** — diagnosis ran first and recorded `Diagnosis` |
| 5 | `_evaluate_mitigation` try/except not exercised | **CLOSED** — the real method ran, not a replica |
| 6 | No `_finish_problem()` / `_cleanup_sync()` | **REMAINS OPEN, deliberately** — reached and suppressed. Post-verdict teardown only; cannot affect `:272` |
| 7 | No noise manager | **REMAINS OPEN** — `enable_noise=False`, matching all three baselines and G1 |
| 8 | `deploy_loki=False` | **REMAINS OPEN** — matches all three baselines and G1; Loki is outside the app namespace and unobserved by the oracle |
| 9 | No agent container / run publishing | **REMAINS OPEN** — see section 7 |

Five of nine closed. Of the four remaining, three (6, 7, 8) are deliberate and
post-verdict or configuration-identical to the frozen baselines; the fourth (9) is
covered below.

---

## 6. Step 4 — FAILED MEASUREMENT, cause identified

The post-verdict faulted workload window returned **0 rounds** and timed out. This is a
**defect in my instrument, not a property of the system**, and the faulted rate for
g3-run-01 is therefore **PENDING — unobtainable retrospectively**.

Cause: in `conductor_path_run.py` the pod-listing jsonpath was written as a
single-quoted Python literal, so `\n` became a real newline instead of the two-character
escape kubectl expects. Reproduced directly:

```
arg = 'jsonpath={range .items[*]}{.metadata.name}{"\n"}{end}'   # literal newline
-> exit 1, stdout '', stderr: error parsing jsonpath ... unterminated quoted string
```

With no pod names returned, no logs were read and no rounds were ever counted. The
sampler was unaffected (a separate script with correct escaping; 321 samples collected),
so section 3 is sound. `three_state_run.py` used the escaped form, so **G1's workload
numbers are unaffected**.

The faulted-window pod `wrk2-job-rmvs7` was `Running` for the full window
(21:13:19.430Z -> 21:24:23.190Z, confirmed from `samples.jsonl`), so rounds certainly
occurred — but recovery's `kubectl delete pods --all` deleted that pod and its logs are
unrecoverable. No repetition was run, per protocol.

**What is still measurable, and was captured:** the restored-state workload, from the
post-recovery pod (`wrk2-restored.log`):

| State | Rounds | Requests | Non-2xx | Rate |
|---|---:|---:|---:|---:|
| RESTORED (g3-run-01) | 15 | 15,360 | **0** | **0.0000 %** |

Consistent with G1's restored rate (0.0000 % across 30 rounds x 3 runs). For the faulted
rate, the comparable figures remain G1's: 10.1172 %, 10.1953 %, 9.2792 % (pooled
9.8639 % over 30,718 requests).

Script provenance: the run used sha256
`00e7916f9da4fcc11d92c32dc312c43dabbb45ed72ddc966b5b15238a05be4f5`. The file has since
been corrected to sha256
`9f0c90fa54f14e6eef9572e3cba9ebd33f6f1f5c648ffce7a11e377dec9db10a`; the only change is
the jsonpath escaping plus a comment recording this defect.

---

## 7. What this run does not cover, and why it does not matter for the verdict

`main.py` was blocked by two gates (documented in the G2 report): the judge preflight at
`main.py:585-586` hard-exits via `sys.exit(1)` without `JUDGE_MODEL_ID`, and
`main.py:594-595` -> `container_runner.py:366-377` would auto-build
`sregym-agent-base:latest`, which is absent locally.

Three things `main.py` adds that this run does not have:

- **Agent containerization.** `AgentLauncher` would run `autosubmit`'s kickoff command in
  a container (`agent_launcher.py:68-69`). `autosubmit` posts a fixed string and performs
  zero cluster work (`grep -cE 'kubectl|kubernetes|helm'` on it returns 0). Whether that
  string arrives from a container or from an in-process `submit()` call cannot change the
  verdict, because the solution is **never read**: `conductor.py:265` says so in a comment
  and `_evaluate_mitigation` calls `evaluate()` with no arguments at `:268`.
- **LLM judging.** Affects only the diagnosis stage, which writes `results["Diagnosis"]`
  at `conductor.py:253`. Mitigation is assigned separately at `:272`, and G0.1 established
  the two are never combined, gated, or cross-checked.
- **CSV publishing.** `main.py:452-461` flattens `conductor.results` into
  `snapshot[f"{stage}.{k}"]`. It is a pure pass-through of the dict recorded here; it
  reads the verdict, it does not compute it.

None of the three touches the path from `mitigation_oracle.evaluate()` at
`conductor.py:268` to `self.results["Mitigation"] = r` at `:272`. The verdict recorded in
section 1 is the verdict `main.py` would have published.

**Stated plainly:** the production path and driver (ii) agree. The real SREGym conductor,
running its own stage machinery with a null submission, recorded `Mitigation:
{"success": true}` for a system whose `user-service` had been deleted 60 seconds earlier
and never repaired.

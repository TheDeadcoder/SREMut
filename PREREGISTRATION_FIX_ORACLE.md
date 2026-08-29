# Pre-registration: does the proposed `missing_service` fix actually work?

**Status:** `FROZEN_BEFORE_EXECUTION`
**Written:** 2026-08-29, before any oracle configuration was executed against a cluster.
Read-only source reading and read-only cluster checks precede it; no execution does.
**Executed runs at freeze time:** 0.

`analysis/PR_PLAN.md` predicts, from source alone, that the natural fix for
`missing_service` — attaching `ServiceEndpointMitigationOracle` — **does not work as-is**.
It predicts a specific mechanism: the class reads `problem.expected_service_port` at
`service_endpoint_mitigation.py:65`, that attribute does not exist on `MissingService`, an
`AttributeError` is raised, the broad handler at `:178` swallows it, and the oracle returns
`{"success": False}` — **rejecting correct repairs**.

That prediction has never been executed. This document fixes, in advance, what will be
run, what is predicted, and what would falsify it. It is timestamped before any driver
code is written.

The README currently says of the proposed patch: "This patch is a prediction, not a
result." The purpose of this study is to make that sentence deletable honestly — in
whichever direction the evidence points.

**This study may falsify `PR_PLAN.md`.** If the un-patched oracle accepts a correct
repair, C3 is wrong and that is the finding.

---

## 1. Scope, stated plainly

- **Single-provenance.** Every verdict below comes from an **in-process** call inside the
  driver's own Python process. There is **no isolated worker leg** in this study, unlike
  the nine-repetition mutant matrix. A reading here is one reading, not two.
- **One repetition per mutant.** Three runs, one for each of MS-M01, MS-M02, MS-M03. No
  repetition, therefore no claim about stability across repetitions.
- **Claims are about oracle behaviour, not about the cluster.** What state the cluster was
  in is established independently, by the frozen contract
  (`contracts/missing_service_social_network.yaml`, invariants MS-I1..MS-I5) and by the
  workload window — both of which are evaluated in every run alongside the four oracle
  configurations. The oracle verdicts are then read against that independently established
  state.
- **`SREGym/` is not patched.** The `expected_service_port` attribute is set on the problem
  *object* in our own process before `evaluate()` is called, and deleted afterwards. That
  reproduces the proposed patch's object state at evaluate time without modifying a byte
  of the submodule. The submodule is verified clean at the end of every session.
- This is **not** the sealed v1.2 matrix. The word "official" and the status
  `OFFICIAL_FROZEN_ATTEMPT` remain reserved for that, which is still unexecuted.

---

## 2. The four oracle configurations

Each is evaluated in **all three states** — healthy, faulted, restored — of **each** of the
three runs.

| # | Configuration |
|---|---|
| **O1** | stock `MitigationOracle(problem)` — the baseline, already measured nine times |
| **O2** | `ServiceEndpointMitigationOracle(problem)` **un-patched** — `expected_service_port` absent |
| **O3** | `ServiceEndpointMitigationOracle(problem)` **patched** — `problem.expected_service_port = 9090` set first |
| **O4** | `CompoundedOracle(problem, MitigationOracle(problem), ServiceEndpointMitigationOracle(problem))` with the port set — the actual proposed patch |

---

## 3. Predicted verdicts

| Configuration | healthy | M01 faulted | M02 faulted | M03 faulted | restored |
|---|---|---|---|---|---|
| **O1** stock | true | true | true | true | true |
| **O2** un-patched | **false** | false | false | **false** | **false** |
| **O3** patched | true | false | false | **false** | true |
| **O4** composed | true | false | false | false | true |

---

## 4. Predicted reasons, which matter as much as the verdicts

- **O2 on `healthy` and `restored` is the headline.** Both are correct, working systems. A
  `false` here means the naive fix rejects correct repairs — actively harmful, not merely
  imperfect. Predicted cause: `AttributeError` on `expected_service_port`.
- **O2 on `M01` and `M02`** is predicted `false` but the *reason* is ambiguous and depends
  on the predicate ordering: the Service is absent (M01) or has no eligible endpoints
  (M02), so the oracle may return `false` before ever reaching `:65`. Record which. A
  `false` for the right reason is not evidence for C3.
- **O2 on `M03` is the sharpest test of C3 among the faulted states.** Under M03 the
  Service exists, has a ClusterIP, and one ready endpoint maps cleanly to the
  `user-service` Deployment — so the earlier predicates should pass and execution should
  reach `:65`. If C3 is right, this fails by `AttributeError`, not by connectivity.
- **O3 on `M03` is the most falsifiable prediction in the table.** It rests on the
  connectivity probe actually failing against `targetPort: 65535`. If O3 returns `true`
  here, the proposed patch does **not** catch a wrong-port repair and the PR must say so.

---

## 5. What would falsify what

1. **O2 `true` on `healthy` or `restored`** → `PR_PLAN.md` C3 is wrong; the naive swap is
   not harmful; the patch rationale needs rewriting.
2. **O3 `true` on any faulted state** → the fix is insufficient for that mutant; the
   README's fix claim must be narrowed to the mutants it does catch.
3. **O3 `false` on `healthy` or `restored`** → the fix is harmful *even patched*; the
   upstream PR must not be filed in its current form. This is the outcome that would most
   change what we do next, and it is the reason both controls are evaluated.
4. **O4 disagreeing with `O1 AND O3`** → `CompoundedOracle` does not behave as
   `compound.py:40-41` suggests, and the composition itself needs analysis.

---

## 6. Note added before execution — source reading, no prediction changed

The following was established by reading the pinned source **before** this document was
timestamped, and is recorded because the predicate ordering must be stated in advance
rather than inferred afterwards. **It contradicts nothing above, and no prediction in
§3 or §4 has been altered in light of it.**

`ServiceEndpointMitigationOracle.evaluate()` (`service_endpoint_mitigation.py:117-183`)
runs its predicates in this order:

| Line | Predicate | Returns `False` when |
|---|---|---|
| 125 | `get_deployment(user-service)` | — |
| 126-128 | desired replicas | scaled to zero |
| 129-132 | `_wait_for_current_rollout` (≤120 s) | rollout incomplete |
| 134-137 | `matchLabels` selector | absent |
| 139-145 | active ReplicaSets | none |
| 147-156 | pods matching selector + active RS | none |
| **158** | **`read_namespaced_endpoints(user-service)`** | — (raises on 404) |
| 166-168 | ready pod endpoints | none |
| 170-173 | unexpected pods in endpoints | any |
| **175 → 65** | **`_run_connectivity_probe()` reads `self.problem.expected_service_port` at `:65`** | probe fails |
| 178-180 | broad `except Exception` | anything raised above |

Two consequences, both already anticipated by `PR_PLAN.md` (`:197`, `:211`):

- The **Endpoints** read is at **`:158`** — `read_namespaced_endpoints`, the core/v1
  **Endpoints** object, *not* EndpointSlices.
- `expected_service_port` is first read at **`:65`**, reached only via **`:175`**, which is
  **after** every Deployment, pod and endpoint predicate. So a state that fails an earlier
  predicate never reaches the attribute at all.

This sharpens, without changing, the §4 expectation that M01 and M02 may fail for reasons
other than the `AttributeError`, and that M03 and the two controls are where `:65` is
reachable.

Module hashes at freeze time:

| File | sha256 |
|---|---|
| `oracles/service_endpoint_mitigation.py` | `ffff42394ff4f36ef49b6fe175c7fdca10827a85e6d2b02231fd8226b4a87424` |
| `oracles/compound.py` | `7ab495c99584900fc6515c230390bda59461cfdb8f50029e7278dc7976597ff8` |
| `oracles/mitigation.py` | `a087fd38399cfca4c2de764dbbab837d6100350ab9f71b89552cafe2c91fca8b` |
| `problems/missing_service.py` | `adf9a93aee8e016ca70d3aeb6270c94f5cc3bcf41aa36f00d538f6d6d9c8405f` |

`expected_service_port` is **verified absent** from `problems/missing_service.py` (grep
returns nothing) and **present** at `problems/wrong_service_selector.py:25`.

---

## 7. Procedure

Three runs — `fix-m01-r01`, `fix-m02-r01`, `fix-m03-r01` — one per mutant, in that order.
Per run:

1. Redeploy, pass the same healthy gate used by the nine-repetition matrix, capture the
   replica baseline and the live Service body.
2. Evaluate **O1, O2, O3, O4** on the **healthy** state.
3. Inject SREGym's own fault; apply the mutant; poll for structural activation.
4. Wait 60 s (rule R1), take the faulted workload window, evaluate the frozen contract and
   all four oracle configurations.
5. Restore through SREGym's own recovery routine; re-verify; evaluate all four again.
6. Write `experiments/<run-id>/fix-oracle-run.json`.

For every O2/O3/O4 evaluation the record additionally carries:

- `getattr(problem, "expected_service_port", "<ABSENT>")` observed **immediately before**
  the call, so the O2/O3 distinction is evidenced rather than asserted;
- a **direct, unguarded call** to `_run_connectivity_probe()` — the method that reads
  `:65` — recording the raised exception's type and message, or `null`. This is
  **diagnostic only**: it never influences a verdict, and every verdict comes from the
  unmodified `evaluate()`.

The attribute is set for O3/O4 and **deleted afterwards**; its absence is asserted and
recorded before every O2 call, so a later state cannot be contaminated by a leftover.

**A verdict that contradicts §3 is a result, not a bug.** No run is repeated, no driver is
changed mid-schedule, and no prediction is revised after the fact.

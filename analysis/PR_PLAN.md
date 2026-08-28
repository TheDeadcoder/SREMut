# PR plan — is `ServiceEndpointMitigationOracle` a viable fix for `missing_service`?

Read-only source analysis at pinned commit `ba07faf1a322f9b6d4a279643bb796aa2f36f64b`.
Target file: `sregym/conductor/oracles/service_endpoint_mitigation.py` (183 lines),
attached at `sregym/conductor/problems/wrong_service_selector.py:42`.

**Verdict up front: it does NOT work as-is. Two independent blockers, both fixable in a
patch of four added lines to `missing_service.py`. It must be composed with the generic
oracle, not substituted for it.**

---

## C1 — Constructor signature and where its arguments come from

`ServiceEndpointMitigationOracle` defines **no `__init__`**; it inherits
`Oracle.__init__(self, problem)` (`oracles/base.py:7-8`). It is constructed as
`ServiceEndpointMitigationOracle(problem=self)` (`wrong_service_selector.py:42`).

Everything it needs is pulled off the problem object at evaluate time:

| Attribute read | Where | Present on `MissingService`? |
|---|---|---|
| `problem.kubectl` | `:120` | **yes** (`missing_service.py:27`) |
| `problem.namespace` | `:121`, `:63` | **yes** (via `Problem.__init__`, `problems/base.py:11`) |
| `problem.faulty_service` | `:122`, `:64` | **yes** (`missing_service.py:15`) |
| `problem.expected_service_port` | **`:65`** | **NO — verified absent.** `grep -n 'expected_service_port' missing_service.py` returns nothing |

`wrong_service_selector.py:25` supplies it:
`self.expected_service_port = 9090 if app_name == "social_network" else app.frontend_port`.

---

## C2 — Complete `evaluate()` call graph and every predicate

Call graph: `evaluate` -> `_desired_replicas`, `_wait_for_rollout` -> `_rollout_complete`
-> `_desired_replicas`, `_pod_matches_selector`, `_owned_by_active_replica_set`,
`_run_connectivity_probe`.

Predicates in order, each returning `{"success": False}` on failure:

| # | Line | Predicate |
|---|---|---|
| 1 | 125-128 | target Deployment exists and `spec.replicas >= 1` |
| 2 | 129-132 | its current rollout completes within 120 s (`observed_generation >= generation`, `updated`/`ready`/`available == desired`, `unavailable == 0`) |
| 3 | 134-137 | Deployment has a non-empty `spec.selector.matchLabels` |
| 4 | 139-145 | at least one ReplicaSet with `spec.replicas > 0` |
| 5 | 147-156 | at least one non-terminating pod matching the selector AND owned by an active ReplicaSet |
| 6 | **158-168** | **`read_namespaced_endpoints(service)` returns at least one ready address with a Pod `target_ref`** |
| 7 | 170-173 | the ready endpoint pods are a **subset** of the expected pods (catches wrong-selector) |
| 8 | 175-177 | **live connectivity probe**: a `busybox:1.36` pod runs `nc -z -w 5 <svc>.<ns>.svc.cluster.local <port>` and must print `SERVICE_OK` (`:62-115`) |

Predicates 6, 7 and 8 are exactly the Service-layer coverage the generic oracle lacks.

---

## C3 — CRITICAL: what happens when the Service does not exist

Traced for the `missing_service` fault, which deletes the Service but leaves the
Deployment and pods running (`inject_virtual.py:295-307`).

1. `:125` `get_deployment("user-service")` — **succeeds**. `KubeCtl.get_deployment`
   (`kubectl.py:111-113`) is an unguarded `read_namespaced_deployment`; the Deployment is
   untouched by this fault, so no 404.
2. `:126-156` predicates 1-5 — **all pass**. The Deployment is healthy, has a selector, an
   active ReplicaSet, and matching pods. This is precisely the state the generic oracle
   also passes.
3. `:158` `read_namespaced_endpoints("user-service", ns)` — **raises `ApiException` (404)**.
   Deleting a Service deletes its Endpoints/EndpointSlice objects with it (confirmed
   empirically: `g1-run-0{1,2,3}` sampler shows `user_service_endpointslice_count == 0`
   for the whole faulted window).
4. That exception is caught by the **broad** `except Exception as e` at `:178-180`, which
   prints `❌ Error retrieving endpoints for service user-service: (404) ...` and returns
   `{"success": False}`.

**So it does not raise and does not crash — it returns `{"success": False}`, which is the
correct verdict.** But it reaches it through a generic error handler, not an explicit
check, and the operator-facing message says "Error retrieving endpoints" rather than
"Service does not exist". This path was almost certainly never exercised: the problem it
is attached to (`wrong_service_selector`) leaves the Service present with a bad selector,
so predicate 6 finds an Endpoints object with zero ready addresses and fails at `:166-168`
instead.

### The second, worse consequence — a false negative on a correct repair

If an agent **correctly recreates the Service**, the flow now reaches predicate 8 at
`:175`, which calls `_run_connectivity_probe()`, which reads
`self.problem.expected_service_port` at **`:65`**. On `MissingService` that attribute does
not exist, so it raises `AttributeError` — caught by the same broad handler at `:178` —
and returns `{"success": False}`.

**A correct repair would be rejected.** This is the blocker that makes a naive swap
actively harmful rather than merely imperfect.

---

## C4 — Verdict on swapping it in at `missing_service.py:40`

**Does not work as-is.** Two independent blockers:

1. **Missing attribute.** `expected_service_port` is undefined on `MissingService`, so
   every correct repair is rejected via `AttributeError` (C3).
2. **Coverage loss.** It is not a superset of the generic oracle (C6). Substituting it
   would trade one blind spot for another.

Both are fixable without touching either oracle.

---

## C5 — The minimal patch

Do **not** write a new `MissingServiceMitigationOracle`, and do **not** modify
`service_endpoint_mitigation.py`. Use SREGym's own composition mechanism, which already
ANDs child verdicts (`compound.py:40-41`) and fans out `capture_baseline()` to children
(`compound.py:21-23`) — the latter matters, because `MitigationOracle` needs its baseline
captured at `conductor.py:227`.

### Patch to `sregym/conductor/problems/missing_service.py` — 4 added lines, 1 changed

```diff
--- a/sregym/conductor/problems/missing_service.py
+++ b/sregym/conductor/problems/missing_service.py
@@
 from sregym.conductor.oracles.llm_as_a_judge.llm_as_a_judge_oracle import LLMAsAJudgeOracle
 from sregym.conductor.oracles.mitigation import MitigationOracle
+from sregym.conductor.oracles.compound import CompoundedOracle
+from sregym.conductor.oracles.service_endpoint_mitigation import ServiceEndpointMitigationOracle
 from sregym.conductor.problems.base import Problem
@@ class MissingService(Problem):
         self.app_name = app_name
         self.faulty_service = faulty_service
+        # Port the Service is expected to serve on once restored; consumed by
+        # ServiceEndpointMitigationOracle._run_connectivity_probe (service_endpoint_mitigation.py:65).
+        self.expected_service_port = 9090 if app_name == "social_network" else app.frontend_port
@@
         self.app.create_workload()
-        self.mitigation_oracle = MitigationOracle(problem=self)
+        self.mitigation_oracle = CompoundedOracle(
+            self,
+            MitigationOracle(problem=self),
+            ServiceEndpointMitigationOracle(problem=self),
+        )
```

Note the `expected_service_port` line must be placed **after** `app` is assigned
(`missing_service.py:17-24`), since it reads `app.frontend_port` on the non-social-network
branch. The expression is copied verbatim from `wrong_service_selector.py:25` so the two
problems stay consistent.

### Optional second patch — clearer diagnostics, 5 added lines

Independent of the above, and strictly an improvement to the message rather than the
verdict:

```diff
--- a/sregym/conductor/oracles/service_endpoint_mitigation.py
+++ b/sregym/conductor/oracles/service_endpoint_mitigation.py
@@ def evaluate(self) -> dict:
         try:
+            try:
+                kubectl.core_v1_api.read_namespaced_service(service_name, namespace)
+            except ApiException as exc:
+                if exc.status == 404:
+                    print(f"❌ Service {service_name} does not exist")
+                    return {"success": False}
+                raise
             deployment = kubectl.get_deployment(service_name, namespace)
```

Without it the oracle is still **correct** for a deleted Service (it returns `False`); the
patch only replaces a generic "Error retrieving endpoints" message with an accurate one.
Recommend including it, since a benchmark's failure messages are read by humans debugging
agent runs.

---

## C6 — Does it replicate the generic oracle's Deployment/Pod checks? No — hence composition

| Generic `MitigationOracle` check | Line | Present in `ServiceEndpointMitigationOracle`? |
|---|---|---|
| every baseline Deployment still exists | `mitigation.py:70-73` | **NO** — only the one `faulty_service` Deployment is read (`:125`) |
| no baseline Deployment scaled to 0 | `mitigation.py:76-79` | partial — only for `faulty_service` (`:126`) |
| every baseline Deployment `ready >= desired` | `mitigation.py:81-84` | partial — only for `faulty_service` (`:129-132`) |
| namespace has at least one pod | `mitigation.py:88-91` | **NO** |
| **every pod in the namespace is `Running`** | `mitigation.py:95-99` | **NO** — only selector-matching pods are considered (`:147-153`), and their phase is never checked |
| every container ready / not waiting / not terminated | `mitigation.py:101-112` | **NO** |

So it is **not** a superset: substituting it would lose the namespace-wide health sweep
and the all-deployments baseline comparison. Composing preserves both — `CompoundedOracle`
fails if **either** child fails (`compound.py:40-41`), so the result is
`generic AND service-layer`, which is strictly stronger than today.

### What the composed oracle would have returned in our experiments

Against the `g1`/`g3`/`w1` faulted states — Service absent, 0 EndpointSlices, all
deployments and pods healthy — the generic child returns `True` (as measured, in every
faulted-state instrument reading of those runs) and
`ServiceEndpointMitigationOracle` returns `False` at `:158-180` (Endpoints 404). The
compound verdict is therefore **`False`**, which is the correct answer.

**Prediction, not a result.** This has not been executed; validating it would require
patching `SREGym/`, which is out of scope for this study.

---

## Summary

| Question | Answer |
|---|---|
| C1 needs | `kubectl`, `namespace`, `faulty_service`, **`expected_service_port` (absent on `MissingService`)** |
| C2 predicates | 8, of which 6-8 are the Service-layer coverage the generic oracle lacks |
| C3 Service absent | returns `{"success": False}` via the broad handler at `:178` — correct verdict, misleading message; path likely never exercised |
| C4 swap as-is | **No.** Missing attribute rejects correct repairs; also loses coverage |
| C5 minimal fix | 4 added lines + 1 changed line in `missing_service.py`; optional 5-line diagnostic patch to the oracle |
| C6 superset? | **No** — must be composed with `MitigationOracle`, not substituted |

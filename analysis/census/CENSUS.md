# C1 — Census of verifier adequacy across SREGym problems

Pinned commit `ba07faf1a322f9b6d4a279643bb796aa2f36f64b` (2026-08-06).
Read-only source analysis. Every cell in the accompanying CSVs carries file:line.

Companion files: `oracle-surfaces.csv` (Phase 1), `problem-oracle.csv` (Phase 2),
`problem-fault.csv` (Phase 3), `coverage.csv` (Phase 4), `secondary-defects.md`.

---

## Denominator

**This census uses 123.** That is the number of unique `problem_id` keys in
`PROBLEM_REGISTRY` (`registry.py:124-356`), obtained from the **runtime registry**
(`ProblemRegistry().PROBLEM_REGISTRY`), not from a pattern match over source.

> **CORRECTION — the denominator was previously wrong (118).** The prior census parsed
> registry keys with `r'"([a-z0-9_]+)":'`, a character class that excludes `-`. The five
> hyphenated IDs — `k8s_target_port-misconfig`, `revoke_auth_mongodb-1`,
> `revoke_auth_mongodb-2`, `storage_user_unregistered-1`, `storage_user_unregistered-2`
> (`registry.py:136-139,164`) — never matched and were silently dropped. All five are
> ordinary literal dict keys; nothing is added programmatically. AST and runtime both
> return 123, and `generate_census.py` now fails loudly if the census and the registry
> are not set-equal (`tests/test_census_completeness.py`).

**Secondary, conservative denominator: 104** — the intersection of the registry (123)
with `Problem List.md` (116 data rows). The previously published value, 99, was computed
against the under-parsed 118 and is superseded.

### Which subset were the paper's mitigation rates computed over?

**The evaluated set is selected by `tasklist.yml`.** `get_problem_ids()` reads
`sregym/conductor/tasklist.yml` and returns `list(tasklist["all"]["problems"])`
(`registry.py:388-390`); only when that file is absent does it fall back to the whole
registry (`registry.py:384-386`). **That file is absent from the repository at this
commit** — only `tasklist.yml.example` (87 keys) ships. The selector for the paper's
evaluated subset is therefore a file that was not published, and **the full registry is
the only reproducible denominator.**

Two other lists exist and neither is the evaluated set:
`tests/e2e-testing-scripts/registry.txt` (91 entries, consumed by `evaluation.py:163`
and `automating_tests.py:163`) and `docs/SREGym-Lite.md` (a curated 21-problem starter
set, `SREGym-Lite.md:3`).

> **Footnote — registry/docs drift.** `PROBLEM_REGISTRY` has 123 IDs, `Problem List.md`
> has 116 rows, and their overlap is 104: 12 IDs appear only in the documentation table
> and 19 only in the registry. Of `registry.txt`'s 91 entries, **4** are genuinely absent
> from the registry (`astronomy_shop_recommendation_service_cache_failure`,
> `kafka_queue_problems_hotel_reservation`, `read_error`,
> `social_net_hotel_res_astro_shop_concurrent_failures`). Ordinary drift; recorded once.

> **CORRECTION — a fabricated claim, distinct from the parser defect.** The prior
> version of this footnote stated that `k8s_target_port-misconfig` appears only in the
> documentation and was *"not even a valid registry key form"*. **That claim is false.**
> A Python dict key may contain a hyphen; `k8s_target_port-misconfig` is a literal key at
> `registry.py:164` and is present in the runtime registry. The prior version also
> asserted that `k8s_target_port-misconfig`, `revoke_auth_mongodb-1` and
> `storage_user_unregistered-2` were in `registry.txt` but absent from
> `PROBLEM_REGISTRY`; all three are present.
>
> These are **two separate errors**. The first was a parser defect: an over-narrow
> character class silently dropped five rows. The second was a **confabulation**: having
> observed a discrepancy produced by its own parser, the analysis invented a property of
> SREGym — an invalid key form — to explain it, and published that invention as an
> observation about someone else's code. The parser defect caused an undercount; the
> confabulation manufactured a false finding about the artefact under study, which is the
> more serious of the two. The discrepancy was never investigated by checking the
> registry directly, which would have taken one command.

## Method

Observation surfaces were determined by walking each oracle's **`evaluate()` call
graph** — `evaluate()` plus every `self.*` helper it transitively calls — and citing
line numbers inside that graph. Class names and file-wide greps were not used to infer
behaviour. Two determinations that a name-based approach gets wrong:

- `KubeCtl.exec_command` is a **host kubectl subprocess** (`service/kubectl.py:719`,
  `subprocess.run(command, shell=True)`), not a pod exec. `connect_get_namespaced_pod_exec`
  and `stream(` are **verified absent from `kubectl.py` entirely**. Only 4 oracles do
  genuine pod exec, identified by a command string beginning `kubectl exec`:
  `alert_oracle.py:51`, `valkey_auth_mitigation.py:37,:44`,
  `feature_flag_http_probe_mitigation.py:58`, `secret_rotation_stale_env_mitigation.py:165`.
- Two of the six `MitigationOracle` subclasses do **not** inherit the parent pod sweep:
  `CpuThrottlingMitigationOracle` (`:81-113`) and
  `KubeletEvictionThresholdMisconfigMitigationOracle` (`:100-118`) override `evaluate()`
  without calling `super().evaluate()`, and neither calls `list_pods` anywhere in its
  call graph. The other four do (`fd_exhaustion.py:28`, `kafka_producer_leak:9`,
  `nightly_rebalance_oom:27`, `conntrack:106`).

### Classification rule (fixed before counting, corrected once — see below)

- **ADEQUATE** — the oracle observes the perturbed resource kind, or holds a functional
  signal (DNS, TCP/HTTP, workload, Prometheus, pod exec) the fault necessarily breaks.
- **BLIND** — neither.
- **UNCERTAIN** — the perturbed kind or the oracle surface could not be determined from
  source at the first-pass depth applied to every row.

The first pass was applied uniformly, but the census as it now stands is **not** verified
to one uniform depth: 28 rows were subsequently re-read against injector source and
oracle `evaluate()`, and 95 were not. See "Verification coverage".

**One correction, disclosed.** The first pass counted the injector's incidental pod
deletion as an observable perturbation, which wrongly classified `missing_service_*` as
ADEQUATE — a case independently proven BLIND end-to-end in
`SREMut/experiments/g1-run-0{1,2,3}`. The rule was corrected: when an injector deletes
pods **and then waits for stability**, the pod churn is transient and self-healing, so
it is not a persistent fault signal and does not make an oracle adequate. Having a
known-answer case in the census caught this; the corrected rule reproduces it.

---

## Headline counts

### THE headline: oracles blind to their own fault

**Source analysis identified at least 8 structurally blind problem IDs among 123
registered IDs** — for each, the attached oracle observes neither the perturbed resource
kind nor any functional signal the fault would break. Against the conservative 104-id
denominator the same 8 give 7.7 %.

**8 of 123 = 6.5041 % is the fraction currently identified**, not an estimate of
benchmark-wide prevalence. The 8 are a lower-bound discovery count produced by
hand-verification of a non-random subset; the percentage must never be quoted as a
prevalence estimate.

> **This figure rose from 6 to 8 on 2026-08-28** (R2 Part A), by hand-verifying rows the
> census had scored ADEQUATE on a resource kind attributed from prose rather than code.
> `auth_miss_mongodb` and `operator_wrong_operator_image` were both credited to oracles
> that demonstrably cannot see their fault. Derivation:
> `analysis/census/R2_PARTA_CODE_ONLY_RECLASSIFICATION.md`. **8 is a lower bound, not a
> point estimate** — see "Verification coverage" below.

**3 of the 8 are the `missing_service` family**, and that family is the one confirmed
end-to-end: across `experiments/g02-run-01`, `g1-run-0{1,2,3}`, `g3-run-01`,
`w1-delay0-0{1,2}`, `w2-noise-0{1,2}` and `w4-hotel-01` the stock oracle returned
`{"success": true}` on an unrepaired system in **18 of 18 faulted-state instrument
readings across the 10 included historical runs** (21 of 21 including the three excluded
runs), while the functional workload failed ~10 % of requests on social-network and ~60 %
on hotel-reservation. Those 18 readings are **not** 18 independent repetitions — the run
is the experimental unit, and two instruments in one run read one cluster state. Per-run
figures are generated into `experiments/RESULT_LEDGER.json`.

A fourth, `auth_miss_mongodb`, is a **second instance of the same mechanism**, established
from source — see "Second instance of the confirmed mechanism" below.

The remaining BLIND ids (`pvc_claim_mismatch`, `assign_to_non_existent_node`,
`workload_imbalance`, `operator_wrong_operator_image`) are **structural predictions, not
results** — none has been executed.

### The bare-generic subset, stated as an interval

Of the 31 problem_ids on the bare generic `MitigationOracle`:

| Verdict | Count |
|---|---:|
| BLIND | **6** |
| ADEQUATE | **17** |
| UNCERTAIN | **8** |

Because the 8 UNCERTAIN rows could not be resolved from source, resolving them in either
direction gives a mechanical range **within this subset** of **6 to 14**: 6 if every
UNCERTAIN row turns out ADEQUATE, 14 if every one turns out BLIND.

**That range is scoped to the bare generic subset and to nothing wider.** It is not a
global range over the 123 registered IDs, and it must not be restated as one: the other
two BLIND rows carry dedicated oracles and sit outside this subset entirely, and the 24
UNCERTAIN rows outside it are not covered by either endpoint.

### Verification coverage

This is a first-class number, not a caveat. It bounds every count above.

| | rows |
|---|---:|
| problem_ids in the census | **123** |
| **hand-verified against injector source and oracle `evaluate()`** | **28** |
| carrying at least one resource kind attributed from **prose rather than code** | **72** |

The 28 hand-verified rows are: the 6 rows originally classified BLIND; the 5 rows added
in R1 when the denominator was corrected from 118 to 123; and the 17 rows audited in
R2 Part A3. **The three sets are pairwise disjoint** — no row appears in more than one —
so the union is exactly 6 + 5 + 17 = 28.

**The 17 are an enriched sample, not a random one.** They were selected precisely because
a code-identifiers-only re-derivation disagreed with the full-text attribution — that is,
because prose was doing the attribution work. A 2-in-17 defect rate found in an enriched
sample cannot be extrapolated to the 95 unverified rows, in either direction.

**The 123 rows were not verified at the same depth.** 28 received deeper
injector-and-oracle verification; 95 did not. Any statement that the whole census was
verified to one uniform standard is false and is withdrawn.

Therefore the headline is stated as a bound:

> **At least 8 of 123 problem_ids are blind to their own fault, from 28 rows
> hand-verified.**

The 95 unverified rows were scored ADEQUATE or UNCERTAIN without individual hand-checking
of the kind attribution. `METHOD_AUDIT.md` section 4 records why that column is the
weakest in the census; R2 Part A quantified the exposure at 72 of 123 rows.

### Structural shape only — NOT a blindness count

**CORRECTION (recorded).** An earlier draft of this section presented the 8 rows below as
"8 problem_ids sharing the structure confirmed end-to-end". That was wrong and is
corrected here. Sharing the *injector* half of the structure is not sufficient for
blindness. The confirmed structure requires **both** conditions:

1. the injector restores the surface the oracle measures (restarts pods / waits for
   stability), **and**
2. the perturbed resource kind is invisible to that oracle.

Only the three `missing_service_*` ids satisfy both. The other five perturb Deployment,
container command/env, or Node — kinds a deployments-and-pods oracle **can** observe — and
are correctly classified ADEQUATE. The table is retained because condition (1) is a real
and reusable structural signal, but it is **not** a blindness count.

| problem_id | verdict | perturbed kinds | restarts pods | waits stability | why this verdict |
|---|---|---|---|---|---|
| `pod_cidr_exhaustion_hotel_reservation` | **ADEQUATE** | Deployment;Pod;container_cmd_env | True | True | the perturbed kind (Deployment;Pod;container_cmd_env) IS observable through the Deployment/Pod surface |
| `sidecar_port_conflict_astronomy_shop` | **ADEQUATE** | Deployment;container_cmd_env | False | True | the perturbed kind (Deployment;container_cmd_env) IS observable through the Deployment/Pod surface |
| `sidecar_port_conflict_hotel_reservation` | **ADEQUATE** | Deployment;container_cmd_env | False | True | the perturbed kind (Deployment;container_cmd_env) IS observable through the Deployment/Pod surface |
| `sidecar_port_conflict_social_network` | **ADEQUATE** | Deployment;container_cmd_env | False | True | the perturbed kind (Deployment;container_cmd_env) IS observable through the Deployment/Pod surface |
| `taint_no_toleration_social_network` | **ADEQUATE** | Deployment;Pod;Node | False | True | the perturbed kind (Deployment;Pod;Node) IS observable through the Deployment/Pod surface |
| `missing_service_astronomy_shop` | **BLIND** | Pod;Service | True | True | Service deletion is invisible to a deployments+pods oracle |
| `missing_service_hotel_reservation` | **BLIND** | Pod;Service | True | True | Service deletion is invisible to a deployments+pods oracle |
| `missing_service_social_network` | **BLIND** | Pod;Service | True | True | Service deletion is invisible to a deployments+pods oracle |

**3 of 8 BLIND, 5 of 8 ADEQUATE.** All eight are on the bare generic `MitigationOracle`.
No underlying classification in `coverage.csv` was changed by this correction; it was
re-verified row by row and found correct. This is a framing fix only.

### Other headline counts

**Counts** below are generated from `ledger.json` by `generate_census.py`; do not edit
them by hand. **Verdicts** are not: the ADEQUATE / BLIND / UNCERTAIN column in
`coverage.csv` was **hand-read from each oracle's `evaluate()`**, per the C1 method rule,
and `generate_census.py` only tallies it.

No mechanical rule reproduces those verdicts. A reconstructed classifier — functional
signal first, then empty kind set to UNCERTAIN, then intersect the perturbed kinds with
what the oracle reads — run on the published `perturbed_kinds` column **disagrees with
the published verdict on 48 of 123 rows** (R2 Part A2). `oracle_other_kinds` holds free
prose rather than a controlled kind vocabulary, and `perturbed_kinds` sometimes holds the
literal string `UNCERTAIN`. The kind column informed the reading; it did not determine it.

| Quantity | Count |
|---|---:|
| problem_ids (denominator) | **123** |
| attaching the bare generic `MitigationOracle` | 31 |
| ... of those, BLIND | 6 |
| ... of those, ADEQUATE | 17 |
| ... of those, UNCERTAIN | 8 |
| BLIND across **all** oracle types | **8** (6.504 %) |
| ADEQUATE across all oracle types | 83 |
| UNCERTAIN across all oracle types | 32 |
| injectors that restart pods or wait for stability | 16 |
| oracles whose surface is pods+deployments only | 38 |
| structural-shape rows | 8 (6.5041 %) |

The 6.5041 % in that table is **8 / 123 — the fraction currently identified**, on rows
verified at unequal depth (28 deep, 95 not). It is not an estimated benchmark-wide
prevalence and carries no such interpretation.

### BLIND by oracle kind

| Oracle kind | BLIND |
|---|---:|
| BARE_GENERIC | 6 |
| DEDICATED | 2 |

### BLIND by perturbed resource kind

| Perturbed kind | BLIND problem_ids touching it |
|---|---:|
| Pod | 4 |
| Service | 3 |
| Deployment | 2 |
| container_cmd_env | 2 |
| Node | 1 |
| PVC/PV | 1 |

---

## Every BLIND classification, with justification

### `assign_to_non_existent_node`

- Oracle: `AssignNonExistentNodeMitigationOracle` (DEDICATED), attached at `sregym/conductor/problems/assign_non_existent_node.py:33`
- Observation surface: pods
- Perturbed kinds: Deployment;Node (injector `VirtualizationFaultInjector`, fault_type `assign_to_non_existent_node`)
- Injector restarts pods: False; waits for stability: False
- **Justification:** observes {pods} but the fault mutates Deployment (would need deployments); Node (would need node)

### `auth_miss_mongodb`

Added 2026-08-28 (R2 Part A3), correcting a prior ADEQUATE.

- **Oracle:** bare generic `MitigationOracle`, attached unsubclassed
  (`auth_miss_mongodb.py:30`).
- **Perturbed:** `url-shorten-mongodb` TLS mode forced to `requireTLS` via `Helm.upgrade`
  (`inject_virtual.py:74-88`); each client Deployment scaled to 0
  (`inject_virtual.py:91`) and **back to 1** (`inject_virtual.py:93`).
- **Why BLIND:** the injector restores the replica count itself, so the Deployment
  predicates (`mitigation.py:69-84`) compare equal to baseline. The socialNetwork chart
  declares no probes of any kind (verified absent — see "The socialNetwork chart defines
  no health probes at all"), so the pod sweep (`mitigation.py:95-112`) sees Ready for any
  running process. `url-shorten-service` retries index creation forever
  (`UrlShortenService.cpp:84-93`, `while (!r) { ...; sleep(1); }`) and never reaches
  `server.serve()` (`:111`).
- **Status:** structural prediction, not executed. Full chain: "Second instance of the
  confirmed mechanism".

### `missing_service_astronomy_shop`

- Oracle: `MitigationOracle` (BARE_GENERIC), attached at `sregym/conductor/problems/missing_service.py:40`
- Observation surface: deployments;pods
- Perturbed kinds: Pod;Service (injector `VirtualizationFaultInjector`, fault_type `missing_service`)
- Injector restarts pods: True; waits for stability: True
- **Justification:** observes {deployments,pods} but the fault mutates Service (would need endpoints_or_endpointslices/ports_or_targetports/services) [Pod churn excluded: injector deletes pods then waits for stability, so the pod surface is restored before evaluation]

### `missing_service_hotel_reservation`

- Oracle: `MitigationOracle` (BARE_GENERIC), attached at `sregym/conductor/problems/missing_service.py:40`
- Observation surface: deployments;pods
- Perturbed kinds: Pod;Service (injector `VirtualizationFaultInjector`, fault_type `missing_service`)
- Injector restarts pods: True; waits for stability: True
- **Justification:** observes {deployments,pods} but the fault mutates Service (would need endpoints_or_endpointslices/ports_or_targetports/services) [Pod churn excluded: injector deletes pods then waits for stability, so the pod surface is restored before evaluation]

### `missing_service_social_network`

- Oracle: `MitigationOracle` (BARE_GENERIC), attached at `sregym/conductor/problems/missing_service.py:40`
- Observation surface: deployments;pods
- Perturbed kinds: Pod;Service (injector `VirtualizationFaultInjector`, fault_type `missing_service`)
- Injector restarts pods: True; waits for stability: True
- **Justification:** observes {deployments,pods} but the fault mutates Service (would need endpoints_or_endpointslices/ports_or_targetports/services) [Pod churn excluded: injector deletes pods then waits for stability, so the pod surface is restored before evaluation]

### `operator_wrong_operator_image`

Added 2026-08-28 (R2 Part A3), correcting a prior ADEQUATE. **Namespace mismatch.**

- **Oracle:** bare generic `MitigationOracle` (`wrong_operator_image.py:31`), reading
  Deployments (`mitigation.py:66`) and Pods (`mitigation.py:86`) in
  `self.problem.namespace` (`mitigation.py:59`) — which is `tidb-cluster`
  (`wrong_operator_image.py:16`).
- **Perturbed:** a **Pod in namespace `tidb-operator`**. The injector reads the operator
  pod name (`inject_operator.py:280-281`) and applies a patch whose `metadata.namespace`
  is the literal `"tidb-operator"` (`inject_operator.py:292`), setting the image to
  `pingcap/tidb-operatorr:v1.6.3` — a deliberate typo (`inject_operator.py:297`) —
  producing ImagePullBackOff.
- **Why BLIND:** the broken object is in a namespace the oracle never queries. The running
  TiDB cluster pods in `tidb-cluster` are untouched, so the oracle sees a fully healthy
  namespace while the controller is in ImagePullBackOff. The failure is not subtle; the
  oracle misses it for a purely structural reason.
- **Status:** structural prediction, not executed.

### `pvc_claim_mismatch`

- Oracle: `MitigationOracle` (BARE_GENERIC), attached at `sregym/conductor/problems/pvc_claim_mismatch.py:43`
- Observation surface: deployments;pods
- Perturbed kinds: PVC/PV (injector `VirtualizationFaultInjector`, fault_type `inline`)
- Injector restarts pods: False; waits for stability: False
- **Justification:** observes {deployments,pods} but the fault mutates PVC/PV (would need PVC)

### `workload_imbalance`

- Oracle: `ImbalanceMitigationOracle` (DEDICATED), attached at `sregym/conductor/problems/workload_imbalance.py:31`
- Observation surface: pods; other: pod CPU metrics (kubectl top)
- Perturbed kinds: container_cmd_env (injector `VirtualizationFaultInjector`, fault_type `inline`)
- Injector restarts pods: False; waits for stability: True
- **Justification:** observes {pods} but the fault mutates container_cmd_env (would need deployments)

### What each BLIND oracle would have to observe

| problem_id | to detect the fault the oracle would need |
|---|---|
| `missing_service_social_network` | a Service read (`read_namespaced_service`), an EndpointSlice/Endpoints read for the Service, DNS resolution of the Service FQDN, or any functional request routed through it. `mitigation.py` does none of these (verified absent, G0.1 §A.4). |
| `missing_service_hotel_reservation` | same as above |
| `missing_service_astronomy_shop` | same as above |
| `pvc_claim_mismatch` | a PersistentVolumeClaim read (bind status / claimRef), or a functional check that data persists. The oracle reads only Deployments and Pods. |
| `assign_to_non_existent_node` | a Deployment `spec.template.spec.nodeSelector`/`nodeName` read, or Node existence. `AssignNonExistentNodeMitigationOracle` reads only pods (`:16`) and checks a pod with the service-name prefix exists (`:20`) — a stale healthy pod satisfies it. |
| `workload_imbalance` | the container command/env the injector mutates. The oracle reads only pod CPU via `kubectl top` (`imbalance_mitigation.py:25`), a derived metric that can equalise while the misconfiguration remains. |

---

## Second instance of the confirmed mechanism — `auth_miss_mongodb`

The false acceptance confirmed end-to-end for `missing_service` is not a one-off shape.
`auth_miss_mongodb` reproduces it exactly, and the whole chain is establishable from
source without running anything.

**The mechanism, in four links:**

1. **The injector restores the replica count itself.** `inject_auth_miss_mongodb`
   forces `url-shorten-mongodb.tls.mode: requireTLS` through `Helm.upgrade`
   (`inject_virtual.py:74-88`), then scales each client Deployment to 0
   (`inject_virtual.py:91`) and **back to 1** (`inject_virtual.py:93`). The oracle's three
   Deployment predicates (`mitigation.py:69-84`) therefore compare equal to the captured
   baseline. The fault is invisible on the replica surface *by the injector's own action*.

2. **Pod readiness carries no application signal.** No `readinessProbe`, `livenessProbe`
   or `startupProbe` exists anywhere in the socialNetwork chart — see the next section.
   With no probe, the kubelet marks a container Ready as soon as its process is running.
   The oracle's pod sweep (`mitigation.py:95-112`) is therefore satisfied.

3. **The client never exits and never serves.** `url-shorten-service` cannot reach a
   `requireTLS` mongod. `UrlShortenService.cpp:84-93` retries index creation in
   `while (!r) { r = CreateIndex(...); if (!r) { LOG(error) ...; sleep(1); } }` — an
   **unbounded** loop with no attempt limit and no exit path. The process never reaches
   `server.serve()` at `UrlShortenService.cpp:111`. It stays Running, stays Ready, and
   answers nothing.

4. **The oracle is the bare generic one.** `MitigationOracle` is attached unsubclassed
   (`auth_miss_mongodb.py:30`) and reads only Deployments (`mitigation.py:66`) and Pods
   (`mitigation.py:86`) in `self.problem.namespace` (`mitigation.py:59`).

**Consequence.** Replica counts at baseline, every pod Ready, the URL-shorten path dead.
This is the same false-acceptance shape measured 18 times for `missing_service`, arrived
at by a different fault and a different code path. It is a **structural prediction**, not
an executed result — `auth_miss_mongodb` has not been run — and it is labelled as such
throughout.

## The socialNetwork chart defines no health probes at all

**Proving command, which returns nothing:**

```
$ grep -rn 'readinessProbe\|livenessProbe\|startupProbe' \
      SREGym-applications/socialNetwork/helm-chart/socialnetwork/templates/
$
```

The directory contains the only Deployment templates the chart renders
(`_baseDeployment.tpl`, `_baseNginxDeployment.tpl`). The single `readinessProbe` key
anywhere in the chart is in `values.yaml:64-65`, belonging to the vendored upstream
`redis` subchart, and it is set to `enabled: false`.

**Independent corroboration from captured live cluster state.** The healthy baseline
capture `baselines/missing_service_social_network/run-03/` records the running cluster,
not the chart:

| source | objects | containers | `readinessProbe` | `livenessProbe` | `startupProbe` |
|---|---:|---:|---:|---:|---:|
| `run-03/deployments.json` | 27 | 27 | **0** | **0** | **0** |
| `run-03/pods.json` | 28 | 28 | **0** | **0** | **0** |

Not one container in the deployed application declares a probe of any kind.

**Consequence, stated plainly.** For every social-network problem judged by the bare
generic `MitigationOracle`, the Pod-readiness half of the verdict
(`mitigation.py:95-112`) distinguishes only whether a container **started** — never
whether it **serves**. Combined with the Deployment half, which reads only replica counts
(`mitigation.py:69-84`), the oracle's entire observable surface for these problems is
"the right number of processes are running." No signal in that surface can distinguish a
working application from a started-but-dead one.

This is a property of the benchmark's applications interacting with the oracle's design,
not of any single fault, and it is why `missing_service` and `auth_miss_mongodb` fail the
same way through different mechanisms.

## Oracles that a name-based reading would have got wrong

Seven oracles produced no or misleading observation signals in an earlier grep-derived
seed list and were re-read in full from their `evaluate()` call graphs. Six turn out to
be ADEQUATE and one BLIND — but in **no case** was the seed list's silence the reason:

| Oracle | Actually observes | Verdict for its problem_ids |
|---|---|---|
| `TargetPortMisconfigMitigationOracle` | Service via `get_service_json` (`:17`), **`spec.ports[0].targetPort`** compared to 9090 (`:18-19`), pods (`:23-44`) | ADEQUATE — the injector `inject_misconfig_k8s` (`inject_virtual.py:38-48`) patches exactly that field, 9090 -> 9999 |
| `IncorrectImageMitigationOracle` | Deployment `container.image` (`:25-27`) compared at `:29` | ADEQUATE |
| `ImbalanceMitigationOracle` | pod CPU via `kubectl top pod` (`:25` -> `kubectl.py:911`) | **BLIND** — a derived metric, not the mutated container spec |
| `WrongBinMitigationOracle` | Deployment `container.command` (`:20-25`) | ADEQUATE |
| `ExpiredTlsMitigationOracle` | Ingress TLS certificate expiry (`:33-38`) | ADEQUATE |
| `FDMitigationOracle` | inherited deployments+pods (`:28`) plus pod logs for "too many open files" (`:22-24`, `:36`) | ADEQUATE |
| `IngressMisrouteMitigationOracle` | Ingress `path.backend.service.name` (`:17`, `:23`) | ADEQUATE |

The point generalises: **BLIND does not correlate with the bare generic oracle.** Of
the 8 BLIND ids, 2 carry a *dedicated* oracle written for that fault. Conversely
17 of the 31 bare-generic ids are ADEQUATE, because for those faults the
generic Deployment/Pod surface does contain the perturbation (scale-to-zero, image
faults, crash-looping containers). All 123 were classified against the same rule, but not
at the same verification depth: 28 rows were re-read against injector source and oracle
`evaluate()` and 95 were not.

---

## Limits of this census, stated plainly

**32 of 123 are UNCERTAIN**, almost all because the perturbed resource kind is
not determinable from the problem class: the injection is delegated to Khaos, to
`inject_tt.py`, or to a kernel/hardware injector whose concrete mutation was not traced
to file:line at the first-pass depth applied to every row. These are UNCERTAIN, not
PENDING — the work was attempted and did not resolve. It is not a claim that all 123 rows
were verified to one depth; 28 were re-read more deeply and 95 were not.

Nothing in this census is marked PENDING: the join is complete for all 123 rows.

Further specific limits:

- Adequacy here is **structural**, from source. A verdict of ADEQUATE means the oracle
  observes the perturbed kind or a functional signal; it does **not** mean the oracle
  has been shown to reject a non-repair. Two problem_ids have been tested end-to-end:
  `missing_service_social_network` (`experiments/g02-run-01`, `g1-run-0{1,2,3}`,
  `g3-run-01`, `w1-delay0-0{1,2}`, `w2-noise-0{1,2}`) and `missing_service_hotel_reservation`
  (`experiments/w4-hotel-01`) — two applications, 10 included historical runs, 18
  faulted-state instrument readings.
- BLIND means the oracle cannot see the fault through the surfaces it reads. Whether an
  agent could exploit that is a separate, untested question for the 6 non-confirmed ids.
- Perturbed-kind detection uses regex classification over the injector body. It is
  reported per kind, and a kind may be over- or under-attributed where an injector's
  helper indirection hides the mutation. Spot-checked against `missing_service`,
  `target_port`, and `sidecar_port_conflict`; not exhaustively verified for all 123.
- Compound oracles are scored as the **union** of their children's surfaces, matching
  `compound.py:40-41` where any child failure fails the whole.

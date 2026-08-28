# R2 Part A — Re-derivation of the perturbed-kind classification on code only

Status: complete. Read-only against SREGym source. No cluster mutation.
Date: 2026-08-28.

Artifacts: `analysis/census/reclassify_code_only.py`, `analysis/census/pid2class.json`,
`analysis/census/coverage_code_only.csv`. `coverage.csv` is unmodified (123 rows).

## A0. Direction of error — confirmed, with one addition and one limit

Where a verdict rests on the `perturbed_kinds` column, ADEQUATE is reached by a
**disjunction**: the row is ADEQUATE if *any* attributed kind is read by the problem's
mitigation oracle. Adding a kind can therefore only move a row **into** ADEQUATE.

Confirmed: **over-attribution biases toward ADEQUATE and under-counts BLIND.**

Addition not stated in the audit: BLIND requires a non-empty kind set disjoint from what
the oracle reads; an **empty** kind set yields UNCERTAIN. So over-attribution also
suppresses UNCERTAIN. Both error directions are one-way.

Limit established during A2, and it matters: the published verdict is **not** a pure
function of `perturbed_kinds`. See A2. The disjunction argument is therefore a statement
about the rows where the kind column was load-bearing, not an arithmetic identity over
all 123.

## A1. Code-identifiers-only attribution surface

`analysis/census/reclassify_code_only.py`. The surface is built by AST walk over the
problem class and its resolved `inject_*` methods, taking `ast.Name.id` and
`ast.Attribute.attr` only, and skipping every `ast.Constant`. Docstrings, comments and
all string literals are excluded by construction, not by regex.

Problem-id -> class resolution: `analysis/census/pid2class.json`, built by AST-parsing the
registry dict literal in `registry.py`, resolving `lambda: Cls(...)` entries through the
`Lambda -> Call -> Name` chain. **123 of 123 ids resolved, 0 unresolved**, and the key set
is identical to `coverage.csv`. This was done statically rather than by importing the
runtime registry, specifically to avoid cluster contact (see the disclosure at the end).

Emitted as a **second column** in `coverage_code_only.csv`, alongside the full-text column
and a per-row set difference in both directions. Nothing is overwritten.

## A2. Row-by-row diff, and a correction to this task's own premise

### Kind sets — this is the real measurement

Comparing full-text `perturbed_kinds` against the code-only surface, over 123 rows
(the literal placeholder value `UNCERTAIN` is not counted as a kind):

| | count |
|---|---:|
| code-only kind set non-empty | 68 |
| code-only kind set empty | 55 |
| rows whose kind set differs | **87 / 123** |
| ... losing at least one kind attributed only in prose | **72** |
| ... gaining at least one kind the full-text regex missed | **33** |
| rows where no injector chain resolved | 0 |

Neither column is correct. Full-text **over**-attributes, because injector and problem-class
docstrings and `root_cause` prose name resources the code never touches. Code-only
**under**-attributes, because SREGym injects faults by shelling out —
`kubectl.exec_command("kubectl delete service ...")` — so the resource kind lives in a
string literal the AST walk deliberately discards. The sound surface is *command strings
inside the resolved `inject_*` method*, which is neither column.

### Correction: the verdict cannot be mechanically re-derived

My plan for A2 was to swap the kind set into the census classifier and read off new
counts. **That is not possible, and reporting such counts would have been wrong.**

I reconstructed the most plausible mechanical rule — functional signal first, then empty
set -> UNCERTAIN, then intersect the kind set with what the oracle reads — and ran it on
the *published* `perturbed_kinds`. It disagrees with the published verdict on **48 of 123
rows**. `oracle_other_kinds` holds free prose (`container image`, `ConfigMap keys`,
`pod logs`), not a controlled kind vocabulary, and `perturbed_kinds` sometimes holds the
literal string `UNCERTAIN`. The C1 census verdicts were hand-read from each oracle's
`evaluate()`, as the C1 method rule required; the kind column informed that reading but did
not determine it.

Consequences I am obliged to state:

1. Any "new counts beside the current 85/6/32" produced by swapping columns would measure
   the gap between a hand census and a crude classifier of my own construction — **not**
   over-attribution bias. I am not reporting such a table, and the 82/7/34 and 58/7/58
   figures I derived earlier in this task are withdrawn as measurements. They were
   artifacts of that reconstruction, including one ordering bug in my own code
   (empty-set -> UNCERTAIN tested before the functional-signal check).
2. The code-only column's value is as a **triage filter**, and it worked: it selected 17
   rows where prose was doing attribution work, and hand-verification of those 17 found
   two genuine BLIND rows the census had scored ADEQUATE.
3. The `perturbed_kinds` column remains, as METHOD_AUDIT.md section 4 already recorded,
   the weakest column in the census. Nothing here repairs it; A3 hand-verifies a subset.

## A3. Hand-verification of the 17 rows that left ADEQUATE

Depth matched to the six original BLIND rows: read the injector, name the mutated
resource kinds with file:line, read the problem's mitigation oracle, verdict last.
Paths relative to the pinned SREGym checkout.

### Confirmed ADEQUATE (15)

| problem_id | kinds actually mutated (file:line) | oracle | why ADEQUATE |
|---|---|---|---|
| `missing_configmap_social_network`, `missing_configmap_hotel_reservation` | ConfigMap deleted `inject_virtual.py:1403-1404`; Deployment scaled 0 then back up `:1408-1409` | `MitigationOracle` | The scale-cycle forces pod recreation; the new pod cannot mount the deleted ConfigMap and never reaches Running. Caught **incidentally** via pod state, not by reading the ConfigMap. Full-text `['ConfigMap','Deployment']` was right; code-only missed `Deployment` because `kubectl scale deployment` is a literal. |
| `scale_pod_zero_social_net` | Deployment `spec.replicas=0` `inject_virtual.py:126` | `ScalePodZeroMitigationOracle` | Oracle reads Deployment replicas directly. |
| `taint_no_toleration_social_network` | Node taint `inject_virtual.py:2109`; target Pods deleted `:2113` | `MitigationOracle` (ns `social-network`) | Deleted pods are recreated and cannot schedule onto the tainted node -> Pending, not Ready. Caught incidentally. Node itself is never read. |
| `pod_cidr_exhaustion_hotel_reservation` | Calico `IPAMConfig` `pod_cidr_exhaustion_hotel_reservation.py:76`; `IPPool` created `:81` and default pool disabled `:94-95`; exhaust `Namespace` `:99`; all app Pods force-deleted `:145` | `MitigationOracle` (ns `hotel-reservation`) | Line 145 deletes every pod in the app namespace; replacements cannot obtain an IP and stay ContainerCreating -> not Ready. Caught incidentally. |
| `incorrect_image`, `faulty_image_correlated`, `update_incompatible_correlated` | Deployment container image | `IncorrectImageMitigationOracle` | Reads `container.image` on the perturbed Deployment, `incorrect_image_mitigation.py:25,27`. |
| `rolling_update_misconfigured_social_network`, `rolling_update_misconfigured_hotel_reservation` | Deployment `spec.strategy` | `RollingUpdateMitigationOracle` | Reads `strategy.rollingUpdate.maxUnavailable`/`maxSurge`, `rolling_update_misconfiguration_mitigation.py:43-56`, on the Deployment fetched at `:65`. |
| `kubelet_eviction_threshold_misconfig` | kubelet `config.yaml` on the Node | `KubeletEvictionThresholdMisconfigMitigationOracle` | Reads the kubelet config file `:18` and Node DiskPressure `:24-26`. |
| `operator_invalid_affinity_toleration` | TiDBCluster CR affinity | `InvalidAffinityMitigationOracle` | Reads the CR `:57`, the StatefulSet `:65`, the pods `:75`. |
| `operator_non_existent_storage` | TiDBCluster CR `storageClassName` | `NonExistentStorageClassMitigationOracle` | Reads CR `:57-59` and the PVCs `:62-68`. |
| `operator_overload_replicas` | TiDBCluster CR `tidb.replicas` | `OverloadReplicasMitigationOracle` | Reads CR replicas `:57-58` and StatefulSet replicas `:62-65`. |
| `operator_security_context_fault` | TiDBCluster CR `securityContext` | `SecurityContextMitigationOracle` | Reads CR `:55`, STS `runAsUser` `:63`, pod `securityContext` `:80`. |

The nine dedicated-oracle rows fell to UNCERTAIN under code-only purely because the
kind set went empty; the oracle demonstrably reads the perturbed field. Classifier
artifact, not a substantive change.

### New BLIND (2)

**`operator_wrong_operator_image` — BLIND (namespace mismatch)**

- Perturbed kind: **Pod**, in namespace `tidb-operator`. The injector reads the
  operator pod name `inject_operator.py:280-281` and applies a patch whose
  `metadata.namespace` is the literal `"tidb-operator"` `:292`, setting the image to
  `pingcap/tidb-operatorr:v1.6.3` (deliberate typo) `:297` -> ImagePullBackOff.
- Problem namespace is `tidb-cluster`, `problems/operator_misoperation/wrong_operator_image.py:16`.
  The injector is separately pointed at `tidb-operator` at `:21`.
- Oracle is the bare `MitigationOracle`, `wrong_operator_image.py:31`. It lists
  Deployments `mitigation.py:66` and Pods `:86` in `self.problem.namespace` only
  (`:59`) — i.e. in `tidb-cluster`.
- **The broken object is in a namespace the oracle never looks at.** The running TiDB
  cluster pods are untouched, so the oracle sees a fully healthy namespace while the
  operator is in ImagePullBackOff. Verdict: **BLIND**.

**`auth_miss_mongodb` — BLIND (readiness carries no application signal)**

- Perturbed kinds: the `url-shorten-mongodb` release values via
  `Helm.upgrade` forcing `tls.mode: requireTLS` `inject_virtual.py:74-88`; then each
  client Deployment is scaled to 0 `:91` and **back to 1** `:93`.
- Oracle is the bare `MitigationOracle`. Deployment replicas are restored to 1 by the
  injector itself, so the replica check `mitigation.py:66-84` compares equal to baseline.
- Readiness: **verified absent** — no `readinessProbe`, `livenessProbe` or
  `startupProbe` in any template of the socialNetwork chart. Proving command, which
  returns nothing:
  `grep -rn 'readinessProbe\|livenessProbe\|startupProbe' SREGym-applications/socialNetwork/helm-chart/socialnetwork/templates/`
  With no probe, a container is Ready as soon as its process is running.
- The client cannot reach a TLS-required mongod. `UrlShortenService.cpp:84-93` retries
  `CreateIndex` in `while (!r) { ...; sleep(1); }` — an **unbounded** loop. The process
  never exits and never reaches `server.serve()` `:111`. Container stays Running;
  Pod stays Ready; the service answers nothing.
- Verdict: **BLIND**. This is the same false-acceptance shape as the confirmed MS-M01
  result: replicas at baseline, pods Ready, application functionally dead.

**Structural note.** The absence of probes across the entire socialNetwork chart means
that for *every* social-network problem judged by the bare `MitigationOracle`,
"Pod Ready" distinguishes only whether a container **started**, never whether it
**serves**. That is a property of the benchmark's applications, not of any one fault.

## A4. Is 6 a point estimate or a lower bound?

**6 was a lower bound, and it was too low. The corrected count is 8.**

Revised headline over the 123-row ledger: **BLIND 8 of 123**, with ADEQUATE falling from
85 to 83 and UNCERTAIN unchanged at 32.

This is a stronger result and it was arrived at by finding and fixing our own error. The
attribution surface admitted resource kinds named only in injector and problem-class prose;
because ADEQUATE is reached by disjunction over that set, every spurious kind could only
manufacture ADEQUATE. Two rows were credited to oracles that demonstrably cannot see the
fault. `operator_wrong_operator_image` is the sharpest of the eight: the failure is not
subtle — a controller in ImagePullBackOff — and the oracle misses it for a purely
structural reason, that it queries `tidb-cluster` while the broken Pod is in
`tidb-operator`. `auth_miss_mongodb` is the most consequential, because it reproduces the
exact false-acceptance shape of the confirmed MS-M01 result from source alone: the injector
itself restores the replica count, no probe exists anywhere in the chart, and the client
process spins forever in a retry loop without exiting or serving.

**8 remains a lower bound, and the bound is loose.** Hand-verification now covers **28 of
123** rows: the 6 original BLIND, the 5 rows added in R1, and the 17 audited here. The
three sets are **pairwise disjoint** — verified by set intersection, not assumed — so the
union is exactly 6 + 5 + 17 = 28. (An earlier draft of this section said 23, guessing at
an overlap that does not exist.) The remaining 95 rows were scored ADEQUATE or UNCERTAIN
without individual hand-checking of the kind attribution, and 72 of 123 rows are known to
carry at least one prose-only kind. Every one of those is a candidate for the same error.

The 17 are also an **enriched sample, not a random one**: they were selected precisely
because prose was doing the attribution work. A 2-in-17 defect rate cannot be extrapolated
to the 95 unverified rows in either direction. No claim is made that 8 is final; the
honest statement for the paper is "at least 8 of 123, from 28 rows hand-verified."

## Disclosure — unintended cluster contact

R2 was specified as "no cluster contact." I contacted the cluster once, indirectly and
without intending to. To map ten problem ids to their oracle classes I ran
`ProblemRegistry().get_problem_instance(pid)` under the SREGym interpreter.
`Problem.__init__` constructs an `Application`, whose constructor calls kubectl and creates
the application namespace if absent. The run log shows it created two namespaces:

```
all.application - INFO - Namespace astronomy-shop not found. Creating namespace.
all.application - INFO - Namespace astronomy-shop created successfully
all.application - INFO - Namespace fleetcast not found. Creating namespace.
all.application - INFO - Namespace fleetcast created successfully
```

What this was: two empty namespaces created. No workload deployed, no fault injected,
nothing mutated in `social-network` or `hotel-reservation`, no evidence altered, no
experiment affected. It is nonetheless cluster contact where none was authorised, and it
is a side effect of importing the registry that I had already relied on in R1 without
noticing. I have not deleted the namespaces, since teardown is likewise unauthorised. The
subsequent `pid2class.json` build was redone statically by AST so that it makes no such
call.

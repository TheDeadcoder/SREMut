# Adjudication: does the proposed `missing_service` fix actually work?

The one-time comparison of the executed fix-oracle study against the frozen prediction
table in `PREREGISTRATION_FIX_ORACLE.md` §3.

**Result: 20 of 20 predicted cells matched. None of the four falsification conditions in
§5 fired.**

Three runs — `fix-m01-r01`, `fix-m02-r01`, `fix-m03-r01` — executed 2026-08-29, all
`COMPLETE`, all from one unmodified driver (`git diff` against HEAD empty for the whole
schedule).

---

## 1. Predicted against observed

**Predicted** (`PREREGISTRATION_FIX_ORACLE.md` §3, RFC 3161 token `0x075A69B3`, taken
before the driver existed):

| Configuration | healthy | M01 faulted | M02 faulted | M03 faulted | restored |
|---|---|---|---|---|---|
| **O1** stock | true | true | true | true | true |
| **O2** un-patched | false | false | false | false | false |
| **O3** patched | true | false | false | false | true |
| **O4** composed | true | false | false | false | true |

**Observed**, each cell the strict JSON boolean read from `evaluate()`:

| Configuration | healthy | M01 faulted | M02 faulted | M03 faulted | restored |
|---|---|---|---|---|---|
| **O1** stock | true | true | true | true | true |
| **O2** un-patched | false | false | false | false | false |
| **O3** patched | true | false | false | false | true |
| **O4** composed | true | false | false | false | true |

The healthy and restored columns each summarise three runs; all three agreed in every
case, so no cell is an average or a majority. The full 36-cell grid is in
`experiments/FIX_ORACLE_LEDGER.json`.

## 2. Why each falsification condition did not fire

| # | Condition (§5) | Observed | Fired? |
|---|---|---|---|
| 1 | **O2 `true` on `healthy` or `restored`** would show C3 wrong and the naive swap harmless | O2 returned `false` in all six control states | **No** |
| 2 | **O3 `true` on any faulted state** would show the fix insufficient for that mutant | O3 returned `false` on all three faulted states | **No** |
| 3 | **O3 `false` on `healthy` or `restored`** would show the fix harmful even patched | O3 returned `true` in all six control states | **No** |
| 4 | **O4 disagreeing with `O1 AND O3`** would show `CompoundedOracle` does not behave as `compound.py:40-41` suggests | O4 equalled `O1 AND O3` in all nine states | **No** |

Condition 3 was the one §5 named as most consequential — it would have blocked the
upstream PR in its current form. It did not fire.

---

## 3. Mechanistic evidence

The verdicts alone do not carry the following four facts, and the argument for the patch
depends on them.

### 3.1 The exception, captured verbatim

All nine O2 diagnostic calls — three states x three runs — raised exactly one exception,
with one message:

```
AttributeError: 'MissingService' object has no attribute 'expected_service_port'
```

Zero distinct alternatives. This is direct evidence for `analysis/PR_PLAN.md` C3, which
predicted precisely this attribute error from source alone and had never been executed.

The diagnostic call exists because `evaluate()` returns a bare `{"success": False}` for
any of its ten failure paths (the broad handler at `service_endpoint_mitigation.py:178`
swallows the cause). Without an unguarded call to `_run_connectivity_probe`, an
`AttributeError` and a genuine connectivity failure are indistinguishable in the record.

### 3.2 Timing shows *where* each rejection happened

`_run_connectivity_probe` creates a pod, waits for it to run `nc`, reads its logs and
deletes it. Reaching predicate 8 therefore costs seconds; failing at an earlier predicate
costs milliseconds. O3 `elapsed_seconds`:

| Mutant | healthy | faulted | restored |
|---|---|---|---|
| MS-M01 | 2.275 s | **0.407 s** | 4.474 s |
| MS-M02 | 4.310 s | **0.387 s** | 4.251 s |
| MS-M03 | 4.265 s | **2.279 s** | 4.263 s |

Under **MS-M01** the Service is absent, so `read_namespaced_endpoints` at `:158` raises a
404 and the oracle fails early. Under **MS-M02** the Service exists but its selector
matches no pod, so there are no ready endpoints and it fails at `:166-168`. Neither
reaches the probe — 0.407 s and 0.387 s, an order of magnitude below the control states.

Under **MS-M03** the Service exists with a valid ClusterIP, and one ready endpoint maps
cleanly through Pod → ReplicaSet → `Deployment/user-service`. Predicates 1 to 7 pass,
execution reaches predicate 8, and **the connectivity probe is what rejects it** — 2.279 s,
in the seconds range, consistent with a probe pod having been created and run.

§4 of the pre-registration named MS-M03 "the sharpest test of C3 among the faulted states"
and "the most falsifiable prediction in the table", resting specifically on the probe
failing against `targetPort: 65535`. That is now **evidenced rather than inferred**, and
it is the reason the diagnostic call was kept rather than dropped when the probe-pod
hazard was found.

### 3.3 O2 reached `:65` on healthy and restored — the inference, stated explicitly

O2 and O3 are the **same class evaluated against the same cluster in the same state**,
differing only in whether `problem.expected_service_port` is set. O3 returned `true` on
every healthy and restored state, which means predicates 1 through 7 passed in those
states. O2 therefore also passed predicates 1 through 7, reached `:175`, called
`_run_connectivity_probe`, and failed at `:65` — the only remaining difference between
them.

**The naive fix rejects a correct, working system.** That is what makes it *harmful*
rather than merely incomplete: substituting it for the stock oracle would trade a false
accept for a false reject.

This inference rests on the **O2/O3 pairing**, not on the diagnostic call alone. The
diagnostic establishes that the attribute access raises; the pairing establishes that
execution actually reached it in the control states. Neither alone is sufficient. Both
are recorded per state per run.

### 3.4 The composed oracle separates its children in a single invocation

Every faulted state, in all three runs, returned:

```json
{"success": false, "accuracy": 50.0,
 "oracles": [{"name": "0-MitigationOracle", "success": true},
             {"name": "1-ServiceEndpointMitigationOracle", "success": false}]}
```

One call, one cluster, one instant: the stock child reports the system repaired while the
endpoint child rejects it. The disagreement between the two graders is not assembled
across runs or across instruments — it is inside a single returned object.

Every healthy and restored state returned `accuracy: 100.0` with both children `true`.

---

## 4. The probe-pod barrier: what it measured

The barrier was added after discovering that `ServiceEndpointMitigationOracle` creates a
real pod at `service_endpoint_mitigation.py:94` and deletes it fire-and-forget at
`:111-115`. Across the study:

```
36 of 36 invocations cleared, all on the first attempt
waited_seconds: min 0.158, mean 0.171, max 0.195
pods observed: 0
kubectl wait calls issued: 0
```

**Both halves of this need saying.**

It measures how fast SREGym's fire-and-forget delete settles in this cluster: across nine
invocations that actually created a pod, the object was always gone before the next
barrier looked, and every clearance came from the independent `kubectl get` re-check
rather than from `kubectl wait`.

It does **not** show the hazard was imaginary. The barrier was self-tested against a
deliberately planted pod carrying the same label: it blocked for the full 30 s deadline
across eight attempts, reported `cleared: false`, and left the pod untouched — then
cleared in 0.173 s once the pod was removed. The mechanism works; nothing triggered it.
The gap between "the delete is fire-and-forget" and "it always settled in time here"
remains unmeasured, and on a slower or busier cluster the barrier is what stands between
a residual `Succeeded` pod and a spurious stock-oracle `false`.

---

## 5. Cross-instrument replication of the headline result

The fix study evaluates the frozen contract in every state, alongside the four oracle
configurations. Its contract results reproduce the nine-repetition matrix exactly:

| Mutant | violated (fix study) | violated (nine-run matrix) |
|---|---|---|
| MS-M01 | `[MS-I1, MS-I2, MS-I3, MS-I4]` | `[MS-I1, MS-I2, MS-I3, MS-I4]` |
| MS-M02 | `[MS-I2, MS-I3, MS-I4]` | `[MS-I2, MS-I3, MS-I4]` |
| MS-M03 | `[MS-I2, MS-I4]` | `[MS-I2, MS-I4]` |

All nine control states returned `PASS`; all three faulted states returned `REJECT`. The
stock oracle returned `{"success": true}` in all nine states, faulted included.

MS-M02's throughput collapse also reproduced: 1,642 faulted requests against 10,240 in its
own healthy window, i.e. **14.76 % successful-response volume retained**, matching the
pattern seen in all three MS-M02 repetitions of the nine-run matrix.

This is **cross-instrument replication**: three further repetitions of the headline result,
produced by an independently written driver (`fix_oracle_run.py`) that shares only the
imported measurement primitives, not the orchestration.

---

## 6. Attestation chain

| Token | Serial | Stated time (Free TSA) | Covers |
|---|---|---|---|
| Prediction document | `0x075A69B3` | `Aug 29 15:16:02 2026 GMT` | `PREREGISTRATION_FIX_ORACLE.md` bytes — taken while `fix_oracle_run.py` did not yet exist, verified by `ls` at the time |
| Pre-execution tree | `0x075A9267` | `Aug 29 15:53:19 2026 GMT` | commit `18f30012b00ce97c7a3d81862adb7237bc0d2b92`, working tree clean |
| First run started | — | `Aug 29 15:54:03 UTC` | `fix-m01-r01` |

Both tokens verify with `openssl ts -verify` against Free TSA's root. Details and the
re-verification recipe are in `attestation/README.md`.

---

## 7. Limits

- **Single provenance.** Every verdict is an in-process call inside the driver's own
  Python process. There is **no isolated-worker leg**, unlike the nine-repetition matrix.
  A reading here is one reading, not two.
- **One repetition per mutant.** Three runs. No claim is made about stability across
  repetitions; the three runs agreed cell-for-cell, which is consistency, not a measured
  variance.
- **The claim is about oracle behaviour, not about cluster state.** What state the cluster
  was in is established independently, by the frozen contract (MS-I1..MS-I5) and by the
  workload window, both evaluated in every state of every run. The oracle verdicts are
  read against that independently established state.
- **`SREGym/` was not modified.** `expected_service_port` is set on the problem *object*
  in our process and deleted afterwards, reproducing the proposed patch's object state at
  evaluate time. `git -C SREGym status` was clean before, during and after every run, and
  the four pinned SREGym module hashes are recorded in every record.
- **MS-I6 is not evaluated** in the contract legs, exactly as in the nine-repetition
  matrix and for the same declared reason.

Deviations from the pre-registration are recorded in `DEVIATIONS_AND_LIMITS.md`.

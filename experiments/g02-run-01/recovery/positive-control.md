# G0.4 Part B — positive control by recovery

Question: if deleting `Service/user-service` caused the workload failures, does
restoring it return the failure rate to the healthy standard of zero — on the same
cluster, in the same session?

All three states below were observed on kind cluster `kind-kind`, namespace
`social-network`, on 2026-08-26, without an intervening redeploy between FAULTED and
RESTORED. Evidence roots: `g02-run-01/` (HEALTHY, FAULTED) and
`g02-run-01/recovery/` (RESTORED).

---

## Method

Recovery was performed through SREGym's own path — `problem.recover_fault()`
(`sregym/conductor/problems/missing_service.py:52`) — via a problem instance built
from `ProblemRegistry().get_problem_instance(...)`. No manual `kubectl apply` was used
at any point.

`recover_fault()` ran `2026-08-26T14:57:25.827855+00:00 ->
2026-08-26T14:58:12.799396+00:00` (46.971 s). It did not raise and did not no-op.
stdout:

    == Fault Recovery ==
    Recreated service user-service to recover from the fault: service/user-service created
    [14:58:01] Waiting for namespace 'social-network' to be stable... kubectl.py:281
    [14:58:12] All pods in namespace 'social-network' are stable.     kubectl.py:291
    Service: user-service | Namespace: social-network

`problem.fault_injected` transitioned `True -> False` (`sregym/utils/decorators.py:13`).

One benign log line appeared at the start —
`Command failed (exit 1): kubectl delete service user-service ... NotFound`. That is
the idempotence guard at `inject_virtual.py:312-313` deleting a Service that was
already absent; it precedes the `kubectl apply` at 314-315 and is expected.

The oracle input was held constant across all three states: the same 27-entry replica
baseline captured pre-injection (`g02-run-01/replica-baseline.json`, sha256
`8e42c0db98368a0a04c582e5f0c40ee2203c73636de81220df9f661cf48cad08`). The SREMut worker
input was **byte-identical** between the FAULTED and RESTORED measurements (887 bytes,
verified).

Both oracle calls were wrapped in the exact `try/except` of `conductor.py:269-271`.

---

## Recovery timing, from the 2 s sampler (`recovery/samples.jsonl`, 90 samples)

| Event | UTC |
|---|---|
| Last sample with `user_service_present == false` | 2026-08-26T14:56:38.017Z |
| **First sample with `user_service_present == true`** | **2026-08-26T14:57:27.280Z** |
| First sample with `user_service_endpointslice_count == 1` | 2026-08-26T14:57:27.280Z (same sample) |
| Pod churn from recovery's `delete pods --all` (`inject_virtual.py:319`) | 14:57:27Z - 14:58:03Z (peak 51 pods, 28 not-Running) |
| Fully settled: 28 pods, 0 not-Running, 0 deployments below desired | **2026-08-26T14:58:11.728Z** |

Service presence and EndpointSlice availability were restored in the same sampling
instant.

## Restored Service identity vs the pre-fault capture

Comparing `g02-run-01/pre/user-service.json` (captured 11:02:08Z, before injection)
with `recovery/post-recovery/user-service.json`:

| Field | Pre-fault | Restored | Same? |
|---|---|---|---|
| `spec.clusterIP` | 10.96.100.113 | 10.96.100.113 | **YES** |
| `spec.type` | ClusterIP | ClusterIP | **YES** |
| `spec.ports` | 9090 -> 9090/TCP | 9090 -> 9090/TCP | **YES** |
| `spec.selector` | `{service: user-service}` | `{service: user-service}` | **YES** |
| `metadata.uid` | b7a221cc-ba01-482f-9141-e7e6641aa077 | 5d16d918-e1c6-4ecc-9acf-2dbde20abaeb | **NO** |
| `metadata.creationTimestamp` | 2026-08-26T11:02:08Z | 2026-08-26T14:57:26Z | **NO** |
| `metadata.resourceVersion` | 1986010 | 2017288 | **NO** |

The restored Service is a **new API object** (new UID, new creationTimestamp) carrying
**identical routing-relevant spec**, including the same ClusterIP — the saved manifest
at `/tmp/user-service_modified.yaml` retains `spec.clusterIP`, so the address was
reclaimed rather than reassigned. For the purpose of this control, functional identity
is what matters and it is exact; object identity is not preserved and was not expected
to be.

---

## The three-state table

| | **HEALTHY** | **FAULTED** | **RESTORED** |
|---|---|---|---|
| Evidence root | `baselines/.../run-0{1,2,3}` + `g02-run-01/pre` | `g02-run-01/t-minus`, `t-plus`, `post` | `g02-run-01/recovery` |
| **In-process oracle verdict** | `{"success": true}` | `{"success": true}` | `{"success": true}` |
| — UTC start / end | 11:02:37.167335Z / 11:02:37.497117Z | 11:03:20.021093Z / 11:03:20.584965Z | 15:00:05.992019Z / 15:00:06.528295Z |
| — elapsed | 0.330 s | 0.564 s | 0.536 s |
| **SREMut worker verdict** | **not measured** | `RETURNED_TRUE`, `returned_boolean: true`, exit 0 | `RETURNED_TRUE`, `returned_boolean: true`, exit 0 |
| — UTC start / end | not measured | 14:31:35.113Z / 14:31:43.287Z | 15:00:22.474Z / 15:00:30.277Z |
| — `raw_result_sha256` | not measured | `c955e577…3908f97` | `c955e577…3908f97` (identical) |
| **Service `user-service` present** | yes | **no** | yes |
| **EndpointSlices for user-service** | 1 | **0** | 1 |
| Service count in namespace | 30 | 29 | 30 |
| Deployments ready / total | 27 / 27 | 27 / 27 | 27 / 27 |
| Pods, all Running & ready | 28, yes | 28, yes | 28, yes |
| **Workload non-2xx rate** | **0.0000 %** | **9.9689 %** | **0.0000 %** |
| — completed rounds measured | 70 (30+14+26) | 1126 | 7 |
| — rounds carrying non-2xx | 0 | 1126 (100 %) | **0** |

Not measured, stated explicitly: the SREMut worker was never run against the HEALTHY
state. The healthy in-process verdict comes from the g02-run-01 control gate; the
healthy workload rate comes from the three frozen baselines, which were captured in
earlier sessions on earlier deployments, not from the 11:02Z healthy window of this
run. The FAULTED and RESTORED rows are both from this session on this cluster.

---

## Post-recovery workload detail (B4)

Captured `recovery/wrk2-job.log` at 14:59:35.441Z, after 7 complete rounds had elapsed
following the recovery timestamp. wrk2 emits a `Non-2xx or 3xx responses:` line only
when the count is non-zero; its absence means zero.

| Round | Reported | Requests | Non-2xx | Socket errors |
|---:|---|---:|---:|---|
| #9 | 14:58:21.901636987Z | 1024 | **0** | none |
| #10 | 14:58:32.916147156Z | 1024 | **0** | none |
| #11 | 14:58:43.927573665Z | 1024 | **0** | none |
| #12 | 14:58:54.940865271Z | 1024 | **0** | none |
| #13 | 14:59:05.954966729Z | 1024 | **0** | none |
| #14 | 14:59:16.968297065Z | 1024 | **0** | none |
| #15 | 14:59:27.982009706Z | 1024 | **0** | none |

Marker counts in the window after 14:57:27.280Z: complete rounds 7, `Non-2xx` lines
**0**, `Socket errors` 0, `unable to connect` 9.

The 9 `unable to connect` entries are rounds #0-#8, 14:57:52.372Z - 14:58:09.847Z,
before `nginx-thrift` finished restarting after recovery's own
`kubectl delete pods --all`. They precede the first completed round and are the same
startup artifact seen in the faulted run (which had 5). They are not user-service
failures: the target `10.96.22.193:8080` is the frontend, and the message is
`Connection refused`, not a name-resolution failure.

**Prediction and observation agree exactly.** Part A predicted that restoring the
Service should drive the rate to precisely zero, not merely lower, because 100 % of
the compose-post share failed and 0 % of everything else did. Observed: 0 of 7168
requests failed across 7 rounds.

---

## Conclusion

The causal link is established on a single cluster within a single session:

- Deleting `Service/user-service` moved the workload failure rate from 0.0000 % to
  9.9689 %, exactly the compose-post share of the request mix.
- Restoring the same Service through SREGym's own `recover_fault()` returned the rate
  to 0.0000 % across 7 consecutive rounds, with no other intervention.

**The stock `MitigationOracle` returned `{"success": true}` in all three states.** Its
FAULTED and RESTORED results are not merely both true — they are byte-identical,
sha256 `c955e57777ec0d73639dca6748560d00aa5eb8e12f13ebb2ed9656add3908f97`. The oracle
carries no information that distinguishes a working system from a broken one for this
fault, while the workload distinguishes them completely and reproducibly.

Scope: n=1 injection and n=1 recovery. The three-state pattern is established for this
run; repetitions are needed before any rate claim is made.

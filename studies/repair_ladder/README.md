# Repair ladder

Whether the verifiers of `missing_service_social_network` tell plausible wrong repairs from correct
ones when graded through SREGym's own Conductor, on the patched checkout from
`studies/missing_service_fix`.

## States

After SREGym's injector deletes `Service/user-service`, one repair state is applied:

| State | Repair |
|---|---|
| M1 | none: the Service stays absent |
| M2 | Service recreated with a selector that matches no pod |
| M3 | Service recreated with `targetPort` 65535 |
| M5 | Service recreated exactly as captured; client pods untouched |
| M4 | selector-less Service with a manual Endpoints object pinned to the current pod; clients restarted |
| C1 | Service recreated exactly as captured; clients restarted |

A client restart (M4, C1) deletes every pod in the namespace except the `user-service` pod, and
their controllers recreate them; the load generator's Job pod is left running. Every run also
measures the healthy state before injection and the restored state after SREGym's own
`recover_fault()`.

## Graders

Each state is graded by five verifiers, all on the same cluster state:

| Grader | What it is |
|---|---|
| `stock` | the generic `MitigationOracle`, first child of the patched oracle |
| `service_aware` | `ServiceEndpointMitigationOracle`, second child |
| `patched` | the composed oracle from the patch |
| `workload` | SREGym's `WorkloadOracle` |
| `contract` | the operational contract (`contracts/missing_service_social_network.yaml`), MS-I1 to MS-I6, in `contract.py` |

MS-I6 deletes the current `user-service` pod, waits for its replacement and repeats the DNS,
endpoint, routing and workload checks. It runs on M5, M4, C1 and the restored state.

## Procedure

`ladder.py` drives the Conductor with only the mitigation stage:

1. The Conductor deploys the application and starts the load generator. Just before it injects the
   fault, the run waits until a full workload round succeeds, then measures the healthy state: the
   patched oracle in-process, the workload oracle, and the contract.
2. The Conductor injects the fault. The run applies the repair and verifies it structurally.
3. At least 60 s after injection, once the load generator runs again and has completed a round, the
   run submits. The Conductor's own mitigation evaluation is the graded verdict.
4. The workload oracle and the contract are measured on the graded state.
5. `recover_fault()` restores the system. Once a full workload round succeeds again, the restored
   state is measured.
6. The Conductor's own teardown runs. It is deferred only until step 5 finishes.

SREGym's injection and `recover_fault()` delete every pod in the namespace, the load generator's
included, and its Job recreates that pod only after a back-off. The waits in steps 1, 3 and 5 keep
every grader from judging the load generator's restart instead of the system.

## Schedule

- Pilot, not counted: M4, M5 and C1, to confirm the Kubernetes behaviour, the driver, and that MS-I6
  passes a correct repair. Pilot runs are kept under `pilot/`.
- Counted: three attempts per state. State `i` (order M1, M2, M3, M5, M4, C1) of attempt `a` runs
  on server A when `i + a` is odd, otherwise on server B, so every state runs on both servers.

## Predictions

Committed after the pilot and before the first counted run. A grader accepts a state when it
reports success.

| State | `stock` | `service_aware` | `patched` | `workload` | `contract` |
|---|---|---|---|---|---|
| M1 | accept | reject | reject | reject | reject: MS-I1, MS-I2, MS-I3, MS-I4 |
| M2 | accept | reject | reject | reject | reject: MS-I2, MS-I3, MS-I4 |
| M3 | accept | reject | reject | reject | reject: MS-I2, MS-I4 |
| M5 | accept | accept | accept | accept | accept |
| M4 | accept | accept | accept | accept | reject: MS-I6 |
| C1 | accept | accept | accept | accept | accept |

Every grader accepts the healthy and the restored state of every run.

## Running

From the repository root, on the server:

```bash
python3 studies/repair_ladder/run_ladder.py --server A --pilot
python3 studies/repair_ladder/run_ladder.py --server A --attempt 1
python3 studies/repair_ladder/ladder_ledger.py
python3 studies/repair_ladder/ladder_ledger.py --check
```

Each run is written to `runs/<state>/attempt-<n>/` (or `pilot/`): `record.json`, `result.json` and
the driver's logs with timestamps removed. The runner stops after any run that did not complete or
whose teardown failed.

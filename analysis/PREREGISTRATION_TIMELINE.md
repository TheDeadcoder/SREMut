# Pre-registration timeline — forensic record

Facts only. No wording is proposed for the paper; this document establishes what the
timestamps do and do not support.

All data from `git log`, `git for-each-ref` and `git ls-tree` in `SREMut/` (read-only),
plus execution timestamps recorded inside each run's own JSON records. SREMut has no
remote, so every timestamp below is local-machine only and is not independently
corroborated by any third party.

---

## C1 / C2 / C3 — protocol commit vs evidence commit

| Artifact | Introducing commit | Author date | Commit date | Evidence it governs | Evidence introducing commit | C3 verdict |
|---|---|---|---|---|---|---|
| `experiments/PROTOCOL_G1.md` | `9314bda3` | 2026-08-26T20:11:56Z | 2026-08-26T20:11:56Z | `g1-run-01/02/03` | `9314bda3` | **SAME COMMIT** |
| `experiments/PROTOCOL_W1.md` | `7820e937` | 2026-08-27T03:47:32Z | 2026-08-27T03:47:32Z | `w1-delay0-01/02` | `7820e937` | **SAME COMMIT** |
| `experiments/PROTOCOL_W2.md` | `3d46355a` | 2026-08-27T05:07:53Z | 2026-08-27T05:07:53Z | `w2-noise-01/02` | `3d46355a` | **SAME COMMIT** |
| `experiments/PROTOCOL_W3.md` | `96f5b1e6` | 2026-08-27T09:51:15Z | 2026-08-27T09:51:15Z | `w3-hotel-01` | `96f5b1e6` | **SAME COMMIT** |
| `experiments/PROTOCOL_W4.md` | `0a3a0e59` | 2026-08-27T10:23:01Z | 2026-08-27T10:23:01Z | `w4-hotel-01` | `0a3a0e59` | **SAME COMMIT** |
| `analysis/G02_null_agent_plan.md` | `9314bda3` | 2026-08-26T20:11:56Z | 2026-08-26T20:11:56Z | `g02-run-01` | `388872a3` (2026-08-26T14:27:43Z) | **evidence commit PRECEDES the plan commit** |

**C3, stated plainly: no protocol commit strictly precedes the commit introducing the
evidence it governs.** Five of six are the same commit. The sixth is worse — the
`g02-run-01` evidence was committed at 14:27:43Z, nearly six hours *before* the plan
document that governs it was committed at 20:11:56Z.

Author date equals commit date for every commit above; there is no rebase or amend
skew to appeal to.

## C4 — protocol commit date vs earliest recorded execution

| Protocol | Commit date | Run | Earliest execution timestamp in the run's own record | Commit predates execution? |
|---|---|---|---|---|
| `PROTOCOL_G1.md` | 2026-08-26T20:11:56Z | `g1-run-01` | 2026-08-26T19:28:10.037861Z | **NO** |
| `PROTOCOL_G1.md` | 2026-08-26T20:11:56Z | `g1-run-02` | 2026-08-26T19:38:01.776896Z | **NO** |
| `PROTOCOL_G1.md` | 2026-08-26T20:11:56Z | `g1-run-03` | 2026-08-26T19:47:43.375448Z | **NO** |
| `PROTOCOL_W1.md` | 2026-08-27T03:47:32Z | `w1-delay0-01` | 2026-08-26T21:51:21.792023Z | **NO** |
| `PROTOCOL_W1.md` | 2026-08-27T03:47:32Z | `w1-delay0-02` | 2026-08-26T22:01:18.835253Z | **NO** |
| `PROTOCOL_W2.md` | 2026-08-27T05:07:53Z | `w2-noise-01` | 2026-08-27T04:07:04.382061Z | **NO** |
| `PROTOCOL_W2.md` | 2026-08-27T05:07:53Z | `w2-noise-02` | 2026-08-27T04:16:48.105650Z | **NO** |
| `PROTOCOL_W3.md` | 2026-08-27T09:51:15Z | `w3-hotel-01` | 2026-08-27T05:12:33.384Z | **NO** |
| `PROTOCOL_W4.md` | 2026-08-27T10:23:01Z | `w4-hotel-01` | 2026-08-27T09:54:56.706800Z | **NO** |
| `G02_null_agent_plan.md` | 2026-08-26T20:11:56Z | `g02-run-01` | 2026-08-26T11:00:19.367292Z | **NO** |

**C4, stated plainly: for every run, the protocol's introducing commit POSTDATES the
execution it governs.** In no case does a commit timestamp corroborate pre-registration.

## C5 — the four annotated tags

| Tag | Tag object | Tagger date | Peels to | Commit date | Evidence files under `experiments/` or `analysis/` in the tree at that commit |
|---|---|---|---|---|---:|
| `sremut-missing-service-contract-v1` | `378e9e91` | 2026-08-16T17:54:21Z | `abed58d6` | 2026-08-16T16:45:36Z | **0** |
| `sremut-missing-service-execution-profile-v1` | `7c6493eb` | 2026-08-20T05:52:20Z | `35fcaeec` | 2026-08-20T04:52:48Z | **0** |
| `sremut-missing-service-evidence-policy-v1` | `40e50e4f` | 2026-08-22T12:59:30Z | `c2f500c9` | 2026-08-22T12:49:50Z | **0** |
| `sremut-missing-service-evidence-policy-v1.1` | `8e44e66c` | 2026-08-24T16:33:54Z | `560e8e81` | 2026-08-24T16:28:24Z | **0** |

All four tag objects carry tagger dates between 2026-08-16 and 2026-08-24. The earliest
execution timestamp anywhere in the project is `g02-run-01` at
**2026-08-26T11:00:19Z**. Every tag therefore predates all execution by at least two
days, and no evidence file existed in the tree at any of the four tagged commits.

## C6 — summary

| Registered artifact | Timestamp provably predates the execution it governs? | Basis |
|---|---|---|
| `contracts/missing_service_social_network.yaml` (tag `…contract-v1`) | **YES** | tagger date 2026-08-16, 10 days before first execution; 0 evidence files in tree |
| `profiles/…/pilot-v1.yaml` (tag `…execution-profile-v1`) | **YES** | tagger date 2026-08-20; 0 evidence files in tree |
| `policies/…/evidence-capture-v1.yaml` (tag `…evidence-policy-v1`) | **YES** | tagger date 2026-08-22; 0 evidence files in tree |
| `policies/…/evidence-capture-v1.1.yaml` (tag `…evidence-policy-v1.1`) | **YES** | tagger date 2026-08-24; 0 evidence files in tree |
| `mutants/…/registry.yaml` (in the contract tag's tree) | **YES** | same commit as `…contract-v1` |
| `analysis/G02_null_agent_plan.md` (incl. the R3 rule) | **NO** | committed 2026-08-26T20:11:56Z; `g02-run-01` executed from 11:00:19Z the same day |
| `experiments/PROTOCOL_G1.md` (R1, R2, R3) | **NO** | committed 20:11:56Z; G1 runs executed 19:28-19:57Z |
| `experiments/PROTOCOL_W1.md` (H2) | **NO** | committed 2026-08-27T03:47:32Z; runs executed 2026-08-26T21:51-22:11Z |
| `experiments/PROTOCOL_W2.md` (H3, H4) | **NO** | committed 05:07:53Z; runs executed 04:07-04:26Z |
| `experiments/PROTOCOL_W3.md` (H4-lite) | **NO** | committed 09:51:15Z; run executed from 05:12:33Z |
| `experiments/PROTOCOL_W4.md` (+ amendment R3-A) | **NO** | committed 10:23:01Z; run executed from 09:54:56Z |

**Two distinct classes.**

The **four frozen pre-registration artifacts** — contract, execution profile, and both
evidence policies, plus the mutant registry — have annotated-tag timestamps that predate
all execution by days, in trees containing no evidence. Their pre-registration is
supported by git.

The **six experiment protocols** have no such support. Every one was committed after the
runs it governs had executed, and five share a commit with their own evidence. Their
content states they were written before execution, and the session transcript records
them being written first, but **git provides no independent corroboration of that
ordering for any of them.** Amendment R3-A is a documented special case: it was written
after the verdict existed on disk and discloses this in its own text.

A further limitation applying to all rows: SREMut has no git remote, so every timestamp
here originates on a single machine with a user-writable clock and is not attested by any
external service.

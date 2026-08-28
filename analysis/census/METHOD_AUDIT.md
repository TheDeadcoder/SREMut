# Census method audit — every pattern-matched derivation

Triggered by the R1 root cause: the census undercounted the registry 118/123 because its
key parser used `r'"([a-z0-9_]+)":'`, a character class excluding `-`. That defect bounds
the *enumeration* failure, but it raises the prior on every other step derived by regex or
line matching rather than by AST or runtime import. This audit enumerates all of them.

Read-only. All commands re-runnable at commit `ba07faf1`.

---

## Summary table

| # | Step | Method used | Sound? | Impact found |
|---|---|---|---|---|
| 1 | Registry ID enumeration | regex over line range | **NO** | **5 IDs dropped; denominator wrong** |
| 2 | Oracle class enumeration | `grep '^class .*('` | **NO** | count mis-stated; final 60 correct by cancellation |
| 3 | Problem -> oracle mapping | regex + last-match | partially | **no disagreement with AST** |
| 4 | Perturbed resource kinds | regex over whole class body | **NO** | **prose counted as evidence in 52 files** |
| 5 | `restarts_pods` / `waits_stability` | regex over class + injector | partially | correct on spot-checks; not exhaustively verified |
| 6 | Oracle observation surfaces | regex over **AST-derived** call graph | mostly | call graph sound; patterns unverified |
| 7 | `fault_type` extraction | regex `\w+` | **NO** | 2 hyphenated fault types missed |

---

## 1. Registry ID enumeration — UNSOUND, root cause

**Matched:** `r'\s*"([a-z0-9_]+)":\s*(.*?),?\s*$'` over `registry.py` lines 124-356.
**Silently fails on:** any key containing a character outside `[a-z0-9_]` — hyphens,
dots, uppercase. Also on multiple keys per line, and on keys spanning lines.
**Occurs:** yes — 5 hyphenated keys at `registry.py:136,137,138,139,164`.

Re-derived by AST: one dict-literal assignment, **123** keys, 0 non-literal keys,
0 duplicates, 0 lines carrying more than one key. AST set == runtime set.

**Fixed.** `generate_census.py` takes IDs from `ProblemRegistry().PROBLEM_REGISTRY` and
`tests/test_census_completeness.py` asserts set equality.

## 2. Oracle class enumeration — UNSOUND method, correct answer by cancellation

**Matched:** `grep -rn '^class .*(' oracles/`, reported as 64.
**Silently fails on:** class definitions without parentheses.
**Occurs:** yes — 5 of them (`judge.py:37,123`; `models.py:9,20,30`).

Re-derived by AST:

| | value |
|---|---:|
| total classes in `oracles/` | **68** |
| classes defining `evaluate()` | **60** |
| `grep '^class '` | 68 |
| `grep '^class .*('` | **63** |

The census stated "64 grep hits minus 4 non-Oracle helpers = 60". The grep actually
returns 63, and there are **8** classes without `evaluate()`, not 4. The correct
derivation is 68 − 8 = 60. **The published 60 is correct; both steps of its stated
derivation were wrong, and the errors cancelled.**

## 3. Problem -> oracle mapping — regex, but AST finds no disagreement

**Matched:** `self.mitigation_oracle\s*=\s*(.*)$`, taking the last assignment per class.
**Silently fails on:** conditional reassignment, where the last textual assignment is not
the one taken.
**Occurs:** once — `IncorrectPortAssignment` assigns at `:51` then reassigns at `:58`
under `if unschedulable`. This was already handled by hand.

AST re-derivation: **99** problem classes assign `self.mitigation_oracle`; exactly **one**
has multiple assignments (the case above). **No row where AST and the previous method
disagree.**

## 4. Perturbed resource kinds — UNSOUND, and it feeds the verdict

This is the highest-impact unsound step, because `perturbed_kinds` is an input to
ADEQUATE / BLIND / UNCERTAIN.

**Matched:** a 17-entry regex table (`Service`, `Deployment`, `ConfigMap`, `Node`, …)
against the **entire text of the problem class**, then unioned with the same table run
against the resolved `inject_*` method body.

**Silently fails on / falsely fires on:** the class body includes docstrings, comments,
`print()` strings and the `root_cause` natural-language description. A `root_cause`
sentence such as *"The Kubernetes Service `user-service` has been deleted…"* matches the
`Service` pattern as strongly as an actual `kubectl delete service` call. The classifier
cannot distinguish a resource that is **mutated** from one merely **mentioned**.

**Occurs — quantified.** Matching the same table against the class body with all string
literals and docstrings removed:

| | matches |
|---|---:|
| full class text (census method) | **114** |
| code identifiers only | **48** |
| files with at least one kind matched **only** in prose | **52 of 100** |

Neither number is the truth: stripping every literal also removes genuine evidence,
because SREGym injects via `kubectl.exec_command("kubectl delete service …")` — a string.
The correct surface is *command strings and API call names inside the resolved `inject_*`
method*, excluding problem-class prose entirely.

A corrected classifier built on that basis reproduces the ground-truth case
(`inject_missing_service` -> `Service`, `Pod`; restarts pods; waits for stability) but
resolves a kind set for only **33 of 56** `inject_*` methods, because many mutate through
helpers (`patch_service`, `_write_yaml_to_file`) rather than literal commands.

**Consequence, stated plainly.** The `perturbed_kinds` column is **not** established to
the standard of the rest of the census. It was spot-checked on three problems
(`missing_service`, `target_port`, `sidecar_port_conflict`) and PAPER_NUMBERS §9.7 already
flagged it. This audit converts that flag into a measured bound: **up to 52 of 100 problem
files have at least one kind attributed from prose rather than from code.**

**What this does and does not put at risk.** The six BLIND rows and the five newly added
rows were each hand-verified against injector source for this audit, so the headline is
not exposed. The 32 UNCERTAIN rows are unaffected — they are UNCERTAIN precisely because
no kind resolved. The exposure is concentrated in the ADEQUATE rows that were classified
solely on a prose-matched kind and never hand-checked; those are not individually
identified here, and that is a known gap.

## 5. `restarts_pods` / `waits_stability` — regex, spot-checked only

**Matched:** `delete pods --all|delete_collection_namespaced_pod` and
`wait_for_stable|wait_for_ready` over class body plus injector body.
**Silently fails on:** a restart issued through a helper, or a wait under a different
name.
**Occurs:** not established. The corrected injector-scoped classifier reproduces both
flags for `inject_missing_service`; no exhaustive cross-check was performed.

## 6. Oracle observation surfaces — call graph sound, patterns unverified

The `evaluate()` call graph itself was derived **by AST** (walking `self.*` helper
references transitively), which is sound. The surface labels were then assigned by regex
over the lines of that graph. Those patterns were not independently validated, though two
were corrected during the census (`exec_command` is a host subprocess, not a pod exec;
two `MitigationOracle` subclasses do not inherit the parent sweep).

## 7. `fault_type` extraction — UNSOUND, same class of defect as #1

**Matched:** `fault_type\s*=\s*["\'](\w+)["\']`. `\w` excludes `-`.
**Occurs:** yes — 2 of 29 distinct fault-type literals are hyphenated: `tt-feat-22`,
`tt-feat-17`.

Incidental finding while checking this: `fault/base.py:58-65` dispatches by
`getattr(self, f"inject_{fault_type}")`, so a hyphenated fault type can never resolve to
a Python attribute and would print "Unknown fault type". The TrainTicket problems override
`_inject` (`inject_tt.py:208`), so this is **not** established as a live bug and is
recorded as an open question, not a finding.

---

## The generalisable lesson

Every defect above shares one shape: a pattern matched, produced a plausible number, and
**nothing cross-checked it against an independent source**. The registry undercount
survived because no test compared the census to the registry; the oracle-count error
survived because two mistakes cancelled; the perturbed-kind contamination survives to this
day in rows nobody hand-checked.

The remedy adopted is not "write better regexes" but **derive from the runtime object or
the AST, and assert set equality against an independent source**. `generate_census.py` and
`tests/test_census_completeness.py` implement that for enumeration. The
`perturbed_kinds` column has no such check and remains the weakest column in the census.

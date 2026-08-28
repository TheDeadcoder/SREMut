# R2 Part B — Closing the oracle-count derivation

Read-only against SREGym. AST-derived. Completed inside the 30-minute cap.
Date: 2026-08-28.

## The answer

**60 is wrong. The correct number is 59.**

There are **59 concrete Oracle subclasses**, and **all 59 define `evaluate()`**.
Zero inherit it. Every published route to "60" counts the abstract base class
`Oracle` itself.

## AST counts

Derivation script: scratchpad `oracle_count.py`; walks every `ast.ClassDef` under
`sregym/`, resolves base names transitively, and tests for a `FunctionDef` named
`evaluate` in the class body (not inherited).

| quantity | AST |
|---|---:|
| `ClassDef` nodes anywhere in `sregym/` | 247 |
| classes transitively deriving `Oracle` (whole tree) | **59** |
| ... of those defining `evaluate()` in their own body | **59** |
| ... of those *not* defining `evaluate()` | **0** |
| `ClassDef` nodes in `conductor/oracles/` | 68 |
| ... deriving `Oracle` | 59 |
| ... not deriving `Oracle` (helpers) | 8 |
| Oracle-derived classes *outside* `conductor/oracles/` | **0** |

The eight helper classes in `conductor/oracles/`, all under `llm_as_a_judge/`:

| class | cite | base |
|---|---|---|
| `LLMJudge` | `judge.py:37` | — |
| `DiagnosisJudge` | `judge.py:123` | — |
| `JudgmentResult` | `judge.py:32` | `StrEnum` |
| `ChecklistParseError` | `judge.py:24` | `Exception` |
| `JudgeParseError` | `judge.py:28` | `Exception` |
| `QuestionResult` | `models.py:9` | — |
| `DimensionResult` | `models.py:20` | — |
| `JudgmentReport` | `models.py:30` | — |

`Oracle` itself is at `conductor/oracles/base.py:6`. Its `evaluate` is decorated
`@abstractmethod` (`base.py:19-22`) — a declaration with a bare `pass`, not an
implementation. Counting it as "an oracle class with `evaluate()`" is the error.

One further fact, relevant to any use of 59 as a denominator: exactly one of the 59,
`MitigationOracle` (`mitigation.py:11`), is *both* concrete and an intermediate base.
It is used unsubclassed by `MissingService`, so all 59 are attachable classes; none is
a pure abstract intermediate.

## The greps, and what each one misses

Every pattern run against `conductor/oracles/`, recursive:

| # | pattern | hits | what it does |
|---:|---|---:|---|
| P1 | `^class ` | 68 | every class at column 0 — equals the AST `ClassDef` count, since no class in the tree is indented (`^\s+class ` returns 0 hits) |
| P2 | `^class .*(` | **63** | **the pattern the census actually used** |
| P3 | `^\s*class ` | 68 | same as P1 |
| P4 | `^\s*def evaluate` | **65** | prefix-matches, see below |
| P5 | `^class .*Oracle` | **60** | the only pattern that yields 60 |
| P6 | `^class .*(.*Oracle.*)` | 59 | direct-or-named Oracle base |

- **P2 misses 5.** It requires a `(`, so it drops the five base-less classes
  `LLMJudge`, `DiagnosisJudge`, `QuestionResult`, `DimensionResult`, `JudgmentReport`.
  63 + 5 = 68, which closes against the AST count exactly.
- **P4 over-counts by 5.** `^\s*def evaluate` prefix-matches `def evaluatePods`, which
  occurs 5 times (the TiDB operator oracles). Word-bounded `def evaluate` gives 60 —
  the 59 subclasses plus the abstract declaration in `base.py`.
- **P5 over-counts by 1.** `^class .*Oracle` matches `class Oracle(ABC):` at
  `base.py:6` on the class *name*. This is the origin of the published 60.

## Reconciled arithmetic

The census stated **"64 grep hits minus 4 non-Oracle helpers = 60"**. Three separate
errors, and they do not cancel:

1. The grep returns **63**, not 64. No pattern tried returns 64; it is a
   transcription or hand-tally error with no derivation behind it.
2. There are **8** non-Oracle helpers, not 4.
3. `63 - 8 = 55`, which is the arithmetic the user flagged. 55 is also not the answer,
   because P2 had already dropped 5 of those very helpers — subtracting all 8 from a
   count that contains only 3 of them double-subtracts 5.

`METHOD_AUDIT.md` row 2 attempted a repair as **"68 - 8 = 60, published 60 correct by
cancellation."** That repair is **also wrong**: it omits the `Oracle` ABC from the
subtraction. The audit's verdict "final 60 correct by cancellation" is withdrawn.

The correct chain:

```
  68   ClassDef nodes in conductor/oracles/            (AST, recursive)
-  8   helper classes not deriving from Oracle         (llm_as_a_judge/)
-  1   Oracle(ABC) itself, base.py:6, evaluate is @abstractmethod
= 59   concrete Oracle subclasses
  59   of which define evaluate() in their own body    (AST; 0 inherit it)
```

Cross-check from the other direction: P2 (63) + 5 base-less classes = 68; and
P5 (60) - 1 ABC = 59. Both close.

## What changes downstream

Every published use of 60 as an oracle-class count or denominator is off by one:

- `PAPER_NUMBERS.md:311` — "Oracle classes with `evaluate()` | 60 | 60" -> **59**.
- `PAPER_NUMBERS.md:514` — "18/60 oracle classes reach TCP/HTTP" -> **18/59**.
- `METHOD_AUDIT.md:17,54,56` — row 2 verdict and its reconciliation, as above.

None of these is load-bearing for the false-acceptance result, which rests on the
behaviour of one oracle, not on how many exist. The correction is a denominator fix.

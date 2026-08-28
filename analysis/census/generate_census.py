"""Regenerate the SREGym verifier-adequacy census from a single ledger.

Design rules, adopted after the R1 root-cause (see METHOD_AUDIT.md):

  1. The problem_id list comes from the RUNTIME registry
     (ProblemRegistry().PROBLEM_REGISTRY), never from a regex over a line range.
     The prior census used r'"([a-z0-9_]+)":' whose character class excluded '-',
     silently dropping the five hyphenated IDs and yielding 118 instead of 123.
  2. The generator FAILS LOUDLY if the registry ID set and the census ID set are
     not equal. Silence was the prior defect: nothing compared the two.
  3. Every count, percentage and table in CENSUS.md / PAPER_NUMBERS section 6 is
     emitted from this one ledger, so they cannot drift apart.

Read-only with respect to SREGym. Constructing ProblemRegistry() reads the
kubeconfig file and builds API client objects but makes NO cluster call; the
cluster call lives in get_problem_instance() (registry.py:365), which is never
invoked here.

Usage:  python generate_census.py [--check]
        --check  verify only; do not write coverage.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

CENSUS_DIR = Path(__file__).resolve().parent
COVERAGE = CENSUS_DIR / "coverage.csv"
SREGYM = Path("/home/sakibbuet2k19/sremut/SREGym")

FUNCTIONAL = {"dns", "tcp_or_http", "workload", "prometheus", "pod_exec"}


def registry_ids() -> set[str]:
    """Runtime registry IDs. Requires SREGym on sys.path."""
    if str(SREGYM) not in sys.path:
        sys.path.insert(0, str(SREGYM))
    from sregym.conductor.problems.registry import ProblemRegistry

    reg = ProblemRegistry()
    keys = list(reg.PROBLEM_REGISTRY)
    ids = reg.get_problem_ids()
    if set(keys) != set(ids):
        raise SystemExit(
            "FATAL: PROBLEM_REGISTRY and get_problem_ids() disagree.\n"
            "  get_problem_ids() falls back to tasklist.yml at registry.py:388-390 "
            "when that file exists; the census denominator is only the registry when "
            "it does not.\n"
            f"  registry={len(keys)} get_problem_ids={len(ids)}"
        )
    return set(keys)


def load_rows() -> list[dict]:
    with COVERAGE.open() as fh:
        return list(csv.DictReader(fh))


def assert_set_equal(reg: set[str], rows: list[dict]) -> None:
    """Loud failure on any missing, extra or duplicate ID."""
    ids = [r["problem_id"] for r in rows]
    dupes = sorted(k for k, v in Counter(ids).items() if v > 1)
    missing = sorted(reg - set(ids))
    extra = sorted(set(ids) - reg)
    problems = []
    if missing:
        problems.append(f"{len(missing)} registry IDs absent from the census: {missing}")
    if extra:
        problems.append(f"{len(extra)} census IDs absent from the registry: {extra}")
    if dupes:
        problems.append(f"{len(dupes)} duplicate census IDs: {dupes}")
    if len(ids) != len(set(ids)):
        problems.append(f"row count {len(ids)} != unique ID count {len(set(ids))}")
    if problems:
        raise SystemExit("FATAL: census/registry mismatch\n  " + "\n  ".join(problems))


def ledger(rows: list[dict]) -> dict:
    """The single source of every published number."""
    n = len(rows)
    verdict = Counter(r["verdict"] for r in rows)
    kind = Counter(r["oracle_kind"] for r in rows)
    bare = [r for r in rows if r["oracle_kind"] == "BARE_GENERIC"]
    blind = [r for r in rows if r["verdict"] == "BLIND"]

    def truthy(r, col):
        return str(r.get(col, "")).strip().lower() == "true"

    restarts_or_waits = [r for r in rows if truthy(r, "restarts_pods") or truthy(r, "waits_stability")]
    pd_only = [r for r in rows if truthy(r, "pods_and_deployments_only")]
    shape = [r for r in restarts_or_waits if truthy(r, "pods_and_deployments_only")]

    blind_by_kind = Counter(r["oracle_kind"] for r in blind)
    blind_by_resource = Counter()
    for r in blind:
        for k in r["perturbed_kinds"].split(";"):
            if k:
                blind_by_resource[k] += 1

    return {
        "n": n,
        "verdict": dict(verdict),
        "oracle_kind": dict(kind),
        "bare": {
            "total": len(bare),
            "BLIND": sum(1 for r in bare if r["verdict"] == "BLIND"),
            "ADEQUATE": sum(1 for r in bare if r["verdict"] == "ADEQUATE"),
            "UNCERTAIN": sum(1 for r in bare if r["verdict"] == "UNCERTAIN"),
        },
        "blind_ids": sorted(r["problem_id"] for r in blind),
        "blind_by_oracle_kind": dict(blind_by_kind),
        "blind_by_resource": dict(blind_by_resource),
        "restarts_or_waits": len(restarts_or_waits),
        "pods_and_deployments_only": len(pd_only),
        "structural_shape": sorted(
            ({"problem_id": r["problem_id"], "verdict": r["verdict"],
              "perturbed_kinds": r["perturbed_kinds"],
              "restarts_pods": r["restarts_pods"], "waits_stability": r["waits_stability"]}
             for r in shape),
            key=lambda x: (x["verdict"], x["problem_id"]),
        ),
        "pct": {
            "blind_of_n": round(100 * len(blind) / n, 4),
            "shape_of_n": round(100 * len(shape) / n, 4),
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="verify only")
    args = ap.parse_args()

    reg = registry_ids()
    rows = load_rows()
    assert_set_equal(reg, rows)          # loud failure is the point
    led = ledger(rows)
    led["registry_count"] = len(reg)

    out = CENSUS_DIR / "ledger.json"
    if not args.check:
        out.write_text(json.dumps(led, indent=2, sort_keys=True) + "\n")

    print(f"registry IDs      : {len(reg)}")
    print(f"census rows       : {led['n']}   set-equal: True")
    print(f"verdicts          : {led['verdict']}")
    print(f"oracle kinds      : {led['oracle_kind']}")
    print(f"bare-generic pool : {led['bare']}")
    print(f"BLIND             : {len(led['blind_ids'])} ({led['pct']['blind_of_n']} %)")
    print(f"structural shape  : {len(led['structural_shape'])} ({led['pct']['shape_of_n']} %)")
    if not args.check:
        print(f"ledger written    : {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Census completeness: the census must cover exactly the runtime registry.

This test exists because the census silently undercounted for weeks. Its parser
used r'"([a-z0-9_]+)":' over a line range; that character class excludes '-', so
the five hyphenated problem IDs never matched and the census reported 118 of 123.
Nothing compared the census against the registry, so nothing failed.

The test is deliberately strict: missing, extra, or duplicate IDs each fail it.
"""

from __future__ import annotations

import csv
import sys
from collections import Counter
from pathlib import Path

import pytest

SREGYM = Path("/home/sakibbuet2k19/sremut/SREGym")
COVERAGE = Path(__file__).resolve().parents[1] / "analysis" / "census" / "coverage.csv"


def _registry_ids() -> set[str]:
    """Runtime registry IDs.

    Constructing ProblemRegistry() reads the kubeconfig and builds API client
    objects; it makes no cluster call. The cluster call is in
    get_problem_instance() (registry.py:365), which is never invoked here.
    """
    if str(SREGYM) not in sys.path:
        sys.path.insert(0, str(SREGYM))
    from sregym.conductor.problems.registry import ProblemRegistry

    return set(ProblemRegistry().PROBLEM_REGISTRY)


def _census_ids() -> list[str]:
    with COVERAGE.open() as fh:
        return [r["problem_id"] for r in csv.DictReader(fh)]


@pytest.fixture(scope="module")
def ids():
    return _registry_ids(), _census_ids()


def test_no_registry_id_missing_from_census(ids):
    reg, cen = ids
    missing = sorted(reg - set(cen))
    assert not missing, f"{len(missing)} registry IDs absent from the census: {missing}"


def test_no_census_id_absent_from_registry(ids):
    reg, cen = ids
    extra = sorted(set(cen) - reg)
    assert not extra, f"{len(extra)} census IDs absent from the registry: {extra}"


def test_census_ids_are_unique(ids):
    _, cen = ids
    dupes = sorted(k for k, v in Counter(cen).items() if v > 1)
    assert not dupes, f"duplicate census IDs: {dupes}"


def test_row_count_equals_unique_id_count(ids):
    _, cen = ids
    assert len(cen) == len(set(cen)), f"row count {len(cen)} != unique IDs {len(set(cen))}"


def test_sets_are_equal(ids):
    reg, cen = ids
    assert set(cen) == reg, (
        f"census ({len(set(cen))}) and registry ({len(reg)}) differ; "
        f"missing={sorted(reg - set(cen))} extra={sorted(set(cen) - reg)}"
    )


def test_hyphenated_ids_are_present(ids):
    """Regression guard for the specific defect that caused the undercount."""
    reg, cen = ids
    hyphenated = sorted(i for i in reg if "-" in i)
    assert hyphenated, "expected hyphenated registry IDs; the guard would be vacuous"
    missing = sorted(set(hyphenated) - set(cen))
    assert not missing, f"hyphenated IDs missing from the census: {missing}"

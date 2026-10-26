"""Inventory checks: case files and tests match one to one, and the §1 floors hold.

The G2 count is read from the case files. A case counts toward a floor when its
status is ``active`` or ``xfail`` (an executable check exists); ``stub`` never
counts. ``xfail`` cases are observed gaps: they count for G2, and fail G3.
"""

from __future__ import annotations

import inspect
import json
from collections import Counter

import pytest

from . import test_kane_runner, test_maintain_docs, test_reconcile_spec
from ._harness import CASES, CATEGORIES, COMPONENTS, QUOTAS, REQUIRED_THEMES, RUBRICS, STATUSES

MODULES = {"maintain_docs": test_maintain_docs, "reconcile_spec": test_reconcile_spec, "kane_runner": test_kane_runner}
PREFIX = {"maintain_docs": "MD", "reconcile_spec": "RS", "kane_runner": "KR"}
COUNTED = {"active", "xfail"}


def counted(component: str) -> Counter:
    return Counter(c["category"] for c in CASES.values() if c["component"] == component and c["status"] in COUNTED)


def shortfall(component: str) -> dict[str, tuple[int, int]]:
    have = counted(component)
    return {cat: (have[cat], need) for cat, need in QUOTAS[component].items() if have[cat] < need}


def test_every_case_file_is_well_formed():
    for cid, c in CASES.items():
        assert c["_path"].stem == cid and c["_path"].parent.name == c["component"], cid
        assert cid.startswith(f"{PREFIX[c['component']]}-{c['category'] if c['category'] != 'CONTROL' else 'CTRL'}-"), cid
        assert c["component"] in COMPONENTS and c["category"] in CATEGORIES and c["status"] in STATUSES, cid
        assert c["rubrics"] and set(c["rubrics"]) <= set(RUBRICS), cid
        assert isinstance(c["input"], dict) and isinstance(c["expected"], dict) and c["title"], cid
        if c["status"] != "active":
            assert c.get("gap"), f"{cid}: {c['status']} needs a gap"


def test_case_files_and_tests_match_one_to_one():
    bound = Counter()
    for component, module in MODULES.items():
        for _, fn in inspect.getmembers(module, inspect.isfunction):
            cid = getattr(fn, "redteam_case", None)
            if cid is not None:
                assert CASES[cid]["component"] == component, cid
                bound[cid] += 1
    assert {cid for cid, n in bound.items() if n > 1} == set()
    assert set(bound) == set(CASES)


def test_maintain_docs_has_a_positive_control():
    controls = [c for c in CASES.values() if c["component"] == "maintain_docs" and c["category"] == "CONTROL"]
    assert [c["status"] for c in controls] == ["active"]


@pytest.mark.parametrize("component", ["maintain_docs", "reconcile_spec"])
def test_g2_floor_met(component):
    assert shortfall(component) == {}


@pytest.mark.xfail(strict=True, reason="Kane runner is below its §1 floor: live-run cases are stubs")
def test_g2_floor_met_kane_runner():
    assert shortfall("kane_runner") == {}


@pytest.mark.parametrize("component", COMPONENTS)
def test_required_themes_have_an_executable_case(component):
    themes = {c["theme"] for c in CASES.values() if c["component"] == component and c["status"] in COUNTED}
    assert set(REQUIRED_THEMES[component]) - themes == set()


def test_inventory_summary(capsys):
    """Print the honest per component x category count (-s to see it)."""
    rows = {}
    for component in COMPONENTS:
        for cat in (*QUOTAS[component], "CONTROL"):
            statuses = Counter(c["status"] for c in CASES.values() if c["component"] == component and c["category"] == cat)
            if statuses or cat in QUOTAS[component]:
                rows[f"{component}/{cat}"] = {"floor": QUOTAS[component].get(cat), **{s: statuses[s] for s in STATUSES}}
    print(json.dumps(rows, indent=1))
    assert rows

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

from . import (
    test_kane_runner,
    test_kane_runner_captures,
    test_kane_runner_depth,
    test_maintain_docs,
    test_maintain_docs_depth,
    test_maintain_docs_store,
    test_reconcile_spec,
    test_reconcile_spec_depth,
    test_reconcile_spec_sources,
    test_syberwork_bridge,
)
from ._harness import CASES, CATEGORIES, COMPONENTS, QUOTAS, REQUIRED_THEMES, RUBRICS, STATUSES

# A component's cases may be split across modules; every module is scanned for @case bindings.
MODULES = {
    "maintain_docs": (test_maintain_docs, test_maintain_docs_depth, test_maintain_docs_store),
    "reconcile_spec": (test_reconcile_spec, test_reconcile_spec_depth, test_reconcile_spec_sources),
    "kane_runner": (test_kane_runner, test_kane_runner_captures, test_kane_runner_depth),
    "syberwork_bridge": (test_syberwork_bridge,),
}
PREFIX = {"maintain_docs": "MD", "reconcile_spec": "RS", "kane_runner": "KR", "syberwork_bridge": "BR"}
COUNTED = {"active", "xfail"}

# SyberWork is not in this repository. Only the categories with an Attest-side
# check are filled here; the rest need the SyberWork bridge itself (N3/N4).
BRIDGE_ATTEST_SIDE = ("PI", "AB")
BRIDGE_ATTEST_SIDE_THEMES = ("receipt_as_reconciliation_proof", "caller_success_or_evidence_fields")
BRIDGE_BELOW_FLOOR = "SyberWork bridge is below its §1 floor: DS, SIM, SCH, COST and REC need the SyberWork side"


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
    for component, modules in MODULES.items():
        for module in modules:
            for _, fn in inspect.getmembers(module, inspect.isfunction):
                cid = getattr(fn, "redteam_case", None)
                if cid is not None and fn.__module__ == module.__name__:
                    assert CASES[cid]["component"] == component, cid
                    bound[cid] += 1
    assert {cid for cid, n in bound.items() if n > 1} == set()
    assert set(bound) == set(CASES)


def test_maintain_docs_has_a_positive_control():
    controls = [c for c in CASES.values() if c["component"] == "maintain_docs" and c["category"] == "CONTROL"]
    assert [c["status"] for c in controls] == ["active"]


@pytest.mark.parametrize("component", [
    "maintain_docs",
    "reconcile_spec",
    "kane_runner",
    pytest.param("syberwork_bridge", marks=pytest.mark.xfail(strict=True, reason=BRIDGE_BELOW_FLOOR)),
])
def test_g2_floor_met(component):
    assert shortfall(component) == {}


def test_g2_floor_met_syberwork_bridge_attest_side():
    assert {cat: v for cat, v in shortfall("syberwork_bridge").items() if cat in BRIDGE_ATTEST_SIDE} == {}


def themes(component: str) -> set[str]:
    return {c["theme"] for c in CASES.values() if c["component"] == component and c["status"] in COUNTED}


@pytest.mark.parametrize("component", [
    "maintain_docs",
    "reconcile_spec",
    "kane_runner",
    pytest.param("syberwork_bridge", marks=pytest.mark.xfail(strict=True, reason=BRIDGE_BELOW_FLOOR)),
])
def test_required_themes_have_an_executable_case(component):
    assert set(REQUIRED_THEMES[component]) - themes(component) == set()


def test_bridge_attest_side_themes_have_an_executable_case():
    assert set(BRIDGE_ATTEST_SIDE_THEMES) <= set(REQUIRED_THEMES["syberwork_bridge"])
    assert set(BRIDGE_ATTEST_SIDE_THEMES) - themes("syberwork_bridge") == set()


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

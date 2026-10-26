"""FACTORY G2 quota floor: the gate's counting rules and its fail-closed behaviour.

The counting tests use small synthetic floors so each rule is checked on its own.
The real inventory is counted by ``scripts/g2-quota-floor.py`` in the G2 job.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]

_spec = importlib.util.spec_from_file_location("g2_quota_floor", ROOT / "scripts" / "g2-quota-floor.py")
g2 = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = g2  # dataclasses look the module up by name
_spec.loader.exec_module(g2)

FLOORS = {"comp": {"PI": 2, "AB": 1}}
THEMES = {"comp": ("t_pi",)}
BOTH = g2.COUNT_MODES["active+xfail"]
ACTIVE = g2.COUNT_MODES["active"]


def mk(cid: str, category: str, status: str = "active", theme: str = "t_pi", component: str = "comp") -> dict:
    return {"id": cid, "component": component, "category": category, "status": status, "theme": theme}


def run(cases: list[dict], outcomes: dict[str, list[str]], counted=BOTH, pending_cells=None, pending_themes=None):
    return g2.count(
        {c["id"]: c for c in cases},
        outcomes,
        counted,
        floors=FLOORS,
        themes=THEMES,
        pending_cells=pending_cells or {},
        pending_themes=pending_themes or {},
    )


MET = [mk("C-PI-01", "PI"), mk("C-PI-02", "PI"), mk("C-AB-01", "AB", theme="t_ab")]
MET_OUT = {"C-PI-01": ["passed"], "C-PI-02": ["passed"], "C-AB-01": ["passed"]}


def cell(report, category):
    return next(c for c in report.cells if c.category == category)


# -- pinned floors ---------------------------------------------------------------------


def test_pinned_floors_match_factory_section_1_totals_and_the_harness():
    assert {k: sum(v.values()) for k, v in g2.FLOORS.items()} == g2.MIN_TOTAL
    assert g2.MIN_TOTAL == {"maintain_docs": 21, "reconcile_spec": 18, "kane_runner": 20, "syberwork_bridge": 22}
    assert g2.check_harness_floors() == []


def test_lowered_harness_quota_fails_the_gate(monkeypatch):
    from tests.redteam import _harness

    lowered = {k: dict(v) for k, v in _harness.QUOTAS.items()}
    lowered["kane_runner"]["ISO"] = 1
    monkeypatch.setattr(_harness, "QUOTAS", lowered)
    assert any("QUOTAS differ" in p for p in g2.check_harness_floors())


def test_pending_exemptions_cover_only_the_syberwork_side_of_the_bridge():
    assert set(g2.PENDING_CELLS) == {("syberwork_bridge", c) for c in ("DS", "SIM", "SCH", "COST", "REC")}
    assert {k[0] for k in g2.PENDING_THEMES} == {"syberwork_bridge"}
    assert ("syberwork_bridge", "receipt_as_reconciliation_proof") not in g2.PENDING_THEMES
    assert ("syberwork_bridge", "caller_success_or_evidence_fields") not in g2.PENDING_THEMES


def test_real_inventory_loads_and_has_every_enforced_component():
    cases = g2.load_cases()
    enforced = {c for c, cats in g2.FLOORS.items() if any((c, cat) not in g2.PENDING_CELLS for cat in cats)}
    assert enforced <= {c["component"] for c in cases.values()}


# -- counting rules --------------------------------------------------------------------


def test_floor_met_passes():
    report = run(MET, MET_OUT)
    assert report.ok, report.problems
    assert cell(report, "PI").counted == 2


def test_below_floor_fails():
    report = run(MET[1:], {k: v for k, v in MET_OUT.items() if k != "C-PI-01"})
    assert not report.ok
    assert "comp/PI: 1 counted < floor 2" in report.problems


def test_empty_inventory_fails_every_cell():
    report = run([], {})
    assert {p.split(":")[0] for p in report.problems} >= {"comp/PI", "comp/AB"}


def test_stub_never_counts():
    cases = [*MET[:1], mk("C-PI-02", "PI", status="stub"), MET[2]]
    report = run(cases, {**MET_OUT, "C-PI-02": ["skipped"]})
    assert cell(report, "PI").counted == 1 and not report.ok


def test_xfail_counts_by_default_and_not_under_active_only():
    cases = [*MET[:1], mk("C-PI-02", "PI", status="xfail"), MET[2]]
    outcomes = {**MET_OUT, "C-PI-02": ["xfailed"]}
    assert run(cases, outcomes).ok
    strict = run(cases, outcomes, counted=ACTIVE)
    assert cell(strict, "PI").counted == 1 and not strict.ok


@pytest.mark.parametrize("status,outcome", [
    ("active", ["failed"]),
    ("active", ["skipped"]),
    ("active", ["not run"]),
    ("active", ["passed", "failed"]),
    ("xfail", ["failed"]),  # strict xfail that passed (XPASS) is reported failed
    ("xfail", ["passed"]),
])
def test_case_whose_test_does_not_end_as_its_status_says_is_not_counted(status, outcome):
    cases = [*MET[:1], mk("C-PI-02", "PI", status=status), MET[2]]
    report = run(cases, {**MET_OUT, "C-PI-02": outcome})
    assert "C-PI-02" in report.not_counted and not report.ok


def test_case_with_no_bound_test_is_not_counted():
    report = run(MET, {k: v for k, v in MET_OUT.items() if k != "C-PI-02"})
    assert "no collected test" in report.not_counted["C-PI-02"] and not report.ok


def test_test_bound_to_an_unknown_case_fails():
    report = run(MET, {**MET_OUT, "C-PI-99": ["passed"]})
    assert any(p.startswith("C-PI-99:") for p in report.problems)


def test_missing_theme_fails():
    cases = [mk("C-PI-01", "PI", theme="other"), mk("C-PI-02", "PI", theme="other"), MET[2]]
    report = run(cases, MET_OUT)
    assert report.themes_missing == {"comp": ["t_pi"]} and not report.ok


def test_theme_needs_a_counted_case_not_a_stub():
    cases = [mk("C-PI-01", "PI", theme="x"), mk("C-PI-02", "PI", theme="x"), MET[2], mk("C-PI-03", "PI", "stub")]
    report = run(cases, {**MET_OUT, "C-PI-03": ["skipped"]})
    assert report.themes_missing == {"comp": ["t_pi"]}


# -- pending exemptions ----------------------------------------------------------------


def test_pending_cell_below_floor_is_reported_not_enforced():
    outcomes = {k: v for k, v in MET_OUT.items() if k != "C-PI-02"}
    report = run(MET[:1] + MET[2:], outcomes, pending_cells={("comp", "PI"): "later"})
    assert report.ok, report.problems
    assert cell(report, "PI").pending == "later" and not cell(report, "PI").met


def test_pending_cell_that_is_met_fails_until_removed():
    report = run(MET, MET_OUT, pending_cells={("comp", "PI"): "later"})
    assert any("pending exemption is met" in p for p in report.problems)


def test_pending_theme_that_is_met_fails_until_removed():
    report = run(MET, MET_OUT, pending_themes={("comp", "t_pi"): "later"})
    assert any("theme t_pi: pending exemption is met" in p for p in report.problems)


def test_pending_exemption_for_an_unknown_cell_fails():
    report = run(MET, MET_OUT, pending_cells={("comp", "REC"): "later"})
    assert any("names no §1 cell" in p for p in report.problems)


# -- case files ------------------------------------------------------------------------


def write_case(root: Path, data: dict, name: str | None = None) -> None:
    folder = root / data["component"]
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name or data['id']}.json").write_text(json.dumps(data), encoding="utf-8")


def test_load_cases_reads_case_files(tmp_path):
    write_case(tmp_path, mk("C-PI-01", "PI"))
    assert set(g2.load_cases(tmp_path)) == {"C-PI-01"}


@pytest.mark.parametrize("data,name,match", [
    ({**mk("C-PI-01", "PI"), "status": "pending"}, None, "unknown status"),
    (mk("C-PI-01", "PI"), "C-PI-02", "does not match"),
    ({k: v for k, v in mk("C-PI-01", "PI").items() if k != "category"}, None, "category"),
])
def test_load_cases_rejects_malformed_files(tmp_path, data, name, match):
    write_case(tmp_path, data, name)
    with pytest.raises(ValueError, match=match):
        g2.load_cases(tmp_path)


def test_load_cases_rejects_duplicate_ids(tmp_path):
    write_case(tmp_path, mk("C-PI-01", "PI"))
    write_case(tmp_path, mk("C-PI-01", "PI", component="other"))
    with pytest.raises(ValueError, match="duplicate"):
        g2.load_cases(tmp_path)


# -- pytest outcome collection ---------------------------------------------------------


def report(when: str, outcome: str, **extra) -> SimpleNamespace:
    return SimpleNamespace(
        nodeid="t::x", when=when, failed=outcome == "failed", skipped=outcome == "skipped", **extra
    )


@pytest.mark.parametrize("reports,expected", [
    ([report("setup", "passed"), report("call", "passed"), report("teardown", "passed")], "passed"),
    ([report("setup", "passed"), report("call", "skipped", wasxfail="gap"), report("teardown", "passed")], "xfailed"),
    ([report("setup", "skipped"), report("teardown", "passed")], "skipped"),
    ([report("setup", "passed"), report("call", "failed"), report("teardown", "passed")], "failed"),
    ([report("setup", "passed"), report("call", "passed"), report("teardown", "failed")], "failed"),
    ([report("setup", "failed"), report("teardown", "passed")], "failed"),
])
def test_collector_maps_phase_reports_to_one_outcome(reports, expected):
    collector = g2._Collector()
    collector.case_of["t::x"] = "C-PI-01"
    for r in reports:
        collector.pytest_runtest_logreport(r)
    assert collector.by_case() == {"C-PI-01": [expected]}


def test_collector_reports_a_collected_test_that_never_ran():
    collector = g2._Collector()
    collector.case_of["t::x"] = "C-PI-01"
    assert collector.by_case() == {"C-PI-01": ["not run"]}

#!/usr/bin/env python3
"""FACTORY G2 quota floor: red-team cases per component x category must meet the §1 minimums.

The gate reads the case files under ``tests/redteam/cases/<component>/<ID>.json``,
runs ``tests/redteam`` once in-process, and counts a case only when:

* its status is a counted status (``active`` and ``xfail`` by default, see
  ``--count``; ``stub`` never counts), and
* pytest collected a test bound to it with ``@case(ID)``, and every such test
  ended the way the status says: ``active`` passed, ``xfail`` xfailed (strict).
  A case whose test is missing, skipped, errored, failed or xpassed is not counted.

It then fails closed when any enforced cell (component x category, from
``FLOORS`` below) is under its floor, when a required §1 theme has no counted
case, when the floors in ``tests/redteam/_harness.py`` differ from ``FLOORS``,
or when a ``PENDING`` exemption is already met (remove it so the cell is
enforced). A tip with no red-team inventory fails: every cell is 0.

A green result means the listed cells have at least that many executable cases.
It says nothing about whether the cases pass their rubrics (that is G3) and it
is not a coverage figure.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CASES_DIR = ROOT / "tests" / "redteam" / "cases"
REDTEAM_TESTS = ROOT / "tests" / "redteam"

# FACTORY-REDTEAM-AND-RUBRICS-2026-10-24 §1 minimums. Pinned here rather than read
# from the harness so a PR cannot pass G2 by lowering the harness quota; the two
# must match (check_harness_floors).
FLOORS = {
    "maintain_docs": {"PI": 4, "AB": 5, "DS": 4, "SIM": 3, "SCH": 3, "COST": 2},
    "reconcile_spec": {"PI": 4, "AB": 6, "DS": 2, "SIM": 2, "SCH": 3, "COST": 1},
    "kane_runner": {"PI": 4, "AB": 1, "DS": 3, "SIM": 3, "SCH": 2, "COST": 3, "ISO": 4},
    "syberwork_bridge": {"PI": 3, "AB": 3, "DS": 3, "SIM": 2, "SCH": 4, "COST": 1, "REC": 6},
}
MIN_TOTAL = {"maintain_docs": 21, "reconcile_spec": 18, "kane_runner": 20, "syberwork_bridge": 22}

# §1 "Required case themes": each needs at least one counted case.
THEMES = {
    "maintain_docs": (
        "protected_text_edit",
        "weaken_promise_refused",
        "reused_doc_sha256",
        "fuzzy_numeric_proof",
        "apply_after_failed_validation",
        "unresolved_required_claims",
        "path_escape",
    ),
    "reconcile_spec": (
        "weaken_requirement_refused_handoff",
        "missing_or_conflicting_authority",
        "injected_superseded_claim",
        "handoff_missing_evidence",
        "ambiguous_never_auto_resolved",
    ),
    "kane_runner": (
        "page_content_injection",
        "stale_or_wrong_app_version",
        "deadline_exceeded",
        "cleanup_in_finally",
        "no_port_kill",
        "escape_disposable_environment",
        "unbounded_retries_or_steps",
    ),
    "syberwork_bridge": (
        "receipt_as_reconciliation_proof",
        "caller_success_or_evidence_fields",
        "status_url_redirect_or_cross_origin",
        "digest_or_key_mismatch",
        "404_treated_as_no_write",
        "retry_after_effect_claim",
        "terminal_outcome_races_lookup",
        "budget_released_while_unresolved",
    ),
}

# Cells and themes reported but not enforced, each with the reason. An entry that
# is met fails the gate until it is removed, so an exemption cannot outlive its gap.
_BRIDGE = "SyberWork bridge side is not in this repository (FACTORY §3 rollout: Bridge quota with N3/N4)"
PENDING_CELLS = {("syberwork_bridge", cat): _BRIDGE for cat in ("DS", "SIM", "SCH", "COST", "REC")}
PENDING_THEMES = {
    ("syberwork_bridge", theme): _BRIDGE
    for theme in THEMES["syberwork_bridge"]
    if theme not in ("receipt_as_reconciliation_proof", "caller_success_or_evidence_fields")
}

COUNT_MODES = {"active+xfail": ("active", "xfail"), "active": ("active",)}
EXPECTED_OUTCOME = {"active": "passed", "xfail": "xfailed"}
STATUSES = ("active", "xfail", "stub")


# -- case files -----------------------------------------------------------------------


def load_cases(cases_dir: Path = CASES_DIR) -> dict[str, dict]:
    """Case id -> case dict. Malformed or duplicate files are errors, not skipped."""
    cases: dict[str, dict] = {}
    for path in sorted(cases_dir.glob("*/*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        for key in ("id", "component", "category", "status"):
            if not isinstance(data.get(key), str):
                raise ValueError(f"{path}: missing or non-string {key!r}")
        if data["id"] in cases:
            raise ValueError(f"{path}: duplicate case id {data['id']!r}")
        if path.stem != data["id"] or path.parent.name != data["component"]:
            raise ValueError(f"{path}: file path does not match id {data['id']!r} / component {data['component']!r}")
        if data["status"] not in STATUSES:
            raise ValueError(f"{path}: unknown status {data['status']!r}")
        cases[data["id"]] = data
    return cases


# -- pytest run -----------------------------------------------------------------------


class _Collector:
    """pytest plugin: case id -> outcome of every test bound to it with @case."""

    def __init__(self) -> None:
        self.case_of: dict[str, str] = {}
        self.outcome: dict[str, str] = {}
        self.collect_errors: list[str] = []

    def pytest_collectreport(self, report) -> None:
        if report.failed:
            self.collect_errors.append(report.nodeid or "<session>")

    def pytest_collection_modifyitems(self, items) -> None:
        for item in items:
            marker = item.get_closest_marker("redteam")
            if marker is not None:
                self.case_of[item.nodeid] = marker.kwargs["case_id"]

    def pytest_runtest_logreport(self, report) -> None:
        if report.nodeid not in self.case_of:
            return
        if report.failed:
            outcome = "failed"
        elif report.skipped:
            outcome = "xfailed" if hasattr(report, "wasxfail") else "skipped"
        elif report.when == "call":
            outcome = "passed"
        else:
            return
        # The worst phase wins: a failed teardown after a passed call is failed.
        if self.outcome.get(report.nodeid) not in ("failed", "skipped"):
            self.outcome[report.nodeid] = outcome

    def by_case(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = defaultdict(list)
        for nodeid, cid in self.case_of.items():
            out[cid].append(self.outcome.get(nodeid, "not run"))
        return dict(out)


def run_redteam_tests() -> tuple[dict[str, list[str]], list[str], int]:
    """Run tests/redteam once; return case outcomes, collection errors and the exit code."""
    import pytest

    collector = _Collector()
    sys.path.insert(0, str(ROOT))
    args = [str(REDTEAM_TESTS), "-q", "-rfEsxX", "-p", "no:cacheprovider", "-o", "xfail_strict=true"]
    code = pytest.main(args, plugins=[collector])
    return collector.by_case(), collector.collect_errors, int(code)


# -- counting -------------------------------------------------------------------------


@dataclass
class Cell:
    component: str
    category: str
    floor: int
    statuses: Counter
    counted: int
    pending: str | None

    @property
    def met(self) -> bool:
        return self.counted >= self.floor


@dataclass
class Report:
    cells: list[Cell]
    themes_missing: dict[str, list[str]]
    themes_pending: dict[str, list[str]]
    not_counted: dict[str, str]
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def count(
    cases: dict[str, dict],
    outcomes: dict[str, list[str]],
    counted_statuses: tuple[str, ...],
    floors: dict[str, dict[str, int]] = FLOORS,
    themes: dict[str, tuple[str, ...]] = THEMES,
    pending_cells: dict[tuple[str, str], str] = PENDING_CELLS,
    pending_themes: dict[tuple[str, str], str] = PENDING_THEMES,
) -> Report:
    problems: list[str] = []
    not_counted: dict[str, str] = {}
    counts: dict[tuple[str, str], int] = Counter()
    statuses: dict[tuple[str, str], Counter] = defaultdict(Counter)
    theme_hits: dict[str, set[str]] = defaultdict(set)

    for cid in sorted(set(outcomes) - set(cases)):
        problems.append(f"{cid}: a test is bound to it but there is no case file")

    for cid, c in sorted(cases.items()):
        key = (c["component"], c["category"])
        statuses[key][c["status"]] += 1
        if c["status"] not in counted_statuses:
            continue
        got = outcomes.get(cid)
        want = EXPECTED_OUTCOME[c["status"]]
        if not got:
            not_counted[cid] = f"status {c['status']} but no collected test is bound to it"
        elif any(o != want for o in got):
            not_counted[cid] = f"status {c['status']} expects {want}, test ended {'/'.join(sorted(set(got)))}"
        else:
            counts[key] += 1
            if c.get("theme"):
                theme_hits[c["component"]].add(c["theme"])

    cells: list[Cell] = []
    for component, cats in floors.items():
        for cat, floor in cats.items():
            reason = pending_cells.get((component, cat))
            cell = Cell(component, cat, floor, statuses[(component, cat)], counts[(component, cat)], reason)
            cells.append(cell)
            if reason is None and not cell.met:
                problems.append(f"{component}/{cat}: {cell.counted} counted < floor {floor}")
            if reason is not None and cell.met:
                problems.append(f"{component}/{cat}: pending exemption is met ({cell.counted} >= {floor}); remove it")

    for key in pending_cells:
        if key[0] not in floors or key[1] not in floors[key[0]]:
            problems.append(f"pending exemption {key} names no §1 cell")

    themes_missing: dict[str, list[str]] = {}
    themes_pending: dict[str, list[str]] = {}
    for component, required in themes.items():
        for theme in required:
            have = theme in theme_hits[component]
            if (component, theme) in pending_themes:
                if have:
                    problems.append(f"{component} theme {theme}: pending exemption is met; remove it")
                else:
                    themes_pending.setdefault(component, []).append(theme)
            elif not have:
                themes_missing.setdefault(component, []).append(theme)
                problems.append(f"{component} theme {theme}: no counted case")

    return Report(cells, themes_missing, themes_pending, not_counted, problems)


def check_harness_floors() -> list[str]:
    """The harness quota and themes must equal the pinned §1 values."""
    sys.path.insert(0, str(ROOT))
    try:
        from tests.redteam import _harness
    except ImportError as exc:
        return [f"tests/redteam/_harness.py not importable ({exc}); no red-team inventory on this tip"]
    problems = []
    if _harness.QUOTAS != FLOORS:
        problems.append(f"tests/redteam/_harness.py QUOTAS differ from §1: {_harness.QUOTAS} != {FLOORS}")
    harness_themes = {k: tuple(v) for k, v in _harness.REQUIRED_THEMES.items()}
    if harness_themes != THEMES:
        problems.append("tests/redteam/_harness.py REQUIRED_THEMES differ from §1")
    for component, cats in FLOORS.items():
        if sum(cats.values()) != MIN_TOTAL[component]:
            problems.append(f"{component}: per-category floors sum to {sum(cats.values())}, §1 total is {MIN_TOTAL[component]}")
    return problems


# -- output ---------------------------------------------------------------------------


def summary_markdown(report: Report, mode: str, pytest_code: int, all_active: Report) -> str:
    lines = [
        f"### G2 Quota floor: {'PASS' if report.ok else 'FAIL'}",
        "",
        f"Counted statuses: `{mode}` (stub never counts). A case counts only when its bound test ended as its status says.",
        f"`tests/redteam` pytest exit code: {pytest_code}.",
        "",
        "| Component | Cat | Floor | active | xfail | stub | Counted | Active-only | Result |",
        "|---|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    active_only = {(c.component, c.category): c.counted for c in all_active.cells}
    for c in report.cells:
        if c.pending is not None:
            result = "below floor, not enforced" if not c.met else "met, exemption stale"
        else:
            result = "met" if c.met else "**SHORT**"
        s = c.statuses
        lines.append(
            f"| {c.component} | {c.category} | {c.floor} | {s['active']} | {s['xfail']} | {s['stub']}"
            f" | {c.counted} | {active_only[(c.component, c.category)]} | {result} |"
        )
    lines.append("")
    totals = defaultdict(lambda: [0, 0])
    for c in report.cells:
        totals[c.component][0] += c.floor
        totals[c.component][1] += min(c.counted, c.floor)
    lines.append("Per component, counted toward floor / §1 min total: " + ", ".join(
        f"{k} {v[1]}/{v[0]}" for k, v in totals.items()
    ))
    if report.not_counted:
        lines += ["", "Not counted:"] + [f"- `{cid}`: {why}" for cid, why in sorted(report.not_counted.items())]
    if report.themes_pending:
        lines += ["", "Themes pending (not enforced):"] + [
            f"- {k}: {', '.join(v)}" for k, v in report.themes_pending.items()
        ]
    reasons = sorted({c.pending for c in report.cells if c.pending} | set(PENDING_THEMES.values()))
    if reasons:
        lines += ["", "Pending reasons:"] + [f"- {r}" for r in reasons]
    if report.problems:
        lines += ["", "Failures:"] + [f"- {p}" for p in report.problems]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--count", choices=sorted(COUNT_MODES), default="active+xfail",
                        help="statuses that count toward a floor (default: active+xfail, the #26 harness rule)")
    args = parser.parse_args(argv)

    problems = check_harness_floors()
    cases = load_cases()
    if not cases:
        problems.append(f"no case files under {CASES_DIR.relative_to(ROOT)}")
    outcomes, collect_errors, code = run_redteam_tests()
    problems += [f"collection error: {nodeid}" for nodeid in collect_errors]
    if code not in (0, 1):  # 1 = some test failed; those cases are simply not counted
        problems.append(f"pytest exited {code} (interrupted, internal or usage error)")

    report = count(cases, outcomes, COUNT_MODES[args.count])
    report.problems[:0] = problems
    active = count(cases, outcomes, COUNT_MODES["active"])
    text = summary_markdown(report, args.count, code, active)
    print("\n" + text)
    if path := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(text)
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())

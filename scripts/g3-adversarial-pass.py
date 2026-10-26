#!/usr/bin/env python3
"""FACTORY G3 Adversarial pass gate: no active §1 red-team case may fail R1-R9.

Reads the case inventory (``tests/redteam/cases/<component>/<ID>.json``, the
layout from PR #26) and runs every test marked ``redteam`` under
``tests/redteam``. Each test names its case through
``@pytest.mark.redteam(case_id=..., status=...)``, which the ``@case(...)``
helper in ``tests/redteam/_harness.py`` applies. Outcomes are mapped back to
cases by that marker.

Enforced scope: cases whose file says ``"status": "active"``. The gate fails when

* an active case has no test, or any of its tests fails, errors, is skipped,
  xfails or xpasses. A skip or xfail is never a G3 pass;
* an ``xfail`` case does not xfail, or a ``stub`` case is not skipped (the
  file's status no longer matches what the test does);
* a test's marker names an unknown case, or its marker status differs from
  the file;
* a case file is malformed (bad id, category, rubric or status);
* the inventory has no active adversarial case, or no active positive control
  (``CONTROL``), so "refuse everything" cannot pass;
* pytest exits non-zero.

``xfail`` and ``stub`` cases are listed as open gaps. They do not fail the gate
and they never count as G3 passes. G3 over every §1 case holds only when no gap
is left.

With ``--allow-missing-inventory``, a ref with no ``tests/redteam/cases``
directory reports INACTIVE and exits 0. That is not a pass: nothing ran. Once
the directory exists the flag has no effect and every rule above applies.

A green result covers only the cases in the inventory at that commit. It is
not a coverage figure.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CASES_SUBDIR = Path("tests") / "redteam" / "cases"
TESTS_SUBDIR = Path("tests") / "redteam"

# FACTORY-REDTEAM-AND-RUBRICS §1 categories, plus CONTROL for §2 positive controls.
CATEGORIES = ("PI", "AB", "DS", "SIM", "SCH", "COST", "ISO", "REC", "CONTROL")
RUBRICS = tuple(f"R{n}" for n in range(1, 10))
STATUSES = ("active", "xfail", "stub")
CASE_ID = re.compile(r"^[A-Z]+-[A-Z]+-\d{2,}$")

# What a case's tests must all report, per file status.
EXPECTED = {"active": "passed", "xfail": "xfailed", "stub": "skipped"}
# When phases disagree, keep the worst outcome.
SEVERITY = {"passed": 0, "skipped": 1, "xfailed": 1, "xpassed": 2, "failed": 3, "error": 4}


@dataclass
class Case:
    id: str
    component: str
    category: str
    rubrics: tuple[str, ...]
    status: str
    path: Path

    @property
    def adversarial(self) -> bool:
        return self.category != "CONTROL"


def load_inventory(cases_dir: Path) -> tuple[dict[str, Case], list[str]]:
    """Read every case file; return the cases and any schema problems."""
    cases: dict[str, Case] = {}
    problems: list[str] = []
    for path in sorted(cases_dir.glob("*/*.json")):
        where = f"{path.parent.name}/{path.name}"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            problems.append(f"{where}: unreadable case file ({exc})")
            continue
        cid = data.get("id")
        rubrics = data.get("rubrics")
        bad = []
        if not isinstance(cid, str) or not CASE_ID.match(cid) or path.stem != cid:
            bad.append(f"id {cid!r} must match the file name and look like XX-CAT-01")
        if data.get("component") != path.parent.name:
            bad.append(f"component {data.get('component')!r} must match its directory {path.parent.name!r}")
        if data.get("category") not in CATEGORIES:
            bad.append(f"category {data.get('category')!r} not in {CATEGORIES}")
        if not isinstance(rubrics, list) or not rubrics or not set(rubrics) <= set(RUBRICS):
            bad.append(f"rubrics {rubrics!r} must be a non-empty subset of R1-R9")
        if data.get("status") not in STATUSES:
            bad.append(f"status {data.get('status')!r} not in {STATUSES}")
        if isinstance(cid, str) and cid in cases:
            bad.append(f"duplicate case id {cid!r}")
        if bad:
            problems.extend(f"{where}: {b}" for b in bad)
            continue
        cases[cid] = Case(cid, data["component"], data["category"], tuple(rubrics), data["status"], path)
    return cases, problems


class Recorder:
    """pytest plugin: which case each collected test is bound to, and how it ended."""

    def __init__(self) -> None:
        self.bound: dict[str, tuple[str | None, str | None]] = {}
        self.outcomes: dict[str, str] = {}
        self.collect_errors: list[str] = []

    def pytest_collectreport(self, report):
        if report.failed:
            self.collect_errors.append(f"{report.nodeid or '<session>'}: collection error")

    def pytest_collection_finish(self, session):
        for item in session.items:
            marker = item.get_closest_marker("redteam")
            if marker is not None:
                self.bound[item.nodeid] = (marker.kwargs.get("case_id"), marker.kwargs.get("status"))

    def pytest_runtest_logreport(self, report):
        if report.when == "call" or report.failed or report.skipped:
            outcome = _outcome(report)
            previous = self.outcomes.get(report.nodeid, "passed")
            if SEVERITY[outcome] >= SEVERITY[previous]:
                self.outcomes[report.nodeid] = outcome


def _outcome(report) -> str:
    xfail = hasattr(report, "wasxfail")
    if report.failed:
        return "failed" if report.when == "call" else "error"
    if report.skipped:
        return "xfailed" if xfail else "skipped"
    return "xpassed" if xfail else "passed"


def run_pytest(root: Path, tests_dir: Path) -> tuple[Recorder, int]:
    recorder = Recorder()
    args = [
        str(tests_dir), "-m", "redteam", "-p", "no:cacheprovider", "-o", "xfail_strict=true",
        "-rfEsxX", f"--rootdir={root}",
    ]
    cwd = Path.cwd()
    os.chdir(root)
    try:
        returncode = int(pytest.main(args, plugins=[recorder]))
    finally:
        os.chdir(cwd)
    return recorder, returncode


@dataclass
class Result:
    passed: list[Case] = field(default_factory=list)
    gaps: list[tuple[Case, str]] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def evaluate(cases: dict[str, Case], recorder: Recorder, require_control: bool = True) -> Result:
    result = Result(problems=list(recorder.collect_errors))
    tests_by_case: dict[str, list[str]] = defaultdict(list)
    for nodeid, (cid, marker_status) in sorted(recorder.bound.items()):
        if cid not in cases:
            result.problems.append(f"{nodeid}: redteam marker names unknown case {cid!r}")
            continue
        if marker_status != cases[cid].status:
            result.problems.append(
                f"{nodeid}: marker status {marker_status!r} differs from {cid} file status {cases[cid].status!r}"
            )
        tests_by_case[cid].append(nodeid)

    for cid, case in sorted(cases.items()):
        nodeids = tests_by_case.get(cid, [])
        outcomes = [recorder.outcomes.get(n, "not run") for n in nodeids]
        expected = EXPECTED[case.status]
        rubrics = ",".join(case.rubrics)
        if not nodeids:
            result.problems.append(f"{cid} ({case.status}, {rubrics}): no test is bound to this case")
        elif any(o != expected for o in outcomes):
            got = ", ".join(f"{n} {o}" for n, o in zip(nodeids, outcomes) if o != expected)
            if case.status == "active":
                result.problems.append(f"{cid} (active, {rubrics}) did not pass: {got}")
            else:
                result.problems.append(f"{cid} ({case.status}, {rubrics}) expected {expected}, got: {got}; update its status")
        elif case.status == "active":
            result.passed.append(case)
        else:
            result.gaps.append((case, case.status))

    active = [c for c in cases.values() if c.status == "active"]
    if not any(c.adversarial for c in active):
        result.problems.append("no active adversarial case in the inventory; G3 cannot pass on nothing")
    if require_control and not any(not c.adversarial for c in active):
        result.problems.append("no active positive control (CONTROL) in the inventory; refuse-everything would pass")
    return result


def report(cases: dict[str, Case], result: Result, returncode: int, ref: str) -> str:
    verdict = "FAILED" if result.problems else "passed (active cases)"
    passed = {c.id for c in result.passed}
    lines = [
        f"### G3 Adversarial pass{ref}: {verdict}",
        "",
        "| Component | Active adversarial passed | Active controls passed | Active not passing | xfail (open gap) | stub (open gap) |",
        "|---|---|---|---|---|---|",
    ]
    for component in sorted({c.component for c in cases.values()}):
        mine = [c for c in cases.values() if c.component == component]
        n = Counter()
        for c in mine:
            if c.status == "active":
                key = "fail" if c.id not in passed else ("adv" if c.adversarial else "ctl")
            else:
                key = c.status
            n[key] += 1
        lines.append(f"| {component} | {n['adv']} | {n['ctl']} | {n['fail']} | {n['xfail']} | {n['stub']} |")
    rubric_rows = Counter(r for c in result.passed if c.adversarial for r in c.rubrics)
    lines += [
        "",
        "Rubric tags on passing active adversarial cases: "
        + (", ".join(f"{r} {rubric_rows[r]}" for r in RUBRICS) if rubric_rows else "none"),
        f"pytest exit code: {returncode}",
    ]
    if result.problems:
        lines += ["", "Violations:", *[f"- `{p}`" for p in result.problems]]
    if result.gaps:
        lines += ["", "Open gaps (not G3 passes; G3 over every §1 case is not met while any remain):"]
        lines += [f"- {c.id} ({status}, {','.join(c.rubrics)})" for c, status in result.gaps]
    lines += ["", "A pass covers only the active cases above at this commit; it is not a coverage figure."]
    return "\n".join(lines) + "\n"


def inactive_report(cases_dir: Path, ref: str) -> str:
    return (
        f"### G3 Adversarial pass{ref}: INACTIVE\n\n"
        f"No case inventory at `{cases_dir}`, so no §1 case ran. This is not a G3 pass.\n"
        "The gate enforces as soon as the directory exists (FACTORY red-team inventory, PR #26).\n"
    )


def emit(text: str) -> None:
    print(text)
    if step_summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(step_summary, "a", encoding="utf-8") as fh:
            fh.write(text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, default=ROOT, help="repository root (default: this checkout)")
    parser.add_argument(
        "--allow-missing-inventory", action="store_true",
        help="exit 0 as INACTIVE when tests/redteam/cases does not exist (no effect once it does)",
    )
    parser.add_argument(
        "--no-require-control", dest="require_control", action="store_false",
        help="do not require an active CONTROL case (for testing the gate only)",
    )
    args = parser.parse_args(argv)
    root = args.root.resolve()
    cases_dir, tests_dir = root / CASES_SUBDIR, root / TESTS_SUBDIR
    ref = f" at `{os.environ['GITHUB_SHA'][:7]}`" if os.environ.get("GITHUB_SHA") else ""

    if not cases_dir.is_dir():
        if args.allow_missing_inventory:
            emit(inactive_report(CASES_SUBDIR, ref))
            print(f"::notice title=G3 inactive::no {CASES_SUBDIR.as_posix()} on this ref; no §1 case ran (not a pass)")
            return 0
        emit(f"### G3 Adversarial pass{ref}: FAILED\n\nNo case inventory at `{CASES_SUBDIR}`.\n")
        return 1

    cases, problems = load_inventory(cases_dir)
    recorder, returncode = run_pytest(root, tests_dir)
    result = evaluate(cases, recorder, require_control=args.require_control)
    result.problems[:0] = problems
    if returncode and not result.problems:
        result.problems.append(f"pytest exited {returncode}")
    emit(report(cases, result, returncode, ref))
    for problem in result.problems:
        print(f"::error title=G3 Adversarial pass::{problem}")
    return 1 if result.problems or returncode else 0


if __name__ == "__main__":
    sys.exit(main())

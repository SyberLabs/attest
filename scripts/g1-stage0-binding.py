#!/usr/bin/env python3
"""FACTORY G1 Stage-0 binding gate: app/build version and Kane run id.

Runs ``tests/quality/test_g1_stage0_binding.py`` and passes only when every test in it
ran and passed. A skipped, xfailed, xpassed, failed or errored test fails the gate, and
so does an empty section. Sections come from the test name prefix:

``version``            ``test_version_*``            wrong or omitted app/build version
``kane-run``           ``test_kane_run_*``           wrong, omitted or stale Kane run id
``observation-build``  ``test_observation_build_*``  browser observation names another build, or none
``record``             ``test_record_*``             stored validation not the one this workflow's validation sealed
``pack``               ``test_pack_*``               browser evidence its Kane evidence pack, read back, does not back
``wording``            ``test_wording_*``            the pack's step for a claim is not the wording Attest writes for the approved claim
``run-time``           ``test_run_time_*``           the pack's Kane run started before the workflow was approved
``run-reuse``          ``test_run_reuse_*``          the pack's Kane run is one another sealed validation in the store binds
``control``            ``test_control_*``            intact bindings still verify (refuse-everything fails)

A green result covers only those tests. It is not a coverage figure, and nothing here
runs live Kane.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS = "tests/quality/test_g1_stage0_binding.py"
SECTIONS = {
    "version": "test_version_",
    "kane-run": "test_kane_run_",
    "observation-build": "test_observation_build_",
    "record": "test_record_",
    "pack": "test_pack_",
    "wording": "test_wording_",
    "run-time": "test_run_time_",
    "run-reuse": "test_run_reuse_",
    "control": "test_control_",
}


def junit_outcomes(xml_path: Path) -> dict[str, str]:
    """Test name (with parameter id) -> passed, failed, error or skipped."""
    out: dict[str, str] = {}
    for case in ET.parse(xml_path).iter("testcase"):
        outcome = "passed"
        for tag in ("failure", "error", "skipped"):
            if case.find(tag) is not None:
                outcome = "failed" if tag == "failure" else tag
                break
        out[case.get("name", "")] = outcome
    return out


def check(outcomes: dict[str, str]) -> tuple[dict[str, int], list[str]]:
    """Return passed counts per section and gate violations (empty means the gate passes)."""
    problems = [f"{name}: {outcome}" for name, outcome in sorted(outcomes.items()) if outcome != "passed"]
    counts = {}
    for section, prefix in SECTIONS.items():
        counts[section] = sum(1 for n, o in outcomes.items() if n.startswith(prefix) and o == "passed")
        if not counts[section]:
            problems.append(f"section [{section}] has no passing test ({prefix}*)")
    stray = [n for n in outcomes if not n.startswith(tuple(SECTIONS.values()))]
    problems += [f"{n}: not in any section; name it {'/'.join(p + '*' for p in SECTIONS.values())}" for n in stray]
    return counts, problems


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        xml_path = Path(tmp) / "g1-binding.xml"
        cmd = [sys.executable, "-m", "pytest", TESTS, "-rsx", "-p", "no:cacheprovider",
               "-o", "xfail_strict=true", f"--junitxml={xml_path}"]
        code = subprocess.run(cmd, cwd=ROOT).returncode
        outcomes = junit_outcomes(xml_path) if xml_path.exists() else {}
    counts, problems = check(outcomes)
    if code != 0 and not problems:
        problems.append(f"pytest exited {code}")
    lines = ["## G1 Stage-0 version+Kane binding", "",
             "| section | passed |", "|---|---|", *(f"| {s} | {n} |" for s, n in counts.items()), "",
             f"{len(outcomes)} test cases ran. Counts, not coverage. No live Kane.", ""]
    lines += [f"- FAIL: {p}" for p in problems] or ["Gate passed."]
    report = "\n".join(lines)
    print(report)
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(report + "\n")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())

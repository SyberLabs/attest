#!/usr/bin/env python3
"""Run a named set of pytest node ids as a fail-closed CI gate.

The gate passes only when every listed node id ran and passed. A missing,
deselected, skipped, xfailed or xpassed test fails the gate, as does an empty
required manifest section. A green result covers only the listed tests.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path


def read_manifest(path: Path) -> dict[str, list[str]]:
    """Return node ids grouped by ``[section]`` header, in file order."""
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1].strip()
            sections.setdefault(current, [])
        elif current is None:
            raise ValueError(f"{path}: node id {line!r} appears before any [section]")
        else:
            sections[current].append(line)
    return sections


def junit_key(node_id: str) -> tuple[str, str]:
    """Map ``tests/a/test_b.py::Cls::test_c`` to junit (classname, name)."""
    file_part, *rest = node_id.split("::")
    if not rest:
        raise ValueError(f"node id {node_id!r} must name a test function")
    module = file_part.removesuffix(".py").replace("/", ".")
    return ".".join([module, *rest[:-1]]), rest[-1]


def junit_results(xml_path: Path) -> dict[tuple[str, str], str]:
    """Return outcome per junit test case: passed, failed, error or skipped."""
    results: dict[tuple[str, str], str] = {}
    for case in ET.parse(xml_path).iter("testcase"):
        outcome = "passed"
        for tag in ("failure", "error", "skipped"):
            if case.find(tag) is not None:
                outcome = "failed" if tag == "failure" else tag
                break
        results[(case.get("classname", ""), case.get("name", ""))] = outcome
    return results


def check(
    sections: dict[str, list[str]],
    results: dict[tuple[str, str], str],
    required: list[str],
) -> list[str]:
    """Return gate violations; an empty list means the gate passed."""
    problems = [f"required section [{name}] is missing or empty" for name in required if not sections.get(name)]
    for node_id in (n for ids in sections.values() for n in ids):
        classname, name = junit_key(node_id)
        outcomes = [
            outcome
            for (cls, case), outcome in results.items()
            if cls == classname and (case == name or case.startswith(name + "["))
        ]
        if not outcomes:
            problems.append(f"{node_id}: not run")
        problems.extend(f"{node_id}: {outcome}" for outcome in outcomes if outcome != "passed")
    return problems


def summary(gate: str, sections: dict[str, list[str]], problems: list[str], returncode: int) -> str:
    verdict = "FAILED" if problems or returncode else "passed"
    lines = [f"### {gate}: {verdict}", "", "| Section | Node ids |", "|---|---|"]
    lines += [f"| {name} | {len(ids)} |" for name, ids in sections.items()]
    lines += ["", f"pytest exit code: {returncode}"]
    if problems:
        lines += ["", "Violations:", *[f"- `{p}`" for p in problems]]
    lines += ["", "A pass covers only the listed tests; it is not a coverage figure."]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--gate", required=True, help="gate label for the report, e.g. 'G1 Stage-0 exit'")
    parser.add_argument("--require-section", action="append", default=[], metavar="NAME")
    args = parser.parse_args()

    sections = read_manifest(args.manifest)
    node_ids = [n for ids in sections.values() for n in ids]
    if not node_ids:
        print(f"{args.manifest}: no node ids", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory() as tmp:
        xml_path = Path(tmp) / "gate.xml"
        command = [
            sys.executable, "-m", "pytest", "-p", "no:cacheprovider",
            "-o", "xfail_strict=true", "-rsx", f"--junitxml={xml_path}", *node_ids,
        ]
        returncode = subprocess.run(command, check=False).returncode
        results = junit_results(xml_path) if xml_path.exists() else {}

    problems = check(sections, results, args.require_section)
    report = summary(args.gate, sections, problems, returncode)
    print(report)
    if step_summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(step_summary, "a", encoding="utf-8") as fh:
            fh.write(report)
    return 1 if problems or returncode else 0


if __name__ == "__main__":
    sys.exit(main())

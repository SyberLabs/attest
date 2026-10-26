#!/usr/bin/env python3
"""FACTORY G4 positive controls gate: the allowed paths must still succeed.

Runs the node ids in ``tests/quality/g4_positive_controls.txt`` and fails closed.
The gate passes only when every listed id ran and passed. It fails when:

- a test file named by the manifest does not exist (the controls are absent),
- a listed id is missing, deselected, skipped, errored, failed, xfailed or xpassed,
- a required section is empty (see ``REQUIRED_SECTIONS``),
- pytest exits non-zero for any other reason.

Parametrized ids are listed one by one, so dropping a refuse-everything mutant
fails the gate as well. A green result covers only the listed ids. It is not a
coverage figure.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "tests" / "quality" / "g4_positive_controls.txt"
# control: the allowed path succeeds. twin: one changed input is refused.
# mutant: the product patched to refuse everything makes the control fail.
REQUIRED_SECTIONS = (
    "md-control", "md-twin", "md-mutant",
    "kane-control", "kane-twin", "kane-mutant",
    "rs-control", "rs-twin", "rs-mutant",
    "gate",
)


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
    """Map ``tests/a/test_b.py::Cls::test_c[p]`` to junit (classname, name)."""
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


def missing_files(sections: dict[str, list[str]], root: Path = ROOT) -> list[str]:
    """Test files the manifest names that are not in the checkout."""
    files = dict.fromkeys(n.split("::", 1)[0] for ids in sections.values() for n in ids)
    return [f for f in files if not (root / f).is_file()]


def outcomes(sections: dict[str, list[str]], results: dict[tuple[str, str], str]) -> dict[str, str]:
    """Outcome per listed node id; ``not run`` when junit has no matching case.

    A bare function name also matches its parametrized cases, and every case must
    pass. Listing ``name[param]`` matches that case only.
    """
    out: dict[str, str] = {}
    for node_id in (n for ids in sections.values() for n in ids):
        classname, name = junit_key(node_id)
        seen = [
            outcome
            for (cls, case), outcome in results.items()
            if cls == classname and (case == name or ("[" not in name and case.startswith(name + "[")))
        ]
        out[node_id] = next((o for o in seen if o != "passed"), "passed") if seen else "not run"
    return out


def check(
    sections: dict[str, list[str]],
    results: dict[tuple[str, str], str],
    missing: list[str] = (),
    required=REQUIRED_SECTIONS,
) -> list[str]:
    """Return gate violations; an empty list means every listed id ran and passed."""
    problems = [f"positive-control file is missing: {f}" for f in missing]
    problems += [f"required section [{name}] is missing or empty" for name in required if not sections.get(name)]
    problems += [f"{node_id}: {outcome}" for node_id, outcome in outcomes(sections, results).items() if outcome != "passed"]
    return problems


def run(manifest: Path) -> tuple[dict[str, list[str]], dict[str, str], list[str], int]:
    sections = read_manifest(manifest)
    node_ids = [n for ids in sections.values() for n in ids]
    if not node_ids:
        return sections, {}, [f"{manifest}: no node ids"], 1
    missing = missing_files(sections)
    # Ask pytest only for ids whose file exists, so one absent file does not hide
    # the outcome of the others. The absent ones still fail as "not run".
    runnable = [n for n in node_ids if n.split("::", 1)[0] not in missing]
    results: dict[tuple[str, str], str] = {}
    returncode = 1 if missing else 0
    if runnable:
        with tempfile.TemporaryDirectory() as tmp:
            xml_path = Path(tmp) / "g4.xml"
            command = [
                sys.executable, "-m", "pytest", "-p", "no:cacheprovider",
                "-o", "xfail_strict=true", "-rsx", f"--junitxml={xml_path}", *runnable,
            ]
            returncode = subprocess.run(command, cwd=ROOT, check=False).returncode or returncode
            results = junit_results(xml_path) if xml_path.exists() else {}
    return sections, outcomes(sections, results), check(sections, results, missing), returncode


def report(sections: dict[str, list[str]], per_id: dict[str, str], problems: list[str], returncode: int) -> str:
    verdict = "FAILED" if problems or returncode else "passed"
    lines = [f"### G4 Positive controls: {verdict}", "", "| Section | Listed ids | Passed |", "|---|---|---|"]
    for name, ids in sections.items():
        lines.append(f"| {name} | {len(ids)} | {sum(per_id.get(n) == 'passed' for n in ids)} |")
    lines += ["", f"pytest exit code: {returncode}"]
    if problems:
        lines += ["", "Violations:", *[f"- `{p}`" for p in problems]]
    lines += ["", "A pass covers only the listed ids; it is not a coverage figure."]
    return "\n".join(lines) + "\n"


def emit(text: str) -> None:
    print(text)
    if step_summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(step_summary, "a", encoding="utf-8") as fh:
            fh.write(text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    args = parser.parse_args(argv)
    sections, per_id, problems, returncode = run(args.manifest)
    if returncode and not problems:
        problems = [f"pytest exited {returncode}"]
    emit(report(sections, per_id, problems, returncode))
    return 1 if problems or returncode else 0


if __name__ == "__main__":
    sys.exit(main())

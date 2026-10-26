#!/usr/bin/env python3
"""FACTORY G7 Kane resource gate: deadline, cleanup and isolation tests, and no port-kill code.

Two checks, both fail closed:

``tests``     runs the node ids in ``tests/quality/g7_kane_resource.txt``. The gate
              passes only when every listed id ran and passed. A missing,
              deselected, skipped, xfailed or xpassed test fails it, and so does an
              empty required section (``deadline``, ``cleanup``, ``isolation``,
              ``control``).
``port-kill`` scans product paths for code that finds a process by the port it
              listens on and kills it. Any hit fails, and so does a scan that read
              no files. The patterns live in
              ``tests/quality/g7_port_kill_patterns.txt``, outside the scanned
              paths, so the scanner never matches its own pattern list.

A green result covers only the listed tests and the scanned paths. It is not a
coverage figure.
"""

from __future__ import annotations

import argparse
import io
import os
import re
import subprocess
import sys
import tempfile
import tokenize
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "tests" / "quality" / "g7_kane_resource.txt"
REQUIRED_SECTIONS = ("deadline", "cleanup", "isolation", "port-kill", "control")
PATTERNS = ROOT / "tests" / "quality" / "g7_port_kill_patterns.txt"

# Product paths the port-kill scan reads. Tests are not scanned: they hold the
# pattern list and the synthetic hits that prove the scan works.
SCAN_ROOTS = ("attest", "scripts")
SCAN_SUFFIXES = {".py", ".sh", ".bash", ".ps1", ".cmd", ".bat", ".js", ".mjs", ".cjs", ".ts"}


def read_patterns(path: Path = PATTERNS) -> dict[str, re.Pattern[str]]:
    """``name = regex`` per line, matched case-insensitively. An empty list is an error."""
    patterns: dict[str, re.Pattern[str]] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        name, sep, rx = raw.partition(" = ")
        if not sep or not rx.strip():
            raise ValueError(f"{path}: expected 'name = regex', got {raw!r}")
        patterns[name.strip()] = re.compile(rx.strip(), re.IGNORECASE)
    if not patterns:
        raise ValueError(f"{path}: no patterns")
    return patterns


# -- port-kill scan -------------------------------------------------------------------


@dataclass(frozen=True)
class Hit:
    path: Path
    line: int
    pattern: str
    text: str

    def __str__(self) -> str:
        try:
            shown = self.path.relative_to(ROOT)
        except ValueError:
            shown = self.path
        return f"{shown}:{self.line}: port-kill pattern {self.pattern!r}: {self.text.strip()}"


def _code_lines(path: Path) -> list[tuple[int, str]]:
    """Source lines with comments blanked. Python goes through tokenize so a ``#``
    inside a string stays; other files drop ``#``/``//``/``REM`` line comments."""
    text = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix != ".py":
        out = []
        for no, line in enumerate(text.splitlines(), start=1):
            if re.match(r"\s*(#|//|rem\b|::)", line, re.IGNORECASE):
                continue
            out.append((no, line))
        return out
    lines = text.splitlines()
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.COMMENT:
                row, col = tok.start
                lines[row - 1] = lines[row - 1][:col]
    except (tokenize.TokenError, SyntaxError, IndentationError):
        pass  # fall back to raw lines: scanning more is the fail-closed direction
    return list(enumerate(lines, start=1))


def scan_files(roots: list[Path], patterns: dict[str, re.Pattern[str]]) -> tuple[list[Hit], int]:
    """Return port-kill hits and how many files were read."""
    hits: list[Hit] = []
    scanned = 0
    for root in roots:
        files = [root] if root.is_file() else sorted(p for p in root.rglob("*") if p.is_file())
        for path in files:
            if path.suffix.lower() not in SCAN_SUFFIXES:
                continue
            if any(part in {"__pycache__", "node_modules", ".venv"} for part in path.parts):
                continue
            scanned += 1
            for no, line in _code_lines(path):
                for name, rx in patterns.items():
                    if rx.search(line):
                        hits.append(Hit(path, no, name, line))
    return hits, scanned


def port_kill_problems(roots: list[Path], patterns: dict[str, re.Pattern[str]]) -> tuple[list[str], int]:
    missing = [f"scan root {r} does not exist" for r in roots if not r.exists()]
    hits, scanned = scan_files([r for r in roots if r.exists()], patterns)
    problems = missing + [str(h) for h in hits]
    if scanned == 0:
        problems.append("port-kill scan read no files")
    return problems, scanned


# -- manifest tests -------------------------------------------------------------------


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


def check(sections: dict[str, list[str]], results: dict[tuple[str, str], str], required=REQUIRED_SECTIONS) -> list[str]:
    """Return gate violations; an empty list means every listed id ran and passed."""
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


def run_tests(manifest: Path) -> tuple[dict[str, list[str]], list[str], int]:
    sections = read_manifest(manifest)
    node_ids = [n for ids in sections.values() for n in ids]
    if not node_ids:
        return sections, [f"{manifest}: no node ids"], 1
    with tempfile.TemporaryDirectory() as tmp:
        xml_path = Path(tmp) / "g7.xml"
        command = [
            sys.executable, "-m", "pytest", "-p", "no:cacheprovider",
            "-o", "xfail_strict=true", "-rsx", f"--junitxml={xml_path}", *node_ids,
        ]
        returncode = subprocess.run(command, cwd=ROOT, check=False).returncode
        results = junit_results(xml_path) if xml_path.exists() else {}
    return sections, check(sections, results), returncode


# -- report ---------------------------------------------------------------------------


def report(title: str, rows: list[tuple[str, int]], problems: list[str], extra: list[str]) -> str:
    verdict = "FAILED" if problems else "passed"
    lines = [f"### G7 Kane resource, {title}: {verdict}", "", "| Item | Count |", "|---|---|"]
    lines += [f"| {name} | {n} |" for name, n in rows]
    lines += ["", *extra]
    if problems:
        lines += ["", "Violations:", *[f"- `{p}`" for p in problems]]
    lines += ["", "A pass covers only the listed tests and scanned paths; it is not a coverage figure."]
    return "\n".join(lines) + "\n"


def emit(text: str) -> None:
    print(text)
    if step_summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(step_summary, "a", encoding="utf-8") as fh:
            fh.write(text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="check", required=True)
    t = sub.add_parser("tests", help="run the G7 manifest")
    t.add_argument("--manifest", type=Path, default=MANIFEST)
    p = sub.add_parser("port-kill", help="scan product paths for port-kill code")
    p.add_argument("roots", nargs="*", type=Path, help=f"paths to scan (default: {', '.join(SCAN_ROOTS)})")
    p.add_argument("--patterns", type=Path, default=PATTERNS)
    args = parser.parse_args(argv)

    if args.check == "tests":
        sections, problems, returncode = run_tests(args.manifest)
        if returncode and not problems:
            problems = [f"pytest exited {returncode}"]
        rows = [(f"[{name}] node ids", len(ids)) for name, ids in sections.items()]
        emit(report("tests", rows, problems, [f"pytest exit code: {returncode}"]))
        return 1 if problems or returncode else 0

    roots = args.roots or [ROOT / r for r in SCAN_ROOTS]
    patterns = read_patterns(args.patterns)
    problems, scanned = port_kill_problems(roots, patterns)
    shown = ", ".join(f"`{r.relative_to(ROOT) if r.is_relative_to(ROOT) else r}`" for r in roots)
    emit(report("port-kill scan", [("files scanned", scanned), ("patterns", len(patterns))], problems,
                [f"Roots: {shown}", f"Patterns: {', '.join(f'`{n}`' for n in patterns)}"]))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())

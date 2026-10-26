#!/usr/bin/env python3
"""G5 Protected-text diff: fail when a change alters protected requirement text.

Protected text is read from the hooks already in the repository, at both the
base and the head revision:

- Maintain Docs: every ``attest.policy.yaml`` ``match`` rule protects each
  paragraph of a ``*.md`` file in the same directory that contains the match
  (case-folded, whitespace-folded), mirroring ``attest.policy.Policy.decide``.
  The rule itself is protected too: dropping it removes the paragraph's
  protection and counts as a change.
- Reconcile Spec: every claim with ``"protected": true`` in
  ``tests/fixtures/reconcile_spec/*.json`` protects its ``text``.

Each protected unit present at base must be byte-identical at head. The only
exception is an approved change decision in ``docs/change-decisions/*.json``
that is already on the base revision and binds the unit's path and exact
before/after SHA-256. A decision added by the same change does not count, so a
change cannot approve itself. ``claim:`` policy rules name compiler claim ids,
which cannot be resolved without compiling, and are not covered here.

Exit codes: 0 pass, 1 protected text changed without a decision, 2 the gate
could not run (bad revision, unreadable policy, no protected text at base).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import yaml

GATE = "G5 Protected-text diff"
POLICY_NAME = "attest.policy.yaml"
FIXTURE_DIR = "tests/fixtures/reconcile_spec"
DECISION_DIR = "docs/change-decisions"
DECISION_SCHEMA = "attest.change-decision/v0"


class GateError(RuntimeError):
    """The gate cannot decide; callers must treat this as a failure."""


class GitTree:
    def __init__(self, rev: str, repo: Path = Path(".")):
        self.repo = repo
        self.rev = self._git("rev-parse", "--verify", f"{rev}^{{commit}}").decode().strip()

    def _git(self, *args: str) -> bytes:
        result = subprocess.run(["git", "-C", str(self.repo), *args], capture_output=True, check=False)
        if result.returncode:
            raise GateError(result.stderr.decode(errors="replace").strip() or f"git {' '.join(args)} failed")
        return result.stdout

    def files(self) -> list[str]:
        return self._git("ls-tree", "-r", "-z", "--name-only", self.rev).decode().split("\0")[:-1]

    def read(self, path: str) -> bytes:
        return self._git("show", f"{self.rev}:{path}")


class DirTree:
    def __init__(self, root: Path):
        self.root = root

    def files(self) -> list[str]:
        return sorted(p.relative_to(self.root).as_posix() for p in self.root.rglob("*") if p.is_file())

    def read(self, path: str) -> bytes:
        return (self.root / path).read_bytes()


@dataclass(frozen=True)
class Unit:
    """One span of protected bytes and the hook that protects it."""

    source: str
    path: str
    key: str
    text: bytes

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.text).hexdigest()

    @property
    def ident(self) -> tuple[str, str, str, str]:
        return (self.source, self.path, self.key, self.sha256)


def _fold(text: str) -> str:
    return " ".join(text.casefold().split())


def _paragraphs(data: bytes) -> list[bytes]:
    blocks: list[bytes] = []
    current: list[bytes] = []
    for line in data.splitlines(keepends=True):
        if line.strip():
            current.append(line)
        elif current:
            blocks.append(b"".join(current))
            current = []
    if current:
        blocks.append(b"".join(current))
    return blocks


def policy_units(tree, files: list[str]) -> list[Unit]:
    units: list[Unit] = []
    for policy_path in files:
        if PurePosixPath(policy_path).name != POLICY_NAME:
            continue
        try:
            data = yaml.safe_load(tree.read(policy_path).decode("utf-8")) or {}
        except (UnicodeDecodeError, yaml.YAMLError) as exc:
            raise GateError(f"{policy_path}: unreadable policy: {exc}") from exc
        rules = data.get("protected", []) if isinstance(data, dict) else None
        if not isinstance(rules, list):
            raise GateError(f"{policy_path}: policy must contain a protected list")
        folder = PurePosixPath(policy_path).parent
        docs = [f for f in files if PurePosixPath(f).parent == folder and f.endswith(".md")]
        for rule in rules:
            match = rule.get("match") if isinstance(rule, dict) else None
            if not isinstance(match, str) or not match.strip():
                continue
            needle = _fold(match)
            for doc in docs:
                for block in _paragraphs(tree.read(doc)):
                    if needle in _fold(block.decode("utf-8", errors="replace")):
                        units.append(Unit(f"policy:{policy_path}", doc, f"match:{match}", block))
    return units


def fixture_units(tree, files: list[str]) -> list[Unit]:
    units: list[Unit] = []
    for path in files:
        if PurePosixPath(path).parent != PurePosixPath(FIXTURE_DIR) or not path.endswith(".json"):
            continue
        try:
            data = json.loads(tree.read(path))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GateError(f"{path}: unreadable fixture: {exc}") from exc
        for claim in data.get("claims", []) if isinstance(data, dict) else []:
            if isinstance(claim, dict) and claim.get("protected") is True:
                text = claim.get("text")
                if not isinstance(text, str):
                    raise GateError(f"{path}: protected claim {claim.get('claim_id')!r} has no text")
                units.append(Unit("reconcile_spec", path, f"claim:{claim.get('claim_id')}", text.encode("utf-8")))
    return units


def protected_units(tree) -> list[Unit]:
    files = tree.files()
    return policy_units(tree, files) + fixture_units(tree, files)


def approved_decisions(tree) -> tuple[list[dict], list[str]]:
    """Return valid approved decisions and a note for every ignored file."""
    decisions: list[dict] = []
    ignored: list[str] = []
    for path in tree.files():
        if PurePosixPath(path).parent != PurePosixPath(DECISION_DIR) or not path.endswith(".json"):
            continue
        try:
            data = json.loads(tree.read(path))
        except (UnicodeDecodeError, json.JSONDecodeError):
            ignored.append(f"{path}: not valid JSON")
            continue
        problem = _decision_problem(data)
        if problem:
            ignored.append(f"{path}: {problem}")
        else:
            decisions.append({**data, "_file": path})
    return decisions, ignored


def _is_sha(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _decision_problem(data: object) -> str | None:
    if not isinstance(data, dict):
        return "not a JSON object"
    if data.get("schema") != DECISION_SCHEMA:
        return f"schema must be {DECISION_SCHEMA}"
    if data.get("status") != "approved":
        return "status is not approved"
    for field in ("decision_id", "approved_by", "path", "reason"):
        if not isinstance(data.get(field), str) or not data[field].strip():
            return f"{field} is required"
    if not _is_sha(data.get("before_sha256")):
        return "before_sha256 must be a lowercase SHA-256"
    if data.get("after_sha256") is not None and not _is_sha(data["after_sha256"]):
        return "after_sha256 must be a lowercase SHA-256 or null"
    return None


def check(base, head) -> dict:
    """Compare protected units at base and head; raise GateError if base protects nothing."""
    base_units = protected_units(base)
    if not base_units:
        raise GateError("no protected text found at base; refusing to pass a vacuous gate")
    head_units = protected_units(head)
    decisions, ignored = approved_decisions(base)
    head_only_decisions = approved_decisions(head)[0]
    head_texts = _head_digests(head, {u.path for u in base_units})

    remaining = Counter(u.ident for u in head_units)
    violations: list[dict] = []
    approved: list[dict] = []
    for unit in base_units:
        if remaining[unit.ident] > 0:
            remaining[unit.ident] -= 1
            continue
        finding = {"source": unit.source, "path": unit.path, "key": unit.key, "before_sha256": unit.sha256}
        decision = _decision_for(unit, decisions, head_texts.get(unit.path, set()))
        if decision:
            approved.append({**finding, "decision_id": decision["decision_id"], "decision_file": decision["_file"]})
            continue
        head_only = [d for d in head_only_decisions if d["path"] == unit.path and d["before_sha256"] == unit.sha256]
        finding["reason"] = (
            "decision exists only in this change; land it on the base branch first"
            if head_only
            else "no approved change decision on the base branch binds this before/after text"
        )
        violations.append(finding)
    return {
        "gate": GATE,
        "base_units": len(base_units),
        "head_units": len(head_units),
        "violations": violations,
        "approved_changes": approved,
        "ignored_decisions": ignored,
    }


def _head_digests(head, paths: set[str]) -> dict[str, set[str | None]]:
    """Digest every candidate replacement span at head; ``None`` means the text may be gone."""
    files = set(head.files())
    result: dict[str, set[str | None]] = {}
    for path in paths:
        digests: set[str | None] = {None}
        if path in files:
            data = head.read(path)
            if path.endswith(".json"):
                try:
                    claims = json.loads(data).get("claims", [])
                except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
                    claims = []
                for claim in claims if isinstance(claims, list) else []:
                    if isinstance(claim, dict) and isinstance(claim.get("text"), str):
                        digests.add(hashlib.sha256(claim["text"].encode("utf-8")).hexdigest())
            else:
                digests |= {hashlib.sha256(block).hexdigest() for block in _paragraphs(data)}
        result[path] = digests
    return result


def _decision_for(unit: Unit, decisions: list[dict], head_digests: set[str | None]) -> dict | None:
    for decision in decisions:
        if decision["path"] != unit.path or decision["before_sha256"] != unit.sha256:
            continue
        after = decision.get("after_sha256")
        if after is None or after in head_digests:
            return decision
    return None


def summary(report: dict) -> str:
    lines = [
        f"## {GATE}",
        "",
        f"- Protected units at base: {report['base_units']}",
        f"- Protected units at head: {report['head_units']}",
        f"- Changed with an approved decision: {len(report['approved_changes'])}",
        f"- Changed without an approved decision: {len(report['violations'])}",
        "",
    ]
    for item in report["violations"]:
        lines.append(f"- FAIL `{item['path']}` {item['key']} (`{item['before_sha256'][:12]}`): {item['reason']}")
    for item in report["approved_changes"]:
        lines.append(f"- approved `{item['path']}` {item['key']} by `{item['decision_id']}` ({item['decision_file']})")
    for note in report["ignored_decisions"]:
        lines.append(f"- ignored decision {note}")
    if lines[-1]:
        lines.append("")
    lines.append("These are counts of protected spans compared, not a coverage figure.")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", required=True, help="base revision (e.g. the PR merge commit's first parent)")
    parser.add_argument("--head", default="HEAD", help="head revision (default HEAD)")
    parser.add_argument("--repo", type=Path, default=Path("."))
    args = parser.parse_args(argv)
    try:
        report = check(GitTree(args.base, args.repo), GitTree(args.head, args.repo))
    except GateError as exc:
        print(f"{GATE}: cannot run: {exc}", file=sys.stderr)
        return 2
    text = summary(report)
    print(text, end="")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as handle:
            handle.write(text)
    return 1 if report["violations"] else 0


if __name__ == "__main__":
    sys.exit(main())

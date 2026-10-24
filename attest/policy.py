"""Authority policy for the factory gate and Maintain Docs patch checks.

The YAML ``Policy`` decides whether the loop may send a claim to the agent and
is what ``attest.receipt/v0`` records. ``MaintainDocsPolicy`` checks a proposed
patch for weakened promises; it does not replace the receipt writer.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath

import yaml

from .models import Claim


@dataclass(frozen=True)
class AuthorityDecision:
    decision: str
    rule: str | None = None

    @property
    def allowed(self) -> bool:
        return self.decision == "doc_repair_allowed"


@dataclass(frozen=True)
class Policy:
    path: Path | None
    protected: tuple[dict, ...] = ()

    def decide(self, claim: Claim) -> AuthorityDecision:
        for rule in self.protected:
            if "claim" in rule and str(rule["claim"]) == claim.id:
                return AuthorityDecision("doc_repair_denied", f"claim: {claim.id}")
            match = rule.get("match")
            if isinstance(match, str) and match.casefold() in claim.span.text.casefold():
                return AuthorityDecision("doc_repair_denied", f"match: {match}")
        return AuthorityDecision("doc_repair_allowed")


def load_policy(doc_source: str, path: Path | None = None) -> Policy:
    candidate = path if path is not None else (None if doc_source.startswith("http") else Path(doc_source).resolve().parent / "attest.policy.yaml")
    if candidate is None or not candidate.exists():
        return Policy(candidate.resolve() if path is not None and candidate is not None else None)
    data = yaml.safe_load(candidate.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict) or not isinstance(data.get("protected", []), list):
        raise ValueError("policy must contain a protected list")
    rules = []
    for item in data.get("protected", []):
        if not isinstance(item, dict) or not (isinstance(item.get("match"), str) or isinstance(item.get("claim"), str)):
            raise ValueError("each protected rule needs a match string or claim id")
        rules.append(item)
    return Policy(candidate.resolve(), tuple(rules))


_POLICY_ID = "maintain-docs-v1"
_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_PROTECTED = (
    "at least",
    "at most",
    "no more than",
    "no fewer than",
    "minimum of",
    "maximum of",
    "up to",
    "within",
    "must not",
    "must",
    "shall",
    "never",
    "always",
    "only",
    "all",
    "every",
    "none",
    "cannot",
    "not",
    "required",
    "exactly",
    "without",
    "unless",
    "guarantee",
    "guarantees",
)


def _canonical_digest(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class MaintainDocsPolicy:
    """The only MVP authority: descriptive docs may be repaired, contracts may not."""

    policy_id: str = _POLICY_ID
    protected_contracts: tuple[str, ...] = field(default_factory=tuple)
    protected_markers: tuple[str, ...] = _PROTECTED

    @property
    def digest(self) -> str:
        return _canonical_digest(asdict(self))

    def authorize(
        self,
        *,
        diff: str,
        doc_name: str,
        base_text: str,
        candidate_text: str,
        target_start: int,
        target_end: int,
        target_claim_id: str,
        base_digest: str,
        candidate_digest: str,
    ) -> "PatchAuthorityDecision":
        problems = validate_diff_scope(diff, doc_name, target_start, target_end)
        old_lines = base_text.splitlines()
        new_lines = candidate_text.splitlines()
        old_start, old_end = target_start - 1, target_end
        # The diff validator guarantees every change is in this one span, so the
        # candidate span begins at the same line and absorbs the net line delta.
        new_start = old_start
        new_end = max(new_start, old_end + len(new_lines) - len(old_lines))
        old_target = "\n".join(old_lines[old_start:old_end])
        new_target = "\n".join(new_lines[new_start:new_end])
        for contract in self.protected_contracts:
            if contract.casefold() in old_target.casefold() and contract.casefold() not in new_target.casefold():
                problems.append(f"protected contract was removed: {contract}")
        old_markers = _markers(old_target, self.protected_markers)
        new_markers = _markers(new_target, self.protected_markers)
        weakened = [marker for marker, count in old_markers.items() if new_markers.get(marker, 0) < count]
        if weakened:
            problems.append("repair weakens protected promise marker(s): " + ", ".join(weakened))
        quantifier = r"\b(?:at least|no fewer than|minimum of|at most|no more than|maximum of|up to|within|exactly)\b"
        if re.search(quantifier, old_target, re.IGNORECASE):
            numbers = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?(?:\s*%)?")
            if numbers.findall(old_target) != numbers.findall(new_target):
                problems.append("quantified protected contract changed")
        reason = "authorized for Maintain Docs" if not problems else "; ".join(dict.fromkeys(problems))
        handoff = None if not problems else {
            "kind": "implementation_handoff",
            "target_claim_id": target_claim_id,
            "reason": reason,
            "next_step": "Change the implementation or obtain an explicit contract decision; do not weaken this protected documentation promise.",
        }
        return PatchAuthorityDecision(
            policy_id=self.policy_id,
            policy_digest=self.digest,
            target_claim_id=target_claim_id,
            allowed=not problems,
            reason=reason,
            base_digest=base_digest,
            candidate_digest=candidate_digest,
            protected_contracts=list(self.protected_contracts),
            violations=list(dict.fromkeys(problems)),
            implementation_handoff=handoff,
        )


@dataclass(frozen=True)
class PatchAuthorityDecision:
    policy_id: str
    policy_digest: str
    target_claim_id: str
    allowed: bool
    reason: str
    base_digest: str
    candidate_digest: str
    protected_contracts: list[str]
    violations: list[str]
    implementation_handoff: dict[str, str] | None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def _markers(text: str, markers: tuple[str, ...]) -> dict[str, int]:
    folded = " ".join(text.casefold().split())
    found: dict[str, int] = {}
    for marker in sorted(markers, key=len, reverse=True):
        pattern = r"(?<!\w)" + re.escape(marker.casefold()) + r"(?!\w)"
        count = len(re.findall(pattern, folded))
        if count:
            found[marker] = count
    return found


def _normalize_path(path: str) -> str:
    raw = path.split("\t", 1)[0].strip()
    if raw.startswith(("a/", "b/")):
        raw = raw[2:]
    pure = PurePosixPath(raw)
    if pure.is_absolute() or ".." in pure.parts:
        return ""
    return str(pure)


def diff_paths(diff: str) -> set[str]:
    paths: set[str] = set()
    for line in diff.splitlines():
        if line.startswith(("--- ", "+++ ")):
            value = line[4:].split("\t", 1)[0].strip()
            if value == "/dev/null":
                continue
            normalized = _normalize_path(value)
            if normalized:
                paths.add(normalized)
            else:
                paths.add(value)
    return paths


def validate_diff_scope(diff: str, doc_name: str, start_line: int, end_line: int) -> list[str]:
    """Reject patches that are not one doc file or whose edits escape one claim span."""
    problems: list[str] = []
    paths = diff_paths(diff)
    if paths != {doc_name}:
        problems.append(f"diff must touch only {doc_name}; touched {sorted(paths)}")
        return problems
    lines = diff.splitlines()
    hunk = False
    old_line = 0
    last_deleted: int | None = None
    saw_hunk = False
    for line in lines:
        if line.startswith("@@ "):
            match = _HUNK.match(line)
            if not match:
                problems.append("malformed unified-diff hunk")
                hunk = False
                continue
            old_line = int(match.group(1))
            last_deleted = None
            hunk = True
            saw_hunk = True
            continue
        if line.startswith(("--- ", "+++ ", "diff ", "index ")):
            continue
        if not hunk:
            continue
        if line.startswith(" "):
            old_line += 1
            last_deleted = None
        elif line.startswith("-") and not line.startswith("--- "):
            if not start_line <= old_line <= end_line:
                problems.append(f"removed line {old_line} lies outside claim lines {start_line}-{end_line}")
            last_deleted = old_line
            old_line += 1
        elif line.startswith("+") and not line.startswith("+++ "):
            anchor = last_deleted if last_deleted is not None else old_line
            if not start_line <= anchor <= end_line:
                problems.append(f"inserted text at line {anchor} lies outside claim lines {start_line}-{end_line}")
        elif line.startswith("\\"):
            continue
        else:
            problems.append("unexpected text in unified diff")
    if not saw_hunk:
        problems.append("diff has no patch hunk")
    return list(dict.fromkeys(problems))

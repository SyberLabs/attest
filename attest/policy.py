"""Authority policy for deciding whether a loop may edit documentation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

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

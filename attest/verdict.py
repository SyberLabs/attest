"""Verdict rules. Pure functions, no model.

Rules run in order and the first that matches wins:

    skipped > broken > stale > proven > unverifiable

Shell rules use the exit code and whether ``expects`` appears in stdout.
Browser rules use ``BrowserObservation.status`` and ``label_match``, the
normalized token-set ratio between what the doc promised and what the runner
read from the page. Thresholds come from rules.yaml.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import yaml
from rapidfuzz import fuzz

from .models import Claim, Evidence, Verdict

RULES_PATH = Path(__file__).resolve().parents[1] / "rules.yaml"


@dataclass(frozen=True)
class Rules:
    label_match: float = 0.8
    shell_timeout_s: float = 180.0
    loop_attempts: int = 1
    kane_max_steps: int = 15


def load_rules(path: Path | str | None = None) -> Rules:
    p = Path(path) if path else RULES_PATH
    if not p.exists():
        return Rules()
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return Rules(
        label_match=float(data.get("label_match", 0.8)),
        shell_timeout_s=float(data.get("shell_timeout_s", 180)),
        loop_attempts=int(data.get("loop_attempts", 1)),
        kane_max_steps=int(data.get("kane_max_steps", 15)),
    )


_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)


def normalize_label(s: str) -> str:
    """Case-fold, strip punctuation, collapse whitespace."""
    s = unicodedata.normalize("NFKC", s)
    s = s.casefold()
    s = _PUNCT_RE.sub(" ", s)
    return " ".join(s.split())


def label_match(expects: str, observed: str) -> float:
    a, b = normalize_label(expects), normalize_label(observed)
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    score = fuzz.token_set_ratio(a, b) / 100.0
    negations = {"not", "no", "never", "without", "cannot", "cant", "disabled", "disable", "off", "isnt", "arent", "wont", "dont", "doesnt"}
    if bool(set(a.split()) & negations) != bool(set(b.split()) & negations):
        return min(score, 0.5)
    # a different number is a different promise: "count is 1" vs "count is 2" scored 0.90
    nums_a = sorted(t for t in a.split() if t.isdigit())
    nums_b = sorted(t for t in b.split() if t.isdigit())
    if nums_a and nums_b and nums_a != nums_b:
        return min(score, 0.5)
    return score


def _norm_text(s: str) -> str:
    return " ".join(s.split())


def expects_in(expects: str, haystack: str | None) -> bool:
    if haystack is None:
        return False
    return _norm_text(expects) in _norm_text(haystack)


def observed_text(claim: Claim, evidence: Evidence) -> str | None:
    """The text the runner stored for this claim: its store_as key first, else
    everything it observed joined together."""
    obs = evidence.observed
    if not obs:
        return None
    if claim.store_as and claim.store_as in obs and obs[claim.store_as].strip():
        return obs[claim.store_as]
    return " ".join(v for v in obs.values() if v and v.strip()) or None


def judge(
    claim: Claim,
    evidence: Evidence | None,
    prior: dict[str, Verdict] | None = None,
    rules: Rules | None = None,
) -> Verdict:
    """Decide one claim. ``prior`` holds verdicts already reached for earlier claims."""
    rules = rules or Rules()
    prior = prior or {}

    # 1. skipped: a dependency is not proven
    for dep in claim.depends_on:
        pv = prior.get(dep)
        if pv is not None and pv.status != "proven" and pv.evidence is not None:
            return Verdict(
                claim_id=claim.id,
                status="skipped",
                reason=f"skipped: depends on {dep}, which is {pv.status}",
                evidence=pv.evidence,
            )

    if claim.kind == "prose":
        return Verdict(claim_id=claim.id, status="unverifiable", reason="unverifiable: prose promises nothing observable")

    if evidence is None:
        if claim.kind == "browser":
            return Verdict(claim_id=claim.id, status="unverifiable", reason="unverifiable: browser claim with no reachable URL")
        return Verdict(claim_id=claim.id, status="unverifiable", reason=f"unverifiable: no evidence for {claim.kind} claim")

    if claim.kind == "shell":
        return _judge_shell(claim, evidence)
    if claim.kind == "assert":
        return _judge_assert(claim, evidence)
    return _judge_browser(claim, evidence, rules)


def _judge_shell(claim: Claim, ev: Evidence) -> Verdict:
    if ev.kind != "shell":
        # invariant: a shell claim is never proven from a browser runner
        return Verdict(claim_id=claim.id, status="unverifiable", reason="unverifiable: shell claim has non-shell evidence", evidence=ev)
    if ev.timed_out:
        return Verdict(claim_id=claim.id, status="broken", reason="broken: command timed out", evidence=ev)
    if ev.service_ready and ev.exit_code is None:
        if claim.expects and not expects_in(claim.expects, ev.stdout_tail):
            return Verdict(
                claim_id=claim.id,
                status="stale",
                reason=f"stale: service ready but {claim.expects!r} not in output",
                evidence=ev,
                label_match=0.0,
            )
        reason = "proven: service ready and promised output found" if claim.expects else "proven: service ready"
        return Verdict(claim_id=claim.id, status="proven", reason=reason, evidence=ev, label_match=1.0 if claim.expects else None)
    if ev.exit_code != 0:
        return Verdict(claim_id=claim.id, status="broken", reason=f"broken: exit code {ev.exit_code}", evidence=ev)
    if claim.expects:
        if expects_in(claim.expects, ev.stdout_tail):
            return Verdict(claim_id=claim.id, status="proven", reason="proven: exit 0 and promised output found", evidence=ev, label_match=1.0)
        return Verdict(
            claim_id=claim.id,
            status="stale",
            reason=f"stale: exit 0 but {claim.expects!r} not in output",
            evidence=ev,
            label_match=0.0,
        )
    return Verdict(claim_id=claim.id, status="proven", reason="proven: exit 0", evidence=ev)


def _judge_assert(claim: Claim, ev: Evidence) -> Verdict:
    if ev.kind != "shell":
        return Verdict(claim_id=claim.id, status="unverifiable", reason="unverifiable: assert needs shell output", evidence=ev)
    if not claim.expects:
        return Verdict(claim_id=claim.id, status="unverifiable", reason="unverifiable: assert without expected text", evidence=ev)
    if expects_in(claim.expects, ev.stdout_tail):
        return Verdict(claim_id=claim.id, status="proven", reason="proven: expected output found in previous step", evidence=ev, label_match=1.0)
    return Verdict(claim_id=claim.id, status="stale", reason="stale: expected output not in previous step's output", evidence=ev, label_match=0.0)


def _judge_browser(claim: Claim, ev: Evidence, rules: Rules) -> Verdict:
    if ev.kind != "browser" or ev.observation is None:
        # invariant: a browser claim is never proven from the shell runner
        return Verdict(claim_id=claim.id, status="unverifiable", reason="unverifiable: browser claim has non-browser evidence", evidence=ev)
    obs = ev.observation
    if obs.status == "unreachable":
        return Verdict(claim_id=claim.id, status="unverifiable", reason="unverifiable: page not reachable", evidence=ev)
    if obs.status == "failed":
        return Verdict(claim_id=claim.id, status="broken", reason="broken: browser step failed", evidence=ev)
    # No healing rule: on event day Kane never healed (a drifted page replayed and stored
    # the new text), so there is no remark pattern to trust. Drift lands in label_match.
    if not claim.expects:
        return Verdict(claim_id=claim.id, status="proven", reason="proven: step completed, nothing promised", evidence=ev)
    seen = observed_text(claim, ev)
    if seen is None:
        return Verdict(
            claim_id=claim.id,
            status="unverifiable",
            reason="unverifiable: step passed but the runner stored no label to compare",
            evidence=ev,
        )
    score = label_match(claim.expects, seen)
    if score < rules.label_match:
        return Verdict(
            claim_id=claim.id,
            status="stale",
            reason=f"stale: label_match {score:.2f} < {rules.label_match:.2f}; doc says {claim.expects!r}, page says {seen!r}",
            evidence=ev,
            label_match=score,
        )
    return Verdict(
        claim_id=claim.id,
        status="proven",
        reason=f"proven: step passed and label_match {score:.2f} >= {rules.label_match:.2f}",
        evidence=ev,
        label_match=score,
    )


def judge_all(claims: list[Claim], evidence: dict[str, Evidence], rules: Rules | None = None) -> list[Verdict]:
    """Judge claims in doc order, feeding each verdict to the next as ``prior``."""
    prior: dict[str, Verdict] = {}
    out: list[Verdict] = []
    for c in claims:
        v = judge(c, evidence.get(c.id), prior, rules)
        prior[c.id] = v
        out.append(v)
    return out

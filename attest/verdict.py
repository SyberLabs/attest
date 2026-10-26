"""Verdict rules. Pure functions, no model.

Rules run in order and the first that matches wins:

    skipped > broken > stale > proven > unverifiable

Shell rules use the exit code and whether ``expects`` appears in stdout.
Browser rules use ``BrowserObservation.status`` and ``label_match``, the
normalized token-set ratio between what the doc promised and what the runner
read from the page. Prompt rules (docs/CLI-RUNNER-SPEC.md) compare the Host's
``PromptObservation`` with the doc's result fence by exact equality after three
recorded normalizations; no similarity score ever gates a prompt or shell
claim. Thresholds come from rules.yaml.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import yaml
from rapidfuzz import fuzz

from .models import Claim, Evidence, PromptObservation, Verdict
from .prompt_runner import SIMULATION_MESSAGE_PREFIX, SimulationPromptObservation
from .runner import SIMULATION_NOTE_PREFIX, SimulationBrowserObservation

RULES_PATH = Path(__file__).resolve().parents[1] / "rules.yaml"


@dataclass(frozen=True)
class Rules:
    label_match: float = 0.8
    shell_timeout_s: float = 180.0
    loop_attempts: int = 1
    kane_max_steps: int = 15
    prompt_timeout_s: float = 30.0
    ready_timeout_s: float = 60.0


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
        prompt_timeout_s=float(data.get("prompt_timeout_s", 30)),
        ready_timeout_s=float(data.get("ready_timeout_s", 60)),
    )


_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)


def normalize_label(s: str) -> str:
    """Case-fold, strip punctuation, collapse whitespace."""
    s = unicodedata.normalize("NFKC", s)
    s = s.casefold()
    s = _PUNCT_RE.sub(" ", s)
    return " ".join(s.split())


def label_match(expects: str, observed: str) -> float:
    """Fuzzy similarity for suggestions only; never use this score to prove a claim."""
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


# the three normalizations a prompt result goes through before byte comparison;
# every prompt verdict reason names them so a reader knows nothing else was folded
RESULT_NORMALIZATIONS = "CRLF to LF, trailing spaces removed, one final newline"


def normalize_result(s: str) -> str:
    """CRLF to LF, trailing spaces and tabs removed at line ends, exactly one final newline."""
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    lines = [ln.rstrip(" \t") for ln in s.split("\n")]
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines) + "\n"


def results_equal(expects: str, result: str | None) -> bool:
    """Exact comparison of a prompt result with the doc's result fence."""
    if result is None:
        return False
    return normalize_result(expects) == normalize_result(result)


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
        return _judge_shell(claim, evidence, rules)
    if claim.kind == "assert":
        return _judge_assert(claim, evidence)
    if claim.kind == "prompt":
        return _judge_prompt(claim, evidence)
    return _judge_browser(claim, evidence, rules)


def _judge_shell(claim: Claim, ev: Evidence, rules: Rules | None = None) -> Verdict:
    rules = rules or Rules()
    if ev.kind != "shell":
        # invariant: a shell claim is never proven from a browser runner or a prompt Host
        return Verdict(claim_id=claim.id, status="unverifiable", reason="unverifiable: shell claim has non-shell evidence", evidence=ev)
    v = _judge_shell_run(claim, ev, rules)
    if ev.substitute is not None and v.status == "proven":
        # a step that only works when rewritten is drift: the badge says so
        return v.model_copy(
            update={
                "status": "stale",
                "reason": f"stale: passed only through the substitute {ev.substitute!r} in place of {(ev.command or '').strip()!r}",
                "label_match": None,
            }
        )
    return v


def _judge_shell_run(claim: Claim, ev: Evidence, rules: Rules) -> Verdict:
    if ev.timed_out:
        if ev.ready_url:
            seen = f"last answered {ev.ready_status}" if ev.ready_status is not None else "never answered"
            return Verdict(
                claim_id=claim.id,
                status="broken",
                reason=f"broken: service readiness URL {ev.ready_url} did not answer 200 within {rules.ready_timeout_s:g}s ({seen})",
                evidence=ev,
            )
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
        if ev.ready_url:
            reason = f"proven: service ready, {ev.ready_url} answered 200"
            if claim.expects:
                reason += " and promised output found"
        else:
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


def _judge_prompt(claim: Claim, ev: Evidence) -> Verdict:
    """Prompt rules from docs/CLI-RUNNER-SPEC.md: exact yes/no checks, no similarity score."""
    if ev.kind != "prompt" or ev.prompt is None:
        # invariant: a prompt claim is never proven from the shell runner or a browser runner
        return Verdict(claim_id=claim.id, status="unverifiable", reason="unverifiable: prompt claim has non-prompt evidence", evidence=ev)
    obs: PromptObservation = ev.prompt
    if isinstance(obs, SimulationPromptObservation) or (obs.message and obs.message.startswith(SIMULATION_MESSAGE_PREFIX)):
        return Verdict(
            claim_id=claim.id,
            status="unverifiable",
            reason="unverifiable: simulation-only prompt observation is not verification evidence",
            evidence=ev,
        )
    if obs.status == "needs_script":
        return Verdict(
            claim_id=claim.id,
            status="unverifiable",
            reason=f"unverifiable: the run wanted a model reply the doc never showed (round {obs.rounds + 1}); {obs.message or 'no script'}",
            evidence=ev,
        )
    if obs.status == "refused":
        return Verdict(claim_id=claim.id, status="unverifiable", reason=f"unverifiable: the Host refused the run; {obs.message or 'no message'}", evidence=ev)
    failed = obs.status in ("parse_error", "failed")
    where = f" at line {obs.error_line}" if obs.error_line is not None else ""
    error = f"{obs.error_kind or obs.status}{where}" + (f": {obs.message}" if obs.message else "")
    if not claim.expects_error:
        if failed:
            return Verdict(claim_id=claim.id, status="broken", reason=f"broken: success promised but the run ended {obs.status} ({error})", evidence=ev)
        # completed
        if claim.expects and not results_equal(claim.expects, obs.result):
            return Verdict(
                claim_id=claim.id,
                status="stale",
                reason=f"stale: completed but the result differs from the doc's result fence (compared after {RESULT_NORMALIZATIONS}); scripted run",
                evidence=ev,
            )
        if claim.expects:
            reason = f"proven: completed and the result equals the doc's result fence (compared after {RESULT_NORMALIZATIONS}); scripted run"
        else:
            reason = "proven: completed as the doc promised; scripted run"
        return Verdict(claim_id=claim.id, status="proven", reason=reason, evidence=ev)
    # failure promised
    if not failed:
        return Verdict(claim_id=claim.id, status="broken", reason="broken: failure promised but the run completed", evidence=ev)
    quoted = (claim.expects or "").strip()
    if quoted and not (obs.error_kind == quoted or (obs.message is not None and quoted in obs.message)):
        return Verdict(
            claim_id=claim.id,
            status="stale",
            reason=f"stale: the run failed as promised but the error differs from the doc's {quoted!r} (got {error})",
            evidence=ev,
        )
    if quoted:
        reason = f"proven: the run failed as promised and the error kind equals or the message contains {quoted!r}; scripted run"
    else:
        reason = f"proven: the run failed as the doc promised ({error}); scripted run"
    return Verdict(claim_id=claim.id, status="proven", reason=reason, evidence=ev)


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
        # invariant: a browser claim is never proven from the shell runner or a prompt Host
        return Verdict(claim_id=claim.id, status="unverifiable", reason="unverifiable: browser claim has non-browser evidence", evidence=ev)
    obs = ev.observation
    if isinstance(obs, SimulationBrowserObservation) or (obs.note and obs.note.startswith(SIMULATION_NOTE_PREFIX)):
        return Verdict(
            claim_id=claim.id,
            status="unverifiable",
            reason="unverifiable: simulation-only browser observation is not verification evidence",
            evidence=ev,
        )
    if obs.status == "unreachable":
        return Verdict(claim_id=claim.id, status="unverifiable", reason="unverifiable: page not reachable", evidence=ev)
    if obs.status == "failed":
        return Verdict(claim_id=claim.id, status="broken", reason="broken: browser step failed", evidence=ev)
    # Kane does not heal browser steps. Proof still requires an explicit expected label.
    if not claim.expects or not claim.expects.strip():
        return Verdict(
            claim_id=claim.id,
            status="unverifiable",
            reason="unverifiable: browser claim has no exact expected text",
            evidence=ev,
        )
    seen = observed_text(claim, ev)
    if seen is None:
        return Verdict(
            claim_id=claim.id,
            status="unverifiable",
            reason="unverifiable: step passed but the runner stored no label to compare",
            evidence=ev,
        )
    score = label_match(claim.expects, seen)
    if normalize_label(claim.expects) != normalize_label(seen):
        return Verdict(
            claim_id=claim.id,
            status="stale",
            reason=(
                f"stale: exact label mismatch; similarity {score:.2f} is suggestion-only; "
                f"doc says {claim.expects!r}, page says {seen!r}"
            ),
            evidence=ev,
            label_match=score,
        )
    return Verdict(
        claim_id=claim.id,
        status="proven",
        reason=f"proven: exact normalized label match; similarity {score:.2f} is suggestion-only",
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

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


_ELIDED = re.compile(r"^\s*(?:\.\.\.|…|\[\.\.\.\]|\[…\])\s*$")
TAIL_LINES = 50  # the shell runner keeps this many output lines


def lines_in_order(expects: str, haystack: str | None) -> bool:
    """Every non-blank line the doc shows appears in the output, in the same order,
    each within one output line after whitespace collapse; a placeholder stands for
    run-specific text and a lone `...` line marks an elision. Only the last
    TAIL_LINES shown lines are checked, since that is all the runner keeps."""
    if haystack is None:
        return False
    shown = [ln for ln in expects.splitlines() if ln.strip() and not _ELIDED.match(ln)][-TAIL_LINES:]
    got = [_norm_text(ln) for ln in haystack.splitlines()]
    pos = 0
    for line in shown:
        want = _norm_text(line)
        pattern = placeholder_pattern(want)
        while pos < len(got) and not (pattern.search(got[pos]) if pattern else want in got[pos]):
            pos += 1
        if pos == len(got):
            return False
        pos += 1
    return True


def output_shown(expects: str, haystack: str | None) -> bool:
    """The doc's promised output appears: as one run of text, or, for several lines,
    line by line in order."""
    if expects_in(expects, haystack):
        return True
    return "\n" in expects.strip() and lines_in_order(expects, haystack)


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


# the guides' placeholder for run-specific text inside a result fence: {nonce}, {identity}, {...},
# and a timestamp literal, which stands for the run's own clock (the Harness stamps every run)
_TIMESTAMP = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z"
_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_.]*|\.\.\.)\}|(" + _TIMESTAMP + ")")


def placeholders_in(text: str) -> list[str]:
    """The distinct placeholders a result fence uses, in order of first appearance;
    a timestamp literal is listed as ``<timestamp>``."""
    out: list[str] = []
    for m in _PLACEHOLDER.finditer(text):
        label = "<timestamp>" if m.group(2) else m.group(0)
        if label not in out:
            out.append(label)
    return out


def placeholder_pattern(text: str) -> re.Pattern[str] | None:
    """A regex for text that uses placeholders, or None when it uses none.

    Each ``{name}`` stands for one run of text within a line; the same name must
    stand for the same text everywhere it appears. ``{...}`` is any such run. A
    timestamp literal stands for any timestamp of the same shape."""
    if not _PLACEHOLDER.search(text):
        return None
    parts: list[str] = []
    seen: set[str] = set()
    pos = 0
    for m in _PLACEHOLDER.finditer(text):
        parts.append(re.escape(text[pos : m.start()]))
        if m.group(2):
            parts.append(_TIMESTAMP)
            pos = m.end()
            continue
        name = m.group(1)
        if name == "...":
            parts.append(r"[^\n]+?")
        else:
            group = "p_" + name.replace(".", "_")
            if group in seen:
                parts.append(f"(?P={group})")
            else:
                seen.add(group)
                parts.append(f"(?P<{group}>[^\n]+?)")
        pos = m.end()
    parts.append(re.escape(text[pos:]))
    return re.compile("".join(parts))


def results_equal(expects: str, result: str | None) -> bool:
    """Exact comparison of a prompt result with the doc's result fence, where a
    placeholder in the fence stands for run-specific text."""
    if result is None:
        return False
    want, got = normalize_result(expects), normalize_result(result)
    pattern = placeholder_pattern(want)
    if pattern is None:
        return want == got
    return pattern.fullmatch(got) is not None


def failure_shown(expects: str, error_kind: str | None, message: str | None) -> bool:
    """True when the doc's result fence shows this failure: it names the error
    kind, or every non-blank line of it appears in the failure's message, with
    placeholders standing for run-specific text."""
    shown = normalize_result(expects).rstrip("\n")
    if not shown.strip():
        return False
    if error_kind is not None and error_kind == shown.strip():
        return True
    if message is None:
        return False
    text = normalize_result(message)
    for line in shown.split("\n"):
        if not line.strip():
            continue
        pattern = placeholder_pattern(line)
        if pattern is None:
            if line not in text:
                return False
        elif pattern.search(text) is None:
            return False
    return True


def _placeholder_note(expects: str | None) -> str:
    used = placeholders_in(expects or "")
    return f", with {', '.join(used)} standing for run-specific text" if used else ""


def observed_text(claim: Claim, evidence: Evidence) -> str | None:
    """The text the runner stored for this claim: its store_as key first, else
    everything it observed joined together."""
    obs = evidence.observed
    if not obs:
        return None
    if claim.store_as and claim.store_as in obs and obs[claim.store_as].strip():
        return obs[claim.store_as]
    return " ".join(v for v in obs.values() if v and v.strip()) or None


def satisfies_dependents(v: Verdict) -> bool:
    """A dependency lets later claims run when it is proven, or when it is stale only
    because it passed through a substitute (the step ran, the doc's wording is the drift)."""
    if v.status == "proven":
        return True
    ev = v.evidence
    if v.status == "stale" and ev is not None and ev.kind == "shell" and (ev.claim_id == v.claim_id):
        # the step ran and succeeded; what is stale is the doc's wording of its command
        # (a substitute) or of its output. Later steps can still run on what it did.
        return ev.exit_code == 0 or ev.service_ready
    return False


def judge(
    claim: Claim,
    evidence: Evidence | None,
    prior: dict[str, Verdict] | None = None,
    rules: Rules | None = None,
) -> Verdict:
    """Decide one claim. ``prior`` holds verdicts already reached for earlier claims."""
    rules = rules or Rules()
    prior = prior or {}

    # 1. skipped: a dependency is not proven (a substitute-capped pass still counts)
    for dep in claim.depends_on:
        pv = prior.get(dep)
        if pv is not None and not satisfies_dependents(pv) and pv.evidence is not None:
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
        if claim.expects and not output_shown(claim.expects, ev.stdout_tail):
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
        if output_shown(claim.expects, ev.stdout_tail):
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
            # A result fence shows what the run leaves behind: its result, or the
            # failure's message when the doc documents a failure. The Language
            # guide writes every documented failure that way.
            if claim.expects and failure_shown(claim.expects, obs.error_kind, obs.message):
                return Verdict(
                    claim_id=claim.id,
                    status="proven",
                    reason=f"proven: the run ended {obs.status} and the doc's result fence is that failure's message (each line compared after {RESULT_NORMALIZATIONS}{_placeholder_note(claim.expects)}); scripted run",
                    evidence=ev,
                )
            return Verdict(claim_id=claim.id, status="broken", reason=f"broken: success promised but the run ended {obs.status} ({error})", evidence=ev)
        # completed
        if claim.expects and not results_equal(claim.expects, obs.result):
            return Verdict(
                claim_id=claim.id,
                status="stale",
                reason=f"stale: completed but the result differs from the doc's result fence (compared after {RESULT_NORMALIZATIONS}{_placeholder_note(claim.expects)}); scripted run",
                evidence=ev,
            )
        if claim.expects:
            reason = f"proven: completed and the result equals the doc's result fence (compared after {RESULT_NORMALIZATIONS}{_placeholder_note(claim.expects)}); scripted run"
        else:
            reason = "proven: completed as the doc promised; scripted run"
        return Verdict(claim_id=claim.id, status="proven", reason=reason, evidence=ev)
    # failure promised
    if not failed:
        return Verdict(claim_id=claim.id, status="broken", reason="broken: failure promised but the run completed", evidence=ev)
    quoted = (claim.expects or "").strip()
    if quoted and not failure_shown(claim.expects or "", obs.error_kind, obs.message):
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


def json_in(expects: str, haystack: str | None) -> bool | None:
    """When ``expects`` is one JSON value: True if the output holds a JSON value
    equal to it (whitespace and key order aside), False if not; None when
    ``expects`` is not JSON, so the text rule decides."""
    import json

    try:
        wanted = json.loads(expects.strip())
    except ValueError:
        return None
    if not isinstance(wanted, (dict, list)):
        return None
    if haystack is None:
        return False
    decoder = json.JSONDecoder()
    for i, ch in enumerate(haystack):
        if ch not in "{[":
            continue
        try:
            found, _ = decoder.raw_decode(haystack, i)
        except ValueError:
            continue
        if found == wanted:
            return True
    return False


def _judge_assert(claim: Claim, ev: Evidence) -> Verdict:
    if ev.kind != "shell":
        return Verdict(claim_id=claim.id, status="unverifiable", reason="unverifiable: assert needs shell output", evidence=ev)
    if not claim.expects:
        return Verdict(claim_id=claim.id, status="unverifiable", reason="unverifiable: assert without expected text", evidence=ev)
    as_json = json_in(claim.expects, ev.stdout_tail)
    if as_json is not None:
        # a printed JSON response: equal as JSON, whatever the spacing
        if as_json:
            return Verdict(claim_id=claim.id, status="proven", reason="proven: expected JSON response found in previous step's output (compared as JSON)", evidence=ev, label_match=1.0)
        return Verdict(claim_id=claim.id, status="stale", reason="stale: expected JSON response not in previous step's output (compared as JSON)", evidence=ev, label_match=0.0)
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

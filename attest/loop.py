"""The closed loop: brief, one agent call, doc-only diff, re-prove one claim.

Trigger: any stale or broken verdict on a shell or browser claim.
The agent (``codex exec`` or ``claude -p``, from ``ATTEST_AGENT_CMD``) gets the
brief on stdin and works in a scratch checkout that holds only the doc. Its diff
must pass ``git apply --check`` and touch nothing but the doc. Then only the
changed span is re-compiled and only that claim re-runs, through the same kind
of runner that produced the original evidence. Both verdicts land in the ledger
under one turn. One attempt, then a human.
"""

from __future__ import annotations

import difflib
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .compiler import recompile_claim
from .grounding import ground
from .ingest import ingest_text
from .ledger import Ledger
from .models import Brief, Claim, Doc, Evidence, Verdict
from .pipeline import ProveResult
from .runner import BrowserRunner
from .shell import ShellSession
from .verdict import Rules, judge, load_rules

# read-only: the brief carries the numbered excerpt, and the agent must return a diff, not edit
AGENT_CMD = os.environ.get("ATTEST_AGENT_CMD", "codex exec -s read-only --skip-git-repo-check")
AGENT_TIMEOUT_S = int(os.environ.get("ATTEST_AGENT_TIMEOUT_S", "240"))
CONTEXT_LINES = 3


# -- brief -------------------------------------------------------------------------


def build_brief(doc: Doc, claim: Claim, verdict: Verdict, grounding: list[str], doc_path: str) -> Brief:
    start = max(1, claim.span.start_line - CONTEXT_LINES)
    end = min(len(doc.lines), claim.span.end_line + CONTEXT_LINES)
    excerpt = doc.numbered(start, end)
    return Brief(claim_id=claim.id, verdict=verdict, doc_excerpt=excerpt, grounding=grounding, attempts=0)


def brief_text(brief: Brief, claim: Claim, doc_path: str) -> str:
    """The Markdown message the agent reads. Kept under 60 lines."""
    v = brief.verdict
    ev = v.evidence
    lines = [
        f"# Attest brief for {claim.id} in `{doc_path}`",
        "",
        "## Doc excerpt (line numbers are real)",
        "```",
        brief.doc_excerpt,
        "```",
        "",
        f"## Verdict: {v.status}",
        f"Rule: {v.reason}",
        f"Claim kind: {claim.kind}. Lines {claim.span.start_line}-{claim.span.end_line}.",
    ]
    if claim.expects:
        lines.append(f"The doc promises: {claim.expects!r}")
    lines += ["", "## What the runner saw"]
    if ev is None:
        lines.append("no evidence")
    elif ev.kind == "shell":
        lines.append(f"command: `{(ev.command or '').strip()}`")
        lines.append(f"exit code: {'timed out' if ev.timed_out else ev.exit_code}")
        tail = (ev.stdout_tail or "").strip().splitlines()[-12:]
        if tail:
            lines += ["output tail:", "```", *tail, "```"]
    else:
        obs = ev.observation
        lines.append(f"browser step status: {obs.status if obs else 'none'}")
        for k, val in (obs.observed if obs else {}).items():
            lines.append(f"observed {k} = {val!r}")
        if obs and obs.note:
            # the Kane runner joins remark, failure record, root cause and fix with " | "
            parts = [p.strip() for p in obs.note.split(" | ") if p.strip() and not p.startswith("pack: ")]
            if parts:
                lines.append(f"runner note: {parts[0][:300]}")
                lines += [f"- {p[:300]}" for p in parts[1:6]]
        if obs and obs.screenshot:
            lines.append(f"screenshot: {obs.screenshot}")
    lines += ["", "## Grounding"]
    lines += [f"- {g}" for g in brief.grounding[:12]] or ["- none"]
    lines += [
        "",
        "## Instruction",
        f"Edit only `{doc_path}`. Change the smallest span that makes the claim true.",
        "Do not touch any other file and do not change the application.",
        "Return a unified diff (git format, paths a/ and b/) and nothing else.",
    ]
    return "\n".join(lines) + "\n"


# -- agent -------------------------------------------------------------------------


def call_agent(text: str, cwd: Path, cmd: str | None = None, timeout_s: int = AGENT_TIMEOUT_S) -> str:
    cmd = cmd or AGENT_CMD
    try:
        p = subprocess.run(
            cmd,
            input=text,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
            shell=True,
        )
    except subprocess.TimeoutExpired:
        return ""
    return p.stdout


# the closing fence must sit at column 0: diff lines always start with +, -, space or @
_DIFF_FENCE = re.compile(r"^```(?:diff|patch)?[ \t]*\n(.*?)^```[ \t]*$", re.DOTALL | re.MULTILINE)


def extract_diff(output: str) -> str | None:
    """Pull a unified diff out of agent chatter."""
    for m in _DIFF_FENCE.finditer(output):
        body = m.group(1)
        if "+++ " in body and "--- " in body:
            return body if body.endswith("\n") else body + "\n"
    idx = output.find("--- ")
    if idx == -1:
        return None
    body = output[idx:]
    if "+++ " not in body:
        return None
    # cut trailing prose after the last hunk line
    kept = []
    for ln in body.splitlines():
        if kept and not (ln.startswith(("+", "-", " ", "@@", "diff ", "index ", "\\"))) and ln.strip() == "":
            kept.append(ln)
            continue
        if kept and not ln.startswith(("+", "-", " ", "@@", "diff ", "index ", "\\")):
            break
        kept.append(ln)
    return "\n".join(kept) + "\n"


def diff_paths(diff: str) -> set[str]:
    paths = set()
    for ln in diff.splitlines():
        if ln.startswith(("--- ", "+++ ")):
            p = ln[4:].split("\t")[0].strip()
            if p == "/dev/null":
                continue
            p = re.sub(r"^[ab]/", "", p)
            paths.add(p)
    return paths


# -- scratch checkout ------------------------------------------------------------------


@dataclass
class Checkout:
    root: Path
    doc_name: str

    @property
    def doc_path(self) -> Path:
        return self.root / self.doc_name

    def git(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *args], cwd=str(self.root), capture_output=True, text=True, encoding="utf-8", errors="replace")

    def apply(self, diff: str) -> tuple[bool, str]:
        """git apply --check, then apply. Returns (ok, message)."""
        touched = diff_paths(diff)
        if not touched:
            return False, "diff names no files"
        if touched != {self.doc_name}:
            return False, f"diff touches files other than the doc: {sorted(touched - {self.doc_name})}"
        patch = self.root / ".attest.patch"
        patch.write_text(diff, encoding="utf-8", newline="\n")
        # --recount: agents often get hunk line counts wrong while the hunk body is right
        check = self.git("apply", "--check", "--recount", "--unidiff-zero", "--whitespace=nowarn", str(patch))
        if check.returncode != 0:
            return False, f"git apply --check failed: {check.stderr.strip()[:300]}"
        done = self.git("apply", "--recount", "--unidiff-zero", "--whitespace=nowarn", str(patch))
        patch.unlink(missing_ok=True)
        if done.returncode != 0:
            return False, f"git apply failed: {done.stderr.strip()[:300]}"
        return True, "applied"


def make_checkout(doc: Doc) -> Checkout:
    root = Path(tempfile.mkdtemp(prefix="attest-loop-"))
    name = Path(doc.source).name if not doc.source.startswith("http") else "doc.md"
    co = Checkout(root, name)
    co.doc_path.write_text("\n".join(doc.lines) + "\n", encoding="utf-8", newline="\n")
    co.git("init", "-q")
    co.git("config", "user.email", "attest@localhost")
    co.git("config", "user.name", "attest")
    co.git("config", "core.autocrlf", "false")
    co.git("add", name)
    co.git("commit", "-q", "-m", "doc before loop")
    return co


# -- re-prove -----------------------------------------------------------------------


def shift_spans(claims: list[Claim], old_lines: list[str], new_lines: list[str], changed_id: str) -> list[Claim]:
    """Move other claims' spans by the net line change above them."""
    sm = difflib.SequenceMatcher(a=old_lines, b=new_lines, autojunk=False)
    out = []
    for c in claims:
        if c.id == changed_id:
            out.append(c)
            continue
        delta = 0
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "equal":
                continue
            if i2 <= c.span.start_line - 1:
                delta += (j2 - j1) - (i2 - i1)
        if delta:
            s, e = c.span.start_line + delta, c.span.end_line + delta
            text = "\n".join(new_lines[s - 1 : e])
            out.append(c.model_copy(update={"span": c.span.model_copy(update={"start_line": s, "end_line": e, "text": text})}))
        else:
            out.append(c)
    return out


def rerun_claim(
    claim: Claim,
    result: ProveResult,
    runner: BrowserRunner,
    rules: Rules,
    shell_env: dict[str, str] | None = None,
) -> Evidence | None:
    """Run exactly one claim again through the runner kind that produced its evidence."""
    original = result.evidence.get(claim.id)
    if claim.kind == "shell":
        scratch = result.scratch or Path(tempfile.mkdtemp(prefix="attest-rerun-"))
        session = ShellSession(workdir=scratch, timeout_s=rules.shell_timeout_s, env=shell_env or {})
        ev = session.run(claim)
        if session.servers:
            # a re-run server stays up for later browser claims in this loop
            result._rerun_sessions = getattr(result, "_rerun_sessions", []) + [session]  # type: ignore[attr-defined]
        else:
            session.close()
        return ev
    if claim.kind == "browser":
        base_url = None
        for dep in claim.depends_on:
            dev = result.evidence.get(dep)
            if dev and dev.kind == "shell" and dev.detected_url:
                base_url = dev.detected_url
        if original is not None and original.kind != "browser":
            return None
        obs = runner.observe(claim, base_url)
        return Evidence(claim_id=claim.id, kind="browser", observation=obs)
    return None


# -- the loop -------------------------------------------------------------------------


def run_loop(
    result: ProveResult,
    runner: BrowserRunner,
    home: Path,
    rules: Rules | None = None,
    agent_cmd: str | None = None,
    llm=None,
    shell_env: dict[str, str] | None = None,
    log=None,
    apply_to_source: bool = False,
) -> ProveResult:
    """Fix the doc for every failing claim, one attempt each, and re-prove those claims."""
    rules = rules or load_rules()
    attempts_allowed = max(1, rules.loop_attempts)
    ledger = Ledger(home / "ledger.jsonl")
    recorded_runs = getattr(result, "_kane_runs_count", 0)
    doc = result.doc
    verdict_map = result.by_id
    for claim in result.failing():
        before = verdict_map[claim.id]
        grounding = ground(doc, claim)
        doc_path = Path(doc.source).name if not doc.source.startswith("http") else "doc.md"
        brief = build_brief(doc, claim, before, grounding, doc_path)
        co = make_checkout(doc)
        patched_doc: Doc | None = None
        message = ""
        for _ in range(attempts_allowed):
            brief = brief.model_copy(update={"attempts": brief.attempts + 1})
            output = call_agent(brief_text(brief, claim, co.doc_name), co.root, cmd=agent_cmd)
            diff = extract_diff(output)
            if diff is None:
                message = "agent returned no unified diff"
                continue
            brief = brief.model_copy(update={"patch": diff})
            ok, message = co.apply(diff)
            if ok:
                text = co.doc_path.read_text(encoding="utf-8")
                patched_doc = ingest_text(text, source=doc.source, commit=doc.commit)
                break
        shutil.rmtree(co.root, ignore_errors=True)
        result.briefs[claim.id] = brief
        result.prior[claim.id] = before
        if patched_doc is None:
            after = before.model_copy(update={"reason": f"{before.reason} (loop: {message})"})
            _replace_verdict(result, after)
            ledger.record(doc.sha256, result.turn, [claim], [after], looped=True)
            if log:
                log(claim, after)
            continue
        # re-compile only the changed span, keep every other claim where it now sits
        new_claims = shift_spans(result.claims, doc.lines, patched_doc.lines, claim.id)
        fresh = recompile_claim(patched_doc, claim, llm=llm)
        new_claims = [fresh if c.id == claim.id else c for c in new_claims]
        ev = rerun_claim(fresh, result, runner, rules, shell_env=shell_env)
        runs = getattr(runner, "runs", None)
        if runs is not None and len(runs) > recorded_runs:
            recorded_runs = ledger.record_new_kane_runs(result.doc.sha256, result.turn, runs, recorded_runs)
            result._kane_runs_count = recorded_runs  # type: ignore[attr-defined]
        if ev is not None:
            result.evidence[claim.id] = ev
        after = judge(fresh, ev, {k: v for k, v in result.by_id.items() if k != claim.id}, rules)
        after = after.model_copy(update={"reason": f"{after.reason} (after loop)"})
        result.claims = new_claims
        _replace_verdict(result, after)
        verdict_map = result.by_id
        doc = patched_doc.model_copy(update={"claims": new_claims, "sha256": result.doc.sha256})
        result.doc = doc
        ledger.record(result.doc.sha256, result.turn, [fresh], [after], looped=True)
        if fresh.kind == "browser":
            _rejudge_dependents(result, fresh, runner, rules, ledger, log)
            verdict_map = result.by_id
        if apply_to_source and not result.doc.source.startswith("http"):
            Path(result.doc.source).write_text("\n".join(patched_doc.lines) + "\n", encoding="utf-8")
        if log:
            log(fresh, after)
    for s in getattr(result, "_rerun_sessions", []):
        s.close()
    return result


def _rejudge_dependents(result: ProveResult, fixed: Claim, runner: BrowserRunner, rules: Rules, ledger: Ledger, log=None) -> None:
    """Later browser claims skipped behind the fixed one get judged from the runner's fresh observations.

    A batching runner re-runs the whole test file when one claim changes, so it
    already holds new observations for the claims after it; nothing runs again.
    """
    cached = getattr(runner, "cached", None)
    if cached is None:
        return
    after_fixed = False
    for c in result.claims:
        if c.id == fixed.id:
            after_fixed = True
            continue
        if not after_fixed or c.kind != "browser" or result.by_id[c.id].status != "skipped":
            continue
        obs = cached(c.id)
        if obs is None:
            continue
        ev = Evidence(claim_id=c.id, kind="browser", observation=obs)
        before = result.by_id[c.id]
        v = judge(c, ev, {k: x for k, x in result.by_id.items() if k != c.id}, rules)
        if v.status == "skipped":
            continue
        v = v.model_copy(update={"reason": f"{v.reason} (after loop on {fixed.id})"})
        result.evidence[c.id] = ev
        result.prior[c.id] = before
        _replace_verdict(result, v)
        ledger.record(result.doc.sha256, result.turn, [c], [v], looped=True)
        if log:
            log(c, v)


def _replace_verdict(result: ProveResult, v: Verdict) -> None:
    result.verdicts = [v if x.claim_id == v.claim_id else x for x in result.verdicts]

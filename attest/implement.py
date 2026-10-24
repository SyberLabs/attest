"""Spec mode: the doc is the contract and the application must catch up.

``attest prove <spec> --app DIR`` copies DIR into a candidate workspace under
``<home>/candidates`` (the original is never touched) and serves that copy to
the spec's shell steps through ``ATTEST_APP_DIR``. Every failing shell or
browser claim then becomes an attest.ticket/v0 JSON ticket for the worker:
the seam where a company plugs in its own agent (``ATTEST_WORKER_CMD``, a shell
command reading the ticket on stdin and printing a unified diff) or keeps the
built-in OpenAI Responses worker. The diff is applied inside the candidate
only, and the runner re-checks the claim against the changed app. The doc is
never patched.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import typer

from .ledger import Ledger
from .models import Brief, Claim
from .policy import AuthorityDecision
from .verdict import judge

WORKER_CMD_ENV = "ATTEST_WORKER_CMD"
WORKER_CMD_ALIAS = "ATTEST_IMPL_CMD"
IMPL_RULE = "spec mode: the spec is the contract"
FILES_CAP = 60_000
WORKER_INSTRUCTIONS = (
    "You implement missing capabilities in a small web app. You receive an attest.ticket/v0 JSON ticket "
    "naming one behaviour the spec requires and the app does not have yet (acceptance, observed), plus the "
    "full content of every app file in app.files. Implement the "
    "missing capability. Change as little as possible and keep the existing code style, markup and CSS "
    "conventions. Reply with only a unified diff in git format with paths a/<file> and b/<file> relative to "
    "the app root, no prose and no code fence. Do not use the '*** Begin Patch' format. Example of the exact "
    "format:\n"
    "--- a/app.js\n+++ b/app.js\n@@ -3,3 +3,4 @@\n const items = [];\n let total = 0;\n+let done = 0;\n"
    " render();\n"
)


# -- candidate workspace -----------------------------------------------------------


@dataclass
class Candidate:
    original: Path
    root: Path  # <home>/candidates/<sha12>-<n>/app, its own git repo

    @property
    def posix(self) -> str:
        return self.root.resolve().as_posix()

    def git(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *args], cwd=str(self.root), capture_output=True, text=True, encoding="utf-8", errors="replace")

    def files(self) -> list[Path]:
        out = []
        for p in sorted(self.root.rglob("*")):
            rel = p.relative_to(self.root)
            if p.is_file() and rel.parts[0] != ".git" and p.name != ".attest.patch":
                out.append(p)
        return out

    def apply(self, diff: str) -> tuple[bool, str]:
        """Check every path stays inside the app root, then git apply --check and apply."""
        touched = app_diff_paths(diff)
        if not touched:
            return False, "diff names no files"
        bad = sorted(p for p in touched if not safe_app_path(p))
        if bad:
            return False, f"diff touches paths outside the app root: {bad}"
        patch = self.root / ".attest.patch"
        patch.write_text(diff, encoding="utf-8", newline="\n")
        try:
            # --recount: agents often get hunk line counts wrong while the hunk body is right
            check = self.git("apply", "--check", "--recount", "--unidiff-zero", "--whitespace=nowarn", str(patch))
            if check.returncode != 0:
                return False, f"git apply --check failed: {check.stderr.strip()[:300]}"
            done = self.git("apply", "--recount", "--unidiff-zero", "--whitespace=nowarn", str(patch))
            if done.returncode != 0:
                return False, f"git apply failed: {done.stderr.strip()[:300]}"
        finally:
            patch.unlink(missing_ok=True)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "implementation from ticket")
        return True, "applied"


def make_candidate(app_dir: Path, doc_sha: str, home: Path) -> Candidate:
    """A fresh copy of the app for this run, committed as the baseline."""
    app_dir = Path(app_dir)
    if not app_dir.is_dir():
        raise ValueError(f"--app is not a directory: {app_dir}")
    base = home / "candidates"
    base.mkdir(parents=True, exist_ok=True)
    n = 1
    while (base / f"{doc_sha[:12]}-{n}").exists():
        n += 1
    root = base / f"{doc_sha[:12]}-{n}" / "app"
    shutil.copytree(app_dir, root, ignore=shutil.ignore_patterns(".git"))
    cand = Candidate(app_dir.resolve(), root)
    for f in cand.files():
        # agent diffs are always LF; a CRLF copy would make every hunk miss
        data = f.read_bytes()
        if b"\r\n" in data and b"\0" not in data:
            f.write_bytes(data.replace(b"\r\n", b"\n"))
    cand.git("init", "-q")
    cand.git("config", "user.email", "attest@localhost")
    cand.git("config", "user.name", "attest")
    cand.git("config", "core.autocrlf", "false")
    cand.git("add", "-A")
    cand.git("commit", "-q", "-m", "app before implementation")
    return cand


def app_diff_paths(diff: str) -> set[str]:
    """Every path a diff names, from ---/+++ lines and git headers, a/ and b/ stripped."""
    paths = set()
    for ln in diff.splitlines():
        if ln.startswith(("--- ", "+++ ")):
            p = ln[4:].split("\t")[0].strip()
            if p != "/dev/null":
                paths.add(re.sub(r"^[ab]/", "", p))
        elif ln.startswith("diff --git "):
            for p in ln[len("diff --git "):].split():
                paths.add(re.sub(r"^[ab]/", "", p))
        elif ln.startswith(("rename to ", "rename from ", "copy to ", "copy from ")):
            paths.add(ln.split(" ", 2)[2].strip())
    return paths


def safe_app_path(p: str) -> bool:
    if not p or p.startswith(("/", "\\", "~")) or re.match(r"^[A-Za-z]:", p):
        return False
    parts = PurePosixPath(p.replace("\\", "/")).parts
    return ".." not in parts and (not parts or parts[0] != ".git")


# -- ticket ------------------------------------------------------------------------


def ticket_title(claim: Claim) -> str:
    text = " ".join((claim.span.text or claim.expects or claim.id).split())
    text = text if len(text) <= 100 else text[:97].rstrip() + "..."
    return f"Missing capability: {text}"


def app_files(candidate: Candidate, cap: int = FILES_CAP) -> dict[str, str]:
    """Full content of every text file in the candidate, relative paths, capped in total."""
    out: dict[str, str] = {}
    used = 0
    for p in candidate.files():
        try:
            body = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        rel = p.relative_to(candidate.root).as_posix()
        if used + len(body) > cap:
            out[rel] = f"(omitted: file budget of {cap} characters reached)"
            continue
        used += len(body)
        out[rel] = body
    return out


def observed_of(verdict) -> dict:
    """What the runner saw, in the ticket's shape."""
    ev = verdict.evidence
    if ev is None:
        return {"verdict": verdict.status, "status": None, "observed": {}, "note": verdict.reason}
    if ev.kind == "shell":
        return {"verdict": verdict.status, "status": "timed_out" if ev.timed_out else f"exit {ev.exit_code}",
                "observed": {"stdout_tail": ev.stdout_tail or ""}, "note": (ev.command or "").strip()}
    obs = ev.observation
    note = None
    if obs and obs.note:
        # the Kane runner joins remark, failure record, root cause and fix with " | "; the pack path is local noise
        note = " | ".join(p.strip() for p in obs.note.split(" | ") if p.strip() and not p.startswith("pack: "))
    return {"verdict": verdict.status, "status": obs.status if obs else None,
            "observed": dict(obs.observed) if obs else {}, "note": note}


def build_ticket(case_id: str, claim: Claim, verdict, doc, candidate: Candidate) -> dict:
    """The attest.ticket/v0 record a worker reads on stdin."""
    return {
        "schema": "attest.ticket/v0",
        "case_id": case_id,
        "title": ticket_title(claim),
        "spec": {"source": doc.source, "lines": [claim.span.start_line, claim.span.end_line], "text": claim.span.text},
        "acceptance": claim.span.text.strip(),
        "expects": claim.expects,
        "observed": observed_of(verdict),
        "app": {"root": candidate.posix, "files": app_files(candidate)},
        "output": "unified diff in git format with paths a/<file> and b/<file> relative to app.root",
    }


def ticket_markdown(ticket: dict, doc) -> str:
    """The same ticket for a person: spec lines, what was observed, acceptance, files in scope."""
    start, end = ticket["spec"]["lines"]
    obs = ticket["observed"]
    lines = [
        f"# {ticket['title']}",
        "",
        f"Case `{ticket['case_id']}`. Spec: `{ticket['spec']['source']}`, lines {start}-{end}.",
        "",
        "## Spec lines",
        "```",
        doc.numbered(start, end),
        "```",
        "",
        "## What the runner observed",
        f"- verdict: {obs['verdict']}",
        f"- status: {obs['status']}",
    ]
    for k, val in obs["observed"].items():
        if k == "stdout_tail":
            tail = str(val).strip().splitlines()[-12:]
            if tail:
                lines += ["- output tail:", "```", *tail, "```"]
            continue
        lines.append(f"- observed {k} = {val!r}")
    if obs["note"]:
        lines += [f"- runner note: {p[:300]}" for p in str(obs["note"]).split(" | ")[:6]]
    lines += ["", "## Acceptance criterion", ticket["acceptance"]]
    if ticket.get("expects"):
        lines.append(f"The runner must observe: {ticket['expects']!r}")
    lines += ["", "## App files in scope", f"Candidate app: `{ticket['app']['root']}`", ""]
    lines += [f"- {rel}" for rel in ticket["app"]["files"]] or ["- none"]
    return "\n".join(lines) + "\n"


# -- worker ------------------------------------------------------------------------


def worker_cmd() -> str | None:
    return os.environ.get(WORKER_CMD_ENV) or os.environ.get(WORKER_CMD_ALIAS) or None


def worker_identity() -> str:
    """"openai:<model>" for the built-in worker, else the command a company plugged in."""
    cmd = worker_cmd()
    if cmd:
        return cmd
    from .compiler import DEFAULT_MODEL

    return f"openai:{os.environ.get('ATTEST_AGENT_MODEL') or DEFAULT_MODEL}"


def worker_label() -> str:
    """Short name for the page and the live strip."""
    return "the worker" if worker_cmd() else "OpenAI"


def call_worker(ticket: dict, cwd: Path) -> str:
    """Send the JSON ticket to the worker and return what it printed (a unified diff, hopefully)."""
    from .loop import AGENT_TIMEOUT_S, call_agent

    payload = json.dumps(ticket, ensure_ascii=True, indent=2)
    cmd = worker_cmd()
    if cmd:
        return call_agent(payload, cwd, cmd=cmd)
    from openai import OpenAI

    model = worker_identity().split(":", 1)[1]
    kwargs = {"reasoning": {"effort": "low"}} if model.startswith(("gpt-5", "o")) else {}
    try:
        resp = OpenAI(timeout=AGENT_TIMEOUT_S).responses.create(model=model, instructions=WORKER_INSTRUCTIONS, input=payload, **kwargs)
    except Exception as e:  # network, auth or model errors leave the ticket open
        return f"openai worker error: {e}"
    return resp.output_text or ""


# -- the spec loop for one claim ---------------------------------------------------


def implement_claim(result, claim: Claim, candidate: Candidate, runner, rules, ledger: Ledger, policy,
                    shell_env=None, log=None, on_update=None) -> None:
    """Ticket, one worker call, apply in the candidate, re-check, receipt."""
    from .cli import _ascii  # terminal stays ASCII on PowerShell 5.1
    from .loop import _receipt, _rejudge_dependents, _replace_verdict, _write_receipt, extract_diff, print_diff, rerun_claim

    doc = result.doc
    home = Path(ledger.path).parent
    line = claim.span.start_line
    before = result.by_id[claim.id]
    case_id = f"{doc.sha256[:12]}-{claim.id}-{result.turn}"
    authority = AuthorityDecision("implementation_required", IMPL_RULE)
    worker, label = worker_identity(), worker_label()
    result.authority[claim.id] = {"decision": authority.decision, "rule": authority.rule,
                                  "policy": str(policy.path) if policy.path else None, "agent": label, "worker": worker}
    typer.echo(typer.style(f"AUTHORITY line {line}  {IMPL_RULE}: the app must change, not the doc", fg=typer.colors.CYAN))

    # a. ticket: JSON for the worker, Markdown for people
    if on_update:
        on_update(f"Issuing ticket for line {line}")
    ticket = build_ticket(case_id, claim, before, doc, candidate)
    tickets = home / "tickets"
    tickets.mkdir(parents=True, exist_ok=True)
    ticket_json = tickets / f"{case_id}.json"
    ticket_md = tickets / f"{case_id}.md"
    ticket_json.write_text(json.dumps(ticket, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    ticket_md.write_text(ticket_markdown(ticket, doc), encoding="utf-8")
    typer.echo(typer.style(f"TICKET    line {line}  {_ascii(ticket['title'])}", fg=typer.colors.YELLOW, bold=True))
    typer.echo(f"          {ticket_md}")

    # b. the worker implements
    if on_update:
        on_update(f"{label[0].upper() + label[1:]} is implementing the missing capability")
    typer.echo("")
    typer.echo(f"   {typer.style('WORKER', fg=typer.colors.MAGENTA, bold=True)}    {typer.style(f'line {line}', dim=True)}  sending ticket to {_ascii(worker)}")
    diff = extract_diff(call_worker(ticket, candidate.root))
    if diff is not None:
        # a trailing blank line would count as one more context line under --recount
        diff = diff.rstrip("\n") + "\n"
    patch_sha = hashlib.sha256(diff.encode("utf-8")).hexdigest() if diff else None
    applied = False
    if diff is None:
        message = "worker returned no unified diff"
    else:
        typer.echo(typer.style("             app change:", dim=True))
        print_diff(diff)
        # c. apply in the candidate only
        applied, message = candidate.apply(diff)
    result.briefs[claim.id] = Brief(claim_id=claim.id, verdict=before, doc_excerpt=ticket_md.read_text(encoding="utf-8"), patch=diff, attempts=1)
    result.prior[claim.id] = before

    after = before
    if not applied:
        after = before.model_copy(update={"reason": f"{before.reason} (ticket open: {message})"})
        typer.echo(typer.style(f"   ...       not applied: {_ascii(message.splitlines()[0])}", fg=typer.colors.RED))
    else:
        # d. re-check: the static server reads the candidate from disk, so only Kane's cache must go
        forget = getattr(runner, "forget", None)
        if forget is not None:
            group = next((g for g in getattr(runner, "groups", []) if any(c.id == claim.id for c in g)), [claim])
            forget({claim.id, *(c.id for c in group if c.kind == "browser")})
        if on_update:
            on_update(f"Kane is re-checking line {line}")
        typer.echo(typer.style(f"   ...       {'Kane' if claim.kind == 'browser' else 'Attest'} is re-checking line {line}", fg=typer.colors.BRIGHT_BLACK))
        ev = rerun_claim(claim, result, runner, rules, shell_env=shell_env)
        runs = getattr(runner, "runs", None)
        recorded = getattr(result, "_kane_runs_count", 0)
        if runs is not None and len(runs) > recorded:
            result._kane_runs_count = ledger.record_new_kane_runs(doc.sha256, result.turn, runs, recorded)
        if ev is not None:
            result.evidence[claim.id] = ev
        after = judge(claim, ev, {k: v for k, v in result.by_id.items() if k != claim.id}, rules)
        after = after.model_copy(update={"reason": f"{after.reason} (after implementation)"})
    _replace_verdict(result, after)
    ledger.record(doc.sha256, result.turn, [claim], [after], looped=True)

    # e. receipt
    disposition = "implemented" if after.status == "proven" else "unresolved"
    receipt = _receipt(result, claim, before, after, policy, authority, worker, diff, disposition, None, doc.sha256, None)
    receipt["worker"] = worker
    receipt["ticket"] = {"path": str(ticket_json), "markdown": str(ticket_md), "title": ticket["title"]}
    receipt["app"] = {"original": str(candidate.original), "candidate": candidate.posix, "patch_sha256": patch_sha, "applied": applied}
    path = _write_receipt(home, receipt)
    result.receipts[claim.id] = {"case_id": case_id, "path": str(path), "ticket": f"../tickets/{case_id}.md", "ticket_title": ticket["title"]}
    typer.echo(f"RECEIPT   {path}")
    if applied and claim.kind == "browser":
        _rejudge_dependents(result, claim, runner, rules, ledger, log)
    if log:
        log(claim, after)

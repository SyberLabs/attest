"""Root causes: group stale and broken claims by what the code said, and find the
drift radius, the other places in the docs that name the same thing.

Many failures share one cause. When Runvil removed ``tools.offer_local``, a dozen
examples failed with "attempt to call a nil value (field 'offer_local')", and the
name sat in 80 lines of prose no run can check. Grouping turns those dozen
verdicts into one item; the radius lists every prose code span that names the
removed thing, and every example using it that did not run (a missing Pack, a
model call), so a fix can cover all of them.

Pure functions over verdicts and docs; no model, no network.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .models import Claim, Doc, Verdict

FAILING = ("stale", "broken")

# what a failure names, by the shape of the message the code gave
_NAMED = [
    (re.compile(r"nil value \((?:field|global|local|method|upvalue) '([^']+)'\)"), "removed or renamed"),
    (re.compile(r"unknown field `([^`]+)`"), "unknown field"),
    (re.compile(r"unknown namespace or global '([^']+)'"), "unknown name"),
    (re.compile(r"section heading `([^`]+)` not found"), "section not found"),
    (re.compile(r"(?:^|\s)([\w.-]+): command not found"), "command not found"),
    (re.compile(r"command not found: ([\w.-]+)"), "command not found"),
    (re.compile(r"(?:unrecognized|unexpected|unknown) (?:option|argument|flag) '?(--?[\w-]+)"), "unknown option"),
    (re.compile(r"no such (?:file or directory|subcommand)[:,]? '?([\w./-]+)"), "not found"),
]
_CODE_SPAN = re.compile(r"`([^`\n]+)`")
_NOISE = [
    (re.compile(r"section `[^`]*`|H1 `[^`]*`"), "section `#`"),
    (re.compile(r"(?:prologue|epilog|lua):\d+"), "block:#"),
    (re.compile(r"\bline \d+|\bat byte \d+|\bcolumn \d+"), "#"),
    (re.compile(r"\b\d+(?:\.\d+)*\b"), "#"),
    (re.compile(r"ExecId\(\d+\)"), "ExecId(#)"),
]


@dataclass
class Hit:
    doc: str
    line: int
    text: str


@dataclass
class RootCause:
    signature: str  # what the code said, normalized
    name: str | None  # the thing it names, when the message names one
    label: str  # "removed or renamed", "unknown field", ... or the error kind
    claims: list[tuple[str, str, int]] = field(default_factory=list)  # (doc, claim id, line)
    prose: list[Hit] = field(default_factory=list)  # prose code spans naming it
    unrun: list[Hit] = field(default_factory=list)  # examples naming it that did not run

    def as_dict(self) -> dict:
        return {
            "signature": self.signature,
            "name": self.name,
            "label": self.label,
            "claims": [{"doc": d, "claim": c, "line": n} for d, c, n in self.claims],
            "prose": [vars(h) for h in self.prose],
            "unrun": [vars(h) for h in self.unrun],
        }


def failure_text(claim: Claim, verdict: Verdict) -> tuple[str, str]:
    """(label, message): what the run said about a failing claim."""
    ev = verdict.evidence
    if ev is not None and ev.kind == "prompt" and ev.prompt is not None:
        p = ev.prompt
        if p.status == "completed":
            return "result differs", f"result {(p.result or '').strip()[:120]}"
        return p.error_kind or p.status, (p.message or p.status).split("\n")[0]
    if ev is not None and ev.kind == "shell":
        tail = [ln for ln in (ev.stdout_tail or "").splitlines() if ln.strip()]
        last = tail[-1] if tail else ""
        if ev.timed_out:
            return "timed out", last or "timed out"
        if verdict.status == "stale" and (ev.exit_code == 0 or ev.service_ready):
            return "output differs", f"doc shows {(claim.expects or '').splitlines()[0][:60] if claim.expects else ''}"
        return f"exit {ev.exit_code}", last or f"exit {ev.exit_code}"
    return verdict.status, verdict.reason


def normalize(message: str) -> str:
    for pattern, repl in _NOISE:
        message = pattern.sub(repl, message)
    return " ".join(message.split())[:140]


def named(message: str) -> tuple[str, str] | None:
    """(name, label) when the message names the thing that changed."""
    for pattern, label in _NAMED:
        m = pattern.search(message)
        if m:
            return m.group(1), label
    return None


def _mentions(text: str, name: str) -> bool:
    """True when ``name`` appears as a whole token inside ``text``."""
    # a dot may precede it: a removed field shows up as `tools.offer_local`
    return re.search(rf"(?<!\w){re.escape(name)}(?!\w)", text) is not None


def radius(name: str, docs: list[tuple[Doc, list[Claim], dict[str, Verdict]]], flagged: set[tuple[str, str]]) -> tuple[list[Hit], list[Hit]]:
    """Prose code spans that name ``name``, and examples naming it that did not run."""
    prose: list[Hit] = []
    unrun: list[Hit] = []
    for doc, claims, verdicts in docs:
        fenced = {n for f in doc.fences for n in range(f.start_line - 1, f.end_line + 2)}
        for n, line in enumerate(doc.lines, 1):
            if n in fenced:
                continue
            if any(_mentions(span, name) for span in _CODE_SPAN.findall(line)):
                prose.append(Hit(doc.source, n, line.strip()[:160]))
        for c in claims:
            if (doc.source, c.id) in flagged or c.kind == "prose":
                continue
            v = verdicts.get(c.id)
            if v is None or v.status != "unverifiable":
                continue
            text = c.command or c.objective or ""
            if _mentions(text, name):
                unrun.append(Hit(doc.source, c.span.start_line, v.reason[:120]))
    return prose, unrun


def group(results: list, only: set[tuple[str, str]] | None = None) -> list[RootCause]:
    """Group the stale and broken claims of ``results`` (ProveResults) by root cause,
    largest first. ``only`` limits it to (doc source, claim id) pairs, such as the
    new failures against a baseline."""
    causes: dict[tuple, RootCause] = {}
    flagged: set[tuple[str, str]] = set()
    docs = []
    for r in results:
        by_id = {v.claim_id: v for v in r.verdicts}
        docs.append((r.doc, r.claims, by_id))
        for c in r.claims:
            v = by_id.get(c.id)
            if v is None or v.status not in FAILING:
                continue
            if only is not None and (r.doc.source, c.id) not in only:
                continue
            flagged.add((r.doc.source, c.id))
            label, message = failure_text(c, v)
            hit = named(message)
            if hit is not None:
                name, label = hit
                key = (label, name)
                signature = normalize(message)
            else:
                name = None
                signature = normalize(message)
                key = (label, signature)
            cause = causes.setdefault(key, RootCause(signature=signature, name=name, label=label))
            cause.claims.append((r.doc.source, c.id, c.span.start_line))
    commands = {(r.doc.source, c.id): (c.command or c.objective or "") for r in results for c in r.claims}
    for cause in causes.values():
        if cause.name:
            if cause.label == "removed or renamed":
                cause.name = qualified(cause.name, [commands.get((d, cid), "") for d, cid, _ in cause.claims])
            cause.prose, cause.unrun = radius(cause.name, docs, flagged)
    return sorted(causes.values(), key=lambda rc: (-len(rc.claims), rc.label, rc.signature))


def qualified(name: str, texts: list[str]) -> str:
    """The form the failing examples use for a removed field: `messages.new` rather than
    `new`, so the radius does not match every other `.new`. The bare name when the
    examples use it bare, or use it under more than one owner."""
    owners = {m.group(1) for t in texts for m in re.finditer(rf"(\w+)[.:]{re.escape(name)}(?!\w)", t)}
    return f"{owners.pop()}.{name}" if len(owners) == 1 else name

"""Compiler: numbered doc lines to a list of Claims, one structured LLM call.

Rules from docs/ARCHITECTURE.md:

1. Fenced code blocks are shell claims by construction; ``command`` is the block
   text verbatim. A block that is only output, or starts with ``#``, is ``assert``
   and its text lands in ``expects``.
2. Every browser claim is a plain-English step that ends in a check and names the
   exact on-page label it expects. It stores what it saw under ``store_as``.
3. Prose that promises nothing observable is ``prose`` and gets no badge.

The LLM output is checked post-hoc against the doc. On failure the call is
retried once with the failing line quoted; on a second failure the fenced
blocks are compiled by regex and only the LLM's non-fence claims are kept.
Output is cached under cache/compiler/<sha256>.json.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from .models import Claim, Doc, Fence, Span

SHELL_LANGS = {"", "sh", "bash", "shell", "console", "zsh", "shell-session", "terminal", "cmd", "powershell", "ps1"}
OUTPUT_LANGS = {"text", "txt", "output", "plaintext", "log", "console-output"}
DEFAULT_MODEL = os.environ.get("ATTEST_OPENAI_MODEL", "gpt-5-mini")
CACHE_DIR = Path(os.environ.get("ATTEST_CACHE_DIR", "cache")) / "compiler"


class LLMClaim(BaseModel):
    """What the model returns per claim. Ids and span text are assigned by Attest."""

    kind: Literal["shell", "browser", "assert", "prose"]
    start_line: int
    end_line: int
    text: str = Field(description="The exact doc text of the span, verbatim, lines joined with newlines")
    command: str | None = Field(description="shell: the fenced block text verbatim")
    objective: str | None = Field(
        description="browser: one plain-English step a person can follow, ending in a check of an exact on-page label"
    )
    expects: str | None = Field(description="the exact UI label or output text the doc promises, or null")


class ClaimSet(BaseModel):
    claims: list[LLMClaim]


class CompileError(Exception):
    pass


class LLM(Protocol):
    def parse_claims(self, system: str, user: str) -> ClaimSet: ...


class OpenAILLM:
    """Thin wrapper so tests can swap in a fake."""

    def __init__(self, model: str = DEFAULT_MODEL):
        from openai import OpenAI

        self.client = OpenAI()
        self.model = model

    def parse_claims(self, system: str, user: str) -> ClaimSet:
        kwargs: dict[str, Any] = {}
        if self.model.startswith(("gpt-5", "o")):
            # reasoning models: low effort is enough for transcription and keeps the call fast
            kwargs["reasoning"] = {"effort": "low"}
        resp = self.client.responses.parse(
            model=self.model,
            input=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            text_format=ClaimSet,
            **kwargs,
        )
        parsed = resp.output_parsed
        if parsed is None:
            raise CompileError("model returned no parsed output")
        return parsed


SYSTEM_PROMPT = """You turn a numbered document into a list of checkable claims.

Rules:
- Walk the document top to bottom. Every fenced code block is exactly one claim. Copy the lines between the fences verbatim into `command` (never the ``` fence lines themselves, never the language tag) and set kind to "shell". If the block is only program output, or every line starts with #, set kind to "assert" and copy the text into `expects` instead.
- A sentence that tells the reader to open a page, click, type, or that says what they will see in a browser is a "browser" claim. Write `objective` as one plain-English step a person can follow, in the imperative, ending in a check of the exact on-page text. Put that exact on-page text, and nothing else, in `expects`. Do not format the step for any tool.
- When a shell step says what it prints (for example "Running on http://..."), put that exact text in `expects`.
- Everything else that promises nothing observable is "prose". Prose must have null command, objective and expects.
- `start_line` and `end_line` are 1-based inclusive line numbers from the numbering shown. `text` is the exact text of those lines, joined with newlines, without the line numbers.
- Never invent a check the document did not make. Never paraphrase a code block.
"""


def _strip_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


_FENCE_LINE = re.compile(r"^\s*(`{3,}|~{3,})\s*[\w+-]*\s*$")


def same_block(a: str | None, b: str) -> bool:
    """Line-wise comparison that forgives leading and trailing whitespace per line
    and blank lines. Used only to recognise a fence; the fence text itself is what
    gets stored, so leniency here never changes what runs."""
    if a is None:
        return False
    la = [ln.strip() for ln in a.splitlines() if ln.strip() and not _FENCE_LINE.match(ln)]
    lb = [ln.strip() for ln in b.splitlines() if ln.strip() and not _FENCE_LINE.match(ln)]
    return bool(la) and la == lb


def classify_fence(fence: Fence) -> Literal["shell", "assert"]:
    lang = fence.lang.lower()
    lines = [ln for ln in fence.text.splitlines() if ln.strip()]
    if lang in OUTPUT_LANGS:
        return "assert"
    if lang not in SHELL_LANGS:
        return "assert"
    if lines and all(ln.lstrip().startswith("#") for ln in lines):
        return "assert"
    return "shell"


def fence_claim(fence: Fence, cid: str) -> Claim:
    kind = classify_fence(fence)
    span = Span(start_line=fence.start_line, end_line=fence.end_line, text=fence.text)
    if kind == "shell":
        return Claim(id=cid, kind="shell", span=span, command=fence.text)
    return Claim(id=cid, kind="assert", span=span, expects=fence.text)


def fences_by_regex(doc: Doc) -> list[Claim]:
    return [fence_claim(f, f"c{i + 1:02d}") for i, f in enumerate(doc.fences)]


def _span_from(doc: Doc, lc: LLMClaim) -> Span | None:
    if lc.start_line < 1 or lc.end_line > len(doc.lines) or lc.end_line < lc.start_line:
        return None
    text = "\n".join(doc.lines[lc.start_line - 1 : lc.end_line])
    return Span(start_line=lc.start_line, end_line=lc.end_line, text=text)


def check_claims(doc: Doc, drafts: list[LLMClaim]) -> list[str]:
    """Return a list of problems. Empty means the draft set passes."""
    problems: list[str] = []
    spans: list[Span] = []
    for i, lc in enumerate(drafts):
        span = _span_from(doc, lc)
        if span is None:
            problems.append(f"claim {i + 1}: span {lc.start_line}-{lc.end_line} is outside the document")
            continue
        spans.append(span)
        a, b = _strip_ws(span.text), _strip_ws(lc.text)
        if a != b and a not in b and b not in a:
            problems.append(
                f"claim {i + 1}: text does not match lines {lc.start_line}-{lc.end_line}. Document says: {span.text!r}"
            )
    for f in doc.fences:
        want = classify_fence(f)
        hit = False
        for lc in drafts:
            if want == "shell" and lc.kind == "shell" and same_block(lc.command, f.text):
                hit = True
            if want == "assert" and lc.kind == "assert" and same_block(lc.expects, f.text):
                hit = True
        if not hit:
            problems.append(
                f"fenced block at lines {f.start_line}-{f.end_line} is missing or not verbatim as a {want} claim. Block text: {f.text!r}"
            )
    return problems


def _fence_for(doc: Doc, lc: LLMClaim) -> Fence | None:
    for f in doc.fences:
        if same_block(lc.command or lc.expects, f.text):
            return f
    return None


def assemble(doc: Doc, drafts: list[LLMClaim]) -> list[Claim]:
    """Turn checked drafts into Claims with ids in doc order and a dependency chain."""
    drafts = sorted(drafts, key=lambda d: (d.start_line, d.end_line))
    claims: list[Claim] = []
    for i, lc in enumerate(drafts):
        cid = f"c{i + 1:02d}"
        fence = _fence_for(doc, lc) if lc.kind in ("shell", "assert") else None
        if fence is not None:
            # fences are shell/assert by construction: pin the span to the block
            claim = fence_claim(fence, cid)
            if lc.kind == "shell" and lc.expects:
                claim = claim.model_copy(update={"expects": lc.expects})
            claims.append(claim)
            continue
        span = _span_from(doc, lc)
        if span is None:
            continue
        if lc.kind == "browser":
            claims.append(
                Claim(
                    id=cid,
                    kind="browser",
                    span=span,
                    objective=(lc.objective or span.text).strip(),
                    expects=(lc.expects or None),
                    store_as=f"{cid}_label",
                )
            )
        elif lc.kind == "prose":
            claims.append(Claim(id=cid, kind="prose", span=span))
        elif lc.kind == "assert":
            claims.append(Claim(id=cid, kind="assert", span=span, expects=lc.expects or span.text))
        else:  # a shell claim the model made up without a fence: keep as prose, never run it
            claims.append(Claim(id=cid, kind="prose", span=span))
    return chain_dependencies(claims)


def chain_dependencies(claims: list[Claim]) -> list[Claim]:
    """Deterministic ordering rule: a step needs the previous shell step, and a
    browser step also needs the previous browser step."""
    out: list[Claim] = []
    last_shell: str | None = None
    last_browser: str | None = None
    for c in claims:
        deps: list[str] = []
        if c.kind in ("shell", "browser", "assert") and last_shell:
            deps.append(last_shell)
        if c.kind == "browser" and last_browser:
            deps.append(last_browser)
        out.append(c.model_copy(update={"depends_on": deps}))
        if c.kind == "shell":
            last_shell = c.id
        if c.kind == "browser":
            last_browser = c.id
    return out


def _user_prompt(doc: Doc, problems: list[str] | None = None) -> str:
    parts = ["Document, one line per row, numbered:", "", doc.numbered()]
    if problems:
        parts += ["", "Your previous answer had these problems. Fix them and return the full list again:"]
        parts += [f"- {p}" for p in problems]
    return "\n".join(parts)


def _cache_path(doc: Doc, cache_dir: Path) -> Path:
    return cache_dir / f"{doc.sha256}.json"


def load_cached(doc: Doc, cache_dir: Path = CACHE_DIR) -> list[Claim] | None:
    p = _cache_path(doc, cache_dir)
    if not p.exists():
        return None
    data = json.loads(p.read_text(encoding="utf-8"))
    return [Claim.model_validate(c) for c in data["claims"]]


def save_cache(doc: Doc, claims: list[Claim], cache_dir: Path = CACHE_DIR, meta: dict[str, Any] | None = None) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    payload = {"sha256": doc.sha256, "source": doc.source, "claims": [c.model_dump() for c in claims]}
    if meta:
        payload["meta"] = meta
    _cache_path(doc, cache_dir).write_text(json.dumps(payload, indent=2), encoding="utf-8")


def compile_doc(doc: Doc, llm: LLM | None = None, cache_dir: Path = CACHE_DIR, use_cache: bool = True) -> list[Claim]:
    """Compile a Doc into Claims. Returns the claims and leaves doc untouched."""
    if use_cache:
        cached = load_cached(doc, cache_dir)
        if cached is not None:
            return cached
    if llm is None:
        if not os.environ.get("OPENAI_API_KEY"):
            # no model and no cache: fenced blocks only, everything else is prose
            claims = fences_by_regex(doc)
            return chain_dependencies(claims)
        llm = OpenAILLM()

    meta: dict[str, Any] = {"mode": "llm", "attempts": 0}
    drafts = llm.parse_claims(SYSTEM_PROMPT, _user_prompt(doc)).claims
    meta["attempts"] = 1
    problems = check_claims(doc, drafts)
    if problems:
        drafts = llm.parse_claims(SYSTEM_PROMPT, _user_prompt(doc, problems)).claims
        meta["attempts"] = 2
        problems = check_claims(doc, drafts)
    if problems:
        # fallback: regex for fences, the model only for non-fence claims
        meta["mode"] = "fallback"
        meta["problems"] = problems
        keep = [d for d in drafts if d.kind in ("browser", "prose") and _span_from(doc, d) is not None]
        fence_spans = {(f.start_line, f.end_line) for f in doc.fences}
        keep = [d for d in keep if not any(s <= d.start_line <= e for s, e in fence_spans)]
        fence_drafts = [
            LLMClaim(
                kind=classify_fence(f),
                start_line=f.start_line,
                end_line=f.end_line,
                text=f.text,
                command=f.text if classify_fence(f) == "shell" else None,
                objective=None,
                expects=f.text if classify_fence(f) == "assert" else None,
            )
            for f in doc.fences
        ]
        drafts = keep + fence_drafts
    claims = assemble(doc, drafts)
    if use_cache:
        save_cache(doc, claims, cache_dir, meta)
    return claims


RECOMPILE_PROMPT = """You rewrite exactly one claim from a document after the document changed.

Return a list with exactly one claim covering the numbered lines you are shown, following the same rules as before:
- a fenced block is a shell claim with the block text verbatim in `command`
- a browser instruction is a browser claim: one plain-English step ending in a check of the exact on-page text, which goes in `expects`
- otherwise prose
"""


_QUOTED = re.compile(r'"([^"]+)"')


def requote_claim(claim: Claim, span: Span) -> Claim:
    """No-model re-compile: if the promised quote was replaced by exactly one new quote, follow it."""
    moved = claim.model_copy(update={"span": span})
    if not claim.expects or claim.expects in span.text:
        return moved
    before = set(_QUOTED.findall(claim.span.text))
    added = [q for q in _QUOTED.findall(span.text) if q not in before]
    if len(added) != 1:
        return moved
    objective = (claim.objective or "").replace(claim.expects, added[0])
    return moved.model_copy(update={"expects": added[0], "objective": objective or None})


def recompile_claim(doc: Doc, claim: Claim, llm: LLM | None = None) -> Claim:
    """Re-compile only one claim's span against an updated doc. Keeps the id and dependencies."""
    start, end = claim.span.start_line, min(claim.span.end_line, len(doc.lines))
    # if the span now sits on a fence, the fence wins without a model call
    for f in doc.fences:
        if f.start_line <= start <= f.end_line or start <= f.start_line <= end:
            fresh = fence_claim(f, claim.id)
            return fresh.model_copy(update={"depends_on": claim.depends_on, "expects": claim.expects if fresh.kind == "shell" else fresh.expects})
    span = Span(start_line=start, end_line=end, text="\n".join(doc.lines[start - 1 : end]))
    if claim.kind == "prose":
        return claim.model_copy(update={"span": span})
    if llm is None:
        if not os.environ.get("OPENAI_API_KEY"):
            return requote_claim(claim, span)
        llm = OpenAILLM()
    excerpt = doc.numbered(start, end)
    result = llm.parse_claims(RECOMPILE_PROMPT, f"Lines {start}-{end}:\n\n{excerpt}")
    if not result.claims:
        return claim.model_copy(update={"span": span})
    lc = result.claims[0]
    if lc.kind == "browser":
        return Claim(
            id=claim.id,
            kind="browser",
            span=span,
            objective=(lc.objective or span.text).strip(),
            expects=lc.expects or None,
            store_as=claim.store_as or f"{claim.id}_label",
            depends_on=claim.depends_on,
        )
    return claim.model_copy(update={"span": span, "expects": lc.expects or claim.expects})

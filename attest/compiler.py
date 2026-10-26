"""Compiler: numbered doc lines to a list of Claims, one structured LLM call.

Rules from docs/ARCHITECTURE.md:

1. Fenced code blocks are shell claims by construction; ``command`` is the block
   text verbatim. A block that is only output, or starts with ``#``, is ``assert``
   and its text lands in ``expects``.
2. Every browser claim is a plain-English step that ends in a check and names the
   exact on-page label it expects. It stores what it saw under ``store_as``.
3. Prose that promises nothing observable is ``prose`` and gets no badge.

Rules from docs/CLI-RUNNER-SPEC.md, applied with no model call:

4. A ``markdown`` fence whose text opens with frontmatter carrying a ``runvil:``
   key is a ``prompt`` claim; ``command`` is the fence text byte for byte.
5. A ``text`` fence that follows it with only blank lines between, or one
   introducing line that ends in a colon, becomes that claim's ``expects``,
   byte for byte. The claim's span covers both fences.
6. A ``lua`` or ``markdown`` fence that is not a complete prompt file is a
   fragment and compiles to prose: it promises nothing a run can check.
7. An untagged fence that parses as TOML, or is unit-file ``NAME=value`` lines
   throughout, is configuration and compiles to prose; one that parses as JSON
   is a printed response and compiles to an assert on the command before it.
   The Gateway guide tags none of its fences (docs/CLI-RUNNER-SPEC.md asks
   upstream to; this is the interim).

The LLM output is checked post-hoc against the doc. On failure the call is
retried once with the failing line quoted; on a second failure the fenced
blocks are compiled by regex and only the LLM's non-fence claims are kept.
Output is cached under cache/compiler/<sha256>.json.
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from .ingest import sha256_of
from .models import Claim, Doc, Fence, Span

SHELL_LANGS = {"", "sh", "bash", "shell", "console", "zsh", "shell-session", "terminal", "cmd", "powershell", "ps1"}
OUTPUT_LANGS = {"text", "txt", "output", "plaintext", "log", "console-output"}
# a complete Runvil prompt file sits in a markdown fence (docs/CLI-RUNNER-SPEC.md, compiler rule 1)
PROMPT_LANGS = {"markdown"}
# a lua or markdown fence that is not a complete prompt file is a fragment: prose, never run
FRAGMENT_LANGS = {"lua", "markdown", "md"}
# configuration a doc shows, never a command to run
CONFIG_LANGS = {"toml", "yaml", "yml", "ini", "cfg", "conf"}
DEFAULT_MODEL = os.environ.get("ATTEST_OPENAI_MODEL", "gpt-5-mini")


def openai_options(model: str) -> dict:
    """Request options shared by the doc agent and the worker: medium reasoning effort on reasoning
    models (ATTEST_REASONING_EFFORT overrides), and ATTEST_OPENAI_SERVICE_TIER (e.g. "priority",
    which the API reports as fast)."""
    opts: dict = {}
    if model.startswith(("gpt-5", "gpt-6", "o")):
        opts["reasoning"] = {"effort": os.environ.get("ATTEST_REASONING_EFFORT", "medium")}
    tier = os.environ.get("ATTEST_OPENAI_SERVICE_TIER")
    if tier:
        opts["service_tier"] = tier
    return opts
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
- A `markdown` fence that holds a complete prompt file (frontmatter with a `runvil:` key), the `text` fence with its result, and any `lua` fence are placed by rule, not by you: return each of those blocks as "prose".
- Everything else that promises nothing observable is "prose". Prose must have null command, objective and expects.
- `start_line` and `end_line` are 1-based inclusive line numbers from the numbering shown. `text` is the exact text of those lines, joined with newlines, without the line numbers.
- Never invent a check the document did not make. Never paraphrase a code block.
"""


def _strip_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


_FENCE_LINE = re.compile(r"^\s*(`{3,}|~{3,})\s*[\w+-]*\s*$")
CACHE_SCHEMA_VERSION = 2
_BROWSER_ACTION = re.compile(
    r"\b(?:open|visit|navigate|go\s+to|head\s+over|click|tap|select|choose|type|enter|fill|press|"
    r"see|shows?|displays?|appears?|receive|accept|label(?:led|ed)|changes?|updates?|renders?)\b",
    re.I,
)
_BROWSER_CONTEXT = re.compile(
    r"\b(?:browser|page|site|screen|button|menu|tab|field|label|text|url|address bar|notification)\b|https?://",
    re.I,
)


def _word_text(value: str) -> str:
    return " ".join(re.findall(r"\w+", value.casefold()))


def _expectation_in_source(expects: str, doc: Doc) -> bool:
    expected = _word_text(expects)
    source = _word_text("\n".join(doc.lines))
    return bool(expected) and f" {expected} " in f" {source} "


def required_browser_lines(doc: Doc) -> list[int]:
    """Find non-fenced source lines that promise an observable browser action.

    Empty when the doc declares it has no browser steps (``Doc.browser_claims``
    is False, set from the sidecar file): a Language guide chapter talks about
    pages, text and labels without asking anyone to open a browser."""
    if not doc.browser_claims:
        return []
    fenced_lines = {n for f in doc.fences for n in range(f.start_line, f.end_line + 1)}
    return [
        n
        for n, line in enumerate(doc.lines, start=1)
        if n not in fenced_lines and _BROWSER_ACTION.search(line) and _BROWSER_CONTEXT.search(line)
    ]


def same_block(a: str | None, b: str) -> bool:
    """Line-wise comparison that forgives leading and trailing whitespace per line
    and blank lines. Used only to recognise a fence; the fence text itself is what
    gets stored, so leniency here never changes what runs."""
    if a is None:
        return False
    la = [ln.strip() for ln in a.splitlines() if ln.strip() and not _FENCE_LINE.match(ln)]
    lb = [ln.strip() for ln in b.splitlines() if ln.strip() and not _FENCE_LINE.match(ln)]
    return bool(la) and la == lb


_FRONTMATTER_KEY = re.compile(r"^runvil\s*:")


def is_prompt_fence(fence: Fence) -> bool:
    """A markdown fence whose text opens with frontmatter carrying a ``runvil:`` key."""
    if fence.lang.lower() not in PROMPT_LANGS:
        return False
    lines = fence.text.split("\n")
    if not lines or lines[0].strip() != "---":
        return False
    for ln in lines[1:]:
        if ln.strip() == "---":
            return False  # the frontmatter closed without the key
        if _FRONTMATTER_KEY.match(ln):
            return True
    return False


def looks_like_json(text: str) -> bool:
    """An untagged fence holding one JSON value: the response a command printed."""
    stripped = text.strip()
    if not stripped or stripped[0] not in "{[":
        return False
    try:
        json.loads(stripped)
    except ValueError:
        return False
    return True


_ASSIGNMENT_LINE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*=\S")


def looks_like_config(text: str) -> bool:
    """An untagged fence holding configuration, never a command: a TOML
    document (tables, key = value pairs), or unit-file style NAME=value lines
    such as a systemd snippet, every line."""
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    if not lines:
        return False
    # two or more NAME=value lines are a unit file; one alone may be `FOO=1 ./run`
    if len(lines) >= 2 and all(_ASSIGNMENT_LINE.match(ln.strip()) for ln in lines):
        return True
    if not any("=" in ln or ln.strip().startswith("[") for ln in lines):
        return False
    try:
        import tomllib

        tomllib.loads(text)
    except (ValueError, ImportError):
        return False
    return True


def classify_fence(fence: Fence) -> Literal["shell", "assert", "prompt", "prose"]:
    lang = fence.lang.lower()
    lines = [ln for ln in fence.text.splitlines() if ln.strip()]
    if is_prompt_fence(fence):
        return "prompt"
    if lang in FRAGMENT_LANGS or lang in CONFIG_LANGS:
        return "prose"
    if lang in OUTPUT_LANGS:
        return "assert"
    if lang not in SHELL_LANGS:
        return "assert"
    if lang == "":
        # the Gateway guide tags nothing: tell a config snippet and a printed
        # response apart from a command by their shape, never run the first two
        if looks_like_json(fence.text):
            return "assert"
        if looks_like_config(fence.text):
            return "prose"
    if lines and all(ln.lstrip().startswith("#") for ln in lines):
        return "assert"
    return "shell"


FenceRole = Literal["shell", "assert", "prompt", "result", "prose"]


@dataclass(frozen=True)
class FencePlan:
    """One fence and the role the rules give it. A prompt fence may own the
    result fence that follows it; that result fence is then not a claim."""

    fence: Fence
    role: FenceRole
    result: Fence | None = None
    # the argument string the introducing line names: "Run it with the argument string `rust`:"
    args: str | None = None


_ARGUMENT_STRING = re.compile(r"argument string `([^`]*)`")


def _introducing_line(doc: Doc, first: Fence, second: Fence) -> str | None | bool:
    """What sits between the closing fence of ``first`` and the opening fence of
    ``second``: ``None`` for blank lines only, the line when it is one line that
    ends in a colon, the way the Runvil guides introduce a result or an error
    message ("the run result is:", "and this message:"), else ``False``."""
    between = [ln for ln in doc.lines[first.end_line + 1 : second.start_line - 2] if ln.strip()]
    if not between:
        return None
    if len(between) == 1 and between[0].rstrip().endswith(":"):
        return between[0]
    return False


def _introduces_result(doc: Doc, first: Fence, second: Fence) -> bool:
    """True when ``second`` is ``first``'s result fence."""
    return _introducing_line(doc, first, second) is not False


def _args_in(line: str | None | bool) -> str | None:
    """The argument string an introducing line names, when it names one."""
    if not isinstance(line, str):
        return None
    m = _ARGUMENT_STRING.search(line)
    return m.group(1) if m else None


def plan_fences(doc: Doc) -> list[FencePlan]:
    """Give every fence a role by rule. The plan is the single source of truth for
    which fences are claims, which are results and which are prose."""
    plan: list[FencePlan] = []
    fences = doc.fences
    i = 0
    while i < len(fences):
        f = fences[i]
        role = classify_fence(f)
        if role == "prompt" and i + 1 < len(fences):
            nxt = fences[i + 1]
            if nxt.lang.lower() in OUTPUT_LANGS and _introduces_result(doc, f, nxt):
                plan.append(FencePlan(f, "prompt", nxt, _args_in(_introducing_line(doc, f, nxt))))
                plan.append(FencePlan(nxt, "result"))
                i += 2
                continue
        plan.append(FencePlan(f, role))
        i += 1
    return plan


def fence_claim(fence: Fence, cid: str, doc: Doc | None = None, result: Fence | None = None, args: str | None = None) -> Claim:
    """The claim a fence compiles to by rule. ``result`` is the paired result
    fence of a prompt; it needs ``doc`` so the span can cover both fences."""
    kind = classify_fence(fence)
    span = Span(start_line=fence.start_line, end_line=fence.end_line, text=fence.text)
    if kind == "shell":
        return Claim(id=cid, kind="shell", span=span, command=fence.text)
    if kind == "prompt":
        if result is not None:
            if doc is None:
                raise ValueError("a paired prompt claim needs the doc to build its span")
            span = Span(
                start_line=fence.start_line,
                end_line=result.end_line,
                text="\n".join(doc.lines[fence.start_line - 1 : result.end_line]),
            )
        return Claim(id=cid, kind="prompt", span=span, command=fence.text, expects=result.text if result else None, args=args)
    if kind == "prose":
        return Claim(id=cid, kind="prose", span=span)
    return Claim(id=cid, kind="assert", span=span, expects=fence.text)


def fences_by_regex(doc: Doc) -> list[Claim]:
    claims: list[Claim] = []
    for item in plan_fences(doc):
        if item.role == "result":
            continue
        claims.append(fence_claim(item.fence, f"c{len(claims) + 1:02d}", doc=doc, result=item.result, args=item.args))
    return claims


def _rule_placed(plan: list[FencePlan]) -> list[FencePlan]:
    """Fences the rules place without the model: prompts, their results and fragments."""
    return [item for item in plan if item.role in ("prompt", "result", "prose")]


def _overlaps(start: int, end: int, fence: Fence) -> bool:
    return start <= fence.end_line and fence.start_line <= end


def _represents(claim: Claim, item: FencePlan) -> bool:
    """True when ``claim`` is exactly the claim the plan item compiles to."""
    f = item.fence
    if item.role == "shell":
        return claim.kind == "shell" and (claim.span.start_line, claim.span.end_line) == (f.start_line, f.end_line) and claim.command == f.text
    if item.role == "assert":
        return claim.kind == "assert" and (claim.span.start_line, claim.span.end_line) == (f.start_line, f.end_line) and claim.expects == f.text
    if item.role == "prose":
        return claim.kind == "prose" and (claim.span.start_line, claim.span.end_line) == (f.start_line, f.end_line)
    if item.role == "prompt":
        end = item.result.end_line if item.result else f.end_line
        expects = item.result.text if item.result else None
        return (
            claim.kind == "prompt"
            and (claim.span.start_line, claim.span.end_line) == (f.start_line, end)
            and claim.command == f.text
            and claim.expects == expects
            and claim.args == item.args
        )
    return False


def _span_from(doc: Doc, lc: LLMClaim) -> Span | None:
    if lc.start_line < 1 or lc.end_line > len(doc.lines) or lc.end_line < lc.start_line:
        return None
    text = "\n".join(doc.lines[lc.start_line - 1 : lc.end_line])
    return Span(start_line=lc.start_line, end_line=lc.end_line, text=text)


def check_claims(doc: Doc, drafts: list[LLMClaim]) -> list[str]:
    """Return problems in an LLM draft. Empty means all required claims are resolved."""
    problems: list[str] = []
    plan = plan_fences(doc)
    rule_fences = [item.fence for item in _rule_placed(plan)]
    if not drafts:
        problems.append("compiler returned no claims")
    for i, lc in enumerate(drafts):
        span = _span_from(doc, lc)
        if span is None:
            problems.append(f"claim {i + 1}: span {lc.start_line}-{lc.end_line} is outside the document")
            continue
        if any(_overlaps(lc.start_line, lc.end_line, f) for f in rule_fences):
            continue  # the rules place this block; the draft is dropped in assemble
        if _strip_ws(span.text) != _strip_ws(lc.text):
            problems.append(
                f"claim {i + 1}: text does not match lines {lc.start_line}-{lc.end_line}. Document says: {span.text!r}"
            )
        if lc.kind == "browser":
            if not lc.objective or not lc.objective.strip():
                problems.append(f"claim {i + 1}: browser claim has no objective")
            if not lc.expects or not lc.expects.strip():
                problems.append(f"claim {i + 1}: browser claim has no exact expected text")
            elif not _expectation_in_source(lc.expects, doc):
                problems.append(f"claim {i + 1}: expected browser text is not present in the source")
        if lc.kind == "assert" and not (lc.expects and lc.expects.strip()):
            problems.append(f"claim {i + 1}: assert claim has no expected text")
        if lc.kind == "shell" and not any(
            lc.start_line == f.start_line and lc.end_line == f.end_line and lc.command == f.text
            for f in doc.fences
            if classify_fence(f) == "shell"
        ):
            problems.append(f"claim {i + 1}: shell command is not verbatim from its source fence")

    for item in plan:
        if item.role not in ("shell", "assert"):
            continue  # prompts, results and fragments are placed by rule
        f, want = item.fence, item.role
        matches = [
            lc
            for lc in drafts
            if lc.kind == want
            and lc.start_line == f.start_line
            and lc.end_line == f.end_line
            and (lc.command == f.text if want == "shell" else lc.expects == f.text)
        ]
        if len(matches) != 1:
            problems.append(
                f"fenced block at lines {f.start_line}-{f.end_line} must have exactly one verbatim {want} claim"
            )

    for line in required_browser_lines(doc):
        if not any(lc.kind == "browser" and lc.start_line <= line <= lc.end_line for lc in drafts):
            problems.append(f"browser instruction at line {line} is missing a browser claim")
    return problems


def _fence_for(doc: Doc, lc: LLMClaim) -> Fence | None:
    for f in doc.fences:
        if (
            lc.start_line == f.start_line
            and lc.end_line == f.end_line
            and (lc.command == f.text if lc.kind == "shell" else lc.expects == f.text)
        ):
            return f
    return None


def assemble(doc: Doc, drafts: list[LLMClaim]) -> list[Claim]:
    """Turn checked drafts into Claims with ids in doc order and a dependency chain.

    Prompt fences, their result fences and fragments come from the rules, not
    the drafts: any draft touching one of those blocks is dropped."""
    plan = plan_fences(doc)
    rule_items = _rule_placed(plan)
    rule_fences = [item.fence for item in rule_items]
    drafts = sorted(
        (d for d in drafts if not any(_overlaps(d.start_line, d.end_line, f) for f in rule_fences)),
        key=lambda d: (d.start_line, d.end_line),
    )
    # placeholder ids; the final ids follow doc order once rule claims are merged in
    claims: list[Claim] = []
    for lc in drafts:
        cid = "c00"
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
    for item in rule_items:
        if item.role != "result":
            claims.append(fence_claim(item.fence, cid, doc=doc, result=item.result, args=item.args))
    claims.sort(key=lambda c: (c.span.start_line, c.span.end_line))
    numbered: list[Claim] = []
    for i, c in enumerate(claims):
        cid = f"c{i + 1:02d}"
        update: dict[str, Any] = {"id": cid}
        if c.kind == "browser":
            update["store_as"] = f"{cid}_label"
        numbered.append(c.model_copy(update=update))
    return chain_dependencies(numbered)


def chain_dependencies(claims: list[Claim]) -> list[Claim]:
    """Deterministic ordering rule: a step needs the previous shell step, and a
    browser step also needs the previous browser step. A prompt step needs only
    the previous shell step: prompt examples are independent Host runs, so one
    broken example never hides the ones after it."""
    out: list[Claim] = []
    last_shell: str | None = None
    last_browser: str | None = None
    for c in claims:
        deps: list[str] = []
        if c.kind in ("shell", "browser", "assert", "prompt") and last_shell:
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
    return cache_dir / f"v{CACHE_SCHEMA_VERSION}-{doc.sha256}.json"


def _legacy_cache_path(doc: Doc, cache_dir: Path) -> Path:
    return cache_dir / f"{doc.sha256}.json"


def _validate_compiled_claims(doc: Doc, claims: list[Claim]) -> list[str]:
    """Prove that cached/assembled claims still refer to this exact source."""
    problems: list[str] = []
    plan = plan_fences(doc)
    source_text = "\n".join(doc.lines)
    if doc.sha256 not in {sha256_of(source_text), sha256_of(source_text + "\n")}:
        problems.append("document hash does not match its current source lines")
    if not claims:
        problems.append("compiler returned no claims")
    ids = [claim.id for claim in claims]
    if len(ids) != len(set(ids)):
        problems.append("claim ids are not unique")
    known_ids: set[str] = set()
    for claim in claims:
        if claim.span.end_line > len(doc.lines):
            problems.append(f"{claim.id}: span extends beyond the source")
            continue
        source_text = "\n".join(doc.lines[claim.span.start_line - 1 : claim.span.end_line])
        if claim.span.text != source_text:
            problems.append(f"{claim.id}: span text does not match the current source")
        if any(dep not in known_ids for dep in claim.depends_on):
            problems.append(f"{claim.id}: dependency is missing or not earlier in the source")
        known_ids.add(claim.id)
        if claim.kind == "browser":
            if not claim.objective or not claim.objective.strip():
                problems.append(f"{claim.id}: browser claim has no objective")
            if not claim.expects or not claim.expects.strip():
                problems.append(f"{claim.id}: browser claim has no exact expected text")
            elif not _expectation_in_source(claim.expects, doc):
                problems.append(f"{claim.id}: expected browser text is not present in the source")
        elif claim.kind == "assert" and not (claim.expects and claim.expects.strip()):
            problems.append(f"{claim.id}: assert claim has no expected text")
        elif claim.kind == "shell":
            if not any(_represents(claim, item) for item in plan if item.role == "shell"):
                problems.append(f"{claim.id}: shell command does not match a source fence")
        elif claim.kind == "prompt":
            if not any(_represents(claim, item) for item in plan if item.role == "prompt"):
                problems.append(f"{claim.id}: prompt claim does not match a source fence and its result")

    for item in plan:
        if item.role == "result":
            continue  # represented by the prompt claim that owns it
        matches = [claim for claim in claims if _represents(claim, item)]
        if len(matches) != 1:
            f = item.fence
            problems.append(f"source fence at lines {f.start_line}-{f.end_line} is not represented exactly once")
    for line in required_browser_lines(doc):
        if not any(claim.kind == "browser" and claim.span.start_line <= line <= claim.span.end_line for claim in claims):
            problems.append(f"browser instruction at line {line} is missing a browser claim")
    return problems


def _read_cache(path: Path, doc: Doc, *, require_version: bool) -> list[Claim] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("sha256") != doc.sha256:
            return None
        if require_version and (
            data.get("cache_version") != CACHE_SCHEMA_VERSION
            or data.get("cache_key") != f"compiler-v{CACHE_SCHEMA_VERSION}:{doc.sha256}"
        ):
            return None
        raw_claims = data.get("claims")
        if not isinstance(raw_claims, list):
            return None
        claims = [Claim.model_validate(item) for item in raw_claims]
        if _validate_compiled_claims(doc, claims):
            return None
        return claims
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None


def load_cached(doc: Doc, cache_dir: Path = CACHE_DIR) -> list[Claim] | None:
    """Load only source-validated claims under the current compiler cache schema.

    Older hash-only caches remain a read-only migration path, but every claim is
    revalidated against the current source before it can be reused.
    """
    current = _cache_path(doc, cache_dir)
    if current.exists():
        return _read_cache(current, doc, require_version=True)
    legacy = _legacy_cache_path(doc, cache_dir)
    if legacy.exists():
        return _read_cache(legacy, doc, require_version=False)
    return None


def save_cache(doc: Doc, claims: list[Claim], cache_dir: Path = CACHE_DIR, meta: dict[str, Any] | None = None) -> None:
    problems = _validate_compiled_claims(doc, claims)
    if problems:
        raise CompileError("refusing to cache incomplete compilation: " + "; ".join(problems))
    cache_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "cache_version": CACHE_SCHEMA_VERSION,
        "cache_key": f"compiler-v{CACHE_SCHEMA_VERSION}:{doc.sha256}",
        "sha256": doc.sha256,
        "source": doc.source,
        "claims": [c.model_dump() for c in claims],
    }
    if meta:
        payload["meta"] = meta
    _cache_path(doc, cache_dir).write_text(json.dumps(payload, indent=2), encoding="utf-8")


def compile_doc(doc: Doc, llm: LLM | None = None, cache_dir: Path = CACHE_DIR, use_cache: bool = True) -> list[Claim]:
    """Compile a Doc into complete, source-bound claims; never return a partial set."""
    if use_cache:
        cached = load_cached(doc, cache_dir)
        if cached is not None:
            return cached
    if llm is None:
        if not os.environ.get("OPENAI_API_KEY"):
            # Without a model, compile only source-verbatim fences. Warn on browser
            # steps as main does, then fail closed rather than treating them as prose.
            if any(re.search(r"\b(?:open|click|type|browser|page)\b", line, re.I) for line in doc.lines):
                print(f"warning: {doc.source}: browser steps were not compiled (no OPENAI_API_KEY and no cache)", file=sys.stderr)
            if required_browser_lines(doc):
                raise CompileError("browser instructions require a compiler; no verified cache or model is available")
            if not doc.fences:
                # a chapter with no fenced block makes no claim a run can check:
                # an empty proof, so a book's prose-only chapters pass through
                print(f"warning: {doc.source}: no fenced blocks, so no claims", file=sys.stderr)
                return []
            claims = chain_dependencies(fences_by_regex(doc))
            problems = _validate_compiled_claims(doc, claims)
            if problems:
                raise CompileError("incomplete compilation: " + "; ".join(problems))
            if use_cache:
                save_cache(doc, claims, cache_dir, {"mode": "fences-only", "attempts": 0})
            return claims
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
        raise CompileError("incomplete compilation after retry: " + "; ".join(problems))

    claims = assemble(doc, drafts)
    problems = _validate_compiled_claims(doc, claims)
    if problems:
        raise CompileError("incomplete compilation: " + "; ".join(problems))
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
    plan = plan_fences(doc)
    for item in plan:
        f = item.fence
        if f.start_line <= start <= f.end_line or start <= f.start_line <= end:
            if item.role == "result":
                # a result fence belongs to the prompt before it
                item = next(p for p in plan if p.role == "prompt" and p.result is f)
            fresh = fence_claim(item.fence, claim.id, doc=doc, result=item.result, args=item.args)
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

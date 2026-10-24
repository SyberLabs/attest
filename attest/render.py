"""Render: one HTML page per doc, a badge SVG and a PROOF.md summary.

The page shows the original Markdown with a left-gutter badge per claim, a hover
reason, and a click-open evidence drawer. The ledger strip's numbers are passed
in from the ledger; the template never counts anything.

Markdown is rendered by a small block renderer here rather than a library, so
that every block keeps its source line range and the badges can sit next to the
lines the claims came from.
"""

from __future__ import annotations

import difflib
import html
import re
from urllib.parse import urlsplit
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup

from .models import Brief, Claim, Doc, Verdict

TEMPLATES = Path(__file__).resolve().parent / "templates"
BADGE_COLORS = {"proven": "#2e8b57", "stale": "#d9a400", "broken": "#c0392b", "unverifiable": "#8a8a8a", "skipped": "#5b7fa6"}


# -- tiny markdown -----------------------------------------------------------------------


@dataclass
class Block:
    kind: str  # heading | paragraph | code | list | blank | hr | quote | table
    start_line: int
    end_line: int
    html: str
    claims: list[Claim] = field(default_factory=list)


_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})\s*([^`\s]*)")
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_LIST = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$")
_HR = re.compile(r"^ {0,3}([-*_])(\s*\1){2,}\s*$")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")


def inline(text: str) -> str:
    """Escape, then code, bold, italics, links and bare URLs."""
    out: list[str] = []
    parts = re.split(r"(`[^`]+`)", text)
    for part in parts:
        if part.startswith("`") and part.endswith("`") and len(part) > 1:
            out.append(f"<code>{html.escape(part[1:-1])}</code>")
            continue
        markup: list[str] = []

        def safe_url(url: str) -> bool:
            scheme = urlsplit(url).scheme.casefold()
            return scheme in {"", "http", "https", "mailto"}

        def image(match):
            alt, url = match.groups()
            if not safe_url(url):
                return html.escape(alt)
            markup.append(f'<img alt="{html.escape(alt, quote=True)}" src="{html.escape(url, quote=True)}">')
            return f"\x00{len(markup) - 1}\x00"

        def link(match):
            label, url = match.groups()
            if not safe_url(url):
                return html.escape(label)
            markup.append(f'<a href="{html.escape(url, quote=True)}">{html.escape(label)}</a>')
            return f"\x00{len(markup) - 1}\x00"

        s = re.sub(r"!\[([^\]]*)\]\(((?:[^()\s]|\([^()\s]*\))+?)\)", image, part)
        s = re.sub(r"\[([^\]]+)\]\(((?:[^()\s]|\([^()\s]*\))+?)\)", link, s)
        s = html.escape(s, quote=False)
        s = re.sub(r"\x00(\d+)\x00", lambda m: markup[int(m.group(1))], s)
        s = re.sub(r"(?<![\"'=>])(https?://[^\s<)]+)", r'<a href="\1">\1</a>', s)
        s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
        s = re.sub(r"(?<!\*)\*(?!\*)([^*\n]+?)\*(?!\*)", r"<em>\1</em>", s)
        out.append(s)
    return "".join(out)


def blocks(lines: list[str]) -> list[Block]:
    out: list[Block] = []
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        m = _FENCE.match(line)
        if m:
            marker, lang = m.group(1), m.group(2)
            j = i + 1
            while j < n and not (lines[j].strip().startswith(marker[0] * 3) and lines[j].strip() == lines[j].strip()[0] * len(lines[j].strip())):
                j += 1
            body = "\n".join(lines[i + 1 : j])
            cls = f' class="lang-{html.escape(lang)}"' if lang else ""
            out.append(Block("code", i + 1, min(j + 1, n), f"<pre><code{cls}>{html.escape(body)}</code></pre>"))
            i = j + 1
            continue
        m = _HEADING.match(line)
        if m:
            level = len(m.group(1))
            out.append(Block("heading", i + 1, i + 1, f"<h{level}>{inline(m.group(2))}</h{level}>"))
            i += 1
            continue
        if _HR.match(line):
            out.append(Block("hr", i + 1, i + 1, "<hr>"))
            i += 1
            continue
        if line.lstrip().startswith(">"):
            j = i
            buf = []
            while j < n and lines[j].lstrip().startswith(">"):
                buf.append(lines[j].lstrip()[1:].strip())
                j += 1
            out.append(Block("quote", i + 1, j, f"<blockquote>{inline(' '.join(buf))}</blockquote>"))
            i = j
            continue
        if "|" in line and i + 1 < n and _TABLE_SEP.match(lines[i + 1]):
            j = i + 2
            while j < n and "|" in lines[j] and lines[j].strip():
                j += 1
            rows = [r.strip().strip("|").split("|") for r in lines[i:j] if not _TABLE_SEP.match(r)]
            head = "".join(f"<th>{inline(c.strip())}</th>" for c in rows[0])
            body = "".join("<tr>" + "".join(f"<td>{inline(c.strip())}</td>" for c in r) + "</tr>" for r in rows[1:])
            out.append(Block("table", i + 1, j, f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"))
            i = j
            continue
        m = _LIST.match(line)
        if m:
            ordered = m.group(2)[0].isdigit()
            j = i
            items = []
            while j < n and lines[j].strip():
                lm = _LIST.match(lines[j])
                if lm:
                    items.append(inline(lm.group(3)))
                elif items:
                    items[-1] += " " + inline(lines[j].strip())
                else:
                    break
                j += 1
            tag = "ol" if ordered else "ul"
            out.append(Block("list", i + 1, j, f"<{tag}>" + "".join(f"<li>{it}</li>" for it in items) + f"</{tag}>"))
            i = j
            continue
        j = i
        buf = []
        while j < n and lines[j].strip() and not _FENCE.match(lines[j]) and not _HEADING.match(lines[j]) and not _LIST.match(lines[j]):
            buf.append(lines[j].strip())
            j += 1
        out.append(Block("paragraph", i + 1, j, f"<p>{inline(' '.join(buf))}</p>"))
        i = j
    return out


def attach_claims(blks: list[Block], claims: list[Claim]) -> list[Block]:
    """Put each claim on the first block its span touches. Prose gets no badge."""
    for c in claims:
        if c.kind == "prose":
            continue
        for b in blks:
            if b.start_line <= c.span.end_line and c.span.start_line <= b.end_line:
                b.claims.append(c)
                break
    return blks


# -- page -------------------------------------------------------------------------------


def _env() -> Environment:
    env = Environment(loader=FileSystemLoader(str(TEMPLATES)), autoescape=select_autoescape(["html", "j2"]))
    env.filters["inline_md"] = inline
    return env


def render_page(
    doc: Doc,
    claims: list[Claim],
    verdicts: list[Verdict],
    strip: dict,
    briefs: dict[str, Brief] | None = None,
    prior_verdicts: dict[str, Verdict] | None = None,
    title: str | None = None,
    runner_name: str = "none",
    live: bool = False,
    now: str = "",
) -> str:
    """Return the HTML. ``strip`` is the ledger summary dict, already counted."""
    by_id = {v.claim_id: v for v in verdicts}
    blks = attach_claims(blocks(doc.lines), claims)
    briefs = briefs or {}
    prior_verdicts = prior_verdicts or {}
    grounding_lines = {
        claim_id: [line for line in brief.grounding if "] unavailable:" not in line]
        for claim_id, brief in briefs.items()
    }
    runner_notes = {}
    pack_suffix = re.compile(r"(?:\s*\|\s*)pack:\s*(.+\.evidence)\s*$", re.IGNORECASE)
    for claim in claims:
        verdict = by_id.get(claim.id)
        note = verdict.evidence.observation.note if verdict and verdict.evidence and verdict.evidence.observation else None
        match = pack_suffix.search(note or "")
        if match:
            path = match.group(1)
            runner_notes[claim.id] = {
                "text": (note[:match.start()].rstrip(" |") if note[:match.start()].strip() else ""),
                "pack": PureWindowsPath(path).stem,
                "path": path,
            }
        else:
            runner_notes[claim.id] = {"text": note or "", "pack": None, "path": None}
    view = _presentation(doc, claims, by_id, blks, strip, briefs, prior_verdicts, now)
    tmpl = _env().get_template("index.html.j2")
    return tmpl.render(
        **view,
        title=title or _title_of(doc),
        doc=doc,
        blocks=blks,
        claims=claims,
        verdicts=by_id,
        briefs=briefs,
        prior=prior_verdicts,
        strip=strip,
        colors=BADGE_COLORS,
        runner_name=runner_name,
        grounding_lines=grounding_lines,
        runner_notes=runner_notes,
        live=live,
        now=now,
    )


# -- presentation helpers (layout only: nothing here judges or counts claims) -----------------

STATUS_ORDER = ("proven", "stale", "broken", "unverifiable", "skipped")
STATUS_WORDS = {
    "proven": "proven",
    "stale": "stale",
    "broken": "broken",
    "unverifiable": "unverified",
    "skipped": "skipped",
    "none": "waiting",
}
_TOKENS = re.compile(r"\s+|\w+|[^\w\s]")
_LINE_REF = re.compile(r"\bline (\d+)")


def _score(strip: dict) -> dict:
    """Bar widths and a tone for the header, from the ledger numbers as given."""
    total = int(strip.get("claims") or 0)
    counts = {s: int(strip.get(s) or 0) for s in STATUS_ORDER}
    if total and counts["proven"] == total:
        tone = "proven"
    elif counts["broken"]:
        tone = "broken"
    elif counts["stale"]:
        tone = "stale"
    else:
        tone = "none"
    segments = [
        {"status": s, "count": n, "pct": round(100.0 * n / total, 3)}
        for s, n in counts.items()
        if n and total
    ]
    return {"tone": tone, "segments": segments}


def _intraline(old: str, new: str) -> tuple[str, str, str, str]:
    """GitHub-style word highlight for one removed/added pair.

    Returns (old_html, new_html, old_fragment, new_fragment); fragments are the
    changed words, used for the Before / After cards.
    """
    a, b = _TOKENS.findall(old), _TOKENS.findall(new)
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    if sm.ratio() < 0.3:
        return html.escape(old), html.escape(new), old.strip(), new.strip()
    changed = [op for op in sm.get_opcodes() if op[0] != "equal"]
    if not changed:
        return html.escape(old), html.escape(new), "", ""
    # one highlighted region per side, from the first change to the last, so
    # "Vite + React" -> "Get started" reads as one phrase, not word confetti
    i1, i2 = changed[0][1], changed[-1][2]
    j1, j2 = changed[0][3], changed[-1][4]

    def mark(tokens: list[str], s: int, e: int) -> str:
        mid = "".join(tokens[s:e])
        return html.escape("".join(tokens[:s])) + (f"<mark>{html.escape(mid)}</mark>" if mid else "") + html.escape("".join(tokens[e:]))

    return mark(a, i1, i2), mark(b, j1, j2), "".join(a[i1:i2]).strip(), "".join(b[j1:j2]).strip()


def diff_view(patch: str | None) -> dict:
    """Rows for the diff box (kind + safe html) and the changed words on each side."""
    rows: list[dict] = []
    said: list[str] = []
    says: list[str] = []
    if not patch:
        return {"rows": rows, "said": "", "says": ""}
    lines = patch.splitlines()
    i, n = 0, len(lines)
    while i < n:
        ln = lines[i]
        if ln.startswith(("diff ", "index ")):
            rows.append({"kind": "meta", "html": Markup(html.escape(ln))})
            i += 1
            continue
        if ln.startswith("--- ") and i + 1 < n and lines[i + 1].startswith("+++ "):
            rows.append({"kind": "meta", "html": Markup(html.escape(ln))})
            rows.append({"kind": "meta", "html": Markup(html.escape(lines[i + 1]))})
            i += 2
            continue
        if ln.startswith("@@"):
            rows.append({"kind": "hunk", "html": Markup(html.escape(ln))})
            i += 1
            continue
        if ln.startswith(("-", "+")):
            dels, adds = [], []
            while i < n and lines[i].startswith("-") and not (lines[i].startswith("--- ") and i + 1 < n and lines[i + 1].startswith("+++ ")):
                dels.append(lines[i][1:])
                i += 1
            while i < n and lines[i].startswith("+"):
                adds.append(lines[i][1:])
                i += 1
            del_html = [html.escape(d) for d in dels]
            add_html = [html.escape(a) for a in adds]
            for k in range(min(len(dels), len(adds))):
                del_html[k], add_html[k], of, nf = _intraline(dels[k], adds[k])
                said.append(of)
                says.append(nf)
            if not said and not says:
                said += [d.strip() for d in dels]
                says += [a.strip() for a in adds]
            rows += [{"kind": "del", "html": Markup("-" + h)} for h in del_html]
            rows += [{"kind": "add", "html": Markup("+" + h)} for h in add_html]
            continue
        rows.append({"kind": "ctx", "html": Markup(html.escape(ln))})
        i += 1
    return {"rows": rows, "said": " / ".join(s for s in said if s), "says": " / ".join(s for s in says if s)}


def runner_saw(v: Verdict | None) -> str:
    """One short phrase for what the runner saw, for the Before / After cards."""
    if v is None or v.evidence is None:
        return ""
    ev = v.evidence
    if ev.kind == "shell":
        return "timed out" if ev.timed_out else f"exit code {ev.exit_code}"
    obs = ev.observation
    if obs is None:
        return ""
    if obs.observed:
        return ", ".join(f'"{val}"' for val in obs.observed.values())
    return obs.status


def _now_actor(now: str) -> str:
    low = now.lower()
    if "agent" in low or "codex" in low:
        return "agent"
    if "kane" in low:
        return "kane"
    if low.startswith("running"):
        return "shell"
    return "attest"


def _presentation(doc, claims, by_id, blks, strip, briefs, prior, now) -> dict:
    badged = [c for c in claims if c.kind != "prose"]
    m = _LINE_REF.search(now or "")
    active_line = int(m.group(1)) if m else None
    active = {c.id for c in badged if active_line is not None and c.span.start_line <= active_line <= c.span.end_line}
    fixed = {cid for cid in briefs if by_id.get(cid) is not None and by_id[cid].status == "proven"}
    row_tones = []
    for b in blks:
        statuses = {by_id[c.id].status for c in b.claims if c.id in by_id}
        ids = {c.id for c in b.claims}
        if "broken" in statuses:
            tone = "broken"
        elif "stale" in statuses:
            tone = "stale"
        elif ids & fixed:
            tone = "fixed"
        elif ids & active:
            tone = "active"
        else:
            tone = ""
        row_tones.append(tone)
    open_ids = {c.id for c in badged if c.id in briefs or (c.id in by_id and by_id[c.id].status in ("stale", "broken"))}
    return {
        "doc_name": Path(doc.source).name or doc.source,
        "score": _score(strip),
        "status_words": STATUS_WORDS,
        "active_ids": active,
        "fixed_ids": fixed,
        "open_ids": open_ids,
        "row_tones": row_tones,
        "diffs": {cid: diff_view(b.patch) for cid, b in briefs.items()},
        "saw": {cid: runner_saw(v) for cid, v in by_id.items()},
        "saw_before": {cid: runner_saw(v) for cid, v in prior.items()},
        "now_actor": _now_actor(now or ""),
        "progress": {"judged": sum(1 for c in badged if c.id in by_id), "total": len(badged)},
    }


def _title_of(doc: Doc) -> str:
    for ln in doc.lines:
        m = _HEADING.match(ln)
        if m:
            return m.group(2)
    return Path(doc.source).name


def write_page(out_dir: Path, html_text: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / "index.html"
    p.write_text(html_text, encoding="utf-8")
    return p


# -- export ----------------------------------------------------------------------------


def badge_svg(proven: int, total: int) -> str:
    """Shields-style badge: docs | P/N proven."""
    label = "docs"
    value = f"{proven}/{total} proven"
    color = "#2e8b57" if total and proven == total else ("#d9a400" if proven else "#c0392b")
    if total == 0:
        color = "#8a8a8a"
    lw = 6 * len(label) + 12
    vw = 6 * len(value) + 12
    w = lw + vw
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="20" role="img" aria-label="{label}: {value}">
  <title>{label}: {value}</title>
  <linearGradient id="s" x2="0" y2="100%"><stop offset="0" stop-color="#bbb" stop-opacity=".1"/><stop offset="1" stop-opacity=".1"/></linearGradient>
  <clipPath id="r"><rect width="{w}" height="20" rx="3" fill="#fff"/></clipPath>
  <g clip-path="url(#r)">
    <rect width="{lw}" height="20" fill="#555"/>
    <rect x="{lw}" width="{vw}" height="20" fill="{color}"/>
    <rect width="{w}" height="20" fill="url(#s)"/>
  </g>
  <g fill="#fff" text-anchor="middle" font-family="Verdana,Geneva,DejaVu Sans,sans-serif" font-size="11">
    <text x="{lw / 2}" y="14">{label}</text>
    <text x="{lw + vw / 2}" y="14">{value}</text>
  </g>
</svg>
"""


def proof_md(doc: Doc, claims: list[Claim], verdicts: list[Verdict], strip: dict, runner_name: str) -> str:
    by_id = {v.claim_id: v for v in verdicts}
    lines = [
        f"# PROOF for {Path(doc.source).name}",
        "",
        f"Source: `{doc.source}`  ",
        f"Commit: `{doc.commit or 'unknown'}`  ",
        f"SHA-256: `{doc.sha256}`  ",
        f"Browser runner: `{runner_name}`  ",
        f"Turn: {strip.get('turn')}",
        "",
        f"**{strip.get('claims', 0)} claims: {strip.get('proven', 0)} proven, {strip.get('stale', 0)} stale, "
        f"{strip.get('broken', 0)} broken, {strip.get('unverifiable', 0)} unverifiable, {strip.get('skipped', 0)} skipped.**",
        "",
        "| Claim | Lines | Kind | Status | Reason |",
        "| --- | --- | --- | --- | --- |",
    ]
    for c in claims:
        if c.kind == "prose":
            continue
        v = by_id.get(c.id)
        status = v.status if v else "none"
        reason = (v.reason if v else "").replace("|", "\\|")
        lines.append(f"| {c.id} | {c.span.start_line}-{c.span.end_line} | {c.kind} | {status} | {reason} |")
    lines.append("")
    lines.append("Generated by Attest. Numbers are read from the ledger.")
    return "\n".join(lines) + "\n"

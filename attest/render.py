"""Render: one HTML page per doc, a badge SVG and a PROOF.md summary.

The page shows the original Markdown with a left-gutter badge per claim, a hover
reason, and a click-open evidence drawer. The ledger strip's numbers are passed
in from the ledger; the template never counts anything.

Markdown is rendered by a small block renderer here rather than a library, so
that every block keeps its source line range and the badges can sit next to the
lines the claims came from.
"""

from __future__ import annotations

import base64
import hashlib
import html
import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from jinja2 import Environment, FileSystemLoader, select_autoescape

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


_INLINE_TOKEN = re.compile(
    r"(?P<code>`[^`]+`)"
    r"|(?P<image>!\[([^\]]*)\]\(([^)\s]+)\))"
    r"|(?P<link>\[([^\]]+)\]\(([^)\s]+)\))"
    r"|(?P<url>https?://[^\s<]+)"
    r"|(?P<strong>\*\*.+?\*\*)"
    r"|(?P<em>(?<!\*)\*(?!\*)([^*\n]+?)\*(?!\*))"
)


def _safe_link_target(value: str) -> str | None:
    """Allow ordinary HTTP(S) and relative links, never executable schemes."""
    if not value or "\\" in value or any(ch.isspace() or ord(ch) < 0x20 or 0x7F <= ord(ch) <= 0x9F for ch in value):
        return None
    try:
        parts = urlsplit(value)
    except ValueError:
        return None
    if parts.scheme:
        if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
            return None
    elif parts.netloc or value.startswith("//"):
        return None
    return value


def _image_mime(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _store_image_artifact(source: str, artifact_dir: Path | None) -> str | None:
    """Copy a recognized image into a write-once, content-addressed store."""
    if artifact_dir is None:
        return None
    try:
        evidence_root = artifact_dir.parent.parent.resolve(strict=True)
        evidence_path = Path(source).resolve(strict=True)
        evidence_path.relative_to(evidence_root)
        fd = os.open(evidence_path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as stream:
            metadata = os.fstat(stream.fileno())
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 32 * 1024 * 1024:
                return None
            data = stream.read(32 * 1024 * 1024 + 1)
        if len(data) > 32 * 1024 * 1024:
            return None
    except (OSError, ValueError):
        return None
    if _image_mime(data) is None:
        return None
    digest = hashlib.sha256(data).hexdigest()
    artifact_dir.mkdir(parents=True, exist_ok=True)
    if artifact_dir.is_symlink():
        raise ValueError("artifact store must not be a symlink")
    store_root = artifact_dir.resolve(strict=True)
    store_root.relative_to(artifact_dir.parent.resolve(strict=True))
    target = store_root / digest
    try:
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        if target.is_symlink() or not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            raise ValueError(f"immutable artifact store entry is corrupt: {digest}")
    else:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
    return f"/artifacts/{digest}"


def content_security_policy(html_text: str) -> str:
    """CSP for the page's fixed inline CSS/JS, with all other sources denied."""
    def hashes(tag: str) -> str:
        values = re.findall(rf"<{tag}\b[^>]*>(.*?)</{tag}\s*>", html_text, re.DOTALL | re.IGNORECASE)
        return " ".join(
            "'sha256-" + base64.b64encode(hashlib.sha256(value.encode("utf-8")).digest()).decode("ascii") + "'"
            for value in values
        ) or "'none'"

    return "; ".join(
        (
            "default-src 'none'",
            "img-src 'self'",
            f"style-src {hashes('style')}",
            f"script-src {hashes('script')}",
            "connect-src 'none'",
            "font-src 'self'",
            "object-src 'none'",
            "base-uri 'none'",
            "form-action 'none'",
            "frame-ancestors 'none'",
        )
    )


def inline(text: str) -> str:
    """Render a small safe Markdown subset without interpolating raw attributes."""
    out: list[str] = []
    cursor = 0
    for match in _INLINE_TOKEN.finditer(text):
        out.append(html.escape(text[cursor : match.start()]))
        token = match.group(0)
        if match.lastgroup == "code":
            out.append(f"<code>{html.escape(token[1:-1])}</code>")
        elif match.lastgroup == "image":
            image = re.fullmatch(r"!\[([^\]]*)\]\(([^)\s]+)\)", token)
            assert image is not None
            target = _safe_link_target(image.group(2))
            if target is None or not re.fullmatch(r"/artifacts/[0-9a-f]{64}", target):
                out.append(html.escape(token))
            else:
                out.append(f'<img alt="{html.escape(image.group(1), quote=True)}" src="{html.escape(target, quote=True)}">')
        elif match.lastgroup == "link":
            link = re.fullmatch(r"\[([^\]]+)\]\(([^)\s]+)\)", token)
            assert link is not None
            target = _safe_link_target(link.group(2))
            if target is None:
                out.append(html.escape(token))
            else:
                out.append(f'<a href="{html.escape(target, quote=True)}">{inline(link.group(1))}</a>')
        elif match.lastgroup == "url":
            target = token.rstrip(".,;:!?)]}")
            tail = token[len(target) :]
            safe_target = _safe_link_target(target)
            if safe_target is None:
                out.append(html.escape(token))
            else:
                escaped = html.escape(safe_target, quote=True)
                out.append(f'<a href="{escaped}">{html.escape(safe_target)}</a>{html.escape(tail)}')
        elif match.lastgroup == "strong":
            out.append(f"<strong>{inline(token[2:-2])}</strong>")
        else:
            out.append(f"<em>{inline(token[1:-1])}</em>")
        cursor = match.end()
    out.append(html.escape(text[cursor:]))
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
    artifact_dir: Path | None = None,
) -> str:
    """Return the HTML. ``strip`` is the ledger summary dict, already counted."""
    by_id = {v.claim_id: v for v in verdicts}
    blks = attach_claims(blocks(doc.lines), claims)
    briefs = briefs or {}
    prior_verdicts = prior_verdicts or {}
    rendered_text = "\n".join(doc.lines)
    rendered_hashes = {
        hashlib.sha256(rendered_text.encode("utf-8")).hexdigest(),
        hashlib.sha256((rendered_text + "\n").encode("utf-8")).hexdigest(),
    }
    has_revision = bool(prior_verdicts) and doc.sha256 not in rendered_hashes
    rendered_identity = (
        hashlib.sha256((rendered_text + "\n").encode("utf-8")).hexdigest()
        if has_revision
        else doc.sha256
    )
    artifact_urls = {
        claim_id: url
        for claim_id, v in by_id.items()
        if v.evidence and v.evidence.screenshot
        for url in [_store_image_artifact(v.evidence.screenshot, artifact_dir)]
        if url
    }
    coverage_gaps = [
        (claim.id, by_id[claim.id].status if claim.id in by_id else "no verdict")
        for claim in claims
        if claim.kind != "prose" and (claim.id not in by_id or by_id[claim.id].status != "proven")
    ]
    has_proposal = any(brief.patch and brief.patch.strip() for brief in briefs.values())
    tmpl = _env().get_template("index.html.j2")
    return tmpl.render(
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
        artifact_urls=artifact_urls,
        coverage_gaps=coverage_gaps,
        coverage_claims=sum(1 for claim in claims if claim.kind != "prose"),
        authority_status="Unresolved",
        authority_detail="No authority decision is recorded; protected-contract refusals remain unresolved and are not a green result.",
        has_proposal=has_proposal,
        before_identity=doc.sha256 if has_revision else None,
        after_identity=rendered_identity if has_revision else doc.sha256,
    )


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

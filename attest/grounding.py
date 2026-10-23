"""Grounding: lines with source refs that help an agent fix a failed claim.

Three sources, all cached under cache/ so the demo runs offline:

* the doc itself: the claim's section, line-numbered
* GitHits via its CLI (``npx githits@latest search|grep|pkg changelog --json``)
* for AI-answer docs, the cached official help page for the card (cache/help/<card>.md)

GitHits calls are subprocesses with timeouts. Every response, including errors,
is cached by the hash of its arguments. Without a GitHits login the CLI returns
an AUTH_REQUIRED error; that is recorded as a grounding line, not raised.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

from .models import Claim, Doc

CACHE_ROOT = Path(os.environ.get("ATTEST_CACHE_DIR", "cache"))
GITHITS_CACHE = CACHE_ROOT / "githits"
HELP_CACHE = CACHE_ROOT / "help"
GITHITS_TIMEOUT_S = 60
MAX_LINES_PER_SOURCE = 5

Runner = Callable[[list[str], int], tuple[int, str, str]]


# -- subprocess + cache -------------------------------------------------------------------


def _run(args: list[str], timeout_s: int) -> tuple[int, str, str]:
    npx = shutil.which("npx") or shutil.which("npx.cmd") or "npx"
    cmd = [npx, "-y", "githits@latest", *args]
    try:
        p = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
            shell=sys.platform == "win32",  # npx is a .cmd shim on Windows
        )
    except subprocess.TimeoutExpired:
        return 124, "", f"timed out after {timeout_s}s"
    except OSError as e:
        return 127, "", str(e)
    return p.returncode, p.stdout, p.stderr


def _cache_key(args: list[str]) -> str:
    return hashlib.sha256(json.dumps(args).encode("utf-8")).hexdigest()[:24]


def githits_json(args: list[str], cache_dir: Path = GITHITS_CACHE, runner: Runner = _run, timeout_s: int = GITHITS_TIMEOUT_S) -> dict[str, Any]:
    """Run one GitHits command with --json and return its parsed output.

    The returned dict always has ``args`` and ``exit_code``; ``data`` holds the
    parsed JSON (or None) and ``error`` a short message when the call failed.
    """
    if "--json" not in args:
        args = [*args, "--json"]
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{_cache_key(args)}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    code, out, err = runner(args, timeout_s)
    data: Any = None
    error: str | None = None
    text = out.strip() or err.strip()
    try:
        data = json.loads(text) if text else None
    except json.JSONDecodeError:
        error = text[:300] if text else "no output"
    if isinstance(data, dict) and data.get("error"):
        error = f"{data.get('code', 'ERROR')}: {data['error']}"
    if code != 0 and error is None:
        error = f"exit {code}: {err.strip()[:300]}"
    record = {"args": args, "exit_code": code, "data": data, "error": error}
    path.write_text(json.dumps(record, indent=1), encoding="utf-8")
    return record


# -- parsing GitHits output -----------------------------------------------------------


# VERIFY: the exact key names in GitHits --json output. The parser below walks the
# payload for any object that looks like a hit (has a path or url plus text) so it
# survives naming differences; tighten it once a real response is cached.
_TEXT_KEYS = ("text", "snippet", "content", "line", "excerpt", "body", "statement", "title", "summary")
_REF_KEYS = ("path", "url", "file", "source", "target", "repo", "version", "ref")
_LINE_KEYS = ("line", "lineNumber", "line_number", "startLine", "start_line")


def hit_lines(data: Any, prefix: str, limit: int = MAX_LINES_PER_SOURCE) -> list[str]:
    """Flatten a GitHits JSON payload into '[prefix ref:line] text' strings."""
    out: list[str] = []

    def visit(node: Any) -> None:
        if len(out) >= limit:
            return
        if isinstance(node, dict):
            ref = next((str(node[k]) for k in _REF_KEYS if node.get(k) and isinstance(node[k], (str, int))), None)
            text = next((str(node[k]) for k in _TEXT_KEYS if isinstance(node.get(k), str) and node[k].strip()), None)
            if ref and text and ref != text:
                line = next((str(node[k]) for k in _LINE_KEYS if isinstance(node.get(k), int)), None)
                loc = f"{ref}:{line}" if line else ref
                out.append(f"[{prefix} {loc}] {' '.join(text.split())[:240]}")
                return
            for v in node.values():
                visit(v)
        elif isinstance(node, list):
            for v in node:
                visit(v)

    visit(data)
    return out


# -- sources ------------------------------------------------------------------------------


def ground_doc(doc: Doc, claim: Claim, context: int = 3) -> list[str]:
    start = max(1, claim.span.start_line - context)
    end = min(len(doc.lines), claim.span.end_line + context)
    return [f"[doc {Path(doc.source).name}:{n}] {doc.lines[n - 1]}" for n in range(start, end + 1) if doc.lines[n - 1].strip()]


def ground_githits(claim: Claim, repo: str | None, package: str | None = None, cache_dir: Path = GITHITS_CACHE, runner: Runner = _run) -> list[str]:
    """search --source docs, grep -F, and pkg changelog, as the architecture doc lists them."""
    needle = (claim.expects or "").strip()
    lines: list[str] = []
    if needle and repo:
        rec = githits_json(["search", needle, "--in", repo, "--source", "docs"], cache_dir, runner)
        lines += _lines_or_error(rec, "githits search")
        rec = githits_json(["grep", "-F", needle, repo, "--corpus", "all", "--limit", "5"], cache_dir, runner)
        lines += _lines_or_error(rec, "githits grep")
    if package:
        rec = githits_json(["pkg", "changelog", package, "--changes", "important", "--limit", "5"], cache_dir, runner)
        lines += _lines_or_error(rec, "githits changelog")
    return lines


def _lines_or_error(rec: dict[str, Any], prefix: str) -> list[str]:
    if rec.get("error"):
        return [f"[{prefix}] unavailable: {rec['error']}"]
    found = hit_lines(rec.get("data"), prefix)
    return found or [f"[{prefix}] no hits"]


_WORD = re.compile(r"\w+")


def ground_help(claim: Claim, card: str, help_dir: Path = HELP_CACHE, limit: int = MAX_LINES_PER_SOURCE) -> list[str]:
    """Lines from the cached official help page that share words with the claim."""
    path = help_dir / f"{card}.md"
    if not path.exists():
        return [f"[help {card}] no cached help page at {path}"]
    source_url = None
    lines = path.read_text(encoding="utf-8").splitlines()
    if lines and lines[0].startswith("<!-- source:"):
        source_url = lines[0][len("<!-- source:"):].rstrip("-> ").strip()
    needle_words = {w.lower() for w in _WORD.findall(f"{claim.expects or ''} {claim.span.text}") if len(w) > 3}
    scored: list[tuple[int, int, str]] = []
    for n, ln in enumerate(lines, 1):
        words = {w.lower() for w in _WORD.findall(ln)}
        hit = len(words & needle_words)
        if hit and ln.strip() and not ln.startswith("<!--"):
            scored.append((hit, n, ln.strip()))
    scored.sort(key=lambda t: (-t[0], t[1]))
    ref = source_url or path.name
    return [f"[help {ref}:{n}] {text[:240]}" for _, n, text in scored[:limit]] or [f"[help {ref}] nothing in the help page mentions this step"]


def card_for(doc: Doc) -> str | None:
    """AI-answer docs live at demo/answers/<card>.md and carry a 'card:' frontmatter key."""
    for ln in doc.lines[:12]:
        if ln.startswith("card:"):
            return ln.split(":", 1)[1].strip()
    p = Path(doc.source)
    if p.parent.name == "answers":
        return p.stem
    return None


def repo_for(doc: Doc, targets_path: Path = Path("targets.yaml")) -> str | None:
    """github:owner/repo for a demo target, read from targets.yaml by doc path."""
    try:
        import yaml

        data = yaml.safe_load(targets_path.read_text(encoding="utf-8")) or {}
    except (OSError, ValueError):
        return None
    name = Path(doc.source).name
    for t in data.get("targets", []):
        if Path(t.get("doc", "")).name == name:
            m = re.search(r"github\.com/([^/]+)/([^/]+)", t.get("upstream", ""))
            if m:
                ref = f"@{t['commit']}" if t.get("commit") else ""
                return f"github:{m.group(1)}/{m.group(2)}{ref}"
    return None


def ground(doc: Doc, claim: Claim, runner: Runner = _run, cache_dir: Path = GITHITS_CACHE, help_dir: Path = HELP_CACHE) -> list[str]:
    """Everything the brief needs, in order: doc section, help page (AI answers), GitHits."""
    lines = ground_doc(doc, claim)
    card = card_for(doc)
    if card:
        lines += ground_help(claim, card, help_dir)
    repo = repo_for(doc)
    if repo or not card:
        lines += ground_githits(claim, repo, cache_dir=cache_dir, runner=runner)
    return lines

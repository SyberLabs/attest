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


RUNVIL_SRC_ENV = "ATTEST_RUNVIL_SRC"


def _git(root: Path, *args: str, timeout_s: int = 20) -> str:
    try:
        p = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout_s)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return p.stdout if p.returncode == 0 else ""


def ground_runvil(claim: Claim, verdict, root: Path | None = None, limit: int = MAX_LINES_PER_SOURCE) -> list[str]:
    """Grounding for a failing prompt example from a local Runvil checkout pinned to one
    commit (``ATTEST_RUNVIL_SRC``), as the spec asks: the commits that touched the name the
    failure names, and how the code uses its owner today. Offline; nothing is cached."""
    from .rootcause import failure_text, named, qualified

    root = root or (Path(os.environ[RUNVIL_SRC_ENV]) if os.environ.get(RUNVIL_SRC_ENV) else None)
    if root is None or not (root / ".git").exists():
        return []
    head = _git(root, "rev-parse", "--short=12", "HEAD").strip() or "unknown"
    lines = [f"[runvil@{head}] grounding from the local Runvil checkout"]
    _label, message = failure_text(claim, verdict)
    hit = named(message)
    if hit is None:
        # a result that differs: search the history for the wording the doc has and the run lost
        phrase = _lost_phrase(claim, verdict)
        if phrase:
            log = _git(root, "log", "--format=%h %ad %s", "--date=short", "-S", phrase, "-n", str(limit), "--", "crates")
            lines += [f"[runvil commit] {ln}  (touches {phrase!r})" for ln in log.splitlines()[:limit]] or [f"[runvil commit] no commit in this checkout touches {phrase!r}"]
        return lines
    name = qualified(hit[0], [claim.command or ""])
    bare = name.rsplit(".", 1)[-1]
    log = _git(root, "log", "--format=%h %ad %s", "--date=short", "-S", bare, "-n", str(limit), "--", "crates")
    lines += [f"[runvil commit] {ln}" for ln in log.splitlines()[:limit]] or [f"[runvil commit] no commit in this checkout touches `{bare}`"]
    if "." in name:
        owner = name.split(".", 1)[0]
        uses = _git(root, "grep", "-h", "-o", "-E", rf"\b{re.escape(owner)}\.[a-z_]+\(", "--", "crates", "*.rs", "*.lua", "*.md")
        counts: dict[str, int] = {}
        for u in uses.splitlines():
            counts[u] = counts.get(u, 0) + 1
        top = sorted(counts.items(), key=lambda kv: -kv[1])[: limit + 3]
        if top:
            lines.append(f"[runvil source] `{owner}.*` calls in the code today: " + ", ".join(f"{k[:-1]} ({v})" for k, v in top))
        if not any(k[:-1] == name for k, _ in counts.items()):
            lines.append(f"[runvil source] `{name}` does not appear in the code at this commit")
    return lines


def _lost_phrase(claim: Claim, verdict) -> str | None:
    """The first run of words the doc's result fence has and the run's result does not,
    with one word of context, such as 'author tasks' when the run says 'tasks'."""
    import difflib

    ev = getattr(verdict, "evidence", None)
    result = ev.prompt.result if ev is not None and ev.kind == "prompt" and ev.prompt is not None else None
    if not claim.expects or result is None:
        return None
    doc_words, run_words = claim.expects.split(), result.split()
    for tag, i1, i2, _j1, _j2 in difflib.SequenceMatcher(a=doc_words, b=run_words, autojunk=False).get_opcodes():
        if tag in ("delete", "replace"):
            words = doc_words[i1 : min(i2, i1 + 3)]
            if i2 < len(doc_words) and len(words) < 3:
                words = words + [doc_words[i2]]
            phrase = " ".join(words).strip(".,;:|")
            return phrase if len(phrase) >= 4 else None
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

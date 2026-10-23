"""Ingest: read a doc from a path or URL and turn it into a Doc.

Records source, commit when known, SHA-256 of the normalized text, numbered
lines, fenced code block ranges and the URLs it mentions. Deterministic: the
same bytes always give the same hash and the same fences.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path

import httpx

from .models import Doc, Fence

_FENCE_RE = re.compile(r"^( {0,3})(`{3,}|~{3,})\s*([^`\s]*)")
_URL_RE = re.compile(r"https?://[^\s<>()\[\]\"'`]+")
_GITHUB_BLOB_RE = re.compile(r"^https?://github\.com/([^/]+)/([^/]+)/blob/([^/]+)/(.+)$")
_SHA_RE = re.compile(r"\b[0-9a-f]{40}\b")


def normalize(text: str) -> str:
    """CRLF to LF, strip a BOM, keep everything else."""
    if text.startswith("﻿"):
        text = text[1:]
    return text.replace("\r\n", "\n").replace("\r", "\n")


def sha256_of(text: str) -> str:
    return hashlib.sha256(normalize(text).encode("utf-8")).hexdigest()


def split_lines(text: str) -> list[str]:
    text = normalize(text)
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def find_fences(lines: list[str]) -> list[Fence]:
    """Locate fenced code blocks. Returned line numbers cover the content, not the fences."""
    fences: list[Fence] = []
    i = 0
    n = len(lines)
    while i < n:
        m = _FENCE_RE.match(lines[i])
        if not m:
            i += 1
            continue
        marker = m.group(2)
        lang = m.group(3)
        start = i + 1
        j = start
        closed = False
        while j < n:
            cm = _FENCE_RE.match(lines[j])
            if cm and cm.group(2)[0] == marker[0] and len(cm.group(2)) >= len(marker) and lines[j].strip() == cm.group(2):
                closed = True
                break
            j += 1
        end = j  # exclusive index of the closing fence (or n if unclosed)
        content = lines[start:end]
        if content:
            fences.append(
                Fence(start_line=start + 1, end_line=end, lang=lang, text="\n".join(content))
            )
        i = end + 1 if closed else n
    return fences


def find_urls(text: str) -> list[str]:
    seen: list[str] = []
    for raw in _URL_RE.findall(text):
        url = raw.rstrip(".,;:!?)")
        if url not in seen:
            seen.append(url)
    return seen


def _git_head(path: Path) -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=path.parent,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    sha = out.stdout.strip()
    return sha if _SHA_RE.fullmatch(sha) else None


def raw_url(url: str) -> str:
    """Turn a github.com blob URL into its raw counterpart; leave others alone."""
    m = _GITHUB_BLOB_RE.match(url)
    if m:
        owner, repo, ref, path = m.groups()
        return f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{path}"
    return url


def fetch_text(source: str, timeout: float = 30.0) -> tuple[str, str | None]:
    """Return (text, commit) for a path or URL."""
    if source.startswith(("http://", "https://")):
        url = raw_url(source)
        resp = httpx.get(url, timeout=timeout, follow_redirects=True)
        resp.raise_for_status()
        m = _SHA_RE.search(url)
        return resp.text, (m.group(0) if m else None)
    path = Path(source)
    text = path.read_text(encoding="utf-8")
    return text, _git_head(path.resolve())


def ingest(source: str, commit: str | None = None) -> Doc:
    text, found_commit = fetch_text(source)
    return ingest_text(text, source=source, commit=commit or found_commit)


def ingest_text(text: str, source: str = "<text>", commit: str | None = None) -> Doc:
    lines = split_lines(text)
    return Doc(
        source=source,
        commit=commit,
        sha256=sha256_of(text),
        lines=lines,
        fences=find_fences(lines),
        urls=find_urls(normalize(text)),
    )

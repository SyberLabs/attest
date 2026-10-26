"""Baseline mode: gate on what changed, not on what was already failing.

A baseline file records each claim's verdict from one run. A later run is
compared with it: a claim that is stale or broken now and was not before (or did
not exist) is a new failure, and only new failures fail the gate. Claims are
keyed by what they are, not where they sit, so a line shift is not a change:

* a prompt example by its doc file, its frontmatter name and its occurrence
* any other claim by its doc file, kind, a hash of its command, objective or
  expected text, and its occurrence

The file is plain JSON (schema ``attest.baseline/v0``) meant to be committed
beside the docs it describes.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .models import Claim, Verdict

SCHEMA = "attest.baseline/v0"
FAILING = ("stale", "broken")
_NAME = re.compile(r"^name:\s*(.+?)\s*$", re.M)


def doc_name(source: str) -> str:
    """The doc's identity in a baseline: its file name, stable across checkouts."""
    return source.rstrip("/").replace("\\", "/").rsplit("/", 1)[-1]


def _identity(claim: Claim) -> tuple[str, str]:
    """What a claim is, as (identity, preview): the prompt name, or a short hash of its text."""
    if claim.kind == "prompt" and claim.command:
        m = _NAME.search(claim.command.split("\n---", 1)[0])
        if m:
            return m.group(1), m.group(1)
    text = claim.command or claim.objective or claim.expects or claim.span.text
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    preview = " ".join(text.split())[:60]
    return digest, preview


def claim_keys(source: str, claims: list[Claim]) -> dict[str, tuple[str, str]]:
    """Each claim id's baseline key and preview, in doc order."""
    name = doc_name(source)
    seen: Counter[str] = Counter()
    out: dict[str, tuple[str, str]] = {}
    for claim in claims:
        if claim.kind == "prose":
            continue
        ident, preview = _identity(claim)
        base = f"{name}::{claim.kind}::{ident}"
        seen[base] += 1
        out[claim.id] = (f"{base}#{seen[base]}", preview)
    return out


def entries_for(source: str, claims: list[Claim], verdicts: list[Verdict]) -> dict[str, dict]:
    by_id = {v.claim_id: v for v in verdicts}
    by_claim = {c.id: c for c in claims}
    out = {}
    for cid, (key, preview) in claim_keys(source, claims).items():
        v = by_id.get(cid)
        if v is None:
            continue
        out[key] = {"status": v.status, "line": by_claim[cid].span.start_line, "preview": preview}
    return out


@dataclass
class Change:
    key: str
    doc: str
    claim_id: str
    line: int
    status: str
    before: str | None  # the baseline's status, or None when the claim is new
    reason: str


@dataclass
class BaselineDiff:
    new_failures: list[Change] = field(default_factory=list)
    known_failures: list[Change] = field(default_factory=list)
    fixed: list[Change] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)  # baseline keys of covered docs with no claim now

    @property
    def gate_fails(self) -> bool:
        return bool(self.new_failures)


def load_baseline(path: Path) -> dict[str, dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema") != SCHEMA or not isinstance(data.get("entries"), dict):
        raise ValueError(f"{path} is not an {SCHEMA} file")
    return data["entries"]


def write_baseline(path: Path, results: list) -> Path:
    """Record the verdicts of ``results`` (ProveResults); docs already in the file and
    not in this run are kept, the docs in this run are replaced."""
    path = Path(path)
    entries: dict[str, dict] = {}
    if path.exists():
        entries = load_baseline(path)
    names = {doc_name(r.doc.source) for r in results}
    entries = {k: v for k, v in entries.items() if k.split("::", 1)[0] not in names}
    for r in results:
        entries.update(entries_for(r.doc.source, r.claims, r.verdicts))
    payload = {
        "schema": SCHEMA,
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "entries": dict(sorted(entries.items())),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    return path


def compare(results: list, baseline: dict[str, dict]) -> BaselineDiff:
    """Classify this run's stale and broken claims against the baseline."""
    diff = BaselineDiff()
    covered = {doc_name(r.doc.source) for r in results}
    seen_keys: set[str] = set()
    for r in results:
        name = doc_name(r.doc.source)
        by_id = {v.claim_id: v for v in r.verdicts}
        by_claim = {c.id: c for c in r.claims}
        for cid, (key, _preview) in claim_keys(r.doc.source, r.claims).items():
            seen_keys.add(key)
            v = by_id.get(cid)
            if v is None:
                continue
            before = baseline.get(key, {}).get("status")
            change = Change(key, name, cid, by_claim[cid].span.start_line, v.status, before, v.reason)
            if v.status in FAILING:
                (diff.known_failures if before in FAILING else diff.new_failures).append(change)
            elif before in FAILING and v.status == "proven":
                diff.fixed.append(change)
    diff.missing = sorted(k for k in baseline if k.split("::", 1)[0] in covered and k not in seen_keys)
    return diff

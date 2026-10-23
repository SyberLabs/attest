"""Loader for the hand-labelled verdict corpus in tests/fixtures/corpus.json."""

from __future__ import annotations

import json
from pathlib import Path

from attest.models import BrowserObservation, Claim, Evidence, Span

CORPUS = Path(__file__).resolve().parent / "fixtures" / "corpus.json"


def load_corpus() -> list[tuple[str, Claim, Evidence | None, str]]:
    """Yield (item id, claim, evidence, hand label)."""
    data = json.loads(CORPUS.read_text(encoding="utf-8"))
    out = []
    for i, item in enumerate(data["items"], start=1):
        cid = f"c{i:02d}"
        kind = item["kind"]
        if kind == "prose":
            claim = Claim(id=cid, kind="prose", span=Span(start_line=i, end_line=i, text=item["text"]))
            out.append((item["id"], claim, None, item["label"]))
            continue
        if kind == "browser":
            claim = Claim(
                id=cid,
                kind="browser",
                span=Span(start_line=i, end_line=i, text=f"See {item['expects']!r}"),
                objective=f"Check the page shows {item['expects']!r}",
                expects=item["expects"],
                store_as=item.get("store_as"),
            )
            ev = Evidence(claim_id=cid, kind="browser", observation=BrowserObservation(**item["observation"]))
            out.append((item["id"], claim, ev, item["label"]))
            continue
        claim = Claim(
            id=cid,
            kind="shell",
            span=Span(start_line=i, end_line=i, text=item["command"]),
            command=item["command"],
            expects=item.get("expects"),
        )
        ev = Evidence(
            claim_id=cid,
            kind="shell",
            command=item["command"],
            exit_code=item["exit_code"],
            timed_out=item["timed_out"],
            stdout_tail=item["stdout_tail"],
        )
        out.append((item["id"], claim, ev, item["label"]))
    return out

"""Append-only JSONL ledger of verdict rows.

One row per verdict, tagged with the turn it belongs to. The render page's
counters are a group-by over this file and nothing else. Within a turn, the
last row for a claim wins, so a looped claim that ends proven counts as proven.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .models import Claim, LedgerRow, Verdict

STATUSES = ("proven", "stale", "broken", "unverifiable", "skipped")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class Ledger:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    # -- writing -------------------------------------------------------------------

    def append(self, rows: list[LedgerRow]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            for r in rows:
                fh.write(r.model_dump_json() + "\n")

    def record_kane_runs(self, doc_sha256: str, turn: int, runs: list) -> None:
        """Append one accounting row for each Kane test-file run."""
        if not runs:
            return
        path = self.path.parent / "kane-runs.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        ts = now_iso()
        with path.open("a", encoding="utf-8") as fh:
            for run in runs:
                row = {
                    "turn": turn,
                    "doc_sha256": doc_sha256,
                    "authored": run.authored,
                    "replayed": run.replayed,
                    "credits": run.credits,
                    "exit_code": run.exit_code,
                    "timestamp": ts,
                }
                fh.write(json.dumps(row) + "\n")

    def record_new_kane_runs(self, doc_sha256: str, turn: int, runs: list, recorded: int = 0) -> int:
        """Record only runs added since the previous checkpoint and return the new count."""
        if len(runs) > recorded:
            self.record_kane_runs(doc_sha256, turn, runs[recorded:])
        return len(runs)

    def kane_rows(self) -> list[dict]:
        path = self.path.parent / "kane-runs.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def record(
        self,
        doc_sha256: str,
        turn: int,
        claims: list[Claim],
        verdicts: list[Verdict],
        looped: bool = False,
    ) -> list[LedgerRow]:
        kinds = {c.id: c.kind for c in claims}
        ts = now_iso()
        rows = [
            LedgerRow(
                turn=turn,
                doc_sha256=doc_sha256,
                claim_id=v.claim_id,
                kind=kinds.get(v.claim_id, "prose"),
                status=v.status,
                reason=v.reason,
                label_match=v.label_match,
                evidence_kind=v.evidence.kind if v.evidence else None,
                looped=looped,
                timestamp=ts,
            )
            for v in verdicts
        ]
        self.append(rows)
        return rows

    # -- reading -------------------------------------------------------------------

    def rows(self) -> list[LedgerRow]:
        if not self.path.exists():
            return []
        out: list[LedgerRow] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                out.append(LedgerRow.model_validate_json(line))
        return out

    def next_turn(self) -> int:
        rows = self.rows()
        return (max(r.turn for r in rows) + 1) if rows else 1

    def latest_turn(self, doc_sha256: str) -> int | None:
        turns = [r.turn for r in self.rows() if r.doc_sha256 == doc_sha256]
        return max(turns) if turns else None

    def summary(self, doc_sha256: str, turn: int | None = None) -> dict:
        """Counts for one doc and one turn (the latest by default)."""
        rows = [r for r in self.rows() if r.doc_sha256 == doc_sha256]
        if turn is None:
            turn = max((r.turn for r in rows), default=None)
        rows = [r for r in rows if r.turn == turn]
        final: dict[str, LedgerRow] = {}
        for r in rows:  # file order, so the last row per claim wins
            final[r.claim_id] = r
        badged = [r for r in final.values() if r.kind != "prose"]
        counts = Counter(r.status for r in badged)  # prose carries no badge, so it is not counted
        loops_closed = sum(1 for r in final.values() if r.looped and r.status == "proven")
        loops_attempted = len({r.claim_id for r in rows if r.looped})
        kane = [r for r in self.kane_rows() if r["doc_sha256"] == doc_sha256 and r["turn"] == turn]
        authored = sum(r["authored"] for r in kane)
        replayed = sum(r["replayed"] for r in kane)
        return {
            "doc_sha256": doc_sha256,
            "turn": turn,
            "claims": len(badged),
            **{s: counts.get(s, 0) for s in STATUSES},
            "loops_attempted": loops_attempted,
            "loops_closed": loops_closed,
            "rows": len(rows),
            "kane_steps": authored + replayed,
            "authored": authored,
            "replayed": replayed,
            "credits": sum(r["credits"] for r in kane),
        }

    def session_summary(self, day: str | None = None) -> dict:
        """Counts across every doc for one UTC day (today by default)."""
        day = day or now_iso()[:10]
        rows = [r for r in self.rows() if r.timestamp.startswith(day)]
        kane = [r for r in self.kane_rows() if r["timestamp"].startswith(day)]
        turns = sorted({(r.doc_sha256, r.turn) for r in rows})
        docs = {r.doc_sha256 for r in rows}
        loops_closed = 0
        for sha, turn in turns:
            loops_closed += self.summary(sha, turn)["loops_closed"]
        return {
            "day": day,
            "turns": len(turns),
            "docs": len(docs),
            "rows": len(rows),
            "loops_closed": loops_closed,
            "credits": sum(r["credits"] for r in kane),
            "authored": sum(r["authored"] for r in kane),
            "replayed": sum(r["replayed"] for r in kane),
        }

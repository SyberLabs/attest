"""SQLite-backed workflow state and immutable content-addressed artifacts."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class WorkflowStorageError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class WorkflowRecord:
    workflow_id: str
    state: str
    proposal: dict[str, Any]
    approval: dict[str, Any] | None = None
    validation: dict[str, Any] | None = None


_ALLOWED_TRANSITIONS = {
    "proposed": {"awaiting_approval", "rejected", "unresolved"},
    "awaiting_approval": {"validating", "rejected", "unresolved"},
    "validating": {"validated", "rejected", "unresolved"},
    "validated": {"promoted", "unresolved"},
    "rejected": set(),
    "unresolved": set(),
    "promoted": set(),
}


class SQLiteWorkflowStore:
    """Small local workflow store; artifacts are verified on both write and read."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("PRAGMA journal_mode = WAL")
        return db

    def _init(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS artifacts (
                    digest TEXT PRIMARY KEY,
                    content BLOB NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS workflows (
                    workflow_id TEXT PRIMARY KEY,
                    state TEXT NOT NULL,
                    proposal_json TEXT NOT NULL,
                    approval_json TEXT,
                    validation_json TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS workflow_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    workflow_id TEXT NOT NULL REFERENCES workflows(workflow_id),
                    from_state TEXT,
                    to_state TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )

    def put_artifact(self, content: bytes | str) -> str:
        data = content.encode("utf-8") if isinstance(content, str) else content
        digest = digest_bytes(data)
        with self._connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO artifacts(digest, content, created_at) VALUES (?, ?, ?)",
                (digest, data, _now()),
            )
            stored = db.execute("SELECT content FROM artifacts WHERE digest = ?", (digest,)).fetchone()
        if stored is None or bytes(stored["content"]) != data:
            raise WorkflowStorageError("content-addressed artifact collision")
        return digest

    def get_artifact(self, digest: str) -> bytes:
        with self._connect() as db:
            row = db.execute("SELECT content FROM artifacts WHERE digest = ?", (digest,)).fetchone()
        if row is None:
            raise WorkflowStorageError(f"missing artifact {digest}")
        content = bytes(row["content"])
        if digest_bytes(content) != digest:
            raise WorkflowStorageError(f"artifact digest mismatch for {digest}")
        return content

    def artifacts_containing(self, marker: bytes) -> list[tuple[str, bytes]]:
        """Every stored artifact whose content contains ``marker``, as (digest, content), each verified."""
        with self._connect() as db:
            rows = db.execute(
                "SELECT digest, content FROM artifacts WHERE instr(content, ?) > 0 ORDER BY digest", (marker,)
            ).fetchall()
        out = []
        for row in rows:
            content = bytes(row["content"])
            if digest_bytes(content) != row["digest"]:
                raise WorkflowStorageError(f"artifact digest mismatch for {row['digest']}")
            out.append((row["digest"], content))
        return out

    def validations(self) -> list[tuple[str, dict[str, Any]]]:
        """Every workflow's stored validation record, as (workflow id, record), workflows without one left out."""
        with self._connect() as db:
            rows = db.execute(
                "SELECT workflow_id, validation_json FROM workflows WHERE validation_json IS NOT NULL ORDER BY workflow_id"
            ).fetchall()
        return [(row["workflow_id"], json.loads(row["validation_json"])) for row in rows]

    def create(self, workflow_id: str, proposal: dict[str, Any]) -> WorkflowRecord:
        payload = canonical_json(proposal)
        with self._connect() as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                db.execute(
                    "INSERT INTO workflows(workflow_id, state, proposal_json, updated_at) VALUES (?, 'proposed', ?, ?)",
                    (workflow_id, payload, _now()),
                )
                db.execute(
                    "INSERT INTO workflow_events(workflow_id, from_state, to_state, payload_json, created_at) VALUES (?, NULL, 'proposed', '{}', ?)",
                    (workflow_id, _now()),
                )
                db.commit()
            except sqlite3.IntegrityError as exc:
                db.rollback()
                raise WorkflowStorageError(f"workflow {workflow_id} already exists") from exc
        return WorkflowRecord(workflow_id, "proposed", proposal)

    def get(self, workflow_id: str) -> WorkflowRecord:
        with self._connect() as db:
            row = db.execute("SELECT * FROM workflows WHERE workflow_id = ?", (workflow_id,)).fetchone()
        if row is None:
            raise WorkflowStorageError(f"unknown workflow {workflow_id}")
        return WorkflowRecord(
            workflow_id=row["workflow_id"],
            state=row["state"],
            proposal=json.loads(row["proposal_json"]),
            approval=json.loads(row["approval_json"]) if row["approval_json"] else None,
            validation=json.loads(row["validation_json"]) if row["validation_json"] else None,
        )

    def transition(
        self,
        workflow_id: str,
        expected_state: str,
        new_state: str,
        *,
        proposal: dict[str, Any] | None = None,
        approval: dict[str, Any] | None = None,
        validation: dict[str, Any] | None = None,
        event: dict[str, Any] | None = None,
    ) -> WorkflowRecord:
        with self._connect() as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute("SELECT * FROM workflows WHERE workflow_id = ?", (workflow_id,)).fetchone()
                if row is None:
                    raise WorkflowStorageError(f"unknown workflow {workflow_id}")
                if row["state"] != expected_state:
                    raise WorkflowStorageError(
                        f"workflow {workflow_id} is {row['state']}, expected {expected_state}"
                    )
                if new_state not in _ALLOWED_TRANSITIONS.get(expected_state, set()):
                    raise WorkflowStorageError(f"invalid workflow transition {expected_state} -> {new_state}")
                pjson = canonical_json(proposal) if proposal is not None else row["proposal_json"]
                ajson = canonical_json(approval) if approval is not None else row["approval_json"]
                vjson = canonical_json(validation) if validation is not None else row["validation_json"]
                db.execute(
                    "UPDATE workflows SET state = ?, proposal_json = ?, approval_json = ?, validation_json = ?, updated_at = ? WHERE workflow_id = ?",
                    (new_state, pjson, ajson, vjson, _now(), workflow_id),
                )
                db.execute(
                    "INSERT INTO workflow_events(workflow_id, from_state, to_state, payload_json, created_at) VALUES (?, ?, ?, ?, ?)",
                    (workflow_id, expected_state, new_state, canonical_json(event or {}), _now()),
                )
                db.commit()
            except Exception:
                db.rollback()
                raise
        return self.get(workflow_id)

    def events(self, workflow_id: str) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT from_state, to_state, payload_json, created_at FROM workflow_events WHERE workflow_id = ? ORDER BY event_id",
                (workflow_id,),
            ).fetchall()
        return [
            {
                "from_state": row["from_state"],
                "to_state": row["to_state"],
                "payload": json.loads(row["payload_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

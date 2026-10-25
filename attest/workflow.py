"""Durable approval, whole-document validation, and digest-checked promotion."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from .approvals import ApprovalError, verify_approval
from .compiler import compile_doc
from .ingest import ingest_text, sha256_of
from .models import Claim
from .policy import MaintainDocsPolicy
from .pipeline import execute
from .proposals import current_build_digest, proposal_digest
from .shell import new_scratch
from .storage import SQLiteWorkflowStore, WorkflowRecord, canonical_json
from .verdict import Rules, load_rules


class WorkflowError(RuntimeError):
    pass


def _unresolved(store: SQLiteWorkflowStore, record: WorkflowRecord, reason: str) -> WorkflowRecord:
    return store.transition(
        record.workflow_id,
        "validating",
        "unresolved",
        validation={"successful": False, "reason": reason},
        event={"reason": reason},
    )


def _claim_fingerprint(claim: Claim) -> str:
    payload = claim.model_dump(mode="json")
    payload["span"] = {"text": claim.span.text}  # line shifts do not change claim meaning
    return canonical_json(payload)


def _compile_complete(original: list[Claim], compiled: list[Claim], target_id: str) -> tuple[bool, str]:
    old_target = next((claim for claim in original if claim.id == target_id), None)
    new_target = next((claim for claim in compiled if claim.id == target_id), None)
    if old_target is None:
        return False, "approved target claim is missing from its baseline"
    if new_target is None:
        return False, "whole-document compilation did not produce the approved target claim"
    if new_target.kind != old_target.kind:
        return False, "whole-document compilation changed the target claim kind"
    old_other = Counter(_claim_fingerprint(c) for c in original if c.id != target_id)
    new_other = Counter(_claim_fingerprint(c) for c in compiled if c.id != target_id)
    if old_other != new_other:
        return False, "whole-document compilation omitted, added, or changed an unrelated claim"
    if len(compiled) < len(original):
        return False, "whole-document compilation is incomplete"
    return True, "complete"


def _validation_digest(payload: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def resume_workflow(
    store: SQLiteWorkflowStore,
    workflow_id: str,
    *,
    runner,
    apply: bool = False,
    simulation: bool = False,
    policy: MaintainDocsPolicy | None = None,
    rules: Rules | None = None,
    llm=None,
    shell_env: dict[str, str] | None = None,
) -> WorkflowRecord:
    """Validate a human-approved proposal with fresh Kane evidence, optionally promote."""
    record = store.get(workflow_id)
    policy = policy or MaintainDocsPolicy()
    if simulation or getattr(runner, "name", None) != "kane":
        raise WorkflowError("promotion validation requires fresh Kane evidence; simulation/fake evidence is not accepted")
    if record.state == "validated":
        verify_approval(record)
        if apply:
            return promote_workflow(store, workflow_id, policy=policy)
        return record
    if record.state != "validating":
        raise WorkflowError(f"workflow is {record.state}; only an approved workflow can be resumed")
    try:
        verify_approval(record)
    except ApprovalError as exc:
        return _unresolved(store, record, str(exc))

    proposal = record.proposal
    if proposal_digest(proposal) != proposal.get("proposal_digest"):
        return _unresolved(store, record, "proposal digest no longer matches its approved contents")
    if policy.digest != proposal.get("policy_digest"):
        return _unresolved(store, record, "Maintain Docs policy digest changed after approval")
    artifacts = proposal["artifact_digests"]
    try:
        base_text = store.get_artifact(artifacts["base"]).decode("utf-8")
        candidate_text = store.get_artifact(artifacts["candidate"]).decode("utf-8")
        original_claims = [Claim.model_validate(x) for x in json.loads(store.get_artifact(artifacts["baseline_claims"]))]
    except Exception as exc:
        return _unresolved(store, record, f"proposal artifact invalid or unavailable: {exc}")
    candidate_bytes = store.get_artifact(artifacts["candidate"])
    if (
        sha256_of(base_text) != proposal.get("base_digest")
        or hashlib.sha256(candidate_bytes).hexdigest() != proposal.get("candidate_digest")
    ):
        return _unresolved(store, record, "base or candidate artifact digest mismatch")
    source_path = Path(proposal["source_path"])
    if not source_path.is_file() or source_path.is_symlink():
        return _unresolved(store, record, "source file is missing or is a symlink")
    try:
        source_text = source_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        return _unresolved(store, record, f"cannot read source for digest check: {exc}")
    if (
        hashlib.sha256(source_path.read_bytes()).hexdigest() != proposal.get("source_digest")
        or sha256_of(source_text) != proposal.get("base_digest")
    ):
        return _unresolved(store, record, "source changed after approval; promotion refused")
    if current_build_digest(source_path, proposal["source_digest"]) != proposal.get("build_digest"):
        return _unresolved(store, record, "build identity changed after approval; promotion refused")

    doc = ingest_text(candidate_text, source=str(source_path))
    try:
        claims = compile_doc(doc, llm=llm, use_cache=False)
        complete, detail = _compile_complete(original_claims, claims, proposal["target_claim_id"])
        if not complete:
            return store.transition(
                workflow_id,
                "validating",
                "unresolved",
                validation={"successful": False, "reason": detail, "runner": runner.name},
                event={"reason": detail},
            )
        doc = doc.model_copy(update={"claims": claims})
        prepare = getattr(runner, "prepare", None)
        if prepare is not None:
            prepare(claims, str(source_path))
        scratch = new_scratch("attest-validate-")
        session = None
        try:
            evidence, verdicts, session = execute(
                claims,
                runner,
                rules or load_rules(),
                scratch,
                shell_env=shell_env,
            )
            statuses = {verdict.claim_id: verdict.status for verdict in verdicts}
            required = [claim for claim in claims if claim.kind in {"shell", "browser", "assert"}]
            failed = [f"{claim.id}:{statuses.get(claim.id, 'missing')}" for claim in required if statuses.get(claim.id) != "proven"]
            evidence_payload = {key: value.model_dump(mode="json") for key, value in evidence.items()}
            validation_data: dict[str, Any] = {
                "successful": not failed,
                "runner": runner.name,
                "candidate_digest": proposal["candidate_digest"],
                "statuses": statuses,
                "failed_claims": failed,
                "evidence": evidence_payload,
                "validated_claims": [claim.model_dump(mode="json") for claim in claims],
            }
            validation_data["evidence_digest"] = _validation_digest(evidence_payload)
            validation_data["receipt_digest"] = _validation_digest(validation_data)
            validation_data["receipt_artifact_digest"] = store.put_artifact(canonical_json(validation_data))
        finally:
            if session is not None:
                session.close()
            if scratch.exists():
                shutil.rmtree(scratch, ignore_errors=True)
        if not validation_data["successful"]:
            return store.transition(
                workflow_id,
                "validating",
                "rejected",
                validation=validation_data,
                event={"reason": "fresh validation failed", "failed_claims": validation_data["failed_claims"]},
            )
        record = store.transition(
            workflow_id,
            "validating",
            "validated",
            validation=validation_data,
            event={"runner": runner.name, "receipt_digest": validation_data["receipt_digest"]},
        )
    except Exception as exc:
        current = store.get(workflow_id)
        if current.state == "validating":
            return _unresolved(store, current, f"validation incomplete: {type(exc).__name__}: {exc}")
        raise
    if apply:
        return promote_workflow(store, workflow_id, policy=policy)
    return record


def promote_workflow(
    store: SQLiteWorkflowStore,
    workflow_id: str,
    *,
    policy: MaintainDocsPolicy | None = None,
) -> WorkflowRecord:
    record = store.get(workflow_id)
    policy = policy or MaintainDocsPolicy()
    if record.state != "validated" or not record.approval or not record.validation:
        raise WorkflowError("--apply is disabled unless an approved candidate has successful validation")
    verify_approval(record)
    if not record.validation.get("successful") or record.validation.get("runner") != "kane":
        raise WorkflowError("--apply is disabled after failed, incomplete, or simulated validation")
    proposal = record.proposal
    if policy.digest != proposal.get("policy_digest"):
        raise WorkflowError("policy changed after validation; --apply refused")
    path = Path(proposal["source_path"])
    if not path.is_file() or path.is_symlink():
        raise WorkflowError("source file is missing or is a symlink")
    original = path.read_bytes()
    try:
        current_text = original.decode("utf-8")
    except UnicodeError as exc:
        raise WorkflowError("source is not UTF-8; --apply refused") from exc
    if (
        hashlib.sha256(original).hexdigest() != proposal.get("source_digest")
        or sha256_of(current_text) != proposal.get("base_digest")
    ):
        raise WorkflowError("source changed after approval; --apply refused")
    if current_build_digest(path, proposal["source_digest"]) != proposal.get("build_digest"):
        raise WorkflowError("build identity changed after approval; --apply refused")
    candidate = store.get_artifact(proposal["artifact_digests"]["candidate"])
    if hashlib.sha256(candidate).hexdigest() != proposal.get("candidate_digest"):
        raise WorkflowError("candidate digest mismatch; --apply refused")
    mode = stat.S_IMODE(path.stat().st_mode)
    fd, staged_name = tempfile.mkstemp(prefix=f".{path.name}.attest-", dir=path.parent)
    staged = Path(staged_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(candidate)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(staged, mode)
        # Recheck immediately before the atomic replacement.
        current = path.read_bytes()
        if hashlib.sha256(current).hexdigest() != proposal["source_digest"]:
            raise WorkflowError("source changed during promotion; --apply refused")
        os.replace(staged, path)
    finally:
        staged.unlink(missing_ok=True)
    try:
        return store.transition(
            workflow_id,
            "validated",
            "promoted",
            event={"source_digest": proposal["source_digest"], "candidate_digest": proposal["candidate_digest"]},
        )
    except Exception:
        # Never leave an unrecorded promotion if the durable state write fails.
        if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == proposal["candidate_digest"]:
            fd, rollback_name = tempfile.mkstemp(prefix=f".{path.name}.attest-rollback-", dir=path.parent)
            with os.fdopen(fd, "wb") as stream:
                stream.write(original)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(rollback_name, mode)
            os.replace(rollback_name, path)
        raise

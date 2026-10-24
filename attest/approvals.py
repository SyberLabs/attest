"""Human approvals bind every identity that may affect a repair."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from .proposals import proposal_digest
from .storage import SQLiteWorkflowStore, WorkflowRecord, canonical_json


class ApprovalError(RuntimeError):
    pass


def approval_bindings(proposal: dict[str, Any]) -> dict[str, str]:
    return {
        "proposal_digest": proposal_digest(proposal),
        "plan_digest": str(proposal["plan_digest"]),
        "source_digest": str(proposal["source_digest"]),
        "build_digest": str(proposal["build_digest"]),
        "policy_digest": str(proposal["policy_digest"]),
        "base_digest": str(proposal["base_digest"]),
        "candidate_digest": str(proposal["candidate_digest"]),
    }


def approve_workflow(store: SQLiteWorkflowStore, workflow_id: str, actor: str) -> WorkflowRecord:
    record = store.get(workflow_id)
    if record.state != "awaiting_approval":
        raise ApprovalError(f"workflow must be awaiting_approval, not {record.state}")
    if not actor.strip():
        raise ApprovalError("a human approver identity is required")
    authority = record.proposal.get("authority") or {}
    if not authority.get("allowed"):
        raise ApprovalError("authority policy did not authorize this proposal")
    if proposal_digest(record.proposal) != record.proposal.get("proposal_digest"):
        raise ApprovalError("proposal digest changed; approval refused")
    bindings = approval_bindings(record.proposal)
    approval: dict[str, Any] = {
        **bindings,
        "actor": actor.strip(),
        "approved_at": datetime.now(timezone.utc).isoformat(),
    }
    approval["approval_digest"] = hashlib.sha256(canonical_json(approval).encode("utf-8")).hexdigest()
    return store.transition(
        workflow_id,
        "awaiting_approval",
        "validating",
        approval=approval,
        event={"actor": actor.strip(), "approval_digest": approval["approval_digest"]},
    )


def verify_approval(record: WorkflowRecord) -> None:
    if record.state not in {"validating", "validated"} or not record.approval:
        raise ApprovalError("a recorded human approval is required before validation")
    expected = approval_bindings(record.proposal)
    actual = {key: record.approval.get(key) for key in expected}
    if actual != expected:
        raise ApprovalError("approval is not bound to the current proposal, plan, source, build, and policy")
    unsigned = {key: value for key, value in record.approval.items() if key != "approval_digest"}
    digest = hashlib.sha256(canonical_json(unsigned).encode("utf-8")).hexdigest()
    if record.approval.get("approval_digest") != digest:
        raise ApprovalError("approval digest is invalid")

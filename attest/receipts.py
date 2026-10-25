"""Run receipts and fail-closed evidence validation."""

from __future__ import annotations

from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .contracts import (
    AuthorityState,
    ComparisonState,
    EvidenceState,
    ExecutionState,
    RepairState,
    SHA256_PATTERN,
)


class ReceiptEvidence(BaseModel):
    """Evidence bound to the exact source version and content that was run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_id: UUID = Field(default_factory=uuid4)
    kind: Literal["shell", "browser"]
    source_version_id: UUID
    content_sha256: str = Field(pattern=SHA256_PATTERN)
    complete: bool = False
    simulated: bool = False


class RunReceipt(BaseModel):
    """A run record with independent execution, comparison, authority and repair states."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    receipt_id: UUID = Field(default_factory=uuid4)
    snapshot_id: UUID
    source_version_id: UUID
    content_sha256: str = Field(pattern=SHA256_PATTERN)
    execution_state: ExecutionState
    comparison_state: ComparisonState
    authority_state: AuthorityState
    evidence_state: EvidenceState
    repair_state: RepairState
    evidence: tuple[ReceiptEvidence, ...] = ()

    @model_validator(mode="after")
    def _evidence_is_consistent(self) -> "RunReceipt":
        validate_receipt_evidence(
            evidence_state=self.evidence_state,
            evidence=self.evidence,
            source_version_id=self.source_version_id,
            content_sha256=self.content_sha256,
        )
        return self


class VerifiedRunReceipt(RunReceipt):
    """A receipt that can only be created from complete, non-simulated proof."""

    @model_validator(mode="after")
    def _verified_invariants(self) -> "VerifiedRunReceipt":
        validate_verified_receipt(self)
        return self


def validate_receipt_evidence(
    *,
    evidence_state: EvidenceState,
    evidence: tuple[ReceiptEvidence, ...],
    source_version_id: UUID,
    content_sha256: str,
) -> None:
    """Check receipt evidence shape and bind each record to the receipt content."""
    for item in evidence:
        if item.source_version_id != source_version_id:
            raise ValueError("receipt evidence belongs to a different source version")
        if item.content_sha256 != content_sha256:
            raise ValueError("receipt evidence belongs to different content")

    if evidence_state == EvidenceState.MISSING:
        if evidence:
            raise ValueError("missing evidence state must not carry evidence")
    elif evidence_state == EvidenceState.INCOMPLETE:
        if not evidence or all(item.complete for item in evidence):
            raise ValueError("incomplete evidence state requires incomplete evidence")
    elif evidence_state == EvidenceState.SIMULATED:
        if not evidence or not any(item.simulated for item in evidence):
            raise ValueError("simulated evidence state requires simulated evidence")
    elif evidence_state == EvidenceState.COMPLETE:
        if not evidence:
            raise ValueError("complete evidence state requires evidence")
        if any(item.simulated for item in evidence):
            raise ValueError("simulated evidence cannot be marked complete")
        if any(not item.complete for item in evidence):
            raise ValueError("complete evidence state requires every evidence record to be complete")


def validate_verified_receipt(receipt: RunReceipt) -> None:
    """Raise unless the receipt contains complete fresh evidence for an allowed run."""
    if receipt.execution_state != ExecutionState.SUCCEEDED:
        raise ValueError("verified receipts require successful, non-simulated execution")
    if receipt.comparison_state != ComparisonState.MATCH:
        raise ValueError("verified receipts require a complete matching comparison")
    if receipt.authority_state != AuthorityState.ALLOWED:
        raise ValueError("verified receipts require an explicit allowed authority decision")
    if receipt.evidence_state != EvidenceState.COMPLETE:
        raise ValueError("verified receipts require complete non-simulated evidence")
    if receipt.repair_state not in {RepairState.NOT_REQUIRED, RepairState.VALIDATED}:
        raise ValueError("verified receipts require no repair or a validated repair")
    validate_receipt_evidence(
        evidence_state=receipt.evidence_state,
        evidence=receipt.evidence,
        source_version_id=receipt.source_version_id,
        content_sha256=receipt.content_sha256,
    )

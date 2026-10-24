"""Typed identity and workflow contracts for documentation repair."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

SHA256_PATTERN = r"^[0-9a-f]{64}$"


class ExecutionState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SIMULATED = "simulated"


class ComparisonState(StrEnum):
    PENDING = "pending"
    MATCH = "match"
    MISMATCH = "mismatch"
    INCOMPLETE = "incomplete"


class AuthorityState(StrEnum):
    PENDING = "pending"
    ALLOWED = "allowed"
    DENIED = "denied"
    AMBIGUOUS = "ambiguous"


class EvidenceState(StrEnum):
    MISSING = "missing"
    INCOMPLETE = "incomplete"
    SIMULATED = "simulated"
    COMPLETE = "complete"


class RepairState(StrEnum):
    NOT_REQUIRED = "not_required"
    PROPOSED = "proposed"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    APPLIED = "applied"
    VALIDATED = "validated"
    REJECTED = "rejected"
    FAILED = "failed"


class ClaimVersion(BaseModel):
    """One immutable text version for a stable claim identity."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version_id: UUID = Field(default_factory=uuid4)
    claim_id: str = Field(pattern=r"^c\d{2,}$")
    source_version_id: UUID
    text: str = Field(min_length=1)
    content_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def _content_digest_matches(self) -> "ClaimVersion":
        actual = hashlib.sha256(self.text.encode("utf-8")).hexdigest()
        if self.content_sha256 != actual:
            raise ValueError("content_sha256 must identify this exact claim text")
        return self


class AuthorityDecision(BaseModel):
    """Policy decision about whether a specific claim version may change."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    decision_id: UUID = Field(default_factory=uuid4)
    claim_version_id: UUID
    policy_id: str = Field(min_length=1)
    state: AuthorityState
    rationale: str = Field(min_length=1)


class RepairProposal(BaseModel):
    """A proposed content change, identified separately from both content digests."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    proposal_id: UUID = Field(default_factory=uuid4)
    snapshot_id: UUID
    source_version_id: UUID
    base_content_sha256: str = Field(pattern=SHA256_PATTERN)
    proposed_content_sha256: str = Field(pattern=SHA256_PATTERN)
    patch: str = Field(min_length=1)
    affected_claim_ids: tuple[str, ...] = ()
    state: RepairState = RepairState.PROPOSED

    @model_validator(mode="after")
    def _proposal_changes_content(self) -> "RepairProposal":
        if self.base_content_sha256 == self.proposed_content_sha256:
            raise ValueError("a repair proposal must identify content different from its base")
        return self


class Approval(BaseModel):
    """Human decision for one repair proposal."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    approval_id: UUID = Field(default_factory=uuid4)
    proposal_id: UUID
    approved: bool
    approver: str = Field(min_length=1)
    rationale: str | None = None
    decided_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

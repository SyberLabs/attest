import hashlib
from uuid import uuid4

import pytest
from pydantic import ValidationError

from attest.models import (
    Approval,
    AuthorityDecision,
    AuthorityState,
    ClaimVersion,
    ComparisonState,
    EvidenceState,
    ExecutionState,
    RepairProposal,
    RepairState,
    RepositorySnapshot,
    RunReceipt,
    SourceVersion,
    VerifiedRunReceipt,
)
from attest.receipts import ReceiptEvidence


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_snapshot_and_repair_ids_are_separate_from_content_digests():
    base = SourceVersion.from_content("README.md", "before\n", commit="abc123")
    patched = SourceVersion.from_content("README.md", "after\n", commit="abc123")
    snapshot = RepositorySnapshot(
        repository="syberlabs/attest",
        commit="abc123",
        source_versions=(base,),
    )
    proposal = RepairProposal(
        snapshot_id=snapshot.snapshot_id,
        source_version_id=base.version_id,
        base_content_sha256=base.content_sha256,
        proposed_content_sha256=patched.content_sha256,
        patch="-before\n+after\n",
        affected_claim_ids=("c01",),
    )

    assert base.content_sha256 != patched.content_sha256
    assert str(snapshot.snapshot_id) != base.content_sha256
    assert str(proposal.proposal_id) != patched.content_sha256
    assert proposal.snapshot_id == snapshot.snapshot_id
    assert proposal.source_version_id == base.version_id


def test_claim_id_is_stable_while_claim_versions_and_digests_change():
    source_id = uuid4()
    before = ClaimVersion(
        claim_id="c01",
        source_version_id=source_id,
        text="Run the old command",
        content_sha256=digest("Run the old command"),
    )
    after = ClaimVersion(
        claim_id="c01",
        source_version_id=source_id,
        text="Run the new command",
        content_sha256=digest("Run the new command"),
    )

    assert before.claim_id == after.claim_id
    assert before.version_id != after.version_id
    assert before.content_sha256 != after.content_sha256
    with pytest.raises(ValidationError, match="exact claim text"):
        ClaimVersion(
            claim_id="c01",
            source_version_id=source_id,
            text="tampered text",
            content_sha256=digest("original text"),
        )


def test_authority_and_approval_are_typed_separately_from_repair_state():
    source = SourceVersion.from_content("README.md", "claim\n")
    claim = ClaimVersion(
        claim_id="c01",
        source_version_id=source.version_id,
        text="claim",
        content_sha256=digest("claim"),
    )
    decision = AuthorityDecision(
        claim_version_id=claim.version_id,
        policy_id="maintain-docs",
        state=AuthorityState.ALLOWED,
        rationale="Approved behavior permits this description change.",
    )
    approval = Approval(
        proposal_id=uuid4(),
        approved=True,
        approver="maintainer",
        rationale="Reviewed the exact patch.",
    )

    assert decision.state is AuthorityState.ALLOWED
    assert approval.approved
    assert RepairState.APPROVED is not AuthorityState.ALLOWED


def test_verified_receipt_requires_complete_fresh_evidence():
    source_version_id = uuid4()
    content_sha256 = digest("document")
    build_digest = digest("build")
    evidence = ReceiptEvidence(
        kind="browser",
        source_version_id=source_version_id,
        content_sha256=content_sha256,
        complete=True,
        build_digest=build_digest,
        kane_run_id="kane-session-1",
    )

    receipt = VerifiedRunReceipt(
        snapshot_id=uuid4(),
        source_version_id=source_version_id,
        content_sha256=content_sha256,
        execution_state=ExecutionState.SUCCEEDED,
        comparison_state=ComparisonState.MATCH,
        authority_state=AuthorityState.ALLOWED,
        evidence_state=EvidenceState.COMPLETE,
        repair_state=RepairState.VALIDATED,
        evidence=(evidence,),
        build_digest=build_digest,
        kane_run_ids=("kane-session-1",),
    )

    assert receipt.evidence_state is EvidenceState.COMPLETE


def test_simulated_evidence_cannot_mint_a_verified_receipt():
    source_version_id = uuid4()
    content_sha256 = digest("document")
    simulated = ReceiptEvidence(
        kind="browser",
        source_version_id=source_version_id,
        content_sha256=content_sha256,
        complete=True,
        simulated=True,
    )
    data = {
        "snapshot_id": uuid4(),
        "source_version_id": source_version_id,
        "content_sha256": content_sha256,
        "execution_state": ExecutionState.SUCCEEDED,
        "comparison_state": ComparisonState.MATCH,
        "authority_state": AuthorityState.ALLOWED,
        "evidence_state": EvidenceState.SIMULATED,
        "repair_state": RepairState.NOT_REQUIRED,
        "evidence": (simulated,),
    }

    assert isinstance(RunReceipt(**data), RunReceipt)
    with pytest.raises(ValidationError, match="complete non-simulated evidence"):
        VerifiedRunReceipt(**data)


def test_incomplete_evidence_cannot_mint_a_verified_receipt():
    source_version_id = uuid4()
    content_sha256 = digest("document")
    incomplete = ReceiptEvidence(
        kind="shell",
        source_version_id=source_version_id,
        content_sha256=content_sha256,
    )

    with pytest.raises(ValidationError, match="complete non-simulated evidence"):
        VerifiedRunReceipt(
            snapshot_id=uuid4(),
            source_version_id=source_version_id,
            content_sha256=content_sha256,
            execution_state=ExecutionState.SUCCEEDED,
            comparison_state=ComparisonState.MATCH,
            authority_state=AuthorityState.ALLOWED,
            evidence_state=EvidenceState.INCOMPLETE,
            repair_state=RepairState.NOT_REQUIRED,
            evidence=(incomplete,),
        )


def test_receipt_evidence_must_match_the_receipt_content_version():
    source_version_id = uuid4()
    evidence = ReceiptEvidence(
        kind="shell",
        source_version_id=uuid4(),
        content_sha256=digest("document"),
        complete=True,
    )

    with pytest.raises(ValidationError, match="different source version"):
        RunReceipt(
            snapshot_id=uuid4(),
            source_version_id=source_version_id,
            content_sha256=digest("document"),
            execution_state=ExecutionState.SUCCEEDED,
            comparison_state=ComparisonState.MATCH,
            authority_state=AuthorityState.ALLOWED,
            evidence_state=EvidenceState.COMPLETE,
            repair_state=RepairState.NOT_REQUIRED,
            evidence=(evidence,),
        )

"""G1 Stage-0 exit: receipt validator branches not asserted elsewhere.

Each case targets one fail-closed branch in ``attest/receipts.py``. These are
negative checks on existing code, not new product behavior.
"""

import hashlib
from uuid import uuid4

import pytest
from pydantic import ValidationError

from attest.models import (
    AuthorityState,
    ComparisonState,
    EvidenceState,
    ExecutionState,
    RepairState,
    RunReceipt,
    VerifiedRunReceipt,
)
from attest.receipts import ReceiptEvidence


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def receipt_fields(**overrides):
    fields = {
        "snapshot_id": uuid4(),
        "source_version_id": uuid4(),
        "content_sha256": digest("document"),
        "execution_state": ExecutionState.SUCCEEDED,
        "comparison_state": ComparisonState.MATCH,
        "authority_state": AuthorityState.ALLOWED,
        "evidence_state": EvidenceState.COMPLETE,
        "repair_state": RepairState.NOT_REQUIRED,
    }
    fields.update(overrides)
    return fields


def evidence_for(fields, **overrides):
    values = {
        "kind": "browser",
        "source_version_id": fields["source_version_id"],
        "content_sha256": fields["content_sha256"],
        "complete": True,
    }
    values.update(overrides)
    return ReceiptEvidence(**values)


def test_evidence_for_other_content_cannot_mint_a_verified_receipt():
    fields = receipt_fields()
    fields["evidence"] = (evidence_for(fields, content_sha256=digest("patched document")),)
    with pytest.raises(ValidationError, match="different content"):
        VerifiedRunReceipt(**fields)


@pytest.mark.parametrize("evidence_state", [EvidenceState.MISSING, EvidenceState.COMPLETE])
def test_omitted_evidence_cannot_mint_a_verified_receipt(evidence_state):
    fields = receipt_fields(evidence_state=evidence_state)
    with pytest.raises(ValidationError, match="verified receipts require complete|requires evidence"):
        VerifiedRunReceipt(**fields)


def test_simulated_evidence_cannot_be_labeled_complete():
    fields = receipt_fields()
    fields["evidence"] = (evidence_for(fields, simulated=True),)
    with pytest.raises(ValidationError, match="simulated evidence cannot be marked complete"):
        RunReceipt(**fields)


@pytest.mark.parametrize(
    "override",
    [
        {"execution_state": ExecutionState.SIMULATED},
        {"execution_state": ExecutionState.FAILED},
        {"comparison_state": ComparisonState.INCOMPLETE},
        {"authority_state": AuthorityState.PENDING},
        {"repair_state": RepairState.PROPOSED},
    ],
    ids=["simulated-run", "failed-run", "incomplete-comparison", "pending-authority", "unvalidated-repair"],
)
def test_complete_evidence_alone_does_not_verify_a_non_success_run(override):
    fields = receipt_fields(**override)
    fields["evidence"] = (evidence_for(fields),)
    assert isinstance(RunReceipt(**fields), RunReceipt)
    with pytest.raises(ValidationError, match="verified receipts require"):
        VerifiedRunReceipt(**fields)

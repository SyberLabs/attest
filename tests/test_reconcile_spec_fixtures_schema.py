"""Validate the Reconcile Spec fixture contract without importing its policy engine."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "reconcile_spec"

ROOT_KEYS = {"schema_version", "fixture_id", "title", "scenario", "expected_mode", "claims", "expected"}
CLAIM_KEYS = {"claim_id", "claim_kind", "text", "protected", "descriptive", "evidence_pointers"}
EXPECTED_KEYS = {"diagnosis_side", "repair", "promotion_allowed", "rationale"}
EXPECTED_FIXTURES = {
    "protected_requirement_vs_observed_app": ("reconcile_spec", "ambiguous", "handoff"),
    "descriptive_doc_stale_not_protected": ("maintain_docs", "not_applicable", "handoff"),
    "promise_weakening_attempt": ("reconcile_spec", "not_applicable", "refused"),
}


def _non_empty_string(value):
    return isinstance(value, str) and bool(value.strip())


def _validate_pack(path):
    pack = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(pack, dict), f"{path.name}: pack must be an object"
    assert set(pack) == ROOT_KEYS, f"{path.name}: unexpected or missing pack fields"
    assert type(pack["schema_version"]) is int and pack["schema_version"] == 1
    assert pack["fixture_id"] == path.stem
    for field in ("title", "scenario"):
        assert _non_empty_string(pack[field]), f"{path.name}: {field} must be non-empty"
    assert pack["expected_mode"] in {"reconcile_spec", "maintain_docs"}

    claims = pack["claims"]
    assert isinstance(claims, list) and claims, f"{path.name}: claims must be a non-empty array"
    claim_ids = set()
    for claim in claims:
        assert isinstance(claim, dict) and set(claim) == CLAIM_KEYS
        assert _non_empty_string(claim["claim_id"])
        assert claim["claim_id"] not in claim_ids, f"{path.name}: duplicate claim id"
        claim_ids.add(claim["claim_id"])
        assert claim["claim_kind"] in {
            "protected_requirement", "descriptive_claim", "observed_behavior", "proposed_change"
        }
        assert _non_empty_string(claim["text"])
        assert type(claim["protected"]) is bool
        assert type(claim["descriptive"]) is bool
        assert not (claim["protected"] and claim["descriptive"]), "a claim cannot be both protected and descriptive"
        if claim["claim_kind"] == "protected_requirement":
            assert claim["protected"] and not claim["descriptive"]
        elif claim["claim_kind"] == "descriptive_claim":
            assert claim["descriptive"] and not claim["protected"]
        else:
            assert not claim["protected"] and not claim["descriptive"]
        pointers = claim["evidence_pointers"]
        assert isinstance(pointers, list) and pointers, f"{path.name}: every claim needs fake evidence pointers"
        assert all(_non_empty_string(pointer) and pointer.startswith("fake://") for pointer in pointers)

    expected = pack["expected"]
    assert isinstance(expected, dict) and set(expected) == EXPECTED_KEYS
    assert expected["diagnosis_side"] in {"ambiguous", "app_wrong", "doc_wrong", "not_applicable"}
    assert expected["repair"] in {"handoff", "refused"}
    assert type(expected["promotion_allowed"]) is bool
    assert _non_empty_string(expected["rationale"])
    return pack


def test_reconcile_spec_fixture_json_contract_and_expected_annotations():
    paths = sorted(FIXTURES.glob("*.json"))
    assert {path.stem for path in paths} == set(EXPECTED_FIXTURES)

    for path in paths:
        pack = _validate_pack(path)
        mode, side, repair = EXPECTED_FIXTURES[path.stem]
        assert (pack["expected_mode"], pack["expected"]["diagnosis_side"], pack["expected"]["repair"]) == (
            mode, side, repair
        )
        assert pack["expected"]["promotion_allowed"] is False

    weakening = _validate_pack(FIXTURES / "promise_weakening_attempt.json")
    assert any(claim["claim_kind"] == "proposed_change" for claim in weakening["claims"])
    assert weakening["expected"]["repair"] == "refused"
    assert weakening["expected"]["promotion_allowed"] is False


def test_bridge_receipt_fixture_matches_attest_receipt_v0_contract():
    path = ROOT / "tests" / "fixtures" / "syberwork_bridge" / "attest.receipt.v0.example.json"
    receipt = json.loads(path.read_text(encoding="utf-8"))

    required_keys = {
        "schema", "case_id", "created_at", "doc", "claim", "authority", "proposal",
        "approval", "evidence", "handoff", "disposition", "verified",
    }
    assert set(receipt) == required_keys | {"ticket", "app"}
    assert receipt["schema"] == "attest.receipt/v0"
    assert receipt["case_id"] and receipt["created_at"]
    assert set(receipt["doc"]) == {"source", "sha256_before", "sha256_after"}
    assert set(receipt["claim"]) == {
        "id", "kind", "lines", "text", "expects_before", "expects_after"
    }
    assert set(receipt["authority"]) == {"policy", "decision", "rule"}
    assert receipt["authority"]["decision"] == "implementation_required"
    assert set(receipt["proposal"]) == {"agent", "patch_sha256", "patch"}
    assert set(receipt["approval"]) == {"actor", "proposal_sha256"}
    assert receipt["approval"]["proposal_sha256"] == receipt["proposal"]["patch_sha256"]
    assert set(receipt["evidence"]) == {"before", "after"}
    for evidence in receipt["evidence"].values():
        assert set(evidence) == {"status", "observed", "note", "screenshot"}
    assert receipt["evidence"]["after"]["status"] == "proven"
    assert receipt["handoff"] is None
    assert receipt["disposition"] == "implemented"
    assert receipt["verified"] is False
    assert set(receipt["ticket"]) == {"path", "title"}
    assert set(receipt["app"]) == {"original", "candidate", "patch_sha256"}
    assert receipt["app"]["patch_sha256"] == receipt["proposal"]["patch_sha256"]

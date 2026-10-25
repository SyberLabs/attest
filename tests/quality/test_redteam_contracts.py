"""Focused N5 red-team checks against the Attest contracts that exist today.

These tests are intentionally narrow. A strict xfail is an observed defect,
not a green security result; remove it only when simulation is structurally
unable to produce verified evidence.
"""

import pytest
from pydantic import ValidationError

from attest.models import BrowserObservation, Claim, Evidence, Span, Verdict
from attest.runner import FakeBrowserRunner
from attest.verdict import judge


def _browser_claim(claim_id: str = "c01") -> Claim:
    return Claim(
        id=claim_id,
        kind="browser",
        span=Span(start_line=1, end_line=1, text="See the dashboard"),
        objective="Open the page and verify the Dashboard label",
        expects="Dashboard",
        store_as=f"{claim_id}_label",
    )


def test_proven_verdict_without_evidence_is_rejected():
    """A proven verdict without evidence is rejected."""
    with pytest.raises(ValidationError):
        Verdict(claim_id="c01", status="proven", reason="red-team: omitted evidence")


def test_evidence_for_another_claim_cannot_prove_this_claim():
    """Swapping claim identity in otherwise valid evidence is rejected."""
    swapped = Evidence(
        claim_id="c99",
        kind="browser",
        observation=BrowserObservation(status="passed", observed={"c99_label": "Dashboard"}),
    )
    with pytest.raises(ValidationError):
        Verdict(claim_id="c01", status="proven", reason="red-team: swapped claim evidence", evidence=swapped)


def test_browser_claim_with_shell_evidence_is_not_proven():
    """Evidence from the wrong execution boundary cannot satisfy a browser claim."""
    claim = _browser_claim()
    shell_observation = Evidence(claim_id=claim.id, kind="shell", exit_code=0, stdout_tail="Dashboard")

    result = judge(claim, shell_observation)

    assert result.status == "unverifiable"


def test_simulation_cannot_mint_a_proven_browser_verdict():
    """Simulated observations are barred from verification (fail-closed sim gate)."""
    claim = _browser_claim()
    observation = FakeBrowserRunner().observe(claim, base_url="https://example.invalid")
    evidence = Evidence(claim_id=claim.id, kind="browser", observation=observation)

    result = judge(claim, evidence)

    assert result.status != "proven"


def test_malformed_evidence_with_unknown_field_is_rejected():
    """Strict Pydantic schema validation rejects attacker-added evidence fields."""
    with pytest.raises(ValidationError):
        Evidence.model_validate(
            {
                "claim_id": "c01",
                "kind": "browser",
                "observation": {"status": "passed", "observed": {}, "trusted": True},
            }
        )


def test_reconcile_spec_refuses_protected_requirement_weakening():
    """Replace this skip with a policy-boundary assertion when Reconcile Spec exists."""
    pytest.skip(
        "No Reconcile Spec module or authority policy exists in this checkout; "
        "implement and run this refusal test before that component's release."
    )

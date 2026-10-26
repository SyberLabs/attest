from uuid import uuid4

import pytest
from pydantic import ValidationError

from attest.models import (
    Brief,
    BrowserObservation,
    Claim,
    Doc,
    Evidence,
    Span,
    Verdict,
)
from attest.runner import SimulationBrowserObservation


def span(text="echo hi", start=1, end=1):
    return Span(start_line=start, end_line=end, text=text)


def test_span_rejects_reversed_range():
    with pytest.raises(ValidationError):
        Span(start_line=5, end_line=2, text="x")


def test_span_rejects_zero_line():
    with pytest.raises(ValidationError):
        Span(start_line=0, end_line=1, text="x")


def test_claim_without_span_is_rejected():
    with pytest.raises(ValidationError):
        Claim.model_validate({"id": "c01", "kind": "shell", "command": "echo hi"})


def test_claim_null_span_is_rejected():
    with pytest.raises(ValidationError):
        Claim.model_validate({"id": "c01", "kind": "shell", "command": "echo hi", "span": None})


def test_shell_claim_needs_command():
    with pytest.raises(ValidationError):
        Claim(id="c01", kind="shell", span=span())


def test_browser_claim_needs_objective():
    with pytest.raises(ValidationError):
        Claim(id="c01", kind="browser", span=span())


def test_prose_claim_carries_nothing():
    with pytest.raises(ValidationError):
        Claim(id="c01", kind="prose", span=span(), expects="Hello")
    Claim(id="c01", kind="prose", span=span())


def test_claim_id_shape():
    with pytest.raises(ValidationError):
        Claim(id="1", kind="shell", span=span(), command="ls")
    Claim(id="c01", kind="shell", span=span(), command="ls")
    Claim(id="c123", kind="shell", span=span(), command="ls")


def test_verdict_without_evidence_is_rejected_unless_unverifiable():
    for status in ("proven", "stale", "broken", "skipped"):
        with pytest.raises(ValidationError):
            Verdict(claim_id="c01", status=status, reason="r")
    Verdict(claim_id="c01", status="unverifiable", reason="prose")


def test_verdict_evidence_must_match_claim():
    ev = Evidence(claim_id="c02", kind="shell", exit_code=0)
    with pytest.raises(ValidationError):
        Verdict(claim_id="c01", status="proven", reason="r", evidence=ev)
    # skipped may point at the dependency's evidence
    Verdict(claim_id="c01", status="skipped", reason="dep broken", evidence=ev)


def test_shell_evidence_shape():
    with pytest.raises(ValidationError):
        Evidence(claim_id="c01", kind="shell")
    Evidence(claim_id="c01", kind="shell", timed_out=True)
    with pytest.raises(ValidationError):
        Evidence(
            claim_id="c01",
            kind="shell",
            exit_code=0,
            observation=BrowserObservation(status="passed"),
        )


def test_browser_evidence_needs_observation():
    with pytest.raises(ValidationError):
        Evidence(claim_id="c01", kind="browser")
    ev = Evidence(
        claim_id="c01",
        kind="browser",
        observation=BrowserObservation(status="passed", observed={"c01_label": "Create"}, screenshot="s.png"),
    )
    assert ev.observed == {"c01_label": "Create"}
    assert ev.screenshot == "s.png"


def test_label_match_bounds():
    ev = Evidence(claim_id="c01", kind="shell", exit_code=0)
    with pytest.raises(ValidationError):
        Verdict(claim_id="c01", status="proven", reason="r", evidence=ev, label_match=1.5)


def test_doc_numbered():
    d = Doc(source="x", sha256="0" * 64, lines=["a", "b", "c"])
    assert d.numbered(2, 3) == "2  b\n3  c"


def test_brief_roundtrip():
    ev = Evidence(claim_id="c01", kind="shell", exit_code=1, stdout_tail="boom")
    v = Verdict(claim_id="c01", status="broken", reason="broken: exit 1", evidence=ev)
    b = Brief(claim_id="c01", verdict=v, doc_excerpt="1  x", grounding=["[src] line"])
    assert Brief.model_validate_json(b.model_dump_json()) == b


def test_claim_json_schema_requires_span():
    schema = Claim.model_json_schema()
    assert "span" in schema["required"]


def test_observation_receipt_evidence_takes_the_observed_build():
    vid = uuid4()
    build = "b" * 64
    ev = BrowserObservation(status="passed", build_digest=build).receipt_evidence(
        source_version_id=vid, content_sha256="0" * 64, kane_run_id="run-1"
    )
    assert (ev.kind, ev.build_digest, ev.kane_run_id, ev.complete, ev.simulated) == ("browser", build, "run-1", True, False)
    assert BrowserObservation(status="passed").receipt_evidence(
        source_version_id=vid, content_sha256="0" * 64, kane_run_id="run-1"
    ).build_digest is None
    for status in ("failed", "unreachable"):
        assert not BrowserObservation(status=status, build_digest=build).receipt_evidence(
            source_version_id=vid, content_sha256="0" * 64, kane_run_id="run-1"
        ).complete


def test_observation_receipt_evidence_is_never_complete_for_simulated_or_unbound_observations():
    vid, build = uuid4(), "b" * 64
    args = {"source_version_id": vid, "content_sha256": "0" * 64, "kane_run_id": "run-1"}
    simulated = SimulationBrowserObservation(status="passed", build_digest=build, note="simulation-only: fake")
    stored = BrowserObservation.model_validate(simulated.model_dump())  # a stored record loses the subclass
    for obs in (simulated, stored):
        ev = obs.receipt_evidence(**args)
        assert (ev.simulated, ev.complete, ev.build_digest) == (True, False, build)
    unbound = BrowserObservation(status="passed", build_digest=build, note="unbound: no evidence pack names Kane session x")
    ev = unbound.receipt_evidence(**args)
    assert (ev.simulated, ev.complete) == (False, False)

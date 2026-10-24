from collections import Counter
from pathlib import Path

import pytest
from pydantic import ValidationError

from attest.models import BrowserObservation, Claim, Evidence, Span, Verdict
from attest.runner import FakeBrowserRunner, SimulationBrowserObservation
from attest.verdict import (
    Rules,
    expects_in,
    judge,
    judge_all,
    label_match,
    load_rules,
    normalize_label,
    observed_text,
)
from tests.corpus import load_corpus

ROOT = Path(__file__).resolve().parents[1]


def span(t="x"):
    return Span(start_line=1, end_line=1, text=t)


def shell_claim(cid="c01", expects=None, deps=()):
    return Claim(id=cid, kind="shell", span=span(), command="echo", expects=expects, depends_on=list(deps))


def browser_claim(cid="c02", expects="Create project", deps=()):
    return Claim(
        id=cid, kind="browser", span=span(), objective="Click it", expects=expects, store_as=f"{cid}_label", depends_on=list(deps)
    )


def shell_ev(cid="c01", exit_code=0, timed_out=False, stdout=""):
    return Evidence(claim_id=cid, kind="shell", command="echo", exit_code=exit_code, timed_out=timed_out, stdout_tail=stdout)


def browser_ev(cid="c02", status="passed", observed=None, note=None):
    return Evidence(
        claim_id=cid,
        kind="browser",
        observation=BrowserObservation(status=status, observed=observed if observed is not None else {}, note=note),
    )


# -- rules file ------------------------------------------------------------------


def test_load_rules_from_repo():
    r = load_rules(ROOT / "rules.yaml")
    assert r.label_match == 0.8
    assert r.shell_timeout_s == 180
    assert r.loop_attempts == 1


def test_load_rules_missing_file_gives_defaults(tmp_path):
    assert load_rules(tmp_path / "nope.yaml") == Rules()


# -- label match -----------------------------------------------------------------


def test_normalize_label():
    assert normalize_label("  Hello, World!  ") == "hello world"
    assert normalize_label("SIGN-IN") == "sign in"


def test_label_match_bounds_and_symmetry():
    assert label_match("Create project", "Create project") == 1.0
    assert label_match("", "") == 1.0
    assert label_match("x", "") == 0.0
    assert label_match("Create project", "New project") < 0.8
    assert label_match("Hello World", "Hello, World!") == 1.0


def test_expects_in_normalizes_whitespace():
    assert expects_in("Local:   http://localhost:5173/", "  ➜  Local:   http://localhost:5173/")
    assert expects_in("Running on http://127.0.0.1:5000", " * Running on http://127.0.0.1:5000\nPress CTRL+C")
    assert not expects_in("Hello", None)


def test_observed_text_prefers_store_as_key():
    c = browser_claim()
    assert observed_text(c, browser_ev(observed={"c02_label": "A", "other": "B"})) == "A"
    assert observed_text(c, browser_ev(observed={"other": "B", "more": "C"})) == "B C"
    assert observed_text(c, browser_ev(observed={})) is None
    assert observed_text(c, browser_ev(observed={"c02_label": "  "})) is None


# -- each rule -------------------------------------------------------------------


def test_prose_is_unverifiable_without_evidence():
    v = judge(Claim(id="c01", kind="prose", span=span()), None)
    assert v.status == "unverifiable"
    assert v.evidence is None


def test_shell_exit_zero_proven():
    v = judge(shell_claim(), shell_ev())
    assert v.status == "proven"
    assert "exit 0" in v.reason


def test_shell_nonzero_broken():
    v = judge(shell_claim(), shell_ev(exit_code=2))
    assert v.status == "broken"
    assert "2" in v.reason


def test_shell_timeout_broken():
    v = judge(shell_claim(), shell_ev(exit_code=None, timed_out=True))
    assert v.status == "broken"
    assert "timed out" in v.reason


def test_shell_expects_in_stdout_proven_else_stale():
    c = shell_claim(expects="Running on http://127.0.0.1:5000")
    assert judge(c, shell_ev(stdout=" * Running on http://127.0.0.1:5000")).status == "proven"
    v = judge(c, shell_ev(stdout=" * Running on http://127.0.0.1:8080"))
    assert v.status == "stale"
    assert v.label_match == 0.0


def test_shell_claim_never_proven_from_browser_evidence():
    v = judge(shell_claim(cid="c01"), browser_ev(cid="c01"))
    assert v.status == "unverifiable"


def test_browser_claim_never_proven_from_shell_evidence():
    v = judge(browser_claim(cid="c02"), shell_ev(cid="c02"))
    assert v.status == "unverifiable"


def test_browser_no_evidence_unverifiable():
    v = judge(browser_claim(), None)
    assert v.status == "unverifiable"
    assert "reachable" in v.reason


def test_browser_unreachable_unverifiable_with_evidence():
    v = judge(browser_claim(), browser_ev(status="unreachable"))
    assert v.status == "unverifiable"
    assert v.evidence is not None


def test_browser_failed_broken():
    v = judge(browser_claim(), browser_ev(status="failed", note="no such control"))
    assert v.status == "broken"


def test_browser_passed_label_mismatch_stale():
    v = judge(browser_claim(), browser_ev(observed={"c02_label": "New project"}))
    assert v.status == "stale"
    assert v.label_match is not None and v.label_match < 0.8
    assert "New project" in v.reason and "Create project" in v.reason


def test_browser_passed_label_match_proven():
    v = judge(browser_claim(), browser_ev(observed={"c02_label": "Create project"}))
    assert v.status == "proven"
    assert v.label_match == 1.0


def test_browser_passed_without_expectation_is_unverifiable():
    v = judge(browser_claim(expects=None), browser_ev())
    assert v.status == "unverifiable"
    assert "no exact expected text" in v.reason


def test_browser_passed_but_nothing_stored_unverifiable():
    v = judge(browser_claim(), browser_ev(observed={}))
    assert v.status == "unverifiable"
    assert "stored no label" in v.reason


def test_similarity_is_advisory_and_never_proves_a_mismatch():
    ev = browser_ev(observed={"c02_label": "New project"})
    low_threshold = judge(browser_claim(), ev, rules=Rules(label_match=0.5))
    high_threshold = judge(browser_claim(), ev, rules=Rules(label_match=0.95))
    assert low_threshold.status == high_threshold.status == "stale"
    assert low_threshold.label_match == high_threshold.label_match
    assert "suggestion-only" in low_threshold.reason


def test_exact_numeric_predicate_does_not_fuzzy_match():
    claim = browser_claim(expects="count is 1")
    verdict = judge(claim, browser_ev(observed={"c02_label": "count is 10"}))
    assert verdict.status == "stale"
    assert verdict.label_match is not None and verdict.label_match > 0.8


def test_fake_runner_observation_is_simulation_not_verification():
    claim = browser_claim()
    runner = FakeBrowserRunner()
    observation = runner.observe(claim, "https://example.test")
    assert runner.mode == "simulation"
    assert isinstance(observation, SimulationBrowserObservation)
    evidence = Evidence(claim_id=claim.id, kind="browser", observation=observation)
    verdict = judge(claim, evidence)
    assert verdict.status == "unverifiable"
    assert "simulation" in verdict.reason


def test_assert_claim_against_previous_shell_output():
    c = Claim(id="c02", kind="assert", span=span(), expects="Hello from Flask", depends_on=["c01"])
    good = Evidence(claim_id="c02", kind="shell", exit_code=0, stdout_tail="...\nHello from Flask\n")
    assert judge(c, good).status == "proven"
    bad = Evidence(claim_id="c02", kind="shell", exit_code=0, stdout_tail="Hi")
    assert judge(c, bad).status == "stale"


# -- skipped and ordering -----------------------------------------------------------


def test_skipped_when_dependency_not_proven_carries_dep_evidence():
    c1 = shell_claim("c01")
    c2 = browser_claim("c02", deps=["c01"])
    ev1 = shell_ev("c01", exit_code=1)
    verdicts = judge_all([c1, c2], {"c01": ev1, "c02": browser_ev("c02", observed={"c02_label": "Create project"})})
    assert [v.status for v in verdicts] == ["broken", "skipped"]
    assert verdicts[1].evidence is ev1
    assert "c01" in verdicts[1].reason and "broken" in verdicts[1].reason


def test_skipped_beats_broken():
    c1 = shell_claim("c01")
    c2 = shell_claim("c02", deps=["c01"])
    verdicts = judge_all([c1, c2], {"c01": shell_ev("c01", exit_code=1), "c02": shell_ev("c02", exit_code=1)})
    assert verdicts[1].status == "skipped"


def test_skipped_chain_propagates():
    c1, c2, c3 = shell_claim("c01"), shell_claim("c02", deps=["c01"]), shell_claim("c03", deps=["c02"])
    verdicts = judge_all([c1, c2, c3], {"c01": shell_ev("c01", exit_code=1), "c02": shell_ev("c02"), "c03": shell_ev("c03")})
    assert [v.status for v in verdicts] == ["broken", "skipped", "skipped"]


def test_unverifiable_dependency_does_not_skip():
    # a prose dependency has no evidence to hand down, so the claim is judged on its own
    p = Claim(id="c01", kind="prose", span=span())
    c2 = shell_claim("c02", deps=["c01"])
    verdicts = judge_all([p, c2], {"c02": shell_ev("c02")})
    assert [v.status for v in verdicts] == ["unverifiable", "proven"]


# -- invariants through the rules ------------------------------------------------------


def test_every_non_unverifiable_verdict_has_evidence():
    for _, claim, ev, _ in load_corpus():
        v = judge(claim, ev)
        if v.status != "unverifiable":
            assert v.evidence is not None


def test_verdict_model_rejects_badge_without_evidence():
    with pytest.raises(ValidationError):
        Verdict(claim_id="c01", status="stale", reason="r")


# -- corpus ---------------------------------------------------------------------------


def test_corpus_shape():
    corpus = load_corpus()
    assert len(corpus) == 40
    assert Counter(label for *_, label in corpus) == {"proven": 22, "stale": 11, "broken": 4, "unverifiable": 3}


def test_corpus_separates_at_chosen_threshold():
    rules = load_rules(ROOT / "rules.yaml")
    misses = []
    for item_id, claim, ev, label in load_corpus():
        got = judge(claim, ev, {}, rules)
        if got.status != label:
            misses.append((item_id, label, got.status, got.reason))
    assert misses == []


def test_similarity_score_cannot_change_corpus_verdicts():
    for _, claim, ev, label in load_corpus():
        if claim.kind == "browser" and ev.observation.status == "passed":
            assert judge(claim, ev, rules=Rules(label_match=0.1)).status == label
            assert judge(claim, ev, rules=Rules(label_match=1.0)).status == label

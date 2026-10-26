"""Prompt and sidecar-shell verdict rules from docs/CLI-RUNNER-SPEC.md, one case per table row.

Every verdict here is an exact yes/no check: no label_match score is ever set on
a prompt verdict, and a shell verdict's similarity field is only the existing
0.0 / 1.0 marker for an output substring.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from attest.models import BrowserObservation, Claim, Evidence, PromptObservation, Span, Verdict
from attest.prompt_runner import FakePromptRunner, SimulationPromptObservation
from attest.verdict import RESULT_NORMALIZATIONS, Rules, judge, judge_all, load_rules, normalize_result, results_equal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PROMPT = "---\nname: fences\nrunvil: 0\n---\n\n# Fences\n\n## Main\n\n```lua\nreturn 'hello'\n```\n"


def span(t="x"):
    return Span(start_line=1, end_line=1, text=t)


def prompt_claim(cid="c01", expects=None, expects_error=False, deps=()):
    return Claim(id=cid, kind="prompt", span=span(PROMPT), command=PROMPT, expects=expects, expects_error=expects_error, depends_on=list(deps))


def obs(status="completed", **kw):
    kw.setdefault("host_version", "runvil@abc123")
    return PromptObservation(status=status, **kw)


def prompt_ev(cid="c01", **kw):
    return Evidence(claim_id=cid, kind="prompt", prompt=obs(**kw))


def shell_claim(cid="c01", command="echo", expects=None):
    return Claim(id=cid, kind="shell", span=span(command), command=command, expects=expects)


def shell_ev(cid="c01", **kw):
    kw.setdefault("command", "echo")
    if "exit_code" not in kw and not kw.get("timed_out") and not kw.get("service_ready"):
        kw["exit_code"] = 0
    return Evidence(claim_id=cid, kind="shell", **kw)


# -- rules file ----------------------------------------------------------------------


def test_rules_file_has_prompt_and_ready_timeouts():
    r = load_rules(ROOT / "rules.yaml")
    assert r.prompt_timeout_s == 30
    assert r.ready_timeout_s == 60
    assert Rules().prompt_timeout_s == 30 and Rules().ready_timeout_s == 60


# -- normalization -------------------------------------------------------------------


def test_normalize_result_folds_only_the_three_recorded_things():
    assert normalize_result("hello\r\n") == "hello\n"
    assert normalize_result("hello   \nworld \t\n\n\n") == "hello\nworld\n"
    assert normalize_result("hello") == "hello\n"
    # nothing else is folded: case, inner spaces and leading spaces all count
    assert normalize_result("Hello") != normalize_result("hello")
    assert normalize_result("a  b") != normalize_result("a b")
    assert normalize_result("  a") != normalize_result("a")
    assert results_equal("hello\n", "hello\r\n ")
    assert not results_equal("hello\n", None)


# -- prompt claims: one case per row of the spec's table -----------------------------------

PROMPT_ROWS = [
    # (row, claim kwargs, observation kwargs, expected status, text that must appear in the reason)
    ("unverifiable: needs_script", {}, {"status": "needs_script", "rounds": 1}, "unverifiable", "model reply"),
    ("unverifiable: refused", {}, {"status": "refused", "message": "web fetch is not installed"}, "unverifiable", "refused"),
    ("broken: success promised, parse_error", {"expects": "hello\n"}, {"status": "parse_error", "error_kind": "UnclosedFence", "error_line": 9, "message": "section `Main` epilog `lua` fence is not closed"}, "broken", "parse_error"),
    ("broken: success promised, failed", {}, {"status": "failed", "error_kind": "LuaError", "message": "attempt to index nil"}, "broken", "failed"),
    ("broken: result fence shown, run failed with another message", {"expects": "hello\n"}, {"status": "failed", "error_kind": "LuaError", "message": "attempt to index nil"}, "broken", "failed"),
    ("proven: result fence is the failure's message", {"expects": "prompt must contain exactly one H1 title, found 2 at lines 7, 11\n"}, {"status": "parse_error", "error_kind": "Structure", "message": "prompt must contain exactly one H1 title, found 2 at lines 7, 11"}, "proven", "failure's message"),
    ("proven: result fence is inside a longer failure message", {"expects": "later fault"}, {"status": "failed", "error_kind": "Lua", "message": "section `lib` prologue:20: later fault\nstack traceback:\n..."}, "proven", "failure's message"),
    ("proven: result fence names the error kind", {"expects": "Fence"}, {"status": "parse_error", "error_kind": "Fence", "message": "unrecognized fence label"}, "proven", "failure's message"),
    ("broken: failure promised, completed", {"expects_error": True, "expects": "UnclosedFence"}, {"status": "completed", "result": "hello\n"}, "broken", "completed"),
    ("stale: completed, result differs", {"expects": "hello\n"}, {"status": "completed", "result": "goodbye\n"}, "stale", RESULT_NORMALIZATIONS),
    ("stale: failed, error kind differs", {"expects_error": True, "expects": "UnclosedFence"}, {"status": "parse_error", "error_kind": "MissingFrontmatter", "message": "no frontmatter"}, "stale", "UnclosedFence"),
    ("stale: failed, message differs", {"expects_error": True, "expects": "fence is not closed"}, {"status": "failed", "error_kind": "LuaError", "message": "attempt to index nil"}, "stale", "fence is not closed"),
    ("proven: completed, result equals expects", {"expects": "hello\n"}, {"status": "completed", "result": "hello\r\n"}, "proven", RESULT_NORMALIZATIONS),
    ("proven: completed, no expects", {}, {"status": "completed", "result": "anything"}, "proven", "completed"),
    ("proven: failed, error kind equals quote", {"expects_error": True, "expects": "UnclosedFence"}, {"status": "parse_error", "error_kind": "UnclosedFence", "error_line": 9}, "proven", "UnclosedFence"),
    ("proven: failed, message contains quote", {"expects_error": True, "expects": "fence is not closed"}, {"status": "parse_error", "error_kind": "UnclosedFence", "message": "section `Main` epilog `lua` fence is not closed"}, "proven", "fence is not closed"),
    ("proven: failed, nothing quoted", {"expects_error": True}, {"status": "failed", "error_kind": "LuaError", "message": "boom"}, "proven", "failed as the doc promised"),
]


@pytest.mark.parametrize("row,claim_kw,obs_kw,status,needle", PROMPT_ROWS, ids=[r[0] for r in PROMPT_ROWS])
def test_prompt_rule_rows(row, claim_kw, obs_kw, status, needle):
    v = judge(prompt_claim(**claim_kw), prompt_ev(**obs_kw))
    assert v.status == status, v.reason
    assert needle in v.reason
    assert v.evidence is not None
    assert v.label_match is None  # never a similarity score on a prompt verdict


def test_prompt_simulation_is_unverifiable_even_when_it_would_match():
    claim = prompt_claim(expects="hello\n")
    sim = SimulationPromptObservation(status="completed", result="hello\n", host_version="fake")
    v = judge(claim, Evidence(claim_id="c01", kind="prompt", prompt=sim))
    assert v.status == "unverifiable" and "simulation" in v.reason
    runner = FakePromptRunner({"c01": obs(status="completed", result="hello\n")})
    replayed = runner.run(claim)
    assert isinstance(replayed, SimulationPromptObservation)
    assert judge(claim, Evidence(claim_id="c01", kind="prompt", prompt=replayed)).status == "unverifiable"


def test_prompt_needs_script_never_becomes_anything_but_unverifiable():
    for claim in (prompt_claim(), prompt_claim(expects="x"), prompt_claim(expects_error=True), prompt_claim(expects_error=True, expects="E")):
        v = judge(claim, prompt_ev(status="needs_script"))
        assert v.status == "unverifiable"


def test_prompt_claim_never_proven_from_shell_or_browser_evidence():
    claim = prompt_claim(expects="hello\n")
    assert judge(claim, shell_ev(stdout_tail="hello\n")).status == "unverifiable"
    browser = Evidence(claim_id="c01", kind="browser", observation=BrowserObservation(status="passed", observed={"c01_label": "hello"}))
    assert judge(claim, browser).status == "unverifiable"
    assert judge(claim, None).status == "unverifiable"


def test_shell_and_browser_claims_never_proven_from_prompt_evidence():
    assert judge(shell_claim(), prompt_ev(status="completed", result="x")).status == "unverifiable"
    b = Claim(id="c01", kind="browser", span=span(), objective="Open it", expects="hello", store_as="c01_label")
    assert judge(b, prompt_ev(status="completed", result="hello")).status == "unverifiable"


def test_prompt_verdict_takes_part_in_the_dependency_chain():
    c1 = prompt_claim("c01", expects="hello\n")
    c2 = prompt_claim("c02", expects="x\n", deps=["c01"])
    verdicts = judge_all([c1, c2], {"c01": prompt_ev("c01", status="failed", error_kind="LuaError"), "c02": prompt_ev("c02", status="completed", result="x\n")})
    assert [v.status for v in verdicts] == ["broken", "skipped"]


# -- shell additions: one case per row --------------------------------------------------


def test_shell_row_broken_service_readiness_not_answered():
    ev = shell_ev(command="runvil-gateway --config gateway.toml", timed_out=True, ready_url="http://127.0.0.1:8081/health", ready_status=503)
    v = judge(shell_claim(command="runvil-gateway --config gateway.toml"), ev, rules=Rules(ready_timeout_s=5))
    assert v.status == "broken"
    assert "http://127.0.0.1:8081/health" in v.reason and "within 5s" in v.reason and "503" in v.reason
    never = shell_ev(command="x", timed_out=True, ready_url="http://127.0.0.1:8081/health")
    assert "never answered" in judge(shell_claim(command="x"), never).reason


def test_shell_row_stale_substitute_caps_a_pass_and_names_both_commands():
    ev = shell_ev(command="cargo install gateway", exit_code=0, substitute="cargo install --path crates/gateway/app")
    v = judge(shell_claim(command="cargo install gateway"), ev)
    assert v.status == "stale"
    assert "cargo install gateway" in v.reason and "cargo install --path crates/gateway/app" in v.reason
    assert v.label_match is None
    # a substitute that still fails is broken, not stale
    failed = shell_ev(command="cargo install gateway", exit_code=101, substitute="cargo install --path crates/gateway/app")
    assert judge(shell_claim(command="cargo install gateway"), failed).status == "broken"
    # a substitute that starts a service is capped too
    service = shell_ev(command="serve", service_ready=True, ready_url="http://127.0.0.1:1/health", ready_status=200, substitute="serve2")
    assert judge(shell_claim(command="serve"), service).status == "stale"


def test_shell_row_proven_service_readiness_answered_200():
    ev = shell_ev(command="runvil-gateway --config gateway.toml", service_ready=True, ready_url="http://127.0.0.1:8081/health", ready_status=200)
    v = judge(shell_claim(command="runvil-gateway --config gateway.toml"), ev)
    assert v.status == "proven"
    assert "answered 200" in v.reason
    # a promised output line still has to be there
    v2 = judge(shell_claim(command="x", expects="listening"), ev)
    assert v2.status == "stale"


def test_existing_shell_rules_unchanged_without_sidecar_fields():
    assert judge(shell_claim(), shell_ev(exit_code=0)).reason == "proven: exit 0"
    assert judge(shell_claim(), shell_ev(timed_out=True)).reason == "broken: command timed out"
    assert judge(shell_claim(), shell_ev(service_ready=True)).reason == "proven: service ready"


# -- models --------------------------------------------------------------------------


def test_prompt_claim_needs_command_and_expects_error_is_prompt_only():
    with pytest.raises(ValidationError):
        Claim(id="c01", kind="prompt", span=span())
    with pytest.raises(ValidationError):
        Claim(id="c01", kind="shell", span=span(), command="ls", expects_error=True)
    with pytest.raises(ValidationError):
        Claim(id="c01", kind="prose", span=span(), expects_error=True)
    c = prompt_claim(expects_error=True, expects="E")
    assert c.setup == [] and c.expects_error is True
    assert Claim.model_validate_json(c.model_dump_json()) == c


def test_prompt_evidence_shape():
    with pytest.raises(ValidationError):
        Evidence(claim_id="c01", kind="prompt")
    with pytest.raises(ValidationError):
        Evidence(claim_id="c01", kind="prompt", prompt=obs(), exit_code=0)
    with pytest.raises(ValidationError):
        Evidence(claim_id="c01", kind="prompt", prompt=obs(), observation=BrowserObservation(status="passed"))
    with pytest.raises(ValidationError):
        Evidence(claim_id="c01", kind="shell", exit_code=0, prompt=obs())
    with pytest.raises(ValidationError):
        Evidence(claim_id="c01", kind="browser", observation=BrowserObservation(status="passed"), prompt=obs())
    ev = Evidence(claim_id="c01", kind="prompt", prompt=obs(result="hi"))
    assert ev.observed == {} and ev.screenshot is None
    assert Evidence.model_validate_json(ev.model_dump_json()) == ev


def test_prompt_observation_requires_host_version_and_valid_status():
    with pytest.raises(ValidationError):
        PromptObservation(status="completed")
    with pytest.raises(ValidationError):
        PromptObservation(status="done", host_version="x")
    with pytest.raises(ValidationError):
        PromptObservation(status="parse_error", error_line=0, host_version="x")


def test_prompt_verdict_still_needs_evidence_unless_unverifiable():
    with pytest.raises(ValidationError):
        Verdict(claim_id="c01", status="proven", reason="r")
    Verdict(claim_id="c01", status="proven", reason="r", evidence=prompt_ev())

"""The fake prompt runner, its fixtures, and the Runvil Host protocol (docs/CLI-RUNNER-SPEC.md, M4).

Offline: fixtures under tests/fixtures/prompt/ cover every prompt verdict path; the Host side
of the protocol is exercised with a small Python stand-in behind ATTEST_RUNVIL_HOST.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from attest.models import Claim, Evidence, Span
from attest.pipeline import execute, prove
from attest.prompt_runner import FakePromptRunner, SimulationPromptObservation
from attest.runner import FakeBrowserRunner
from attest.runvil_runner import HOST_ENV, PromptRunnerError, RunvilPromptRunner, claim_seed, claim_started_at
from attest.verdict import Rules, judge

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures" / "prompt"
PY = sys.executable

PROMPT = "---\nname: fences\ndescription: One Lua block between two prose blocks\nrunvil: 0\n---\n\n# Fences\n\n## Main\n\nBefore.\n\n```lua\nreturn 'hello'\n```\n\nAfter.\n"
DOC = f"# Chapter\n\n````markdown\n{PROMPT}````\n\n````text\nhello\n````\n"


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv(HOST_ENV, raising=False)
    monkeypatch.chdir(tmp_path)  # the compiler cache is relative to the working directory


def prompt_claim(cid="c01", expects="hello", expects_error=False):
    return Claim(id=cid, kind="prompt", span=Span(start_line=1, end_line=1, text=PROMPT), command=PROMPT, expects=expects, expects_error=expects_error)


# -- fixtures: one per verdict path -------------------------------------------------------------

CASES = [
    ("completed_match", {"expects": "hello"}, "proven"),
    ("completed_no_expects", {"expects": None}, "proven"),
    ("completed_mismatch", {"expects": "hello"}, "stale"),
    ("parse_error", {"expects": "hello"}, "broken"),
    ("failed", {"expects": None}, "broken"),
    ("needs_script", {"expects": "hello"}, "unverifiable"),
    ("refused", {"expects": None}, "unverifiable"),
    ("expected_error_kind_matches", {"expects": "UnclosedFence", "expects_error": True}, "proven"),
    ("expected_error_message_matches", {"expects": "fence is not closed", "expects_error": True}, "proven"),
    ("expected_error_wrong_kind", {"expects": "UnclosedFence", "expects_error": True}, "stale"),
    ("expected_error_but_completed", {"expects": None, "expects_error": True}, "broken"),
]


def test_every_fixture_is_listed_in_the_readme_and_the_table():
    files = sorted(p.stem for p in FIX.glob("*.json"))
    assert files == sorted(name for name, _, _ in CASES)
    readme = (FIX / "README.md").read_text(encoding="utf-8")
    for name in files:
        assert f"`{name}.json`" in readme


@pytest.mark.parametrize("name,claim_kw,status", CASES, ids=[c[0] for c in CASES])
def test_fixture_judges_to_its_verdict_and_is_unverifiable_through_the_fake(name, claim_kw, status):
    runner = FakePromptRunner.from_file(FIX / f"{name}.json")
    claim = prompt_claim(**claim_kw)
    raw = runner.observations["c01"]
    assert "fixture" in raw.host_version
    verdict = judge(claim, Evidence(claim_id="c01", kind="prompt", prompt=raw))
    assert verdict.status == status, verdict.reason
    assert verdict.label_match is None
    replayed = runner.run(claim)
    assert isinstance(replayed, SimulationPromptObservation)
    assert replayed.status == raw.status and replayed.result == raw.result and replayed.error_kind == raw.error_kind
    assert judge(claim, Evidence(claim_id="c01", kind="prompt", prompt=replayed)).status == "unverifiable"


def test_fake_runner_never_guesses_an_unrecorded_claim():
    runner = FakePromptRunner()
    obs = runner.run(prompt_claim("c09"))
    assert obs.status == "needs_script" and "c09" in (obs.message or "")
    assert runner.calls == ["c09"]
    assert judge(prompt_claim("c09"), Evidence(claim_id="c09", kind="prompt", prompt=obs)).status == "unverifiable"


def test_fake_runner_rejects_a_file_that_is_not_a_mapping(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(ValueError, match="mapping claim ids"):
        FakePromptRunner.from_file(bad)


# -- the execute branch --------------------------------------------------------------------------


class VerifierPromptRunner:
    """Returns raw observations as a live Host would; the fake is simulation only."""

    name = "stub-host"

    def __init__(self, observations):
        self.observations = observations
        self.calls = []

    def run(self, claim):
        self.calls.append(claim.id)
        return self.observations[claim.id]

    def close(self):
        pass


def test_execute_sends_prompt_claims_to_the_prompt_runner(tmp_path):
    raw = FakePromptRunner.from_file(FIX / "completed_match.json").observations["c01"]
    runner = VerifierPromptRunner({"c01": raw})
    evidence, verdicts, session = execute([prompt_claim()], FakeBrowserRunner(), Rules(), tmp_path / "scratch", prompt_runner=runner)
    session.close()
    assert runner.calls == ["c01"]
    assert evidence["c01"].kind == "prompt" and evidence["c01"].prompt is raw
    assert verdicts[0].status == "proven"


def test_execute_without_a_prompt_runner_leaves_prompt_claims_unverifiable(tmp_path):
    evidence, verdicts, session = execute([prompt_claim()], FakeBrowserRunner(), Rules(), tmp_path / "scratch")
    session.close()
    assert "c01" not in evidence
    assert verdicts[0].status == "unverifiable" and "prompt claim" in verdicts[0].reason


def test_execute_in_simulation_stays_unverifiable_even_with_a_matching_recording(tmp_path):
    fake = FakePromptRunner.from_file(FIX / "completed_match.json")
    evidence, verdicts, session = execute([prompt_claim()], FakeBrowserRunner(), Rules(), tmp_path / "scratch", prompt_runner=fake, simulation=True)
    session.close()
    assert verdicts[0].status == "unverifiable" and "simulation" in verdicts[0].reason
    assert evidence["c01"].prompt.status == "completed"


# -- prove and the CLI -----------------------------------------------------------------------------


def test_prove_refuses_a_fake_prompt_runner_outside_simulation_and_needs_a_runner_for_prompt_docs(tmp_path):
    doc = tmp_path / "doc.md"
    doc.write_text(DOC, encoding="utf-8")
    with pytest.raises(ValueError, match="fake prompt observations require simulation"):
        prove(str(doc), runner=VerifierBrowser(), home=tmp_path / "home", prompt_runner=FakePromptRunner())
    with pytest.raises(ValueError, match="pass a prompt runner"):
        prove(str(doc), runner=VerifierBrowser(), home=tmp_path / "home")
    # a doc without prompt claims never needs one
    plain = tmp_path / "plain.md"
    plain.write_text("```bash\necho hi\n```\n", encoding="utf-8")
    result = prove(str(plain), runner=VerifierBrowser(), home=tmp_path / "home")
    assert result.by_id["c01"].status == "proven"


class VerifierBrowser:
    name = "stub"

    def observe(self, claim, base_url):
        raise AssertionError("no browser claims expected")

    def close(self):
        pass


def test_prove_checks_the_host_before_running_anything(tmp_path):
    doc = tmp_path / "doc.md"
    doc.write_text("```bash\necho ran > marker\n```\n\n" + DOC, encoding="utf-8")
    with pytest.raises(PromptRunnerError, match="runvil-docs-host not found"):
        prove(str(doc), runner=VerifierBrowser(), home=tmp_path / "home", prompt_runner=RunvilPromptRunner())
    assert not list(tmp_path.glob("**/marker"))


def test_prove_with_the_fake_prompt_runner_renders_the_prompt_drawer(tmp_path):
    doc = tmp_path / "doc.md"
    doc.write_text(DOC, encoding="utf-8")
    fake = FakePromptRunner.from_file(FIX / "completed_match.json")
    result = prove(str(doc), runner=FakeBrowserRunner(), home=tmp_path / "home", prompt_runner=fake, simulation=True)
    assert [c.kind for c in result.claims] == ["prompt"]
    assert result.by_id["c01"].status == "unverifiable"
    page = (result.out_dir / "index.html").read_text(encoding="utf-8")
    assert "Prompt file" in page and "Doc expects the result" in page and "Run status" in page
    assert "prompt claim" in page


def test_cli_prompt_runner_fake_is_simulation_only(tmp_path, monkeypatch):
    from attest.cli import app

    doc = tmp_path / "doc.md"
    doc.write_text(DOC, encoding="utf-8")
    home = str(tmp_path / "home")
    obs = str(FIX / "completed_match.json")
    ok = CliRunner().invoke(app, ["prove", str(doc), "--runner", "fake", "--prompt-runner", "fake", "--prompt-observations", obs, "--simulation", "--home", home])
    assert ok.exit_code == 0, ok.output
    assert "UNVERIFIABLE" in ok.output and "simulation" in ok.output
    no_sim = CliRunner().invoke(app, ["prove", str(doc), "--runner", "fake", "--prompt-runner", "fake", "--home", home])
    assert no_sim.exit_code == 2
    assert "simulation" in (no_sim.output + str(no_sim.stderr_bytes or b""))
    misuse = CliRunner().invoke(app, ["prove", str(doc), "--runner", "fake", "--simulation", "--prompt-observations", obs, "--home", home])
    assert misuse.exit_code == 2
    missing_host = CliRunner().invoke(app, ["prove", str(doc), "--runner", "fake", "--simulation", "--home", home])
    assert missing_host.exit_code == 1
    assert "runvil-docs-host not found" in missing_host.output + str(missing_host.stderr_bytes or b"")


def test_cli_narrates_prompt_verdicts(capsys):
    from attest.cli import _log
    from attest.models import Verdict

    claim = prompt_claim(expects="hello")
    raw = FakePromptRunner.from_file(FIX / "completed_mismatch.json").observations["c01"]
    ev = Evidence(claim_id="c01", kind="prompt", prompt=raw)
    _log(claim, Verdict(claim_id="c01", status="stale", reason="stale", evidence=ev))
    out = capsys.readouterr().out
    assert "STALE" in out and 'doc expects "hello"' in out and 'result "goodbye"' in out
    failed = FakePromptRunner.from_file(FIX / "parse_error.json").observations["c01"]
    _log(claim, Verdict(claim_id="c01", status="broken", reason="broken", evidence=Evidence(claim_id="c01", kind="prompt", prompt=failed)))
    out = capsys.readouterr().out
    assert "BROKEN" in out and "run parse_error: UnclosedFence" in out


# -- the Runvil Host protocol ------------------------------------------------------------------------

FAKE_HOST = '''
import json, re, sys, time
req = json.loads(sys.stdin.read())
open(sys.argv[1], "w", encoding="utf-8").write(json.dumps(req))
prompt = req["prompt"]
if "sleep" in prompt:
    time.sleep(5)
if "crash" in prompt:
    print("boom", file=sys.stderr); sys.exit(3)
if "garbage" in prompt:
    print("not json"); sys.exit(0)
m = re.search(r"return '([^']*)'", prompt)
if m:
    out = {"status": "completed", "result": m.group(1) + "\\n", "rounds": 0, "host_version": "fake-host@1"}
else:
    out = {"status": "parse_error", "error_kind": "NoReturn", "error_line": 1, "host_version": "fake-host@1"}
print(json.dumps(out))
'''


def fake_host(tmp_path: Path) -> tuple[str, Path]:
    script = tmp_path / "fake_host.py"
    script.write_text(FAKE_HOST, encoding="utf-8")
    log = tmp_path / "request.json"
    if sys.platform == "win32":
        wrapper = tmp_path / "runvil-docs-host.cmd"
        wrapper.write_text(f'@"{PY}" "{script}" "{log}"\r\n', encoding="utf-8")
    else:
        wrapper = tmp_path / "runvil-docs-host"
        wrapper.write_text(f'#!/bin/sh\nexec "{PY}" "{script}" "{log}"\n', encoding="utf-8")
        wrapper.chmod(0o755)
    return str(wrapper), log


def test_seed_and_started_at_are_derived_from_the_claim_id():
    assert claim_seed("c01") == claim_seed("c01") and claim_seed("c01") != claim_seed("c02")
    assert claim_started_at("c01") == claim_started_at("c01") and claim_started_at("c01") != claim_started_at("c02")
    assert claim_started_at("c01").startswith("2026-") and claim_started_at("c01").endswith("Z")


def test_runvil_runner_speaks_the_protocol(tmp_path, monkeypatch):
    host, log = fake_host(tmp_path)
    monkeypatch.setenv(HOST_ENV, host)
    runner = RunvilPromptRunner(timeout_s=10)
    assert runner.check() == host
    obs = runner.run(prompt_claim())
    assert obs.status == "completed" and obs.result == "hello\n" and obs.host_version == "fake-host@1"
    request = json.loads(log.read_text(encoding="utf-8"))
    assert request == {"prompt": PROMPT, "args": [], "script": [], "seed": claim_seed("c01"), "started_at": claim_started_at("c01")}
    assert judge(prompt_claim(), Evidence(claim_id="c01", kind="prompt", prompt=obs)).status == "proven"
    # the same claim gives the same request twice
    runner.run(prompt_claim())
    assert json.loads(log.read_text(encoding="utf-8")) == request
    # the doc's argument string travels as one argument the Host joins verbatim
    runner.run(prompt_claim().model_copy(update={"args": "later canned value"}))
    assert json.loads(log.read_text(encoding="utf-8"))["args"] == ["later canned value"]


def test_runvil_runner_fails_closed_on_a_bad_host(tmp_path, monkeypatch):
    host, _ = fake_host(tmp_path)
    monkeypatch.setenv(HOST_ENV, host)
    runner = RunvilPromptRunner(timeout_s=10)
    crash = prompt_claim().model_copy(update={"command": PROMPT.replace("return 'hello'", "crash")})
    with pytest.raises(PromptRunnerError, match="exited 3"):
        runner.run(crash)
    garbage = prompt_claim().model_copy(update={"command": PROMPT.replace("return 'hello'", "garbage")})
    with pytest.raises(PromptRunnerError, match="other than a PromptObservation"):
        runner.run(garbage)
    monkeypatch.setenv(HOST_ENV, str(tmp_path / "missing"))
    with pytest.raises(PromptRunnerError, match="not found"):
        RunvilPromptRunner().check()


def test_runvil_runner_timeout_is_never_evidence(tmp_path, monkeypatch):
    host, _ = fake_host(tmp_path)
    monkeypatch.setenv(HOST_ENV, host)
    runner = RunvilPromptRunner(timeout_s=1.0)
    slow = prompt_claim().model_copy(update={"command": PROMPT.replace("return 'hello'", "sleep")})
    obs = runner.run(slow)
    assert obs.status == "refused" and "did not answer within 1s" in (obs.message or "")
    assert judge(slow, Evidence(claim_id="c01", kind="prompt", prompt=obs)).status == "unverifiable"


# -- --runner none: command and prompt docs need no browser tool ---------------------------------


def test_runner_none_proves_a_doc_without_browser_steps_and_refuses_one_with_them(tmp_path):
    from attest.runner import NoBrowserRunner

    plain = tmp_path / "plain.md"
    plain.write_text("```bash\necho hi\n```\n", encoding="utf-8")
    result = prove(str(plain), runner=NoBrowserRunner(), home=tmp_path / "home")
    assert result.by_id["c01"].status == "proven"
    browser_doc = tmp_path / "browser.md"
    browser_doc.write_text("```bash\necho hi\n```\n", encoding="utf-8")
    from attest import pipeline

    real_compile = pipeline.compile_doc

    def with_browser(doc, llm=None):
        claims = real_compile(doc, llm=llm)
        extra = Claim(id="c02", kind="browser", span=claims[0].span, objective="Open the page", expects="hi", store_as="c02_label")
        return [*claims, extra]

    pipeline.compile_doc = with_browser
    try:
        with pytest.raises(ValueError, match="--runner none chosen but the document has browser claims: c02"):
            prove(str(browser_doc), runner=NoBrowserRunner(), home=tmp_path / "home")
    finally:
        pipeline.compile_doc = real_compile
    with pytest.raises(RuntimeError, match="--runner none"):
        NoBrowserRunner().observe(extra_claim(), None)


def extra_claim():
    return Claim(id="c09", kind="browser", span=Span(start_line=1, end_line=1, text="x"), objective="Open it", expects="x", store_as="c09_label")


def test_cli_runner_none_needs_no_kane(tmp_path):
    from attest.cli import app

    doc = tmp_path / "doc.md"
    doc.write_text("```bash\necho hi\n```\n", encoding="utf-8")
    r = CliRunner().invoke(app, ["prove", str(doc), "--runner", "none", "--home", str(tmp_path / "home")])
    assert r.exit_code == 0, r.output
    assert "runner: none" in r.output and "1/1 proven" in r.output

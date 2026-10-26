"""The repair loop for prompt examples: brief, Runvil grounding, candidate re-check,
approval, and a no-browser validation that passes only without regressions."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from attest.approvals import approve_workflow
from attest.loop import brief_text, build_brief, run_loop
from attest.models import Claim, Evidence, PromptObservation, Span, Verdict
from attest.pipeline import prove
from attest.runner import NoBrowserRunner
from attest.storage import SQLiteWorkflowStore
from attest.verdict import Rules
from attest.workflow import WorkflowError, promote_workflow, resume_workflow

PY = sys.executable
FENCE = "`" * 4


def example(name, value):
    return "\n".join([
        FENCE + "markdown",
        f"---\nname: {name}\ndescription: d\nrunvil: 0\n---\n\n# T\n\n## Main\n\n```lua\nreturn '{value}'\n```",
        FENCE,
    ])


def doc_text(second_result="hello there"):
    return "\n".join([
        "# Prompts", "",
        example("first", "hello"), "", FENCE + "text", "hello", FENCE, "",
        example("second", "goodbye"), "", FENCE + "text", second_result, FENCE, "",
    ]) + "\n"


class StubHost:
    """Runs `return '...'` the way the Host would; anything else fails."""

    name = "stub-host"

    def __init__(self):
        self.calls = []

    def run(self, claim):
        self.calls.append(claim.id)
        m = re.search(r"return '([^']*)'", claim.command or "")
        if m:
            return PromptObservation(status="completed", result=m.group(1), host_version="stub@1")
        return PromptObservation(status="failed", error_kind="Lua", message="no return", host_version="stub@1")

    def close(self):
        pass


FIX_AGENT = textwrap.dedent(
    """\
    import difflib, re, sys
    from pathlib import Path
    brief = sys.stdin.read()
    assert "line diff, doc to run:" in brief and "-hello there" in brief and "+goodbye" in brief
    name = re.search(r"Edit only `([^`]+)`", brief).group(1)
    old = Path(name).read_text(encoding="utf-8")
    new = old.replace("hello there", "goodbye")
    print("".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True), fromfile="a/" + name, tofile="b/" + name)))
    """
)

# a careless agent: fixes the second example's fence and breaks the first one's
CARELESS_AGENT = FIX_AGENT.replace('new = old.replace("hello there", "goodbye")', 'new = old.replace("hello there", "goodbye").replace("````text\\nhello\\n", "````text\\nhowdy\\n")')


def agent(tmp_path, name, body):
    p = tmp_path / f"{name}.py"
    p.write_text(body, encoding="utf-8")
    return f'"{PY}" "{p}"'


@pytest.fixture
def stale_doc(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "prompts.md"
    path.write_text(doc_text(), encoding="utf-8", newline="\n")
    host = StubHost()
    result = prove(str(path), runner=NoBrowserRunner(), home=tmp_path / "home", prompt_runner=host)
    assert [v.status for v in result.verdicts] == ["proven", "stale"]
    return path, host, result


def test_prompt_claims_reach_the_loop_with_a_brief_that_shows_the_diff(stale_doc):
    path, host, result = stale_doc
    assert [c.id for c in result.failing()] == ["c02"]
    claim = result.claims[1]
    brief = build_brief(result.doc, claim, result.by_id["c02"], [], path.name)
    text = brief_text(brief, claim, path.name)
    assert "prompt run status: completed (stub@1)" in text
    assert "line diff, doc to run:" in text and "-hello there" in text and "+goodbye" in text
    assert "Edit only `prompts.md`" in text


def test_loop_proposes_a_prompt_repair_and_rechecks_the_candidate(stale_doc, tmp_path):
    path, host, result = stale_doc
    before = path.read_text(encoding="utf-8")
    result = run_loop(result, NoBrowserRunner(), home=tmp_path / "home", rules=Rules(), agent_cmd=agent(tmp_path, "fix", FIX_AGENT))
    assert result.proposals["c02"]
    check = result.candidate_checks["c02"]
    assert check.status == "proven" and host.calls[-1] == "c02"  # only the patched example re-ran
    assert path.read_text(encoding="utf-8") == before  # nothing promoted before approval
    receipt = json.loads(next((tmp_path / "home" / "receipts").glob("*.json")).read_text(encoding="utf-8"))
    assert receipt["claim"]["kind"] == "prompt" and receipt["disposition"] == "awaiting_approval"
    assert receipt["evidence"]["before"]["observed"]["result"] == "goodbye"
    assert receipt["evidence"]["candidate"]["status"] == "proven"
    store = SQLiteWorkflowStore(tmp_path / "home" / "workflow.sqlite3")
    record = store.get(result.proposals["c02"])
    statuses = json.loads(store.get_artifact(record.proposal["artifact_digests"]["baseline_statuses"]))
    assert statuses == {"prompts.md::prompt::first#1": "proven", "prompts.md::prompt::second#1": "stale"}

    # approval, then a no-browser validation with a real (non-fake) prompt runner, then promotion
    approve_workflow(store, record.workflow_id, "maintainer")
    validated = resume_workflow(store, record.workflow_id, runner=NoBrowserRunner(), prompt_runner=StubHost())
    assert validated.state == "validated", validated.validation
    assert validated.validation["criterion"] == "no_regressions" and validated.validation["prompt_runner"] == "stub-host"
    promoted = promote_workflow(store, record.workflow_id)
    assert promoted.state == "promoted"
    assert path.read_text(encoding="utf-8") == doc_text("goodbye")


def test_a_repair_that_edits_another_example_is_refused_before_approval(stale_doc, tmp_path):
    path, host, result = stale_doc
    result = run_loop(result, NoBrowserRunner(), home=tmp_path / "home", rules=Rules(), agent_cmd=agent(tmp_path, "careless", CARELESS_AGENT))
    store = SQLiteWorkflowStore(tmp_path / "home" / "workflow.sqlite3")
    record = store.get(result.proposals["c02"])
    assert record.state == "rejected"  # the Maintain Docs scope policy: only the target claim's lines may change


class FirstBreaksHost(StubHost):
    """The code changed between proposal and validation: the first example now fails."""

    def run(self, claim):
        if "name: first" in (claim.command or ""):
            self.calls.append(claim.id)
            return PromptObservation(status="failed", error_kind="Lua", message="attempt to call a nil value (field 'x')", host_version="stub@2")
        return super().run(claim)


def test_validation_rejects_a_repair_when_another_example_regressed(stale_doc, tmp_path):
    path, host, result = stale_doc
    result = run_loop(result, NoBrowserRunner(), home=tmp_path / "home", rules=Rules(), agent_cmd=agent(tmp_path, "fix", FIX_AGENT))
    store = SQLiteWorkflowStore(tmp_path / "home" / "workflow.sqlite3")
    record = store.get(result.proposals["c02"])
    approve_workflow(store, record.workflow_id, "maintainer")
    rejected = resume_workflow(store, record.workflow_id, runner=NoBrowserRunner(), prompt_runner=FirstBreaksHost())
    assert rejected.state == "rejected"
    assert rejected.validation["failed_claims"] == ["prompts.md::prompt::first#1:broken (was proven)"]
    with pytest.raises(WorkflowError):
        promote_workflow(store, record.workflow_id)
    assert path.read_text(encoding="utf-8") == doc_text()


def test_no_browser_validation_refuses_fake_or_missing_prompt_runners(stale_doc, tmp_path):
    from attest.prompt_runner import FakePromptRunner

    path, host, result = stale_doc
    result = run_loop(result, NoBrowserRunner(), home=tmp_path / "home", rules=Rules(), agent_cmd=agent(tmp_path, "fix", FIX_AGENT))
    store = SQLiteWorkflowStore(tmp_path / "home" / "workflow.sqlite3")
    record = store.get(result.proposals["c02"])
    approve_workflow(store, record.workflow_id, "maintainer")
    with pytest.raises(WorkflowError, match="real prompt runner"):
        resume_workflow(store, record.workflow_id, runner=NoBrowserRunner(), prompt_runner=FakePromptRunner())
    with pytest.raises(WorkflowError, match="real prompt runner"):
        resume_workflow(store, record.workflow_id, runner=NoBrowserRunner())


def test_runvil_grounding_reads_the_checkouts_history(tmp_path):
    from attest.grounding import ground_runvil

    repo = tmp_path / "runvil"
    (repo / "crates").mkdir(parents=True)
    def git(*args):
        subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t", *args], check=True, capture_output=True)
    git("init", "-q")
    (repo / "crates" / "api.lua").write_text("tools.offer_local(x)\ntools.call(y)\n", encoding="utf-8")
    git("add", "."); git("commit", "-q", "-m", "Add local tools")
    (repo / "crates" / "api.lua").write_text("tools.adhoc(x)\ntools.offer(x)\ntools.call(y)\n", encoding="utf-8")
    git("add", "."); git("commit", "-q", "-m", "Collapse tool offering into tools.offer with adhoc tools")
    claim = Claim(id="c01", kind="prompt", span=Span(start_line=1, end_line=1, text="x"),
                  command="---\nname: a\ndescription: d\nrunvil: 0\n---\n\n# T\n\n## M\n\n```lua\ntools.offer_local('g', f)\n```\n")
    obs = PromptObservation(status="failed", error_kind="Lua", message="attempt to call a nil value (field 'offer_local')", host_version="t")
    v = Verdict(claim_id="c01", status="broken", reason="b", evidence=Evidence(claim_id="c01", kind="prompt", prompt=obs))
    lines = ground_runvil(claim, v, root=repo)
    assert lines[0].startswith("[runvil@")
    assert any("Collapse tool offering into tools.offer with adhoc tools" in ln for ln in lines)
    assert any("`tools.*` calls in the code today:" in ln and "tools.adhoc" in ln for ln in lines)
    assert any("`tools.offer_local` does not appear" in ln for ln in lines)
    assert ground_runvil(claim, v, root=tmp_path / "nowhere") == []


def test_a_bare_diff_with_a_trailing_newline_still_applies(tmp_path):
    from attest.ingest import ingest
    from attest.loop import extract_diff, make_checkout

    path = tmp_path / "prompts.md"
    path.write_text(doc_text(), encoding="utf-8", newline="\n")
    co = make_checkout(ingest(str(path)))
    printed = "--- a/prompts.md\n+++ b/prompts.md\n@@ -37,6 +37,6 @@\n ````\n \n ````text\n-hello there\n+goodbye\n ````\n \n\n"
    diff = extract_diff(printed)
    assert not diff.endswith("\n\n")
    assert co.apply(diff) == (True, "applied")


def test_attest_output_in_the_docs_repo_does_not_change_the_build_identity(tmp_path):
    from attest.proposals import current_build_digest

    repo = tmp_path / "docs"
    repo.mkdir()
    doc = repo / "guide.md"
    doc.write_text("# Guide\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t", "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "docs"], check=True)
    before = current_build_digest(doc)
    (repo / ".attest").mkdir()
    (repo / ".attest" / "workflow.sqlite3").write_bytes(b"approval written here")
    (repo / "cache" / "compiler").mkdir(parents=True)
    (repo / "cache" / "compiler" / "v2-x.json").write_text("{}", encoding="utf-8")
    assert current_build_digest(doc) == before
    (repo / "notes.txt").write_text("a real change", encoding="utf-8")
    assert current_build_digest(doc) != before

import difflib

import pytest

from attest.approvals import ApprovalError, approve_workflow
from attest.compiler import CompileError, fences_by_regex
from attest.ingest import ingest_text, sha256_of
from attest.models import Claim, Span
from attest.policy import MaintainDocsPolicy, validate_diff_scope
from attest.proposals import create_repair_proposal, current_build_digest
from attest.storage import SQLiteWorkflowStore
from attest.workflow import WorkflowError, promote_workflow, resume_workflow


class KaneTestRunner:
    name = "kane"

    def observe(self, claim, base_url):
        raise AssertionError("fixture has no browser claims")

    def close(self):
        pass


def make_diff(old, new, filename="guide.md"):
    return "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=f"a/{filename}",
            tofile=f"b/{filename}",
        )
    )


def allowed_fixture(tmp_path, candidate="# Quickstart\n\nRun this:\n\n```bash\necho ready && exit 0\n```\n"):
    base = "# Quickstart\n\nRun this:\n\n```bash\necho ready && exit 1\n```\n"
    source = tmp_path / "guide.md"
    source.write_text(base, encoding="utf-8")
    doc = ingest_text(base, source=str(source))
    claims = fences_by_regex(doc)
    target = claims[0]
    store = SQLiteWorkflowStore(tmp_path / "workflow.sqlite3")
    record = create_repair_proposal(
        store,
        source_path=source,
        base_text=base,
        candidate_text=candidate,
        diff=make_diff(base, candidate),
        target_claim=target,
        baseline_claims=claims,
        plan="Repair only c01; preserve all promises.",
    )
    return source, store, record


def test_cli_approve_records_pause_and_resume_rejects_simulation(tmp_path):
    from typer.testing import CliRunner

    from attest.cli import app

    source, store, proposal = allowed_fixture(tmp_path)
    cli = CliRunner()
    approved = cli.invoke(
        app,
        ["approve", proposal.workflow_id, "--by", "maintainer", "--home", str(tmp_path)],
    )
    assert approved.exit_code == 0, approved.output
    assert store.get(proposal.workflow_id).state == "validating"
    simulation = cli.invoke(
        app,
        ["resume", proposal.workflow_id, "--runner", "fake", "--simulation", "--apply", "--home", str(tmp_path)],
    )
    assert simulation.exit_code == 2
    assert "fresh Kane evidence" in simulation.output
    assert store.get(proposal.workflow_id).state == "validating"
    assert "exit 1" in source.read_text(encoding="utf-8")


def test_maintain_docs_approval_binds_digests_then_promotes_after_fresh_validation(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    source, store, proposal = allowed_fixture(tmp_path)
    base = source.read_text(encoding="utf-8")
    assert proposal.state == "awaiting_approval"
    assert source.read_text(encoding="utf-8") == base
    assert proposal.proposal["candidate_digest"] == sha256_of(
        store.get_artifact(proposal.proposal["artifact_digests"]["candidate"]).decode()
    )

    approved = approve_workflow(store, proposal.workflow_id, "maintainer@example.test")
    assert approved.state == "validating"
    assert approved.approval["proposal_digest"] == approved.proposal["proposal_digest"]
    for field in ("plan_digest", "source_digest", "build_digest", "policy_digest"):
        assert approved.approval[field] == approved.proposal[field]
    assert source.read_text(encoding="utf-8") == base

    validated = resume_workflow(store, proposal.workflow_id, runner=KaneTestRunner())
    assert validated.state == "validated"
    assert validated.validation["successful"] is True
    assert validated.validation["runner"] == "kane"
    assert validated.validation["statuses"] == {"c01": "proven"}
    assert source.read_text(encoding="utf-8") == base

    promoted = resume_workflow(store, proposal.workflow_id, runner=KaneTestRunner(), apply=True)
    assert promoted.state == "promoted"
    after = source.read_text(encoding="utf-8")
    assert "echo ready && exit 0" in after and "exit 1" not in after
    assert sha256_of(after) == proposal.proposal["candidate_digest"]
    assert [event["to_state"] for event in store.events(proposal.workflow_id)] == [
        "proposed", "awaiting_approval", "validating", "validated", "promoted"
    ]


def test_failed_validation_disables_apply_and_preserves_source(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    candidate = "# Quickstart\n\nRun this:\n\n```bash\necho ready && exit 2\n```\n"
    source, store, proposal = allowed_fixture(tmp_path, candidate)
    approved = approve_workflow(store, proposal.workflow_id, "reviewer")
    base = source.read_text(encoding="utf-8")
    rejected = resume_workflow(store, approved.workflow_id, runner=KaneTestRunner(), apply=True)
    assert rejected.state == "rejected"
    assert rejected.validation["successful"] is False
    assert rejected.validation["failed_claims"] == ["c01:broken"]
    assert source.read_text(encoding="utf-8") == base
    with pytest.raises(WorkflowError, match="successful validation"):
        promote_workflow(store, proposal.workflow_id)
    with pytest.raises(WorkflowError, match="only an approved workflow"):
        resume_workflow(store, proposal.workflow_id, runner=KaneTestRunner(), apply=True)


def test_fake_or_simulated_validation_cannot_advance_or_promote(tmp_path):
    source, store, proposal = allowed_fixture(tmp_path)
    approve_workflow(store, proposal.workflow_id, "reviewer")

    class FakeRunner:
        name = "fake"

    with pytest.raises(WorkflowError, match="fresh Kane evidence"):
        resume_workflow(store, proposal.workflow_id, runner=FakeRunner(), simulation=True, apply=True)
    assert store.get(proposal.workflow_id).state == "validating"
    assert source.read_text(encoding="utf-8").find("exit 1") >= 0


def test_build_digest_binds_dirty_tracked_and_untracked_worktree_inputs(tmp_path):
    import subprocess

    root = tmp_path / "repo"
    root.mkdir()
    source = root / "guide.md"
    source.write_text("# Guide\n", encoding="utf-8")
    app = root / "app.py"
    app.write_text("VERSION = 1\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.test"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Tests"], cwd=root, check=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "baseline"], cwd=root, check=True)
    clean = current_build_digest(source, "source")
    app.write_text("VERSION = 2\n", encoding="utf-8")
    dirty = current_build_digest(source, "source")
    assert clean != dirty
    (root / "new.py").write_text("NEW = True\n", encoding="utf-8")
    untracked = current_build_digest(source, "source")
    assert untracked != dirty


def test_source_or_build_change_after_approval_is_unresolved(tmp_path):
    source, store, proposal = allowed_fixture(tmp_path)
    approve_workflow(store, proposal.workflow_id, "reviewer")
    source.write_text("externally changed\n", encoding="utf-8")
    result = resume_workflow(store, proposal.workflow_id, runner=KaneTestRunner())
    assert result.state == "unresolved"
    assert "source changed after approval" in result.validation["reason"]
    assert source.read_text(encoding="utf-8") == "externally changed\n"


def test_policy_refuses_promise_weakening_repair(tmp_path):
    base = "The service must never expose private data.\n"
    candidate = "The service may expose private data.\n"
    source = tmp_path / "guide.md"
    source.write_text(base, encoding="utf-8")
    target = Claim(id="c01", kind="prose", span=Span(start_line=1, end_line=1, text=base.strip()))
    store = SQLiteWorkflowStore(tmp_path / "state.sqlite3")
    proposal = create_repair_proposal(
        store,
        source_path=source,
        base_text=base,
        candidate_text=candidate,
        diff=make_diff(base, candidate),
        target_claim=target,
        baseline_claims=[target],
        plan="Weaken the security promise.",
        policy=MaintainDocsPolicy(),
    )
    assert proposal.state == "rejected"
    assert "weakens protected promise" in proposal.proposal["authority"]["reason"]
    with pytest.raises(ApprovalError, match="awaiting_approval"):
        approve_workflow(store, proposal.workflow_id, "reviewer")
    assert source.read_text(encoding="utf-8") == base


def test_pipeline_closes_shell_session_when_browser_runner_raises(tmp_path, monkeypatch):
    import attest.pipeline as pipeline
    from attest.models import Evidence, Span
    from attest.verdict import Rules

    closed = []

    class Session:
        base_url = "http://localhost:9000"

        def __init__(self, **kwargs):
            pass

        def run(self, claim):
            return Evidence(claim_id=claim.id, kind="shell", command=claim.command, exit_code=0, detected_url=self.base_url)

        def close(self):
            closed.append(True)

    class BrokenRunner:
        name = "kane"

        def observe(self, claim, base_url):
            raise RuntimeError("runner failed")

    monkeypatch.setattr(pipeline, "ShellSession", Session)
    claims = [
        Claim(id="c01", kind="shell", span=Span(start_line=1, end_line=1, text="echo hi"), command="echo hi"),
        Claim(
            id="c02",
            kind="browser",
            span=Span(start_line=2, end_line=2, text="Open page"),
            objective="Open page",
            expects="Done",
            depends_on=["c01"],
        ),
    ]
    with pytest.raises(RuntimeError, match="runner failed"):
        pipeline.execute(claims, BrokenRunner(), Rules(), tmp_path / "scratch")
    assert closed == [True]


def test_pipeline_fails_closed_when_browser_action_was_not_compiled(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    source = tmp_path / "incomplete.md"
    source.write_text("# Setup\n\nOpen the dashboard and click Save.\n", encoding="utf-8")
    from attest.pipeline import prove

    with pytest.raises(CompileError, match="incomplete compilation"):
        prove(str(source), runner=KaneTestRunner(), home=tmp_path / "home")


def test_policy_refuses_weakening_quantified_contract(tmp_path):
    base = "The service retains at least 10 versions.\n"
    candidate = "The service retains at least 2 versions.\n"
    source = tmp_path / "guide.md"
    source.write_text(base, encoding="utf-8")
    target = Claim(id="c01", kind="prose", span=Span(start_line=1, end_line=1, text=base.strip()))
    store = SQLiteWorkflowStore(tmp_path / "state.sqlite3")
    proposal = create_repair_proposal(
        store,
        source_path=source,
        base_text=base,
        candidate_text=candidate,
        diff=make_diff(base, candidate),
        target_claim=target,
        baseline_claims=[target],
        plan="Change the retention claim.",
    )
    assert proposal.state == "rejected"
    assert "quantified protected contract changed" in proposal.proposal["authority"]["reason"]


def test_proposal_requires_candidate_to_match_exact_patch(tmp_path):
    base = "# Quickstart\n\n```bash\necho ready && exit 1\n```\n"
    candidate = "# Quickstart\n\n```bash\necho ready && exit 0\n```\n"
    mismatch = "# Quickstart\n\n```bash\necho nope && exit 0\n```\n"
    source = tmp_path / "guide.md"
    source.write_text(base, encoding="utf-8")
    target = fences_by_regex(ingest_text(base, source=str(source)))[0]
    with pytest.raises(ValueError, match="exact result"):
        create_repair_proposal(
            SQLiteWorkflowStore(tmp_path / "state.sqlite3"),
            source_path=source,
            base_text=base,
            candidate_text=mismatch,
            diff=make_diff(base, candidate),
            target_claim=target,
            baseline_claims=[target],
            plan="Repair c01.",
        )


def test_scope_checker_rejects_unrelated_obligation_in_same_file():
    diff = "--- a/guide.md\n+++ b/guide.md\n@@ -1,2 +1,2 @@\n-The service must never expose private data.\n-echo ready && exit 1\n+The service may expose private data.\n+echo ready && exit 0\n"
    problems = validate_diff_scope(diff, "guide.md", 2, 2)
    assert any("outside claim lines" in problem for problem in problems)

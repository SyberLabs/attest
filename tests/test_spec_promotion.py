"""Spec mode promotion: candidate edits need no approval; writing the original app does."""

import json
import sqlite3
import textwrap

import pytest
from typer.testing import CliRunner

from attest.app_promotion import SPEC_APP_KIND, tree_digest, tree_manifest
from attest.approvals import ApprovalError, approve_workflow
from attest.cli import app as cli_app
from attest.loop import run_loop
from attest.pipeline import prove
from attest.storage import SQLiteWorkflowStore, canonical_json
from attest.verdict import Rules
from attest.workflow import WorkflowError, promote_workflow, resume_workflow

from tests.test_spec_mode import APP_JS, IMPL_AGENT, SPEC, KaneTestRunner, setup

# A worker whose diff applies but does not implement the capability: the claim stays broken.
NOOP_AGENT = textwrap.dedent(
    """\
    import difflib, json, sys
    ticket = json.loads(sys.stdin.read())
    old = ticket["app"]["files"]["app.js"]
    new = old + "// todo\\n"
    print("".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True), "a/app.js", "b/app.js")))
    """
)


class FakeRunner:
    name = "fake"


def spec_run(tmp_path, monkeypatch, agent=IMPL_AGENT):
    app, spec = setup(tmp_path, monkeypatch, agent)
    home = tmp_path / "home"
    runner = KaneTestRunner()
    result = prove(str(spec), runner=runner, home=home, app_dir=app, keep_servers=True)
    result = run_loop(result, runner, home=home, rules=Rules())
    store = SQLiteWorkflowStore(home / "workflow.sqlite3")
    return app, spec, home, result, store, result.proposals["app"]


def test_candidate_is_implemented_without_approval_and_the_app_waits(tmp_path, monkeypatch):
    app, spec, home, result, store, workflow_id = spec_run(tmp_path, monkeypatch)
    # the candidate copy changed and was re-proven with no approval anywhere
    assert "Mark as won" in (result.candidate.root / "app.js").read_text()
    assert result.by_id["c02"].status == "proven"
    # the original app is untouched and the promotion is a durable, unapproved proposal
    assert (app / "app.js").read_text() == APP_JS
    record = store.get(workflow_id)
    assert record.state == "awaiting_approval" and record.approval is None
    assert record.proposal["kind"] == SPEC_APP_KIND
    assert record.proposal["changed_paths"] == ["app.js"]
    assert record.proposal["implemented_claims"] == ["c02"]
    assert record.proposal["source_digest"] == tree_digest(tree_manifest(app))
    assert record.proposal["candidate_digest"] == tree_digest(tree_manifest(result.candidate.root))
    # the receipt no longer claims a policy approval for the app change
    receipt = json.loads(next((home / "receipts").glob("*.json")).read_text())
    assert receipt["approval"]["actor"] is None and receipt["approval"]["state"] == "not_approved"
    assert receipt["promotion"]["workflow_id"] == workflow_id
    assert receipt["promotion"]["state"] == "awaiting_approval"
    assert spec.read_text() == SPEC


def test_promotion_without_approval_fails_closed(tmp_path, monkeypatch):
    app, _, home, _, store, workflow_id = spec_run(tmp_path, monkeypatch)
    with pytest.raises(WorkflowError, match="approved candidate has successful validation"):
        promote_workflow(store, workflow_id)
    with pytest.raises(WorkflowError, match="only an approved workflow"):
        resume_workflow(store, workflow_id, runner=KaneTestRunner(), apply=True)
    import attest.cli as cli_module

    monkeypatch.setattr(cli_module, "_runner", lambda *args, **kwargs: KaneTestRunner())
    out = CliRunner().invoke(cli_app, ["resume", workflow_id, "--apply", "--home", str(home)])
    assert out.exit_code == 2 and "only an approved workflow" in out.output
    assert store.get(workflow_id).state == "awaiting_approval"
    assert (app / "app.js").read_text() == APP_JS


def test_forged_approval_cannot_promote(tmp_path, monkeypatch):
    app, _, home, _, store, workflow_id = spec_run(tmp_path, monkeypatch)
    # an approval written straight into the store, not bound to the proposal digests
    store.transition(workflow_id, "awaiting_approval", "validating", approval={"actor": "agent"})
    record = resume_workflow(store, workflow_id, runner=KaneTestRunner(), apply=True)
    assert record.state == "unresolved"
    assert "not bound" in record.validation["reason"]
    assert (app / "app.js").read_text() == APP_JS


def test_approved_candidate_promotes_after_fresh_kane_validation(tmp_path, monkeypatch):
    app, _, home, result, store, workflow_id = spec_run(tmp_path, monkeypatch)
    out = CliRunner().invoke(cli_app, ["approve", workflow_id, "--by", "maintainer", "--home", str(home)])
    assert out.exit_code == 0, out.output
    assert (app / "app.js").read_text() == APP_JS  # approval alone writes nothing
    # tampering with the mutable candidate directory after approval changes nothing that is promoted
    (result.candidate.root / "app.js").write_text("// tampered\n", encoding="utf-8")

    validated = resume_workflow(store, workflow_id, runner=KaneTestRunner())
    assert validated.state == "validated"
    assert validated.validation["runner"] == "kane" and validated.validation["statuses"] == {"c01": "proven", "c02": "proven"}
    assert (app / "app.js").read_text() == APP_JS  # validation alone writes nothing

    promoted = resume_workflow(store, workflow_id, runner=KaneTestRunner(), apply=True)
    assert promoted.state == "promoted"
    after = (app / "app.js").read_text()
    assert "Mark as won" in after and "tampered" not in after
    assert (app / "index.html").read_text() == "<h1>Deals</h1>\n"
    assert [e["to_state"] for e in store.events(workflow_id)] == [
        "proposed", "awaiting_approval", "validating", "validated", "promoted",
    ]


def test_simulated_or_fake_validation_cannot_promote(tmp_path, monkeypatch):
    app, _, home, _, store, workflow_id = spec_run(tmp_path, monkeypatch)
    approve_workflow(store, workflow_id, "maintainer")
    with pytest.raises(WorkflowError, match="fresh Kane evidence"):
        resume_workflow(store, workflow_id, runner=FakeRunner(), simulation=True, apply=True)
    out = CliRunner().invoke(cli_app, ["resume", workflow_id, "--runner", "fake", "--simulation", "--apply", "--home", str(home)])
    assert out.exit_code == 2
    assert store.get(workflow_id).state == "validating"
    assert (app / "app.js").read_text() == APP_JS


def test_failed_validation_rejects_and_preserves_the_app(tmp_path, monkeypatch):
    app, _, _, result, store, workflow_id = spec_run(tmp_path, monkeypatch, NOOP_AGENT)
    assert result.by_id["c02"].status == "broken"
    assert store.get(workflow_id).proposal["implemented_claims"] == []
    approve_workflow(store, workflow_id, "maintainer")
    rejected = resume_workflow(store, workflow_id, runner=KaneTestRunner(), apply=True)
    assert rejected.state == "rejected" and rejected.validation["failed_claims"] == ["c02:broken"]
    with pytest.raises(WorkflowError, match="successful validation"):
        promote_workflow(store, workflow_id)
    assert (app / "app.js").read_text() == APP_JS


def test_app_changed_after_approval_refuses_promotion(tmp_path, monkeypatch):
    app, _, _, _, store, workflow_id = spec_run(tmp_path, monkeypatch)
    approve_workflow(store, workflow_id, "maintainer")
    assert resume_workflow(store, workflow_id, runner=KaneTestRunner()).state == "validated"
    (app / "index.html").write_text("<h1>Deals v2</h1>\n", encoding="utf-8")
    with pytest.raises(WorkflowError, match="original app changed after approval"):
        promote_workflow(store, workflow_id)
    assert (app / "app.js").read_text() == APP_JS
    assert store.get(workflow_id).state == "validated"


def test_approval_binds_the_candidate_digest(tmp_path, monkeypatch):
    app, _, home, _, store, workflow_id = spec_run(tmp_path, monkeypatch)
    approve_workflow(store, workflow_id, "maintainer")
    # swap the approved candidate for another tree after approval
    record = store.get(workflow_id)
    proposal = dict(record.proposal, candidate_digest="0" * 64)
    with sqlite3.connect(home / "workflow.sqlite3") as db:
        db.execute("UPDATE workflows SET proposal_json = ? WHERE workflow_id = ?", (canonical_json(proposal), workflow_id))
    record = resume_workflow(store, workflow_id, runner=KaneTestRunner(), apply=True)
    assert record.state == "unresolved"
    assert (app / "app.js").read_text() == APP_JS


def test_unapproved_spec_proposal_cannot_be_approved_twice_or_by_nobody(tmp_path, monkeypatch):
    _, _, _, _, store, workflow_id = spec_run(tmp_path, monkeypatch)
    with pytest.raises(ApprovalError, match="approver identity"):
        approve_workflow(store, workflow_id, "  ")
    approve_workflow(store, workflow_id, "maintainer")
    with pytest.raises(ApprovalError, match="awaiting_approval"):
        approve_workflow(store, workflow_id, "maintainer")


def test_prove_apply_with_app_is_still_refused(tmp_path, monkeypatch):
    app, spec = setup(tmp_path, monkeypatch, IMPL_AGENT)
    out = CliRunner().invoke(cli_app, ["prove", str(spec), "--app", str(app), "--apply", "--home", str(tmp_path / "home")])
    assert out.exit_code == 2 and "--apply is disabled here" in out.output
    assert (app / "app.js").read_text() == APP_JS


LINK_AGENT = textwrap.dedent(
    """\
    import difflib, json, sys
    ticket = json.loads(sys.stdin.read())
    old = ticket["app"]["files"]["link.js"]
    new = old + "// Mark as won\\n"
    print("".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True), "a/link.js", "b/link.js")))
    """
)


def test_candidate_change_through_an_app_symlink_is_never_approvable(tmp_path, monkeypatch):
    # the copy follows link.js -> ../outside.js; promoting the copy would write outside the app root
    app, spec = setup(tmp_path, monkeypatch, LINK_AGENT)
    outside = tmp_path / "outside.js"
    outside.write_text("// outside\n", encoding="utf-8")
    try:
        (app / "link.js").symlink_to(outside)
    except OSError as exc:
        if getattr(exc, "winerror", None) != 1314:
            raise
        pytest.skip("host does not grant Windows symlink creation")
    home = tmp_path / "home"
    runner = KaneTestRunner()
    result = prove(str(spec), runner=runner, home=home, app_dir=app, keep_servers=True)
    result = run_loop(result, runner, home=home, rules=Rules())
    store = SQLiteWorkflowStore(home / "workflow.sqlite3")
    record = store.get(result.proposals["app"])
    assert record.state == "rejected" and "unsafe promotion paths" in record.proposal["authority"]["reason"]
    with pytest.raises(ApprovalError):
        approve_workflow(store, record.workflow_id, "maintainer")
    assert outside.read_text() == "// outside\n" and (app / "app.js").read_text() == APP_JS

"""Spec mode promotion rests on a human approval recorded by `attest approve` for this workflow.

`verify_approval` checks that an approval agrees with the proposal's digests and with its own
digest. Every case below passes that check, because it carries correctly bound digests. Each one
validated and wrote the original app before `verify_spec_approval`. The refusal codes are stable:
approval_missing, approval_unbound, approval_not_this_workflow, approval_not_human,
authority_not_allowed, approval_unrecorded, approval_stale.
"""

import hashlib
import sqlite3
from datetime import datetime, timezone

import pytest
from typer.testing import CliRunner

from attest.approvals import ApprovalError, approval_bindings, approve_workflow
from attest.cli import app as cli_app
from attest.proposals import proposal_digest
from attest.storage import canonical_json
from attest.workflow import WorkflowError, promote_workflow, resume_workflow

from tests.test_spec_mode import APP_JS, KaneTestRunner
from tests.test_spec_promotion import spec_run


def bound_approval(record, *, workflow_id=None, actor="maintainer", approved_at=None) -> dict:
    """An approval shaped and digested exactly as `approve_workflow` makes one, made without it."""
    approval = {
        **approval_bindings(record.proposal),
        "actor": actor,
        "approved_at": approved_at or datetime.now(timezone.utc).isoformat(),
        "workflow_id": workflow_id or record.workflow_id,
    }
    approval["approval_digest"] = hashlib.sha256(canonical_json(approval).encode("utf-8")).hexdigest()
    return approval


def event_for(approval: dict) -> dict:
    return {"actor": approval["actor"], "approval_digest": approval["approval_digest"]}


def sql(home, statement: str, *params) -> None:
    with sqlite3.connect(home / "workflow.sqlite3") as db:
        db.execute(statement, params)


def app_unchanged(app) -> bool:
    return (app / "app.js").read_text(encoding="utf-8") == APP_JS


def refused(record, code: str) -> bool:
    return record.state == "unresolved" and f"({code})" in record.validation["reason"]


# -- approval_unrecorded -------------------------------------------------------------


def test_bound_approval_set_through_the_store_without_attest_approve_is_unrecorded(tmp_path, monkeypatch):
    app, _, _, _, store, workflow_id = spec_run(tmp_path, monkeypatch)
    approval = bound_approval(store.get(workflow_id))
    store.transition(workflow_id, "awaiting_approval", "validating", approval=approval)  # event {}

    outcome = resume_workflow(store, workflow_id, runner=KaneTestRunner(), apply=True)

    assert refused(outcome, "approval_unrecorded"), outcome.validation
    assert app_unchanged(app)


def test_bound_approval_written_into_the_row_with_no_event_is_unrecorded(tmp_path, monkeypatch):
    app, _, home, _, store, workflow_id = spec_run(tmp_path, monkeypatch)
    approval = bound_approval(store.get(workflow_id))
    sql(home, "UPDATE workflows SET state = 'validating', approval_json = ? WHERE workflow_id = ?",
        canonical_json(approval), workflow_id)

    outcome = resume_workflow(store, workflow_id, runner=KaneTestRunner(), apply=True)

    assert refused(outcome, "approval_unrecorded"), outcome.validation
    assert app_unchanged(app)


def test_approver_rewritten_after_attest_approve_is_unrecorded(tmp_path, monkeypatch):
    app, _, home, _, store, workflow_id = spec_run(tmp_path, monkeypatch)
    approved = approve_workflow(store, workflow_id, "maintainer")
    rewritten = bound_approval(approved, actor="release-manager", approved_at=approved.approval["approved_at"])
    sql(home, "UPDATE workflows SET approval_json = ? WHERE workflow_id = ?", canonical_json(rewritten), workflow_id)

    outcome = resume_workflow(store, workflow_id, runner=KaneTestRunner(), apply=True)

    assert refused(outcome, "approval_unrecorded"), outcome.validation
    assert app_unchanged(app)


def test_approval_event_deleted_after_validation_refuses_apply(tmp_path, monkeypatch):
    app, _, home, _, store, workflow_id = spec_run(tmp_path, monkeypatch)
    approve_workflow(store, workflow_id, "maintainer")
    assert resume_workflow(store, workflow_id, runner=KaneTestRunner()).state == "validated"
    sql(home, "DELETE FROM workflow_events WHERE workflow_id = ? AND to_state = 'validating'", workflow_id)

    with pytest.raises(WorkflowError, match=r"\(approval_unrecorded\).*--apply refused"):
        promote_workflow(store, workflow_id)
    with pytest.raises(WorkflowError, match=r"\(approval_unrecorded\).*--apply refused"):
        resume_workflow(store, workflow_id, runner=KaneTestRunner(), apply=True)
    with pytest.raises(WorkflowError, match=r"\(approval_unrecorded\)"):
        resume_workflow(store, workflow_id, runner=KaneTestRunner())  # no "validated" answer either
    assert store.get(workflow_id).state == "validated"
    assert app_unchanged(app)


def _offered_event_deleted(home, workflow_id):
    sql(home, "DELETE FROM workflow_events WHERE workflow_id = ? AND to_state = 'awaiting_approval'", workflow_id)


def _offered_under_another_policy(home, workflow_id):
    sql(home, "UPDATE workflow_events SET payload_json = ? WHERE workflow_id = ? AND to_state = 'awaiting_approval'",
        canonical_json({"authority": "allowed", "policy_digest": "0" * 64}), workflow_id)


def _offered_after_the_approval(home, workflow_id):
    # the two rows trade places in the log; each keeps its own timestamp
    with sqlite3.connect(home / "workflow.sqlite3") as db:
        rows = db.execute("SELECT event_id, from_state, to_state, payload_json FROM workflow_events WHERE workflow_id = ? "
                          "AND to_state IN ('awaiting_approval', 'validating') ORDER BY event_id", (workflow_id,)).fetchall()
        (first, *a), (second, *b) = rows
        db.execute("UPDATE workflow_events SET from_state = ?, to_state = ?, payload_json = ? WHERE event_id = ?", (*b, first))
        db.execute("UPDATE workflow_events SET from_state = ?, to_state = ?, payload_json = ? WHERE event_id = ?", (*a, second))


@pytest.mark.parametrize("tamper", [_offered_event_deleted, _offered_under_another_policy, _offered_after_the_approval],
                         ids=["offer-deleted", "offer-under-another-policy", "offer-after-approval"])
def test_approval_without_its_recorded_proposal_event_is_unrecorded(tmp_path, monkeypatch, tamper):
    app, _, home, _, store, workflow_id = spec_run(tmp_path, monkeypatch)
    approve_workflow(store, workflow_id, "maintainer")
    tamper(home, workflow_id)

    outcome = resume_workflow(store, workflow_id, runner=KaneTestRunner(), apply=True)

    assert refused(outcome, "approval_unrecorded"), outcome.validation
    assert app_unchanged(app)


def test_cli_resume_apply_on_an_unrecorded_approval_writes_nothing(tmp_path, monkeypatch):
    import attest.cli as cli_module

    app, _, home, _, store, workflow_id = spec_run(tmp_path, monkeypatch)
    store.transition(workflow_id, "awaiting_approval", "validating", approval=bound_approval(store.get(workflow_id)))
    monkeypatch.setattr(cli_module, "_runner", lambda *args, **kwargs: KaneTestRunner())

    out = CliRunner().invoke(cli_app, ["resume", workflow_id, "--apply", "--home", str(home)])

    assert out.exit_code == 1 and "unresolved" in out.output, out.output
    assert refused(store.get(workflow_id), "approval_unrecorded")
    assert app_unchanged(app)


# -- approval_not_this_workflow ------------------------------------------------------


def test_approval_moved_with_its_events_from_another_workflow_is_refused(tmp_path, monkeypatch):
    app, _, home, _, store, approved_id = spec_run(tmp_path, monkeypatch)
    approve_workflow(store, approved_id, "maintainer")
    moved = "f" * 32  # never proposed or approved: its row and events are copies of the approved one's
    sql(home, "INSERT INTO workflows SELECT ?, state, proposal_json, approval_json, validation_json, updated_at "
              "FROM workflows WHERE workflow_id = ?", moved, approved_id)
    sql(home, "INSERT INTO workflow_events(workflow_id, from_state, to_state, payload_json, created_at) "
              "SELECT ?, from_state, to_state, payload_json, created_at FROM workflow_events "
              "WHERE workflow_id = ? ORDER BY event_id", moved, approved_id)

    outcome = resume_workflow(store, moved, runner=KaneTestRunner(), apply=True)

    assert refused(outcome, "approval_not_this_workflow"), outcome.validation
    assert app_unchanged(app)


# -- approval_not_human --------------------------------------------------------------


@pytest.mark.parametrize("actor", ["policy:default", "", "  maintainer"], ids=["policy-actor", "blank", "unstripped"])
def test_approval_by_a_policy_or_blank_actor_is_not_human(tmp_path, monkeypatch, actor):
    app, _, _, _, store, workflow_id = spec_run(tmp_path, monkeypatch)
    approval = bound_approval(store.get(workflow_id), actor=actor)
    store.transition(workflow_id, "awaiting_approval", "validating", approval=approval, event=event_for(approval))

    outcome = resume_workflow(store, workflow_id, runner=KaneTestRunner(), apply=True)

    assert refused(outcome, "approval_not_human"), outcome.validation
    assert app_unchanged(app)


# -- authority_not_allowed -----------------------------------------------------------


def test_approval_of_a_proposal_authority_denied_is_refused(tmp_path, monkeypatch):
    app, _, home, _, store, workflow_id = spec_run(tmp_path, monkeypatch)
    proposal = dict(store.get(workflow_id).proposal)
    proposal["authority"] = {**proposal["authority"], "allowed": False, "reason": "owner review required"}
    proposal["proposal_digest"] = proposal_digest(proposal)
    sql(home, "UPDATE workflows SET proposal_json = ? WHERE workflow_id = ?", canonical_json(proposal), workflow_id)
    with pytest.raises(ApprovalError, match="authority policy did not authorize"):
        approve_workflow(store, workflow_id, "maintainer")
    approval = bound_approval(store.get(workflow_id))
    store.transition(workflow_id, "awaiting_approval", "validating", approval=approval, event=event_for(approval))

    outcome = resume_workflow(store, workflow_id, runner=KaneTestRunner(), apply=True)

    assert refused(outcome, "authority_not_allowed"), outcome.validation
    assert app_unchanged(app)


# -- approval_stale ------------------------------------------------------------------


def test_approval_dated_before_its_proposal_is_stale(tmp_path, monkeypatch):
    app, _, _, _, store, workflow_id = spec_run(tmp_path, monkeypatch)
    approval = bound_approval(store.get(workflow_id), approved_at="2026-01-01T00:00:00+00:00")
    store.transition(workflow_id, "awaiting_approval", "validating", approval=approval, event=event_for(approval))

    outcome = resume_workflow(store, workflow_id, runner=KaneTestRunner(), apply=True)

    assert refused(outcome, "approval_stale"), outcome.validation
    assert app_unchanged(app)


# -- controls ------------------------------------------------------------------------


def test_control_attest_approve_validates_and_promotes(tmp_path, monkeypatch):
    app, _, home, _, store, workflow_id = spec_run(tmp_path, monkeypatch)
    out = CliRunner().invoke(cli_app, ["approve", workflow_id, "--by", "maintainer", "--home", str(home)])
    assert out.exit_code == 0, out.output

    assert resume_workflow(store, workflow_id, runner=KaneTestRunner()).state == "validated"
    assert resume_workflow(store, workflow_id, runner=KaneTestRunner(), apply=True).state == "promoted"
    assert "Mark as won" in (app / "app.js").read_text(encoding="utf-8")


def test_control_attest_approve_names_the_workflow_and_refuses_a_policy_actor(tmp_path, monkeypatch):
    _, _, _, _, store, workflow_id = spec_run(tmp_path, monkeypatch)
    with pytest.raises(ApprovalError, match="human approver identity"):
        approve_workflow(store, workflow_id, "policy:default")
    approved = approve_workflow(store, workflow_id, "maintainer")
    assert approved.approval["workflow_id"] == workflow_id
    assert [e["payload"] for e in store.events(workflow_id) if e["to_state"] == "validating"] == [event_for(approved.approval)]

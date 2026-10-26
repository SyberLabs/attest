"""A spec approval is only good on a workflow whose recorded history leads to its current state.

`verify_spec_approval` (#60) checks the approval against its proposal event and its approve
event. It doesn't check what was recorded after them, or whether the event log leads to the
row's state at all. So a decision the store recorded as final, a rejection, an `unresolved` or
a promotion, could be reopened by one `UPDATE workflows SET state = ...`, and the old approval
(or a new `attest approve` on the reopened proposal) validated and wrote the app again.

The event log must replay from exactly one creation event, through the store's allowed
transitions, each step leaving from the state the previous one entered, to the row's state.
Otherwise the refusal is `approval_history_broken`. These cases need no forged approval or
approve event. The approval in every one of them is the one `attest approve` recorded.
"""

import sqlite3

import pytest
from typer.testing import CliRunner

import attest.app_promotion as app_promotion
from attest.approvals import approve_workflow
from attest.cli import app as cli_app
from attest.storage import canonical_json
from attest.workflow import WorkflowError, promote_workflow, resume_workflow

from tests.test_spec_mode import APP_JS, KaneTestRunner
from tests.test_spec_promotion import spec_run


def sql(home, statement: str, *params) -> None:
    with sqlite3.connect(home / "workflow.sqlite3") as db:
        db.execute(statement, params)


def reopen(home, workflow_id: str, state: str) -> None:
    """The one write each case makes: the row's state, and nothing else."""
    sql(home, "UPDATE workflows SET state = ? WHERE workflow_id = ?", state, workflow_id)


def app_unchanged(app) -> bool:
    return (app / "app.js").read_text(encoding="utf-8") == APP_JS


def refused(record) -> bool:
    return record.state == "unresolved" and "(approval_history_broken)" in record.validation["reason"]


# -- a final decision reopened at validation -----------------------------------------


def _rejected_by_validation(store, workflow_id):
    store.transition(workflow_id, "validating", "rejected", validation={"successful": False, "failed_claims": ["c02:failed"]},
                     event={"reason": "fresh validation failed", "failed_claims": ["c02:failed"]})


def _unresolved_at_validation(store, workflow_id):
    store.transition(workflow_id, "validating", "unresolved", validation={"successful": False, "reason": "changed during validation"},
                     event={"reason": "changed during validation"})


@pytest.mark.parametrize("decision", [_rejected_by_validation, _unresolved_at_validation], ids=["rejected", "unresolved"])
def test_final_validation_decision_reopened_to_validating_is_refused(tmp_path, monkeypatch, decision):
    app, _, home, _, store, workflow_id = spec_run(tmp_path, monkeypatch)
    approve_workflow(store, workflow_id, "maintainer")
    decision(store, workflow_id)
    reopen(home, workflow_id, "validating")

    outcome = resume_workflow(store, workflow_id, runner=KaneTestRunner(), apply=True)

    assert refused(outcome), outcome.validation
    assert app_unchanged(app)


def test_reopened_rejection_with_a_bridging_event_is_refused(tmp_path, monkeypatch):
    app, _, home, _, store, workflow_id = spec_run(tmp_path, monkeypatch)
    approve_workflow(store, workflow_id, "maintainer")
    _rejected_by_validation(store, workflow_id)
    reopen(home, workflow_id, "validating")
    # the log now leads to the row's state, through a step the store never allows
    sql(home, "INSERT INTO workflow_events(workflow_id, from_state, to_state, payload_json, created_at) "
              "VALUES (?, 'rejected', 'validating', '{}', datetime('now'))", workflow_id)

    outcome = resume_workflow(store, workflow_id, runner=KaneTestRunner(), apply=True)

    assert refused(outcome), outcome.validation
    assert app_unchanged(app)


def test_proposal_closed_before_approval_then_reopened_and_approved_is_refused(tmp_path, monkeypatch):
    app, _, home, _, store, workflow_id = spec_run(tmp_path, monkeypatch)
    store.transition(workflow_id, "awaiting_approval", "unresolved", event={"reason": "superseded by a newer proposal"})
    reopen(home, workflow_id, "awaiting_approval")
    approve_workflow(store, workflow_id, "release-manager")  # a real approve event, on a closed proposal

    outcome = resume_workflow(store, workflow_id, runner=KaneTestRunner(), apply=True)

    assert refused(outcome), outcome.validation
    assert app_unchanged(app)


def test_cli_approve_and_resume_apply_on_a_reopened_proposal_write_nothing(tmp_path, monkeypatch):
    import attest.cli as cli_module

    app, _, home, _, store, workflow_id = spec_run(tmp_path, monkeypatch)
    store.transition(workflow_id, "awaiting_approval", "unresolved", event={"reason": "superseded by a newer proposal"})
    reopen(home, workflow_id, "awaiting_approval")
    monkeypatch.setattr(cli_module, "_runner", lambda *args, **kwargs: KaneTestRunner())

    approved = CliRunner().invoke(cli_app, ["approve", workflow_id, "--by", "maintainer", "--home", str(home)])
    out = CliRunner().invoke(cli_app, ["resume", workflow_id, "--apply", "--home", str(home)])

    assert approved.exit_code == 0, approved.output
    assert out.exit_code == 1 and "unresolved" in out.output, out.output
    assert refused(store.get(workflow_id))
    assert app_unchanged(app)


def _creation_deleted(home, workflow_id):
    sql(home, "DELETE FROM workflow_events WHERE workflow_id = ? AND from_state IS NULL", workflow_id)


def _created_twice(home, workflow_id):
    sql(home, "INSERT INTO workflow_events(workflow_id, from_state, to_state, payload_json, created_at) "
              "SELECT workflow_id, NULL, 'proposed', '{}', created_at FROM workflow_events "
              "WHERE workflow_id = ? AND from_state IS NULL", workflow_id)


@pytest.mark.parametrize("tamper", [_creation_deleted, _created_twice], ids=["creation-deleted", "created-twice"])
def test_history_that_does_not_start_at_one_creation_is_refused(tmp_path, monkeypatch, tamper):
    app, _, home, _, store, workflow_id = spec_run(tmp_path, monkeypatch)
    approve_workflow(store, workflow_id, "maintainer")
    tamper(home, workflow_id)

    outcome = resume_workflow(store, workflow_id, runner=KaneTestRunner(), apply=True)

    assert refused(outcome), outcome.validation
    assert app_unchanged(app)


# -- a final decision reopened at promotion ------------------------------------------


def test_validated_workflow_closed_then_reopened_cannot_promote(tmp_path, monkeypatch):
    app, _, home, _, store, workflow_id = spec_run(tmp_path, monkeypatch)
    approve_workflow(store, workflow_id, "maintainer")
    assert resume_workflow(store, workflow_id, runner=KaneTestRunner()).state == "validated"
    store.transition(workflow_id, "validated", "unresolved", event={"reason": "withdrawn before --apply"})
    reopen(home, workflow_id, "validated")

    with pytest.raises(WorkflowError, match=r"\(approval_history_broken\).*--apply refused"):
        promote_workflow(store, workflow_id)
    with pytest.raises(WorkflowError, match=r"\(approval_history_broken\).*--apply refused"):
        resume_workflow(store, workflow_id, runner=KaneTestRunner(), apply=True)
    with pytest.raises(WorkflowError, match=r"\(approval_history_broken\)"):
        resume_workflow(store, workflow_id, runner=KaneTestRunner())  # no "validated" answer either
    assert app_unchanged(app)


def test_promotion_reopened_after_the_app_was_rolled_back_is_not_applied_again(tmp_path, monkeypatch):
    app, _, home, _, store, workflow_id = spec_run(tmp_path, monkeypatch)
    approve_workflow(store, workflow_id, "maintainer")
    assert resume_workflow(store, workflow_id, runner=KaneTestRunner(), apply=True).state == "promoted"
    (app / "app.js").write_text(APP_JS, encoding="utf-8")  # the owner rolled the change back
    reopen(home, workflow_id, "validated")

    with pytest.raises(WorkflowError, match=r"\(approval_history_broken\).*--apply refused"):
        promote_workflow(store, workflow_id)
    assert app_unchanged(app)
    assert [e["to_state"] for e in store.events(workflow_id)].count("promoted") == 1


# -- controls ------------------------------------------------------------------------


def test_control_the_recorded_history_replays_at_each_spec_check(tmp_path, monkeypatch):
    app, _, _, _, store, workflow_id = spec_run(tmp_path, monkeypatch)
    for step in (lambda: approve_workflow(store, workflow_id, "maintainer"),
                 lambda: resume_workflow(store, workflow_id, runner=KaneTestRunner())):
        record = step()
        app_promotion.verify_spec_approval(store, record)
        app_promotion.verify_spec_history(store, record)
    assert promote_workflow(store, workflow_id).state == "promoted"
    assert [(e["from_state"], e["to_state"]) for e in store.events(workflow_id)] == [
        (None, "proposed"), ("proposed", "awaiting_approval"), ("awaiting_approval", "validating"),
        ("validating", "validated"), ("validated", "promoted"),
    ]
    assert "Mark as won" in (app / "app.js").read_text(encoding="utf-8")


def test_control_a_rejected_workflow_left_closed_still_says_only_an_approved_one_resumes(tmp_path, monkeypatch):
    app, _, _, _, store, workflow_id = spec_run(tmp_path, monkeypatch)
    approve_workflow(store, workflow_id, "maintainer")
    _rejected_by_validation(store, workflow_id)

    with pytest.raises(WorkflowError, match="workflow is rejected; only an approved workflow can be resumed"):
        resume_workflow(store, workflow_id, runner=KaneTestRunner(), apply=True)
    assert app_unchanged(app)
    assert canonical_json(store.events(workflow_id)[-1]["payload"]).count("fresh validation failed") == 1

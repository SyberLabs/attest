"""Maintain Docs red-team cases (FACTORY §1) on what the workflow store and the source path
hold between approval and --apply: rewritten approvals, a borrowed validation record, and a
source swapped for a symlink.

Inputs and expected invariants live in cases/maintain_docs/<ID>.json. Store rewrites go
through SQLite directly, the way MD-DS-04 edits a stored proposal.
"""

from __future__ import annotations

import hashlib
import sqlite3
import sys
from pathlib import Path

import pytest

from attest.approvals import approve_workflow
from attest.compiler import fences_by_regex
from attest.execution.recipes import ApprovedRecipeExecutor
from attest.ingest import ingest_text
from attest.proposals import create_repair_proposal
from attest.storage import SQLiteWorkflowStore, canonical_json
from attest.workflow import WorkflowError, promote_workflow, resume_workflow

from ._harness import KaneTestRunner, case, make_diff


@pytest.fixture(autouse=True)
def _no_model(monkeypatch):
    # Every case here must hold without a compiler model; none may reach the network.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)


def _source(tmp_path: Path, base: str) -> Path:
    source = tmp_path / "guide.md"
    source.write_text(base, encoding="utf-8", newline=chr(10))
    return source


def _proposal(store: SQLiteWorkflowStore, source: Path, base: str, candidate: str):
    claims = fences_by_regex(ingest_text(base, source=str(source)))
    return create_repair_proposal(
        store, source_path=source, base_text=base, candidate_text=candidate, diff=make_diff(base, candidate),
        target_claim=claims[0], baseline_claims=claims, plan="Repair only c01; preserve all promises.",
    )


def _recipes(approved: dict[str, str]) -> ApprovedRecipeExecutor:
    return ApprovedRecipeExecutor({command: [sys.executable, "-c", code] for command, code in approved.items()})


def _rewrite(store: SQLiteWorkflowStore, workflow_id: str, **columns) -> None:
    sets = ", ".join(f"{name} = ?" for name in columns)
    with sqlite3.connect(store.path) as db:
        db.execute(f"UPDATE workflows SET {sets} WHERE workflow_id = ?", (*columns.values(), workflow_id))


def _digest(payload: dict) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


# -- AB: authority bypass ------------------------------------------------------------


@case("MD-AB-14")
def test_rewritten_approval_stops_validation(rt_case, tmp_path):
    i, x = rt_case["input"], rt_case["expected"]
    for name, rewrite in i["rewrites"].items():
        root = tmp_path / name
        root.mkdir()
        source = _source(root, i["base"])
        store = SQLiteWorkflowStore(root / "workflow.sqlite3")
        record = _proposal(store, source, i["base"], i["candidate"])
        approved = approve_workflow(store, record.workflow_id, i["approver"])
        if rewrite["fields"] is None:
            approval_json = None
        else:
            approval = {**approved.approval, **rewrite["fields"]}
            if rewrite["redigest"]:
                approval["approval_digest"] = _digest({k: v for k, v in approval.items() if k != "approval_digest"})
            approval_json = canonical_json(approval)
        _rewrite(store, record.workflow_id, approval_json=approval_json)

        result = resume_workflow(store, record.workflow_id, runner=KaneTestRunner(),
                                 recipe_executor=_recipes(i["approved_recipes"]), apply=True)
        assert result.state == x["state"], name
        assert x["reasons"][name] in result.validation["reason"], name
        with pytest.raises(WorkflowError, match=x["promote_error"]):
            promote_workflow(store, record.workflow_id)
        assert (source.read_text(encoding="utf-8") == i["base"]) is x["source_unchanged"], name


# -- SIM: simulation or fake evidence --------------------------------------------------


@case("MD-SIM-05")
def test_sibling_validation_record_does_not_promote(rt_case, tmp_path):
    i, x = rt_case["input"], rt_case["expected"]
    for name, variant in i["variants"].items():
        root = tmp_path / name
        root.mkdir()
        source = _source(root, i["base"])
        store = SQLiteWorkflowStore(root / "workflow.sqlite3")
        first = _proposal(store, source, i["base"], i["candidate"])
        second = _proposal(store, source, i["base"], i["candidate"])
        # Same base, candidate and plan: the two proposals are digest-identical, so only the
        # validation's binding to its own workflow and approval tells them apart.
        assert (first.proposal["proposal_digest"] == second.proposal["proposal_digest"]) is x["same_proposal_digest"]
        approve_workflow(store, first.workflow_id, i["approver"])
        theirs = approve_workflow(store, second.workflow_id, i["approver"])
        validated = resume_workflow(store, first.workflow_id, runner=KaneTestRunner(),
                                    recipe_executor=_recipes(i["approved_recipes"]))
        assert validated.state == x["validated"], validated.validation

        borrowed = dict(validated.validation)
        for key in variant.get("rebind", []):
            borrowed[key] = second.workflow_id if key == "workflow_id" else theirs.approval["approval_digest"]
        _rewrite(store, second.workflow_id, state="validated", validation_json=canonical_json(borrowed))

        with pytest.raises(WorkflowError, match=x["errors"][name]):
            promote_workflow(store, second.workflow_id)
        assert (source.read_text(encoding="utf-8") == i["base"]) is x["source_unchanged"], name


# -- DS: digest swap / identity confusion ----------------------------------------------


def _swap_for_symlink(source: Path, copy: Path) -> None:
    copy.parent.mkdir(parents=True, exist_ok=True)
    copy.write_bytes(source.read_bytes())
    source.unlink()
    try:
        source.symlink_to(copy)
    except OSError as exc:  # Windows without Developer Mode or admin cannot create symlinks
        pytest.skip(f"cannot create a symlink here ({exc})")


@case("MD-DS-07")
def test_source_swapped_for_a_symlink_is_never_written_through(rt_case, tmp_path):
    i, x = rt_case["input"], rt_case["expected"]
    recipes = _recipes(i["approved_recipes"])

    before = tmp_path / "before"
    before.mkdir()
    source = _source(before, i["base"])
    store = SQLiteWorkflowStore(before / "workflow.sqlite3")
    record = _proposal(store, source, i["base"], i["candidate"])
    approve_workflow(store, record.workflow_id, i["approver"])
    _swap_for_symlink(source, before / i["copy_name"])
    result = resume_workflow(store, record.workflow_id, runner=KaneTestRunner(), recipe_executor=recipes, apply=True)
    assert result.state == x["before_validation"]["state"]
    assert x["before_validation"]["reason_contains"] in result.validation["reason"]
    assert ((before / i["copy_name"]).read_text(encoding="utf-8") == i["base"]) is x["copy_unchanged"]
    assert source.is_symlink() is x["still_a_symlink"]

    after = tmp_path / "after"
    after.mkdir()
    source = _source(after, i["base"])
    store = SQLiteWorkflowStore(after / "workflow.sqlite3")
    record = _proposal(store, source, i["base"], i["candidate"])
    approve_workflow(store, record.workflow_id, i["approver"])
    validated = resume_workflow(store, record.workflow_id, runner=KaneTestRunner(), recipe_executor=recipes)
    assert validated.state == x["after_validation"]["validated"], validated.validation
    _swap_for_symlink(source, after / i["copy_name"])
    with pytest.raises(WorkflowError, match=x["after_validation"]["promote_error"]):
        promote_workflow(store, record.workflow_id)
    assert store.get(record.workflow_id).state == x["after_validation"]["state"]
    assert ((after / i["copy_name"]).read_text(encoding="utf-8") == i["base"]) is x["copy_unchanged"]
    assert source.is_symlink() is x["still_a_symlink"]

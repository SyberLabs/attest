"""Maintain Docs red-team cases (FACTORY §1) plus the §2 positive control.

Inputs and expected invariants live in cases/maintain_docs/<ID>.json.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from attest.approvals import ApprovalError, approval_bindings, approve_workflow, verify_approval
from attest.compiler import ClaimSet, CompileError, compile_doc, fences_by_regex
from attest.contracts import RepairProposal
from attest.execution.recipes import ApprovedRecipeExecutor
from attest.execution.sandbox import safe_pack_path
from attest.ingest import ingest_text, sha256_of
from attest.loop import run_loop
from attest.models import BrowserObservation, Claim, Evidence, Span, Verdict
from attest.pipeline import prove
from attest.policy import MaintainDocsPolicy, Policy, load_policy, validate_diff_scope
from attest.proposals import create_repair_proposal, proposal_digest
from attest.receipts import ReceiptEvidence
from attest.runner import FakeBrowserRunner
from attest.snapshots import SourceVersion
from attest.storage import SQLiteWorkflowStore, canonical_json
from attest.verdict import Rules, judge
from attest import workflow as workflow_module
from attest.workflow import DOCS_APPROVAL_CODES, WorkflowError, promote_workflow, resume_workflow

from ._harness import KaneTestRunner, case, make_diff


@pytest.fixture(autouse=True)
def _no_model(monkeypatch):
    # Every case here must hold without a compiler model; none may reach the network.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)


def _prose_target(text: str, start: int, end: int) -> Claim:
    lines = text.splitlines()
    return Claim(id="c01", kind="prose", span=Span(start_line=start, end_line=end, text="\n".join(lines[start - 1 : end])))


def propose(tmp_path: Path, base: str, candidate: str, *, target: Claim | None = None, policy=None,
            plan: str = "Repair only c01; preserve all promises."):
    tmp_path.mkdir(parents=True, exist_ok=True)
    source = tmp_path / "guide.md"
    # LF bytes on every platform: text mode writes CRLF on Windows, and the CRLF-resave case
    # (MD-DS-05) needs the approved bytes to differ from the resaved ones
    source.write_text(base, encoding="utf-8", newline=chr(10))
    claims = [target] if target is not None else fences_by_regex(ingest_text(base, source=str(source)))
    store = SQLiteWorkflowStore(tmp_path / "workflow.sqlite3")
    record = create_repair_proposal(
        store,
        source_path=source,
        base_text=base,
        candidate_text=candidate,
        diff=make_diff(base, candidate),
        target_claim=claims[0],
        baseline_claims=claims,
        plan=plan,
        policy=policy,
    )
    return source, store, record


def _browser_claim(expects: str) -> Claim:
    return Claim(
        id="c01",
        kind="browser",
        span=Span(start_line=1, end_line=1, text=f'The page shows "{expects}".'),
        objective=f'Open the page and check for "{expects}".',
        expects=expects,
        store_as="c01_label",
    )


# -- positive control ----------------------------------------------------------------


@case("MD-CTRL-01")
def test_allowed_repair_promotes_end_to_end(rt_case, tmp_path):
    i, x = rt_case["input"], rt_case["expected"]
    source, store, record = propose(tmp_path, i["base"], i["candidate"], plan=i["plan"])
    assert record.state == "awaiting_approval"
    approve_workflow(store, record.workflow_id, i["approver"])
    # #25 validates shell claims recipes-only. This control's candidate is the
    # approved command, so the resume has to name that recipe or it fails closed.
    ready = ApprovedRecipeExecutor({"echo ready && exit 0": [sys.executable, "-c", "print('ready')"]})
    validated = resume_workflow(store, record.workflow_id, runner=KaneTestRunner(), recipe_executor=ready)
    assert validated.state == "validated" and validated.validation["successful"] is True
    promoted = resume_workflow(store, record.workflow_id, runner=KaneTestRunner(), recipe_executor=ready, apply=True)

    assert promoted.state == x["final_state"]
    assert sha256_of(source.read_text(encoding="utf-8")) == record.proposal[x["source_matches"]]
    assert [e["to_state"] for e in store.events(record.workflow_id)] == x["events"]


# -- PI: prompt injection ------------------------------------------------------------


@case("MD-PI-01")
def test_injected_edit_of_protected_text_outside_target_is_rejected(rt_case, tmp_path):
    i, x = rt_case["input"], rt_case["expected"]
    target = _prose_target(i["base"], *i["target_lines"])
    source, store, record = propose(tmp_path, i["base"], i["candidate"], target=target)

    assert record.state == x["state"]
    authority = record.proposal["authority"]
    assert any(x["violation_contains"] in v for v in authority["violations"])
    assert authority["implementation_handoff"]["kind"] == x["handoff_kind"]
    assert source.read_text(encoding="utf-8") == i["base"]
    assert i["protected_line"] in source.read_text(encoding="utf-8")


@case("MD-PI-02")
def test_agent_output_patching_another_file_creates_no_proposal(rt_case, tmp_path, monkeypatch):
    i, x = rt_case["input"], rt_case["expected"]
    monkeypatch.chdir(tmp_path)
    doc = tmp_path / "loop.md"
    doc.write_text(i["doc"], encoding="utf-8")
    canned = tmp_path / "agent_output.txt"
    canned.write_text(i["agent_output"], encoding="utf-8")
    agent = tmp_path / "agent.py"
    agent.write_text(f"import sys\nsys.stdin.read()\nsys.stdout.write(open({str(canned)!r}, encoding='utf-8').read())\n", encoding="utf-8")
    home = tmp_path / "home"

    result = prove(str(doc), runner=KaneTestRunner(), home=home)
    result = run_loop(result, KaneTestRunner(), home=home, rules=Rules(), agent_cmd=f'"{sys.executable}" "{agent}"')

    assert x["no_proposal"] and result.proposals == {}
    receipt = json.loads(next((home / "receipts").glob("*.json")).read_text(encoding="utf-8"))
    assert receipt["disposition"] == x["disposition"]
    assert x["reason_contains"] in result.by_id["c01"].reason
    assert doc.read_text(encoding="utf-8") == i["doc"]
    assert not (tmp_path / x["foreign_file_absent"]).exists()


@case("MD-PI-03")
def test_protected_claim_text_cannot_declare_itself_editable(rt_case):
    i, x = rt_case["input"], rt_case["expected"]
    claim = Claim(id="c01", kind="prose", span=Span(start_line=1, end_line=1, text=i["span_text"]))
    decision = Policy(path=None, protected=tuple(i["policy_rules"])).decide(claim)
    assert decision.decision == x["decision"]
    assert decision.allowed is x["allowed"]
    assert decision.rule == x["rule"]


@case("MD-PI-04")
def test_page_text_instructions_do_not_prove_a_browser_claim(rt_case):
    i, x = rt_case["input"], rt_case["expected"]
    claim = _browser_claim(i["expects"])
    for item in i["observations"]:
        observation = BrowserObservation(status="passed", observed={"c01_label": item["observed"]}, note=item["note"])
        verdict = judge(claim, Evidence(claim_id="c01", kind="browser", observation=observation))
        assert verdict.status == x["status"], item


# -- AB: authority bypass ------------------------------------------------------------


@case("MD-AB-01")
def test_weakened_promise_is_refused_with_handoff(rt_case, tmp_path):
    i, x = rt_case["input"], rt_case["expected"]
    target = _prose_target(i["base"], 1, 1)
    source, store, record = propose(tmp_path, i["base"], i["candidate"], target=target, plan="Match the app.")

    assert record.state == x["state"]
    authority = record.proposal["authority"]
    for fragment in x["violations_contain"]:
        assert any(fragment in v for v in authority["violations"]), fragment
    assert authority["implementation_handoff"]["kind"] == x["handoff_kind"]
    with pytest.raises(ApprovalError, match=x["approve_error"]):
        approve_workflow(store, record.workflow_id, "reviewer")
    assert source.read_text(encoding="utf-8") == i["base"]


@case("MD-AB-02")
def test_apply_after_failed_validation_never_writes_source(rt_case, tmp_path):
    i, x = rt_case["input"], rt_case["expected"]
    source, store, record = propose(tmp_path, i["base"], i["candidate"])
    approve_workflow(store, record.workflow_id, i["approver"])

    rejected = resume_workflow(store, record.workflow_id, runner=KaneTestRunner(), apply=True)
    assert rejected.state == x["state"]
    assert rejected.validation["failed_claims"] == x["failed_claims"]
    with pytest.raises(WorkflowError, match=x["promote_error"]):
        promote_workflow(store, record.workflow_id)
    with pytest.raises(WorkflowError, match=x["resume_error"]):
        resume_workflow(store, record.workflow_id, runner=KaneTestRunner(), apply=True)
    assert source.read_text(encoding="utf-8") == i["base"]


@case("MD-AB-03")
def test_unapproved_proposal_cannot_be_promoted(rt_case, tmp_path):
    i, x = rt_case["input"], rt_case["expected"]
    source, store, record = propose(tmp_path, i["base"], i["candidate"])
    assert record.state == x["state"]
    with pytest.raises(WorkflowError, match=x["promote_error"]):
        promote_workflow(store, record.workflow_id)
    with pytest.raises(WorkflowError, match=x["resume_error"]):
        resume_workflow(store, record.workflow_id, runner=KaneTestRunner(), apply=True)
    assert store.get(record.workflow_id).state == x["state"]
    assert source.read_text(encoding="utf-8") == i["base"]


@case("MD-AB-04")
def test_blank_approver_is_refused(rt_case, tmp_path):
    i, x = rt_case["input"], rt_case["expected"]
    _, store, record = propose(tmp_path, i["base"], i["candidate"])
    for actor in i["approvers"]:
        with pytest.raises(ApprovalError, match=x["approve_error"]):
            approve_workflow(store, record.workflow_id, actor)
    assert store.get(record.workflow_id).state == x["state"]


@case("MD-AB-05")
def test_removing_a_configured_protected_contract_is_rejected(rt_case, tmp_path):
    i, x = rt_case["input"], rt_case["expected"]
    policy = MaintainDocsPolicy(protected_contracts=tuple(i["protected_contracts"]))
    target = _prose_target(i["base"], 1, 1)
    source, _, record = propose(tmp_path, i["base"], i["candidate"], target=target, policy=policy)
    assert record.state == x["state"]
    assert any(x["violation_contains"] in v for v in record.proposal["authority"]["violations"])
    assert source.read_text(encoding="utf-8") == i["base"]


@case("MD-AB-06")
def test_url_or_symlink_source_cannot_be_promoted(rt_case, tmp_path):
    i, x = rt_case["input"], rt_case["expected"]
    target = _prose_target(i["base"], 1, 1)
    store = SQLiteWorkflowStore(tmp_path / "workflow.sqlite3")
    common = dict(base_text=i["base"], candidate_text=i["candidate"], diff=make_diff(i["base"], i["candidate"]),
                  target_claim=target, baseline_claims=[target], plan="Repair c01.")
    with pytest.raises(ValueError, match=x["url_error"]):
        create_repair_proposal(store, source_path=i["url_source"], **common)

    real = tmp_path / "real.md"
    real.write_text(i["base"], encoding="utf-8")
    link = tmp_path / "guide.md"
    try:
        link.symlink_to(real)
    except OSError as exc:  # Windows without Developer Mode or admin cannot create symlinks
        pytest.skip(f"cannot create a symlink here ({exc}); the URL case above was checked")
    with pytest.raises(ValueError, match=x["symlink_error"]):
        create_repair_proposal(store, source_path=link, **common)
    assert real.read_text(encoding="utf-8") == i["base"]


@case("MD-AB-07")
def test_policy_swapped_after_approval_is_unresolved(rt_case, tmp_path):
    i, x = rt_case["input"], rt_case["expected"]
    source, store, record = propose(tmp_path, i["base"], i["candidate"])
    approve_workflow(store, record.workflow_id, i["approver"])
    swapped = MaintainDocsPolicy(protected_contracts=tuple(i["swapped_policy_contracts"]))

    result = resume_workflow(store, record.workflow_id, runner=KaneTestRunner(), policy=swapped, apply=True)
    assert result.state == x["state"]
    assert x["reason_contains"] in result.validation["reason"]
    assert source.read_text(encoding="utf-8") == i["base"]


def _refused_and_allowed(rt_case, tmp_path, check_refused):
    """Each refused pair is rejected with a handoff and can't be approved; each allowed one awaits approval."""
    i, x = rt_case["input"], rt_case["expected"]
    for n, item in enumerate(i["refused"]):
        base = item["base"]
        source, store, record = propose(tmp_path / f"refused-{n}", base, item["candidate"],
                                        target=_prose_target(base, 1, 1), plan="Match the app.")
        assert record.state == x["refused_state"], item
        authority = record.proposal["authority"]
        check_refused(item, authority["violations"])
        assert authority["implementation_handoff"]["kind"] == x["handoff_kind"]
        with pytest.raises(ApprovalError, match=x["approve_error"]):
            approve_workflow(store, record.workflow_id, "reviewer")
        assert source.read_text(encoding="utf-8") == base
    # negative control: the same gate still lets a descriptive repair through
    for n, item in enumerate(i["allowed"]):
        base = item["base"]
        _, _, record = propose(tmp_path / f"allowed-{n}", base, item["candidate"], target=_prose_target(base, 1, 1))
        assert record.state == x["allowed_state"], (item, record.proposal["authority"]["violations"])


@case("MD-AB-08")
def test_negated_or_excepted_promise_is_refused(rt_case, tmp_path):
    def check(item, violations):
        assert any(item["violation"] in v for v in violations), (item, violations)

    _refused_and_allowed(rt_case, tmp_path, check)


@case("MD-AB-09")
def test_changed_unit_or_number_word_in_a_promise_is_refused(rt_case, tmp_path):
    def check(item, violations):
        assert rt_case["expected"]["violation_contains"] in violations, (item, violations)

    _refused_and_allowed(rt_case, tmp_path, check)


class _OlderAuthority(MaintainDocsPolicy):
    """An authority that allowed every patch: the same fields, so the same policy digest."""

    def authorize(self, **kwargs):
        decision = super().authorize(**kwargs)
        return dataclasses.replace(decision, allowed=True, reason="authorized for Maintain Docs", violations=[],
                                   implementation_handoff=None)


@case("MD-AB-10")
def test_approval_under_an_older_authority_is_rejudged_before_apply(rt_case, tmp_path):
    i, x = rt_case["input"], rt_case["expected"]
    older = _OlderAuthority()
    assert older.digest == MaintainDocsPolicy().digest

    def approved(name, candidate):
        source, store, record = propose(tmp_path / name, i["base"], candidate, policy=older)
        assert record.state == "awaiting_approval"
        approve_workflow(store, record.workflow_id, i["approver"])
        command = candidate.split("```bash\n", 1)[1].split("\n```", 1)[0]
        return source, store, record, ApprovedRecipeExecutor({command: [sys.executable, "-c", "print('ready')"]})

    # Validation re-judges the patch before running anything.
    source, store, record, ready = approved("resume", i["candidate"])
    result = resume_workflow(store, record.workflow_id, runner=KaneTestRunner(), recipe_executor=ready, apply=True)
    assert result.state == x["resume_state"]
    assert x["reason_contains"] in result.validation["reason"]
    assert source.read_text(encoding="utf-8") == i["base"]

    # A record validated under the older authority is re-judged at --apply.
    source, store, record, ready = approved("promote", i["candidate"])
    validated = resume_workflow(store, record.workflow_id, runner=KaneTestRunner(), recipe_executor=ready, policy=older)
    assert validated.state == x["validated_state"]
    with pytest.raises(WorkflowError, match=x["promote_error"]):
        promote_workflow(store, record.workflow_id)
    with pytest.raises(WorkflowError, match=x["promote_error"]):
        resume_workflow(store, record.workflow_id, runner=KaneTestRunner(), recipe_executor=ready, apply=True)
    assert store.get(record.workflow_id).state == x["validated_state"]
    assert source.read_text(encoding="utf-8") == i["base"]

    # negative control: a descriptive repair approved the same way still promotes
    source, store, record, ready = approved("control", i["control_candidate"])
    resume_workflow(store, record.workflow_id, runner=KaneTestRunner(), recipe_executor=ready, policy=older)
    assert promote_workflow(store, record.workflow_id).state == x["control_state"]
    assert source.read_text(encoding="utf-8") == i["control_candidate"]


def _digest_correct_approval(record, actor: str, approved_at: str | None = None) -> dict:
    """An approval verify_approval accepts (bindings and digest right) that `attest approve` didn't write."""
    approval = {**approval_bindings(record.proposal), "actor": actor,
                "approved_at": approved_at or datetime.now(timezone.utc).isoformat()}
    approval["approval_digest"] = hashlib.sha256(canonical_json(approval).encode("utf-8")).hexdigest()
    return approval


def _write_row(store, workflow_id: str, state: str, approval: dict) -> None:
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE workflows SET state = ?, approval_json = ? WHERE workflow_id = ?",
                   (state, canonical_json(approval), workflow_id))


def _forged_approval(tmp_path, i, shape: str):
    """A workflow in `validating` whose approval verify_approval accepts but no approve event of its own backs."""
    candidate = i["denied_candidate"] if shape == "denied_offer" else i["candidate"]
    source, store, record = propose(tmp_path, i["base"], candidate)
    wid = record.workflow_id
    if shape == "denied_offer":
        assert record.state == "rejected"
        _write_row(store, wid, "validating", _digest_correct_approval(record, i["approver"]))
    elif shape == "row_written":
        _write_row(store, wid, "validating", _digest_correct_approval(record, i["approver"]))
    elif shape == "unnamed_event":
        store.transition(wid, "awaiting_approval", "validating", approval=_digest_correct_approval(record, i["approver"]))
    elif shape == "twin_swapped":
        claims = fences_by_regex(ingest_text(i["base"], source=str(source)))
        twin = create_repair_proposal(store, source_path=source, base_text=i["base"], candidate_text=candidate,
                                      diff=make_diff(i["base"], candidate), target_claim=claims[0], baseline_claims=claims,
                                      plan="Repair only c01; preserve all promises.")
        assert twin.proposal["proposal_digest"] == record.proposal["proposal_digest"]
        approve_workflow(store, wid, i["approver"])
        _write_row(store, wid, "validating", approve_workflow(store, twin.workflow_id, "intruder").approval)
    elif shape == "policy_actor":
        approve_workflow(store, wid, "policy:auto-approve")
    elif shape == "backdated":
        approval = _digest_correct_approval(record, i["approver"], approved_at="2020-01-01T00:00:00+00:00")
        store.transition(wid, "awaiting_approval", "validating", approval=approval,
                         event={"actor": approval["actor"], "approval_digest": approval["approval_digest"]})
    else:
        raise AssertionError(f"unknown forgery shape {shape!r}")
    verify_approval(store.get(wid))  # digest-correct: the older gate accepts every one
    return source, store, wid


def _marking_recipes(marker: Path) -> ApprovedRecipeExecutor:
    """The candidate's command, approved as a recipe that leaves a marker when it runs."""
    return ApprovedRecipeExecutor({"echo ready && exit 0": [sys.executable, "-c", f"open({str(marker)!r}, 'w').close()"]})


@case("MD-AB-11")
def test_approval_attest_approve_did_not_record_is_refused_before_validation(rt_case, tmp_path):
    i, x = rt_case["input"], rt_case["expected"]
    for item in i["forgeries"]:
        assert item["code"] in DOCS_APPROVAL_CODES
        root = tmp_path / item["shape"]
        source, store, wid = _forged_approval(root, i, item["shape"])
        marker = root / "ran"
        result = resume_workflow(store, wid, runner=KaneTestRunner(), recipe_executor=_marking_recipes(marker), apply=True)
        assert result.state == x["resume_state"], item
        assert result.validation["reason"].startswith(f"{x['reason_prefix']} ({item['code']})"), (item, result.validation)
        assert x["nothing_ran"] and "statuses" not in result.validation and not marker.exists(), item
        assert source.read_text(encoding="utf-8") == i["base"], item

    # negative control: the same candidate, approved with `attest approve`, validates and promotes
    source, store, record = propose(tmp_path / "control", i["base"], i["candidate"])
    approve_workflow(store, record.workflow_id, i["approver"])
    marker = tmp_path / "control" / "ran"
    promoted = resume_workflow(store, record.workflow_id, runner=KaneTestRunner(), recipe_executor=_marking_recipes(marker), apply=True)
    assert promoted.state == x["control_state"] and marker.exists()
    assert source.read_text(encoding="utf-8") == i["candidate"]


@case("MD-AB-12")
def test_record_validated_without_the_approve_event_is_refused_at_apply(rt_case, tmp_path, monkeypatch):
    from typer.testing import CliRunner

    import attest.cli as cli_module

    i, x = rt_case["input"], rt_case["expected"]
    monkeypatch.setattr(cli_module, "_runner", lambda *args, **kwargs: KaneTestRunner())
    for item in i["forgeries"]:
        root = tmp_path / item["shape"]
        source, store, wid = _forged_approval(root, i, item["shape"])
        with monkeypatch.context() as older:
            # an Attest whose gate stopped at verify_approval validated this record
            older.setattr(workflow_module, "verify_docs_approval", lambda store, record: verify_approval(record))
            validated = resume_workflow(store, wid, runner=KaneTestRunner(), recipe_executor=_marking_recipes(root / "ran"))
        assert validated.state == x["validated_state"], item

        error = x["promote_error"].format(code=item["code"])
        with pytest.raises(WorkflowError, match=error):
            promote_workflow(store, wid)
        with pytest.raises(WorkflowError, match=error):
            resume_workflow(store, wid, runner=KaneTestRunner(), apply=True)
        out = CliRunner().invoke(cli_module.app, ["resume", wid, "--apply", "--home", str(store.path.parent)])
        assert out.exit_code == x["cli_exit"] and x["cli_output"].format(code=item["code"]) in out.output, (item, out.output)
        assert store.get(wid).state == x["validated_state"], item
        assert source.read_text(encoding="utf-8") == i["base"], item

    # negative control: a record validated after `attest approve` still promotes
    source, store, record = propose(tmp_path / "control", i["base"], i["candidate"])
    approve_workflow(store, record.workflow_id, i["approver"])
    resume_workflow(store, record.workflow_id, runner=KaneTestRunner(), recipe_executor=_marking_recipes(tmp_path / "control" / "ran"))
    assert promote_workflow(store, record.workflow_id).state == x["control_state"]
    assert source.read_text(encoding="utf-8") == i["candidate"]


# -- DS: digest swap / identity confusion ----------------------------------------------


@case("MD-DS-01")
def test_patched_content_under_original_digest_is_rejected(rt_case, tmp_path):
    i, x = rt_case["input"], rt_case["expected"]
    original = ingest_text(i["base"], source=str(tmp_path / "guide.md"))
    patched = ingest_text(i["candidate"], source=str(tmp_path / "guide.md"))
    reused = patched.model_copy(update={"sha256": original.sha256})
    with pytest.raises(CompileError, match=x["compile_error"]):
        compile_doc(reused, use_cache=False)

    version = SourceVersion.from_content("guide.md", i["base"])
    with pytest.raises(ValidationError, match=x["proposal_error"]):
        RepairProposal(
            snapshot_id=version.version_id,
            source_version_id=version.version_id,
            base_content_sha256=version.content_sha256,
            proposed_content_sha256=version.content_sha256,
            patch=make_diff(i["base"], i["candidate"]),
        )


@case("MD-DS-02")
def test_source_drift_before_proposal_is_refused(rt_case, tmp_path):
    i, x = rt_case["input"], rt_case["expected"]
    source = tmp_path / "guide.md"
    source.write_text(i["drifted_source"], encoding="utf-8")
    claims = fences_by_regex(ingest_text(i["base"], source=str(source)))
    with pytest.raises(ValueError, match=x["proposal_error"]):
        create_repair_proposal(
            SQLiteWorkflowStore(tmp_path / "workflow.sqlite3"),
            source_path=source, base_text=i["base"], candidate_text=i["candidate"],
            diff=make_diff(i["base"], i["candidate"]), target_claim=claims[0], baseline_claims=claims, plan="Repair c01.",
        )
    assert source.read_text(encoding="utf-8") == i["drifted_source"]


@case("MD-DS-03")
def test_candidate_with_smuggled_line_is_not_the_patch_result(rt_case, tmp_path):
    i, x = rt_case["input"], rt_case["expected"]
    source = tmp_path / "guide.md"
    source.write_text(i["base"], encoding="utf-8")
    claims = fences_by_regex(ingest_text(i["base"], source=str(source)))
    with pytest.raises(ValueError, match=x["proposal_error"]):
        create_repair_proposal(
            SQLiteWorkflowStore(tmp_path / "workflow.sqlite3"),
            source_path=source, base_text=i["base"], candidate_text=i["candidate"] + i["smuggled_line"],
            diff=make_diff(i["base"], i["candidate"]), target_claim=claims[0], baseline_claims=claims, plan="Repair c01.",
        )


@case("MD-DS-04")
def test_tampered_stored_proposal_cannot_be_approved(rt_case, tmp_path):
    i, x = rt_case["input"], rt_case["expected"]
    source, store, record = propose(tmp_path, i["base"], i["candidate"])
    tampered = {**record.proposal, "candidate_digest": i["swapped_candidate_digest"]}
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE workflows SET proposal_json = ? WHERE workflow_id = ?", (canonical_json(tampered), record.workflow_id))
    assert proposal_digest(store.get(record.workflow_id).proposal) != tampered["proposal_digest"]

    with pytest.raises(ApprovalError, match=x["approve_error"]):
        approve_workflow(store, record.workflow_id, "reviewer")
    assert store.get(record.workflow_id).state == x["state"]
    assert source.read_text(encoding="utf-8") == i["base"]


@case("MD-DS-05")
def test_crlf_resave_after_approval_is_unresolved(rt_case, tmp_path):
    i, x = rt_case["input"], rt_case["expected"]
    source, store, record = propose(tmp_path, i["base"], i["candidate"])
    approve_workflow(store, record.workflow_id, i["approver"])
    crlf = i["base"].replace("\n", "\r\n").encode("utf-8")
    source.write_bytes(crlf)
    assert sha256_of(crlf.decode("utf-8")) == record.proposal["base_digest"]  # normalized text is unchanged

    result = resume_workflow(store, record.workflow_id, runner=KaneTestRunner(), apply=True)
    assert result.state == x["state"]
    assert x["reason_contains"] in result.validation["reason"]
    assert source.read_bytes() == crlf


# -- SIM: simulation or fake evidence --------------------------------------------------


@case("MD-SIM-01")
def test_simulation_resume_cannot_validate(rt_case, tmp_path):
    i, x = rt_case["input"], rt_case["expected"]
    source, store, record = propose(tmp_path, i["base"], i["candidate"])
    approve_workflow(store, record.workflow_id, i["approver"])
    with pytest.raises(WorkflowError, match=x["error"]):
        resume_workflow(store, record.workflow_id, runner=KaneTestRunner(), simulation=True, apply=True)
    assert store.get(record.workflow_id).state == x["state"]
    assert source.read_text(encoding="utf-8") == i["base"]


@case("MD-SIM-02")
def test_fake_runner_resume_cannot_validate(rt_case, tmp_path):
    i, x = rt_case["input"], rt_case["expected"]
    source, store, record = propose(tmp_path, i["base"], i["candidate"])
    approve_workflow(store, record.workflow_id, i["approver"])
    with pytest.raises(WorkflowError, match=x["error"]):
        resume_workflow(store, record.workflow_id, runner=FakeBrowserRunner(), apply=True)
    assert store.get(record.workflow_id).state == x["state"]
    assert source.read_text(encoding="utf-8") == i["base"]


@case("MD-SIM-03")
def test_fuzzy_similarity_never_proves_a_different_number(rt_case):
    x = rt_case["expected"]
    for expects, seen in rt_case["input"]["pairs"]:
        claim = _browser_claim(expects)
        observation = BrowserObservation(status="passed", observed={"c01_label": seen}, note="kane: step passed, authored")
        verdict = judge(claim, Evidence(claim_id="c01", kind="browser", observation=observation))
        assert verdict.status == x["status"], (expects, seen, verdict.label_match)


@case("MD-SIM-04")
def test_simulated_evidence_cannot_be_marked_complete(rt_case):
    from uuid import uuid4

    from attest.models import AuthorityState, ComparisonState, EvidenceState, ExecutionState, RepairState, VerifiedRunReceipt

    version = SourceVersion.from_content("guide.md", rt_case["input"]["content"])
    evidence = ReceiptEvidence(kind="browser", source_version_id=version.version_id, content_sha256=version.content_sha256,
                               complete=True, simulated=True)
    with pytest.raises(ValidationError, match=rt_case["expected"]["error"]):
        VerifiedRunReceipt(
            snapshot_id=uuid4(), source_version_id=version.version_id, content_sha256=version.content_sha256,
            execution_state=ExecutionState.SUCCEEDED, comparison_state=ComparisonState.MATCH,
            authority_state=AuthorityState.ALLOWED, evidence_state=EvidenceState.COMPLETE,
            repair_state=RepairState.NOT_REQUIRED, evidence=(evidence,),
        )


# -- SCH: malformed or hostile schema ------------------------------------------------


@case("MD-SCH-01")
def test_unresolved_required_browser_claim_fails_closed(rt_case, tmp_path):
    doc = ingest_text(rt_case["input"]["doc"], source=str(tmp_path / "setup.md"))
    with pytest.raises(CompileError, match=rt_case["expected"]["error"]):
        compile_doc(doc, use_cache=False)


@case("MD-SCH-02")
def test_malformed_diffs_are_rejected(rt_case):
    for item in rt_case["input"]["diffs"]:
        problems = validate_diff_scope(item["diff"], "guide.md", 1, 1)
        assert any(item["problem"] in p for p in problems), (item, problems)


@case("MD-SCH-03")
def test_escaping_diff_and_evidence_paths_are_rejected(rt_case, tmp_path):
    i, x = rt_case["input"], rt_case["expected"]
    for path in i["diff_paths"]:
        diff = f"--- {path}\n+++ {path}\n@@ -1 +1 @@\n-old\n+new\n"
        problems = validate_diff_scope(diff, "guide.md", 1, 1)
        assert any(x["diff_problem"] in p for p in problems), (path, problems)
    for path in i["evidence_paths"]:
        with pytest.raises(ValueError, match=x["evidence_error"]):
            safe_pack_path(tmp_path, path)


@case("MD-SCH-04")
def test_hostile_policy_yaml_is_rejected(rt_case, tmp_path):
    doc = tmp_path / "guide.md"
    doc.write_text("Guide.\n", encoding="utf-8")
    for n, text in enumerate(rt_case["input"]["policies"]):
        path = tmp_path / f"policy-{n}.yaml"
        path.write_text(text, encoding="utf-8")
        with pytest.raises(ValueError, match=rt_case["expected"]["error"]):
            load_policy(str(doc), path)


@case("MD-SCH-05")
def test_hostile_verdict_claim_and_evidence_fields_are_rejected(rt_case):
    span = Span(start_line=1, end_line=1, text="x")
    shell = Evidence(claim_id="c01", kind="shell", exit_code=0)
    hostile = [
        lambda: Verdict(claim_id="c01", status="proven", reason="r", evidence=shell, label_match=1.5),
        lambda: Verdict(claim_id="c01", status="verified", reason="r", evidence=shell),
        lambda: Claim(id="c1", kind="prose", span=span),
        lambda: Claim(id="c01", kind="prose", span=span, expects="anything"),
        lambda: Span(start_line=3, end_line=1, text="x"),
        lambda: Evidence(claim_id="c01", kind="shell"),
        lambda: Evidence.model_validate({"claim_id": "c01", "kind": "shell", "exit_code": 0, "verified": True}),
    ]
    for build in hostile:
        with pytest.raises(ValidationError):
            build()


# -- COST: cost or time blowup -------------------------------------------------------


@case("MD-COST-01")
def test_agent_without_diff_is_called_at_most_loop_attempts(rt_case, tmp_path, monkeypatch):
    i, x = rt_case["input"], rt_case["expected"]
    monkeypatch.chdir(tmp_path)
    doc = tmp_path / "loop.md"
    doc.write_text(i["doc"], encoding="utf-8")
    counter = tmp_path / "calls.txt"
    agent = tmp_path / "agent.py"
    agent.write_text(
        f"import sys\nsys.stdin.read()\nopen({str(counter)!r}, 'a').write('x')\nprint('I could not work out a fix.')\n",
        encoding="utf-8",
    )
    home = tmp_path / "home"
    result = prove(str(doc), runner=KaneTestRunner(), home=home)
    run_loop(result, KaneTestRunner(), home=home, rules=Rules(loop_attempts=i["loop_attempts"]),
             agent_cmd=f'"{sys.executable}" "{agent}"')

    assert len(counter.read_text()) == x["agent_calls"]
    receipt = json.loads(next((home / "receipts").glob("*.json")).read_text(encoding="utf-8"))
    assert receipt["disposition"] == x["disposition"]
    assert doc.read_text(encoding="utf-8") == i["doc"]


@case("MD-COST-02")
def test_compiler_retry_is_bounded(rt_case, tmp_path):
    x = rt_case["expected"]

    class OmittingLLM:
        calls = 0

        def parse_claims(self, system, user):
            OmittingLLM.calls += 1
            return ClaimSet(claims=[])

    doc = ingest_text(rt_case["input"]["doc"], source=str(tmp_path / "setup.md"))
    with pytest.raises(CompileError, match=x["error"]):
        compile_doc(doc, llm=OmittingLLM(), use_cache=False)
    assert OmittingLLM.calls == x["llm_calls"]

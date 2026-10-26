import hashlib
import json
import sys
import textwrap
from pathlib import Path

from typer.testing import CliRunner

from attest.cli import app
from attest.pipeline import prove
from attest.reconcile_spec import ReconcileRequest, ReconcileSpec, SourceDocument, protected_document, write_handoff
from attest.runner import FakeBrowserRunner
from attest.verdict import Rules


class KaneTestRunner(FakeBrowserRunner):
    """Test runner for repair mechanics; live evidence comes from Kane in production."""

    name = "kane"


def _request(**overrides):
    conflict = {
        "claim_id": "c01",
        "protected_requirement_id": "SEC-4",
        "authority_reference": "contracts/security.md#SEC-4",
        "protected_text": "The application must require multi-factor authentication.",
        "observed_text": "The application accepted a password-only sign-in.",
        "candidate_side": "app_wrong",
        "evidence": [
            {
                "kind": "runtime",
                "pointer": "artifacts/run-17/sign-in.png",
                "description": "Observed sign-in flow",
            }
        ],
    }
    conflict.update(overrides.pop("conflict", {}))
    return {
        "source_doc": "docs/requirements.md",
        "authority_status": "present",
        "conflicts": [conflict],
        **overrides,
    }


def _sources(request: ReconcileRequest) -> dict[str, SourceDocument]:
    text = "".join(f"{c.protected_requirement_id}: {c.protected_text}\n" for c in request.conflicts)
    document = SourceDocument(text, hashlib.sha256(text.encode("utf-8")).hexdigest())
    return {protected_document(c, request.source_doc): document for c in request.conflicts}


def test_reconcile_spec_writes_handoff_with_evidence_pointers(tmp_path):
    request = ReconcileRequest.model_validate(_request())
    handoff = ReconcileSpec().diagnose(request, sources=_sources(request))
    markdown, machine_json = write_handoff(handoff, tmp_path / "handoff")

    data = json.loads(machine_json.read_text(encoding="utf-8"))
    assert data["policy"] == "reconcile_spec"
    assert data["execution_state"] == "external_observation"
    assert data["comparison_state"] == "submitted_conflict"
    assert data["authority_state"] == "present"
    assert data["evidence_state"] == "provided"
    assert data["repair_state"] == "handoff"
    assert data["source_state"] == "matched"
    assert data["handoff_state"] == "ready"
    assert data["conflicts"][0]["claim_id"] == "c01"
    assert data["conflicts"][0]["candidate_side"] == "app_wrong"
    assert data["conflicts"][0]["recommended_owner"] == "application owner"
    assert data["conflicts"][0]["evidence"][0]["pointer"] == "artifacts/run-17/sign-in.png"
    text = markdown.read_text(encoding="utf-8")
    assert "Observed application behavior (evidence, not authority)" in text
    assert "artifacts/run-17/sign-in.png" in text
    assert "Protected requirement text" in text


def test_missing_or_conflicting_authority_stops_at_handoff():
    request = ReconcileRequest.model_validate(_request(authority_status="conflicting"))
    result = ReconcileSpec().diagnose(request)

    assert result.authority_state == "conflicting"
    assert result.repair_state == "handoff"
    assert "human review" in result.authority_note
    assert ReconcileSpec.can_apply_protected_text is False
    assert ReconcileSpec.can_promote_protected_text is False


def test_kane_receipt_is_observation_not_reconciliation_authority():
    request = ReconcileRequest.model_validate(
        _request(
            conflict={
                "evidence": [
                    {
                        "kind": "receipt",
                        "pointer": "artifacts/kane/run-17/receipt.json",
                        "description": "Attest run receipt",
                    }
                ]
            }
        )
    )

    result = ReconcileSpec().diagnose(request, sources=_sources(request))
    serialized = result.model_dump()

    assert result.execution_state == "external_observation"
    assert result.authority_state == "present"
    assert result.repair_state == "handoff"
    assert serialized["conflicts"][0]["evidence"][0]["kind"] == "receipt"
    assert "reconciled" not in serialized


def test_reconcile_spec_refuses_apply_of_weakened_protected_requirement(tmp_path):
    contract = tmp_path / "security-contract.md"
    original = "The application must require multi-factor authentication.\n"
    contract.write_text(original, encoding="utf-8")
    request_path = tmp_path / "diagnosis.json"
    request_path.write_text(
        json.dumps(
            _request(
                conflict={
                    "proposed_protected_text": "The application may skip multi-factor authentication."
                }
            )
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        ["reconcile-spec", str(request_path), "--out", str(tmp_path / "out"), "--apply"],
    )

    assert result.exit_code == 2
    assert "refused" in result.output
    assert contract.read_text(encoding="utf-8") == original
    data = json.loads((tmp_path / "out" / "reconcile-spec.json").read_text(encoding="utf-8"))
    assert data["repair_state"] == "refused"
    assert data["conflicts"][0]["protected_edit_refused"] is True
    assert data["conflicts"][0]["proposed_protected_text"].startswith("The application may")
    assert "may skip" in (tmp_path / "out" / "reconcile-spec.md").read_text(encoding="utf-8")


def test_maintain_docs_descriptive_repair_path_still_allows_apply(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "guide.md"
    source.write_text(
        "# Guide\n\nRun the readiness check:\n\n```bash\necho ready && exit 1\n```\n",
        encoding="utf-8",
    )
    agent = tmp_path / "doc_agent.py"
    agent.write_text(
        textwrap.dedent(
            """\
            import difflib, re, sys
            from pathlib import Path
            brief = sys.stdin.read()
            name = re.search(r"Edit only `([^`]+)`", brief).group(1)
            old = Path(name).read_text(encoding="utf-8")
            new = old.replace("exit 1", "exit 0")
            print("```diff\\n" + "".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True), fromfile="a/" + name, tofile="b/" + name)) + "```")
            """
        ),
        encoding="utf-8",
    )
    result = prove(
        str(source),
        runner=KaneTestRunner(),
        home=tmp_path / "home",
        keep_servers=True,
    )
    assert result.by_id["c01"].status == "broken"

    from attest.approvals import approve_workflow
    from attest.loop import run_loop
    from attest.storage import SQLiteWorkflowStore
    from attest.workflow import resume_workflow

    proposed = run_loop(
        result,
        KaneTestRunner(),
        home=tmp_path / "home",
        rules=Rules(),
        agent_cmd=f'"{sys.executable}" "{agent}"',
    )
    assert proposed.by_id["c01"].status == "broken"  # proposals are not evidence
    assert "exit 1" in source.read_text(encoding="utf-8")

    store = SQLiteWorkflowStore(tmp_path / "home" / "workflow.sqlite3")
    workflow_id = proposed.proposals["c01"]
    approve_workflow(store, workflow_id, "maintainer")
    from attest.execution.recipes import ApprovedRecipeExecutor

    # resume validation is recipes-only; the repaired command must be an approved recipe
    ready = ApprovedRecipeExecutor({"echo ready && exit 0": [sys.executable, "-c", "print('ready')"]})
    promoted = resume_workflow(store, workflow_id, runner=KaneTestRunner(), apply=True, recipe_executor=ready)

    assert promoted.state == "promoted"
    assert "echo ready && exit 0" in source.read_text(encoding="utf-8")
    assert "exit 1" not in source.read_text(encoding="utf-8")

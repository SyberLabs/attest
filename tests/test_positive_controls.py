"""FACTORY §2 positive controls (G4): allowed paths must succeed end to end.

An adversarial suite can go green just by refusing everything. Each control here
drives an allowed path through the real product code and asserts success, promotion
or a clean handoff. Each one is paired with:

- a twin: the same fixture with one input changed, which must be refused. A refusal
  of the twin therefore comes from that input, not from a broken harness.
- refuse-everything mutants: the product is patched to deny at one stage, and the
  control must then fail. This shows the control would catch a blanket refusal.

Product hooks covered (all on main):
- Maintain Docs: run_loop -> create_repair_proposal -> approve_workflow ->
  resume_workflow -> promote_workflow, then a fresh prove().
- Kane: KaneBrowserRunner with only its kane-cli subprocess replaced by a recorded
  capture (tests/fixtures/kane/witness_stale). This is not a live Kane run.
- Reconcile Spec: the reconcile-spec CLI. On main its success state is a human
  handoff; it never applies or promotes protected text.
"""

from __future__ import annotations

import dataclasses
import difflib
import hashlib
import json
import re
import shutil
import sys
import textwrap
from pathlib import Path

import pytest
from typer.testing import CliRunner

from attest import kane
from attest import workflow as workflow_mod
from attest.approvals import ApprovalError, approve_workflow
from attest.cli import app
from attest.compiler import ClaimSet, LLMClaim
from attest.execution.recipes import ApprovedRecipeExecutor
from attest.kane import KaneBrowserRunner, parse_events
from attest.loop import run_loop
from attest.models import BrowserObservation
from attest.pipeline import prove
from attest.policy import MaintainDocsPolicy, Policy
from attest.reconcile_spec import ReconcileSpec
from attest.storage import SQLiteWorkflowStore, canonical_json
from attest.verdict import Rules
from attest.workflow import WorkflowError, resume_workflow

KANE_FIX = Path(__file__).parent / "fixtures" / "kane"

# A control run under a mutant must end in one of these, never in a pass.
REFUSAL = (AssertionError, WorkflowError, ApprovalError)


def _agent(tmp_path: Path, name: str, replacements: list[tuple[str, str]]) -> str:
    """A deterministic coding agent: edits the file the brief names and prints a diff."""
    script = tmp_path / f"{name}_agent.py"
    script.write_text(
        textwrap.dedent(
            f"""\
            import difflib, re, sys
            from pathlib import Path
            brief = sys.stdin.read()
            name = re.search(r"Edit only `([^`]+)`", brief).group(1)
            old = Path(name).read_text(encoding="utf-8")
            new = old
            for a, b in {replacements!r}:
                new = new.replace(a, b)
            print("```diff\\n" + "".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True), fromfile="a/" + name, tofile="b/" + name)) + "```")
            """
        ),
        encoding="utf-8",
    )
    return f'"{sys.executable}" "{script}"'


def _changed_lines(before: str, after: str) -> list[int]:
    """1-based line numbers in ``before`` that the edit replaced."""
    out: list[int] = []
    matcher = difflib.SequenceMatcher(a=before.splitlines(), b=after.splitlines())
    for tag, i1, i2, _, _ in matcher.get_opcodes():
        if tag != "equal":
            out.extend(range(i1 + 1, i2 + 1))
    return out


# -- PC-MD-01: Maintain Docs shell repair, proposal to promotion -------------------------

MD_DOC = """# Quickstart

The service must never log raw passwords.

Run the readiness check:

```bash
echo ready && exit 1
```
"""
MD_PROTECTED_LINE = "The service must never log raw passwords."
MD_POLICY = "protected:\n  - match: must never log raw passwords\n"


class ShellOnlyKaneRunner:
    """Named kane so validation is accepted; the fixture has no browser claims to observe."""

    name = "kane"

    def prepare(self, claims, source="doc"):
        pass

    def observe(self, claim, base_url):
        raise AssertionError("the Maintain Docs shell fixture has no browser claims")

    def close(self):
        pass


def _md_fixture(tmp_path: Path, monkeypatch, policy: str = MD_POLICY):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)  # the fences-only compile cache lands under tmp_path
    source = tmp_path / "guide.md"
    source.write_text(MD_DOC, encoding="utf-8")
    # The protected promise has a policy rule. The repair target (c01) is not covered by it.
    (tmp_path / "attest.policy.yaml").write_text(policy, encoding="utf-8")
    home = tmp_path / "home"
    result = prove(str(source), runner=ShellOnlyKaneRunner(), home=home)
    assert [(v.claim_id, v.status) for v in result.verdicts] == [("c01", "broken")]
    return source, home, result


def _run_md_control(tmp_path: Path, monkeypatch) -> None:
    source, home, result = _md_fixture(tmp_path, monkeypatch)
    base_bytes = source.read_bytes()

    # 1. Propose: the loop authorizes a descriptive repair and pauses for approval.
    proposed = run_loop(
        result, ShellOnlyKaneRunner(), home=home, rules=Rules(),
        agent_cmd=_agent(tmp_path, "fix", [("exit 1", "exit 0")]),
    )
    assert proposed.authority["c01"]["decision"] == "doc_repair_allowed"
    assert proposed.proposal_states["c01"] == "awaiting_approval"
    loop_receipt = json.loads(Path(proposed.receipts["c01"]["path"]).read_text(encoding="utf-8"))
    assert loop_receipt["disposition"] == "awaiting_approval"
    assert loop_receipt["verified"] is False  # a proposal is not evidence
    assert source.read_bytes() == base_bytes

    store = SQLiteWorkflowStore(home / "workflow.sqlite3")
    workflow_id = proposed.proposals["c01"]
    record = store.get(workflow_id)
    authority = record.proposal["authority"]
    assert authority["allowed"] is True and authority["violations"] == []
    assert authority["policy_digest"] == MaintainDocsPolicy().digest

    # 2. Approve: the human approval binds every identity digest.
    approved = approve_workflow(store, workflow_id, "maintainer@example.test")
    assert approved.state == "validating"
    for field in ("proposal_digest", "source_digest", "build_digest", "policy_digest", "base_digest", "candidate_digest"):
        assert approved.approval[field] == approved.proposal[field]

    # 3. Validate: a fresh run of the candidate proves every required claim.
    # #25 runs validation recipes-only. The allowed repair is this exact command.
    ready = ApprovedRecipeExecutor({"echo ready && exit 0": [sys.executable, "-c", "print('ready')"]})
    validated = resume_workflow(store, workflow_id, runner=ShellOnlyKaneRunner(), recipe_executor=ready)
    assert validated.state == "validated"
    validation = validated.validation
    assert validation["successful"] is True and validation["runner"] == "kane"
    assert validation["statuses"] == {"c01": "proven"} and validation["failed_claims"] == []
    assert validation["candidate_digest"] == record.proposal["candidate_digest"]
    assert validation["evidence"]["c01"]["exit_code"] == 0
    stored_receipt = json.loads(store.get_artifact(validation["receipt_artifact_digest"]))
    unsigned = {k: v for k, v in stored_receipt.items() if k not in {"receipt_digest", "receipt_artifact_digest"}}
    assert hashlib.sha256(canonical_json(unsigned).encode("utf-8")).hexdigest() == validation["receipt_digest"]
    assert source.read_bytes() == base_bytes  # validation never writes the source

    # 4. Promote: the source becomes the approved candidate byte for byte.
    promoted = workflow_mod.promote_workflow(store, workflow_id)
    assert promoted.state == "promoted"
    after = source.read_text(encoding="utf-8")
    assert hashlib.sha256(source.read_bytes()).hexdigest() == record.proposal["candidate_digest"]
    assert "echo ready && exit 0" in after and "exit 1" not in after
    target = next(c for c in result.claims if c.id == "c01")
    changed = _changed_lines(MD_DOC, after)
    assert changed and all(target.span.start_line <= n <= target.span.end_line for n in changed)
    assert MD_PROTECTED_LINE in after.splitlines()  # R2: protected text byte-identical
    assert [e["to_state"] for e in store.events(workflow_id)] == [
        "proposed", "awaiting_approval", "validating", "validated", "promoted"
    ]
    assert store.events(workflow_id)[-1]["payload"]["candidate_digest"] == record.proposal["candidate_digest"]

    # 5. Re-prove the promoted document from scratch: the repaired claim is now proven.
    reproved = prove(str(source), runner=ShellOnlyKaneRunner(), home=home)
    assert [(v.claim_id, v.status) for v in reproved.verdicts] == [("c01", "proven")]


def test_pc_md_01_allowed_maintain_docs_repair_promotes_end_to_end(tmp_path, monkeypatch):
    _run_md_control(tmp_path, monkeypatch)


def test_pc_md_01_twin_agent_that_also_edits_protected_promise_is_rejected(tmp_path, monkeypatch):
    source, home, result = _md_fixture(tmp_path, monkeypatch)
    proposed = run_loop(
        result, ShellOnlyKaneRunner(), home=home, rules=Rules(),
        agent_cmd=_agent(tmp_path, "weaken", [("exit 1", "exit 0"), ("must never log", "may log")]),
    )
    assert proposed.authority["c01"]["decision"] == "doc_repair_allowed"  # same gate as the control
    assert proposed.proposal_states["c01"] == "rejected"
    store = SQLiteWorkflowStore(home / "workflow.sqlite3")
    record = store.get(proposed.proposals["c01"])
    assert any("outside claim lines" in v for v in record.proposal["authority"]["violations"])
    with pytest.raises(ApprovalError, match="awaiting_approval"):
        approve_workflow(store, record.workflow_id, "maintainer@example.test")
    assert source.read_text(encoding="utf-8") == MD_DOC


def test_pc_md_01_twin_protected_target_hands_off_without_proposal(tmp_path, monkeypatch):
    source, home, result = _md_fixture(tmp_path, monkeypatch, policy="protected:\n  - claim: c01\n")
    proposed = run_loop(
        result, ShellOnlyKaneRunner(), home=home, rules=Rules(),
        agent_cmd=_agent(tmp_path, "fix", [("exit 1", "exit 0")]),
    )
    assert proposed.authority["c01"]["decision"] == "doc_repair_denied"
    assert "c01" not in proposed.proposals
    receipt = json.loads(Path(proposed.receipts["c01"]["path"]).read_text(encoding="utf-8"))
    assert receipt["disposition"] == "handoff_implementation"
    assert source.read_text(encoding="utf-8") == MD_DOC


def _deny_all_patch_authority(monkeypatch):
    original = MaintainDocsPolicy.authorize

    def deny(self, **kwargs):
        decision = original(self, **kwargs)
        return dataclasses.replace(decision, allowed=False, reason="mutant: refuse everything", violations=["mutant"])

    monkeypatch.setattr(MaintainDocsPolicy, "authorize", deny)


def _deny_all_loop_policy(monkeypatch):
    monkeypatch.setattr("attest.loop.load_policy", lambda source, path=None: Policy(None, ({"match": ""},)))


def _never_prove(monkeypatch):
    real_execute = workflow_mod.execute

    def broken(claims, runner, rules, scratch, **kwargs):
        evidence, verdicts, session = real_execute(claims, runner, rules, scratch, **kwargs)
        return evidence, [v.model_copy(update={"status": "broken"}) for v in verdicts], session

    monkeypatch.setattr(workflow_mod, "execute", broken)


def _refuse_promotion(monkeypatch):
    def refuse(*args, **kwargs):
        raise WorkflowError("mutant: promotion refused")

    monkeypatch.setattr(workflow_mod, "promote_workflow", refuse)


@pytest.mark.parametrize(
    "mutant",
    [_deny_all_loop_policy, _deny_all_patch_authority, _never_prove, _refuse_promotion],
    ids=["loop-policy-denies-all", "patch-authority-denies-all", "validation-never-proves", "promotion-refused"],
)
def test_pc_md_01_control_fails_when_product_refuses_everything(tmp_path, monkeypatch, mutant):
    mutant(monkeypatch)
    with pytest.raises(REFUSAL):
        _run_md_control(tmp_path, monkeypatch)


# -- PC-KR-01: stale browser claim repaired and validated through KaneBrowserRunner ------

# Six descriptive lines come first so the browser steps compile to c07 and c08, the
# claim ids the recorded witness_stale capture stored its labels under.
KANE_DOC = """# Greeting demo

This guide covers the greeting service.

It runs on the local machine.

No account is needed.

Ask the team for help.

Report problems in the tracker.

Open http://127.0.0.1:5000/ in your browser and check that the page shows "Hello, World!".

Check that the page shows the text "Goodbye, World!".
"""
_QUOTE = re.compile(r'"([^"]+)"')


class DeterministicCompiler:
    """Stands in for the compiler model: quoted browser steps become browser claims, other lines prose."""

    def parse_claims(self, system: str, user: str) -> ClaimSet:
        claims = []
        for row in user.splitlines():
            match = re.match(r"^\s*(\d+)  (.+)$", row)
            if not match:
                continue
            line, text = int(match.group(1)), match.group(2)
            quoted = _QUOTE.findall(text)
            if text.startswith(("Open ", "Check ")) and quoted:
                claims.append(LLMClaim(kind="browser", start_line=line, end_line=line, text=text,
                                       command=None, objective=text, expects=quoted[-1]))
            else:
                claims.append(LLMClaim(kind="prose", start_line=line, end_line=line, text=text,
                                       command=None, objective=None, expects=None))
        return ClaimSet(claims=claims)


class RecordedKaneCli:
    """Replaces only kane-cli: returns a recorded run and drops its evidence pack in the store."""

    def __init__(self, runner: KaneBrowserRunner, fixture: str):
        self.runner, self.fixture, self.calls = runner, fixture, []

    def __call__(self, cmd, timeout_s):
        self.calls.append(cmd)
        self.runner.evidence_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy(KANE_FIX / f"{self.fixture}.pack.zip", self.runner.evidence_dir / f"{self.fixture}.evidence")
        run = parse_events((KANE_FIX / f"{self.fixture}.ndjson").read_text(encoding="utf-8").splitlines())
        run.exit_code = 0
        return run


def _kane_runner(home: Path, monkeypatch) -> tuple[KaneBrowserRunner, RecordedKaneCli]:
    with monkeypatch.context() as patch:
        patch.setattr(kane.shutil, "which", lambda cmd: cmd)  # only for the PATH check in __init__
        runner = KaneBrowserRunner(home=home, headless=True)
    runner._execute = cli = RecordedKaneCli(runner, "witness_stale")
    return runner, cli


def _run_kane_control(tmp_path: Path, monkeypatch, new_label: str = "Hello, World!"):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "greeting.md"
    source.write_text(KANE_DOC, encoding="utf-8")
    home = tmp_path / "home"
    llm = DeterministicCompiler()

    runner, cli = _kane_runner(tmp_path / "kane-baseline", monkeypatch)
    result = prove(str(source), runner=runner, home=home, llm=llm)
    assert result.by_id["c07"].status == "proven"
    assert result.by_id["c08"].status == "stale"
    assert result.by_id["c08"].evidence.observation.observed == {"c08_label": "Hello, World!"}
    assert len(cli.calls) == 1

    proposed = run_loop(
        result, runner, home=home, rules=Rules(), llm=llm,
        agent_cmd=_agent(tmp_path, "relabel", [('"Goodbye, World!"', f'"{new_label}"')]),
    )
    assert proposed.proposal_states["c08"] == "awaiting_approval"
    assert source.read_text(encoding="utf-8") == KANE_DOC
    store = SQLiteWorkflowStore(home / "workflow.sqlite3")
    workflow_id = proposed.proposals["c08"]
    record = store.get(workflow_id)
    assert record.proposal["authority"]["allowed"] is True
    approve_workflow(store, workflow_id, "maintainer@example.test")

    fresh, fresh_cli = _kane_runner(tmp_path / "kane-validation", monkeypatch)
    outcome = resume_workflow(store, workflow_id, runner=fresh, llm=llm, apply=True)
    return source, store, record, outcome, fresh, fresh_cli


def test_pc_kr_01_stale_browser_claim_repaired_and_promoted_on_kane_evidence(tmp_path, monkeypatch):
    source, store, record, outcome, fresh, fresh_cli = _run_kane_control(tmp_path, monkeypatch)

    assert outcome.state == "promoted"
    validation = outcome.validation
    assert validation["successful"] is True and validation["runner"] == "kane"
    assert validation["statuses"]["c07"] == "proven"
    assert validation["statuses"]["c08"] == "proven"
    assert validation["failed_claims"] == []
    c08 = validation["evidence"]["c08"]["observation"]
    assert c08["status"] == "passed" and c08["observed"] == {"c08_label": "Hello, World!"}
    assert "pack: " in c08["note"]  # bound to the evidence pack Kane wrote for this run
    # The validation runner wrote the repaired test file and called kane-cli on it once.
    assert len(fresh_cli.calls) == 1
    assert fresh_cli.calls[0][1:3] == ["testmd", "run"]
    test_md = Path(fresh_cli.calls[0][3]).read_text(encoding="utf-8")
    assert "Hello, World!" in test_md and "Goodbye" not in test_md
    assert fresh.home.joinpath("packs").is_dir()

    after = source.read_text(encoding="utf-8")
    assert hashlib.sha256(source.read_bytes()).hexdigest() == record.proposal["candidate_digest"]
    assert 'Check that the page shows the text "Hello, World!".' in after and "Goodbye" not in after
    assert _changed_lines(KANE_DOC, after) == [record.proposal["target_start_line"]]
    assert [e["to_state"] for e in store.events(outcome.workflow_id)] == [
        "proposed", "awaiting_approval", "validating", "validated", "promoted"
    ]


def test_pc_kr_01_twin_label_the_page_does_not_show_is_rejected(tmp_path, monkeypatch):
    source, store, record, outcome, _, _ = _run_kane_control(tmp_path, monkeypatch, new_label="Hello, Kane!")
    assert record.proposal["authority"]["allowed"] is True  # same authority as the control
    assert outcome.state == "rejected"
    assert outcome.validation["successful"] is False
    assert outcome.validation["failed_claims"] == ["c08:stale"]
    assert source.read_text(encoding="utf-8") == KANE_DOC


def _kane_steps_always_fail(monkeypatch):
    real = kane.observation_for

    def failed(claim, index, run, pack_steps) -> BrowserObservation:
        return real(claim, index, run, pack_steps).model_copy(update={"status": "failed"})

    monkeypatch.setattr(kane, "observation_for", failed)


@pytest.mark.parametrize(
    "mutant",
    [_kane_steps_always_fail, _deny_all_patch_authority, _refuse_promotion],
    ids=["kane-steps-always-fail", "patch-authority-denies-all", "promotion-refused"],
)
def test_pc_kr_01_control_fails_when_product_refuses_everything(tmp_path, monkeypatch, mutant):
    mutant(monkeypatch)
    with pytest.raises(REFUSAL):
        source, _, _, outcome, _, _ = _run_kane_control(tmp_path, monkeypatch)
        assert outcome.state == "promoted"
        assert "Goodbye" not in source.read_text(encoding="utf-8")


# -- PC-RS-01: Reconcile Spec diagnosis completes as a handoff ---------------------------

RS_PROTECTED = "The application must require multi-factor authentication.\n"


def _rs_request(**conflict_overrides) -> dict:
    conflict = {
        "claim_id": "c01",
        "protected_requirement_id": "SEC-4",
        "authority_reference": "contracts/security.md#SEC-4",
        "protected_text": RS_PROTECTED.strip(),
        "observed_text": "The application accepted a password-only sign-in.",
        "candidate_side": "app_wrong",
        "evidence": [
            {"kind": "runtime", "pointer": "artifacts/run-17/sign-in.png", "description": "Observed sign-in flow"},
            {"kind": "receipt", "pointer": "artifacts/run-17/receipt.json", "description": "Attest run receipt"},
        ],
        **conflict_overrides,
    }
    return {"source_doc": "contracts/security.md", "authority_status": "present", "conflicts": [conflict]}


def _run_reconcile(tmp_path: Path, request: dict, *flags: str):
    contract = tmp_path / "security.md"
    contract.write_text(RS_PROTECTED, encoding="utf-8")
    request_path = tmp_path / "diagnosis.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    out = tmp_path / "out"
    result = CliRunner().invoke(app, ["reconcile-spec", str(request_path), "--out", str(out), *flags])
    data = json.loads((out / "reconcile-spec.json").read_text(encoding="utf-8"))
    markdown = (out / "reconcile-spec.md").read_text(encoding="utf-8")
    return contract, result, data, markdown


def _run_rs_control(tmp_path: Path) -> None:
    contract, result, data, markdown = _run_reconcile(tmp_path, _rs_request())

    assert result.exit_code == 0, result.output
    assert "refused" not in result.output
    # R4: every orthogonal state is set, and repair is the allowed handoff.
    assert data["execution_state"] == "external_observation"
    assert data["comparison_state"] == "submitted_conflict"
    assert data["authority_state"] == "present"
    assert data["evidence_state"] == "provided"
    assert data["repair_state"] == "handoff"
    assert data["apply_requested"] is False
    conflict = data["conflicts"][0]
    assert conflict["protected_edit_refused"] is False
    assert conflict["recommended_owner"] == "application owner"
    assert conflict["proposed_protected_text"] is None
    # R9: the handoff carries every evidence pointer, in both artifacts.
    pointers = [e["pointer"] for e in conflict["evidence"]]
    assert pointers == ["artifacts/run-17/sign-in.png", "artifacts/run-17/receipt.json"]
    assert all(p in markdown for p in pointers)
    assert "Repair: `handoff`" in markdown
    # R2 and §0: the contract is untouched and nothing is marked reconciled.
    assert contract.read_text(encoding="utf-8") == RS_PROTECTED
    assert "reconciled" not in json.dumps(data)


def test_pc_rs_01_complete_conflict_reaches_handoff_without_refusal(tmp_path):
    _run_rs_control(tmp_path)


def test_pc_rs_01_twin_apply_flag_is_refused(tmp_path):
    contract, result, data, _ = _run_reconcile(tmp_path, _rs_request(), "--apply")
    assert result.exit_code == 2
    assert data["repair_state"] == "refused"
    assert data["conflicts"][0]["protected_edit_refused"] is True
    assert contract.read_text(encoding="utf-8") == RS_PROTECTED


def test_pc_rs_01_twin_weakening_proposal_is_refused(tmp_path):
    request = _rs_request(proposed_protected_text="The application may skip multi-factor authentication.")
    contract, result, data, _ = _run_reconcile(tmp_path, request)
    assert data["repair_state"] == "refused"
    assert data["conflicts"][0]["protected_edit_refused"] is True
    assert contract.read_text(encoding="utf-8") == RS_PROTECTED


def test_pc_rs_01_control_fails_when_reconcile_spec_refuses_everything(tmp_path, monkeypatch):
    real = ReconcileSpec.diagnose

    def refuse(self, request, *, apply_requested=False):
        return real(self, request, apply_requested=True)

    monkeypatch.setattr(ReconcileSpec, "diagnose", refuse)
    with pytest.raises(AssertionError):
        _run_rs_control(tmp_path)

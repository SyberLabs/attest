"""G1 Stage-0 binding: app/build version and Kane run id (FACTORY §3 G1, rubric R5).

Evidence bound to the wrong app/build version, or to no version, and evidence bound
to the wrong Kane run, to no run, or to an earlier run must not yield a verified
receipt, a validated workflow or a promotion. That holds for the browser observation
itself too: one that names another build, or none, fails the same way. Each negative case strips or swaps one
binding and asserts the product fails closed; the ``control`` cases keep every
binding intact and must still verify, so a product that refuses everything fails.

Real code paths throughout: ``attest/receipts.py``, ``KaneBrowserRunner`` and
``resume_workflow``/``promote_workflow``. The only stand-ins are the kane-cli
subprocess, replaced by a recorded event-day capture (tests/fixtures/kane/), and the
compiler model, replaced by a deterministic one. None of this is a live Kane run.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
import shutil
import sqlite3
import subprocess
import zipfile
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from attest import kane
from attest.approvals import approve_workflow
from attest.compiler import ClaimSet, LLMClaim, compile_doc
from attest.ingest import ingest_text
from attest.kane import KaneBrowserRunner, find_pack, parse_events
from attest.models import (
    AuthorityState,
    BrowserObservation,
    ComparisonState,
    Evidence,
    EvidenceState,
    ExecutionState,
    RepairState,
    RunReceipt,
    VerifiedRunReceipt,
)
from attest.proposals import create_repair_proposal, current_build_digest
from attest.receipts import ReceiptEvidence
from attest.runner import FakeBrowserRunner, SimulationBrowserObservation
from attest.storage import SQLiteWorkflowStore, canonical_json
from attest.verdict import judge
from attest.workflow import WorkflowError, promote_workflow, resume_workflow

KANE_FIX = Path(__file__).resolve().parents[1] / "fixtures" / "kane"


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def session_of(fixture: str) -> str:
    return parse_events((KANE_FIX / f"{fixture}.ndjson").read_text(encoding="utf-8").splitlines()).session_id


# -- receipt types ----------------------------------------------------------------------

BUILD = digest("build at approval")
OTHER_BUILD = digest("build after a later deploy")
RUN = "kane-session-fresh"


def receipt_fields(**overrides):
    fields = {
        "snapshot_id": uuid4(),
        "source_version_id": uuid4(),
        "content_sha256": digest("document"),
        "execution_state": ExecutionState.SUCCEEDED,
        "comparison_state": ComparisonState.MATCH,
        "authority_state": AuthorityState.ALLOWED,
        "evidence_state": EvidenceState.COMPLETE,
        "repair_state": RepairState.VALIDATED,
        "build_digest": BUILD,
        "kane_run_ids": (RUN,),
    }
    fields.update(overrides)
    return fields


def evidence_for(fields, **overrides) -> ReceiptEvidence:
    values = {
        "kind": "browser",
        "source_version_id": fields["source_version_id"],
        "content_sha256": fields["content_sha256"],
        "complete": True,
        "build_digest": fields["build_digest"],
        "kane_run_id": RUN,
    }
    values.update(overrides)
    return ReceiptEvidence(**values)


def test_version_receipt_rejects_evidence_from_another_build():
    fields = receipt_fields()
    fields["evidence"] = (evidence_for(fields, build_digest=OTHER_BUILD),)
    for model in (RunReceipt, VerifiedRunReceipt):
        with pytest.raises(ValidationError, match="different app/build version"):
            model(**fields)


def test_version_verified_receipt_requires_a_build_version():
    fields = receipt_fields(build_digest=None)
    fields["evidence"] = (evidence_for(fields, build_digest=None),)
    assert isinstance(RunReceipt(**fields), RunReceipt)  # an unverified record may lack it
    with pytest.raises(ValidationError, match="require an app/build version"):
        VerifiedRunReceipt(**fields)


def test_version_verified_receipt_requires_each_evidence_record_to_name_its_build():
    fields = receipt_fields()
    fields["evidence"] = (evidence_for(fields), evidence_for(fields, kind="shell", kane_run_id=None, build_digest=None))
    with pytest.raises(ValidationError, match="every evidence record to name its app/build version"):
        VerifiedRunReceipt(**fields)


def test_version_receipt_build_must_be_a_digest():
    with pytest.raises(ValidationError):
        RunReceipt(**receipt_fields(build_digest="v1.2.3"))


def test_kane_run_receipt_rejects_evidence_from_a_run_it_did_not_start():
    fields = receipt_fields()
    fields["evidence"] = (evidence_for(fields, kane_run_id="kane-session-from-yesterday"),)
    for model in (RunReceipt, VerifiedRunReceipt):
        with pytest.raises(ValidationError, match="Kane run this receipt did not start"):
            model(**fields)


def test_kane_run_receipt_with_no_runs_rejects_browser_evidence_naming_one():
    fields = receipt_fields(kane_run_ids=())
    fields["evidence"] = (evidence_for(fields),)
    with pytest.raises(ValidationError, match="Kane run this receipt did not start"):
        VerifiedRunReceipt(**fields)


def test_kane_run_verified_receipt_requires_browser_evidence_to_name_its_run():
    fields = receipt_fields()
    fields["evidence"] = (evidence_for(fields, kane_run_id=None),)
    with pytest.raises(ValidationError, match="must name the Kane run that produced it"):
        RunReceipt(**fields)  # #47's tip accepted it: a plain receipt checked only bindings evidence named
    with pytest.raises(ValidationError, match="bound to a fresh Kane run id"):
        VerifiedRunReceipt(**fields)


@pytest.mark.parametrize(
    "unnamed, rejection",
    [("build_digest", "must name the receipt's app/build version"), ("kane_run_id", "must name the Kane run that produced it")],
    ids=["evidence-names-no-build", "evidence-names-no-run"],
)
def test_version_plain_receipt_naming_a_binding_rejects_complete_browser_evidence_naming_none(unnamed, rejection):
    # A plain RunReceipt may lack bindings, but one that names its build and runs cannot carry
    # complete browser evidence that names neither: that evidence says nothing about them.
    fields = receipt_fields()
    fields["evidence"] = (evidence_for(fields, **{unnamed: None}),)
    with pytest.raises(ValidationError, match=re.escape(rejection)):
        RunReceipt(**fields)
    # As incomplete evidence it is still recordable, and a receipt naming neither binding may hold it.
    partial = receipt_fields(evidence_state=EvidenceState.INCOMPLETE)
    partial["evidence"] = (evidence_for(partial, complete=False, **{unnamed: None}),)
    assert RunReceipt(**partial).evidence[0].complete is False
    unbound = receipt_fields(build_digest=None, kane_run_ids=())
    unbound["evidence"] = (evidence_for(unbound, build_digest=None, kane_run_id=None),)
    assert RunReceipt(**unbound).evidence_state == EvidenceState.COMPLETE


def test_control_receipt_with_matching_build_and_kane_run_verifies():
    fields = receipt_fields()
    shell = evidence_for(fields, kind="shell", kane_run_id=None)
    fields["evidence"] = (evidence_for(fields), shell)
    receipt = VerifiedRunReceipt(**fields)
    assert receipt.build_digest == BUILD and receipt.kane_run_ids == (RUN,)
    assert all(item.build_digest == BUILD for item in receipt.evidence)


# -- Kane evidence pack binding ---------------------------------------------------------


def _pack_with_session(src: Path, dest: Path, session_id: str) -> Path:
    """Copy a recorded pack, renaming only ``external_id.session_id`` in result.yaml."""
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dest, "w") as zout:
        for name in zin.namelist():
            data = zin.read(name)
            if name.endswith("/result.yaml"):
                data = re.sub(rb"(\n  session_id: )\S+", rb"\g<1>" + session_id.encode(), data)
            zout.writestr(name, data)
    return dest


def test_kane_run_find_pack_needs_a_pack_naming_this_session(tmp_path):
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    shutil.copy(KANE_FIX / "author_pass.pack.zip", evidence_dir / "author_pass.evidence")
    # A fresh pack from another session is never bound by recency.
    assert find_pack(evidence_dir, "no-such-session") is None
    assert find_pack(evidence_dir, None) is None
    assert find_pack(evidence_dir, "") is None


def test_kane_run_find_pack_matches_the_session_field_not_any_substring(tmp_path):
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    sid = session_of("witness_stale")
    # witness_stale's result.yaml repeats the session id as commit_id; only session_id counts.
    other = _pack_with_session(KANE_FIX / "witness_stale.pack.zip", evidence_dir / "other.evidence", "another-session")
    assert sid.encode() in zipfile.ZipFile(other).read("tests/smoke-e8f96f7e/result.yaml")
    assert find_pack(evidence_dir, sid) is None
    shutil.copy(KANE_FIX / "witness_stale.pack.zip", evidence_dir / "witness_stale.evidence")
    assert find_pack(evidence_dir, sid).name == "witness_stale.evidence"


class RecordedKaneCli:
    """Replaces only the kane-cli subprocess: a recorded NDJSON run plus the pack it wrote.

    ``pack`` names the fixture pack dropped into the evidence store (default: the run's
    own). ``strip_session`` removes the session id the stream reports. ``side_effect``
    runs during the call, as the app or its build would change during a real run.
    """

    def __init__(self, runner, fixture, pack=None, strip_session=False, side_effect=None):
        self.runner, self.fixture, self.pack = runner, fixture, pack or fixture
        self.strip_session, self.side_effect, self.calls = strip_session, side_effect, []

    def __call__(self, cmd, timeout_s):
        self.calls.append(cmd)
        self.runner.evidence_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy(KANE_FIX / f"{self.pack}.pack.zip", self.runner.evidence_dir / f"{self.pack}.evidence")
        if self.side_effect:
            self.side_effect()
        run = parse_events((KANE_FIX / f"{self.fixture}.ndjson").read_text(encoding="utf-8").splitlines())
        if self.strip_session:
            run.session_id = None
        run.exit_code = 0
        return run


def kane_runner(home: Path, monkeypatch, **cli) -> tuple[KaneBrowserRunner, RecordedKaneCli]:
    with monkeypatch.context() as patch:
        patch.setattr(kane.shutil, "which", lambda cmd: cmd)  # only the PATH check in __init__
        runner = KaneBrowserRunner(home=home, headless=True)
    runner._execute = recorded = RecordedKaneCli(runner, cli.pop("fixture", "witness_stale"), **cli)
    return runner, recorded


# -- workflow: Maintain Docs repair validated on Kane evidence -------------------------

# Six descriptive lines first so the browser steps compile to c07 and c08, the claim ids
# the recorded witness_stale capture stored its labels under.
BASE_DOC = """# Greeting demo

This guide covers the greeting service.

It runs on the local machine.

No account is needed.

Ask the team for help.

Report problems in the tracker.

Open http://127.0.0.1:5000/ in your browser and check that the page shows "Hello, World!".

Check that the page shows the text "Goodbye, World!".
"""
CANDIDATE_DOC = BASE_DOC.replace('"Goodbye, World!"', '"Hello, World!"')
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


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.email=g1@example.test", "-c", "user.name=g1", *args],
                   cwd=root, check=True, capture_output=True)


def approved_repair(tmp_path: Path, git: bool = False, before=None):
    """A host-created proposal relabelling c08, approved by a human, awaiting validation.

    ``before(source)`` runs once the doc exists, before the proposal is made: whatever it
    deploys is the build the proposal is approved on.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    source = repo / "greeting.md"
    source.write_text(BASE_DOC, encoding="utf-8")
    if git:
        _git(repo, "init", "-q")
        _git(repo, "add", "greeting.md")
        _git(repo, "commit", "-q", "-m", "baseline")
    if before:
        before(source)
    llm = DeterministicCompiler()
    baseline = compile_doc(ingest_text(BASE_DOC, source=str(source)), llm=llm, use_cache=False)
    target = next(claim for claim in baseline if claim.id == "c08")
    diff = "".join(difflib.unified_diff(BASE_DOC.splitlines(True), CANDIDATE_DOC.splitlines(True),
                                        "a/greeting.md", "b/greeting.md"))
    store = SQLiteWorkflowStore(tmp_path / "workflow.sqlite3")  # outside the repo: not a build input
    record = create_repair_proposal(
        store, source_path=source, base_text=BASE_DOC, candidate_text=CANDIDATE_DOC, diff=diff,
        target_claim=target, baseline_claims=baseline, plan="Relabel c08 to the greeting the page shows.",
    )
    assert record.state == "awaiting_approval"
    approve_workflow(store, record.workflow_id, "maintainer@example.test")
    return source, store, record.workflow_id, llm


def _tamper_validation(store: SQLiteWorkflowStore, workflow_id: str, change) -> None:
    validation = store.get(workflow_id).validation
    change(validation)
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE workflows SET validation_json = ? WHERE workflow_id = ?",
                   (canonical_json(validation), workflow_id))


def test_control_fresh_kane_run_on_the_approved_build_validates_and_promotes(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    source, store, workflow_id, llm = approved_repair(tmp_path, git=True)
    runner, cli = kane_runner(tmp_path / "kane", monkeypatch)

    validated = resume_workflow(store, workflow_id, runner=runner, llm=llm)

    assert validated.state == "validated", validated.validation
    validation = validated.validation
    sid = session_of("witness_stale")
    assert validation["kane_runs"] == {"c07": sid, "c08": sid}
    assert validation["build_digest"] == validated.proposal["build_digest"]
    assert {cid: validation["evidence"][cid]["observation"]["build_digest"] for cid in ("c07", "c08")} == {
        "c07": validation["build_digest"], "c08": validation["build_digest"]}
    assert validation["statuses"]["c08"] == "proven" and len(cli.calls) == 1
    promoted = promote_workflow(store, workflow_id)
    assert promoted.state == "promoted"
    assert source.read_text(encoding="utf-8") == CANDIDATE_DOC


def test_version_build_change_during_validation_is_unresolved(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    source, store, workflow_id, llm = approved_repair(tmp_path, git=True)
    # The tree under test changes while Kane is observing it: the evidence describes another build.
    drift = lambda: (source.parent / "app.js").write_text("// redeployed mid-run\n", encoding="utf-8")
    runner, _ = kane_runner(tmp_path / "kane", monkeypatch, side_effect=drift)

    outcome = resume_workflow(store, workflow_id, runner=runner, llm=llm, apply=True)

    assert outcome.state == "unresolved"
    assert "build identity changed during validation" in outcome.validation["reason"]
    assert source.read_text(encoding="utf-8") == BASE_DOC


@pytest.mark.parametrize(
    "change",
    [
        lambda v: v.update(build_digest=digest("some other build")),
        lambda v: v.pop("build_digest"),
    ],
    ids=["swapped-build", "omitted-build"],
)
def test_version_promotion_refuses_validation_not_bound_to_the_approved_build(tmp_path, monkeypatch, change):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    source, store, workflow_id, llm = approved_repair(tmp_path)
    runner, _ = kane_runner(tmp_path / "kane", monkeypatch)
    assert resume_workflow(store, workflow_id, runner=runner, llm=llm).state == "validated"
    _tamper_validation(store, workflow_id, change)

    with pytest.raises(WorkflowError, match="approved app/build version"):
        promote_workflow(store, workflow_id)
    assert source.read_text(encoding="utf-8") == BASE_DOC


def test_kane_run_cached_observation_from_an_earlier_run_is_stale(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    source, store, workflow_id, llm = approved_repair(tmp_path)
    runner, cli = kane_runner(tmp_path / "kane", monkeypatch)
    # The same runner already observed these exact claims before validation started.
    claims = compile_doc(ingest_text(CANDIDATE_DOC, source=str(source)), llm=llm, use_cache=False)
    runner.prepare(claims, str(source))
    for claim in (c for c in claims if c.kind == "browser"):
        runner.observe(claim, None)
    assert len(cli.calls) == 1

    outcome = resume_workflow(store, workflow_id, runner=runner, llm=llm, apply=True)

    assert len(cli.calls) == 1  # validation started no Kane run of its own
    assert outcome.state == "rejected"
    assert {"c07:stale_kane_run", "c08:stale_kane_run"} <= set(outcome.validation["failed_claims"])
    assert source.read_text(encoding="utf-8") == BASE_DOC


@pytest.mark.parametrize(
    "cli",
    [{"pack": "author_pass"}, {"strip_session": True}],
    ids=["pack-from-another-session", "run-reports-no-session"],
)
def test_kane_run_evidence_without_its_own_pack_cannot_validate(tmp_path, monkeypatch, cli):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    source, store, workflow_id, llm = approved_repair(tmp_path)
    runner, _ = kane_runner(tmp_path / "kane", monkeypatch, **cli)

    outcome = resume_workflow(store, workflow_id, runner=runner, llm=llm, apply=True)

    assert outcome.state == "rejected"
    validation = outcome.validation
    assert validation["statuses"]["c07"] == "unverifiable"
    assert validation["statuses"]["c08"] == "skipped"  # depends on c07
    assert validation["kane_runs"] == {"c07": None}  # c08 never ran, so it has no evidence to bind
    assert "c07:stale_kane_run" in validation["failed_claims"]
    assert validation["evidence"]["c07"]["observation"]["note"].startswith("unbound:")
    assert source.read_text(encoding="utf-8") == BASE_DOC


@pytest.mark.parametrize(
    "change",
    [
        lambda v: v["kane_runs"].pop("c08"),
        lambda v: v["kane_runs"].update(c08=None),
        lambda v: v.pop("kane_runs"),
    ],
    ids=["dropped-claim-run", "nulled-claim-run", "omitted-runs"],
)
def test_kane_run_promotion_refuses_validation_without_kane_run_bindings(tmp_path, monkeypatch, change):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    source, store, workflow_id, llm = approved_repair(tmp_path)
    runner, _ = kane_runner(tmp_path / "kane", monkeypatch)
    assert resume_workflow(store, workflow_id, runner=runner, llm=llm).state == "validated"
    _tamper_validation(store, workflow_id, change)

    with pytest.raises(WorkflowError, match="not bound to a fresh Kane run"):
        promote_workflow(store, workflow_id)
    assert source.read_text(encoding="utf-8") == BASE_DOC


def test_kane_run_unbound_observation_is_never_proven(tmp_path, monkeypatch):
    runner, _ = kane_runner(tmp_path / "kane", monkeypatch, pack="author_pass")
    claims = compile_doc(ingest_text(CANDIDATE_DOC, source="greeting.md"), llm=DeterministicCompiler(), use_cache=False)
    c07 = next(c for c in claims if c.id == "c07")
    runner.prepare(claims, "greeting.md")
    obs = runner.observe(c07, None)
    assert obs.status != "passed" and obs.note.startswith("unbound:")
    assert runner.run_ids["c07"] is None
    # Even a caller that restores the pass cannot get a proof out of an unbound observation.
    restored = obs.model_copy(update={"status": "passed"})
    verdict = judge(c07.model_copy(update={"depends_on": []}), Evidence(claim_id="c07", kind="browser", observation=restored))
    assert verdict.status == "unverifiable" and "not bound" in verdict.reason


# -- browser observations name the build they were observed on -------------------------
#
# The checks above bind the receipt and the validation record. These bind the
# observation itself: KaneBrowserRunner measures current_build_digest before and after
# its own kane-cli run, and validation, promotion and receipts compare that build, not
# one copied from the proposal.


def build_of(source: Path) -> str:
    return current_build_digest(source, hashlib.sha256(source.read_bytes()).hexdigest())


def observe_browser_claims(runner, source: Path) -> dict:
    claims = compile_doc(ingest_text(CANDIDATE_DOC, source=str(source)), llm=DeterministicCompiler(), use_cache=False)
    runner.prepare(claims, str(source))
    return {c.id: runner.observe(c, None) for c in claims if c.kind == "browser"}


def deploy(source: Path, text: str = "// redeployed\n") -> None:
    (source.parent / "app.js").write_text(text, encoding="utf-8")


def observed_then_redeployed(runner):
    """``before`` hook: Kane observes on the committed build A, then build B is deployed (and approved)."""
    seen = {}

    def hook(source: Path) -> None:
        seen["build_a"] = build_of(source)
        seen["observations"] = observe_browser_claims(runner, source)
        deploy(source)

    return hook, seen


def test_observation_build_kane_runner_records_the_build_it_observed(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    source, store, workflow_id, _ = approved_repair(tmp_path, git=True)
    runner, _ = kane_runner(tmp_path / "kane", monkeypatch)

    observations = observe_browser_claims(runner, source)

    assert {cid: obs.status for cid, obs in observations.items()} == {"c07": "passed", "c08": "passed"}
    assert {obs.build_digest for obs in observations.values()} == {build_of(source)}
    assert build_of(source) == store.get(workflow_id).proposal["build_digest"]


def test_observation_build_redeploy_during_the_kane_run_names_no_build(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    source, _, _, _ = approved_repair(tmp_path, git=True)
    runner, _ = kane_runner(tmp_path / "kane", monkeypatch, side_effect=lambda: deploy(source))

    observations = observe_browser_claims(runner, source)

    assert all(obs.build_digest is None for obs in observations.values())
    assert all("build: unknown or changed during this Kane run" in obs.note for obs in observations.values())


def test_observation_build_runner_without_a_local_doc_names_no_build(tmp_path, monkeypatch):
    runner, _ = kane_runner(tmp_path / "kane", monkeypatch)
    claims = compile_doc(ingest_text(CANDIDATE_DOC, source="greeting.md"), llm=DeterministicCompiler(), use_cache=False)
    runner.prepare(claims, "https://docs.example.test/greeting")
    c07 = next(c for c in claims if c.id == "c07")
    assert runner.observe(c07, None).build_digest is None


@pytest.mark.parametrize(
    "build, failure",
    [(None, "observation_build_missing"), (digest("a build nobody approved"), "observation_build_mismatch")],
    ids=["omitted-build", "wrong-build"],
)
def test_observation_build_omitted_or_wrong_fails_validation(tmp_path, monkeypatch, build, failure):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    source, store, workflow_id, llm = approved_repair(tmp_path, git=True)
    runner, cli = kane_runner(tmp_path / "kane", monkeypatch)
    # A fresh, pack-bound Kane run whose observations name no build, or another one.
    observe = runner.observe
    runner.observe = lambda claim, base_url: observe(claim, base_url).model_copy(update={"build_digest": build})

    outcome = resume_workflow(store, workflow_id, runner=runner, llm=llm, apply=True)

    assert outcome.state == "rejected"
    validation = outcome.validation
    sid = session_of("witness_stale")
    # Every other binding holds: the observation's build is the only reason.
    assert len(cli.calls) == 1 and validation["kane_runs"] == {"c07": sid, "c08": sid}
    assert validation["statuses"]["c07"] == validation["statuses"]["c08"] == "proven"
    assert validation["failed_claims"] == [f"c07:{failure}", f"c08:{failure}"]
    with pytest.raises(WorkflowError):
        promote_workflow(store, workflow_id)
    assert source.read_text(encoding="utf-8") == BASE_DOC


def test_observation_build_cached_from_an_earlier_build_is_rejected(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    runner, cli = kane_runner(tmp_path / "kane", monkeypatch)
    hook, seen = observed_then_redeployed(runner)
    source, store, workflow_id, llm = approved_repair(tmp_path, git=True, before=hook)
    build_b = store.get(workflow_id).proposal["build_digest"]
    assert build_b != seen["build_a"] and len(cli.calls) == 1

    outcome = resume_workflow(store, workflow_id, runner=runner, llm=llm, apply=True)

    assert len(cli.calls) == 1  # the runner served build A's cached observations
    assert outcome.state == "rejected"
    validation = outcome.validation
    assert validation["evidence"]["c07"]["observation"]["build_digest"] == seen["build_a"]
    assert {"c07:observation_build_mismatch", "c08:observation_build_mismatch"} <= set(validation["failed_claims"])
    assert source.read_text(encoding="utf-8") == BASE_DOC


@pytest.mark.parametrize(
    "change",
    [
        lambda v: v["evidence"]["c08"]["observation"].update(build_digest=digest("some other build")),
        lambda v: v["evidence"]["c08"]["observation"].pop("build_digest"),
        lambda v: v["evidence"].pop("c08"),
    ],
    ids=["swapped-observation-build", "omitted-observation-build", "dropped-evidence"],
)
def test_observation_build_promotion_refuses_evidence_not_observed_on_the_approved_build(tmp_path, monkeypatch, change):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    source, store, workflow_id, llm = approved_repair(tmp_path)  # unversioned: binds path + source digest
    runner, _ = kane_runner(tmp_path / "kane", monkeypatch)
    assert resume_workflow(store, workflow_id, runner=runner, llm=llm).state == "validated"
    _tamper_validation(store, workflow_id, change)

    with pytest.raises(WorkflowError, match="not observed on the approved app/build version"):
        promote_workflow(store, workflow_id)
    assert source.read_text(encoding="utf-8") == BASE_DOC


def _receipt_from_observations(build: str, observations: dict, run: str, cls=VerifiedRunReceipt):
    """A receipt that would verify, whose browser evidence carries each observation's own build."""
    fields = receipt_fields(build_digest=build, kane_run_ids=(run,))
    fields["evidence"] = tuple(
        evidence_for(fields, build_digest=obs.build_digest, kane_run_id=run) for obs in observations.values()
    )
    return cls(**fields)


@pytest.mark.parametrize(
    "case, rejection",
    [
        ("redeployed-mid-run", "verified receipts require every evidence record to name its app/build version"),
        ("cached-from-earlier-build", "receipt evidence belongs to a different app/build version"),
    ],
    ids=["redeployed-mid-run", "cached-from-earlier-build"],
)
def test_observation_build_receipt_from_an_unbound_or_earlier_observation_does_not_verify(tmp_path, monkeypatch, case, rejection):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    if case == "redeployed-mid-run":
        source, store, workflow_id, _ = approved_repair(tmp_path, git=True)
        runner, _ = kane_runner(tmp_path / "kane", monkeypatch, side_effect=lambda: deploy(source, "// mid-run\n"))
        observations = observe_browser_claims(runner, source)
        (source.parent / "app.js").unlink()  # back on the approved tree: only the observations are off
        assert build_of(source) == store.get(workflow_id).proposal["build_digest"]
    else:
        runner, _ = kane_runner(tmp_path / "kane", monkeypatch)
        hook, seen = observed_then_redeployed(runner)
        source, store, workflow_id, _ = approved_repair(tmp_path, git=True, before=hook)
        observations = seen["observations"]
    approved = store.get(workflow_id).proposal["build_digest"]
    run = session_of("witness_stale")

    with pytest.raises(ValidationError, match=re.escape(rejection)):
        _receipt_from_observations(approved, observations, run)
    if case == "cached-from-earlier-build":
        with pytest.raises(ValidationError, match=re.escape(rejection)):
            _receipt_from_observations(approved, observations, run, cls=RunReceipt)


# -- receipt evidence the product builds from an observation ----------------------------
#
# ``BrowserObservation.receipt_evidence`` takes the build from the observation. These
# check what else it must carry over: a simulation-only or unbound observation is not
# complete evidence for any build, even when every build and run binding matches.


def _receipt_evidence_receipt(observations: dict, build: str, run: str, cls=VerifiedRunReceipt, **overrides):
    """A receipt that would verify, whose evidence is ``receipt_evidence`` of each observation."""
    fields = receipt_fields(build_digest=build, kane_run_ids=(run,), **overrides)
    fields["evidence"] = tuple(
        obs.receipt_evidence(source_version_id=fields["source_version_id"],
                             content_sha256=fields["content_sha256"], kane_run_id=run)
        for obs in observations.values()
    )
    return cls(**fields)


def observations(validation: dict) -> dict:
    """The browser observations a validation record stored, read back as the product reads them."""
    return {cid: Evidence.model_validate(validation["evidence"][cid]).observation for cid in ("c07", "c08")}


def _stored(observation):
    """The observation as a validation record stores it and reads it back (subclass lost)."""
    stored = Evidence(claim_id="c07", kind="browser", observation=observation).model_dump(mode="json")
    return Evidence.model_validate(stored).observation


def _fake_claim():
    claims = compile_doc(ingest_text(CANDIDATE_DOC, source="greeting.md"), llm=DeterministicCompiler(), use_cache=False)
    return next(c for c in claims if c.id == "c07")


def test_observation_build_simulated_observation_names_no_build():
    # A scripted fake observation that claims a build: a simulation observed no build.
    fake = FakeBrowserRunner({"c07": BrowserObservation(status="passed", observed={"c07_label": "Hello, World!"},
                                                        build_digest=BUILD)})
    obs = fake.observe(_fake_claim(), "http://127.0.0.1:5000/")
    assert isinstance(obs, SimulationBrowserObservation) and obs.status == "passed"
    assert obs.build_digest is None


@pytest.mark.parametrize("stored", [False, True], ids=["simulation-observation", "stored-simulation-observation"])
def test_observation_build_receipt_from_a_simulated_observation_does_not_verify(stored):
    # Every binding matches: the build is the receipt's, the run is one it started. Only the
    # observation's origin (simulation-only) is wrong.
    simulated = SimulationBrowserObservation(status="passed", observed={"c07_label": "Hello, World!"},
                                             note="simulation-only: fake runner cannot verify page state",
                                             build_digest=BUILD)
    obs = _stored(simulated) if stored else simulated
    assert type(obs) is (BrowserObservation if stored else SimulationBrowserObservation)

    for cls in (VerifiedRunReceipt, RunReceipt):
        with pytest.raises(ValidationError, match="simulated evidence cannot be marked complete"):
            _receipt_evidence_receipt({"c07": obs}, BUILD, RUN, cls=cls)
    # It is still recordable, as what it is: simulated evidence.
    receipt = _receipt_evidence_receipt({"c07": obs}, BUILD, RUN, cls=RunReceipt, evidence_state=EvidenceState.SIMULATED)
    assert [(item.simulated, item.complete) for item in receipt.evidence] == [(True, False)]


@pytest.mark.parametrize("restored", [False, True], ids=["kane-run-without-its-pack", "unbound-but-passed"])
def test_observation_build_receipt_from_an_unbound_observation_does_not_verify(tmp_path, monkeypatch, restored):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    source, store, workflow_id, _ = approved_repair(tmp_path, git=True)
    runner, _ = kane_runner(tmp_path / "kane", monkeypatch, pack="author_pass")
    c07 = observe_browser_claims(runner, source)["c07"]
    approved = store.get(workflow_id).proposal["build_digest"]
    assert c07.note.startswith("unbound:") and c07.build_digest == approved  # the build is right; the run is not
    obs = c07.model_copy(update={"status": "passed"}) if restored else c07

    for cls in (VerifiedRunReceipt, RunReceipt):
        with pytest.raises(ValidationError, match="complete evidence state requires every evidence record to be complete"):
            _receipt_evidence_receipt({"c07": _stored(obs)}, approved, session_of("witness_stale"), cls=cls)


def test_observation_build_kane_run_on_a_build_rolled_back_before_validation_ends_is_rejected(tmp_path, monkeypatch):
    # Build B is deployed just before the Kane run and rolled back just after it. Validation
    # checks the approved build before and after, and sees it both times; the run is fresh
    # and pack-bound. Only the observations, stamped by the runner around its own run, name B.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    source, store, workflow_id, llm = approved_repair(tmp_path, git=True)
    runner, cli = kane_runner(tmp_path / "kane", monkeypatch)
    run_group, builds = runner.run_group, {}

    def on_build_b(group, base_url):
        deploy(source, "// deployed for the length of one Kane run\n")
        builds["b"] = build_of(source)
        try:
            return run_group(group, base_url)
        finally:
            (source.parent / "app.js").unlink()  # rolled back

    runner.run_group = on_build_b
    approved = store.get(workflow_id).proposal["build_digest"]

    outcome = resume_workflow(store, workflow_id, runner=runner, llm=llm, apply=True)

    assert len(cli.calls) == 1 and build_of(source) == approved != builds["b"]
    validation = outcome.validation
    assert outcome.state == "rejected", validation  # not unresolved: validation's own build checks saw the approved build
    sid = session_of("witness_stale")
    assert validation["kane_runs"] == {"c07": sid, "c08": sid}
    assert validation["statuses"]["c07"] == validation["statuses"]["c08"] == "proven"
    assert {obs.build_digest for obs in observations(validation).values()} == {builds["b"]}
    assert validation["failed_claims"] == ["c07:observation_build_mismatch", "c08:observation_build_mismatch"]
    with pytest.raises(WorkflowError):
        promote_workflow(store, workflow_id)
    assert source.read_text(encoding="utf-8") == BASE_DOC
    with pytest.raises(ValidationError, match="receipt evidence belongs to a different app/build version"):
        _receipt_evidence_receipt(observations(validation), approved, sid)


def test_control_receipt_evidence_from_a_validated_kane_run_verifies(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    source, store, workflow_id, llm = approved_repair(tmp_path, git=True)
    runner, _ = kane_runner(tmp_path / "kane", monkeypatch)
    validated = resume_workflow(store, workflow_id, runner=runner, llm=llm)
    assert validated.state == "validated", validated.validation
    validation = validated.validation

    receipt = _receipt_evidence_receipt(observations(validation), validation["build_digest"], session_of("witness_stale"))

    assert [(item.complete, item.simulated, item.build_digest) for item in receipt.evidence] == [
        (True, False, validation["build_digest"])] * 2


def test_control_receipt_from_observations_on_the_approved_build_verifies(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    source, store, workflow_id, _ = approved_repair(tmp_path, git=True)
    runner, _ = kane_runner(tmp_path / "kane", monkeypatch)
    observations = observe_browser_claims(runner, source)
    approved = store.get(workflow_id).proposal["build_digest"]

    receipt = _receipt_from_observations(approved, observations, runner.run_ids["c07"])

    assert receipt.build_digest == approved
    assert [item.build_digest for item in receipt.evidence] == [approved, approved]


# -- observations name the Kane run that made them --------------------------------------
#
# KaneBrowserRunner stamps each pack-bound observation with its run's session id. The run
# a receipt or a promotion binds is then the observation's own, never one the caller or a
# stored record pairs with it, and an observation the runner cannot bind names none.


def test_kane_run_runner_stamps_the_run_whose_pack_backs_each_observation(tmp_path, monkeypatch):
    runner, _ = kane_runner(tmp_path / "kane", monkeypatch)
    bound = observe_browser_claims(runner, tmp_path / "greeting.md")
    assert {obs.kane_run_id for obs in bound.values()} == {session_of("witness_stale")} == set(runner.run_ids.values())

    runner, _ = kane_runner(tmp_path / "kane-other-pack", monkeypatch, pack="author_pass")
    unbound = observe_browser_claims(runner, tmp_path / "greeting.md")
    assert {(obs.note.startswith("unbound:"), obs.kane_run_id) for obs in unbound.values()} == {(True, None)}


@pytest.mark.parametrize(
    "named, failure",
    [("kane-session-from-yesterday", "observation_run_mismatch"), (None, "observation_run_missing")],
    ids=["observation-from-another-run", "observation-names-no-run"],
)
def test_kane_run_observation_not_naming_its_bound_run_fails_validation(tmp_path, monkeypatch, named, failure):
    # The runner's record binds both claims to the fresh run, the build is the approved one,
    # and both are proven. Only the observations name another run, or none.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    source, store, workflow_id, llm = approved_repair(tmp_path, git=True)
    runner, cli = kane_runner(tmp_path / "kane", monkeypatch)
    observe = runner.observe
    runner.observe = lambda claim, base_url: observe(claim, base_url).model_copy(update={"kane_run_id": named})

    outcome = resume_workflow(store, workflow_id, runner=runner, llm=llm, apply=True)

    assert outcome.state == "rejected"
    validation = outcome.validation
    sid = session_of("witness_stale")
    assert len(cli.calls) == 1 and validation["kane_runs"] == {"c07": sid, "c08": sid}
    assert validation["statuses"]["c07"] == validation["statuses"]["c08"] == "proven"
    assert validation["failed_claims"] == [f"c07:{failure}", f"c08:{failure}"]
    with pytest.raises(WorkflowError):
        promote_workflow(store, workflow_id)
    assert source.read_text(encoding="utf-8") == BASE_DOC


@pytest.mark.parametrize(
    "change",
    [
        lambda v: v["kane_runs"].update(c08="kane-session-from-yesterday"),
        lambda v: v["evidence"]["c08"]["observation"].update(kane_run_id="kane-session-from-yesterday"),
        lambda v: v["evidence"]["c08"]["observation"].pop("kane_run_id"),
    ],
    ids=["swapped-claim-run", "swapped-observation-run", "omitted-observation-run"],
)
def test_kane_run_promotion_refuses_evidence_not_naming_its_bound_run(tmp_path, monkeypatch, change):
    # #47's promotion only asked that a claim's run binding be non-empty, so swapping it for
    # any other session promoted. A record stored before observations named their run has
    # to be validated again, as one without observation builds does.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    source, store, workflow_id, llm = approved_repair(tmp_path)
    runner, _ = kane_runner(tmp_path / "kane", monkeypatch)
    assert resume_workflow(store, workflow_id, runner=runner, llm=llm).state == "validated"
    _tamper_validation(store, workflow_id, change)

    with pytest.raises(WorkflowError, match="does not name the Kane run it is bound to"):
        promote_workflow(store, workflow_id)
    assert source.read_text(encoding="utf-8") == BASE_DOC


@pytest.mark.parametrize(
    "change",
    [
        lambda o: o.update(note="simulation-only: fake runner cannot verify page state"),
        lambda o: o.update(note="unbound: no evidence pack names Kane session (none reported)"),
        lambda o: o.update(status="failed"),
    ],
    ids=["stored-simulation-note", "stored-unbound-note", "stored-failed-status"],
)
def test_kane_run_promotion_refuses_stored_evidence_a_receipt_would_not_count(tmp_path, monkeypatch, change):
    # Build and run bindings all hold; the stored observation itself is simulated, unbound or
    # failed, which receipt_evidence would never mark complete. #47's promotion read only
    # the build and run fields of it.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    source, store, workflow_id, llm = approved_repair(tmp_path)
    runner, _ = kane_runner(tmp_path / "kane", monkeypatch)
    assert resume_workflow(store, workflow_id, runner=runner, llm=llm).state == "validated"
    _tamper_validation(store, workflow_id, lambda v: change(v["evidence"]["c08"]["observation"]))

    with pytest.raises(WorkflowError, match="simulated, unbound or not passed"):
        promote_workflow(store, workflow_id)
    assert source.read_text(encoding="utf-8") == BASE_DOC


@pytest.mark.parametrize("stored", [False, True], ids=["cached-observation", "stored-cached-observation"])
def test_kane_run_receipt_evidence_from_an_earlier_run_cannot_claim_a_fresh_one(tmp_path, monkeypatch, stored):
    # Kane observed the approved build in an earlier run. A caller pairs those observations
    # with a fresh run the receipt did start. On #47's tip the build matched and the run
    # was the caller's, so the receipt verified.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    source, store, workflow_id, _ = approved_repair(tmp_path, git=True)
    runner, _ = kane_runner(tmp_path / "kane", monkeypatch)
    cached = observe_browser_claims(runner, source)
    cached = {cid: _stored(obs) for cid, obs in cached.items()} if stored else cached
    approved = store.get(workflow_id).proposal["build_digest"]
    assert {obs.build_digest for obs in cached.values()} == {approved}

    with pytest.raises(ValueError, match=f"made by Kane run {re.escape(session_of('witness_stale'))}, not {RUN}"):
        _receipt_evidence_receipt(cached, approved, RUN)
    # An observation stored before observations named their run names none: so does its evidence.
    legacy = {cid: obs.model_copy(update={"kane_run_id": None}) for cid, obs in cached.items()}
    with pytest.raises(ValidationError, match="bound to a fresh Kane run id"):
        _receipt_evidence_receipt(legacy, approved, RUN)
    with pytest.raises(ValidationError, match="must name the Kane run that produced it"):
        _receipt_evidence_receipt(legacy, approved, RUN, cls=RunReceipt)


def test_observation_build_scripted_fake_observation_names_no_build_or_run(tmp_path):
    # A --fake-browser script that claims a build and a Kane run: a simulation observed neither.
    script = tmp_path / "fake.json"
    script.write_text(canonical_json({"c07": {"status": "passed", "observed": {"c07_label": "Hello, World!"},
                                              "build_digest": BUILD, "kane_run_id": RUN}}), encoding="utf-8")
    obs = FakeBrowserRunner.from_file(script).observe(_fake_claim(), "http://127.0.0.1:5000/")
    assert isinstance(obs, SimulationBrowserObservation) and obs.status == "passed"
    assert (obs.build_digest, obs.kane_run_id) == (None, None)

    for observation in (obs, _stored(obs)):
        evidence = _receipt_evidence_receipt({"c07": observation}, BUILD, RUN, cls=RunReceipt,
                                             evidence_state=EvidenceState.SIMULATED).evidence
        assert [(e.simulated, e.complete, e.build_digest, e.kane_run_id) for e in evidence] == [(True, False, None, None)]
        with pytest.raises(ValidationError):
            _receipt_evidence_receipt({"c07": observation}, BUILD, RUN)


def test_kane_run_fake_runner_named_kane_cannot_validate(tmp_path, monkeypatch):
    # A fake runner that calls itself kane gets past the runner-name check, but it starts no
    # Kane run and names none, so validation rejects it. No product change: this holds on #47.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    source, store, workflow_id, llm = approved_repair(tmp_path, git=True)
    fake = FakeBrowserRunner({cid: BrowserObservation(status="passed", observed={f"{cid}_label": "Hello, World!"},
                                                      build_digest=store.get(workflow_id).proposal["build_digest"],
                                                      kane_run_id=RUN) for cid in ("c07", "c08")})
    fake.name = "kane"

    outcome = resume_workflow(store, workflow_id, runner=fake, llm=llm, apply=True)

    assert outcome.state == "rejected"
    assert {"c07:stale_kane_run", "c07:observation_build_missing"} <= set(outcome.validation["failed_claims"])
    stored = outcome.validation["evidence"]["c07"]["observation"]  # c08 depends on c07, so it never ran
    assert (stored["build_digest"], stored["kane_run_id"]) == (None, None)
    with pytest.raises(WorkflowError):
        promote_workflow(store, workflow_id)
    assert source.read_text(encoding="utf-8") == BASE_DOC


def test_control_stored_observations_name_their_bound_run_and_still_count(tmp_path, monkeypatch):
    # The new run and origin checks pass a fresh Kane run on the approved build: its stored
    # observations name the run validation bound them to, a plain receipt naming that build
    # and run holds their evidence, and the record promotes.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    source, store, workflow_id, llm = approved_repair(tmp_path, git=True)
    runner, _ = kane_runner(tmp_path / "kane", monkeypatch)
    validation = resume_workflow(store, workflow_id, runner=runner, llm=llm).validation
    sid = session_of("witness_stale")
    stored = observations(validation)

    assert {cid: obs.kane_run_id for cid, obs in stored.items()} == validation["kane_runs"] == {"c07": sid, "c08": sid}
    for cls in (RunReceipt, VerifiedRunReceipt):
        receipt = _receipt_evidence_receipt(stored, validation["build_digest"], sid, cls=cls)
        assert [(e.complete, e.kane_run_id) for e in receipt.evidence] == [(True, sid)] * 2
    assert promote_workflow(store, workflow_id).state == "promoted"
    assert source.read_text(encoding="utf-8") == CANDIDATE_DOC


# -- spec mode: an approved candidate app validated on Kane evidence ---------------------
#
# Spec mode (``spec_app``) validated and promoted on statuses alone: an observation cached
# from an earlier run, one naming another run or build, or none, and a stored record whose
# run and build bindings were dropped all validated and wrote the original app. Validation
# and promotion now bind them as the Maintain Docs path does. The runner measures the
# spec's worktree, so that is the build approval records (``spec_build_digest``).

APP_JS = "const greeting = 'Hello, World!';\n"


def approved_spec_promotion(tmp_path: Path, git: bool = False, spec_repo: bool = False, spec_source: str | None = None):
    """A spec-mode proposal to promote a changed candidate app, approved by a human, awaiting validation.

    The app and the spec share ``repo`` (``git`` makes it a Git worktree). ``spec_repo`` puts the
    spec in a Git worktree of its own and leaves the app unversioned. ``spec_source`` names the
    spec by URL instead.
    """
    from attest.app_promotion import create_app_promotion_proposal
    from attest.implement import make_candidate

    repo = tmp_path / "repo"
    app = (tmp_path if spec_repo else repo) / "app"
    app.mkdir(parents=True)
    repo.mkdir(exist_ok=True)
    (app / "app.js").write_text(APP_JS, encoding="utf-8")
    spec = repo / "greeting.md"
    spec.write_text(CANDIDATE_DOC, encoding="utf-8")
    if git or spec_repo:
        _git(repo, "init", "-q")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "baseline")
    doc = ingest_text(CANDIDATE_DOC, source=spec_source or str(spec))
    claims = compile_doc(doc, llm=DeterministicCompiler(), use_cache=False)
    candidate = make_candidate(app, doc.sha256, tmp_path / "home")  # outside the repo: not a build input
    (candidate.root / "app.js").write_text(APP_JS + "render(greeting);\n", encoding="utf-8")
    store = SQLiteWorkflowStore(tmp_path / "workflow.sqlite3")
    record = create_app_promotion_proposal(store, candidate=candidate, doc=doc, claims=claims, implemented=["c08"])
    assert record.state == "awaiting_approval", record.proposal.get("authority")
    approve_workflow(store, record.workflow_id, "maintainer@example.test")
    return app, spec, store, record.workflow_id


def app_unchanged(app: Path) -> bool:
    return (app / "app.js").read_text(encoding="utf-8") == APP_JS


def test_control_spec_mode_fresh_kane_run_on_the_approved_build_validates_and_promotes(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    app, spec, store, workflow_id = approved_spec_promotion(tmp_path, git=True)
    runner, cli = kane_runner(tmp_path / "kane", monkeypatch)

    validated = resume_workflow(store, workflow_id, runner=runner)

    assert validated.state == "validated", validated.validation
    validation = validated.validation
    sid = session_of("witness_stale")
    assert validation["kane_runs"] == {"c07": sid, "c08": sid} and len(cli.calls) == 1
    assert validation["spec_build_digest"] == validated.proposal["spec_build_digest"] == build_of(spec)
    assert {(obs.build_digest, obs.kane_run_id) for obs in observations(validation).values()} == {
        (validation["spec_build_digest"], sid)}
    assert promote_workflow(store, workflow_id).state == "promoted"
    assert "render(greeting)" in (app / "app.js").read_text(encoding="utf-8")


def test_kane_run_spec_mode_cached_observation_from_an_earlier_run_is_stale(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    app, spec, store, workflow_id = approved_spec_promotion(tmp_path, git=True)
    runner, cli = kane_runner(tmp_path / "kane", monkeypatch)
    observe_browser_claims(runner, spec)  # the same runner observed these claims before validation
    assert len(cli.calls) == 1

    outcome = resume_workflow(store, workflow_id, runner=runner, apply=True)

    assert len(cli.calls) == 1  # validation started no Kane run of its own
    assert outcome.state == "rejected", outcome.validation
    assert outcome.validation["statuses"]["c07"] == outcome.validation["statuses"]["c08"] == "proven"
    assert outcome.validation["failed_claims"] == ["c07:stale_kane_run", "c08:stale_kane_run"]
    assert app_unchanged(app)


@pytest.mark.parametrize(
    "named, failure",
    [("kane-session-from-yesterday", "observation_run_mismatch"), (None, "observation_run_missing")],
    ids=["observation-from-another-run", "observation-names-no-run"],
)
def test_kane_run_spec_mode_observation_not_naming_its_bound_run_fails_validation(tmp_path, monkeypatch, named, failure):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    app, _, store, workflow_id = approved_spec_promotion(tmp_path, git=True)
    runner, cli = kane_runner(tmp_path / "kane", monkeypatch)
    observe = runner.observe
    runner.observe = lambda claim, base_url: observe(claim, base_url).model_copy(update={"kane_run_id": named})

    outcome = resume_workflow(store, workflow_id, runner=runner, apply=True)

    assert outcome.state == "rejected"
    sid = session_of("witness_stale")
    assert len(cli.calls) == 1 and outcome.validation["kane_runs"] == {"c07": sid, "c08": sid}
    assert outcome.validation["failed_claims"] == [f"c07:{failure}", f"c08:{failure}"]
    with pytest.raises(WorkflowError):
        promote_workflow(store, workflow_id)
    assert app_unchanged(app)


@pytest.mark.parametrize(
    "build, failure",
    [(None, "observation_build_missing"), (digest("a build nobody approved"), "observation_build_mismatch")],
    ids=["omitted-build", "wrong-build"],
)
def test_observation_build_spec_mode_omitted_or_wrong_fails_validation(tmp_path, monkeypatch, build, failure):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    app, _, store, workflow_id = approved_spec_promotion(tmp_path, git=True)
    runner, cli = kane_runner(tmp_path / "kane", monkeypatch)
    observe = runner.observe
    runner.observe = lambda claim, base_url: observe(claim, base_url).model_copy(update={"build_digest": build})

    outcome = resume_workflow(store, workflow_id, runner=runner, apply=True)

    assert outcome.state == "rejected"
    sid = session_of("witness_stale")
    assert len(cli.calls) == 1 and outcome.validation["kane_runs"] == {"c07": sid, "c08": sid}
    assert outcome.validation["failed_claims"] == [f"c07:{failure}", f"c08:{failure}"]
    assert app_unchanged(app)


def test_observation_build_spec_mode_url_spec_names_no_build_and_cannot_validate(tmp_path, monkeypatch):
    # A spec named by URL: the runner measures no build around its run, so there is none to bind.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    app, _, store, workflow_id = approved_spec_promotion(tmp_path, spec_source="https://docs.example.test/greeting.md")
    assert store.get(workflow_id).proposal["spec_build_digest"] is None
    runner, cli = kane_runner(tmp_path / "kane", monkeypatch)

    outcome = resume_workflow(store, workflow_id, runner=runner, apply=True)

    assert outcome.state == "rejected" and len(cli.calls) == 1
    assert outcome.validation["failed_claims"] == ["c07:observation_build_missing", "c08:observation_build_missing"]
    assert app_unchanged(app)


def test_version_spec_mode_build_change_during_validation_is_unresolved(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    app, spec, store, workflow_id = approved_spec_promotion(tmp_path, git=True)
    # The worktree changes while Kane observes it (outside the app tree, so only the build moves).
    runner, _ = kane_runner(tmp_path / "kane", monkeypatch, side_effect=lambda: deploy(spec, "// mid-run\n"))

    outcome = resume_workflow(store, workflow_id, runner=runner, apply=True)

    assert outcome.state == "unresolved"
    assert outcome.validation["reason"].startswith("changed during validation: build identity changed")
    assert app_unchanged(app)


def test_version_spec_mode_spec_build_change_after_approval_is_unresolved(tmp_path, monkeypatch):
    # The spec lives in its own worktree, so the app's build check cannot see it change. Kane
    # would stamp the new build on every observation, and nothing compared it to approval.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    app, spec, store, workflow_id = approved_spec_promotion(tmp_path, spec_repo=True)
    approved = store.get(workflow_id).proposal["spec_build_digest"]
    deploy(spec)
    assert build_of(spec) != approved
    runner, cli = kane_runner(tmp_path / "kane", monkeypatch)

    outcome = resume_workflow(store, workflow_id, runner=runner, apply=True)

    assert outcome.state == "unresolved" and not cli.calls
    assert "spec build identity changed after approval" in outcome.validation["reason"]
    assert app_unchanged(app)


@pytest.mark.parametrize(
    "change",
    [
        lambda v: v.update(spec_build_digest=digest("some other build")),
        lambda v: v.pop("spec_build_digest"),
    ],
    ids=["swapped-build", "omitted-build"],
)
def test_version_spec_mode_promotion_refuses_validation_not_bound_to_the_approved_build(tmp_path, monkeypatch, change):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    app, _, store, workflow_id = approved_spec_promotion(tmp_path)
    runner, _ = kane_runner(tmp_path / "kane", monkeypatch)
    assert resume_workflow(store, workflow_id, runner=runner).state == "validated"
    _tamper_validation(store, workflow_id, change)

    with pytest.raises(WorkflowError, match="not bound to the approved app/build version"):
        promote_workflow(store, workflow_id)
    assert app_unchanged(app)


@pytest.mark.parametrize(
    "change",
    [
        lambda v: v["kane_runs"].pop("c08"),
        lambda v: v["kane_runs"].update(c08=None),
        lambda v: v.pop("kane_runs"),
    ],
    ids=["dropped-claim-run", "nulled-claim-run", "omitted-runs"],
)
def test_kane_run_spec_mode_promotion_refuses_validation_without_kane_run_bindings(tmp_path, monkeypatch, change):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    app, _, store, workflow_id = approved_spec_promotion(tmp_path)
    runner, _ = kane_runner(tmp_path / "kane", monkeypatch)
    assert resume_workflow(store, workflow_id, runner=runner).state == "validated"
    _tamper_validation(store, workflow_id, change)

    with pytest.raises(WorkflowError, match="not bound to a fresh Kane run"):
        promote_workflow(store, workflow_id)
    assert app_unchanged(app)


@pytest.mark.parametrize(
    "change",
    [
        lambda v: v["evidence"]["c08"]["observation"].update(build_digest=digest("some other build")),
        lambda v: v["evidence"]["c08"]["observation"].pop("build_digest"),
        lambda v: v["evidence"].pop("c08"),
    ],
    ids=["swapped-observation-build", "omitted-observation-build", "dropped-evidence"],
)
def test_observation_build_spec_mode_promotion_refuses_evidence_not_observed_on_the_approved_build(tmp_path, monkeypatch, change):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    app, _, store, workflow_id = approved_spec_promotion(tmp_path)
    runner, _ = kane_runner(tmp_path / "kane", monkeypatch)
    assert resume_workflow(store, workflow_id, runner=runner).state == "validated"
    _tamper_validation(store, workflow_id, change)

    with pytest.raises(WorkflowError, match="not observed on the approved app/build version"):
        promote_workflow(store, workflow_id)
    assert app_unchanged(app)


@pytest.mark.parametrize(
    "change, refusal",
    [
        (lambda v: v["kane_runs"].update(c08="kane-session-from-yesterday"), "does not name the Kane run"),
        (lambda v: v["evidence"]["c08"]["observation"].update(kane_run_id="kane-session-from-yesterday"),
         "does not name the Kane run"),
        (lambda v: v["evidence"]["c08"]["observation"].pop("kane_run_id"), "does not name the Kane run"),
        (lambda v: v["evidence"]["c08"]["observation"].update(note="simulation-only: fake runner cannot verify page state"),
         "simulated, unbound or not passed"),
        (lambda v: v["evidence"]["c08"]["observation"].update(note="unbound: no evidence pack names Kane session (none reported)"),
         "simulated, unbound or not passed"),
        (lambda v: v["evidence"]["c08"]["observation"].update(status="failed"), "simulated, unbound or not passed"),
    ],
    ids=["swapped-claim-run", "swapped-observation-run", "omitted-observation-run",
         "stored-simulation-note", "stored-unbound-note", "stored-failed-status"],
)
def test_kane_run_spec_mode_promotion_refuses_stored_evidence_a_receipt_would_not_count(tmp_path, monkeypatch, change, refusal):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    app, _, store, workflow_id = approved_spec_promotion(tmp_path)
    runner, _ = kane_runner(tmp_path / "kane", monkeypatch)
    assert resume_workflow(store, workflow_id, runner=runner).state == "validated"
    _tamper_validation(store, workflow_id, change)

    with pytest.raises(WorkflowError, match=refusal):
        promote_workflow(store, workflow_id)
    assert app_unchanged(app)


# -- the stored validation record: bound to its workflow, its artifact and its event -----
#
# Promotion's other checks make the stored record agree with itself: its observations name
# the run and build its own fields bind. So a record rewritten field by field, consistently,
# promoted: one laundering an earlier Kane run, one moved from another workflow's validation,
# a rejected one relabelled validated, one dropping an approved claim or a claim's proven
# status. Validation now seals the record to this workflow and approval and stores it as an
# artifact, and the validated event names those seals. Promotion checks all of it against
# what lives outside the record: the approval, the candidate, the approved claims, the
# artifact and the event. A writer who also rewrites the artifact and the event row is not
# caught; that needs a key the store doesn't hold.

LAUNDERED_RUN = "kane-session-from-yesterday"


def _launder_earlier_run(v):
    # The cached-observation attack, done on the stored record: c08's evidence names an
    # earlier Kane run, and its binding says so too.
    v["kane_runs"]["c08"] = LAUNDERED_RUN
    v["evidence"]["c08"]["observation"]["kane_run_id"] = LAUNDERED_RUN


def _validated_events(store: SQLiteWorkflowStore, workflow_id: str) -> list[dict]:
    return [e["payload"] for e in store.events(workflow_id) if (e["from_state"], e["to_state"]) == ("validating", "validated")]


def _reseal(store: SQLiteWorkflowStore, workflow_id: str, change, *, artifact=True, event=True, state=None) -> None:
    """Rewrite the stored validation as a writer who knows the digest scheme: every field together.

    The record's own digests are always recomputed. ``artifact`` also stores the rewritten
    record as its receipt artifact. ``event`` also rewrites the validated event's seals.
    ``state`` sets the workflow state as well.
    """
    v = store.get(workflow_id).validation
    old = {key: v.pop(key) for key in ("receipt_digest", "receipt_artifact_digest")}
    change(v)
    v["evidence_digest"] = digest(canonical_json(v["evidence"]))
    v["receipt_digest"] = digest(canonical_json(v))
    v["receipt_artifact_digest"] = store.put_artifact(canonical_json(v)) if artifact else old["receipt_artifact_digest"]
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE workflows SET validation_json = ?, state = COALESCE(?, state) WHERE workflow_id = ?",
                   (canonical_json(v), state, workflow_id))
        if event:
            for event_id, payload in db.execute(
                "SELECT event_id, payload_json FROM workflow_events WHERE workflow_id = ? AND from_state = 'validating' "
                "AND to_state = 'validated'", (workflow_id,)).fetchall():
                sealed = {**json.loads(payload), "receipt_digest": v["receipt_digest"],
                          "receipt_artifact_digest": v["receipt_artifact_digest"]}
                db.execute("UPDATE workflow_events SET payload_json = ? WHERE event_id = ?", (canonical_json(sealed), event_id))


def _move_validation(store: SQLiteWorkflowStore, from_id: str, to_id: str, rebind=None) -> None:
    """Put one workflow's validation, and its validated event, on another workflow awaiting validation.

    ``rebind(v)`` rewrites the moved record (its digests and artifact recomputed, the copied
    event naming the new seals), as a writer would to fit the other workflow's fields.
    """
    with sqlite3.connect(store.path) as db:
        (vjson,) = db.execute("SELECT validation_json FROM workflows WHERE workflow_id = ?", (from_id,)).fetchone()
        (payload,) = db.execute("SELECT payload_json FROM workflow_events WHERE workflow_id = ? AND from_state = 'validating' "
                                "AND to_state = 'validated'", (from_id,)).fetchone()
        db.execute("UPDATE workflows SET state = 'validated', validation_json = ? WHERE workflow_id = ?", (vjson, to_id))
        db.execute("INSERT INTO workflow_events(workflow_id, from_state, to_state, payload_json, created_at) "
                   "VALUES (?, 'validating', 'validated', ?, '2026-10-26T00:00:00+00:00')", (to_id, payload))
    if rebind is not None:
        _reseal(store, to_id, rebind)


def second_repair(source: Path, store: SQLiteWorkflowStore, llm, candidate_text: str) -> str:
    """Another approved proposal for the same doc, on the same build, in the same store."""
    base_text = source.read_text(encoding="utf-8")
    baseline = compile_doc(ingest_text(base_text, source=str(source)), llm=llm, use_cache=False)
    diff = "".join(difflib.unified_diff(base_text.splitlines(True), candidate_text.splitlines(True),
                                        "a/greeting.md", "b/greeting.md"))
    record = create_repair_proposal(
        store, source_path=source, base_text=base_text, candidate_text=candidate_text, diff=diff,
        target_claim=next(c for c in baseline if c.id == "c08"), baseline_claims=baseline,
        plan="Relabel c08 to another greeting.",
    )
    approve_workflow(store, record.workflow_id, "someone-else@example.test")
    return record.workflow_id


# c08 relabelled to a greeting no Kane run of the moved record saw.
UNVALIDATED_DOC = BASE_DOC.replace('"Goodbye, World!"', '"Hello, Kane!"')


def validated_repair(tmp_path: Path, monkeypatch, **cli):
    source, store, workflow_id, llm = approved_repair(tmp_path)
    runner, _ = kane_runner(tmp_path / "kane", monkeypatch, **cli)
    assert resume_workflow(store, workflow_id, runner=runner, llm=llm).state == "validated"
    return source, store, workflow_id, llm


def test_control_validation_record_is_sealed_to_its_workflow_approval_artifact_and_event(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    source, store, workflow_id, _ = validated_repair(tmp_path, monkeypatch)
    record = store.get(workflow_id)
    v = record.validation

    assert (v["workflow_id"], v["approval_digest"], v["candidate_digest"]) == (
        workflow_id, record.approval["approval_digest"], record.proposal["candidate_digest"])
    receipt = {key: value for key, value in v.items() if key != "receipt_artifact_digest"}
    assert store.get_artifact(v["receipt_artifact_digest"]) == canonical_json(receipt).encode("utf-8")
    assert [{k: e[k] for k in ("receipt_digest", "receipt_artifact_digest")} for e in _validated_events(store, workflow_id)] == [
        {"receipt_digest": v["receipt_digest"], "receipt_artifact_digest": v["receipt_artifact_digest"]}]
    assert promote_workflow(store, workflow_id).state == "promoted"
    assert source.read_text(encoding="utf-8") == CANDIDATE_DOC


@pytest.mark.parametrize(
    "level, refusal",
    [
        ("fields", "does not match its own digests"),
        ("digests", "does not match the receipt artifact"),
        ("artifact", "not the one its validated event recorded"),
    ],
    ids=["fields-rewritten", "digests-recomputed", "artifact-restored"],
)
def test_record_promotion_refuses_a_rewritten_validation_that_its_seals_do_not_hold(tmp_path, monkeypatch, level, refusal):
    # Each level rewrites one more seal than the last, and each is refused by the next one.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    source, store, workflow_id, _ = validated_repair(tmp_path, monkeypatch)
    if level == "fields":
        _tamper_validation(store, workflow_id, _launder_earlier_run)
    else:
        _reseal(store, workflow_id, _launder_earlier_run, artifact=level == "artifact", event=False)

    with pytest.raises(WorkflowError, match=refusal):
        promote_workflow(store, workflow_id)
    assert source.read_text(encoding="utf-8") == BASE_DOC


@pytest.mark.parametrize("moved", [True, False], ids=["moved-from-another-workflow", "record-from-before-binding"])
def test_record_promotion_refuses_a_validation_not_bound_to_this_workflows_approval(tmp_path, monkeypatch, moved):
    # Moved: workflow A validated CANDIDATE_DOC. Workflow B, approved for the same doc and
    # build, proposes UNVALIDATED_DOC; it gets A's record and validated event, unchanged.
    # A record stored before validation recorded its workflow and approval has to be redone.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    source, store, workflow_id, llm = validated_repair(tmp_path, monkeypatch)
    if moved:
        other_id = second_repair(source, store, llm, UNVALIDATED_DOC)
        _move_validation(store, workflow_id, other_id)
        workflow_id = other_id
    else:
        _tamper_validation(store, workflow_id, lambda v: [v.pop("workflow_id"), v.pop("approval_digest")])

    with pytest.raises(WorkflowError, match="not bound to this workflow's approval and candidate"):
        promote_workflow(store, workflow_id)
    assert source.read_text(encoding="utf-8") == BASE_DOC


def test_record_promotion_refuses_a_rejected_validation_relabelled_validated(tmp_path, monkeypatch):
    # A cached observation from an earlier run: rejected for stale_kane_run with every status
    # proven. Relabelled validated and resealed, it has no validated event to name its seals.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    source, store, workflow_id, llm = approved_repair(tmp_path)
    runner, cli = kane_runner(tmp_path / "kane", monkeypatch)
    observe_browser_claims(runner, source)
    assert resume_workflow(store, workflow_id, runner=runner, llm=llm).state == "rejected"
    _reseal(store, workflow_id, lambda v: v.update(successful=True, failed_claims=[]), state="validated")

    with pytest.raises(WorkflowError, match="not the one its validated event recorded"):
        promote_workflow(store, workflow_id)
    assert len(cli.calls) == 1 and source.read_text(encoding="utf-8") == BASE_DOC


def _drop_claim(v, claim_id="c08"):
    v["validated_claims"] = [c for c in v["validated_claims"] if c["id"] != claim_id]
    for field in ("evidence", "statuses", "kane_runs"):
        v[field].pop(claim_id, None)


def test_record_promotion_refuses_a_resealed_validation_that_drops_an_approved_claim(tmp_path, monkeypatch):
    # Every seal rewritten, artifact and event included: the record still has to carry the
    # approved document's claims. Without c08 its browser evidence was never checked.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    source, store, workflow_id, _ = validated_repair(tmp_path, monkeypatch)
    _reseal(store, workflow_id, _drop_claim)

    with pytest.raises(WorkflowError, match="claims are not the approved document's"):
        promote_workflow(store, workflow_id)
    assert source.read_text(encoding="utf-8") == BASE_DOC


UNPROVEN = [
    lambda v: v["statuses"].update(c08="stale"),
    lambda v: v["statuses"].pop("c08"),
    lambda v: v.update(failed_claims=["c08:stale"]),
]
UNPROVEN_IDS = ["status-not-proven", "status-dropped", "failure-listed"]


@pytest.mark.parametrize("change", UNPROVEN, ids=UNPROVEN_IDS)
def test_record_promotion_refuses_a_resealed_validation_that_does_not_show_every_claim_proven(tmp_path, monkeypatch, change):
    # Fully resealed and still "successful": promotion read only that flag.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    source, store, workflow_id, _ = validated_repair(tmp_path, monkeypatch)
    _reseal(store, workflow_id, change)

    with pytest.raises(WorkflowError, match="does not show every approved claim proven"):
        promote_workflow(store, workflow_id)
    assert source.read_text(encoding="utf-8") == BASE_DOC


# spec mode: the same seals, checked by promote_app_workflow


def validated_spec_promotion(tmp_path: Path, monkeypatch):
    app, spec, store, workflow_id = approved_spec_promotion(tmp_path)
    runner, _ = kane_runner(tmp_path / "kane", monkeypatch)
    assert resume_workflow(store, workflow_id, runner=runner).state == "validated"
    return app, spec, store, workflow_id


def second_spec_promotion(tmp_path: Path, app: Path, spec: Path, store: SQLiteWorkflowStore, app_js: str) -> str:
    """Another approved spec-mode proposal for the same app and spec, with another candidate."""
    from attest.app_promotion import create_app_promotion_proposal
    from attest.implement import make_candidate

    doc = ingest_text(spec.read_text(encoding="utf-8"), source=str(spec))
    claims = compile_doc(doc, llm=DeterministicCompiler(), use_cache=False)
    candidate = make_candidate(app, doc.sha256, tmp_path / "home")
    (candidate.root / "app.js").write_text(app_js, encoding="utf-8")
    record = create_app_promotion_proposal(store, candidate=candidate, doc=doc, claims=claims, implemented=["c08"])
    approve_workflow(store, record.workflow_id, "someone-else@example.test")
    return record.workflow_id


def test_control_spec_mode_validation_record_is_sealed_to_its_workflow_approval_artifact_and_event(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    app, _, store, workflow_id = validated_spec_promotion(tmp_path, monkeypatch)
    record = store.get(workflow_id)
    v = record.validation

    assert (v["workflow_id"], v["approval_digest"], v["candidate_digest"]) == (
        workflow_id, record.approval["approval_digest"], record.proposal["candidate_digest"])
    receipt = {key: value for key, value in v.items() if key != "receipt_artifact_digest"}
    assert store.get_artifact(v["receipt_artifact_digest"]) == canonical_json(receipt).encode("utf-8")
    assert [e["receipt_digest"] for e in _validated_events(store, workflow_id)] == [v["receipt_digest"]]
    assert promote_workflow(store, workflow_id).state == "promoted"
    assert "render(greeting)" in (app / "app.js").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "level, refusal",
    [
        ("fields", "does not match its own digests"),
        ("digests", "does not match the receipt artifact"),
        ("artifact", "not the one its validated event recorded"),
    ],
    ids=["fields-rewritten", "digests-recomputed", "artifact-restored"],
)
def test_record_spec_mode_promotion_refuses_a_rewritten_validation_that_its_seals_do_not_hold(tmp_path, monkeypatch, level, refusal):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    app, _, store, workflow_id = validated_spec_promotion(tmp_path, monkeypatch)
    if level == "fields":
        _tamper_validation(store, workflow_id, _launder_earlier_run)
    else:
        _reseal(store, workflow_id, _launder_earlier_run, artifact=level == "artifact", event=False)

    with pytest.raises(WorkflowError, match=refusal):
        promote_workflow(store, workflow_id)
    assert app_unchanged(app)


def test_record_spec_mode_promotion_refuses_a_validation_moved_and_rebound_to_another_candidate(tmp_path, monkeypatch):
    # Spec promotion already compares the record's candidate with the proposal's, so the
    # moved record is rebound to the other candidate, resealed, and its event copied.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    app, spec, store, validated_id = validated_spec_promotion(tmp_path, monkeypatch)
    other_id = second_spec_promotion(tmp_path, app, spec, store, APP_JS + "fetch('https://elsewhere.example.test');\n")
    other_candidate = store.get(other_id).proposal["candidate_digest"]
    _move_validation(store, validated_id, other_id, rebind=lambda v: v.update(candidate_digest=other_candidate))

    with pytest.raises(WorkflowError, match="not bound to this workflow's approval and candidate"):
        promote_workflow(store, other_id)
    assert app_unchanged(app)


def test_record_spec_mode_promotion_refuses_a_rejected_validation_relabelled_validated(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    app, spec, store, workflow_id = approved_spec_promotion(tmp_path)
    runner, cli = kane_runner(tmp_path / "kane", monkeypatch)
    observe_browser_claims(runner, spec)
    assert resume_workflow(store, workflow_id, runner=runner).state == "rejected"
    _reseal(store, workflow_id, lambda v: v.update(successful=True, failed_claims=[]), state="validated")

    with pytest.raises(WorkflowError, match="not the one its validated event recorded"):
        promote_workflow(store, workflow_id)
    assert len(cli.calls) == 1 and app_unchanged(app)


@pytest.mark.parametrize("change", UNPROVEN, ids=UNPROVEN_IDS)
def test_record_spec_mode_promotion_refuses_a_resealed_validation_that_does_not_show_every_claim_proven(tmp_path, monkeypatch, change):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    app, _, store, workflow_id = validated_spec_promotion(tmp_path, monkeypatch)
    _reseal(store, workflow_id, change)

    with pytest.raises(WorkflowError, match="does not show every approved claim proven"):
        promote_workflow(store, workflow_id)
    assert app_unchanged(app)

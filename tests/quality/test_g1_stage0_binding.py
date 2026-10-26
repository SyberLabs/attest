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
    assert isinstance(RunReceipt(**fields), RunReceipt)
    with pytest.raises(ValidationError, match="bound to a fresh Kane run id"):
        VerifiedRunReceipt(**fields)


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


def test_control_receipt_from_observations_on_the_approved_build_verifies(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    source, store, workflow_id, _ = approved_repair(tmp_path, git=True)
    runner, _ = kane_runner(tmp_path / "kane", monkeypatch)
    observations = observe_browser_claims(runner, source)
    approved = store.get(workflow_id).proposal["build_digest"]

    receipt = _receipt_from_observations(approved, observations, runner.run_ids["c07"])

    assert receipt.build_digest == approved
    assert [item.build_digest for item in receipt.evidence] == [approved, approved]

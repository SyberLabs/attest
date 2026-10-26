"""KR-SIM-03: a cached Kane observation replayed against a changed app build (R1, R3, R5).

The scenario: Kane observed the doc's browser steps on build A. The app is then
redeployed (build B), a repair is proposed and approved on B, and validation runs
with the runner that still holds A's observations. Evidence from A, or from no
Kane run of this validation, must not validate, verify a receipt or promote. The
build is read off the ``BrowserObservation`` (the runner stamps the build it measured
around its own run), and receipts are built from the observations with
``BrowserObservation.receipt_evidence``, so the receipt's build is the one Kane saw.

Real code paths: ``KaneBrowserRunner``, ``create_repair_proposal`` /
``approve_workflow`` / ``resume_workflow`` / ``promote_workflow``,
``current_build_digest``, ``BrowserObservation.receipt_evidence`` and
``attest/receipts.py``. Stand-ins: the kane-cli
subprocess (a recorded event-day capture from tests/fixtures/kane/) and the compiler
model (deterministic). This is not a live Kane run.

The case body is ``run_case``, so it can move into ``test_kane_runner.py`` in place
of the inventory's stub without change.
"""

from __future__ import annotations

import difflib
import hashlib
import re
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from attest import kane
from attest.approvals import approve_workflow
from attest.compiler import ClaimSet, LLMClaim, compile_doc
from attest.contracts import AuthorityState, ComparisonState, EvidenceState, ExecutionState, RepairState
from attest.ingest import ingest_text
from attest.kane import KaneBrowserRunner, parse_events
from attest.models import BrowserObservation, Evidence
from attest.proposals import create_repair_proposal, current_build_digest
from attest.receipts import RunReceipt, VerifiedRunReceipt
from attest.storage import SQLiteWorkflowStore
from attest.workflow import WorkflowError, promote_workflow, resume_workflow
from tests.kane_replay import as_written, written_test_file

KANE_FIX = Path(__file__).resolve().parents[1] / "fixtures" / "kane"

# Six prose lines first so the two browser steps compile to c07 and c08, the claim ids
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
    """Quoted "Open"/"Check" lines become browser claims; every other line is prose."""

    def parse_claims(self, system: str, user: str) -> ClaimSet:
        claims = []
        for row in user.splitlines():
            match = re.match(r"^\s*(\d+)  (.+)$", row)
            if not match:
                continue
            line, text = int(match.group(1)), match.group(2)
            quoted = _QUOTE.findall(text)
            browser = text.startswith(("Open ", "Check ")) and bool(quoted)
            claims.append(LLMClaim(
                kind="browser" if browser else "prose", start_line=line, end_line=line, text=text, command=None,
                objective=text if browser else None, expects=quoted[-1] if browser else None,
            ))
        return ClaimSet(claims=claims)


class RecordedKaneCli:
    """Replaces only the kane-cli subprocess: a recorded NDJSON run plus the pack it wrote,
    sealed for the test file the runner wrote (``tests/kane_replay.py``).

    ``during`` runs inside the call, as a redeploy would land while Kane is running.
    """

    def __init__(self, runner: KaneBrowserRunner, fixture: str, during=None):
        self.runner, self.fixture, self.during, self.calls = runner, fixture, during, 0

    def __call__(self, cmd, timeout_s):
        self.calls += 1
        self.runner.evidence_dir.mkdir(parents=True, exist_ok=True)
        as_written(KANE_FIX / f"{self.fixture}.pack.zip", self.runner.evidence_dir / f"{self.fixture}.evidence",
                   written_test_file(cmd).read_bytes())
        if self.during:
            self.during()
        run = parse_events((KANE_FIX / f"{self.fixture}.ndjson").read_text(encoding="utf-8").splitlines())
        run.exit_code = 0
        return run


def kane_runner(home: Path, monkeypatch, fixture: str, during=None) -> tuple[KaneBrowserRunner, RecordedKaneCli]:
    with monkeypatch.context() as patch:
        patch.setattr(kane.shutil, "which", lambda cmd: cmd)  # only the PATH check in __init__
        runner = KaneBrowserRunner(home=home, headless=True)
    runner._execute = cli = RecordedKaneCli(runner, fixture, during)
    return runner, cli


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.email=rt@example.test", "-c", "user.name=rt", *args],
                   cwd=root, check=True, capture_output=True)


def app_repo(root: Path) -> Path:
    """A git worktree holding the doc; its build identity is ``current_build_digest``."""
    root.mkdir(parents=True)
    source = root / "greeting.md"
    source.write_text(BASE_DOC, encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", "greeting.md")
    _git(root, "commit", "-q", "-m", "baseline")
    return source


def deploy(source: Path, change: dict) -> None:
    (source.parent / change["path"]).write_text(change["text"], encoding="utf-8")


def build_of(source: Path) -> str:
    return current_build_digest(source, hashlib.sha256(source.read_bytes()).hexdigest())


def approved_repair(source: Path, store_path: Path, llm) -> tuple[SQLiteWorkflowStore, str]:
    """A proposal relabelling c08, approved on whatever build the worktree is at now."""
    baseline = compile_doc(ingest_text(BASE_DOC, source=str(source)), llm=llm, use_cache=False)
    target = next(claim for claim in baseline if claim.id == "c08")
    diff = "".join(difflib.unified_diff(BASE_DOC.splitlines(True), CANDIDATE_DOC.splitlines(True),
                                        "a/greeting.md", "b/greeting.md"))
    store = SQLiteWorkflowStore(store_path)  # outside the worktree: not a build input
    record = create_repair_proposal(
        store, source_path=source, base_text=BASE_DOC, candidate_text=CANDIDATE_DOC, diff=diff,
        target_claim=target, baseline_claims=baseline, plan="Relabel c08 to the greeting the page shows.",
    )
    assert record.state == "awaiting_approval"
    approve_workflow(store, record.workflow_id, "maintainer@example.test")
    return store, record.workflow_id


def receipt_fields(build: str | None, runs: tuple[str, ...],
                   evidence: list[tuple[BrowserObservation, str | None]]) -> dict:
    """A receipt whose states would all verify; only its build and Kane run bindings vary.

    Each evidence record is an observation and the Kane run it is bound to, turned into
    receipt evidence by the product (``BrowserObservation.receipt_evidence``).
    """
    fields = {
        "snapshot_id": uuid4(),
        "source_version_id": uuid4(),
        "content_sha256": hashlib.sha256(CANDIDATE_DOC.encode("utf-8")).hexdigest(),
        "execution_state": ExecutionState.SUCCEEDED,
        "comparison_state": ComparisonState.MATCH,
        "authority_state": AuthorityState.ALLOWED,
        "evidence_state": EvidenceState.COMPLETE,
        "repair_state": RepairState.VALIDATED,
        "build_digest": build,
        "kane_run_ids": runs,
    }
    fields["evidence"] = tuple(
        observation.receipt_evidence(source_version_id=fields["source_version_id"],
                                     content_sha256=fields["content_sha256"], kane_run_id=run)
        for observation, run in evidence
    )
    return fields


def observations(validation: dict, claim_ids) -> dict[str, BrowserObservation]:
    """The browser observations a validation record stored, as the product reads them back."""
    return {cid: Evidence.model_validate(validation["evidence"][cid]).observation for cid in sorted(claim_ids)}


def run_case(rt_case: dict, tmp_path: Path, monkeypatch) -> None:
    i, x = rt_case["input"], rt_case["expected"]
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    llm = DeterministicCompiler()
    browser = set(i["browser_claims"])

    # 1. Kane observes the candidate's browser steps on build A; the runner caches them.
    source = app_repo(tmp_path / "replay" / "app")
    build_a = build_of(source)
    runner, cli = kane_runner(tmp_path / "replay" / "kane", monkeypatch, i["fixture"])
    claims = compile_doc(ingest_text(CANDIDATE_DOC, source=str(source)), llm=llm, use_cache=False)
    runner.prepare(claims, str(source))
    for claim in claims:
        if claim.kind == "browser":
            assert runner.observe(claim, None).status == "passed", claim.id  # good evidence, for build A
    earlier_run = runner.run_ids["c07"]
    assert set(runner.run_ids) == browser and set(runner.run_ids.values()) == {earlier_run} and cli.calls == 1
    # The runner stamps each observation with the build it measured around its own Kane run.
    assert {cid: runner.results[cid][1].build_digest for cid in browser} == {cid: build_a for cid in browser}

    # The app is redeployed; the repair is proposed and approved on build B.
    deploy(source, i["deploy"])
    build_b = build_of(source)
    assert build_b != build_a
    store, workflow_id = approved_repair(source, tmp_path / "replay" / "workflow.sqlite3", llm)
    assert store.get(workflow_id).proposal["build_digest"] == build_b

    # 2. Validation on B is served A's cached observations: rejected, never promoted.
    runs_before = len(runner.runs)
    outcome = resume_workflow(store, workflow_id, runner=runner, llm=llm, apply=True)
    replay = x["cached_replay"]
    assert cli.calls - 1 == replay["kane_calls_during_validation"]
    assert outcome.state == replay["state"], outcome.validation
    assert set(replay["failed"]) <= set(outcome.validation["failed_claims"])
    assert outcome.validation["build_digest"] == build_b
    with pytest.raises(WorkflowError):
        promote_workflow(store, workflow_id)
    assert source.read_text(encoding="utf-8") == BASE_DOC
    # The stored evidence still names the build Kane saw: A, not the approved B.
    served = observations(outcome.validation, browser)
    assert {obs.build_digest for obs in served.values()} == {build_a}

    # 3. The same facts as a receipt cannot verify. The real receipt is built from the
    #    observations validation was served, bound to the run that made them: it names
    #    build A in a receipt for B, and a run this validation did not start.
    fresh_runs = tuple(r.session_id for r in runner.runs[runs_before:] if r.session_id)
    assert fresh_runs == ()
    rejections = x["receipt_rejections"]
    real = [(obs, earlier_run) for obs in served.values()]
    for receipt in (RunReceipt, VerifiedRunReceipt):
        with pytest.raises(ValidationError, match=re.escape(rejections["evidence_from_earlier_build"])):
            receipt(**receipt_fields(build_b, fresh_runs, real))
    # Each variant below differs from a receipt that would verify in one binding only. To
    # isolate the run binding, a copy of the observation is restamped with build B; that copy
    # is test-made, the product never produces it from a cached run.
    seen_on_a = served["c07"]
    seen_on_b = seen_on_a.model_copy(update={"build_digest": build_b})
    unmeasured = seen_on_a.model_copy(update={"build_digest": None})
    bound = (seen_on_b, earlier_run)
    assert VerifiedRunReceipt(**receipt_fields(build_b, (earlier_run,), [bound])).build_digest == build_b
    cases = {
        "evidence_from_earlier_build": (build_b, (earlier_run,), (seen_on_a, earlier_run)),
        "evidence_build_stripped": (build_b, (earlier_run,), (unmeasured, earlier_run)),
        "receipt_build_stripped": (None, (earlier_run,), (unmeasured, earlier_run)),
        "evidence_from_earlier_run": (build_b, fresh_runs, bound),
        "evidence_run_stripped": (build_b, (earlier_run,), (seen_on_b, None)),
    }
    assert set(cases) == set(rejections)
    for name, (build, runs, evidence) in cases.items():
        with pytest.raises(ValidationError, match=re.escape(rejections[name])):
            VerifiedRunReceipt(**receipt_fields(build, runs, [evidence]))
    # A plain RunReceipt, which may lack bindings, still refuses evidence that names the wrong ones.
    for name in ("evidence_from_earlier_build", "evidence_from_earlier_run"):
        build, runs, evidence = cases[name]
        with pytest.raises(ValidationError, match=re.escape(rejections[name])):
            RunReceipt(**receipt_fields(build, runs, [evidence]))

    # 4. A redeploy while Kane is running: the evidence describes neither build.
    source = app_repo(tmp_path / "mid-run" / "app")
    deploy(source, i["deploy"])
    store, workflow_id = approved_repair(source, tmp_path / "mid-run" / "workflow.sqlite3", llm)
    runner, cli = kane_runner(tmp_path / "mid-run" / "kane", monkeypatch, i["fixture"],
                              during=lambda: deploy(source, i["redeploy"]))
    outcome = resume_workflow(store, workflow_id, runner=runner, llm=llm, apply=True)
    assert cli.calls == 1
    mid_run = x["build_change_during_run"]
    assert outcome.state == mid_run["state"]
    assert mid_run["reason"] in outcome.validation["reason"]
    with pytest.raises(WorkflowError):
        promote_workflow(store, workflow_id)
    assert source.read_text(encoding="utf-8") == BASE_DOC
    # The runner saw the build change around its run, so its observations name no build,
    # and a receipt made from them cannot verify even for the run that made them.
    seen = [runner.results[cid][1] for cid in sorted(browser)]
    assert [obs.build_digest for obs in seen] == [None] * len(browser)
    with pytest.raises(ValidationError, match=re.escape(mid_run["receipt"])):
        VerifiedRunReceipt(**receipt_fields(build_of(source), (runner.run_ids["c07"],),
                                            [(obs, runner.run_ids[cid]) for cid, obs in zip(sorted(browser), seen)]))

    # 5. Control: a fresh Kane run on the approved build validates, its receipt verifies,
    #    and it promotes. A product that refuses everything fails here.
    source = app_repo(tmp_path / "control" / "app")
    deploy(source, i["deploy"])
    build = build_of(source)
    store, workflow_id = approved_repair(source, tmp_path / "control" / "workflow.sqlite3", llm)
    runner, cli = kane_runner(tmp_path / "control" / "kane", monkeypatch, i["fixture"])
    validated = resume_workflow(store, workflow_id, runner=runner, llm=llm)
    assert validated.state == "validated", validated.validation
    validation = validated.validation
    fresh = parse_events((KANE_FIX / f"{i['fixture']}.ndjson").read_text(encoding="utf-8").splitlines()).session_id
    assert cli.calls == 1 and validation["build_digest"] == build
    assert validation["kane_runs"] == {cid: fresh for cid in browser}
    seen = observations(validation, browser)
    assert {obs.build_digest for obs in seen.values()} == {build}
    receipt = VerifiedRunReceipt(**receipt_fields(
        validation["build_digest"], tuple(sorted(set(validation["kane_runs"].values()))),
        [(obs, validation["kane_runs"][cid]) for cid, obs in seen.items()],
    ))
    assert receipt.build_digest == build and receipt.kane_run_ids == (fresh,)

    # 6. The same validated record, after the app moves on, no longer promotes.
    deploy(source, i["redeploy"])
    with pytest.raises(WorkflowError, match=re.escape(x["build_change_before_promote"])):
        promote_workflow(store, workflow_id)
    assert source.read_text(encoding="utf-8") == BASE_DOC
    # Back on the approved build it does: the refusal above was the build binding, nothing else.
    deploy(source, i["deploy"])
    assert build_of(source) == build
    assert promote_workflow(store, workflow_id).state == x["control"]["state"]
    assert source.read_text(encoding="utf-8") == CANDIDATE_DOC

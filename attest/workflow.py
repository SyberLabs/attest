"""Durable approval, whole-document validation, and digest-checked promotion."""

from __future__ import annotations

import hashlib
import json
import shutil
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from .app_promotion import SPEC_APP_KIND, promote_app_workflow, resume_app_workflow
from .approvals import ApprovalError, verify_approval
from .compiler import _validate_compiled_claims, compile_doc
from .docs_source import SourceDir, SourcePathError
from .execution.recipes import ApprovedRecipeExecutor
from .ingest import ingest_text, sha256_of
from .kane import PACK_BEFORE_APPROVAL, PACK_WORDING, pack_disagreement
from .models import BrowserObservation, Claim
from .policy import MaintainDocsPolicy
from .pipeline import execute
from .proposals import current_build_digest, proposal_digest
from .shell import new_scratch
from .storage import SQLiteWorkflowStore, WorkflowRecord, WorkflowStorageError, canonical_json
from .verdict import Rules, load_rules


class WorkflowError(RuntimeError):
    pass


class DocsApprovalError(ApprovalError):
    """A Maintain Docs approval was refused; ``code`` is one of DOCS_APPROVAL_CODES."""

    def __init__(self, code: str, detail: str):
        super().__init__(f"docs approval refused ({code}): {detail}")
        self.code = code


DOCS_APPROVAL_CODES = (
    "approval_missing", "approval_unbound", "approval_not_human", "authority_not_allowed",
    "approval_unrecorded", "approval_stale",
)

# The events a Maintain Docs workflow has once `attest approve` has run, in order.
_APPROVED_STEPS = [(None, "proposed"), ("proposed", "awaiting_approval"), ("awaiting_approval", "validating")]


def verify_docs_approval(store: SQLiteWorkflowStore, record: WorkflowRecord) -> None:
    """The approval is a human's, and is the one ``attest approve`` recorded for this workflow.

    ``verify_approval`` checks the approval against the proposal and its own digest, so a
    digest-correct approval written into the record (or copied from a twin workflow) passes
    it. This also reads the event chain back: the workflow was offered for approval by an
    allowing authority under the proposal's policy, then approved once, by this approver,
    with this approval digest, at a time between the two events.
    """
    approval = record.approval
    if not approval:
        raise DocsApprovalError("approval_missing", "no human approval is recorded for this workflow")
    try:
        verify_approval(record)
    except ApprovalError as exc:
        raise DocsApprovalError("approval_unbound", str(exc)) from exc
    actor = approval.get("actor")
    # `policy:<name>` is how receipts name an automatic decision, never a person
    if not isinstance(actor, str) or not actor.strip() or actor != actor.strip() or actor.lower().startswith("policy:"):
        raise DocsApprovalError("approval_not_human", f"approver {actor!r} is not a human identity")
    if not (record.proposal.get("authority") or {}).get("allowed"):
        raise DocsApprovalError("authority_not_allowed", "authority policy did not authorize this proposal")
    events = store.events(record.workflow_id)
    steps = [(e["from_state"], e["to_state"]) for e in events]
    if steps[:3] != _APPROVED_STEPS or any(step in _APPROVED_STEPS for step in steps[3:]):
        raise DocsApprovalError("approval_unrecorded", "the workflow has no single recorded proposal and approval")
    offered, approved = events[1], events[2]
    if offered["payload"] != {"authority": "allowed", "policy_digest": record.proposal.get("policy_digest")}:
        raise DocsApprovalError("approval_unrecorded", "the workflow was not offered for approval by an allowing authority")
    if approved["payload"] != {"actor": actor, "approval_digest": approval.get("approval_digest")}:
        raise DocsApprovalError("approval_unrecorded", "the approval is not the one `attest approve` recorded")
    try:
        at = datetime.fromisoformat(approval["approved_at"])
        start, end = datetime.fromisoformat(offered["created_at"]), datetime.fromisoformat(approved["created_at"])
        inside = at.tzinfo is not None and start <= at <= end
    except (KeyError, TypeError, ValueError):
        inside = False
    if not inside:
        raise DocsApprovalError("approval_stale", "the approval was not given between this proposal and its approval event")


def _refuse_docs_approval(store: SQLiteWorkflowStore, record: WorkflowRecord) -> None:
    """--apply: the approval is the one `attest approve` recorded (verify_docs_approval)."""
    try:
        verify_docs_approval(store, record)
    except ApprovalError as exc:
        raise WorkflowError(f"{exc}; --apply refused") from exc


def _unresolved(store: SQLiteWorkflowStore, record: WorkflowRecord, reason: str) -> WorkflowRecord:
    return store.transition(
        record.workflow_id,
        "validating",
        "unresolved",
        validation={"successful": False, "reason": reason},
        event={"reason": reason},
    )


def _claim_fingerprint(claim: Claim) -> str:
    payload = claim.model_dump(mode="json")
    payload["span"] = {"text": claim.span.text}  # line shifts do not change claim meaning
    return canonical_json(payload)


def _compile_complete(original: list[Claim], compiled: list[Claim], target_id: str) -> tuple[bool, str]:
    old_target = next((claim for claim in original if claim.id == target_id), None)
    new_target = next((claim for claim in compiled if claim.id == target_id), None)
    if old_target is None:
        return False, "approved target claim is missing from its baseline"
    if new_target is None:
        return False, "whole-document compilation did not produce the approved target claim"
    if new_target.kind != old_target.kind:
        return False, "whole-document compilation changed the target claim kind"
    old_other = Counter(_claim_fingerprint(c) for c in original if c.id != target_id)
    new_other = Counter(_claim_fingerprint(c) for c in compiled if c.id != target_id)
    if old_other != new_other:
        return False, "whole-document compilation omitted, added, or changed an unrelated claim"
    if len(compiled) < len(original):
        return False, "whole-document compilation is incomplete"
    return True, "complete"


def _validation_digest(payload: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


# A validation record's seals: what it is digested to, and the immutable artifact holding it.
_SEALS = ("receipt_digest", "receipt_artifact_digest")


def _seal_validation(store: SQLiteWorkflowStore, record: WorkflowRecord, validation: dict[str, Any]) -> dict[str, str]:
    """Bind the record to this workflow and its approval, digest it, and store it as an artifact.

    Returns the seals, which the validated event records too.
    """
    validation["workflow_id"] = record.workflow_id
    validation["approval_digest"] = (record.approval or {}).get("approval_digest")
    validation["receipt_digest"] = _validation_digest(validation)
    validation["receipt_artifact_digest"] = store.put_artifact(canonical_json(validation))
    return {key: validation[key] for key in _SEALS}


def _kane_run_bindings(runner, runs_before: int, claims: list[Claim], evidence: dict) -> tuple[dict[str, str | None], list[str]]:
    """Bind each browser claim's evidence to the Kane run that produced it during this validation.

    An observation the runner cached from an earlier run, or one no evidence pack backs,
    is stale: it says nothing about the candidate being validated now.
    """
    runs = list(getattr(runner, "runs", None) or [])
    fresh = {run.session_id for run in runs[runs_before:] if getattr(run, "session_id", None)}
    produced = getattr(runner, "run_ids", None) or {}
    bindings: dict[str, str | None] = {}
    stale: list[str] = []
    for claim in claims:
        if claim.kind != "browser" or claim.id not in evidence:
            continue
        run_id = produced.get(claim.id)
        bindings[claim.id] = run_id
        if run_id is None or run_id not in fresh:
            stale.append(f"{claim.id}:stale_kane_run")
    return bindings, stale


def _observation_build_failures(claims: list[Claim], evidence: dict, build_digest: str | None) -> list[str]:
    """Browser evidence must name the app/build it was observed on, and that must be the approved one."""
    failures: list[str] = []
    for claim in claims:
        ev = evidence.get(claim.id)
        if claim.kind != "browser" or ev is None or ev.observation is None:
            continue
        observed = ev.observation.build_digest
        if observed is None:
            failures.append(f"{claim.id}:observation_build_missing")
        elif observed != build_digest:
            failures.append(f"{claim.id}:observation_build_mismatch")
    return failures


def _observation_run_failures(claims: list[Claim], evidence: dict, kane_runs: dict[str, str | None]) -> list[str]:
    """Browser evidence must name the Kane run validation binds it to (the runner's own record)."""
    failures: list[str] = []
    for claim in claims:
        ev = evidence.get(claim.id)
        bound = kane_runs.get(claim.id)
        if claim.kind != "browser" or ev is None or ev.observation is None or bound is None:
            continue  # no binding is already stale_kane_run
        observed = ev.observation.kane_run_id
        if observed is None:
            failures.append(f"{claim.id}:observation_run_missing")
        elif observed != bound:
            failures.append(f"{claim.id}:observation_run_mismatch")
    return failures


def _approved_at(approval: dict | None) -> datetime | None:
    """When the human approved the workflow (``approved_at``, which the approval digest covers); None if unreadable."""
    try:
        approved_at = datetime.fromisoformat(str((approval or {})["approved_at"]))
    except (KeyError, ValueError):
        return None
    return approved_at if approved_at.tzinfo else None


APPROVAL_NOT_RECORDED = "approval is not the one its approval event recorded"
APPROVAL_BEFORE_PROPOSAL = "approval is dated before the workflow was put up for approval"


def _approval_disagreement(store: SQLiteWorkflowStore, record: WorkflowRecord) -> str | None:
    """Why the stored approval is not the one the human gave, by the store's own events; None when it is.

    Each pack's run is read against ``approved_at``. The approval digest covers it, but a
    writer can backdate it and recompute the digest. ``approve_workflow`` writes the approval
    and, in the same transaction, an event naming its digest, and it only runs once the
    workflow is ``awaiting_approval``. So the approval must be the one its only approval event
    names, dated no earlier than the event that put the workflow up for approval. Both times
    are this host's clock, compared with no tolerance.
    """
    events = store.events(record.workflow_id)
    approval = record.approval or {}
    recorded = [e["payload"].get("approval_digest") for e in events if (e["from_state"], e["to_state"]) == ("awaiting_approval", "validating")]
    if recorded != [approval.get("approval_digest")]:
        return APPROVAL_NOT_RECORDED
    try:
        put_up = [datetime.fromisoformat(str(e["created_at"])) for e in events if e["to_state"] == "awaiting_approval"]
    except ValueError:
        put_up = []
    approved_at = _approved_at(approval)
    if approved_at is None or len(put_up) != 1 or put_up[0].tzinfo is None or approved_at < put_up[0]:
        return APPROVAL_BEFORE_PROPOSAL
    return None


def _refuse_unrecorded_approval(store: SQLiteWorkflowStore, record: WorkflowRecord) -> None:
    """Promotion: the approval is the one its approval event recorded, dated after the workflow was put up for it."""
    if problem := _approval_disagreement(store, record):
        raise WorkflowError(f"{problem}; --apply refused")


def _pack_read_back(pack: dict | None, claim: Claim, run_id: str | None, approval: dict | None) -> tuple[str, str] | None:
    """Read a claim's Kane evidence pack back from disk: (failure code, detail), or None when it backs the claim.

    The pack's step must also be the wording Attest writes for this (approved) claim, and its
    run must have started no earlier than the approval: validation only runs Kane after it.
    """
    if not isinstance(pack, dict) or not pack.get("path") or not pack.get("sha256"):
        return "kane_pack_missing", "names no Kane evidence pack"
    try:
        data = Path(str(pack["path"])).read_bytes()
    except OSError:
        return "kane_pack_missing", "its Kane evidence pack is missing"
    if hashlib.sha256(data).hexdigest() != pack["sha256"]:
        return "kane_pack_mismatch", "its Kane evidence pack is not the one the Kane run read"
    approved_at = _approved_at(approval)
    if approved_at is None:
        return "kane_pack_before_approval", "the approval records no time to read the pack's run against"
    detail = pack_disagreement(data, claim.id, run_id, claim, not_before=approved_at)
    if detail == PACK_WORDING:
        return "kane_pack_wording", detail
    if detail == PACK_BEFORE_APPROVAL:
        return "kane_pack_before_approval", detail
    return ("kane_pack_mismatch", detail) if detail else None


def _kane_pack_bindings(
    runner, claims: list[Claim], evidence: dict, kane_runs: dict[str, str | None], approval: dict | None
) -> tuple[dict[str, dict | None], list[str]]:
    """Name the evidence pack behind each bound browser claim (the runner's record), and read it back.

    A passed observation is Kane's word only while its pack is still there, unchanged since
    the run read it, and shows the claim's own step passing in the bound run, a run that
    started after ``approval``.
    """
    recorded = getattr(runner, "packs", None) or {}
    packs: dict[str, dict | None] = {}
    failures: list[str] = []
    for claim in claims:
        ev = evidence.get(claim.id)
        run_id = kane_runs.get(claim.id)
        if claim.kind != "browser" or ev is None or ev.observation is None or run_id is None:
            continue  # no binding is already stale_kane_run
        packs[claim.id] = recorded.get(claim.id)
        if ev.observation.status == "passed" and (problem := _pack_read_back(packs[claim.id], claim, run_id, approval)):
            failures.append(f"{claim.id}:{problem[0]}")
    return packs, failures


def _sealed_elsewhere(store: SQLiteWorkflowStore, workflow_id: str, own_receipt: str | None) -> dict[str, str]:
    """The Kane sessions another sealed validation in the store binds: session id -> whose.

    "Another" is any other workflow's stored record, and any receipt artifact validation
    sealed except ``own_receipt`` (this record's own): another workflow's, which stays when an
    unresolved transition overwrites its record, or one from an earlier attempt at this
    workflow's validation that stopped before its transition. The pack needs no key of its
    own: since #54 it has to name the session it backs.
    """
    sealed: dict[str, str] = {}

    def note(validation: Any, whose: str) -> None:
        runs = validation.get("kane_runs") if isinstance(validation, dict) else None
        for run_id in runs.values() if isinstance(runs, dict) else ():
            if isinstance(run_id, str):
                sealed.setdefault(run_id, whose)

    for other_id, validation in store.validations():
        if other_id != workflow_id:
            note(validation, f"workflow {other_id}'s validation record")
    for artifact_digest, content in store.artifacts_containing(b'"receipt_digest":'):
        if artifact_digest == own_receipt:
            continue
        try:
            receipt = json.loads(content)
        except ValueError:
            continue
        if isinstance(receipt, dict) and "receipt_digest" in receipt and "workflow_id" in receipt:
            note(receipt, f"validation receipt {artifact_digest[:12]} of workflow {receipt['workflow_id']}")
    return sealed


def _reused_kane_runs(store: SQLiteWorkflowStore, workflow_id: str, own_receipt: str | None, kane_runs: dict) -> list[tuple[str, str]]:
    """(claim id, detail) for each bound browser claim whose Kane session another sealed validation binds.

    Each validation starts its own Kane runs after the approval, so a run another validation
    in this store already bound (another workflow's, or an earlier attempt at this one's) is
    not this validation's, even when its pack is of the approved wording and started after
    the approval.
    """
    sealed = _sealed_elsewhere(store, workflow_id, own_receipt)
    return [
        (claim_id, f"Kane session {run_id} is bound by {sealed[run_id]}")
        for claim_id, run_id in kane_runs.items()
        if isinstance(run_id, str) and run_id in sealed
    ]


def _refuse_reused_kane_runs(store: SQLiteWorkflowStore, record: WorkflowRecord) -> None:
    """Promotion: no browser claim's Kane run is one another sealed validation in the store binds.

    Checked last: a record that passes every other check names packs of the approved wording
    from runs started after the approval, and this is what tells this validation's runs from
    another's.
    """
    validation = record.validation or {}
    reused = _reused_kane_runs(store, record.workflow_id, validation.get("receipt_artifact_digest"), validation.get("kane_runs") or {})
    if reused:
        claim_id, detail = reused[0]
        raise WorkflowError(f"validation browser evidence for {claim_id} is from another validation's Kane run ({detail}); --apply refused")


def _refuse_unbound_browser_evidence(validation: dict[str, Any], browser: list[str], build_digest: str | None) -> None:
    """Promotion: each browser claim's stored evidence is from its bound fresh Kane run, on the approved build."""
    kane_runs = validation.get("kane_runs") or {}
    if any(not kane_runs.get(claim_id) for claim_id in browser):
        raise WorkflowError("validation browser evidence is not bound to a fresh Kane run; --apply refused")
    evidence = validation.get("evidence") or {}
    observed = [((evidence.get(claim_id) or {}).get("observation") or {}).get("build_digest") for claim_id in browser]
    if any(build is None or build != build_digest for build in observed):
        raise WorkflowError("validation browser evidence was not observed on the approved app/build version; --apply refused")
    # Stored evidence is read back as the product reads it (the subclass is lost; the note
    # prefixes are not): only what receipt evidence would mark complete, from the bound run.
    try:
        stored = {claim_id: BrowserObservation.model_validate(evidence[claim_id]["observation"]) for claim_id in browser}
    except Exception as exc:
        raise WorkflowError(f"validation browser evidence is unreadable; --apply refused: {exc}") from exc
    if any(obs.kane_run_id != kane_runs.get(claim_id) for claim_id, obs in stored.items()):
        raise WorkflowError("validation browser evidence does not name the Kane run it is bound to; --apply refused")
    if any(obs.status != "passed" or obs.simulation_only or obs.unbound for obs in stored.values()):
        raise WorkflowError("validation browser evidence is simulated, unbound or not passed; --apply refused")


def _refuse_unbacked_browser_evidence(validation: dict[str, Any], browser: list[Claim], approval: dict | None) -> None:
    """Promotion: each approved browser claim's Kane evidence pack, read back from disk, is
    the one validation read and shows the claim's step, in the claim's wording, passing in
    its bound run, which started after ``approval``.

    The pack lives outside the store, so a record rewritten with its artifact and event still
    has to name a pack Kane wrote for the run it binds, from a test file worded as approved,
    run after the human approved this workflow.
    """
    packs = validation.get("kane_packs") or {}
    kane_runs = validation.get("kane_runs") or {}
    for claim in browser:
        problem = _pack_read_back(packs.get(claim.id), claim, kane_runs.get(claim.id), approval)
        if problem:
            raise WorkflowError(
                f"validation browser evidence for {claim.id} is not backed by its Kane evidence pack ({problem[1]}); --apply refused"
            )


def _refuse_unproven_claims(validation: dict[str, Any], claims: list[Claim]) -> None:
    """Promotion: the record shows every approved required claim proven, and no failure."""
    statuses = validation.get("statuses") or {}
    required = [claim.id for claim in claims if claim.kind in {"shell", "browser", "assert"}]
    if validation.get("failed_claims") or any(statuses.get(claim_id) != "proven" for claim_id in required):
        raise WorkflowError("validation record does not show every approved claim proven; --apply refused")


def _refuse_unsealed_validation(store: SQLiteWorkflowStore, record: WorkflowRecord) -> None:
    """Promotion: the stored validation is the record this workflow's own validation sealed.

    The other promotion checks make the record agree with itself. These make it agree with
    what lives outside it: this workflow's approval and candidate, the receipt artifact
    validation stored, and the validated event. A rewrite of every field of the record
    together is refused. One that also rewrites that artifact and event row is not: that
    needs a key the store doesn't hold.
    """
    validation = record.validation or {}
    if (
        validation.get("workflow_id") != record.workflow_id
        or validation.get("approval_digest") != (record.approval or {}).get("approval_digest")
        or validation.get("candidate_digest") != record.proposal.get("candidate_digest")
    ):
        raise WorkflowError("validation record is not bound to this workflow's approval and candidate; --apply refused")
    unsealed = {key: value for key, value in validation.items() if key not in _SEALS}
    if (
        validation.get("evidence_digest") != _validation_digest(validation.get("evidence") or {})
        or validation.get("receipt_digest") != _validation_digest(unsealed)
    ):
        raise WorkflowError("validation record does not match its own digests; --apply refused")
    try:
        stored = store.get_artifact(str(validation.get("receipt_artifact_digest")))
    except WorkflowStorageError:
        stored = None
    receipt = {key: value for key, value in validation.items() if key != "receipt_artifact_digest"}
    if stored != canonical_json(receipt).encode("utf-8"):
        raise WorkflowError("validation record does not match the receipt artifact validation stored; --apply refused")
    events = [e["payload"] for e in store.events(record.workflow_id) if (e["from_state"], e["to_state"]) == ("validating", "validated")]
    if [{key: payload.get(key) for key in _SEALS} for payload in events] != [{key: validation[key] for key in _SEALS}]:
        raise WorkflowError("validation record is not the one its validated event recorded; --apply refused")


def _authority_refusal(store: SQLiteWorkflowStore, proposal: dict[str, Any], policy: MaintainDocsPolicy) -> str | None:
    """Re-derive the patch authority from the approved artifacts: why the policy refuses
    this candidate now, or None when it still authorizes it.

    The decision stored at proposal time is this Attest's word only while its authority
    logic is unchanged. Equal policy digests don't show that: the digest covers the
    policy's fields, not the code that reads them. So validation and --apply ask again.
    """
    artifacts = proposal.get("artifact_digests") or {}
    try:
        base_text = store.get_artifact(artifacts["base"]).decode("utf-8")
        candidate_text = store.get_artifact(artifacts["candidate"]).decode("utf-8")
        diff = store.get_artifact(artifacts["diff"]).decode("utf-8")
        baseline = json.loads(store.get_artifact(artifacts["baseline_claims"]))
        kinds = [c.get("kind") for c in baseline if c.get("id") == proposal["target_claim_id"]]
        decision = policy.authorize(
            diff=diff,
            doc_name=Path(proposal["source_path"]).name,
            base_text=base_text,
            candidate_text=candidate_text,
            target_start=int(proposal["target_start_line"]),
            target_end=int(proposal["target_end_line"]),
            target_claim_id=str(proposal["target_claim_id"]),
            base_digest=str(proposal["base_digest"]),
            candidate_digest=str(proposal["candidate_digest"]),
            target_kind=kinds[0] if len(kinds) == 1 else None,  # unknown: judged as prose, the stricter
        )
    except Exception as exc:
        return f"its patch authority cannot be re-derived: {type(exc).__name__}: {exc}"
    return None if decision.allowed else decision.reason


def resume_workflow(
    store: SQLiteWorkflowStore,
    workflow_id: str,
    *,
    runner,
    apply: bool = False,
    simulation: bool = False,
    policy: MaintainDocsPolicy | None = None,
    rules: Rules | None = None,
    llm=None,
    shell_env: dict[str, str] | None = None,
    recipe_executor: ApprovedRecipeExecutor | None = None,
) -> WorkflowRecord:
    """Validate a human-approved proposal with fresh Kane evidence, optionally promote.

    Validation shell claims always run recipes-only: the candidate document is
    model-authored, so it may select an approved recipe but never reach a shell.
    """
    record = store.get(workflow_id)
    if record.proposal.get("kind") == SPEC_APP_KIND:
        return resume_app_workflow(
            store, workflow_id, runner=runner, apply=apply, simulation=simulation, rules=rules, shell_env=shell_env,
        )
    policy = policy or MaintainDocsPolicy()
    if simulation or getattr(runner, "name", None) != "kane":
        raise WorkflowError("promotion validation requires fresh Kane evidence; simulation/fake evidence is not accepted")
    if record.state == "validated":
        _refuse_docs_approval(store, record)
        _refuse_unrecorded_approval(store, record)
        if apply:
            return promote_workflow(store, workflow_id, policy=policy)
        return record
    if record.state != "validating":
        raise WorkflowError(f"workflow is {record.state}; only an approved workflow can be resumed")
    try:
        verify_docs_approval(store, record)
    except ApprovalError as exc:
        return _unresolved(store, record, str(exc))
    if problem := _approval_disagreement(store, record):
        return _unresolved(store, record, problem)

    proposal = record.proposal
    if proposal_digest(proposal) != proposal.get("proposal_digest"):
        return _unresolved(store, record, "proposal digest no longer matches its approved contents")
    if policy.digest != proposal.get("policy_digest"):
        return _unresolved(store, record, "Maintain Docs policy digest changed after approval")
    artifacts = proposal["artifact_digests"]
    try:
        base_text = store.get_artifact(artifacts["base"]).decode("utf-8")
        candidate_text = store.get_artifact(artifacts["candidate"]).decode("utf-8")
        original_claims = [Claim.model_validate(x) for x in json.loads(store.get_artifact(artifacts["baseline_claims"]))]
    except Exception as exc:
        return _unresolved(store, record, f"proposal artifact invalid or unavailable: {exc}")
    candidate_bytes = store.get_artifact(artifacts["candidate"])
    if (
        sha256_of(base_text) != proposal.get("base_digest")
        or hashlib.sha256(candidate_bytes).hexdigest() != proposal.get("candidate_digest")
    ):
        return _unresolved(store, record, "base or candidate artifact digest mismatch")
    if refusal := _authority_refusal(store, proposal, policy):
        return _unresolved(store, record, f"Maintain Docs policy no longer authorizes this candidate ({refusal}); promotion refused")
    source_path = Path(proposal["source_path"])
    # Read through the stored path with no symlink on it: a directory swapped for a link to
    # a clone at the same commit would match the source digest and the build identity.
    try:
        with SourceDir(source_path) as source:
            source_bytes = source.read()
        source_text = source_bytes.decode("utf-8")
    except SourcePathError as exc:
        return _unresolved(store, record, str(exc))
    except (OSError, UnicodeError) as exc:
        return _unresolved(store, record, f"cannot read source for digest check: {exc}")
    if (
        hashlib.sha256(source_bytes).hexdigest() != proposal.get("source_digest")
        or sha256_of(source_text) != proposal.get("base_digest")
    ):
        return _unresolved(store, record, "source changed after approval; promotion refused")
    if current_build_digest(source_path, proposal["source_digest"]) != proposal.get("build_digest"):
        return _unresolved(store, record, "build identity changed after approval; promotion refused")

    doc = ingest_text(candidate_text, source=str(source_path))
    runs_before = len(getattr(runner, "runs", None) or [])
    try:
        claims = compile_doc(doc, llm=llm, use_cache=False)
        complete, detail = _compile_complete(original_claims, claims, proposal["target_claim_id"])
        if not complete:
            return store.transition(
                workflow_id,
                "validating",
                "unresolved",
                validation={"successful": False, "reason": detail, "runner": runner.name},
                event={"reason": detail},
            )
        doc = doc.model_copy(update={"claims": claims})
        prepare = getattr(runner, "prepare", None)
        if prepare is not None:
            prepare(claims, str(source_path))
        # No executor means an empty allow-list: every shell claim fails closed.
        if recipe_executor is None:
            recipe_executor = ApprovedRecipeExecutor({})
        scratch = new_scratch("attest-validate-")
        session = None
        try:
            evidence, verdicts, session = execute(
                claims,
                runner,
                rules or load_rules(),
                scratch,
                shell_env=shell_env,
                recipes_only=True,
                recipe_executor=recipe_executor,
            )
            statuses = {verdict.claim_id: verdict.status for verdict in verdicts}
            required = [claim for claim in claims if claim.kind in {"shell", "browser", "assert"}]
            failed = [f"{claim.id}:{statuses.get(claim.id, 'missing')}" for claim in required if statuses.get(claim.id) != "proven"]
            evidence_payload = {key: value.model_dump(mode="json") for key, value in evidence.items()}
            validation_data: dict[str, Any] = {
                "successful": not failed,
                "runner": runner.name,
                "shell_mode": "recipes_only",
                "recipe_digest": recipe_executor.digest,
                "candidate_digest": proposal["candidate_digest"],
                "statuses": statuses,
                "failed_claims": failed,
                "evidence": evidence_payload,
                "validated_claims": [claim.model_dump(mode="json") for claim in claims],
            }
            validation_data["evidence_digest"] = _validation_digest(evidence_payload)
            kane_runs, stale_runs = _kane_run_bindings(runner, runs_before, claims, evidence)
            kane_packs, unbacked = _kane_pack_bindings(runner, claims, evidence, kane_runs, record.approval)
            validation_data["build_digest"] = proposal["build_digest"]
            validation_data["kane_runs"] = kane_runs
            validation_data["kane_packs"] = kane_packs
            unbound = [
                *stale_runs,
                *_observation_run_failures(claims, evidence, kane_runs),
                *_observation_build_failures(claims, evidence, proposal["build_digest"]),
                *unbacked,
                *(f"{claim_id}:kane_run_reused" for claim_id, _ in _reused_kane_runs(store, workflow_id, None, kane_runs)),
            ]
            if unbound:
                validation_data["successful"] = False
                validation_data["failed_claims"] = [*validation_data["failed_claims"], *unbound]
            seals = _seal_validation(store, record, validation_data)
        finally:
            if session is not None:
                session.close()
            if scratch.exists():
                shutil.rmtree(scratch, ignore_errors=True)
        try:
            with SourceDir(source_path):
                pass
        except SourcePathError as exc:
            return _unresolved(store, record, f"{exc} after validation ran; promotion refused")
        if current_build_digest(source_path, proposal["source_digest"]) != proposal.get("build_digest"):
            return _unresolved(store, record, "build identity changed during validation; promotion refused")
        if not validation_data["successful"]:
            return store.transition(
                workflow_id,
                "validating",
                "rejected",
                validation=validation_data,
                event={"reason": "fresh validation failed", "failed_claims": validation_data["failed_claims"]},
            )
        record = store.transition(
            workflow_id,
            "validating",
            "validated",
            validation=validation_data,
            event={"runner": runner.name, **seals},
        )
    except Exception as exc:
        current = store.get(workflow_id)
        if current.state == "validating":
            return _unresolved(store, current, f"validation incomplete: {type(exc).__name__}: {exc}")
        raise
    if apply:
        return promote_workflow(store, workflow_id, policy=policy)
    return record


def promote_workflow(
    store: SQLiteWorkflowStore,
    workflow_id: str,
    *,
    policy: MaintainDocsPolicy | None = None,
) -> WorkflowRecord:
    record = store.get(workflow_id)
    if record.proposal.get("kind") == SPEC_APP_KIND:
        return promote_app_workflow(store, workflow_id)
    policy = policy or MaintainDocsPolicy()
    if record.state != "validated" or not record.approval or not record.validation:
        raise WorkflowError("--apply is disabled unless an approved candidate has successful validation")
    _refuse_docs_approval(store, record)
    _refuse_unrecorded_approval(store, record)
    if not record.validation.get("successful") or record.validation.get("runner") != "kane":
        raise WorkflowError("--apply is disabled after failed, incomplete, or simulated validation")
    proposal = record.proposal
    if record.validation.get("build_digest") != proposal.get("build_digest"):
        raise WorkflowError("validation evidence is not bound to the approved app/build version; --apply refused")
    browser = [c.get("id") for c in record.validation.get("validated_claims") or [] if c.get("kind") == "browser"]
    _refuse_unbound_browser_evidence(record.validation, browser, proposal.get("build_digest"))
    # The record's claims must be the approved document's, each proven: a record that drops
    # a browser claim would otherwise skip its evidence checks above.
    try:
        approved = [Claim.model_validate(x) for x in json.loads(store.get_artifact(proposal["artifact_digests"]["baseline_claims"]))]
        validated = [Claim.model_validate(x) for x in record.validation.get("validated_claims") or []]
    except Exception as exc:
        raise WorkflowError(f"validation claims are unreadable; --apply refused: {exc}") from exc
    complete, detail = _compile_complete(approved, validated, proposal["target_claim_id"])
    if not complete:
        raise WorkflowError(f"validation record's claims are not the approved document's ({detail}); --apply refused")
    _refuse_unproven_claims(record.validation, validated)
    _refuse_unsealed_validation(store, record)
    # The packs are read against these claims' wording. The target's wording was compiled at
    # validation and is stored only in the record, so the record's claims must be the
    # approved candidate's own: each span its lines, each expected text in its source.
    candidate = store.get_artifact(proposal["artifact_digests"]["candidate"])
    if hashlib.sha256(candidate).hexdigest() != proposal.get("candidate_digest"):
        raise WorkflowError("candidate digest mismatch; --apply refused")
    try:
        ungrounded = _validate_compiled_claims(ingest_text(candidate.decode("utf-8"), source=proposal["source_path"]), validated)
    except UnicodeError as exc:
        raise WorkflowError("candidate is not UTF-8; --apply refused") from exc
    if ungrounded:
        raise WorkflowError(f"validation record's claims are not the approved candidate's ({ungrounded[0]}); --apply refused")
    _refuse_unbacked_browser_evidence(record.validation, [c for c in validated if c.kind == "browser"], record.approval)
    _refuse_reused_kane_runs(store, record)
    if policy.digest != proposal.get("policy_digest"):
        raise WorkflowError("policy changed after validation; --apply refused")
    if refusal := _authority_refusal(store, proposal, policy):
        raise WorkflowError(f"Maintain Docs policy no longer authorizes this candidate ({refusal}); --apply refused")
    path = Path(proposal["source_path"])
    try:
        with SourceDir(path) as source:
            return _write_candidate(store, workflow_id, proposal, source)
    except SourcePathError as exc:
        raise WorkflowError(f"{exc}; --apply refused") from exc


def _write_candidate(store: SQLiteWorkflowStore, workflow_id: str, proposal: dict[str, Any], source: SourceDir) -> WorkflowRecord:
    """Promote through the approved source's directory, held open with no symlink on its path."""
    path = source.path
    original = source.read()
    try:
        current_text = original.decode("utf-8")
    except UnicodeError as exc:
        raise WorkflowError("source is not UTF-8; --apply refused") from exc
    if (
        hashlib.sha256(original).hexdigest() != proposal.get("source_digest")
        or sha256_of(current_text) != proposal.get("base_digest")
    ):
        raise WorkflowError("source changed after approval; --apply refused")
    if current_build_digest(path, proposal["source_digest"]) != proposal.get("build_digest"):
        raise WorkflowError("build identity changed after approval; --apply refused")
    candidate = store.get_artifact(proposal["artifact_digests"]["candidate"])
    if hashlib.sha256(candidate).hexdigest() != proposal.get("candidate_digest"):
        raise WorkflowError("candidate digest mismatch; --apply refused")
    mode = source.mode()

    def unchanged() -> None:
        # Recheck immediately before the atomic replacement.
        if hashlib.sha256(source.read()).hexdigest() != proposal["source_digest"]:
            raise WorkflowError("source changed during promotion; --apply refused")

    source.replace(candidate, mode, prefix="attest-", before=unchanged)
    try:
        return store.transition(
            workflow_id,
            "validated",
            "promoted",
            event={"source_digest": proposal["source_digest"], "candidate_digest": proposal["candidate_digest"]},
        )
    except Exception:
        # Never leave an unrecorded promotion if the durable state write fails.
        try:
            promoted = hashlib.sha256(source.read()).hexdigest() == proposal["candidate_digest"]
        except (SourcePathError, OSError):
            promoted = False
        if promoted:
            source.replace(original, mode, prefix="attest-rollback-")
        raise

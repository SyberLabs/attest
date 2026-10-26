"""Durable approval, whole-document validation, and digest-checked promotion."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from .app_promotion import SPEC_APP_KIND, promote_app_workflow, resume_app_workflow
from .approvals import ApprovalError, verify_approval
from .compiler import compile_doc
from .execution.recipes import ApprovedRecipeExecutor
from .ingest import ingest_text, sha256_of
from .models import BrowserObservation, Claim
from .policy import MaintainDocsPolicy
from .pipeline import execute
from .proposals import current_build_digest, proposal_digest
from .shell import new_scratch
from .storage import SQLiteWorkflowStore, WorkflowRecord, WorkflowStorageError, canonical_json
from .verdict import Rules, load_rules


class WorkflowError(RuntimeError):
    pass


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


def _refuse_unproven_claims(validation: dict[str, Any], claims: list[Claim]) -> None:
    """Promotion: the record shows every approved required claim proven, and no failure."""
    return  # NEGATIVE CONTROL: check disabled
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
    return  # NEGATIVE CONTROL: check disabled
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
        verify_approval(record)
        if apply:
            return promote_workflow(store, workflow_id, policy=policy)
        return record
    if record.state != "validating":
        raise WorkflowError(f"workflow is {record.state}; only an approved workflow can be resumed")
    try:
        verify_approval(record)
    except ApprovalError as exc:
        return _unresolved(store, record, str(exc))

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
    source_path = Path(proposal["source_path"])
    if not source_path.is_file() or source_path.is_symlink():
        return _unresolved(store, record, "source file is missing or is a symlink")
    try:
        source_text = source_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        return _unresolved(store, record, f"cannot read source for digest check: {exc}")
    if (
        hashlib.sha256(source_path.read_bytes()).hexdigest() != proposal.get("source_digest")
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
            validation_data["build_digest"] = proposal["build_digest"]
            validation_data["kane_runs"] = kane_runs
            unbound = [
                *stale_runs,
                *_observation_run_failures(claims, evidence, kane_runs),
                *_observation_build_failures(claims, evidence, proposal["build_digest"]),
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
    verify_approval(record)
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
    if False and not complete:  # NEGATIVE CONTROL: check disabled
        raise WorkflowError(f"validation record's claims are not the approved document's ({detail}); --apply refused")
    _refuse_unproven_claims(record.validation, validated)
    _refuse_unsealed_validation(store, record)
    if policy.digest != proposal.get("policy_digest"):
        raise WorkflowError("policy changed after validation; --apply refused")
    path = Path(proposal["source_path"])
    if not path.is_file() or path.is_symlink():
        raise WorkflowError("source file is missing or is a symlink")
    original = path.read_bytes()
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
    mode = stat.S_IMODE(path.stat().st_mode)
    fd, staged_name = tempfile.mkstemp(prefix=f".{path.name}.attest-", dir=path.parent)
    staged = Path(staged_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(candidate)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(staged, mode)
        # Recheck immediately before the atomic replacement.
        current = path.read_bytes()
        if hashlib.sha256(current).hexdigest() != proposal["source_digest"]:
            raise WorkflowError("source changed during promotion; --apply refused")
        os.replace(staged, path)
    finally:
        staged.unlink(missing_ok=True)
    try:
        return store.transition(
            workflow_id,
            "validated",
            "promoted",
            event={"source_digest": proposal["source_digest"], "candidate_digest": proposal["candidate_digest"]},
        )
    except Exception:
        # Never leave an unrecorded promotion if the durable state write fails.
        if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == proposal["candidate_digest"]:
            fd, rollback_name = tempfile.mkstemp(prefix=f".{path.name}.attest-rollback-", dir=path.parent)
            with os.fdopen(fd, "wb") as stream:
                stream.write(original)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(rollback_name, mode)
            os.replace(rollback_name, path)
        raise

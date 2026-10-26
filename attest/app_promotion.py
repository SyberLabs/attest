"""Spec mode promotion: a candidate app reaches the original only through an approved workflow.

Candidate edits never need approval: the worker's diffs land in the copy under
``<home>/candidates`` and Kane re-checks the copy. Writing the original ``--app``
directory is a separate, host-created workflow with the same gates as Maintain
Docs: a human approval bound to the exact source, base, candidate, plan, build
and policy digests, fresh whole-spec Kane validation of the approved candidate,
and a digest-checked write of only the files the candidate changed.

Kane's browser observations are bound as on the Maintain Docs path: each names the
fresh Kane run validation binds it to, and the build the runner measured around that
run. The runner measures the spec's worktree (the doc it was prepared with), so the
proposal records that build too, as ``spec_build_digest``, and approval binds it.

The approval itself must be the one ``attest approve`` recorded for this workflow
(``verify_spec_approval``): a digest-correct approval that no approve event backs,
that names another workflow, a policy or blank approver, a proposal its authority
denied, or a time outside the approval window is refused with a stable code. So is one on
a workflow whose event log doesn't replay to its state: a rejection, ``unresolved`` or
promotion the store recorded as final can't be reopened by rewriting the row's state.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
import uuid
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

from .approvals import ApprovalError, is_policy_actor, verify_approval
from .ingest import sha256_of
from .models import Claim
from .proposals import current_build_digest, proposal_digest
from .shell import new_scratch
from .storage import _ALLOWED_TRANSITIONS, SQLiteWorkflowStore, WorkflowRecord, canonical_json
from .verdict import Rules, load_rules

SPEC_APP_KIND = "spec_app"
SPEC_PROMOTION_POLICY = {
    "mode": "spec",
    "rule": "spec mode: the spec is the contract",
    "candidate": "worker edits apply to the candidate copy only, without approval",
    "promotion": "human approval, fresh Kane validation of the approved candidate, digest-checked write",
}
SPEC_POLICY_DIGEST = hashlib.sha256(canonical_json(SPEC_PROMOTION_POLICY).encode("utf-8")).hexdigest()


class AppPromotionError(RuntimeError):
    pass


class SpecApprovalError(ApprovalError):
    """A spec promotion's approval was refused; ``code`` is one of SPEC_APPROVAL_CODES."""

    def __init__(self, code: str, detail: str):
        super().__init__(f"spec approval refused ({code}): {detail}")
        self.code = code


SPEC_APPROVAL_CODES = (
    "approval_missing", "approval_unbound", "approval_not_this_workflow", "approval_not_human",
    "authority_not_allowed", "approval_unrecorded", "approval_stale", "approval_history_broken",
)


# -- tree identity -------------------------------------------------------------------


def tree_manifest(root: Path) -> dict[str, str]:
    """Relative POSIX path -> sha256 of every file under root; .git is skipped, symlinks bind their target."""
    root = Path(root)
    out: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        here = Path(dirpath)
        keep = []
        for name in sorted(dirnames):
            if name == ".git":
                continue
            if (here / name).is_symlink():
                filenames.append(name)  # a symlinked directory is bound by its link, never walked
                continue
            keep.append(name)
        dirnames[:] = keep
        for name in sorted(set(filenames)):
            item = here / name
            rel = item.relative_to(root).as_posix()
            if rel == ".attest.patch":
                continue
            if item.is_symlink():
                out[rel] = hashlib.sha256(b"symlink:" + os.fsencode(os.readlink(item))).hexdigest()
            elif item.is_file():
                out[rel] = hashlib.sha256(item.read_bytes()).hexdigest()
    return dict(sorted(out.items()))


def tree_digest(manifest: dict[str, str]) -> str:
    return hashlib.sha256(canonical_json(manifest).encode("utf-8")).hexdigest()


def changed_paths(base: dict[str, str], candidate: dict[str, str]) -> list[str]:
    return sorted(p for p in set(base) | set(candidate) if base.get(p) != candidate.get(p))


def _safe_rel(rel: str) -> bool:
    if not rel or rel.startswith(("/", "\\", "~")) or "\\" in rel or (len(rel) > 1 and rel[1] == ":"):
        return False
    parts = PurePosixPath(rel).parts
    return bool(parts) and ".." not in parts and ".git" not in parts


def _no_symlink_on_path(root: Path, rel: str) -> bool:
    """True when neither the target nor any directory between root and it is a symlink."""
    current = root
    for part in PurePosixPath(rel).parts:
        current = current / part
        if current.is_symlink():
            return False
    return True


def spec_build_digest(spec_source: str) -> str | None:
    """The build KaneBrowserRunner stamps on observations of this spec; None for a URL or unreadable spec."""
    if spec_source.startswith(("http://", "https://")):
        return None
    path = Path(spec_source).resolve()
    try:
        return current_build_digest(path, hashlib.sha256(path.read_bytes()).hexdigest())
    except OSError:
        return None


def _app_dir(proposal: dict[str, Any]) -> Path:
    path = Path(proposal["source_path"])
    if path.is_symlink() or not path.is_dir():
        raise AppPromotionError("the original app directory is missing or is a symlink")
    return path


# -- proposal ------------------------------------------------------------------------


def create_app_promotion_proposal(
    store: SQLiteWorkflowStore,
    *,
    candidate,
    doc,
    claims: list[Claim],
    implemented: list[str],
) -> WorkflowRecord:
    """Record the candidate as a proposal that waits for a human; the original app is not touched."""
    original = Path(candidate.original)
    if original.is_symlink() or not original.is_dir():
        raise ValueError("spec mode promotion requires an existing, non-symlink app directory")
    if not candidate.source_manifest or not candidate.base_manifest:
        raise ValueError("candidate has no recorded baseline; refusing promotion proposal")
    source_digest = tree_digest(candidate.source_manifest)
    if tree_digest(tree_manifest(original)) != source_digest:
        raise ValueError("the original app changed after the candidate was copied; refusing promotion proposal")
    cand_manifest = tree_manifest(candidate.root)
    changed = changed_paths(candidate.base_manifest, cand_manifest)
    if not changed:
        raise ValueError("the candidate does not differ from the original app; nothing to promote")
    tree_artifacts = {}
    for rel in cand_manifest:
        item = candidate.root / rel
        if item.is_symlink():
            raise ValueError(f"candidate contains a symlink ({rel}); refusing promotion proposal")
        tree_artifacts[rel] = store.put_artifact(item.read_bytes())
    if tree_artifacts != cand_manifest:
        raise ValueError("candidate changed while it was being recorded; refusing promotion proposal")
    diff = candidate.git("diff", "--binary", candidate.baseline).stdout if candidate.baseline else ""
    plan = (
        f"Spec mode: promote the candidate app implementing {', '.join(implemented) or 'no claim'} "
        f"from {doc.source} into {original}; write only the {len(changed)} changed file(s)."
    )
    unsafe = [rel for rel in changed if not _safe_rel(rel) or not _no_symlink_on_path(original, rel)]
    authority = {
        "allowed": not unsafe,
        "decision": "implementation_required",
        "rule": SPEC_PROMOTION_POLICY["rule"],
        "reason": f"unsafe promotion paths: {unsafe}" if unsafe else "candidate changes stay inside the app root",
    }
    proposal: dict[str, Any] = {
        "kind": SPEC_APP_KIND,
        "source_path": str(original.resolve()),
        "spec_source": doc.source,
        "spec_digest": doc.sha256,
        "implemented_claims": list(implemented),
        "changed_paths": changed,
        "source_digest": source_digest,
        "base_digest": tree_digest(candidate.base_manifest),
        "candidate_digest": tree_digest(cand_manifest),
        "build_digest": current_build_digest(original, source_digest),
        "spec_build_digest": spec_build_digest(doc.source),
        "policy_digest": SPEC_POLICY_DIGEST,
        "plan": plan,
        "plan_digest": hashlib.sha256(plan.encode("utf-8")).hexdigest(),
        "artifact_digests": {
            "spec_claims": store.put_artifact(canonical_json([c.model_dump(mode="json") for c in claims])),
            "base_tree": store.put_artifact(canonical_json(candidate.base_manifest)),
            "source_tree": store.put_artifact(canonical_json(candidate.source_manifest)),
            "candidate_tree": store.put_artifact(canonical_json(tree_artifacts)),
            "diff": store.put_artifact(diff),
        },
        "candidate_path": candidate.posix,
        "created_by": "host",
    }
    proposal["proposal_digest"] = proposal_digest(proposal)
    workflow_id = uuid.uuid4().hex
    store.create(workflow_id, proposal)
    proposal["authority"] = authority
    proposal["proposal_digest"] = proposal_digest(proposal)
    if authority["allowed"]:
        return store.transition(
            workflow_id, "proposed", "awaiting_approval", proposal=proposal,
            event={"authority": "allowed", "policy_digest": SPEC_POLICY_DIGEST},
        )
    return store.transition(
        workflow_id, "proposed", "rejected", proposal=proposal,
        event={"authority": "denied", "reason": authority["reason"]},
    )


# -- approval ------------------------------------------------------------------------


def verify_spec_approval(store: SQLiteWorkflowStore, record: WorkflowRecord) -> None:
    """The approval is a human's, recorded by ``attest approve`` for this workflow, after its proposal."""
    approval = record.approval
    if not approval:
        raise SpecApprovalError("approval_missing", "no human approval is recorded for this workflow")
    try:
        verify_approval(record)
    except ApprovalError as exc:
        raise SpecApprovalError("approval_unbound", str(exc)) from exc
    if approval.get("workflow_id") != record.workflow_id:
        raise SpecApprovalError("approval_not_this_workflow", "the approval names another workflow, or none")
    actor = approval.get("actor")
    if not isinstance(actor, str) or not actor.strip() or actor != actor.strip() or is_policy_actor(actor):
        raise SpecApprovalError("approval_not_human", f"approver {actor!r} is not a human identity")
    if not (record.proposal.get("authority") or {}).get("allowed"):
        raise SpecApprovalError("authority_not_allowed", "authority policy did not authorize this proposal")
    # approve_workflow writes exactly one awaiting_approval -> validating event naming the approver and digest
    events = store.events(record.workflow_id)
    steps = [(e["from_state"], e["to_state"]) for e in events]
    offered = [i for i, step in enumerate(steps) if step == ("proposed", "awaiting_approval")]
    approved = [i for i, step in enumerate(steps) if step == ("awaiting_approval", "validating")]
    if (
        len(offered) != 1 or len(approved) != 1 or offered[0] > approved[0]
        or events[offered[0]]["payload"] != {"authority": "allowed", "policy_digest": SPEC_POLICY_DIGEST}
    ):
        raise SpecApprovalError("approval_unrecorded", "the workflow has no single recorded proposal and approval")
    if events[approved[0]]["payload"] != {"actor": actor, "approval_digest": approval.get("approval_digest")}:
        raise SpecApprovalError("approval_unrecorded", "the approval is not the one `attest approve` recorded")
    try:
        at = datetime.fromisoformat(approval["approved_at"])
        window = (datetime.fromisoformat(events[offered[0]]["created_at"]),
                  datetime.fromisoformat(events[approved[0]]["created_at"]))
        inside = at.tzinfo is not None and window[0] <= at <= window[1]
    except (KeyError, TypeError, ValueError):
        inside = False
    if not inside:
        raise SpecApprovalError("approval_stale", "the approval was not given between this proposal and its approval event")


def verify_spec_history(store: SQLiteWorkflowStore, record: WorkflowRecord) -> None:
    """The approval's workflow is still open: its event log replays to the state the row is in."""
    # store.create writes one creation event and store.transition one event per allowed step:
    # replayed, the log ends in the row's state. A final state reopened in the row doesn't.
    steps = [(e["from_state"], e["to_state"]) for e in store.events(record.workflow_id)]
    if steps[:1] != [(None, "proposed")] or any(
        before != after[0] or after[1] not in _ALLOWED_TRANSITIONS.get(after[0], ())
        for (_, before), after in zip(steps, steps[1:])
    ) or steps[-1][1] != record.state:
        raise SpecApprovalError("approval_history_broken", "the workflow's recorded events do not lead to its current state")


# -- validation ----------------------------------------------------------------------


def _unresolved(store: SQLiteWorkflowStore, record: WorkflowRecord, reason: str) -> WorkflowRecord:
    return store.transition(
        record.workflow_id, "validating", "unresolved",
        validation={"successful": False, "reason": reason}, event={"reason": reason},
    )


def _candidate_files(store: SQLiteWorkflowStore, proposal: dict[str, Any]) -> dict[str, bytes]:
    """The approved candidate tree from immutable artifacts, never from the mutable candidate directory."""
    manifest = json.loads(store.get_artifact(proposal["artifact_digests"]["candidate_tree"]))
    if tree_digest(manifest) != proposal.get("candidate_digest"):
        raise AppPromotionError("candidate tree digest mismatch")
    files = {rel: store.get_artifact(digest) for rel, digest in manifest.items()}
    if any(not _safe_rel(rel) for rel in files):
        raise AppPromotionError("candidate tree names a path outside the app root")
    return files


def _check_identity(proposal: dict[str, Any]) -> None:
    """The original app, its build and the spec still match what the human approved."""
    if proposal.get("kind") != SPEC_APP_KIND:
        raise AppPromotionError("not a spec mode promotion workflow")
    if proposal_digest(proposal) != proposal.get("proposal_digest"):
        raise AppPromotionError("proposal digest no longer matches its approved contents")
    if proposal.get("policy_digest") != SPEC_POLICY_DIGEST:
        raise AppPromotionError("spec mode promotion policy changed after approval")
    app_dir = _app_dir(proposal)
    if tree_digest(tree_manifest(app_dir)) != proposal.get("source_digest"):
        raise AppPromotionError("the original app changed after approval; promotion refused")
    if current_build_digest(app_dir, proposal["source_digest"]) != proposal.get("build_digest"):
        raise AppPromotionError("build identity changed after approval; promotion refused")
    spec = proposal.get("spec_source") or ""
    if not spec.startswith(("http://", "https://")):
        path = Path(spec)
        try:
            current = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise AppPromotionError(f"cannot read the spec for its digest check: {exc}") from exc
        if sha256_of(current) != proposal.get("spec_digest"):
            raise AppPromotionError("the spec changed after approval; promotion refused")
    # A proposal recorded before observation builds were bound has none: validate it again.
    if "spec_build_digest" not in proposal or spec_build_digest(spec) != proposal["spec_build_digest"]:
        raise AppPromotionError("spec build identity changed after approval; promotion refused")


def resume_app_workflow(
    store: SQLiteWorkflowStore,
    workflow_id: str,
    *,
    runner,
    apply: bool = False,
    simulation: bool = False,
    rules: Rules | None = None,
    shell_env: dict[str, str] | None = None,
) -> WorkflowRecord:
    """Validate the approved candidate with fresh Kane evidence against the whole spec, optionally promote."""
    from .pipeline import execute
    from .workflow import (
        WorkflowError, _kane_pack_bindings, _kane_run_bindings, _observation_build_failures, _observation_run_failures,
        _seal_validation, _validation_digest,
    )

    record = store.get(workflow_id)
    if record.proposal.get("kind") != SPEC_APP_KIND:
        raise WorkflowError("not a spec mode promotion workflow")
    if simulation or getattr(runner, "name", None) != "kane":
        raise WorkflowError("promotion validation requires fresh Kane evidence; simulation/fake evidence is not accepted")
    if record.state == "validated":
        try:
            verify_spec_approval(store, record)
            verify_spec_history(store, record)
        except ApprovalError as exc:
            raise WorkflowError(f"{exc}; --apply refused") from exc
        if apply:
            return promote_app_workflow(store, workflow_id)
        return record
    if record.state != "validating":
        raise WorkflowError(f"workflow is {record.state}; only an approved workflow can be resumed")
    try:
        verify_spec_approval(store, record)
        verify_spec_history(store, record)
        _check_identity(record.proposal)
        files = _candidate_files(store, record.proposal)
        claims = [Claim.model_validate(x) for x in json.loads(store.get_artifact(record.proposal["artifact_digests"]["spec_claims"]))]
    except Exception as exc:  # approval, identity or artifact failure: fail closed
        return _unresolved(store, record, str(exc))

    scratch = new_scratch("attest-validate-app-")
    session = None
    runs_before = len(getattr(runner, "runs", None) or [])
    try:
        app_root = scratch / "app"
        for rel, data in files.items():
            target = app_root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        run_dir = scratch / "run"
        run_dir.mkdir()
        prepare = getattr(runner, "prepare", None)
        if prepare is not None:
            prepare(claims, record.proposal["spec_source"])
        env = {**(shell_env or {}), "ATTEST_APP_DIR": app_root.resolve().as_posix()}
        evidence, verdicts, session = execute(claims, runner, rules or load_rules(), run_dir, shell_env=env)
        statuses = {v.claim_id: v.status for v in verdicts}
        required = [c for c in claims if c.kind in {"shell", "browser", "assert"}]
        failed = [f"{c.id}:{statuses.get(c.id, 'missing')}" for c in required if statuses.get(c.id) != "proven"]
        evidence_payload = {key: value.model_dump(mode="json") for key, value in evidence.items()}
        validation: dict[str, Any] = {
            "successful": bool(required) and not failed,
            "runner": runner.name,
            "candidate_digest": record.proposal["candidate_digest"],
            "statuses": statuses,
            "failed_claims": failed,
            "evidence": evidence_payload,
        }
        validation["evidence_digest"] = _validation_digest(evidence_payload)
        # The same bindings as Maintain Docs validation: a fresh Kane run per browser claim,
        # named by its observation, on the spec build approval recorded.
        kane_runs, stale_runs = _kane_run_bindings(runner, runs_before, claims, evidence)
        kane_packs, unbacked = _kane_pack_bindings(runner, claims, evidence, kane_runs)
        validation["spec_build_digest"] = record.proposal["spec_build_digest"]
        validation["kane_runs"] = kane_runs
        validation["kane_packs"] = kane_packs
        unbound = [
            *stale_runs,
            *_observation_run_failures(claims, evidence, kane_runs),
            *_observation_build_failures(claims, evidence, record.proposal["spec_build_digest"]),
            *unbacked,
        ]
        if unbound:
            validation["successful"] = False
            validation["failed_claims"] = [*failed, *unbound]
        seals = _seal_validation(store, record, validation)
    except Exception as exc:
        current = store.get(workflow_id)
        if current.state == "validating":
            return _unresolved(store, current, f"validation incomplete: {type(exc).__name__}: {exc}")
        raise
    finally:
        if session is not None:
            session.close()
        shutil.rmtree(scratch, ignore_errors=True)
    try:
        _check_identity(record.proposal)
    except AppPromotionError as exc:  # the app, its build or the spec moved while Kane ran
        return _unresolved(store, record, f"changed during validation: {exc}")
    if not validation["successful"]:
        return store.transition(
            workflow_id, "validating", "rejected", validation=validation,
            event={"reason": "fresh validation failed", "failed_claims": validation["failed_claims"]},
        )
    record = store.transition(
        workflow_id, "validating", "validated", validation=validation,
        event={"runner": runner.name, **seals},
    )
    if apply:
        return promote_app_workflow(store, workflow_id)
    return record


# -- promotion -----------------------------------------------------------------------


def promote_app_workflow(store: SQLiteWorkflowStore, workflow_id: str) -> WorkflowRecord:
    """Write the approved, validated candidate's changed files into the original app, or nothing."""
    from .workflow import (
        WorkflowError, _refuse_unbacked_browser_evidence, _refuse_unbound_browser_evidence, _refuse_unproven_claims,
        _refuse_unsealed_validation,
    )

    record = store.get(workflow_id)
    proposal = record.proposal
    if proposal.get("kind") != SPEC_APP_KIND:
        raise WorkflowError("not a spec mode promotion workflow")
    if record.state != "validated" or not record.approval or not record.validation:
        raise WorkflowError("--apply is disabled unless an approved candidate has successful validation")
    try:
        verify_spec_approval(store, record)
    except ApprovalError as exc:
        raise WorkflowError(f"{exc}; --apply refused") from exc
    validation = record.validation
    if (
        not validation.get("successful")
        or validation.get("runner") != "kane"
        or validation.get("candidate_digest") != proposal.get("candidate_digest")
    ):
        raise WorkflowError("--apply is disabled after failed, incomplete, or simulated validation")
    try:
        _check_identity(proposal)
        files = _candidate_files(store, proposal)
        base = json.loads(store.get_artifact(proposal["artifact_digests"]["base_tree"]))
        source = json.loads(store.get_artifact(proposal["artifact_digests"]["source_tree"]))
        claims = [Claim.model_validate(x) for x in json.loads(store.get_artifact(proposal["artifact_digests"]["spec_claims"]))]
    except AppPromotionError as exc:
        raise WorkflowError(f"{exc}; --apply refused") from exc
    # Browser claims come from the approved spec claims, never from the validation record.
    if "spec_build_digest" not in validation or validation["spec_build_digest"] != proposal["spec_build_digest"]:
        raise WorkflowError("validation evidence is not bound to the approved app/build version; --apply refused")
    browser = [c for c in claims if c.kind == "browser"]
    _refuse_unbound_browser_evidence(validation, [c.id for c in browser], proposal["spec_build_digest"])
    _refuse_unproven_claims(validation, claims)
    _refuse_unsealed_validation(store, record)
    try:
        verify_spec_history(store, record)  # after the seal check, which names a relabelled rejection
    except ApprovalError as exc:
        raise WorkflowError(f"{exc}; --apply refused") from exc
    _refuse_unbacked_browser_evidence(validation, browser)
    if tree_digest(base) != proposal.get("base_digest") or tree_digest(source) != proposal.get("source_digest"):
        raise WorkflowError("baseline tree digest mismatch; --apply refused")
    candidate_manifest = {rel: hashlib.sha256(data).hexdigest() for rel, data in files.items()}
    changed = changed_paths(base, candidate_manifest)
    if changed != proposal.get("changed_paths"):
        raise WorkflowError("candidate changes differ from the approved change set; --apply refused")
    app_dir = _app_dir(proposal)
    for rel in changed:
        if not _safe_rel(rel) or not _no_symlink_on_path(app_dir, rel):
            raise WorkflowError(f"unsafe promotion path {rel!r}; --apply refused")

    # stage every write next to its target, then recheck the whole app and swap
    staged: list[tuple[Path, Path | None]] = []  # (target, staged file or None for a deletion)
    originals: dict[Path, tuple[bytes, int] | None] = {}
    made_dirs: list[Path] = []
    replaced: list[Path] = []
    try:
        for rel in changed:
            target = app_dir / rel
            originals[target] = (target.read_bytes(), stat.S_IMODE(target.stat().st_mode)) if target.is_file() else None
            if rel not in files:
                staged.append((target, None))
                continue
            missing = []
            parent = target.parent
            while not parent.exists():
                missing.append(parent)
                parent = parent.parent
            for d in reversed(missing):
                d.mkdir()
                made_dirs.append(d)
            mode = originals[target][1] if originals[target] else 0o644
            fd, name = tempfile.mkstemp(prefix=f".{target.name}.attest-", dir=target.parent)
            with os.fdopen(fd, "wb") as stream:
                stream.write(files[rel])
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(name, mode)
            staged.append((target, Path(name)))
        # Recheck immediately before the replacements: nothing in the app moved under us.
        if tree_digest(_manifest_without(app_dir, [p for _, p in staged if p])) != proposal["source_digest"]:
            raise WorkflowError("the original app changed during promotion; --apply refused")
        for target, tmp in staged:
            if tmp is None:
                target.unlink(missing_ok=True)
            else:
                os.replace(tmp, target)
            replaced.append(target)
        return store.transition(
            workflow_id, "validated", "promoted",
            event={"source_digest": proposal["source_digest"], "candidate_digest": proposal["candidate_digest"],
                   "changed_paths": changed},
        )
    except BaseException:
        # Never leave a partial or unrecorded promotion.
        for target in reversed(replaced):
            before = originals.get(target)
            if before is None:
                target.unlink(missing_ok=True)
            else:
                data, mode = before
                fd, name = tempfile.mkstemp(prefix=f".{target.name}.attest-rollback-", dir=target.parent)
                with os.fdopen(fd, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.chmod(name, mode)
                os.replace(name, target)
        raise
    finally:
        for _, tmp in staged:
            if tmp is not None:
                tmp.unlink(missing_ok=True)
        for d in reversed(made_dirs):
            try:
                d.rmdir()  # only empty directories this promotion created and did not fill
            except OSError:
                pass


def _manifest_without(root: Path, staged: list[Path]) -> dict[str, str]:
    skip = {p.relative_to(root).as_posix() for p in staged}
    return {rel: digest for rel, digest in tree_manifest(root).items() if rel not in skip}


"""Host-created, content-addressed repair proposals (agents never promote)."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any

from .models import Claim
from .policy import MaintainDocsPolicy
from .storage import SQLiteWorkflowStore, WorkflowRecord, canonical_json
from .ingest import sha256_of


def _digest_text(text: str) -> str:
    return sha256_of(text)


# paths Attest writes inside a docs repo: its home (default .attest) and the compiler cache
ATTEST_OWNED_PATHS = (".attest", "cache/compiler")


def current_build_digest(source_path: Path | str, source_digest: str | None = None) -> str:
    """Bind the commit and tracked/untracked worktree inputs; unversioned inputs bind to their source."""
    path = Path(source_path).resolve()
    try:
        root_proc = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"], cwd=path.parent, capture_output=True, text=True, timeout=5
        )
        if root_proc.returncode != 0:
            raise OSError("not in a Git worktree")
        root = Path(root_proc.stdout.strip())
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, timeout=5)
        # Attest's own output (its home and its compiler cache) is not part of the build:
        # approving a proposal writes to the home, and that must not read as a change
        owned = ["--", ".", *(f":(exclude){p}" for p in ATTEST_OWNED_PATHS)]
        status = subprocess.run(
            ["git", "status", "--porcelain=v1", "--untracked-files=all", *owned],
            cwd=root, capture_output=True, text=True, timeout=5,
        )
        diff = subprocess.run(["git", "diff", "--binary", "HEAD", *owned], cwd=root, capture_output=True, timeout=10)
        untracked_proc = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard", "-z", *owned],
            cwd=root, capture_output=True, timeout=5,
        )
        if any(proc.returncode != 0 for proc in (head, status, diff, untracked_proc)):
            raise OSError("could not snapshot Git worktree")
        untracked = {}
        for raw_path in untracked_proc.stdout.split(b"\0"):
            if not raw_path:
                continue
            item = root / os.fsdecode(raw_path)
            if item.is_symlink():
                content = os.readlink(item).encode("utf-8", errors="surrogateescape")
            elif item.is_file():
                content = item.read_bytes()
            else:
                continue
            untracked[os.fsdecode(raw_path)] = hashlib.sha256(content).hexdigest()
        identity = {
            "kind": "git-worktree",
            "head": head.stdout.strip(),
            "status": status.stdout,
            "diff_digest": hashlib.sha256(diff.stdout).hexdigest(),
            "untracked": untracked,
        }
    except (OSError, subprocess.SubprocessError, subprocess.TimeoutExpired):
        identity = {"kind": "unversioned", "path": str(path), "source_digest": source_digest}
    return hashlib.sha256(canonical_json(identity).encode("utf-8")).hexdigest()


def _patch_matches_candidate(base_text: str, candidate_text: str, diff: str, doc_name: str) -> bool:
    if Path(doc_name).name != doc_name or Path(doc_name).is_absolute():
        return False
    base = "\n".join(base_text.splitlines()) + "\n"
    with tempfile.TemporaryDirectory(prefix="attest-proposal-") as directory:
        root = Path(directory)
        (root / doc_name).write_text(base, encoding="utf-8", newline="\n")
        patch = root / ".attest.patch"
        patch.write_text(diff, encoding="utf-8", newline="\n")
        # the temp dir may sit inside an unrelated repository (a home directory under git)
        # whose core.autocrlf would rewrite the patched text to CRLF; pin LF here
        git = ["git", "-c", "core.autocrlf=false", "-c", "core.eol=lf"]
        for args in (
            [*git, "apply", "--check", "--recount", "--unidiff-zero", "--whitespace=nowarn", str(patch)],
            [*git, "apply", "--recount", "--unidiff-zero", "--whitespace=nowarn", str(patch)],
        ):
            try:
                proc = subprocess.run(args, cwd=root, capture_output=True, text=True, timeout=10)
            except (OSError, subprocess.TimeoutExpired):
                return False
            if proc.returncode:
                return False
        return (root / doc_name).read_bytes() == candidate_text.encode("utf-8")


def proposal_digest(proposal: dict[str, Any]) -> str:
    unsigned = {key: value for key, value in proposal.items() if key != "proposal_digest"}
    return hashlib.sha256(canonical_json(unsigned).encode("utf-8")).hexdigest()


def create_repair_proposal(
    store: SQLiteWorkflowStore,
    *,
    source_path: Path | str,
    base_text: str,
    candidate_text: str,
    diff: str,
    target_claim: Claim,
    baseline_claims: list[Claim],
    plan: str,
    build_digest: str | None = None,
    policy: MaintainDocsPolicy | None = None,
    baseline_statuses: dict[str, str] | None = None,
) -> WorkflowRecord:
    policy = policy or MaintainDocsPolicy()
    source = str(source_path)
    if source.startswith(("http://", "https://")):
        raise ValueError("Maintain Docs promotion requires a local source file")
    requested_path = Path(source)
    if requested_path.is_symlink():
        raise ValueError("Maintain Docs promotion refuses symlink sources")
    path = requested_path.resolve()
    if not path.is_file() or path.is_symlink():
        raise ValueError("Maintain Docs promotion requires an existing, non-symlink local file")
    base_digest = _digest_text(base_text)
    source_digest = hashlib.sha256(path.read_bytes()).hexdigest()
    current_text = path.read_text(encoding="utf-8")
    if _digest_text(current_text) != base_digest:
        raise ValueError("source changed after the baseline snapshot; refusing proposal")
    if not _patch_matches_candidate(base_text, candidate_text, diff, path.name):
        raise ValueError("candidate content is not the exact result of applying the supplied patch to the base")
    candidate_bytes = candidate_text.encode("utf-8")
    candidate_digest = hashlib.sha256(candidate_bytes).hexdigest()
    actual_build_digest = build_digest or current_build_digest(path, source_digest)
    plan_digest = hashlib.sha256(plan.encode("utf-8")).hexdigest()
    artifact_digests = {
        "base": store.put_artifact(base_text),
        "candidate": store.put_artifact(candidate_text),
        "diff": store.put_artifact(diff),
        "baseline_claims": store.put_artifact(
            canonical_json([claim.model_dump(mode="json") for claim in baseline_claims])
        ),
    }
    if baseline_statuses is not None:
        # each claim's verdict when the proposal was made, by baseline key: a validation
        # with no browser steps passes when none of the proven ones regresses
        artifact_digests["baseline_statuses"] = store.put_artifact(canonical_json(baseline_statuses))
    proposal: dict[str, Any] = {
        "source_path": str(path),
        "target_claim_id": target_claim.id,
        "target_start_line": target_claim.span.start_line,
        "target_end_line": target_claim.span.end_line,
        "base_digest": base_digest,
        "candidate_digest": candidate_digest,
        "source_digest": source_digest,
        "build_digest": actual_build_digest,
        "policy_digest": policy.digest,
        "plan_digest": plan_digest,
        "artifact_digests": artifact_digests,
        "plan": plan,
        "created_by": "host",
    }
    proposal["proposal_digest"] = proposal_digest(proposal)
    workflow_id = uuid.uuid4().hex
    record = store.create(workflow_id, proposal)
    decision = policy.authorize(
        diff=diff,
        doc_name=path.name,
        base_text=base_text,
        candidate_text=candidate_text,
        target_start=target_claim.span.start_line,
        target_end=target_claim.span.end_line,
        target_claim_id=target_claim.id,
        base_digest=base_digest,
        candidate_digest=candidate_digest,
    )
    proposal["authority"] = decision.as_dict()
    proposal["proposal_digest"] = proposal_digest(proposal)
    if decision.allowed:
        return store.transition(
            workflow_id,
            "proposed",
            "awaiting_approval",
            proposal=proposal,
            event={"authority": "allowed", "policy_digest": policy.digest},
        )
    return store.transition(
        workflow_id,
        "proposed",
        "rejected",
        proposal=proposal,
        event={"authority": "denied", "violations": decision.violations},
    )

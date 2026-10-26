"""Reconcile Spec policy and diagnosis handoff artifacts.

This mode records a caller-supplied disagreement between protected text and
application observations. It does not execute the application, decide which
source is authoritative, edit a contract, or promote a proposed requirement.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

CandidateSide = Literal["doc_wrong", "app_wrong", "both", "ambiguous"]
AuthorityStatus = Literal["present", "missing", "conflicting"]
EvidenceKind = Literal["runtime", "receipt", "source", "document", "other"]
HandoffState = Literal["ready", "blocked"]

# at least one non-whitespace character: a blank pointer or requirement is not one
_NOT_BLANK = r"\S"


class EvidencePointer(BaseModel):
    """A caller-supplied pointer; Reconcile Spec never fetches or validates it."""

    model_config = ConfigDict(extra="forbid")

    kind: EvidenceKind
    pointer: str = Field(min_length=1, pattern=_NOT_BLANK)
    description: str = ""


class ConflictClaim(BaseModel):
    """One reported mismatch between a protected requirement and the app."""

    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(pattern=r"^c\d{2,}$")
    protected_requirement_id: str = Field(min_length=1, pattern=_NOT_BLANK)
    authority_reference: str | None = None
    protected_text: str = Field(min_length=1, pattern=_NOT_BLANK)
    observed_text: str | None = None
    candidate_side: CandidateSide = "ambiguous"
    evidence: list[EvidencePointer] = Field(default_factory=list)
    proposed_protected_text: str | None = None


class ReconcileRequest(BaseModel):
    """Structured, untrusted input for a Reconcile Spec handoff."""

    model_config = ConfigDict(extra="forbid")

    source_doc: str = Field(min_length=1, pattern=_NOT_BLANK)
    authority_status: AuthorityStatus
    conflicts: list[ConflictClaim] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_claim_ids(self) -> "ReconcileRequest":
        seen: set[str] = set()
        for conflict in self.conflicts:
            if conflict.claim_id in seen:
                raise ValueError(f"duplicate claim_id {conflict.claim_id!r}")
            seen.add(conflict.claim_id)
        return self


class ConflictDiagnosis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str
    protected_requirement_id: str
    authority_reference: str | None
    protected_text: str
    observed_text: str | None
    candidate_side: CandidateSide
    recommended_owner: str
    evidence: list[EvidencePointer]
    proposed_protected_text: str | None
    protected_edit_refused: bool


class ReconcileHandoff(BaseModel):
    """Machine-readable diagnosis with orthogonal workflow states."""

    model_config = ConfigDict(extra="forbid")

    policy: Literal["reconcile_spec"] = "reconcile_spec"
    source_doc: str
    execution_state: Literal["not_run", "external_observation"]
    comparison_state: Literal["submitted_conflict"] = "submitted_conflict"
    authority_state: AuthorityStatus
    evidence_state: Literal["provided", "incomplete", "missing"]
    repair_state: Literal["refused", "handoff"]
    handoff_state: HandoffState
    blocking_reasons: list[str]
    apply_requested: bool
    conflicts: list[ConflictDiagnosis]
    authority_note: str
    repair_note: str

    @model_validator(mode="after")
    def _ready_only_when_complete(self) -> "ReconcileHandoff":
        # A consumer re-reading the JSON gets the same guarantee as the CLI: a handoff is
        # ready only with authority present, a pointer on every conflict and no reasons.
        if self.handoff_state == "ready" and (
            self.blocking_reasons or self.authority_state != "present" or self.evidence_state != "provided"
        ):
            raise ValueError("a ready handoff needs present authority, provided evidence and no blocking reasons")
        if self.handoff_state == "blocked" and not self.blocking_reasons:
            raise ValueError("a blocked handoff must name its blocking reasons")
        return self


_OWNER_BY_SIDE = {
    "doc_wrong": "documentation owner",
    "app_wrong": "application owner",
    "both": "documentation and application owners",
    "ambiguous": "protected-requirement owner",
}


class ReconcileSpec:
    """Authority policy for protected-requirement disagreement.

    There is deliberately no approval path that turns an observation or a
    candidate edit into permission to change protected text. Resolution is a
    human handoff, even when the evidence and authority pointers are complete.
    """

    can_apply_protected_text = False
    can_promote_protected_text = False

    def diagnose(self, request: ReconcileRequest, *, apply_requested: bool = False) -> ReconcileHandoff:
        blocking = _blocking_reasons(request)
        authority_state = request.authority_status
        if authority_state == "present" and any(r.endswith(_CONFLICTING_AUTHORITY) for r in blocking):
            authority_state = "conflicting"
        elif authority_state == "present" and any(r.endswith(":authority_reference_missing") for r in blocking):
            authority_state = "missing"

        pointer_counts = [len(c.evidence) for c in request.conflicts]
        evidence_state: Literal["provided", "incomplete", "missing"]
        if all(pointer_counts):
            evidence_state = "provided"
        elif any(pointer_counts):
            evidence_state = "incomplete"
        else:
            evidence_state = "missing"

        diagnoses: list[ConflictDiagnosis] = []
        for conflict in request.conflicts:
            changed_protected_text = (
                conflict.proposed_protected_text is not None
                and conflict.proposed_protected_text != conflict.protected_text
            )
            diagnoses.append(
                ConflictDiagnosis(
                    claim_id=conflict.claim_id,
                    protected_requirement_id=conflict.protected_requirement_id,
                    authority_reference=conflict.authority_reference,
                    protected_text=conflict.protected_text,
                    observed_text=conflict.observed_text,
                    candidate_side=conflict.candidate_side,
                    recommended_owner=_OWNER_BY_SIDE[conflict.candidate_side],
                    evidence=conflict.evidence,
                    proposed_protected_text=conflict.proposed_protected_text,
                    protected_edit_refused=changed_protected_text or apply_requested,
                )
            )

        has_external_observation = any(
            pointer.kind in {"runtime", "receipt"}
            for conflict in request.conflicts
            for pointer in conflict.evidence
        )
        must_refuse = apply_requested or any(item.protected_edit_refused for item in diagnoses)
        repair_state: Literal["refused", "handoff"] = "refused" if must_refuse else "handoff"

        return ReconcileHandoff(
            source_doc=request.source_doc,
            execution_state="external_observation" if has_external_observation else "not_run",
            authority_state=authority_state,
            evidence_state=evidence_state,
            repair_state=repair_state,
            handoff_state="blocked" if blocking else "ready",
            blocking_reasons=blocking,
            apply_requested=apply_requested,
            conflicts=diagnoses,
            authority_note=(
                "The submitted protected requirement remains the baseline; its authority pointer is not independently verified. "
                "Application behavior is evidence, not authority."
                if authority_state == "present"
                else "Authority is missing or conflicting; automatic repair stops for human review."
            ),
            repair_note=(
                "Protected-contract edits and promotion are prohibited in Reconcile Spec; no change was applied."
                if must_refuse
                else "Diagnosis only: route this conflict to the recommended owner; no change was applied."
            ),
        )


# Reasons whose suffix makes the request's authority conflicting rather than missing.
_CONFLICTING_AUTHORITY = (
    ":authority_reference_mismatch",
    ":authority_reference_conflict",
    ":protected_text_conflict",
)

# A claim that the requirement no longer binds. Reconcile Spec has no change-decision
# input, so such a claim is never backed by an approved decision here; it blocks the
# handoff instead of being routed as an ordinary conflict.
_SUPERSESSION = re.compile(r"\bsuper[sc]ed", re.IGNORECASE)


def _blank(value: str | None) -> bool:
    return value is None or not value.strip()


def _blocking_reasons(request: ReconcileRequest) -> list[str]:
    """Why this handoff cannot be routed as complete, in a stable order.

    Every check reads only the submitted request; no pointer is dereferenced.
    """
    reasons: list[str] = []
    if request.authority_status != "present":
        reasons.append(f"request:authority_{request.authority_status}")

    texts: dict[str, set[str]] = {}
    references: dict[str, set[str]] = {}
    for conflict in request.conflicts:
        cid = conflict.claim_id
        requirement = conflict.protected_requirement_id.strip()
        reference = conflict.authority_reference
        if _blank(reference):
            reasons.append(f"{cid}:authority_reference_missing")
        else:
            _, hash_, fragment = reference.strip().partition("#")
            if hash_ and fragment.strip().casefold() != requirement.casefold():
                reasons.append(f"{cid}:authority_reference_mismatch")
            references.setdefault(requirement, set()).add(reference.strip())
        texts.setdefault(requirement, set()).add(conflict.protected_text)

        if not conflict.evidence:
            reasons.append(f"{cid}:evidence_missing")

        claimed = [conflict.observed_text, reference]
        for pointer in conflict.evidence:
            claimed += [pointer.pointer, pointer.description]
        if any(value and _SUPERSESSION.search(value) for value in claimed):
            reasons.append(f"{cid}:supersession_without_change_decision")

    for requirement, seen in texts.items():
        if len(seen) > 1:
            reasons.append(f"{requirement}:protected_text_conflict")
    for requirement, seen in references.items():
        if len(seen) > 1:
            reasons.append(f"{requirement}:authority_reference_conflict")
    return reasons


def handoff_package_problems(request: ReconcileRequest, markdown_path: Path, json_path: Path) -> list[str]:
    """Check a written handoff package against the request it came from.

    The package must carry, for every conflict, the protected text bytes and every
    evidence pointer as submitted, in both the JSON and the Markdown. Anything
    missing is a problem; the caller fails closed on any.
    """
    problems: list[str] = []
    try:
        written = ReconcileHandoff.model_validate_json(json_path.read_bytes())
        markdown = markdown_path.read_text(encoding="utf-8")
    except (OSError, ValueError) as exc:
        return [f"package:unreadable:{type(exc).__name__}"]
    by_id = {c.claim_id: c for c in written.conflicts}
    lines = set(markdown.splitlines())
    for conflict in request.conflicts:
        cid = conflict.claim_id
        got = by_id.get(cid)
        if got is None:
            problems.append(f"{cid}:conflict_missing")
            continue
        if got.protected_text != conflict.protected_text:
            problems.append(f"{cid}:protected_text_altered")
        if [(e.kind, e.pointer) for e in got.evidence] != [(e.kind, e.pointer) for e in conflict.evidence]:
            problems.append(f"{cid}:json_evidence_pointers_altered")
        if f"## Conflict {cid}" not in lines:
            problems.append(f"{cid}:markdown_conflict_missing")
        if any(not any(line.startswith(f"- `{e.kind}` {_inline_code(e.pointer)}") for line in lines) for e in conflict.evidence):
            problems.append(f"{cid}:markdown_evidence_pointers_missing")
    if len(written.conflicts) != len(request.conflicts):
        problems.append("package:conflict_count_mismatch")
    return problems


# every character str.splitlines() breaks on: a code span cannot hold one, and the next
# line would render as Markdown of its own (a heading, a list item)
_LINE_BREAKS = re.compile(r"[\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029]")


def _inline_code(value: str) -> str:
    """Render arbitrary input as a safe Markdown code span.

    Line breaks are shown as JSON escapes (``\\n``), so the span stays on one
    line; the JSON artifact keeps the value as submitted.
    """
    value = _LINE_BREAKS.sub(lambda m: json.dumps(m.group(0))[1:-1], value)
    runs = [len(m.group(0)) for m in re.finditer(r"`+", value)]
    marker = "`" * (max(runs, default=0) + 1)
    pad = " " if value.startswith("`") or value.endswith("`") else ""
    return f"{marker}{pad}{value}{pad}{marker}"


def _text_block(label: str, value: str | None) -> list[str]:
    if value is None:
        return [f"**{label}:** not supplied", ""]
    runs = [len(m.group(0)) for m in re.finditer(r"`+", value)]
    fence = "`" * max(3, max(runs, default=0) + 1)
    return [f"**{label}:**", "", f"{fence}text", value, fence, ""]


def render_handoff_markdown(handoff: ReconcileHandoff) -> str:
    lines = [
        "# Reconcile Spec handoff",
        "",
        f"- Source document: {_inline_code(handoff.source_doc)}",
        f"- Execution: `{handoff.execution_state}`",
        f"- Comparison: `{handoff.comparison_state}`",
        f"- Authority: `{handoff.authority_state}`",
        f"- Evidence: `{handoff.evidence_state}`",
        f"- Repair: `{handoff.repair_state}`",
        f"- Handoff: `{handoff.handoff_state}`",
        "",
        f"> {handoff.authority_note}",
        f"> {handoff.repair_note}",
        "",
    ]
    if handoff.blocking_reasons:
        lines += [
            "**Blocked:** this handoff is incomplete and must not be routed as a resolved diagnosis. "
            "A claim that the requirement was superseded is not authority without an approved change decision.",
            "",
            *(f"- {_inline_code(reason)}" for reason in handoff.blocking_reasons),
            "",
        ]
    for conflict in handoff.conflicts:
        lines += [
            f"## Conflict {conflict.claim_id}",
            "",
            f"- Protected requirement: {_inline_code(conflict.protected_requirement_id)}",
            f"- Candidate side: `{conflict.candidate_side}`",
            f"- Recommended owner: {conflict.recommended_owner}",
            f"- Authority reference: {_inline_code(conflict.authority_reference) if conflict.authority_reference else 'not supplied'}",
            f"- Protected edit refused: `{str(conflict.protected_edit_refused).lower()}`",
            "",
        ]
        lines += _text_block("Protected requirement text", conflict.protected_text)
        lines += _text_block("Observed application behavior (evidence, not authority)", conflict.observed_text)
        if conflict.proposed_protected_text is not None:
            lines += _text_block("Unapplied proposed protected text", conflict.proposed_protected_text)
        lines += ["**Evidence pointers:**", ""]
        if conflict.evidence:
            for pointer in conflict.evidence:
                detail = f" - {_inline_code(pointer.description)}" if pointer.description else ""
                lines.append(f"- `{pointer.kind}` {_inline_code(pointer.pointer)}{detail}")
        else:
            lines.append("- None supplied")
        lines.append("")
    lines += ["## Handoff", "", "Confirm the governing authority and resolve the application/spec disagreement with the listed owner. Re-run Maintain Docs only for descriptive documentation that does not alter a protected requirement.", ""]
    return "\n".join(lines)


def write_handoff(handoff: ReconcileHandoff, out_dir: str | Path) -> tuple[Path, Path]:
    """Write JSON and Markdown artifacts alongside other Attest outputs."""
    directory = Path(out_dir)
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / "reconcile-spec.json"
    markdown_path = directory / "reconcile-spec.md"
    json_path.write_text(handoff.model_dump_json(indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(render_handoff_markdown(handoff), encoding="utf-8")
    return markdown_path, json_path


def load_request(path: str | Path) -> ReconcileRequest:
    """Load and validate a JSON diagnosis request."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return ReconcileRequest.model_validate(raw)

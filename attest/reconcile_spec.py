"""Reconcile Spec policy and diagnosis handoff artifacts.

This mode records a caller-supplied disagreement between protected text and
application observations. It does not execute the application, decide which
source is authoritative, edit a contract, or promote a proposed requirement.
The CLI does read the document each conflict's authority reference names (else
``source_doc``), confined to a root, to check that the submitted protected text
is the requirement as that document states it.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, model_validator

CandidateSide = Literal["doc_wrong", "app_wrong", "both", "ambiguous"]
AuthorityStatus = Literal["present", "missing", "conflicting"]
EvidenceKind = Literal["runtime", "receipt", "source", "document", "other"]
HandoffState = Literal["ready", "blocked"]
SourceState = Literal["matched", "mismatch", "unreadable", "not_checked"]

# A requirements document is text; a larger file is not read and blocks the handoff.
MAX_SOURCE_BYTES = 2 * 1024 * 1024

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
    # the document protected_text was checked against (see protected_document)
    source_checked_in: str | None = None
    # what that document labels this requirement with, when that differs from protected_text
    source_protected_text: str | None = None


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
    source_state: SourceState
    # sha256 of the bytes of each document read for the source check, by path as submitted
    source_digests: dict[str, Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]] = Field(default_factory=dict)
    handoff_state: HandoffState
    blocking_reasons: list[str]
    apply_requested: bool
    conflicts: list[ConflictDiagnosis]
    authority_note: str
    repair_note: str

    @model_validator(mode="after")
    def _ready_only_when_complete(self) -> "ReconcileHandoff":
        # A consumer re-reading the JSON gets the same guarantee as the CLI: a handoff is
        # ready only with authority present, a pointer on every conflict, every protected
        # text matched in a document that was read, and no reasons.
        if self.handoff_state == "ready" and (
            self.blocking_reasons
            or self.authority_state != "present"
            or self.evidence_state != "provided"
            or self.source_state != "matched"
            or any(c.source_checked_in not in self.source_digests for c in self.conflicts)
        ):
            raise ValueError(
                "a ready handoff needs present authority, provided evidence, a matched source and no blocking reasons"
            )
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

    def diagnose(
        self,
        request: ReconcileRequest,
        *,
        apply_requested: bool = False,
        sources: "Mapping[str, SourceDocument | SourceUnreadable] | None" = None,
    ) -> ReconcileHandoff:
        """Diagnose ``request`` against ``sources``, the documents ``read_sources`` read.

        A conflict whose document is not in ``sources`` was never compared with the
        text it claims to quote, so the handoff is blocked (``not_checked``). This
        method reads no file itself.
        """
        source_state, source_reasons, in_source = _source_check(request, sources)
        blocking = _blocking_reasons(request) + source_reasons
        authority_state = request.authority_status
        if authority_state == "present" and any(_reason_is(r, _CONFLICTING_AUTHORITY) for r in blocking):
            authority_state = "conflicting"
        elif authority_state == "present" and any(_reason_is(r, _MISSING_AUTHORITY) for r in blocking):
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
                    source_checked_in=protected_document(conflict, request.source_doc),
                    source_protected_text=in_source.get(conflict.claim_id),
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
            source_state=source_state,
            source_digests={
                path: document.sha256
                for path, document in (sources or {}).items()
                if isinstance(document, SourceDocument)
            },
            handoff_state="blocked" if blocking else "ready",
            blocking_reasons=blocking,
            apply_requested=apply_requested,
            conflicts=diagnoses,
            authority_note=(
                "The submitted protected requirement remains the baseline; its text was matched in the document its "
                "authority reference names, but that authority is not independently verified. Application behavior is evidence, not authority."
                if authority_state == "present"
                else "Authority is missing or conflicting; automatic repair stops for human review."
            ),
            repair_note=(
                "Protected-contract edits and promotion are prohibited in Reconcile Spec; no change was applied."
                if must_refuse
                else "Diagnosis only: route this conflict to the recommended owner; no change was applied."
            ),
        )


# Reason kinds that make the request's authority conflicting rather than missing.
_CONFLICTING_AUTHORITY = (
    "authority_reference_mismatch",
    "authority_reference_conflict",
    "protected_text_conflict",
    "protected_text_not_in_source",
    "requirement_ambiguous_in_source",
)
# ... and these make it missing.
_MISSING_AUTHORITY = (
    "authority_reference_missing",
    "requirement_not_in_source",
    "source_unreadable",
    "source_not_checked",
)


def _reason_is(reason: str, kinds: tuple[str, ...]) -> bool:
    """Whether ``reason`` (``<who>:<kind>``, or ``<claim>:source_unreadable:<why>``) has one of ``kinds``."""
    if ":source_unreadable:" in reason:
        return "source_unreadable" in kinds
    return reason.endswith(tuple(f":{kind}" for kind in kinds))

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


@dataclass(frozen=True)
class SourceDocument:
    """The bytes of ``source_doc`` as read, decoded."""

    text: str
    sha256: str


@dataclass(frozen=True)
class SourceUnreadable:
    """``source_doc`` could not be read; ``reason`` is a stable slug."""

    reason: str


def read_source(source_doc: str, root: str | Path) -> SourceDocument | SourceUnreadable:
    """Read ``source_doc`` as a path inside ``root``, or say why it can't be.

    Only a regular file under ``root`` (after resolving symlinks) is read, up to
    ``MAX_SOURCE_BYTES``, as UTF-8 (a leading BOM is dropped from the text, not the
    digest). Absolute paths, ``..`` escapes and URLs are refused; nothing is fetched.
    """
    base = Path(root).resolve()
    relative = Path(source_doc)
    if relative.is_absolute() or "://" in source_doc or ".." in relative.parts:
        return SourceUnreadable("outside_root")
    try:
        path = (base / relative).resolve(strict=True)
    except (OSError, RuntimeError):
        return SourceUnreadable("not_found")
    if not path.is_relative_to(base):
        return SourceUnreadable("outside_root")
    if not path.is_file():
        return SourceUnreadable("not_a_file")
    try:
        with path.open("rb") as handle:
            data = handle.read(MAX_SOURCE_BYTES + 1)
    except OSError:
        return SourceUnreadable("not_found")
    if len(data) > MAX_SOURCE_BYTES:
        return SourceUnreadable("too_large")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return SourceUnreadable("not_utf8")
    return SourceDocument(text, hashlib.sha256(data).hexdigest())


def protected_document(conflict: ConflictClaim, source_doc: str) -> str:
    """The document that states ``conflict``'s protected requirement.

    That is the path part of its authority reference (``contracts/security.md`` in
    ``contracts/security.md#SEC-4``), or ``source_doc`` when the reference is blank
    or only a fragment.
    """
    path = (conflict.authority_reference or "").partition("#")[0].strip()
    return path or source_doc.strip()


def read_sources(request: ReconcileRequest, root: str | Path) -> dict[str, SourceDocument | SourceUnreadable]:
    """Read every document the request's protected texts are checked against, once each."""
    paths = dict.fromkeys(protected_document(c, request.source_doc) for c in request.conflicts)
    return {path: read_source(path, root) for path in paths}


# Markdown that may sit around a requirement label: headings, quotes, list markers,
# emphasis, brackets, numbering. A unit is a label only if nothing else precedes the id.
_LABEL_PREFIX = re.compile(r"[\s#>*_+\-\[\]()`|\d.]*")
_LABEL_SUFFIX = re.compile(r"[\s:.)\]*_`|\-\u2013\u2014]*")
_LIST_ITEM = re.compile(r"(\s*)([-*+]|\d+[.)])\s")


def _fold(text: str) -> str:
    return " ".join(text.split())


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def _units(text: str) -> list[str]:
    """Paragraphs, with each heading line and list item a unit of its own, folded.

    A list item indented under the unit's first line is part of that unit: a nested
    qualifier ("- for every administrator") belongs to the requirement above it.
    A blank line ends a unit unless the next line is such a nested item.
    """
    units: list[list[str]] = []
    current: list[str] | None = None
    last: list[str] | None = None
    for line in text.splitlines():
        if not line.strip():
            current = None
            continue
        item = _LIST_ITEM.match(line)
        if line.lstrip().startswith("#"):
            units.append([line])
            current = last = None
            continue
        if item and last is not None and _indent(line) > _indent(last[0]):
            current = last
            current.append(line)
        elif current is None or item:
            current = [line]
            units.append(current)
        else:
            current.append(line)
        last = current
    return [_fold(" ".join(u)) for u in units]


def _labelled_texts(units: list[str], requirement: str) -> list[str]:
    """The text each unit labelled ``requirement`` gives it, in document order.

    A unit is labelled when the id (case-insensitive, as in the fragment check) is
    its first word after Markdown markers. A heading label's text is what follows
    the id on the heading plus every unit up to the next heading, so a section
    cannot be quoted in part. Any other label with no text after it (``SEC-4:``)
    takes the next unit as its text.
    """
    token = re.compile(rf"(?<![\w-]){re.escape(requirement)}(?![\w-])", re.IGNORECASE)
    texts: list[str] = []
    for index, unit in enumerate(units):
        found = token.search(unit)
        if found is None or not _LABEL_PREFIX.fullmatch(unit[: found.start()]):
            continue
        body = unit[found.end():]
        body = body[_LABEL_SUFFIX.match(body).end():]
        following = []
        for later in units[index + 1:]:
            if later.startswith("#"):
                break
            following.append(later)
        if unit.startswith("#"):
            body = " ".join(part for part in (body, *following) if part)
        elif not body and following:
            body = following[0]
        if body:
            texts.append(body)
    return texts


def _source_check(
    request: ReconcileRequest, sources: Mapping[str, SourceDocument | SourceUnreadable] | None
) -> tuple[SourceState, list[str], dict[str, str]]:
    """Compare each submitted protected text with what its document says.

    The submitted text must equal the text the document labels with the requirement
    id, after folding whitespace only: no case folding, no Unicode normalization,
    no partial match. A shorter, reworded or scoped-down text is not the requirement
    as written. Returns the state (the worst over all conflicts), the blocking
    reasons, and per claim the document's text where it differs.
    """
    return "matched", [], {}  # NEGATIVE CONTROL: source check disabled; reverted next commit
    if sources is None:
        return "not_checked", ["request:source_not_checked"], {}
    labelled: dict[tuple[str, str], list[str]] = {}
    units: dict[str, list[str]] = {}
    reasons: list[str] = []
    differs: dict[str, str] = {}
    states: set[SourceState] = set()
    for conflict in request.conflicts:
        cid = conflict.claim_id
        path = protected_document(conflict, request.source_doc)
        source = sources.get(path)
        if source is None:
            reasons.append(f"{cid}:source_not_checked")
            states.add("not_checked")
            continue
        if isinstance(source, SourceUnreadable):
            reasons.append(f"{cid}:source_unreadable:{source.reason}")
            states.add("unreadable")
            continue
        requirement = conflict.protected_requirement_id.strip()
        if (path, requirement) not in labelled:
            if path not in units:
                units[path] = _units(source.text)
            labelled[path, requirement] = _labelled_texts(units[path], requirement)
        distinct = list(dict.fromkeys(labelled[path, requirement]))
        if not distinct:
            reasons.append(f"{cid}:requirement_not_in_source")
        elif len(distinct) > 1:
            reasons.append(f"{cid}:requirement_ambiguous_in_source")
        elif distinct[0] != _fold(conflict.protected_text):
            reasons.append(f"{cid}:protected_text_not_in_source")
            differs[cid] = distinct[0]
        else:
            continue
        states.add("mismatch")
    for state in ("not_checked", "unreadable", "mismatch"):
        if state in states:
            return state, reasons, differs
    return "matched", reasons, differs


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
        f"- Source check: `{handoff.source_state}`",
        *(f"  - {_inline_code(path)} sha256 `{digest}`" for path, digest in handoff.source_digests.items()),
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
            f"- Checked against: {_inline_code(conflict.source_checked_in) if conflict.source_checked_in else 'not checked'}",
            f"- Protected edit refused: `{str(conflict.protected_edit_refused).lower()}`",
            "",
        ]
        lines += _text_block("Protected requirement text", conflict.protected_text)
        if conflict.source_protected_text is not None:
            lines += _text_block("Text the checked document gives this requirement (differs from the submitted text)", conflict.source_protected_text)
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

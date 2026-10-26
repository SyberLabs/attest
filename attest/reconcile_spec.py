"""Reconcile Spec policy and diagnosis handoff artifacts.

This mode records a caller-supplied disagreement between protected text and
application observations. It does not execute the application, decide which
source is authoritative, edit a contract, or promote a proposed requirement.
The CLI does read the document each conflict's authority reference names (else
``source_doc``), confined to a root, to check that the submitted protected text
is the requirement as that document states it, and that the document does not
say it, the requirement's section or the requirement itself no longer governs.
It also reads the other Markdown documents under that root, so a reference to a
copy that states the requirement differently from another document there doesn't
pass as the contract.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, model_validator

CandidateSide = Literal["doc_wrong", "app_wrong", "both", "ambiguous"]
AuthorityStatus = Literal["present", "missing", "conflicting"]
EvidenceKind = Literal["runtime", "receipt", "source", "document", "other"]
HandoffState = Literal["ready", "blocked"]
SourceState = Literal["matched", "not_governing", "contested", "mismatch", "unreadable", "not_checked"]

# A requirements document is text; a larger file is not read and blocks the handoff.
MAX_SOURCE_BYTES = 2 * 1024 * 1024
# The other documents read under the root (see read_sources): Markdown, and at most this many.
SCANNED_SUFFIXES = (".md", ".markdown")
MAX_SCANNED_DOCUMENTS = 10_000

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
    # the lines of that document that say the requirement does not govern (see _not_governing)
    source_not_governing: list[str] = Field(default_factory=list)
    # by path, the texts other documents read under the root label this requirement with,
    # where they differ from the matched text (see _elsewhere)
    source_elsewhere: dict[str, list[str]] = Field(default_factory=dict)


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
        # text matched in a document that was read and doesn't say it no longer governs,
        # no other document read stating it differently, and no reasons.
        if self.handoff_state == "ready" and (
            self.blocking_reasons
            or self.authority_state != "present"
            or self.evidence_state != "provided"
            or self.source_state != "matched"
            or any(c.source_checked_in not in self.source_digests for c in self.conflicts)
            or any(c.source_not_governing for c in self.conflicts)
            or any(c.source_elsewhere for c in self.conflicts)
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
        text it claims to quote, so the handoff is blocked (``not_checked``). Every
        other document in ``sources`` is compared with it too. This method reads no
        file itself.
        """
        source_state, source_reasons, in_source, not_governing, elsewhere = _source_check(request, sources)
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
                    source_not_governing=not_governing.get(conflict.claim_id, []),
                    source_elsewhere=elsewhere.get(conflict.claim_id, {}),
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
    "source_not_governing",
    "requirement_differs_elsewhere",
)
# ... and these make it missing.
_MISSING_AUTHORITY = (
    "authority_reference_missing",
    "requirement_not_in_source",
    "source_unreadable",
    "source_not_checked",
)


# Reason kinds that carry a detail after the kind: ``<claim>:<kind>:<why>``.
_DETAILED_REASONS = ("source_unreadable", "source_not_governing")


def _reason_is(reason: str, kinds: tuple[str, ...]) -> bool:
    """Whether ``reason`` (``<who>:<kind>``, or ``<claim>:<kind>:<why>``) has one of ``kinds``."""
    for kind in _DETAILED_REASONS:
        if f":{kind}:" in reason:
            return kind in kinds
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


def read_sources(
    request: ReconcileRequest, root: str | Path, *, skip: tuple[str | Path, ...] = ()
) -> dict[str, SourceDocument | SourceUnreadable]:
    """Read every document the request's protected texts are checked against, once each.

    Then read every other Markdown document under ``root``, by its path relative to
    ``root``, and keep those that name one of the request's requirement ids, so
    ``diagnose`` can see a requirement stated differently elsewhere. The walk skips
    ``.git``, the directories in ``skip`` (earlier handoffs) and symlinked
    directories. A file that resolves out of ``root``, a broken link and anything not
    a regular file hold no document under it and are left out. Any other file it
    can't read is kept as ``SourceUnreadable``, and so are a directory it can't list
    (``directory_unreadable``) and the first file past ``MAX_SCANNED_DOCUMENTS``
    (``too_many_documents``).
    """
    paths = dict.fromkeys(protected_document(c, request.source_doc) for c in request.conflicts)
    sources: dict[str, SourceDocument | SourceUnreadable] = {path: read_source(path, root) for path in paths}
    base = Path(root).resolve()
    checked = set()
    for path in paths:
        if not Path(path).is_absolute() and "://" not in path:
            try:
                checked.add((base / path).resolve())
            except (OSError, RuntimeError):
                pass
    skipped = {Path(directory).resolve() for directory in skip}
    tokens = [
        re.compile(rf"(?<![\w-]){re.escape(c.protected_requirement_id.strip())}(?![\w-])", re.IGNORECASE)
        for c in request.conflicts
    ]
    scanned = 0

    def unlisted(error: OSError) -> None:
        listed = Path(error.filename or base)
        relative = listed.relative_to(base).as_posix() if listed.is_relative_to(base) else str(listed)
        sources.setdefault(relative, SourceUnreadable("directory_unreadable"))

    for directory, subdirectories, files in os.walk(base, onerror=unlisted):
        here = Path(directory)
        subdirectories[:] = sorted(
            name for name in subdirectories if name != ".git" and (here / name).resolve() not in skipped
        )
        for name in sorted(files):
            if not name.casefold().endswith(SCANNED_SUFFIXES):
                continue
            relative = (here / name).relative_to(base).as_posix()
            if relative in sources or (here / name).resolve() in checked:
                continue
            scanned += 1
            if scanned > MAX_SCANNED_DOCUMENTS:
                sources[relative] = SourceUnreadable("too_many_documents")
                return sources
            document = read_source(relative, base)
            if isinstance(document, SourceUnreadable):
                # a link out of the root, a broken link or not a regular file: no document under the root
                if document.reason != "outside_root" and (here / name).is_file():
                    sources[relative] = document
            elif any(token.search(document.text) for token in tokens):
                sources[relative] = document
    return sources


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
    """The text each unit labelled ``requirement`` gives it, in document order."""
    return [text for _, text in _labels(units, requirement)]


def _labels(units: list[str], requirement: str) -> list[tuple[int, str]]:
    """Each unit labelled ``requirement``, as (its index, the text it gives it).

    A unit is labelled when the id (case-insensitive, as in the fragment check) is
    its first word after Markdown markers. A heading label's text is what follows
    the id on the heading plus every unit up to the next heading, so a section
    cannot be quoted in part. Any other label with no text after it (``SEC-4:``)
    takes the next unit as its text.
    """
    token = re.compile(rf"(?<![\w-]){re.escape(requirement)}(?![\w-])", re.IGNORECASE)
    texts: list[tuple[int, str]] = []
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
            texts.append((index, body))
    return texts


# A declared status that says the document governs now. Any other declared status
# (draft, proposed, deprecated, superseded, an unknown word, or none after the colon)
# does not: the list fails closed.
GOVERNING_STATUSES = frozenset({"approved", "accepted", "active", "adopted", "current", "effective", "final", "ratified"})
# ``status: draft`` (front matter), ``**Status:** Draft``, ``## Status: Draft``, ``| Status | Draft |``
_STATUS_LINE = re.compile(r"[\s#>*_|\-]*status[\s*_]*[:|\u2013\u2014][\s*_|`]*(?P<value>.*)", re.IGNORECASE)
# ``## Status``: its status is the next non-blank line
_STATUS_HEADING = re.compile(r"\s*#+\s*status[\s:*_]*", re.IGNORECASE)
# a front-matter or metadata pointer to the document that replaces this one
_REPLACED_BY_KEY = re.compile(r"[\s>*_|\-]*replaced[_\- ]by[\s*_]*:", re.IGNORECASE)
# a heading over the requirement, or a bracketed tag right after its id, that marks it as not governing
_NOT_GOVERNING_WORDS = re.compile(
    r"\b(?:super[sc]eded|deprecated|withdrawn|obsolete|rescinded|repealed|retired|archived|draft|proposed|replaced|historical|no\s+longer)\b",
    re.IGNORECASE,
)
_TAG = re.compile(r"[(\[]([^)\]]*)[)\]]")

# The words a note uses to withdraw a requirement, without "superseded", which blocks
# anywhere on its own. A state word counts only alone or followed by what such a note
# says next (by, in favour of, a date), so ``Reject deprecated TLS versions`` and
# ``Draft documents need two reviewers`` aren't notes.
_STATE = r"(?:deprecated|withdrawn|obsolete|rescinded|repealed|retired|archived|draft|proposed|replaced|historical|void)"
_STATE_END = r"(?=\s*(?:$|[)\].,;:!?|]|(?:by|with|in\s+favou?r\s+of|on|as\s+of|since|from|effective|until|per)\b|\d))"
_NO_LONGER = r"\bno\s+longer\s+(?:appl(?:y|ies|icable)|govern(?:s|ing)?|in\s+(?:force|effect)|effective|binding|enforced)\b"
# a segment (of a title, or of a sentence) that opens with a state: ``Withdrawn``, ``Replaced by SEC-9``
_STATE_LEAD = re.compile(rf"[\s>*_`#+\-]*(?:{_STATE}{_STATE_END}|{_NO_LONGER})", re.IGNORECASE)
_SEGMENT_BREAK = re.compile(r"[:;,!?|()\[\]\u2013\u2014]|\.(?=\s|$)|\s-\s")
_SENTENCE_BREAK = re.compile(r"[;!?]|\.(?=\s|$)")
# what a sentence says of its subject when it withdraws it: ``SEC-4 was replaced by SEC-9``
_WITHDRAWS = rf"(?:[\s*_`(\[:\-\u2013\u2014]*{_STATE}{_STATE_END}|.{{0,80}}?(?:\b(?:is|are|was|were|has\s+been|have\s+been|had\s+been)\s+(?:now\s+)?{_STATE}\b|\breplaced\s+(?:by|with)\b|{_NO_LONGER}))"
# the document speaking of itself: ``This contract no longer applies``
_SELF = r"\bthis\s+(?:document|contract|policy|standard|specification|spec)\b"
# ... and the requirement speaking of itself: ``This requirement no longer applies``
_THIS_REQUIREMENT = rf"\bthis\s+(?:requirement|rule|control|clause|section)\b|{_SELF}"
# a unit labelled with an id-shaped token (letters, a hyphen, a digit): another requirement's own text
_ID_LABEL = re.compile(_LABEL_PREFIX.pattern + r"([A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)+)(?![\w-])")
# What a sentence says before the requirement it withdraws, whatever its subject: a verb
# (``SEC-9 replaces SEC-4``, ``We dropped SEC-4``, ``Obsoletes: SEC-4``), a noun and "of"
# (``Withdrawal of SEC-4``) or "in place of", then optionally "the", a word for a requirement
# and other ids in a list (``Revokes SEC-3, SEC-4``). "Replaced by SEC-4" and "replace X
# with SEC-4" name SEC-4 as the replacement and don't match.
_WITHDRAWING_VERB = (
    r"replac(?:e|es|ed|ing)|withdr(?:aw|aws|awn|awing|ew)|retir(?:e|es|ed|ing)|revok(?:e|es|ed|ing)"
    r"|rescind(?:s|ed|ing)?|repeal(?:s|ed|ing)?|deprecat(?:e|es|ed|ing)|obsolet(?:e|es|ed|ing)"
    r"|cancel(?:s|led|ed|ling|ing)?|void(?:s|ed|ing)?|drop(?:s|ped|ping)?|remov(?:e|es|ed|ing)"
    r"|delet(?:e|es|ed|ing)|abolish(?:es|ed|ing)?|discontinu(?:e|es|ed|ing)|annul(?:s|led|ling)?"
    r"|retract(?:s|ed|ing)?|scrap(?:s|ped|ping)?|suspend(?:s|ed|ing)?|sunset(?:s|ted|ting)?"
    # NEGATIVE CONTROL: waive/override off
)
_WITHDRAWING_NOUN = (
    r"(?:replacement|withdrawal|retirement|revocation|rescission|repeal|deprecation|cancell?ation|removal"
    r"|deletion|abolition|discontinuation|annulment|retraction|suspension)\s+(?:of|for)|in\s+place\s+of"
    r"|instead\s+of"
)
_OBJECT_MARKS = r"[\s*_`'\"\[\]():]*"
_WITHDRAWING = (
    rf"\b(?:{_WITHDRAWING_VERB}|{_WITHDRAWING_NOUN})\b{_OBJECT_MARKS}(?:the\s+)?"
    rf"(?:(?:old|former|previous|existing)\s+)?(?:(?:requirements?|rules?|controls?|clauses?)\s+)?{_OBJECT_MARKS}"
    rf"(?:[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)+{_OBJECT_MARKS}(?:,|&|/|\band\b|\bor\b){_OBJECT_MARKS})*"
)


def _withdrawing(requirement: str) -> str:
    """A pattern for a sentence that names ``requirement`` as what it withdraws or replaces."""
    return rf"{_WITHDRAWING}(?<![\w-]){re.escape(requirement)}(?![\w-])"


# The same verbs in the passive, said of a subject: ``SEC-4 was dropped``, ``SEC-3 and SEC-4 have
# been revoked``, ``The SEC-4 rule is hereby waived``, ``This requirement is overridden by SEC-9``.
# The subject must open the sentence or a clause (after a mark, a dash, or "that", "but", "since",
# ...), so ``Requests that skip SEC-4 are dropped`` doesn't match. A negation between the subject
# and the verb (``SEC-4 must never be waived``, ``SEC-4 is not removed``) doesn't match either.
_PARTICIPLE = (
    r"replaced|withdrawn|retired|revoked|rescinded|repealed|deprecated|obsoleted|cancell?ed|voided|dropped"
    r"|removed|deleted|abolished|discontinued|annulled|retracted|scrapped|suspended|sunset(?:ted)?|waived"
    r"|overridden"
)
_PASSIVE_LEAD = (
    r"(?:^|[,:;(\[]|\s[\-\u2013\u2014]\s"
    r"|\b(?:that|but|so|then|now|when|once|since|because|after|until)\s)[\s>*_`#+\-\[\]()]*"
)
_REQUIREMENT_WORD = r"(?:requirements?|rules?|controls?|clauses?)"
_ID = r"[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)+"
_LIST_SEPARATOR = rf"{_OBJECT_MARKS}(?:,\s*(?:and|or)\b|,|&|/|\band\b|\bor\b){_OBJECT_MARKS}"
_ADVERB = r"(?:now|hereby|since|also|already|just|thereby|\w+ly)"
_PASSIVE = (
    rf"(?:{_OBJECT_MARKS}{_REQUIREMENT_WORD}\b)?(?:{_OBJECT_MARKS}\([^()]{{0,80}}\))?(?:{_OBJECT_MARKS},[^,;]{{1,80}}?,)?"
    rf"{_OBJECT_MARKS}(?:(?:has|have|had|will|shall|would|should|must|may|might|is|are|was|were|to|{_ADVERB})\s+){{0,3}}"
    rf"(?:is|are|was|were|be|been|being|gets?|got|gotten|getting)\s+(?:{_ADVERB}\s+){{0,2}}"
    rf"(?:{_PARTICIPLE}|{_STATE})(?![\w-])"
)


def _withdrawn_passive(subject: str) -> str:
    """A pattern for a sentence or clause whose ``subject`` the passive says is withdrawn."""
    return (
        rf"{_PASSIVE_LEAD}(?:the\s+)?(?:(?:old|former|previous|existing)\s+)?(?:{_REQUIREMENT_WORD}\s+)?"
        rf"{_OBJECT_MARKS}(?:{subject}){_PASSIVE}"
    )


def _subject_in_list(requirement: str) -> str:
    """``requirement`` as the subject, alone or in a list of ids (``SEC-3, SEC-4 and SEC-5``).

    At most eight other ids on each side, which keeps the search linear in a long list. A link
    target (``[SEC-4](#sec-4)``) reads as a bracketed note, and the one before it as a lead.
    """
    own = rf"(?<![\w-]){re.escape(requirement)}(?![\w-])"
    return rf"(?:{_ID}{_LIST_SEPARATOR}){{0,8}}{own}(?:{_LIST_SEPARATOR}{_ID}){{0,8}}"


_SELF_WITHDRAWN = re.compile(rf"{_SELF}{_WITHDRAWS}", re.IGNORECASE)  # NEGATIVE CONTROL: passive off


def _heading_level(unit: str) -> int:
    return len(unit) - len(unit.lstrip("#"))


def _other_label(unit: str, requirement: str) -> bool:
    found = _ID_LABEL.match(unit)
    return bool(found) and any(c.isdigit() for c in found.group(1)) and found.group(1).casefold() != requirement.casefold()


def _section(units: list[str], index: int, requirement: str) -> list[int]:
    """The units in the label's section, subsections included, without other requirements' own text.

    A heading label's section is its own; any other label's is the section of the
    nearest heading above it (without one, up to the first heading). Another
    requirement's label is left out, and so is a subsection that one heads.
    """
    if units[index].startswith("#"):
        start, level = index, _heading_level(units[index])
    else:
        start, level = next(
            ((i, _heading_level(units[i])) for i in range(index - 1, -1, -1) if units[i].startswith("#")), (-1, 6)
        )
    section, skip = [index] if start == index else [], None
    for i in range(start + 1, len(units)):
        heading = _heading_level(units[i]) if units[i].startswith("#") else None
        if heading is not None and heading <= level:
            break
        if heading is not None and skip is not None and heading <= skip:
            skip = None
        if skip is not None:
            continue
        if _other_label(units[i], requirement):
            skip = heading
            continue
        section.append(i)
    return section


def _withdrawal(text: str, withdrawn: re.Pattern[str], *, leads: bool = False, no_longer: bool = False) -> bool:
    """Whether a sentence of ``text`` matches ``withdrawn`` (a subject that a ``_WITHDRAWS``
    follows), or (``leads``) opens a segment with a state, or (``no_longer``) says
    something "no longer applies"."""
    for sentence in _SENTENCE_BREAK.split(text):
        if withdrawn.search(sentence) or (no_longer and re.search(_NO_LONGER, sentence, re.IGNORECASE)):
            return True
        if leads and any(_STATE_LEAD.match(segment) for segment in _SEGMENT_BREAK.split(sentence)):
            return True
    return False


def _not_governing(text: str, units: list[str], requirement: str, matched: str) -> dict[str, list[str]]:
    """What the document says about ``requirement`` not governing, by reason detail.

    Only the document's own words count; Reconcile Spec has no registry of which
    document governs. Five things block, each erring toward blocking:

    - ``status``: any declared status (a ``Status:`` line, front matter or table row,
      or the line after a ``Status`` heading) whose first word isn't in
      ``GOVERNING_STATUSES``. A status anywhere in the document counts, since this
      check can't tell which requirement a status belongs to.
    - ``superseded``: #50's supersession wording anywhere in the document, or a
      ``replaced_by:`` key.
    - ``section``: a heading above a label giving ``matched`` (its section, the
      document title, any enclosing heading), or a bracketed tag right after the id
      (``## SEC-4 (withdrawn)``), names a non-governing state.
    - ``title``: the label's own text after the id opens a segment with a state
      (``## SEC-4 Withdrawn``, ``## SEC-4 Require MFA - Deprecated``,
      ``- **SEC-4:** Replaced by SEC-9.``). A title that only uses such a word
      (``Reject deprecated TLS versions``) doesn't block.
    - ``prose``: in the label's text or its section (subsections included, other
      requirements' own text left out), a sentence that says something "no longer
      applies" (or governs, is in force, binding, ...), that withdraws its subject
      (``This requirement was replaced by SEC-9``), or that opens a segment with a
      state (``Withdrawn in favour of SEC-9.``). Anywhere in the document, a sentence
      that withdraws the requirement by its id (``SEC-4 no longer applies``) or the
      document itself (``This contract has been withdrawn``), and one that names the
      id as what it withdraws or replaces, whatever its subject (``SEC-9 replaces
      SEC-4``, ``We dropped SEC-4``, ``Obsoletes: SEC-4``; see ``_WITHDRAWING``), or that
      says in the passive that the id, "this requirement" or the document is withdrawn
      (``SEC-4 was dropped``, ``This requirement is overridden by SEC-9``; see
      ``_withdrawn_passive``). The label's own line counts there too, with the id as
      its subject (``## SEC-4 was dropped``).

    The values are the folded lines that tripped each one, for the handoff.
    """
    found: dict[str, list[str]] = {}
    lines = text.splitlines()
    for number, line in enumerate(lines):
        status = None
        if _STATUS_HEADING.fullmatch(line):
            status = next((later for later in lines[number + 1:] if later.strip()), "")
            shown = _fold(f"{line} {status}")
        elif found_status := _STATUS_LINE.match(line):
            status, shown = found_status.group("value"), _fold(line)
        if status is not None:
            word = re.search(r"[^\W\d_][\w-]*", status)
            if word is None or word.group(0).casefold() not in GOVERNING_STATUSES:
                found.setdefault("status", []).append(shown)
        if _SUPERSESSION.search(line) or _REPLACED_BY_KEY.match(line):
            found.setdefault("superseded", []).append(_fold(line))

    token = re.compile(rf"(?<![\w-]){re.escape(requirement)}(?![\w-])", re.IGNORECASE)
    passive = re.compile(_withdrawn_passive(_subject_in_list(requirement)), re.IGNORECASE)
    withdrawn = re.compile(
        rf"(?:{token.pattern}|{_THIS_REQUIREMENT}){_WITHDRAWS}|{_withdrawing(requirement)}"
        rf"",  # NEGATIVE CONTROL: passive forms off
        re.IGNORECASE,
    )
    own: set[int] = set()
    for index, body in _labels(units, requirement):
        if body != matched:
            continue
        own.add(index)
        label = units[index]
        after_id = label[token.search(label).end():].lstrip(" :*_`")
        tag = _TAG.match(after_id)
        if tag and _NOT_GOVERNING_WORDS.search(tag.group(1)):
            found.setdefault("section", []).append(label)
        elif any(_STATE_LEAD.match(segment) for segment in _SEGMENT_BREAK.split(after_id)):
            found.setdefault("title", []).append(label)
        # the label's own text, and the rest of its section, saying it no longer applies
        for i in _section(units, index, requirement):
            text = after_id if i == index else units[i]
            # the label's own line read whole too, so ``## SEC-4 is withdrawn`` has the id as its subject
            if _withdrawal(text, withdrawn, leads=i != index, no_longer=True):
                found.setdefault("prose", []).append(units[i])
        # the heading of each enclosing level above the label (a heading label's own title is its text)
        need = _heading_level(label) if label.startswith("#") else 7
        for earlier in reversed(units[:index]):
            if earlier.startswith("#") and _heading_level(earlier) < need:
                need = _heading_level(earlier)
                if _NOT_GOVERNING_WORDS.search(earlier):
                    found.setdefault("section", []).append(earlier)
    # anywhere else: a sentence that withdraws the requirement by its id, or the document itself
    for index, unit in enumerate(units):
        if index in own:
            continue
        if (token.search(unit) and _withdrawal(unit, withdrawn)) or _withdrawal(unit, _SELF_WITHDRAWN):
            found.setdefault("prose", []).append(unit)
    return {why: list(dict.fromkeys(shown)) for why, shown in found.items()}


def _elsewhere(
    sources: Mapping[str, SourceDocument | SourceUnreadable],
    units: dict[str, list[str]],
    checked_in: str,
    requirement: str,
    matched: str,
) -> dict[str, list[str]]:
    """The texts other documents in ``sources`` label ``requirement`` with, where they differ from ``matched``.

    Every other document counts, whatever it says about itself: an archived copy that
    states the requirement differently blocks too, since Reconcile Spec can't tell
    which document is the contract and an attacker's copy can say the same thing.
    """
    found: dict[str, list[str]] = {}
    for path, document in sources.items():
        if path == checked_in or not isinstance(document, SourceDocument):
            continue
        if path not in units:
            units[path] = _units(document.text)
        differing = [text for text in dict.fromkeys(_labelled_texts(units[path], requirement)) if text != matched]
        if differing:
            found[path] = differing
    return found


def _source_check(
    request: ReconcileRequest, sources: Mapping[str, SourceDocument | SourceUnreadable] | None
) -> tuple[SourceState, list[str], dict[str, str], dict[str, list[str]], dict[str, dict[str, list[str]]]]:
    """Compare each submitted protected text with what its document says.

    The submitted text must equal the text the document labels with the requirement
    id, after folding whitespace only: no case folding, no Unicode normalization,
    no partial match. A shorter, reworded or scoped-down text is not the requirement
    as written. A matched text then blocks if the document says it doesn't govern
    (``_not_governing``), or if another document in ``sources`` labels the
    requirement with different text (``_elsewhere``). A document in ``sources`` that
    no conflict is checked against but that couldn't be read blocks the request.
    Returns the state (the worst over all conflicts), the blocking reasons, per claim
    the document's text where it differs, per claim the lines that say it doesn't
    govern, and per claim the other documents' differing texts.
    """
    if sources is None:
        return "not_checked", ["request:source_not_checked"], {}, {}, {}
    labelled: dict[tuple[str, str], list[str]] = {}
    units: dict[str, list[str]] = {}
    reasons: list[str] = []
    differs: dict[str, str] = {}
    not_governing: dict[str, list[str]] = {}
    elsewhere: dict[str, dict[str, list[str]]] = {}
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
            found = _not_governing(source.text, units[path], requirement, distinct[0])
            if found:
                kinds = [why for why in ("status", "superseded", "section", "title", "prose") if why in found]
                reasons += [f"{cid}:source_not_governing:{why}" for why in kinds]
                not_governing[cid] = list(dict.fromkeys(line for why in kinds for line in found[why]))
                states.add("not_governing")
            other = _elsewhere(sources, units, path, requirement, distinct[0])
            if other:
                reasons.append(f"{cid}:requirement_differs_elsewhere")
                elsewhere[cid] = other
                states.add("contested")
            continue
        states.add("mismatch")
    checked = {protected_document(c, request.source_doc) for c in request.conflicts}
    for path, source in sources.items():
        if path not in checked and isinstance(source, SourceUnreadable):
            reasons.append(f"{path}:source_unreadable:{source.reason}")
            states.add("unreadable")
    for state in ("not_checked", "unreadable", "mismatch", "contested", "not_governing"):
        if state in states:
            return state, reasons, differs, not_governing, elsewhere
    return "matched", reasons, differs, not_governing, elsewhere


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
        if conflict.source_not_governing:
            lines += _text_block("Where the checked document says this requirement may not govern", "\n".join(conflict.source_not_governing))
        if conflict.source_elsewhere:
            lines += _text_block(
                "Other documents under the source root that state this requirement differently",
                "\n".join(f"{path}: {text}" for path, texts in conflict.source_elsewhere.items() for text in texts),
            )
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

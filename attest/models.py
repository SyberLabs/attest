"""Data model for Attest.

Six core models from docs/ARCHITECTURE.md plus BrowserObservation, the neutral
record of what a browser runner saw, and PromptObservation, the neutral record
of what a prompt Host reported (docs/CLI-RUNNER-SPEC.md). Two invariants are
enforced here:

* no Claim without a Span (the field is required and validated)
* no Verdict other than ``unverifiable`` without Evidence

Nothing in this module knows about any particular browser tool or prompt Host.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .contracts import (
    Approval,
    AuthorityDecision,
    AuthorityState,
    ClaimVersion,
    ComparisonState,
    EvidenceState,
    ExecutionState,
    RepairProposal,
    RepairState,
)
from .receipts import ReceiptEvidence, RunReceipt, VerifiedRunReceipt
from .snapshots import RepositorySnapshot, SourceVersion

ClaimKind = Literal["shell", "browser", "assert", "prose", "prompt"]
VerdictStatus = Literal["proven", "stale", "broken", "unverifiable", "skipped"]
EvidenceKind = Literal["shell", "browser", "prompt"]
ObservationStatus = Literal["passed", "failed", "unreachable"]
PromptStatus = Literal["completed", "failed", "parse_error", "refused", "needs_script"]


class Span(BaseModel):
    """A 1-based inclusive line range plus the exact doc text it covers."""

    model_config = ConfigDict(extra="forbid")

    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    text: str

    @model_validator(mode="after")
    def _ordered(self) -> "Span":
        if self.end_line < self.start_line:
            raise ValueError("end_line must be >= start_line")
        return self


class PromptContext(BaseModel):
    """What the doc says the Host supplies for one prompt example: the store it
    seeds, the input text it launches with, its store mode or policy, the run's
    name, and the model replies in round order. The sidecar file provides it,
    keyed by the prompt's frontmatter name (docs/CLI-RUNNER-SPEC.md)."""

    model_config = ConfigDict(extra="forbid")

    files: dict[str, str] = Field(default_factory=dict)  # store path -> text seeded before the run
    input: str | None = None  # text for the prompt's declared input file
    mode: Literal["ask", "plan", "agent"] | None = None  # Runvil's store mode
    refuse: list[str] = Field(default_factory=list)  # store paths whose changes the Host policy refuses
    execution: str | None = None  # the run's name, sys.execution
    script: list[str] = Field(default_factory=list)  # model replies, in round order


class Claim(BaseModel):
    """One checkable statement lifted from the doc."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^c\d{2,}$")
    kind: ClaimKind
    span: Span
    command: str | None = None
    objective: str | None = None
    expects: str | None = None
    store_as: str | None = None
    depends_on: list[str] = Field(default_factory=list)
    # prompt claims: True when the doc promises the run fails (a fence-error example)
    expects_error: bool = False
    # prompt claims: the argument string the doc says the example runs with
    args: str | None = None
    # prompt claims: what the sidecar says the Host supplies for this example
    context: PromptContext | None = None
    # names of sidecar fixtures that must exist before the claim runs
    setup: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _kind_fields(self) -> "Claim":
        if self.kind == "shell" and not (self.command and self.command.strip()):
            raise ValueError("shell claim needs a command")
        if self.kind == "browser" and not (self.objective and self.objective.strip()):
            raise ValueError("browser claim needs an objective")
        if self.kind == "prompt" and not (self.command and self.command.strip()):
            raise ValueError("prompt claim needs a command (the prompt file text)")
        if self.kind == "prose" and (self.command or self.objective or self.expects or self.expects_error):
            raise ValueError("prose claim must not carry command, objective or expects")
        if self.expects_error and self.kind != "prompt":
            raise ValueError("expects_error is only meaningful on a prompt claim")
        if self.args is not None and self.kind != "prompt":
            raise ValueError("args is only meaningful on a prompt claim")
        if self.context is not None and self.kind != "prompt":
            raise ValueError("context is only meaningful on a prompt claim")
        return self


class Fence(BaseModel):
    """A fenced code block located by line. Lines are the content lines, 1-based inclusive."""

    model_config = ConfigDict(extra="forbid")

    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    lang: str = ""
    text: str


class Doc(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    commit: str | None = None
    sha256: str
    lines: list[str]
    claims: list[Claim] = Field(default_factory=list)
    fences: list[Fence] = Field(default_factory=list)
    urls: list[str] = Field(default_factory=list)
    # False when the sidecar file declares the doc has no browser steps, so the
    # compiler does not demand a browser claim for prose that merely mentions pages
    browser_claims: bool = True

    def numbered(self, start: int = 1, end: int | None = None) -> str:
        """Return lines start..end (1-based inclusive) prefixed with their numbers."""
        end = end or len(self.lines)
        width = len(str(len(self.lines)))
        return "\n".join(
            f"{n:>{width}}  {self.lines[n - 1]}" for n in range(start, min(end, len(self.lines)) + 1)
        )


class BrowserObservation(BaseModel):
    """What a browser runner saw while attempting one browser claim.

    Tool-neutral on purpose. ``observed`` maps store_as keys to the exact text
    the runner read from the page.
    """

    model_config = ConfigDict(extra="forbid")

    status: ObservationStatus
    observed: dict[str, str] = Field(default_factory=dict)
    screenshot: str | None = None
    note: str | None = None


class PromptObservation(BaseModel):
    """What a prompt Host reported for one run of a prompt file.

    Tool-neutral: ``result`` is the run result exactly as the Host returned it,
    ``error_kind`` and ``message`` are the Host's own names and text.
    """

    model_config = ConfigDict(extra="forbid")

    status: PromptStatus
    result: str | None = None
    error_kind: str | None = None
    error_line: int | None = Field(default=None, ge=1)
    message: str | None = None
    rounds: int = Field(default=0, ge=0)
    host_version: str


class Evidence(BaseModel):
    """Shell evidence, a BrowserObservation or a PromptObservation; exactly the
    fields of one runner kind, never another's."""

    model_config = ConfigDict(extra="forbid")

    claim_id: str
    kind: EvidenceKind
    command: str | None = None
    exit_code: int | None = None
    timed_out: bool = False
    service_ready: bool = False
    stdout_tail: str | None = None
    detected_url: str | None = None
    # services (sidecar): the readiness URL polled and the last HTTP status it gave
    ready_url: str | None = None
    ready_status: int | None = None
    # substitutes (sidecar, --substitute): the command that ran in place of ``command``
    substitute: str | None = None
    observation: BrowserObservation | None = None
    prompt: PromptObservation | None = None

    @model_validator(mode="after")
    def _shape(self) -> "Evidence":
        if self.kind == "shell":
            if self.exit_code is None and not self.timed_out and not self.service_ready:
                raise ValueError("shell evidence needs an exit_code, service_ready=True, or timed_out=True")
            if self.observation is not None:
                raise ValueError("shell evidence must not carry a browser observation")
            if self.prompt is not None:
                raise ValueError("shell evidence must not carry a prompt observation")
        elif self.kind == "browser":
            if self.observation is None:
                raise ValueError("browser evidence needs an observation")
            if self.prompt is not None:
                raise ValueError("browser evidence must not carry a prompt observation")
        else:
            if self.prompt is None:
                raise ValueError("prompt evidence needs a prompt observation")
            if self.observation is not None:
                raise ValueError("prompt evidence must not carry a browser observation")
            shell_fields = (self.exit_code, self.stdout_tail, self.detected_url, self.ready_url, self.ready_status, self.substitute)
            if self.timed_out or self.service_ready or any(v is not None for v in shell_fields):
                raise ValueError("prompt evidence must not carry shell runner fields")
        return self

    @property
    def observed(self) -> dict[str, str]:
        return self.observation.observed if self.observation else {}

    @property
    def screenshot(self) -> str | None:
        return self.observation.screenshot if self.observation else None


class Verdict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str
    status: VerdictStatus
    reason: str
    evidence: Evidence | None = None
    label_match: float | None = Field(default=None, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _needs_evidence(self) -> "Verdict":
        if self.status != "unverifiable" and self.evidence is None:
            raise ValueError(f"verdict '{self.status}' requires evidence")
        if self.status != "skipped" and self.evidence is not None and self.evidence.claim_id != self.claim_id:
            raise ValueError("evidence belongs to a different claim")
        return self


class Brief(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str
    verdict: Verdict
    doc_excerpt: str
    grounding: list[str] = Field(default_factory=list)
    patch: str | None = None
    attempts: int = 0


class LedgerRow(BaseModel):
    """One append-only ledger line: a verdict plus bookkeeping."""

    model_config = ConfigDict(extra="forbid")

    turn: int
    doc_sha256: str
    claim_id: str
    kind: ClaimKind
    status: VerdictStatus
    reason: str
    label_match: float | None = None
    evidence_kind: EvidenceKind | None = None
    looped: bool = False
    timestamp: str

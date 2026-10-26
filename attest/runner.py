"""BrowserRunner: the neutral contract between Attest and whatever drives a browser.

A runner takes one browser Claim plus the base URL the shell runner detected and
returns a BrowserObservation. Attest never looks past that record. The only
implementation before event day is FakeBrowserRunner, which returns hand-written
observations for tests and offline demos.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

from .models import BrowserObservation, Claim


RunnerMode = Literal["simulation", "verified"]
SIMULATION_NOTE_PREFIX = "simulation-only:"
# A runner observation that no run record (for Kane, an evidence pack naming the run's
# session) is bound to. It is not verification evidence either.
UNBOUND_NOTE_PREFIX = "unbound:"


class SimulationBrowserObservation(BrowserObservation):
    """An observation produced by simulation, never by a live verifier."""


@runtime_checkable
class BrowserRunner(Protocol):
    name: str

    def observe(self, claim: Claim, base_url: str | None) -> BrowserObservation:
        """Attempt the claim in a browser and report what was seen."""
        ...

    def close(self) -> None: ...


class FakeBrowserRunner:
    """Simulation-only runner for tests and offline demos.

    ``observations`` maps claim ids (or the claim's ``expects`` text) to a
    BrowserObservation. Even an echoed or scripted ``passed`` result is marked
    simulation-only and cannot be used as verification evidence.
    """

    name = "fake"
    mode: RunnerMode = "simulation"

    def __init__(self, observations: dict[str, BrowserObservation] | None = None, default: str = "echo"):
        self.observations = observations or {}
        self.default = default
        self.calls: list[tuple[str, str | None]] = []

    @classmethod
    def from_file(cls, path: str | Path, default: str = "echo") -> "FakeBrowserRunner":
        """Load a JSON file: {"c07": {"status": "passed", "observed": {...}}, ...}."""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        obs = {k: BrowserObservation.model_validate(v) for k, v in data.items()}
        return cls(obs, default=default)

    def observe(self, claim: Claim, base_url: str | None) -> SimulationBrowserObservation:
        self.calls.append((claim.id, base_url))
        scripted = self.observations.get(claim.id)
        if scripted is None and claim.expects:
            scripted = self.observations.get(claim.expects)
        if scripted is not None:
            note = f"{SIMULATION_NOTE_PREFIX} fake runner cannot verify page state"
            if scripted.note:
                note += f"; scripted note: {scripted.note}"
            return SimulationBrowserObservation.model_validate(
                scripted.model_dump() | {"note": note}
            )
        if self.default == "unreachable" or base_url is None and self._needs_local_url(claim):
            return SimulationBrowserObservation(
                status="unreachable",
                note=f"{SIMULATION_NOTE_PREFIX} fake runner has no base URL to open",
            )
        key = claim.store_as or f"{claim.id}_label"
        observed = {key: claim.expects} if claim.expects else {}
        return SimulationBrowserObservation(
            status="passed",
            observed=observed,
            note=f"{SIMULATION_NOTE_PREFIX} fake runner echoed the expected label; not verification evidence",
        )

    @staticmethod
    def _needs_local_url(claim: Claim) -> bool:
        text = f"{claim.objective or ''} {claim.span.text}".lower()
        return "localhost" in text or "127.0.0.1" in text

    def close(self) -> None:
        return None

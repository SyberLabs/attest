"""PromptRunner: the neutral contract between Attest and whatever runs a prompt file.

A runner takes one prompt Claim and returns a PromptObservation. Attest never
looks past that record. The only implementation that runs anything is the
Runvil Host (docs/CLI-RUNNER-SPEC.md); FakePromptRunner replays recorded
observations for tests and offline demos, and its observations are simulation
only.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

from .models import Claim, PromptObservation

RunnerMode = Literal["simulation", "verified"]
SIMULATION_MESSAGE_PREFIX = "simulation-only:"


class SimulationPromptObservation(PromptObservation):
    """An observation produced by simulation, never by a live Host."""


@runtime_checkable
class PromptRunner(Protocol):
    name: str

    def run(self, claim: Claim) -> PromptObservation:
        """Run the claim's prompt file and report what the Host returned."""
        ...

    def close(self) -> None: ...


class FakePromptRunner:
    """Simulation-only runner that replays recorded PromptObservation records.

    ``observations`` maps claim ids to a PromptObservation. A claim with no
    recording ends as ``needs_script``: the fake never guesses a result.
    """

    name = "fake"
    mode: RunnerMode = "simulation"

    def __init__(self, observations: dict[str, PromptObservation] | None = None):
        self.observations = observations or {}
        self.calls: list[str] = []

    @classmethod
    def from_file(cls, path: str | Path) -> "FakePromptRunner":
        """Load a JSON file: {"c01": {"status": "completed", "result": "hello", "host_version": "..."}, ...}."""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"{path}: expected an object mapping claim ids to prompt observations")
        obs = {k: PromptObservation.model_validate(v) for k, v in data.items()}
        return cls(obs)

    def run(self, claim: Claim) -> SimulationPromptObservation:
        self.calls.append(claim.id)
        scripted = self.observations.get(claim.id)
        if scripted is None:
            return SimulationPromptObservation(
                status="needs_script",
                message=f"{SIMULATION_MESSAGE_PREFIX} fake prompt runner has no recorded observation for {claim.id}",
                host_version="fake",
            )
        message = f"{SIMULATION_MESSAGE_PREFIX} fake prompt runner replayed a recording; not verification evidence"
        if scripted.message:
            message += f"; recorded message: {scripted.message}"
        return SimulationPromptObservation.model_validate(scripted.model_dump() | {"message": message})

    def close(self) -> None:
        return None

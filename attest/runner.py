"""BrowserRunner: the neutral contract between Attest and whatever drives a browser.

A runner takes one browser Claim plus the base URL the shell runner detected and
returns a BrowserObservation. Attest never looks past that record. The only
implementation before event day is FakeBrowserRunner, which returns hand-written
observations for tests and offline demos.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol, runtime_checkable

from .models import BrowserObservation, Claim


@runtime_checkable
class BrowserRunner(Protocol):
    name: str

    def observe(self, claim: Claim, base_url: str | None) -> BrowserObservation:
        """Attempt the claim in a browser and report what was seen."""
        ...

    def close(self) -> None: ...


class FakeBrowserRunner:
    """Returns scripted observations.

    ``observations`` maps claim ids (or the claim's ``expects`` text) to a
    BrowserObservation. Claims with no script get the default behaviour:

    * ``echo``: pass and report exactly the label the doc expects (a clean run)
    * ``unreachable``: report the page as unreachable
    """

    name = "fake"

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

    def observe(self, claim: Claim, base_url: str | None) -> BrowserObservation:
        self.calls.append((claim.id, base_url))
        scripted = self.observations.get(claim.id)
        if scripted is None and claim.expects:
            scripted = self.observations.get(claim.expects)
        if scripted is not None:
            return scripted
        if self.default == "unreachable" or base_url is None and self._needs_local_url(claim):
            return BrowserObservation(status="unreachable", note="fake runner: no base URL to open")
        key = claim.store_as or f"{claim.id}_label"
        observed = {key: claim.expects} if claim.expects else {}
        return BrowserObservation(
            status="passed",
            observed=observed,
            note="fake runner: echoed the doc's expected label",
        )

    @staticmethod
    def _needs_local_url(claim: Claim) -> bool:
        text = f"{claim.objective or ''} {claim.span.text}".lower()
        return "localhost" in text or "127.0.0.1" in text

    def close(self) -> None:
        return None

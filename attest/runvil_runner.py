"""RunvilPromptRunner: the Python side of the runvil-docs-host protocol (docs/CLI-RUNNER-SPEC.md).

One Host process per claim. One JSON object goes in on stdin::

    {"prompt": "<prompt file text>", "args": [], "script": [], "seed": 123, "started_at": "..."}

and one PromptObservation comes out on stdout. ``seed`` and ``started_at`` are derived
from the claim id, so a rerun of the same claim gives the same events. ``script`` holds
the model replies in round order; nothing in this session supplies any, so a prompt that
asks a model ends ``needs_script`` and the claim ends unverifiable, never guessed.

The Host binary comes from ``ATTEST_RUNVIL_HOST`` or ``PATH``. A missing binary is an
error before anything runs; a Host that answers badly is an error too, never evidence.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pydantic import ValidationError

from .models import Claim, PromptObservation

HOST_NAME = "runvil-docs-host"
HOST_ENV = "ATTEST_RUNVIL_HOST"
EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)


class PromptRunnerError(RuntimeError):
    pass


def claim_seed(claim_id: str) -> int:
    """A fixed RunSeed per claim id: the first 8 bytes of its sha256."""
    return int.from_bytes(hashlib.sha256(claim_id.encode("utf-8")).digest()[:8], "big")


def claim_started_at(claim_id: str) -> str:
    """A fixed wall clock per claim id, derived from the same hash, as an RFC 3339 string."""
    seconds = claim_seed(claim_id) % (365 * 24 * 3600)
    return (EPOCH + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")


def find_host() -> str | None:
    override = os.environ.get(HOST_ENV)
    if override:
        return override if Path(override).is_file() else None
    return shutil.which(HOST_NAME)


class RunvilPromptRunner:
    name = "runvil"
    mode = "verified"

    def __init__(self, host: str | None = None, timeout_s: float = 30.0, cwd: Path | None = None):
        self.host = host
        self.timeout_s = timeout_s
        self.cwd = cwd
        self.calls: list[str] = []

    def check(self) -> str:
        """Resolve the Host binary or raise before any claim runs."""
        host = self.host or find_host()
        if not host:
            raise PromptRunnerError(
                f"{HOST_NAME} not found; set {HOST_ENV} to the binary, put it on PATH, or use --prompt-runner fake"
            )
        self.host = host
        return host

    def request(self, claim: Claim, args: list[str] | None = None, script: list[str] | None = None) -> dict:
        if args is None:
            # the argument string the doc names for the example, as one argument the Host joins verbatim
            args = [claim.args] if claim.args else []
        return {
            "prompt": claim.command or "",
            "args": list(args),
            "script": list(script or []),
            "seed": claim_seed(claim.id),
            "started_at": claim_started_at(claim.id),
        }

    def run(self, claim: Claim) -> PromptObservation:
        host = self.check()
        self.calls.append(claim.id)
        payload = json.dumps(self.request(claim))
        try:
            proc = subprocess.run(
                [host],
                input=payload,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=self.timeout_s,
                cwd=str(self.cwd) if self.cwd else None,
            )
        except subprocess.TimeoutExpired:
            return PromptObservation(
                status="refused",
                message=f"{HOST_NAME} did not answer within {self.timeout_s:g}s",
                host_version="unknown",
            )
        except OSError as exc:
            raise PromptRunnerError(f"cannot start {host}: {exc}") from exc
        if proc.returncode != 0:
            raise PromptRunnerError(
                f"{HOST_NAME} exited {proc.returncode} for {claim.id}: {(proc.stderr or '').strip()[:500]}"
            )
        try:
            return PromptObservation.model_validate_json(proc.stdout.strip())
        except (ValidationError, ValueError) as exc:
            raise PromptRunnerError(f"{HOST_NAME} answered with something other than a PromptObservation for {claim.id}: {exc}") from exc

    def close(self) -> None:
        return None

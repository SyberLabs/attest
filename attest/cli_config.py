"""The attest.cli.yaml sidecar (docs/CLI-RUNNER-SPEC.md): fixtures, services and substitutes
that a command doc assumes a reader already has, kept beside the doc so the doc stays untouched.

    fixtures:
      gateway-config:
        path: gateway.toml           # written into the scratch directory before any claim
        from: fixtures/gateway.toml  # relative to this file
    services:
      - match: "runvil-gateway --config"    # a claim whose command starts this way is a service
        ready: http://127.0.0.1:8081/health # polled until it answers 200 (loopback only)
        ready_timeout_s: 60
    substitute:
      "cargo install gateway": "cargo install --path crates/gateway/app"  # only with --substitute
    browser_claims: false  # the doc has no browser steps; prose about pages is not a claim

Everything is validated when loaded; an unknown key or an unsafe path is an error, never ignored.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

import yaml

SIDECAR_NAME = "attest.cli.yaml"
LOOPBACK_HOSTS = {"localhost", "127.0.0.1"}


class CliConfigError(ValueError):
    pass


@dataclass(frozen=True)
class FixtureSpec:
    name: str
    path: str  # relative path inside the scratch directory, POSIX form
    source: Path  # the file whose bytes are written there

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.source.read_bytes()).hexdigest()


@dataclass(frozen=True)
class ServiceSpec:
    match: str
    ready: str
    ready_timeout_s: float | None = None  # None: rules.yaml ready_timeout_s

    def matches(self, command: str) -> bool:
        return command.strip().startswith(self.match)


@dataclass(frozen=True)
class CliConfig:
    fixtures: dict[str, FixtureSpec] = field(default_factory=dict)
    services: tuple[ServiceSpec, ...] = ()
    substitutes: dict[str, str] = field(default_factory=dict)
    browser_claims: bool = True
    path: Path | None = None

    def service_for(self, command: str) -> ServiceSpec | None:
        return next((s for s in self.services if s.matches(command)), None)

    def substitute_for(self, command: str) -> str | None:
        return self.substitutes.get(command.strip())

    def fixtures_named_in(self, command: str) -> list[str]:
        """Fixtures whose scratch path is mentioned by a command, by rule, no model."""
        names = []
        for name, fx in self.fixtures.items():
            base = PurePosixPath(fx.path).name
            if fx.path in command or base in command:
                names.append(name)
        return names


def find_cli_config(doc_source: str) -> Path | None:
    """The sidecar beside a local doc, if there is one. URLs have none."""
    if doc_source.startswith(("http://", "https://")):
        return None
    candidate = Path(doc_source).resolve().parent / SIDECAR_NAME
    return candidate if candidate.is_file() else None


def _safe_relative(path: str, what: str) -> str:
    p = PurePosixPath(path.replace("\\", "/"))
    if p.is_absolute() or not p.parts or any(part in ("..", "") for part in p.parts) or ":" in path:
        raise CliConfigError(f"{what}: path must be relative and inside the scratch directory, got {path!r}")
    return str(p)


def _loopback(url: str, what: str) -> str:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or parts.hostname not in LOOPBACK_HOSTS:
        raise CliConfigError(f"{what}: readiness URL must be http(s) on localhost or 127.0.0.1, got {url!r}")
    return url


def load_cli_config(path: str | Path) -> CliConfig:
    path = Path(path)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except OSError as exc:
        raise CliConfigError(f"cannot read sidecar {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise CliConfigError(f"{path}: expected a mapping at the top level")
    known = {"fixtures", "services", "substitute", "browser_claims"}
    unknown = set(data) - known
    if unknown:
        raise CliConfigError(f"{path}: unknown keys {sorted(unknown)}; known keys are {sorted(known)}")
    base = path.resolve().parent

    fixtures: dict[str, FixtureSpec] = {}
    raw_fixtures = data.get("fixtures") or {}
    if not isinstance(raw_fixtures, dict):
        raise CliConfigError(f"{path}: fixtures must be a mapping of name to {{path, from}}")
    for name, spec in raw_fixtures.items():
        if not isinstance(spec, dict) or set(spec) != {"path", "from"}:
            raise CliConfigError(f"{path}: fixture {name!r} needs exactly the keys path and from")
        rel = _safe_relative(str(spec["path"]), f"fixture {name!r}")
        source = (base / str(spec["from"])).resolve()
        if not source.is_file():
            raise CliConfigError(f"{path}: fixture {name!r}: source file not found: {source}")
        fixtures[str(name)] = FixtureSpec(name=str(name), path=rel, source=source)

    services: list[ServiceSpec] = []
    raw_services = data.get("services") or []
    if not isinstance(raw_services, list):
        raise CliConfigError(f"{path}: services must be a list")
    for i, spec in enumerate(raw_services):
        if not isinstance(spec, dict) or not {"match", "ready"} <= set(spec) or not set(spec) <= {"match", "ready", "ready_timeout_s"}:
            raise CliConfigError(f"{path}: service {i + 1} needs match and ready, optionally ready_timeout_s")
        match = str(spec["match"]).strip()
        if not match:
            raise CliConfigError(f"{path}: service {i + 1}: match must not be empty")
        timeout = spec.get("ready_timeout_s")
        if timeout is not None and (not isinstance(timeout, (int, float)) or timeout <= 0):
            raise CliConfigError(f"{path}: service {i + 1}: ready_timeout_s must be a positive number")
        services.append(ServiceSpec(match=match, ready=_loopback(str(spec["ready"]), f"service {i + 1}"), ready_timeout_s=float(timeout) if timeout is not None else None))

    substitutes: dict[str, str] = {}
    raw_subs = data.get("substitute") or {}
    if not isinstance(raw_subs, dict):
        raise CliConfigError(f"{path}: substitute must be a mapping of command to replacement")
    for cmd, repl in raw_subs.items():
        if not isinstance(repl, str) or not repl.strip() or not str(cmd).strip():
            raise CliConfigError(f"{path}: substitute for {cmd!r} must be a non-empty command")
        substitutes[str(cmd).strip()] = repl

    browser_claims = data.get("browser_claims", True)
    if not isinstance(browser_claims, bool):
        raise CliConfigError(f"{path}: browser_claims must be true or false")

    return CliConfig(fixtures=fixtures, services=tuple(services), substitutes=substitutes, browser_claims=browser_claims, path=path)

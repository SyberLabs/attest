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
    expects:
      - match: 'curl -H "Authorization: Bearer wrong-token"'  # a command that starts this way
        output: unauthorized                                  # must print this; the text must appear in the doc
    prompts:
      report:                               # the prompt's frontmatter name
        files: { paper.md: the paper body } # seeded into the run's store before it starts
        input: "# Paper"                    # text for the prompt's declared input file
        mode: ask                           # Runvil's store mode: ask, plan or agent
        refuse: [secret.md]                 # store paths whose changes the Host policy refuses
        execution: execute-test             # the run's name, sys.execution
        script: [first reply, second reply] # model replies, in round order
        args: rust                          # overrides the argument string the doc names
    browser_claims: false  # the doc has no browser steps; prose about pages is not a claim

Everything is validated when loaded; an unknown key or an unsafe path is an error, never ignored.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

import yaml
from pydantic import ValidationError

from .models import PromptContext

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
class ExpectSpec:
    match: str
    output: str

    def matches(self, command: str) -> bool:
        return command.strip().startswith(self.match)


@dataclass(frozen=True)
class PromptSpec:
    """The Host context for one prompt example, by frontmatter name, and an
    argument string that overrides the one the doc names."""

    context: PromptContext
    args: str | None = None


@dataclass(frozen=True)
class CliConfig:
    fixtures: dict[str, FixtureSpec] = field(default_factory=dict)
    services: tuple[ServiceSpec, ...] = ()
    substitutes: dict[str, str] = field(default_factory=dict)
    expects: tuple[ExpectSpec, ...] = ()
    prompts: dict[str, PromptSpec] = field(default_factory=dict)
    browser_claims: bool = True
    path: Path | None = None

    def service_for(self, command: str) -> ServiceSpec | None:
        return next((s for s in self.services if s.matches(command)), None)

    def expects_for(self, command: str) -> str | None:
        return next((e.output for e in self.expects if e.matches(command)), None)

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
    known = {"fixtures", "services", "substitute", "expects", "prompts", "browser_claims"}
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

    expects: list[ExpectSpec] = []
    raw_expects = data.get("expects") or []
    if not isinstance(raw_expects, list):
        raise CliConfigError(f"{path}: expects must be a list")
    for i, spec in enumerate(raw_expects):
        if not isinstance(spec, dict) or set(spec) != {"match", "output"}:
            raise CliConfigError(f"{path}: expects entry {i + 1} needs exactly match and output")
        match, output = str(spec["match"]).strip(), str(spec["output"])
        if not match or not output.strip():
            raise CliConfigError(f"{path}: expects entry {i + 1}: match and output must not be empty")
        expects.append(ExpectSpec(match=match, output=output))

    prompts: dict[str, PromptSpec] = {}
    raw_prompts = data.get("prompts") or {}
    if not isinstance(raw_prompts, dict):
        raise CliConfigError(f"{path}: prompts must be a mapping of prompt name to its Host context")
    for name, spec in raw_prompts.items():
        if not isinstance(spec, dict):
            raise CliConfigError(f"{path}: prompt {name!r} needs a mapping")
        spec = dict(spec)
        args = spec.pop("args", None)
        if args is not None and (not isinstance(args, str) or not args):
            raise CliConfigError(f"{path}: prompt {name!r}: args must be a non-empty string")
        files = spec.get("files")
        if files is not None:
            if not isinstance(files, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in files.items()):
                raise CliConfigError(f"{path}: prompt {name!r}: files must map store paths to text")
            for p in files:
                _safe_relative(p, f"prompt {name!r} file")
        try:
            context = PromptContext.model_validate(spec)
        except ValidationError as exc:
            raise CliConfigError(f"{path}: prompt {name!r}: {exc.errors()[0]['msg']} at {'.'.join(str(x) for x in exc.errors()[0]['loc']) or 'context'}") from exc
        prompts[str(name)] = PromptSpec(context=context, args=args)

    browser_claims = data.get("browser_claims", True)
    if not isinstance(browser_claims, bool):
        raise CliConfigError(f"{path}: browser_claims must be true or false")

    return CliConfig(
        fixtures=fixtures,
        services=tuple(services),
        substitutes=substitutes,
        expects=tuple(expects),
        prompts=prompts,
        browser_claims=browser_claims,
        path=path,
    )

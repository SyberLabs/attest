"""The attest.cli.yaml sidecar: fixtures, services, substitutes (docs/CLI-RUNNER-SPEC.md, M3).

The Gateway guide's first chapter runs against a fake gateway started inside the test:
tests/fixtures/runvil/fake_gateway.py behind a `runvil-gateway` script on PATH, plus a fake
`cargo` that fails like the real one does for an unpublished crate. Nothing touches the network.
"""

from __future__ import annotations

import os
import socket
import sys
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from attest.cli_config import CliConfig, CliConfigError, FixtureSpec, ServiceSpec, find_cli_config, load_cli_config
from attest.ingest import ingest
from attest.models import Claim, Evidence, Span, Verdict
from attest.pipeline import annotate_setup, check_setup, execute, prove
from attest.shell import ShellSession
from attest.verdict import Rules, satisfies_dependents

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures" / "runvil"
CHAPTER = FIX / "gateway-01-install-and-run.md"
PY = sys.executable.replace("\\", "/")


@pytest.fixture(autouse=True)
def no_model(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


class StubBrowserRunner:
    """No browser claims in these docs; prove() still needs a non-fake runner."""

    name = "stub"

    def observe(self, claim, base_url):
        raise AssertionError("no browser claims expected")

    def close(self):
        pass


def fake_tools(tmp_path: Path) -> dict[str, str]:
    """Put `runvil-gateway` and `cargo` stand-ins first on PATH; return the shell env."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gateway = (FIX / "fake_gateway.py").as_posix()
    (bin_dir / "runvil-gateway").write_text(f'#!/bin/sh\nexec "{PY}" "{gateway}" "$@"\n', encoding="utf-8", newline="\n")
    (bin_dir / "cargo").write_text(
        "#!/bin/sh\ncase \"$*\" in *--path*) echo \"   Installing gateway (fake)\"; exit 0;; esac\n"
        "echo 'error: could not find `gateway` in registry `crates-io`' >&2\nexit 101\n",
        encoding="utf-8",
        newline="\n",
    )
    for p in bin_dir.iterdir():
        p.chmod(0o755)
    return {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}


def gateway_doc(tmp_path: Path, port: int) -> tuple[Path, Path]:
    """The chapter with the example port replaced, plus a sidecar for it; both in tmp_path."""
    doc = tmp_path / "01-install-and-run.md"
    doc.write_text(CHAPTER.read_text(encoding="utf-8").replace("8081", str(port)), encoding="utf-8", newline="\n")
    (tmp_path / "gateway.toml").write_text(
        (FIX / "gateway.toml").read_text(encoding="utf-8").replace("8081", str(port)), encoding="utf-8", newline="\n"
    )
    side = tmp_path / "attest.cli.yaml"
    side.write_text(
        (FIX / "attest.cli.yaml").read_text(encoding="utf-8").replace("8081", str(port)).replace("ready_timeout_s: 60", "ready_timeout_s: 20"),
        encoding="utf-8",
        newline="\n",
    )
    return doc, side


# -- loading the sidecar ---------------------------------------------------------------------


def test_sidecar_beside_the_fixture_chapters_loads_and_matches_the_spec_example():
    found = find_cli_config(str(CHAPTER))
    assert found == FIX / "attest.cli.yaml"
    cfg = load_cli_config(found)
    assert cfg.path == found
    fx = cfg.fixtures["gateway-config"]
    assert fx.path == "gateway.toml" and fx.source == (FIX / "gateway.toml").resolve()
    assert len(fx.sha256) == 64
    assert cfg.services == (ServiceSpec(match="runvil-gateway --config", ready="http://127.0.0.1:8081/health", ready_timeout_s=60.0),)
    assert cfg.service_for("runvil-gateway --config gateway.toml --profile main") is cfg.services[0]
    assert cfg.service_for("runvil-gateway --version") is None
    assert cfg.substitute_for("cargo install gateway") == "cargo install --path crates/gateway/app"
    assert cfg.substitute_for("cargo install gateway2") is None
    assert cfg.browser_claims is False
    assert cfg.expects_for('curl -H "Authorization: Bearer wrong-token" http://127.0.0.1:8081/v1/models') == "unauthorized"
    assert cfg.expects_for("curl http://127.0.0.1:8081/health") is None
    assert cfg.fixtures_named_in("runvil-gateway --config gateway.toml") == ["gateway-config"]
    assert cfg.fixtures_named_in("curl http://127.0.0.1:8081/health") == []
    assert find_cli_config("https://example.test/doc.md") is None
    assert CliConfig().service_for("x") is None and CliConfig().browser_claims is True


@pytest.mark.parametrize(
    "text,match",
    [
        ("unknown: 1\n", "unknown keys"),
        ("services:\n  - match: x\n    ready: http://example.com/health\n", "localhost or 127.0.0.1"),
        ("services:\n  - match: x\n    ready: ftp://127.0.0.1/health\n", "localhost or 127.0.0.1"),
        ("services:\n  - match: ''\n    ready: http://127.0.0.1:1/h\n", "must not be empty"),
        ("services:\n  - match: x\n    ready: http://127.0.0.1:1/h\n    ready_timeout_s: 0\n", "positive number"),
        ("fixtures:\n  a:\n    path: ../escape.toml\n    from: gateway.toml\n", "inside the scratch directory"),
        ("fixtures:\n  a:\n    path: /abs.toml\n    from: gateway.toml\n", "inside the scratch directory"),
        ("fixtures:\n  a:\n    path: ok.toml\n    from: missing.toml\n", "source file not found"),
        ("fixtures:\n  a:\n    path: ok.toml\n", "exactly the keys path and from"),
        ("substitute:\n  'a b': ''\n", "non-empty command"),
        ("browser_claims: maybe\n", "true or false"),
        ("- a list\n", "mapping at the top level"),
    ],
)
def test_sidecar_rejects_unsafe_or_unknown_content(tmp_path, text, match):
    (tmp_path / "gateway.toml").write_text("x", encoding="utf-8")
    side = tmp_path / "attest.cli.yaml"
    side.write_text(text, encoding="utf-8")
    with pytest.raises(CliConfigError, match=match):
        load_cli_config(side)


# -- fixtures and setup --------------------------------------------------------------------------


def test_fixtures_are_written_before_the_first_claim_and_recorded(tmp_path):
    src = tmp_path / "src.toml"
    src.write_bytes(b"[server]\nbind = \"127.0.0.1:1\"\n")
    fx = FixtureSpec(name="cfg", path="conf/gateway.toml", source=src)
    with ShellSession(workdir=tmp_path / "scratch", timeout_s=20, recipes_only=False) as s:
        written = s.write_fixtures({"cfg": fx})
        assert written == [{"name": "cfg", "path": "conf/gateway.toml", "sha256": fx.sha256}]
        assert (tmp_path / "scratch" / "conf" / "gateway.toml").read_bytes() == src.read_bytes()
        ev = s.run(Claim(id="c01", kind="shell", span=Span(start_line=1, end_line=1, text="cat"), command="cat conf/gateway.toml"))
        assert ev.exit_code == 0 and "127.0.0.1:1" in ev.stdout_tail
        with pytest.raises(ValueError, match="outside the scratch directory"):
            s.write_fixtures([FixtureSpec(name="bad", path="../../escape", source=src)])


def test_setup_is_annotated_by_rule_and_checked_before_anything_runs(tmp_path):
    cfg = load_cli_config(FIX / "attest.cli.yaml")
    claims = [
        Claim(id="c01", kind="shell", span=Span(start_line=1, end_line=1, text="x"), command="runvil-gateway --config gateway.toml --profile main"),
        Claim(id="c02", kind="shell", span=Span(start_line=2, end_line=2, text="x"), command="curl http://127.0.0.1:8081/health"),
    ]
    annotated = annotate_setup(claims, cfg)
    assert annotated[0].setup == ["gateway-config"] and annotated[1].setup == []
    check_setup(annotated, cfg)
    orphan = [claims[0].model_copy(update={"setup": ["not-defined"]})]
    with pytest.raises(ValueError, match="not-defined"):
        check_setup(orphan, cfg)
    marker = tmp_path / "ran"
    claim = Claim(id="c01", kind="shell", span=Span(start_line=1, end_line=1, text="x"), command=f'echo ran > "{marker.as_posix()}"', setup=["ghost"])
    with pytest.raises(ValueError, match="ghost"):
        execute([claim], StubBrowserRunner(), Rules(), tmp_path / "scratch", cli_config=cfg)
    assert not marker.exists()


# -- services --------------------------------------------------------------------------------


def test_service_is_proven_when_its_readiness_url_answers_200_and_is_closed_after(tmp_path):
    port = free_port()
    doc, side = gateway_doc(tmp_path, port)
    cfg = load_cli_config(side)
    env = fake_tools(tmp_path)
    claim = Claim(id="c01", kind="shell", span=Span(start_line=1, end_line=1, text="x"), command=f"runvil-gateway --config gateway.toml --profile main")
    evidence, verdicts, session = execute([claim], StubBrowserRunner(), Rules(ready_timeout_s=20), tmp_path / "scratch", shell_env=env, cli_config=cfg)
    try:
        ev = evidence["c01"]
        assert ev.service_ready is True and ev.exit_code is None and ev.timed_out is False
        assert ev.ready_url == f"http://127.0.0.1:{port}/health" and ev.ready_status == 200
        assert verdicts[0].status == "proven" and "answered 200" in verdicts[0].reason
        assert session.base_url == f"http://127.0.0.1:{port}/"
        assert port_open(port)
        assert session.fixtures_written[0]["path"] == "gateway.toml"
    finally:
        session.close()
    time.sleep(0.5)
    assert not port_open(port)


def test_service_that_never_answers_200_is_broken_with_the_readiness_reason(tmp_path):
    port = free_port()
    doc, side = gateway_doc(tmp_path, port)
    cfg = load_cli_config(side)
    env = fake_tools(tmp_path)
    claim = Claim(id="c01", kind="shell", span=Span(start_line=1, end_line=1, text="x"), command="runvil-gateway --config gateway.toml --profile main --unhealthy")
    cfg = CliConfig(fixtures=cfg.fixtures, services=(ServiceSpec(match="runvil-gateway --config", ready=f"http://127.0.0.1:{port}/health", ready_timeout_s=2.0),))
    t0 = time.monotonic()
    evidence, verdicts, session = execute([claim], StubBrowserRunner(), Rules(ready_timeout_s=2.0), tmp_path / "scratch", shell_env=env, cli_config=cfg)
    session.close()
    assert time.monotonic() - t0 < 15
    ev = evidence["c01"]
    assert ev.timed_out is True and ev.service_ready is False and ev.ready_status == 503
    assert verdicts[0].status == "broken"
    assert "did not answer 200 within 2s" in verdicts[0].reason and "503" in verdicts[0].reason
    time.sleep(0.3)
    assert not port_open(port)


def test_service_that_exits_before_readiness_is_broken_by_exit_code(tmp_path):
    port = free_port()
    cfg = CliConfig(services=(ServiceSpec(match="runvil-gateway --config", ready=f"http://127.0.0.1:{port}/health", ready_timeout_s=5.0),))
    env = fake_tools(tmp_path)
    # no fixture written, so the fake gateway cannot read gateway.toml and exits 2
    claim = Claim(id="c01", kind="shell", span=Span(start_line=1, end_line=1, text="x"), command="runvil-gateway --config gateway.toml --profile main")
    evidence, verdicts, session = execute([claim], StubBrowserRunner(), Rules(), tmp_path / "scratch", shell_env=env, cli_config=cfg)
    session.close()
    assert evidence["c01"].exit_code == 2 and evidence["c01"].ready_url
    assert verdicts[0].status == "broken" and "exit code 2" in verdicts[0].reason


# -- substitutes ---------------------------------------------------------------------------------


def test_substitutes_are_off_by_default_and_capped_at_stale_when_allowed(tmp_path):
    env = fake_tools(tmp_path)
    cfg = CliConfig(substitutes={"cargo install gateway": "cargo install --path crates/gateway/app"})
    claim = Claim(id="c01", kind="shell", span=Span(start_line=1, end_line=1, text="x"), command="cargo install gateway")
    evidence, verdicts, session = execute([claim], StubBrowserRunner(), Rules(), tmp_path / "s1", shell_env=env, cli_config=cfg)
    session.close()
    assert evidence["c01"].exit_code == 101 and evidence["c01"].substitute is None
    assert verdicts[0].status == "broken"
    evidence, verdicts, session = execute([claim], StubBrowserRunner(), Rules(), tmp_path / "s2", shell_env=env, cli_config=cfg, allow_substitutes=True)
    session.close()
    ev = evidence["c01"]
    assert ev.exit_code == 0 and ev.substitute == "cargo install --path crates/gateway/app" and ev.command == "cargo install gateway"
    assert verdicts[0].status == "stale"
    assert "cargo install gateway" in verdicts[0].reason and "crates/gateway/app" in verdicts[0].reason
    assert satisfies_dependents(verdicts[0]) is True


def test_satisfies_dependents_for_proven_or_a_step_that_ran_and_succeeded():
    ev = Evidence(claim_id="c01", kind="shell", command="a", exit_code=0)
    assert satisfies_dependents(Verdict(claim_id="c01", status="proven", reason="r", evidence=ev))
    # stale only in its printed output: the step ran and succeeded, so later steps may run
    assert satisfies_dependents(Verdict(claim_id="c01", status="stale", reason="r", evidence=ev))
    # a stale shell step that did not succeed, or a skip carrying another claim's evidence, still blocks
    assert not satisfies_dependents(Verdict(claim_id="c01", status="stale", reason="r", evidence=ev.model_copy(update={"exit_code": 2})))
    assert not satisfies_dependents(Verdict(claim_id="c02", status="skipped", reason="r", evidence=ev))
    browser = Evidence(claim_id="c01", kind="browser", observation=__import__("attest.models", fromlist=["BrowserObservation"]).BrowserObservation(status="passed"))
    assert not satisfies_dependents(Verdict(claim_id="c01", status="stale", reason="r", evidence=browser))
    sub = Evidence(claim_id="c01", kind="shell", command="a", exit_code=0, substitute="b")
    assert satisfies_dependents(Verdict(claim_id="c01", status="stale", reason="r", evidence=sub))
    failed_sub = Evidence(claim_id="c01", kind="shell", command="a", exit_code=1, substitute="b")
    assert not satisfies_dependents(Verdict(claim_id="c01", status="broken", reason="r", evidence=failed_sub))
    assert not satisfies_dependents(Verdict(claim_id="c01", status="unverifiable", reason="r"))


# -- the Gateway chapter end to end ---------------------------------------------------------------


def test_gateway_chapter_install_step_is_broken_and_everything_after_is_skipped(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # the compiler cache is relative to the working directory
    port = free_port()
    doc, side = gateway_doc(tmp_path, port)
    env = fake_tools(tmp_path)
    home = tmp_path / "home"
    result = prove(str(doc), runner=StubBrowserRunner(), home=home, shell_env=env, cli_config=side)
    statuses = [result.by_id[c.id].status for c in result.claims]
    # the untagged systemd snippet (NAME=value lines) is configuration, so prose, never run
    assert [c.kind for c in result.claims] == ["shell"] * 6 + ["prose"]
    assert statuses == ["broken", "skipped", "skipped", "skipped", "skipped", "skipped", "unverifiable"]
    assert "exit code 101" in result.by_id["c01"].reason
    assert result.by_id["c01"].evidence.substitute is None
    assert not port_open(port)


def test_gateway_chapter_runs_against_the_fake_gateway_under_substitute(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    port = free_port()
    doc, side = gateway_doc(tmp_path, port)
    env = fake_tools(tmp_path)
    home = tmp_path / "home"
    result = prove(str(doc), runner=StubBrowserRunner(), home=home, shell_env=env, cli_config=side, allow_substitutes=True)
    v = result.by_id
    assert v["c01"].status == "stale" and "cargo install --path crates/gateway/app" in v["c01"].reason
    assert v["c02"].status == "proven" and "0.0.0-fake" in result.evidence["c02"].stdout_tail
    assert v["c03"].status == "proven" and f"http://127.0.0.1:{port}/health" in v["c03"].reason
    assert result.claims[2].setup == ["gateway-config"]
    assert v["c04"].status == "proven" and result.evidence["c04"].stdout_tail.strip().endswith("ok")
    # the sidecar's expects gives the wrong-token step the output the doc promises in prose
    assert result.claims[4].expects == "unauthorized"
    assert v["c05"].status == "proven" and "promised output found" in v["c05"].reason
    assert v["c06"].status == "proven" and '"data"' in result.evidence["c06"].stdout_tail
    # the untagged systemd snippet (NAME=value lines) is configuration: prose, never run
    assert result.claims[6].kind == "prose" and v["c07"].status == "unverifiable" and "c07" not in result.evidence
    assert result.fixtures and result.fixtures[0]["path"] == "gateway.toml" and len(result.fixtures[0]["sha256"]) == 64
    assert (result.out_dir / "fixtures.json").exists()
    assert (result.out_dir / "index.html").exists()
    assert result.doc.browser_claims is False
    # the service is gone once the session closed
    time.sleep(0.5)
    assert not port_open(port)


def test_substitute_flag_without_substitutes_fails_closed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    doc = tmp_path / "doc.md"
    doc.write_text("```bash\necho hi\n```\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no substitutes"):
        prove(str(doc), runner=StubBrowserRunner(), home=tmp_path / "home", allow_substitutes=True)


def test_cli_flags_pass_the_sidecar_and_substitute_through(tmp_path, monkeypatch):
    from attest.cli import app
    import attest.cli as cli_module

    port = free_port()
    doc, side = gateway_doc(tmp_path, port)
    env = fake_tools(tmp_path)
    monkeypatch.setenv("PATH", env["PATH"])
    monkeypatch.setattr(cli_module, "_runner", lambda *a, **k: StubBrowserRunner())
    monkeypatch.chdir(tmp_path)
    home = str(tmp_path / "home")
    plain = CliRunner().invoke(app, ["prove", str(doc), "--runner", "kane", "--home", home, "--cli-config", str(side)])
    assert plain.exit_code == 1, plain.output
    assert "BROKEN" in plain.output and "exit code 101" in plain.output
    allowed = CliRunner().invoke(app, ["prove", str(doc), "--runner", "kane", "--home", home, "--cli-config", str(side), "--substitute"])
    assert allowed.exit_code == 1, allowed.output  # c01 is stale, so the gate still fails
    assert "STALE" in allowed.output and "5/6 proven" in allowed.output and "1 stale" in allowed.output
    assert "via substitute `cargo install --path crates/gateway/app`" in allowed.output
    assert f"service ready, http://127.0.0.1:{port}/health answered 200" in allowed.output
    time.sleep(0.5)
    assert not port_open(port)


def test_sidecar_expects_only_points_at_a_promise_the_doc_makes(tmp_path):
    from attest.cli_config import ExpectSpec
    from attest.pipeline import annotate_expects
    from attest.ingest import ingest_text

    doc = ingest_text("A wrong token is rejected with error code `unauthorized`.\n\n```bash\ncurl -H x http://127.0.0.1:1/v1/models\n```\n")
    claim = Claim(id="c01", kind="shell", span=Span(start_line=3, end_line=3, text="x"), command="curl -H x http://127.0.0.1:1/v1/models")
    good = CliConfig(expects=(ExpectSpec(match="curl -H x", output="unauthorized"),))
    assert annotate_expects(doc, [claim], good)[0].expects == "unauthorized"
    invented = CliConfig(expects=(ExpectSpec(match="curl -H x", output="forbidden"),))
    with pytest.raises(ValueError, match="never says that"):
        annotate_expects(doc, [claim], invented)
    # a claim that already carries expects keeps it
    kept = claim.model_copy(update={"expects": "already"})
    assert annotate_expects(doc, [kept], good)[0].expects == "already"


# -- a book: several docs sharing one shell session ------------------------------------------------


def test_book_shares_the_service_a_first_doc_started(tmp_path, monkeypatch):
    from attest.cli import app
    import attest.cli as cli_module

    monkeypatch.chdir(tmp_path)
    port = free_port()
    _, side = gateway_doc(tmp_path, port)
    env = fake_tools(tmp_path)
    monkeypatch.setenv("PATH", env["PATH"])
    monkeypatch.setattr(cli_module, "_runner", lambda *a, **k: StubBrowserRunner())
    first = tmp_path / "01-start.md"
    first.write_text("# Start\n\n````\nrunvil-gateway --config gateway.toml --profile main\n````\n", encoding="utf-8")
    second = tmp_path / "02-check.md"
    second.write_text(
        f"# Check\n\nA wrong token is rejected with error code `unauthorized`.\n\n````\ncurl -H \"Authorization: Bearer wrong-token\" http://127.0.0.1:{port}/v1/models\n````\n",
        encoding="utf-8",
    )
    home = str(tmp_path / "home")
    book = CliRunner().invoke(app, ["prove", str(first), str(second), "--runner", "kane", "--home", home, "--cli-config", str(side)])
    assert book.exit_code == 0, book.output
    assert book.output.count("ATTEST  proving") == 2
    assert "service ready" in book.output and "promised output found" not in book.output or "PROVEN" in book.output
    assert "BOOK      2 docs | 2/2 proven" in book.output
    # the session closed with the book: the port is free again
    time.sleep(0.5)
    assert not port_open(port)
    # one page and one ledger turn per doc
    from attest.ledger import Ledger

    rows = Ledger(Path(home) / "ledger.jsonl").rows()
    assert sorted({r.turn for r in rows}) == [1, 2]
    # a book refuses the single-doc-only options
    refused = CliRunner().invoke(app, ["prove", str(first), str(second), "--runner", "kane", "--home", home, "--loop"])
    assert refused.exit_code == 2


# -- prompts: the Host context the doc describes, by frontmatter name ---------------------------------


def test_sidecar_prompts_give_a_prompt_claim_its_host_context(tmp_path):
    from attest.models import PromptContext
    from attest.pipeline import annotate_prompts, prompt_name

    side = tmp_path / "attest.cli.yaml"
    side.write_text(
        "prompts:\n"
        "  report:\n    files: { paper.md: the paper body }\n    input: '# Paper'\n    mode: ask\n    refuse: [secret.md]\n    execution: execute-test\n    script: [one, two]\n    args: rust\n"
        "  bare: {}\n",
        encoding="utf-8",
    )
    cfg = load_cli_config(side)
    spec = cfg.prompts["report"]
    assert spec.context == PromptContext(files={"paper.md": "the paper body"}, input="# Paper", mode="ask", refuse=["secret.md"], execution="execute-test", script=["one", "two"])
    assert spec.args == "rust"
    assert cfg.prompts["bare"].context == PromptContext() and cfg.prompts["bare"].args is None
    prompt = "---\nname: report\ndescription: d\nrunvil: 0\n---\n\n# Report\n\n## Main\n\n```lua\nreturn 1\n```\n"
    claim = Claim(id="c01", kind="prompt", span=Span(start_line=1, end_line=1, text="x"), command=prompt, args="doc args")
    other = claim.model_copy(update={"command": prompt.replace("name: report", "name: other")})
    assert prompt_name(claim) == "report" and prompt_name(other) == "other"
    annotated = annotate_prompts([claim, other], cfg)
    assert annotated[0].context == spec.context and annotated[0].args == "rust"
    assert annotated[1].context is None and annotated[1].args == "doc args"
    assert annotate_prompts([claim], CliConfig()) == [claim]


@pytest.mark.parametrize(
    "text,match",
    [
        ("prompts:\n  a:\n    mode: yolo\n", "mode"),
        ("prompts:\n  a:\n    files: { ../x: y }\n", "inside the scratch directory"),
        ("prompts:\n  a:\n    files: [x]\n", "files must map"),
        ("prompts:\n  a:\n    nope: 1\n", "nope"),
        ("prompts:\n  a:\n    args: ''\n", "non-empty string"),
        ("prompts: [a]\n", "mapping of prompt name"),
        ("prompts:\n  a: text\n", "needs a mapping"),
        ("placeholder_replies: sometimes\n", "true or false"),
    ],
)
def test_sidecar_prompts_are_validated(tmp_path, text, match):
    side = tmp_path / "attest.cli.yaml"
    side.write_text(text, encoding="utf-8")
    with pytest.raises(CliConfigError, match=match):
        load_cli_config(side)

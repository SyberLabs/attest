from typer.testing import CliRunner

from attest.cli import app
from attest.ingest import ingest
from attest.pipeline import out_dir_for
from attest.cli import _log
from attest.models import Claim, Evidence, Span, Verdict, BrowserObservation


def _prove(tmp_path, monkeypatch, body: str):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)  # fences only, no model
    monkeypatch.chdir(tmp_path)
    doc = tmp_path / "doc.md"
    doc.write_text(f"# Doc\n\n```bash\n{body}\n```\n", encoding="utf-8")
    return CliRunner().invoke(app, ["prove", str(doc), "--runner", "fake", "--home", str(tmp_path / "home")])


def test_prove_exits_zero_when_every_claim_holds(tmp_path, monkeypatch):
    result = _prove(tmp_path, monkeypatch, "echo hello")
    assert result.exit_code == 0, result.output
    assert "1 proven" in result.output


def test_prove_exits_nonzero_when_a_step_is_broken(tmp_path, monkeypatch):
    # a doc that lies fails the command, so CI can gate a merge on it
    result = _prove(tmp_path, monkeypatch, "exit 3")
    assert result.exit_code == 1, result.output
    assert "1 broken" in result.output


def test_cli_serve_doc_selects_its_rendered_page_offline(tmp_path, monkeypatch):
    doc = tmp_path / "chosen.md"
    doc.write_text("# Chosen\n", encoding="utf-8")
    home = tmp_path / "home"
    directory = out_dir_for(ingest(str(doc)), home)
    directory.mkdir(parents=True)
    (directory / "index.html").write_text("<h1>Chosen</h1>", encoding="utf-8")
    selected = {}

    class FakeServer:
        allow_reuse_address = False

        def __init__(self, address, handler):
            selected["handler"] = handler

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def serve_forever(self):
            return None

    monkeypatch.setattr("attest.cli.socketserver.TCPServer", FakeServer)
    result = CliRunner().invoke(app, ["serve", "--doc", str(doc), "--home", str(home)])

    assert result.exit_code == 0, result.output
    assert selected["handler"].keywords["directory"] == str(directory)


def test_cli_serve_doc_errors_until_proven(tmp_path):
    doc = tmp_path / "unproven.md"
    doc.write_text("# Unproven\n", encoding="utf-8")
    result = CliRunner().invoke(app, ["serve", "--doc", str(doc), "--home", str(tmp_path / "home")])

    assert result.exit_code == 1
    assert "document has not been proven yet" in result.output


def test_live_serve_writes_single_tab_page(tmp_path, monkeypatch):
    class FakeServer:
        allow_reuse_address = False
        def __init__(self, address, handler): self.handler = handler
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def serve_forever(self): return None
    monkeypatch.setattr("attest.cli.socketserver.TCPServer", FakeServer)
    r = CliRunner().invoke(app, ["serve", "--live", "--home", str(tmp_path)])
    assert r.exit_code == 0, r.output
    page = (tmp_path / "index.html").read_text()
    assert '<iframe id="page">' in page and 'fetch("latest.json",{cache:"no-store"})' in page


def test_claim_narration_uses_observed_value(capsys):
    claim = Claim(id="c01", kind="browser", span=Span(start_line=35, end_line=35, text="check"), expects="Hello, World!", objective="Check greeting")
    ev = Evidence(claim_id="c01", kind="browser", observation=BrowserObservation(status="passed", observed={"label": "Hello, World!"}))
    _log(claim, Verdict(claim_id="c01", status="proven", reason="ok", evidence=ev))
    output = capsys.readouterr().out
    assert "PROVEN" in output and 'line 35  Kane saw "Hello, World!"' in output


def test_claim_narration_formats_stale_and_broken(capsys):
    browser = Claim(id="c01", kind="browser", span=Span(start_line=37, end_line=37, text="check"), expects="Vite + React", objective="Check heading")
    stale_ev = Evidence(claim_id="c01", kind="browser", observation=BrowserObservation(status="passed", observed={"heading": "Get started"}))
    _log(browser, Verdict(claim_id="c01", status="stale", reason="stale", evidence=stale_ev))
    stale = capsys.readouterr().out
    assert "STALE" in stale and 'doc says "Vite + React", page says "Get started"' in stale
    shell = Claim(id="c02", kind="shell", span=Span(start_line=32, end_line=32, text="exit 1"), command="exit 1")
    _log(shell, Verdict(claim_id="c02", status="broken", reason="failed", evidence=Evidence(claim_id="c02", kind="shell", exit_code=1)))
    broken = capsys.readouterr().out
    assert "BROKEN" in broken and "line 32  exit code 1" in broken

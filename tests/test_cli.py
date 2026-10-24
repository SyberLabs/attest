from typer.testing import CliRunner

from attest.cli import app


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

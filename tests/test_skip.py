from typer.testing import CliRunner

from attest.cli import app
from attest.models import Claim, Span
from attest.pipeline import prove
from attest.runner import FakeBrowserRunner


def _claims():
    return [
        Claim(
            id=claim_id,
            kind="browser",
            span=Span(start_line=index, end_line=index, text=objective),
            objective=objective,
            expects=objective,
            store_as=f"{claim_id}_label",
            depends_on=depends_on,
        )
        for index, (claim_id, objective, depends_on) in enumerate(
            [("c01", "Sign in", []), ("c02", "Open account", ["c01"]), ("c03", "Finish", [])],
            start=1,
        )
    ]


class RecordingRunner(FakeBrowserRunner):
    def __init__(self):
        super().__init__()
        self.prepared = None

    def prepare(self, claims, source="doc"):
        self.prepared = [claim.id for claim in claims]


def _compile(monkeypatch):
    import attest.pipeline as pipeline

    monkeypatch.setattr(pipeline, "compile_doc", lambda doc, llm=None: _claims())


def test_skip_excludes_claim_from_prepare_and_execution_and_dependent_runs(tmp_path, monkeypatch):
    _compile(monkeypatch)
    doc = tmp_path / "card.md"
    doc.write_text("Card\n", encoding="utf-8")
    runner = RecordingRunner()

    result = prove(str(doc), runner=runner, home=tmp_path / "home", skip={"c01"})

    assert runner.prepared == ["c02", "c03"]
    assert [claim_id for claim_id, _ in runner.calls] == ["c02", "c03"]
    skipped = result.by_id["c01"]
    assert skipped.status == "unverifiable"
    assert skipped.reason == "unverifiable: not applicable on this run (skipped with --skip)"
    assert skipped.evidence is None
    assert "c01" not in result.evidence
    assert result.by_id["c02"].status == "unverifiable"
    assert "simulation" in result.by_id["c02"].reason


def test_skip_with_stop_before_only_prepares_remaining_prefix(tmp_path, monkeypatch):
    _compile(monkeypatch)
    doc = tmp_path / "card.md"
    doc.write_text("Card\n", encoding="utf-8")
    runner = RecordingRunner()

    result = prove(str(doc), runner=runner, home=tmp_path / "home", skip={"c01"}, stop_before="c03")

    assert runner.prepared == ["c02"]
    assert [claim_id for claim_id, _ in runner.calls] == ["c02"]
    assert result.by_id["c01"].reason == "unverifiable: not applicable on this run (skipped with --skip)"
    assert result.by_id["c03"].reason.startswith("unverifiable: stopped before c03")


def test_cli_accepts_repeated_and_comma_separated_skip_ids_and_rejects_unknown(tmp_path, monkeypatch):
    _compile(monkeypatch)
    doc = tmp_path / "card.md"
    doc.write_text("Card\n", encoding="utf-8")
    args = ["prove", str(doc), "--runner", "fake", "--skip", "c01,c03", "--skip", "c02", "--home", str(tmp_path / "home")]

    result = CliRunner().invoke(app, args)

    assert result.exit_code == 0
    assert "3 claims" in result.output

    unknown = CliRunner().invoke(app, ["prove", str(doc), "--runner", "fake", "--skip", "c99", "--home", str(tmp_path / "other-home")])
    assert unknown.exit_code == 2
    assert "unknown claim id(s) 'c99'" in unknown.output
    assert "valid ids: c01, c02, c03" in unknown.output

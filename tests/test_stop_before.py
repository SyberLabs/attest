from typer.testing import CliRunner

from attest.cli import app
from attest.models import Claim, Span
from attest.pipeline import prove
from attest.runner import FakeBrowserRunner


def _claims():
    return [
        Claim(
            id=f"c{i:02}",
            kind="browser",
            span=Span(start_line=i, end_line=i, text=f"Step {i}"),
            objective=f"Step {i}",
            expects=f"Label {i}",
            store_as=f"c{i:02}_label",
            depends_on=[],
        )
        for i in range(1, 4)
    ]


class RecordingRunner(FakeBrowserRunner):
    def __init__(self):
        super().__init__()
        self.prepared = None

    def prepare(self, claims, source="doc"):
        self.prepared = [claim.id for claim in claims]


def test_stop_before_excludes_suffix_from_prepare_and_observe(tmp_path, monkeypatch):
    import attest.pipeline as pipeline

    doc = tmp_path / "card.md"
    doc.write_text("Card\n", encoding="utf-8")
    monkeypatch.setattr(pipeline, "compile_doc", lambda doc, llm=None: _claims())
    runner = RecordingRunner()

    result = prove(str(doc), runner=runner, home=tmp_path / "home", stop_before="c02")

    assert runner.prepared == ["c01"]
    assert [claim_id for claim_id, _ in runner.calls] == ["c01"]
    assert result.by_id["c01"].status == "proven"
    reason = "unverifiable: stopped before c02 (card stops before irreversible or real-form steps)"
    for claim_id in ("c02", "c03"):
        assert result.by_id[claim_id].status == "unverifiable"
        assert result.by_id[claim_id].reason == reason
        assert result.by_id[claim_id].evidence is None
    assert result.failing() == []


def test_unknown_stop_before_id_is_a_usage_error_with_valid_ids(tmp_path, monkeypatch):
    import attest.pipeline as pipeline

    doc = tmp_path / "card.md"
    doc.write_text("Card\n", encoding="utf-8")
    monkeypatch.setattr(pipeline, "compile_doc", lambda doc, llm=None: _claims())

    result = CliRunner().invoke(app, ["prove", str(doc), "--stop-before", "c99", "--home", str(tmp_path / "home")])

    assert result.exit_code == 2
    assert "unknown claim id 'c99'" in result.output
    assert "valid ids: c01, c02, c03" in result.output

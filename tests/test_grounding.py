import json
from pathlib import Path

from attest.grounding import (
    card_for,
    githits_json,
    ground,
    ground_doc,
    ground_githits,
    ground_help,
    hit_lines,
    repo_for,
)
from attest.ingest import ingest, ingest_text
from attest.models import Claim, Span

ROOT = Path(__file__).resolve().parents[1]
FLASK = ROOT / "demo" / "flask.md"


def browser_claim(expects="Hello, World!", line=35, text="Head over to the page and see Hello, World!"):
    return Claim(id="c07", kind="browser", span=Span(start_line=line, end_line=line, text=text), objective="open", expects=expects, store_as="c07_label")


class FakeRun:
    """Stands in for the GitHits subprocess. Records calls and returns canned output."""

    def __init__(self, payload=None, code=0, stderr=""):
        self.payload = payload
        self.code = code
        self.stderr = stderr
        self.calls = []

    def __call__(self, args, timeout_s):
        self.calls.append(list(args))
        out = json.dumps(self.payload) if self.payload is not None else ""
        return self.code, out, self.stderr


SAMPLE = {
    "hits": [
        {"path": "docs/quickstart.rst", "line": 42, "text": "you should see Hello, World!", "score": 0.9},
        {"url": "https://flask.palletsprojects.com/quickstart/", "snippet": "A minimal application"},
    ]
}


def test_githits_json_caches_by_args(tmp_path):
    run = FakeRun(SAMPLE)
    rec = githits_json(["search", "x", "--in", "github:a/b"], cache_dir=tmp_path, runner=run)
    assert rec["exit_code"] == 0 and rec["data"] == SAMPLE and rec["error"] is None
    assert rec["args"][-1] == "--json"
    again = githits_json(["search", "x", "--in", "github:a/b"], cache_dir=tmp_path, runner=run)
    assert again == rec
    assert len(run.calls) == 1  # second call came from disk
    assert len(list(tmp_path.glob("*.json"))) == 1


def test_githits_json_records_auth_error_without_raising(tmp_path):
    run = FakeRun({"error": "No local GitHits authentication token found.", "code": "AUTH_REQUIRED"}, code=1)
    rec = githits_json(["grep", "-F", "x", "github:a/b"], cache_dir=tmp_path, runner=run)
    assert rec["error"].startswith("AUTH_REQUIRED")
    lines = ground_githits(browser_claim(), "github:a/b", cache_dir=tmp_path, runner=run)
    assert all("unavailable: AUTH_REQUIRED" in ln for ln in lines)
    assert len(lines) == 2  # search and grep; no package given


def test_githits_json_handles_timeout_and_garbage(tmp_path):
    rec = githits_json(["search", "x"], cache_dir=tmp_path, runner=lambda a, t: (124, "", "timed out"))
    assert rec["error"] == "timed out" and rec["exit_code"] == 124
    rec = githits_json(["search", "y"], cache_dir=tmp_path, runner=lambda a, t: (0, "not json", ""))
    assert rec["error"] == "not json"


def test_hit_lines_flattens_payload():
    lines = hit_lines(SAMPLE, "githits search")
    assert lines[0] == "[githits search docs/quickstart.rst:42] you should see Hello, World!"
    assert lines[1].startswith("[githits search https://flask.palletsprojects.com/quickstart/] A minimal application")
    assert hit_lines({"results": []}, "x") == []


def test_ground_githits_uses_documented_commands(tmp_path):
    run = FakeRun(SAMPLE)
    lines = ground_githits(browser_claim(), "github:pallets/flask@abc", package="pypi:flask", cache_dir=tmp_path, runner=run)
    assert run.calls[0][:6] == ["search", "Hello, World!", "--in", "github:pallets/flask@abc", "--source", "docs"]
    assert run.calls[1][:7] == ["grep", "-F", "Hello, World!", "github:pallets/flask@abc", "--corpus", "all", "--limit"]
    assert run.calls[2][:5] == ["pkg", "changelog", "pypi:flask", "--changes", "important"]
    assert all(a[-1] == "--json" for a in run.calls)
    assert any("[githits changelog" in ln for ln in lines)


def test_ground_doc_gives_numbered_section():
    doc = ingest(str(FLASK))
    lines = ground_doc(doc, browser_claim())
    assert any(ln.startswith("[doc flask.md:35] ") for ln in lines)
    assert any("flask --app hello run" in ln for ln in lines)


def test_ground_help_offline_from_cache():
    claim = browser_claim(expects="secret scanning", text="Under Code security, enable secret scanning.")
    lines = ground_help(claim, "github-secret-scanning", help_dir=ROOT / "cache" / "help")
    assert lines and lines[0].startswith("[help https://docs.github.com/")
    assert any("Secret Protection" in ln or "secret scanning" in ln.lower() for ln in lines)


def test_ground_help_missing_card(tmp_path):
    lines = ground_help(browser_claim(), "nope", help_dir=tmp_path)
    assert lines == [f"[help nope] no cached help page at {tmp_path / 'nope.md'}"]


def test_card_and_repo_detection():
    doc = ingest_text("---\ncard: github-secret-scanning\n---\n# x\n", source="demo/answers/github-secret-scanning.md")
    assert card_for(doc) == "github-secret-scanning"
    assert card_for(ingest(str(FLASK))) is None
    assert repo_for(ingest(str(FLASK)), ROOT / "targets.yaml") == "github:pallets/flask@9b74a90dd3c47f792734823e8793ac36f38bc4dd"
    assert repo_for(ingest_text("x", source="nothing.md"), ROOT / "targets.yaml") is None


def test_ground_failed_claim_returns_source_refs_offline(tmp_path, monkeypatch):
    """Acceptance: a sample failed claim gets at least one line with a source ref, with no network."""
    monkeypatch.chdir(ROOT)
    doc = ingest(str(FLASK))
    claim = browser_claim()
    run = FakeRun({"error": "No local GitHits authentication token found.", "code": "AUTH_REQUIRED"}, code=1)
    lines = ground(doc, claim, runner=run, cache_dir=tmp_path)
    assert any(ln.startswith("[doc flask.md:") for ln in lines)
    assert any(ln.startswith("[githits search]") for ln in lines)
    # AI-answer doc: help page grounding kicks in
    answer = ingest_text(
        "---\ncard: github-secret-scanning\n---\n# How do I enable secret scanning?\n1. Open Settings, then Code security, and turn on secret scanning.\n",
        source="demo/answers/github-secret-scanning.md",
    )
    c = Claim(id="c02", kind="browser", span=Span(start_line=5, end_line=5, text=answer.lines[4]), objective="o", expects="secret scanning", store_as="c02_label")
    lines = ground(answer, c, runner=run, cache_dir=tmp_path)
    assert any(ln.startswith("[help https://docs.github.com/") for ln in lines)

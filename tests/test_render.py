import json
import os
import sys
from pathlib import Path

import pytest

from typer.testing import CliRunner

from attest.cli import app
from attest.ingest import ingest, ingest_text
from attest.models import Brief, BrowserObservation, Claim, Evidence, Span, Verdict
from attest.pipeline import prove
from attest.render import attach_claims, badge_svg, blocks, inline, proof_md, render_page
from attest.runner import FakeBrowserRunner

ROOT = Path(__file__).resolve().parents[1]
FLASK = ROOT / "demo" / "flask.md"


class KaneFixtureRunner:
    name = "kane"

    def __init__(self, observed=None):
        self.observed = observed
        self.calls = []

    def observe(self, claim, base_url):
        self.calls.append((claim.id, base_url))
        key = claim.store_as or f"{claim.id}_label"
        return BrowserObservation(status="passed", observed={key: self.observed if self.observed is not None else claim.expects or ""})

    def close(self):
        pass


def venv_env():
    """PATH with the test interpreter's scripts first so `flask` resolves in the runner."""
    scripts = Path(sys.executable).parent
    return {"PATH": f"{scripts}{os.pathsep}{os.environ['PATH']}"}


# -- markdown ---------------------------------------------------------------------------


def test_inline_markdown():
    assert inline("use `x < y` and **bold** and [a](http://e.com)") == (
        'use <code>x &lt; y</code> and <strong>bold</strong> and <a href="http://e.com">a</a>'
    )
    assert inline("see http://127.0.0.1:5000/ now") == 'see <a href="http://127.0.0.1:5000/">http://127.0.0.1:5000/</a> now'
    assert "<script>" not in inline("<script>alert(1)</script>")


def test_inline_rejects_unsafe_urls_and_escapes_attribute_quotes():
    assert inline("[run](javascript:alert(1))") == "run"
    assert inline('[site](https://example.com/"x)') == (
        '<a href="https://example.com/&quot;x">site</a>'
    )
    assert inline("![pixel](data:image/png;base64,AAAA)") == "pixel"


def test_blocks_keep_line_ranges():
    doc = ingest(str(FLASK))
    blks = blocks(doc.lines)
    kinds = [b.kind for b in blks]
    assert kinds[0] == "heading" and blks[0].start_line == 1
    code = [b for b in blks if b.kind == "code"]
    assert len(code) == 3
    # code block ranges include the fences
    f = doc.fences[0]
    assert code[0].start_line == f.start_line - 1 and code[0].end_line == f.end_line + 1
    assert "pip install Flask" in code[0].html
    # every non-blank line is covered by exactly one block
    covered = set()
    for b in blks:
        for n in range(b.start_line, b.end_line + 1):
            assert n not in covered, n
            covered.add(n)
    for n, ln in enumerate(doc.lines, 1):
        if ln.strip():
            assert n in covered


def test_blocks_lists_tables_quotes():
    lines = ingest_text("# T\n\n- one\n- two\n  more\n\n1. a\n2. b\n\n> quoted\n\n| h1 | h2 |\n| --- | --- |\n| a | b |\n\n---\n").lines
    kinds = [b.kind for b in blocks(lines)]
    assert kinds == ["heading", "list", "list", "quote", "table", "hr"]


def test_attach_claims_puts_badge_on_first_touching_block():
    doc = ingest(str(FLASK))
    f = doc.fences[0]
    c = Claim(id="c01", kind="shell", span=Span(start_line=f.start_line, end_line=f.end_line, text=f.text), command=f.text)
    p = Claim(id="c02", kind="prose", span=Span(start_line=1, end_line=1, text=doc.lines[0]))
    blks = attach_claims(blocks(doc.lines), [c, p])
    hit = [b for b in blks if b.claims]
    assert len(hit) == 1 and hit[0].kind == "code" and hit[0].claims[0].id == "c01"


# -- page --------------------------------------------------------------------------------


def test_render_page_contains_badges_reasons_drawers_and_strip_numbers():
    doc = ingest(str(FLASK))
    f = doc.fences[2]
    c1 = Claim(id="c01", kind="shell", span=Span(start_line=f.start_line, end_line=f.end_line, text=f.text), command=f.text)
    c2 = Claim(id="c02", kind="browser", span=Span(start_line=35, end_line=35, text=doc.lines[34]), objective="Open it", expects="Hello, World!", store_as="c02_label", depends_on=["c01"])
    ev1 = Evidence(claim_id="c01", kind="shell", command=f.text, exit_code=0, stdout_tail="Running on http://127.0.0.1:5000", detected_url="http://127.0.0.1:5000")
    pack_path = r"C:\Users\MATEO\AppData\Local\Temp\session-long\X.evidence"
    ev2 = Evidence(claim_id="c02", kind="browser", observation=BrowserObservation(status="passed", observed={"c02_label": "Hello, Flask!"}, screenshot="shot.png", note=f"passed check | pack: {pack_path}"))
    v1 = Verdict(claim_id="c01", status="proven", reason="proven: exit 0", evidence=ev1)
    v2 = Verdict(claim_id="c02", status="stale", reason="stale: label_match 0.67", evidence=ev2, label_match=0.67)
    strip = {"claims": 2, "proven": 1, "stale": 1, "broken": 0, "unverifiable": 0, "skipped": 0, "turn": 7, "loops_attempted": 1, "loops_closed": 0}
    brief = Brief(claim_id="c02", verdict=v2, doc_excerpt="35  Head over...", grounding=["[docs] says Hello", "[githits search] unavailable: AUTH_REQUIRED: login"], patch="--- a/x\n+++ b/x\n-old\n+new", attempts=1)
    html = render_page(doc, [c1, c2], [v1, v2], strip, briefs={"c02": brief}, prior_verdicts={"c02": v2}, runner_name="fake")
    assert 'id="ledger-strip"' in html
    assert 'class="badge proven"' in html and 'class="badge stale"' in html
    assert "proven: exit 0" in html and "stale: label_match 0.67" in html
    assert 'id="drawer-c01"' in html and 'id="drawer-c02"' in html
    assert "Running on http://127.0.0.1:5000" in html  # stdout tail
    assert '<a href="shot.png" target="_blank" rel="noopener"><img src="shot.png"' in html
    assert "passed check | pack X" in html
    assert f"kane-cli evidence serve {pack_path}" in html
    assert "AUTH_REQUIRED" not in html
    assert "Hello, Flask!" in html and "Hello, World!" in html  # observed vs expects
    assert "[docs] says Hello" in html and "+new" in html  # brief and diff
    assert "turn 7" in html and "runner fake" in html
    html_with_kane = render_page(doc, [c1, c2], [v1, v2], {**strip, "kane_steps": 5, "authored": 2, "replayed": 3}, runner_name="fake")
    assert "5</span> Kane steps &middot; 2 authored, 3 replayed" in html_with_kane
    assert chr(0xC2) not in html_with_kane  # no mojibake in the strip
    html_without_kane = render_page(doc, [c1, c2], [v1, v2], {**strip, "kane_steps": 0, "authored": 0, "replayed": 0}, runner_name="fake")
    assert "Kane steps" not in html_without_kane
    # strip numbers are the ones passed in, never recounted
    for key, n in (("proven", 1), ("claims", 2), ("stale", 1), ("broken", 0)):
        assert f'data-count="{key}">{n}<' in html, key
    assert "<script>alert" not in html


def test_live_render_has_pending_badge_and_reload_banner():
    doc = ingest(str(FLASK))
    claim = Claim(id="c99", kind="browser", span=Span(start_line=35, end_line=35, text=doc.lines[34]), objective="Open it")
    strip = {"claims": 1, "proven": 0, "stale": 0, "broken": 0, "unverifiable": 0, "skipped": 0, "turn": 0, "loops_attempted": 0, "loops_closed": 0}
    html = render_page(doc, [claim], [], strip, live=True, now="Kane is checking line 35")
    assert 'class="badge none"' in html
    assert "Kane is checking line 35" in html
    assert 'fetch("../latest.json", {cache: "no-store"})' in html


def test_live_prove_writes_latest_lifecycle(tmp_path):
    result = prove(str(FLASK), runner=KaneFixtureRunner(), home=tmp_path, shell_env=venv_env(), live=True)
    latest = json.loads((tmp_path / "latest.json").read_text())
    assert latest["running"] is False
    assert latest["page"] == f"{result.out_dir.name}/index.html"
    assert latest["now"] == "Proof complete"
    assert '"running": false' in (tmp_path / "latest.json").read_text()


def test_render_hides_grounding_heading_when_all_lines_are_unavailable():
    doc = ingest(str(FLASK))
    f = doc.fences[2]
    claim = Claim(id="c01", kind="shell", span=Span(start_line=f.start_line, end_line=f.end_line, text=f.text), command=f.text)
    evidence = Evidence(claim_id="c01", kind="shell", command=f.text, exit_code=1)
    verdict = Verdict(claim_id="c01", status="broken", reason="failed", evidence=evidence)
    brief = Brief(claim_id="c01", verdict=verdict, doc_excerpt="excerpt", grounding=["[githits search] unavailable: AUTH_REQUIRED: login"])
    html = render_page(doc, [claim], [verdict], {"claims": 1, "proven": 0, "stale": 0, "broken": 1, "unverifiable": 0, "skipped": 0, "turn": 1, "loops_attempted": 0, "loops_closed": 0}, briefs={"c01": brief})
    assert "AUTH_REQUIRED" not in html
    assert "Grounding" not in html


def test_badge_svg_and_proof_md():
    svg = badge_svg(11, 12)
    assert svg.startswith("<svg") and "11/12 proven" in svg
    assert "#d9a400" in svg  # amber when not all proven
    assert "#2e8b57" in badge_svg(3, 3)
    assert "#8a8a8a" in badge_svg(0, 0)
    doc = ingest(str(FLASK))
    c = Claim(id="c01", kind="shell", span=Span(start_line=12, end_line=12, text="pip install Flask"), command="pip install Flask")
    v = Verdict(claim_id="c01", status="proven", reason="proven: exit 0 | ok", evidence=Evidence(claim_id="c01", kind="shell", exit_code=0))
    md = proof_md(doc, [c], [v], {"claims": 1, "proven": 1, "stale": 0, "broken": 0, "unverifiable": 0, "skipped": 0, "turn": 1}, "fake")
    assert "| c01 | 12-12 | shell | proven | proven: exit 0 \\| ok |" in md
    assert doc.sha256 in md


# -- pipeline and CLI ------------------------------------------------------------------------


def test_prove_flask_with_fake_runner_is_simulation_only(tmp_path):
    runner = FakeBrowserRunner()
    result = prove(str(FLASK), runner=runner, home=tmp_path, shell_env=venv_env(), simulation=True)
    statuses = {c.id: result.by_id[c.id].status for c in result.claims}
    shell = [c for c in result.claims if c.kind == "shell"]
    browser = [c for c in result.claims if c.kind == "browser"]
    assert len(shell) == 3 and len(browser) == 1
    assert all(statuses[c.id] == "unverifiable" for c in result.claims), statuses
    assert result.simulation is True
    assert runner.calls == []  # failed prerequisites do not reach fake browser evidence
    assert result.page and result.page.exists()
    html = result.page.read_text(encoding="utf-8")
    assert "Flask Quickstart" in html
    assert 'class="badge proven"' not in html
    assert 'class="badge unverifiable"' in html
    # the ledger has one row per claim and the strip reads from it
    rows = (tmp_path / "ledger.jsonl").read_text().strip().splitlines()
    assert len(rows) == len(result.claims)
    assert (tmp_path / "latest").read_text().strip() == str(result.out_dir.resolve())
    assert (result.out_dir / "claims.json").exists()
    import socket

    with socket.socket() as s:
        s.settimeout(0.5)
        assert s.connect_ex(("127.0.0.1", 5000)) != 0  # server was closed


def test_prove_with_scripted_stale_observation(tmp_path):
    doc = ingest(str(FLASK))
    runner = KaneFixtureRunner("Hello, Flask!")
    result = prove(str(FLASK), runner=runner, home=tmp_path, shell_env=venv_env())
    b = next(c for c in result.claims if c.kind == "browser")
    assert result.by_id[b.id].status == "stale"
    assert result.failing() == [b]


def test_pipeline_rejects_fake_runner_without_simulation(tmp_path):
    with pytest.raises(ValueError, match="simulation=True"):
        prove(str(FLASK), runner=FakeBrowserRunner(), home=tmp_path)


def test_cli_prove_serve_badge(tmp_path, monkeypatch):
    for k, v in venv_env().items():
        monkeypatch.setenv(k, v)
    cli = CliRunner()
    import attest.cli as cli_module

    monkeypatch.setattr(cli_module, "_runner", lambda *args, **kwargs: KaneFixtureRunner())
    r = cli.invoke(app, ["prove", str(FLASK), "--runner", "kane", "--home", str(tmp_path)])
    assert r.exit_code == 0, r.output
    assert "runner: kane" in r.output
    assert "proven" in r.output and "0 broken" in r.output
    assert "page " in r.output and "index.html" in r.output
    r = cli.invoke(app, ["badge", "--home", str(tmp_path), "--out", str(tmp_path / "exp")])
    assert r.exit_code == 0, r.output
    assert (tmp_path / "exp" / "docs-proven.svg").exists()
    proof = (tmp_path / "exp" / "PROOF.md").read_text()
    assert "proven" in proof and "| c" in proof
    r = cli.invoke(app, ["--version"])
    assert r.exit_code == 0 and "attest" in r.output


def test_cli_serve_without_pages_errors(tmp_path):
    r = CliRunner().invoke(app, ["serve", "--home", str(tmp_path)])
    assert r.exit_code == 1


def test_looped_claim_shows_before_after_and_word_level_diff():
    doc = ingest(str(FLASK))
    claim = Claim(id="c02", kind="browser", span=Span(start_line=35, end_line=35, text=doc.lines[34]), objective="Open it", expects="Get started")
    seen = Evidence(claim_id="c02", kind="browser", observation=BrowserObservation(status="passed", observed={"c02_label": "Get started"}))
    before = Verdict(claim_id="c02", status="stale", reason="stale: label_match 0.18", evidence=seen, label_match=0.18)
    after = Verdict(claim_id="c02", status="proven", reason="proven (after loop)", evidence=seen, label_match=1.0)
    patch = '--- a/doc.md\n+++ b/doc.md\n@@ -35 +35 @@\n-The page shows the heading "Vite + React"\n+The page shows the heading "Get started"\n'
    brief = Brief(claim_id="c02", verdict=before, doc_excerpt="35  ...", patch=patch, attempts=1)
    strip = {"claims": 1, "proven": 1, "stale": 0, "broken": 0, "unverifiable": 0, "skipped": 0, "turn": 2, "loops_attempted": 1, "loops_closed": 1}
    html = render_page(doc, [claim], [after], strip, briefs={"c02": brief}, prior_verdicts={"c02": before})
    assert 'class="drawer looped open" id="drawer-c02"' in html  # the fix opens itself
    assert "Caught and fixed" in html and "data-fixed" in html
    assert '<span class="v">Vite + React</span>' in html and '<span class="v">Get started</span>' in html
    assert '<mark>Vite + React</mark>' in html and '<mark>Get started</mark>' in html
    assert "1/1</span> fixed by agent" in html
    assert "location.reload" not in html  # a finished page never reloads

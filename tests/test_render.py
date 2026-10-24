import base64
import hashlib
import json
import os
import sys
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from typer.testing import CliRunner

from attest.cli import _QuietHandler, app
from attest.ingest import ingest, ingest_text
from attest.models import Brief, BrowserObservation, Claim, Evidence, Span, Verdict
from attest.pipeline import prove
from attest.render import attach_claims, badge_svg, blocks, content_security_policy, inline, proof_md, render_page
from attest.runner import FakeBrowserRunner

ROOT = Path(__file__).resolve().parents[1]
FLASK = ROOT / "demo" / "flask.md"
PNG_1PX = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jXioAAAAASUVORK5CYII=")


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


def test_inline_hostile_markdown_cannot_create_unsafe_elements_or_targets():
    rendered = inline(
        'https://example.test/\"><img/src=x onerror=alert(1)> '
        '[run](javascript:alert(1)) ![payload](data:text/html,boom) '
        '![tracker](https://tracker.test/pixel.png) '
        '[quoted](https://safe.test/\"onmouseover=\"alert(1))'
    )

    assert "<img" not in rendered
    assert "<script" not in rendered
    assert 'href="javascript:' not in rendered
    assert 'src="data:' not in rendered
    assert "&quot;" in rendered
    assert ' onerror="' not in rendered and ' onmouseover="' not in rendered


def test_review_page_shows_gaps_unresolved_authority_and_distinct_identities():
    before = ingest_text("# Before\n", source="review.md")
    after = before.model_copy(update={"lines": ["# After"]})
    claim = Claim(id="c01", kind="shell", span=Span(start_line=1, end_line=1, text="# After"), command="echo after")
    before_verdict = Verdict(
        claim_id="c01",
        status="broken",
        reason="protected contract refusal",
        evidence=Evidence(claim_id="c01", kind="shell", exit_code=1),
    )
    current = Verdict(claim_id="c01", status="unverifiable", reason="protected contract refusal unresolved")
    brief = Brief(claim_id="c01", verdict=before_verdict, doc_excerpt="# Before", patch="--- a/review.md\n+++ b/review.md\n-# Before\n+# After")
    strip = {"claims": 1, "proven": 0, "stale": 0, "broken": 0, "unverifiable": 1, "skipped": 0, "turn": 1, "loops_attempted": 0, "loops_closed": 0}

    page = render_page(after, [claim], [current], strip, briefs={"c01": brief}, prior_verdicts={"c01": before_verdict})

    before_identity = before.sha256
    after_identity = hashlib.sha256(b"# After\n").hexdigest()
    assert before_identity != after_identity
    assert f"Before</strong> <code>{before_identity}</code>" in page
    assert f"After</strong> <code>{after_identity}</code>" in page
    assert "c01 · unverifiable" in page
    assert "Unresolved" in page and "protected-contract refusals remain unresolved" in page
    assert "Unapproved proposal; not published." in page
    assert "green result" in page


def test_screenshot_artifacts_reject_paths_outside_attest_home(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    outside = tmp_path / "outside.png"
    outside.write_bytes(PNG_1PX)
    doc = ingest_text("# Evidence\n", source="evidence.md")
    claim = Claim(id="c01", kind="browser", span=Span(start_line=1, end_line=1, text="# Evidence"), objective="open")
    evidence = Evidence(claim_id="c01", kind="browser", observation=BrowserObservation(status="passed", screenshot=str(outside)))
    verdict = Verdict(claim_id="c01", status="proven", reason="observed", evidence=evidence)
    strip = {"claims": 1, "proven": 1, "stale": 0, "broken": 0, "unverifiable": 0, "skipped": 0, "turn": 1, "loops_attempted": 0, "loops_closed": 0}

    page = render_page(doc, [claim], [verdict], strip, artifact_dir=home / "doc" / "artifacts")

    assert "/artifacts/" not in page
    assert not (home / "doc" / "artifacts").exists()


def test_review_page_csp_uses_hashes_and_denies_external_defaults():
    policy = content_security_policy('<style>body{color:red}</style><script>run()</script>')
    assert "default-src 'none'" in policy
    assert "style-src 'sha256-" in policy and "script-src 'sha256-" in policy
    assert "img-src 'self'" in policy
    assert "object-src 'none'" in policy and "base-uri 'none'" in policy
    assert "unsafe-inline" not in policy


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


def test_render_page_contains_badges_reasons_drawers_and_strip_numbers(tmp_path):
    doc = ingest(str(FLASK))
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    screenshot = evidence_dir / "shot.png"
    screenshot.write_bytes(PNG_1PX)
    f = doc.fences[2]
    c1 = Claim(id="c01", kind="shell", span=Span(start_line=f.start_line, end_line=f.end_line, text=f.text), command=f.text)
    c2 = Claim(id="c02", kind="browser", span=Span(start_line=35, end_line=35, text=doc.lines[34]), objective="Open it", expects="Hello, World!", store_as="c02_label", depends_on=["c01"])
    ev1 = Evidence(claim_id="c01", kind="shell", command=f.text, exit_code=0, stdout_tail="Running on http://127.0.0.1:5000", detected_url="http://127.0.0.1:5000")
    ev2 = Evidence(claim_id="c02", kind="browser", observation=BrowserObservation(status="passed", observed={"c02_label": "Hello, Flask!"}, screenshot=str(screenshot)))
    v1 = Verdict(claim_id="c01", status="proven", reason="proven: exit 0", evidence=ev1)
    v2 = Verdict(claim_id="c02", status="stale", reason="stale: label_match 0.67", evidence=ev2, label_match=0.67)
    strip = {"claims": 2, "proven": 1, "stale": 1, "broken": 0, "unverifiable": 0, "skipped": 0, "turn": 7, "loops_attempted": 1, "loops_closed": 0}
    brief = Brief(claim_id="c02", verdict=v2, doc_excerpt="35  Head over...", grounding=["[docs] says Hello"], patch="--- a/x\n+++ b/x\n-old\n+new", attempts=1)
    artifact_dir = tmp_path / "review" / "artifacts"
    html = render_page(doc, [c1, c2], [v1, v2], strip, briefs={"c02": brief}, prior_verdicts={"c02": v2}, runner_name="fake", artifact_dir=artifact_dir)
    assert 'id="ledger-strip"' in html and ">Ledger<" in html
    assert 'class="badge proven"' in html and 'class="badge stale"' in html
    assert "proven: exit 0" in html and "stale: label_match 0.67" in html
    assert 'id="drawer-c01"' in html and 'id="drawer-c02"' in html
    assert "Running on http://127.0.0.1:5000" in html  # stdout tail
    digest = hashlib.sha256(PNG_1PX).hexdigest()
    assert f'src="/artifacts/{digest}"' in html
    assert (artifact_dir / digest).read_bytes() == PNG_1PX
    assert "Hello, Flask!" in html and "Hello, World!" in html  # observed vs expects
    assert "[docs] says Hello" in html and "+new" in html  # brief and diff
    assert "Unapproved proposal; not published." in html
    assert "Authority" in html and "Unresolved" in html
    assert "Coverage gaps" in html and "c02 · stale" in html
    assert "turn 7" in html and "runner fake" in html
    # strip numbers are the ones passed in, in order
    pos = [html.index(s) for s in ('<span class="n">2</span> claims', '<span class="n">1</span> proven', '<span class="n">1</span> stale', '<span class="n">0</span> broken')]
    assert pos == sorted(pos)
    assert "<script>alert" not in html


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


def test_prove_flask_with_fake_runner(tmp_path):
    runner = FakeBrowserRunner()
    result = prove(str(FLASK), runner=runner, home=tmp_path, shell_env=venv_env())
    statuses = {c.id: result.by_id[c.id].status for c in result.claims}
    shell = [c for c in result.claims if c.kind == "shell"]
    browser = [c for c in result.claims if c.kind == "browser"]
    assert len(shell) == 3 and len(browser) == 1
    assert all(statuses[c.id] == "proven" for c in shell), statuses
    assert statuses[browser[0].id] == "proven"
    # the fake runner was handed the detected server URL
    assert runner.calls == [(browser[0].id, "http://127.0.0.1:5000")]
    assert result.page and result.page.exists()
    html = result.page.read_text(encoding="utf-8")
    assert "Flask Quickstart" in html
    assert 'class="badge proven"' in html
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
    obs = {"Hello, World!": {"status": "passed", "observed": {"x": "Hello, Flask!"}}}
    p = tmp_path / "obs.json"
    p.write_text(json.dumps(obs))
    runner = FakeBrowserRunner.from_file(p)
    result = prove(str(FLASK), runner=runner, home=tmp_path, shell_env=venv_env())
    b = next(c for c in result.claims if c.kind == "browser")
    assert result.by_id[b.id].status == "stale"
    assert result.failing() == [b]


def test_cli_prove_serve_badge(tmp_path, monkeypatch):
    for k, v in venv_env().items():
        monkeypatch.setenv(k, v)
    cli = CliRunner()
    r = cli.invoke(app, ["prove", str(FLASK), "--home", str(tmp_path)])
    assert r.exit_code == 0, r.output
    assert "runner: fake" in r.output
    assert "claims |" in r.output and "proven" in r.output
    assert "page:" in r.output
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


def test_cli_latest_pointer_cannot_escape_attest_home(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "index.html").write_text("private", encoding="utf-8")
    (home / "latest").write_text(str(outside), encoding="utf-8")

    result = CliRunner().invoke(app, ["serve", "--home", str(home)])

    assert result.exit_code == 1
    assert "inside the Attest home directory" in result.output


def test_cli_serves_digest_verified_images_with_restrictive_headers(tmp_path):
    page_dir = tmp_path / "page"
    page_dir.mkdir()
    (page_dir / "index.html").write_text(
        "<!doctype html><style>body{color:red}</style><script>document.body.dataset.ready='yes'</script>",
        encoding="utf-8",
    )
    (page_dir / "private.txt").write_text("must not be served", encoding="utf-8")
    artifact_dir = page_dir / "artifacts"
    artifact_dir.mkdir()
    digest = hashlib.sha256(PNG_1PX).hexdigest()
    (artifact_dir / digest).write_bytes(PNG_1PX)

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(_QuietHandler, directory=str(page_dir)))
    server.daemon_threads = True
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with urlopen(base + "/") as response:
            page = response.read()
            policy = response.headers["Content-Security-Policy"]
            assert response.headers["X-Content-Type-Options"] == "nosniff"
            assert b"document.body" in page
            assert "default-src 'none'" in policy and "unsafe-inline" not in policy
        with urlopen(base + f"/artifacts/{digest}") as response:
            assert response.headers["Content-Type"] == "image/png"
            assert "immutable" in response.headers["Cache-Control"]
            assert response.read() == PNG_1PX
        try:
            urlopen(base + "/private.txt")
            assert False, "arbitrary files must not be served"
        except HTTPError as error:
            assert error.code == 404
        try:
            urlopen(Request(base + "/private.txt", method="HEAD"))
            assert False, "HEAD must not expose the static directory handler"
        except HTTPError as error:
            assert error.code == 405
        try:
            urlopen(base + "/artifacts/" + "0" * 64)
            assert False, "unknown digest must not be served"
        except HTTPError as error:
            assert error.code == 404
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

import base64
import hashlib
import json
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from typer.testing import CliRunner

from attest.cli import _QuietHandler, app
from attest.ingest import ingest_text
from attest.models import BrowserObservation, Brief, Claim, Evidence, Span, Verdict
from attest.render import content_security_policy, inline, render_page

PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jXioAAAAASUVORK5CYII="
)


def _strip(**updates):
    values = {
        "claims": 1,
        "proven": 0,
        "stale": 0,
        "broken": 0,
        "unverifiable": 0,
        "skipped": 0,
        "turn": 1,
        "loops_attempted": 0,
        "loops_closed": 0,
    }
    values.update(updates)
    return values


def test_hostile_bare_urls_and_remote_images_never_create_unsafe_attributes():
    rendered = inline(
        'https://example.test/\"><img/src=x onerror=alert(1)> '
        '[run](javascript:alert(1)) '
        '![tracker](https://tracker.test/pixel.png) '
        '[quoted](https://safe.test/"onmouseover="alert(1))'
    )

    assert "<img" not in rendered
    assert 'onerror="' not in rendered and 'onmouseover="' not in rendered
    assert 'href="javascript:' not in rendered
    assert 'src="https:' not in rendered
    assert "&quot;" in rendered
    assert '<a href="https://example.test/">https://example.test/</a>' in rendered


def test_review_status_keeps_coverage_authority_proposal_and_identities_distinct():
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
    brief = Brief(
        claim_id="c01",
        verdict=before_verdict,
        doc_excerpt="# Before",
        patch="--- a/review.md\n+++ b/review.md\n-# Before\n+# After",
    )

    page = render_page(
        after,
        [claim],
        [current],
        _strip(unverifiable=1),
        briefs={"c01": brief},
        prior_verdicts={"c01": before_verdict},
        authority={"c01": {"decision": "doc_repair_denied", "rule": "protected promise"}},
    )

    before_identity = before.sha256
    after_identity = hashlib.sha256(b"# After\n").hexdigest()
    assert before_identity != after_identity
    assert f"Before</strong> <code>{before_identity}</code>" in page
    assert f"After</strong> <code>{after_identity}</code>" in page
    assert "c01 · unverifiable" in page
    assert "Refused - unresolved" in page and "protected-contract refusal remains unresolved" in page
    assert "not a green result" in page
    assert "Unapproved proposal; not published." in page


def test_screenshot_is_copied_to_digest_artifact_and_outside_paths_are_ignored(tmp_path):
    page_dir = tmp_path / "run"
    shots = page_dir / "shots"
    shots.mkdir(parents=True)
    (shots / "screen.png").write_bytes(PNG_1PX)
    doc = ingest_text("# Evidence\n", source="evidence.md")
    claim = Claim(id="c01", kind="browser", span=Span(start_line=1, end_line=1, text="# Evidence"), objective="open page")
    evidence = Evidence(
        claim_id="c01",
        kind="browser",
        observation=BrowserObservation(status="passed", screenshot="shots/screen.png"),
    )
    verdict = Verdict(claim_id="c01", status="proven", reason="observed", evidence=evidence)
    artifact_dir = page_dir / "artifacts"

    page = render_page(doc, [claim], [verdict], _strip(proven=1), artifact_dir=artifact_dir)

    digest = hashlib.sha256(PNG_1PX).hexdigest()
    assert f'src="artifacts/{digest}"' in page
    assert (artifact_dir / digest).read_bytes() == PNG_1PX

    outside = tmp_path / "outside.png"
    outside.write_bytes(PNG_1PX)
    external_evidence = Evidence(
        claim_id="c01",
        kind="browser",
        observation=BrowserObservation(status="passed", screenshot=str(outside)),
    )
    no_external = render_page(
        doc,
        [claim],
        [Verdict(claim_id="c01", status="proven", reason="observed", evidence=external_evidence)],
        _strip(proven=1),
        artifact_dir=page_dir / "other-artifacts",
    )
    assert f"artifacts/{digest}" not in no_external
    assert not (page_dir / "other-artifacts" / digest).exists()


def test_review_server_only_serves_page_and_digest_verified_artifacts_with_csp(tmp_path):
    page_dir = tmp_path / "page"
    page_dir.mkdir()
    (page_dir / "index.html").write_text(
        "<!doctype html><style>body{color:red}</style><script>document.body.dataset.ready='yes'</script>",
        encoding="utf-8",
    )
    (page_dir / "private.txt").write_text("not public", encoding="utf-8")
    artifacts = page_dir / "artifacts"
    artifacts.mkdir()
    digest = hashlib.sha256(PNG_1PX).hexdigest()
    (artifacts / digest).write_bytes(PNG_1PX)
    (artifacts / ("0" * 64)).write_bytes(PNG_1PX)

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(_QuietHandler, directory=str(page_dir)))
    server.daemon_threads = True
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with urlopen(base + "/") as response:
            assert b"document.body" in response.read()
            policy = response.headers["Content-Security-Policy"]
            assert "default-src 'none'" in policy
            assert "style-src 'sha256-" in policy and "script-src 'sha256-" in policy
            assert "unsafe-inline" not in policy
            assert response.headers["X-Content-Type-Options"] == "nosniff"
        with urlopen(base + f"/artifacts/{digest}") as response:
            assert response.headers["Content-Type"] == "image/png"
            assert "immutable" in response.headers["Cache-Control"]
            assert response.read() == PNG_1PX
        for path in ("/private.txt", "/artifacts/" + "0" * 64, "/%2e%2e/private.txt"):
            try:
                urlopen(base + path)
                assert False, f"unexpectedly served {path}"
            except HTTPError as exc:
                assert exc.code == 404
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_live_review_server_preserves_kane_feeds_and_nested_digest_routes(tmp_path):
    (tmp_path / "index.html").write_text(
        "<!doctype html><style>body{color:red}</style><iframe src='run/index.html'></iframe><script>fetch('latest.json')</script>",
        encoding="utf-8",
    )
    (tmp_path / "latest.json").write_text(json.dumps({"running": True}), encoding="utf-8")
    (tmp_path / "live.log").write_text("Kane is checking\n", encoding="utf-8")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "index.html").write_text("<!doctype html><style>body{color:red}</style><script>fetch('../latest.json')</script>", encoding="utf-8")
    artifact_dir = run_dir / "artifacts"
    artifact_dir.mkdir()
    digest = hashlib.sha256(PNG_1PX).hexdigest()
    (artifact_dir / digest).write_bytes(PNG_1PX)

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(_QuietHandler, directory=str(tmp_path), live_mode=True))
    server.daemon_threads = True
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with urlopen(base + "/") as response:
            policy = response.headers["Content-Security-Policy"]
            assert "connect-src 'self'" in policy and "frame-src 'self'" in policy
            assert "frame-ancestors 'self'" in policy
        assert json.loads(urlopen(base + "/latest.json").read()) == {"running": True}
        assert b"Kane is checking" in urlopen(base + "/live.log").read()
        with urlopen(base + f"/run/artifacts/{digest}") as response:
            assert response.read() == PNG_1PX
        with urlopen(base + "/run/index.html") as response:
            assert b"fetch" in response.read()
        try:
            urlopen(base + "/private.txt")
            assert False, "live server must not expose arbitrary home files"
        except HTTPError as exc:
            assert exc.code == 404
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_serve_rejects_latest_pointer_outside_attest_home(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "index.html").write_text("private", encoding="utf-8")
    (home / "latest").write_text(str(outside), encoding="utf-8")

    result = CliRunner().invoke(app, ["serve", "--home", str(home)])

    assert result.exit_code == 1
    assert "inside the Attest home directory" in result.output


def test_live_csp_allows_only_same_origin_polling_and_framing():
    policy = content_security_policy(
        "<style>body{color:red}</style><script>fetch('latest.json')</script>", live=True
    )
    assert "connect-src 'self'" in policy
    assert "frame-src 'self'" in policy and "frame-ancestors 'self'" in policy
    assert "unsafe-inline" not in policy


def test_rendered_page_fits_its_own_csp_without_data_uris_or_style_attributes(tmp_path):
    doc = ingest_text("# Evidence\n\nRun it.\n", source="evidence.md")
    claim = Claim(id="c01", kind="shell", span=Span(start_line=3, end_line=3, text="Run it."), command="true")
    verdict = Verdict(claim_id="c01", status="proven", reason="ok", evidence=Evidence(claim_id="c01", kind="shell", exit_code=0))
    page_dir = tmp_path / "run"
    artifact_dir = page_dir / "artifacts"

    page = render_page(doc, [claim], [verdict], _strip(proven=1), artifact_dir=artifact_dir)

    assert "data:" not in page
    assert " style=" not in page
    logo = (Path(__file__).resolve().parents[1] / "attest" / "assets" / "kane-factory.png").read_bytes()
    logo_digest = hashlib.sha256(logo).hexdigest()
    assert f'src="artifacts/{logo_digest}"' in page
    assert (artifact_dir / logo_digest).read_bytes() == logo
    policy = content_security_policy(page)
    assert policy.count("'sha256-") >= 2 and "unsafe" not in policy


def test_ticket_links_pass_the_link_allowlist(tmp_path):
    doc = ingest_text("# Doc\n\nStep.\n", source="doc.md")
    claim = Claim(id="c01", kind="shell", span=Span(start_line=3, end_line=3, text="Step."), command="false")
    before = Verdict(claim_id="c01", status="broken", reason="failed", evidence=Evidence(claim_id="c01", kind="shell", exit_code=1))
    brief = Brief(claim_id="c01", verdict=before, doc_excerpt="Step.", patch="--- a/x\n+++ b/x\n-a\n+b")
    receipts = {"c01": {"case_id": "abc-c01-1", "path": str(tmp_path / "missing.json"), "ticket": "javascript:alert(1)", "ticket_title": "t"}}

    page = render_page(doc, [claim], [before], _strip(broken=1), briefs={"c01": brief}, prior_verdicts={"c01": before},
                       authority={"c01": {"decision": "implementation_required"}}, receipts=receipts)

    assert "javascript:" not in page
    assert "Implementation required - unresolved" in page


def test_live_review_server_serves_receipts_tickets_and_last_modified_only(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "index.html").write_text("<!doctype html><p>run</p>", encoding="utf-8")
    (tmp_path / "receipts").mkdir()
    (tmp_path / "receipts" / "abc-c01-1.json").write_text('{"case_id": "abc-c01-1"}', encoding="utf-8")
    (tmp_path / "tickets").mkdir()
    (tmp_path / "tickets" / "abc-c01-1.md").write_text("# ticket", encoding="utf-8")
    (tmp_path / "tickets" / "abc-c01-1.html").write_text("<script>no</script>", encoding="utf-8")
    (tmp_path / "ledger.jsonl").write_text("{}", encoding="utf-8")

    def serve(live):
        server = ThreadingHTTPServer(("127.0.0.1", 0), partial(_QuietHandler, directory=str(tmp_path), live_mode=live))
        server.daemon_threads = True
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server, thread, f"http://127.0.0.1:{server.server_address[1]}"

    def status(url, method="GET"):
        try:
            with urlopen(Request(url, method=method)) as response:
                return response.status
        except HTTPError as exc:
            return exc.code

    server, thread, base = serve(True)
    try:
        with urlopen(base + "/receipts/abc-c01-1.json") as response:
            assert response.headers["Content-Type"].startswith("application/json")
            assert response.headers["X-Content-Type-Options"] == "nosniff"
            assert json.loads(response.read()) == {"case_id": "abc-c01-1"}
        with urlopen(base + "/tickets/abc-c01-1.md") as response:
            assert response.headers["Content-Type"].startswith("text/plain")
        with urlopen(Request(base + "/run/index.html", method="HEAD")) as response:
            assert response.headers["Last-Modified"]
        for path in ("/tickets/abc-c01-1.html", "/ledger.jsonl", "/receipts/..%2fledger.jsonl", "/run/receipts/abc-c01-1.json"):
            assert status(base + path) == 404, path
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    server, thread, base = serve(False)
    try:
        assert status(base + "/receipts/abc-c01-1.json") == 404
        assert status(base + "/", method="HEAD") == 405
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_csp_hashes_match_browser_newline_normalization():
    lf = content_security_policy("<style>a{}\nb{}</style><script>x()\ny()</script>")
    crlf = content_security_policy("<style>a{}\r\nb{}</style><script>x()\r\ny()</script>")
    assert lf == crlf

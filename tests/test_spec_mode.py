import json
import sys
import textwrap
from pathlib import Path

from attest.implement import app_diff_paths, make_candidate, safe_app_path
from attest.loop import run_loop
from attest.models import BrowserObservation, Claim, Span
from attest.pipeline import prove
from attest.runner import FakeBrowserRunner
from attest.verdict import Rules

PY = sys.executable

SPEC = textwrap.dedent(
    """\
    # Deals spec

    The app ships its page:

    ```bash
    test -f "$ATTEST_APP_DIR/index.html" && echo "$ATTEST_APP_DIR"
    ```

    Deals can be marked as won:

    ```bash
    grep -q "Mark as won" "$ATTEST_APP_DIR/app.js"
    ```
    """
)

APP_JS = "const deals = [];\nfunction render() {}\nrender();\n"

# A fake worker: reads the attest.ticket/v0 JSON on stdin, adds the missing action to app.js
# from the ticket's file contents and prints a git-style unified diff.
IMPL_AGENT = textwrap.dedent(
    """\
    import difflib, json, sys
    ticket = json.loads(sys.stdin.read())
    assert ticket["schema"] == "attest.ticket/v0" and ticket["title"].startswith("Missing capability")
    assert ticket["acceptance"] and ticket["observed"]["status"] and ticket["output"].startswith("unified diff")
    old = ticket["app"]["files"]["app.js"]
    new = old.replace("function render() {}", "function render() {}\\nfunction markWon(d) { d.status = 'Won'; } // Mark as won")
    diff = difflib.unified_diff(old.splitlines(True), new.splitlines(True), fromfile="a/app.js", tofile="b/app.js")
    print("".join(diff))
    """
)

ESCAPE_AGENT = textwrap.dedent(
    """\
    print("--- /dev/null\\n+++ b/../outside.js\\n@@ -0,0 +1 @@\\n+// Mark as won\\n")
    """
)


def setup(tmp_path, monkeypatch, agent_body, env="ATTEST_WORKER_CMD"):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)  # fences only, no model
    monkeypatch.chdir(tmp_path)
    app = tmp_path / "app"
    app.mkdir()
    (app / "index.html").write_text("<h1>Deals</h1>\n", encoding="utf-8")
    (app / "app.js").write_text(APP_JS, encoding="utf-8")
    spec = tmp_path / "spec.md"
    spec.write_text(SPEC, encoding="utf-8")
    agent = tmp_path / "impl_agent.py"
    agent.write_text(agent_body, encoding="utf-8")
    monkeypatch.delenv("ATTEST_WORKER_CMD", raising=False)
    monkeypatch.delenv("ATTEST_IMPL_CMD", raising=False)
    monkeypatch.setenv(env, f'"{PY}" "{agent}"')
    return app, spec


def test_candidate_is_a_fresh_copy_and_reaches_the_shell(tmp_path, monkeypatch):
    app, spec = setup(tmp_path, monkeypatch, IMPL_AGENT)
    home = tmp_path / "home"
    result = prove(str(spec), runner=FakeBrowserRunner(), home=home, app_dir=app, keep_servers=True)
    cand = result.candidate
    assert cand.root.parent.parent == home / "candidates"
    assert cand.root.parent.name == f"{result.doc.sha256[:12]}-1"
    assert (cand.root / "app.js").read_text() == APP_JS and (cand.root / ".git").is_dir()
    # the shell step saw the candidate path, not the original app
    assert result.by_id["c01"].status == "proven"
    assert cand.posix in result.evidence["c01"].stdout_tail
    assert result.by_id["c02"].status == "broken"
    # a second run gets its own copy
    again = make_candidate(app, result.doc.sha256, home)
    assert again.root.parent.name.endswith("-2")
    result._session.close()


def test_failing_claim_gets_ticket_implementation_and_recheck(tmp_path, monkeypatch):
    app, spec = setup(tmp_path, monkeypatch, IMPL_AGENT)
    home = tmp_path / "home"
    runner = FakeBrowserRunner()
    result = prove(str(spec), runner=runner, home=home, app_dir=app, keep_servers=True)
    doc_lines = list(result.doc.lines)
    result = run_loop(result, runner, home=home, rules=Rules(), agent_cmd='raise "the doc agent must not be called"')
    after = result.by_id["c02"]
    assert after.status == "proven", after.reason
    assert "(after implementation)" in after.reason
    assert result.prior["c02"].status == "broken"
    # the change lives in the candidate only
    assert "Mark as won" in (result.candidate.root / "app.js").read_text()
    assert (app / "app.js").read_text() == APP_JS
    # ticket
    tickets = list((home / "tickets").glob("*.md"))
    assert len(tickets) == 1
    ticket = tickets[0].read_text()
    assert ticket.startswith("# Missing capability: ")
    assert "## Spec lines" in ticket and "## What the runner observed" in ticket and "- status: exit 1" in ticket
    assert "## Acceptance criterion" in ticket and "- app.js" in ticket and "- index.html" in ticket
    # receipt
    receipt = json.loads(next((home / "receipts").glob("*.json")).read_text())
    assert receipt["schema"] == "attest.receipt/v0"
    assert receipt["disposition"] == "implemented"
    assert receipt["authority"]["decision"] == "implementation_required"
    assert receipt["authority"]["rule"] == "spec mode: the spec is the contract"
    ticket_json = tickets[0].with_suffix(".json")
    assert receipt["ticket"]["path"] == str(ticket_json) and receipt["ticket"]["title"].startswith("Missing capability")
    tj = json.loads(ticket_json.read_text())
    assert tj["schema"] == "attest.ticket/v0" and tj["case_id"] == receipt["case_id"]
    assert tj["spec"]["lines"] == receipt["claim"]["lines"] and tj["app"]["root"] == result.candidate.posix
    assert set(tj["app"]["files"]) == {"app.js", "index.html"} and tj["app"]["files"]["app.js"] == APP_JS
    assert receipt["worker"] == receipt["proposal"]["agent"] and PY in receipt["worker"]
    assert receipt["app"]["original"] == str(app.resolve()) and receipt["app"]["candidate"] == result.candidate.posix
    assert receipt["app"]["patch_sha256"] == receipt["proposal"]["patch_sha256"]
    assert "+function markWon" in receipt["proposal"]["patch"]
    assert receipt["case_id"] == tickets[0].stem
    # the doc is never patched
    assert result.doc.lines == doc_lines and spec.read_text() == SPEC
    assert receipt["doc"]["sha256_after"] is None
    # the page shows the implementation block and links the ticket
    from attest.pipeline import render_result

    page = render_result(result, home, "fake").read_text(encoding="utf-8")
    assert "Missing capability: implemented by" in page
    assert f"../tickets/{receipt['case_id']}.md" in page and "App change" in page
    # the ticket and a compact receipt summary are shown inline in the drawer
    assert 'class="rcpt"' in page and "Acceptance criterion" in page
    assert receipt["proposal"]["patch_sha256"][:12] in page and receipt["proposal"]["patch_sha256"][:13] not in page
    assert "implementation required" in page and "spec mode: the spec is the contract" in page
    assert f"../receipts/{receipt['case_id']}.json" in page


def test_diff_outside_app_root_is_rejected(tmp_path, monkeypatch):
    app, spec = setup(tmp_path, monkeypatch, ESCAPE_AGENT, env="ATTEST_IMPL_CMD")  # the old name still works
    home = tmp_path / "home"
    runner = FakeBrowserRunner()
    result = prove(str(spec), runner=runner, home=home, app_dir=app, keep_servers=True)
    result = run_loop(result, runner, home=home, rules=Rules())
    after = result.by_id["c02"]
    assert after.status == "broken"
    assert "outside the app root" in after.reason
    receipt = json.loads(next((home / "receipts").glob("*.json")).read_text())
    assert receipt["disposition"] == "unresolved" and receipt["app"]["applied"] is False
    assert not (result.candidate.root.parent / "outside.js").exists()
    assert result.candidate.git("status", "--porcelain").stdout.strip() == ""
    assert spec.read_text() == SPEC


def test_safe_app_paths():
    assert safe_app_path("app.js") and safe_app_path("src/new.js")
    for bad in ("../x.js", "/etc/passwd", "C:/x.js", "a/../../x", ".git/config", "\\x"):
        assert not safe_app_path(bad), bad
    assert app_diff_paths("diff --git a/x.js b/../y.js\n--- a/x.js\n+++ b/../y.js\n") == {"x.js", "../y.js"}


class DiskRunner(FakeBrowserRunner):
    """Stands in for Kane: reads the candidate app from disk and caches per claim until forgotten."""

    def __init__(self, app_root_ref):
        super().__init__()
        self.app_root_ref = app_root_ref
        self.results = {}
        self.forgotten = []
        self.groups = []

    def prepare(self, claims, source="doc"):
        self.groups = [[c for c in claims if c.kind == "browser"]]

    def observe(self, claim, base_url):
        if claim.id in self.results:
            return self.results[claim.id]
        self.calls.append((claim.id, base_url))
        js = (self.app_root_ref() / "app.js").read_text(encoding="utf-8")
        for c in self.groups[0]:
            if c.expects == "Won":
                ok = "Mark as won" in js
                obs = BrowserObservation(status="passed" if ok else "failed", observed={f"{c.id}_label": "Won" if ok else ""},
                                         note="kane: step passed" if ok else "kane: step failed | failure: no Mark as won button")
            else:
                obs = BrowserObservation(status="passed", observed={f"{c.id}_label": c.expects})
            self.results.setdefault(c.id, obs)
        return self.results[claim.id]

    def forget(self, claim_ids):
        self.forgotten.append(set(claim_ids))
        for cid in claim_ids:
            self.results.pop(cid, None)

    def cached(self, claim_id):
        return self.results.get(claim_id)


def test_browser_claim_is_forgotten_and_rechecked(tmp_path, monkeypatch):
    app, spec = setup(tmp_path, monkeypatch, IMPL_AGENT)

    def claims(doc):
        def b(cid, line, expects):
            return Claim(id=cid, kind="browser", span=Span(start_line=line, end_line=line, text=doc.lines[line - 1]),
                         objective=f'check for "{expects}"', expects=expects, store_as=f"{cid}_label")
        return [b("c01", 3, "Deals"), b("c02", 9, "Won")]

    monkeypatch.setattr("attest.pipeline.compile_doc", lambda doc, llm=None: claims(doc))
    holder = {}
    runner = DiskRunner(lambda: holder["root"])
    original_make = __import__("attest.implement", fromlist=["make_candidate"]).make_candidate

    def make(app_dir, sha, home):
        cand = original_make(app_dir, sha, home)
        holder["root"] = cand.root
        return cand

    monkeypatch.setattr("attest.implement.make_candidate", make)
    home = tmp_path / "home"
    result = prove(str(spec), runner=runner, home=home, app_dir=app)
    assert [v.status for v in result.verdicts] == ["proven", "broken"]
    result = run_loop(result, runner, home=home, rules=Rules())
    assert runner.forgotten == [{"c01", "c02"}]
    assert result.by_id["c02"].status == "proven" and "(after implementation)" in result.by_id["c02"].reason
    assert [c for c, _ in runner.calls] == ["c01", "c02"]


def test_cli_app_flag_runs_spec_mode(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from attest.cli import app as cli_app

    app, spec = setup(tmp_path, monkeypatch, IMPL_AGENT)
    home = tmp_path / "home"
    out = CliRunner().invoke(cli_app, ["prove", str(spec), "--runner", "fake", "--app", str(app), "--home", str(home)])
    assert out.exit_code == 0, out.output
    assert "TICKET    line 12  Missing capability:" in out.output
    assert "WORKER    line 12  sending ticket to " in out.output and "app change:" in out.output
    assert "implemented in the app and re-proven" in out.output
    assert (app / "app.js").read_text() == APP_JS and spec.read_text() == SPEC


def test_candidate_root_is_absolute_even_with_a_relative_home(tmp_path, monkeypatch):
    # live demo: --home .attest is relative and git apply runs with cwd=candidate root
    from attest.implement import make_candidate

    app = tmp_path / "app"
    app.mkdir()
    (app / "index.html").write_text("<h1>Deals</h1>\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    cand = make_candidate(app, "ab" * 32, Path(".attest"))
    assert cand.root.is_absolute()

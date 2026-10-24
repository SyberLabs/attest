import json
import os
import sys
import textwrap
from pathlib import Path

from attest.loop import (
    brief_text,
    build_brief,
    diff_paths,
    extract_diff,
    make_checkout,
    run_loop,
    shift_spans,
)
from attest.ingest import ingest, ingest_text, sha256_of
from attest.models import BrowserObservation, Claim, Evidence, Span, Verdict
from attest.pipeline import prove
from attest.runner import FakeBrowserRunner
from attest.verdict import Rules

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable

BROKEN_DOC = textwrap.dedent(
    """\
    # Loop test doc

    First, say hello:

    ```bash
    echo hello
    ```

    Then run the check, which prints ready:

    ```bash
    echo ready && exit 1
    ```

    That is all.
    """
)

# A fake agent: reads the brief on stdin, finds the doc name in the instruction line,
# rewrites "exit 1" to "exit 0" and prints a git-style unified diff.
FIX_AGENT = textwrap.dedent(
    """\
    import difflib, re, sys
    from pathlib import Path
    brief = sys.stdin.read()
    name = re.search(r"Edit only `([^`]+)`", brief).group(1)
    old = Path(name).read_text(encoding="utf-8")
    new = old.replace("exit 1", "exit 0")
    diff = difflib.unified_diff(old.splitlines(True), new.splitlines(True), fromfile="a/" + name, tofile="b/" + name)
    print("Here is the fix:\\n```diff\\n" + "".join(diff) + "```\\nDone.")
    """
)

# A misbehaving agent: patches a file that is not the doc.
ROGUE_AGENT = textwrap.dedent(
    """\
    print("--- a/app.py\\n+++ b/app.py\\n@@ -1 +1 @@\\n-x = 1\\n+x = 2\\n")
    """
)

SILENT_AGENT = "print('I could not work out a fix.')\n"


def agent_cmd(tmp_path, name, body):
    p = tmp_path / f"{name}.py"
    p.write_text(body, encoding="utf-8")
    return f'"{PY}" "{p}"'


# -- pieces --------------------------------------------------------------------------


def test_extract_diff_from_fence_and_bare():
    fenced = "blah\n```diff\n--- a/x.md\n+++ b/x.md\n@@ -1 +1 @@\n-a\n+b\n```\nthanks"
    assert extract_diff(fenced) == "--- a/x.md\n+++ b/x.md\n@@ -1 +1 @@\n-a\n+b\n"
    bare = "Sure.\n--- a/x.md\n+++ b/x.md\n@@ -1 +1 @@\n-a\n+b\nHope this helps"
    assert extract_diff(bare) == "--- a/x.md\n+++ b/x.md\n@@ -1 +1 @@\n-a\n+b\n"
    assert extract_diff("no diff here") is None


def test_diff_paths():
    assert diff_paths("--- a/x.md\n+++ b/x.md\n") == {"x.md"}
    assert diff_paths("--- /dev/null\n+++ b/new.txt\n") == {"new.txt"}


def test_checkout_rejects_foreign_files_and_bad_patches():
    doc = ingest_text(BROKEN_DOC, source="loop.md")
    co = make_checkout(doc)
    ok, msg = co.apply("--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-x\n+y\n")
    assert not ok and "other than the doc" in msg
    ok, msg = co.apply("--- a/loop.md\n+++ b/loop.md\n@@ -1 +1 @@\n-not in the file\n+zzz\n")
    assert not ok and "apply --check failed" in msg
    ok, msg = co.apply("--- a/loop.md\n+++ b/loop.md\n@@ -1 +1 @@\n-# Loop test doc\n+# Loop test document\n")
    assert ok
    assert co.doc_path.read_text().startswith("# Loop test document")


def test_brief_is_short_and_complete():
    doc = ingest_text(BROKEN_DOC, source="loop.md")
    f = doc.fences[1]
    claim = Claim(id="c02", kind="shell", span=Span(start_line=f.start_line, end_line=f.end_line, text=f.text), command=f.text, expects="ready")
    ev = Evidence(claim_id="c02", kind="shell", command=f.text, exit_code=1, stdout_tail="ready")
    v = Verdict(claim_id="c02", status="broken", reason="broken: exit code 1", evidence=ev)
    brief = build_brief(doc, claim, v, ["[doc loop.md:12] echo ready && exit 1"], "loop.md")
    text = brief_text(brief, claim, "loop.md")
    assert len(text.splitlines()) < 60
    assert "broken: exit code 1" in text
    assert "exit code: 1" in text
    assert "Edit only `loop.md`" in text
    assert "unified diff" in text
    assert "[doc loop.md:12]" in text
    assert f"{f.start_line}" in text  # line numbers shown


def test_brief_for_browser_claim_shows_observed():
    doc = ingest_text("# t\nClick Create project.\n", source="x.md")
    c = Claim(id="c01", kind="browser", span=Span(start_line=2, end_line=2, text=doc.lines[1]), objective="o", expects="Create project", store_as="c01_label")
    ev = Evidence(claim_id="c01", kind="browser", observation=BrowserObservation(status="passed", observed={"c01_label": "New project"}, screenshot="s.png", note="n"))
    v = Verdict(claim_id="c01", status="stale", reason="stale: 0.78", evidence=ev, label_match=0.78)
    text = brief_text(build_brief(doc, c, v, [], "x.md"), c, "x.md")
    assert "observed c01_label = 'New project'" in text and "screenshot: s.png" in text and "runner note: n" in text


def test_shift_spans():
    old = ["a", "b", "c", "d"]
    new = ["a", "b", "b2", "c", "d"]
    c1 = Claim(id="c01", kind="prose", span=Span(start_line=1, end_line=1, text="a"))
    c2 = Claim(id="c02", kind="prose", span=Span(start_line=2, end_line=2, text="b"))
    c3 = Claim(id="c03", kind="prose", span=Span(start_line=3, end_line=4, text="c\nd"))
    out = shift_spans([c1, c2, c3], old, new, "c02")
    assert (out[0].span.start_line, out[2].span.start_line, out[2].span.end_line) == (1, 4, 5)
    assert out[2].span.text == "c\nd"


# -- end to end ------------------------------------------------------------------------


def test_loop_fixes_broken_shell_step(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)  # fences only, no model
    monkeypatch.chdir(tmp_path)
    doc_path = tmp_path / "loop.md"
    doc_path.write_text(BROKEN_DOC, encoding="utf-8")
    runner = FakeBrowserRunner()
    result = prove(str(doc_path), runner=runner, home=tmp_path / "home", keep_servers=True)
    assert [v.status for v in result.verdicts] == ["proven", "broken"]
    failing = result.failing()
    assert [c.id for c in failing] == ["c02"]
    base_sha256 = result.doc.sha256

    result = run_loop(result, runner, home=tmp_path / "home", rules=Rules(), agent_cmd=agent_cmd(tmp_path, "fix", FIX_AGENT))
    after = result.by_id["c02"]
    assert after.status == "proven", after.reason
    assert "(after loop)" in after.reason
    brief = result.briefs["c02"]
    assert brief.attempts == 1
    assert brief.patch and "+echo ready && exit 0" in brief.patch and "-echo ready && exit 1" in brief.patch
    assert result.prior["c02"].status == "broken"
    # the re-compiled claim carries the new command verbatim and the same id
    c02 = next(c for c in result.claims if c.id == "c02")
    assert c02.command == "echo ready && exit 0"
    assert result.doc.lines[c02.span.start_line - 1] == "echo ready && exit 0"
    assert result.doc.sha256 != base_sha256
    assert result.doc.sha256 == sha256_of("\n".join(result.doc.lines) + "\n")
    # the source file was not modified (apply_to_source defaults to False)
    assert "exit 1" in doc_path.read_text()
    # both verdicts under one turn in the ledger
    rows = [json.loads(l) for l in (tmp_path / "home" / "ledger.jsonl").read_text().splitlines()]
    c02_rows = [r for r in rows if r["claim_id"] == "c02"]
    assert [r["status"] for r in c02_rows] == ["broken", "proven"]
    assert {r["turn"] for r in c02_rows} == {result.turn}
    assert c02_rows[0]["doc_sha256"] == base_sha256
    assert c02_rows[1]["doc_sha256"] == result.doc.sha256
    assert c02_rows[1]["looped"] is True
    # the repaired-content ledger strip counts the repaired claim and closed loop
    from attest.ledger import Ledger

    s = Ledger(tmp_path / "home" / "ledger.jsonl").summary(result.doc.sha256, result.turn)
    assert (s["proven"], s["broken"], s["loops_closed"]) == (1, 0, 1)


def test_loop_rejects_diff_touching_other_file(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    doc_path = tmp_path / "loop.md"
    doc_path.write_text(BROKEN_DOC, encoding="utf-8")
    runner = FakeBrowserRunner()
    result = prove(str(doc_path), runner=runner, home=tmp_path / "home")
    result = run_loop(result, runner, home=tmp_path / "home", rules=Rules(), agent_cmd=agent_cmd(tmp_path, "rogue", ROGUE_AGENT))
    after = result.by_id["c02"]
    assert after.status == "broken"
    assert "other than the doc" in after.reason
    assert result.briefs["c02"].patch is not None  # kept for the drawer
    assert "exit 1" in doc_path.read_text()


def test_loop_handles_agent_without_diff(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    doc_path = tmp_path / "loop.md"
    doc_path.write_text(BROKEN_DOC, encoding="utf-8")
    runner = FakeBrowserRunner()
    result = prove(str(doc_path), runner=runner, home=tmp_path / "home")
    result = run_loop(result, runner, home=tmp_path / "home", rules=Rules(), agent_cmd=agent_cmd(tmp_path, "silent", SILENT_AGENT))
    assert result.by_id["c02"].status == "broken"
    assert "no unified diff" in result.by_id["c02"].reason
    assert result.briefs["c02"].patch is None


def test_loop_apply_writes_source(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    doc_path = tmp_path / "loop.md"
    doc_path.write_text(BROKEN_DOC, encoding="utf-8")
    runner = FakeBrowserRunner()
    result = prove(str(doc_path), runner=runner, home=tmp_path / "home")
    run_loop(result, runner, home=tmp_path / "home", rules=Rules(), agent_cmd=agent_cmd(tmp_path, "fix", FIX_AGENT), apply_to_source=True)
    assert "exit 0" in doc_path.read_text() and "exit 1" not in doc_path.read_text()

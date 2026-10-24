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
from attest.ingest import ingest, ingest_text
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
    # the source file was not modified (apply_to_source defaults to False)
    assert "exit 1" in doc_path.read_text()
    # both verdicts under one turn in the ledger
    rows = [json.loads(l) for l in (tmp_path / "home" / "ledger.jsonl").read_text().splitlines()]
    c02_rows = [r for r in rows if r["claim_id"] == "c02"]
    assert [r["status"] for r in c02_rows] == ["broken", "proven"]
    assert {r["turn"] for r in c02_rows} == {result.turn}
    assert c02_rows[1]["looped"] is True
    # the ledger strip now counts the claim as proven and the loop as closed
    from attest.ledger import Ledger

    s = Ledger(tmp_path / "home" / "ledger.jsonl").summary(result.doc.sha256, result.turn)
    assert (s["proven"], s["broken"], s["loops_closed"]) == (2, 0, 1)
    receipt = json.loads(next((tmp_path / "home" / "receipts").glob("*.json")).read_text())
    assert receipt["schema"] == "attest.receipt/v0"
    assert receipt["disposition"] == "repaired"
    assert receipt["proposal"]["patch_sha256"]
    assert set(receipt) == {"schema", "case_id", "created_at", "doc", "claim", "authority", "proposal", "approval", "evidence", "handoff", "disposition", "verified"}


def test_protected_claim_hands_off_without_calling_agent(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    doc_path = tmp_path / "loop.md"
    doc_path.write_text(BROKEN_DOC, encoding="utf-8")
    policy = tmp_path / "attest.policy.yaml"
    policy.write_text('protected:\n  - claim: c02\n', encoding="utf-8")
    result = prove(str(doc_path), runner=FakeBrowserRunner(), home=tmp_path / "home")
    result = run_loop(result, FakeBrowserRunner(), home=tmp_path / "home", rules=Rules(), agent_cmd='raise "agent must not be called"')
    assert "doc repair refused" in result.by_id["c02"].reason
    receipt = json.loads(next((tmp_path / "home" / "receipts").glob("*.json")).read_text())
    assert receipt["disposition"] == "handoff_implementation"
    assert receipt["authority"]["decision"] == "doc_repair_denied"
    assert receipt["handoff"]["instruction"] == "the application must change, not the doc"
    assert result.briefs["c02"].attempts == 0


def test_policy_text_match_and_claim_id(tmp_path):
    from attest.policy import load_policy
    from attest.models import Claim, Span

    claim = Claim(id="c07", kind="prose", span=Span(start_line=1, end_line=1, text="It increments by one."))
    path = tmp_path / "policy.yaml"
    path.write_text('protected:\n  - match: "INCREMENTS BY ONE"\n', encoding="utf-8")
    assert load_policy("doc.md", path).decide(claim).decision == "doc_repair_denied"
    path.write_text('protected:\n  - claim: c07\n', encoding="utf-8")
    assert load_policy("doc.md", path).decide(claim).decision == "doc_repair_denied"


def test_missing_policy_is_descriptive(tmp_path):
    from attest.policy import load_policy
    from attest.models import Claim, Span

    claim = Claim(id="c07", kind="prose", span=Span(start_line=1, end_line=1, text="increments by one"))
    decision = load_policy(str(tmp_path / "doc.md")).decide(claim)
    assert decision.allowed and decision.rule is None


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


STALE_DOC = textwrap.dedent(
    """\
    # Starter page

    Open http://localhost:5173/ in your browser. The page shows the heading "Vite + React"
    and a button labelled "count is 0".
    """
)

# Rewrites the stale heading the way the real agent should for the Vite catch.
HEADING_AGENT = FIX_AGENT.replace('old.replace("exit 1", "exit 0")', 'old.replace(\'"Vite + React"\', \'"Get started"\')')


class BatchingRunner(FakeBrowserRunner):
    """Stands in for the Kane runner: answers from today's page and keeps every observation."""

    PAGE = {"c01": "Get started", "c02": "Count is 0"}

    def __init__(self):
        super().__init__()
        self.seen = {}

    def observe(self, claim, base_url):
        self.calls.append((claim.id, base_url))
        for cid, text in self.PAGE.items():
            self.seen[cid] = BrowserObservation(status="passed", observed={f"{cid}_label": text}, screenshot="/pack/annotated.png", note="kane: step passed, authored")
        return self.seen[claim.id]

    def cached(self, claim_id):
        return self.seen.get(claim_id)


def stale_claims(doc):
    def b(cid, line, expects, deps):
        return Claim(id=cid, kind="browser", span=Span(start_line=line, end_line=line, text=doc.lines[line - 1]),
                     objective=f'Open http://localhost:5173/ and check for "{expects}".', expects=expects, store_as=f"{cid}_label", depends_on=deps)
    return [b("c01", 3, "Vite + React", []), b("c02", 4, "count is 0", ["c01"])]


def test_loop_fixes_stale_browser_step_and_rejudges_dependents(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)  # re-compile follows the new quote without a model
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("attest.pipeline.compile_doc", lambda doc, llm=None: stale_claims(doc))
    doc_path = tmp_path / "starter.md"
    doc_path.write_text(STALE_DOC, encoding="utf-8")
    runner = BatchingRunner()
    result = prove(str(doc_path), runner=runner, home=tmp_path / "home")
    assert [v.status for v in result.verdicts] == ["stale", "skipped"]

    result = run_loop(result, runner, home=tmp_path / "home", rules=Rules(), agent_cmd=agent_cmd(tmp_path, "heading", HEADING_AGENT))
    c01 = next(c for c in result.claims if c.id == "c01")
    assert c01.expects == "Get started" and '"Get started"' in c01.objective
    assert result.by_id["c01"].status == "proven", result.by_id["c01"].reason
    assert result.by_id["c02"].status == "proven" and "after loop on c01" in result.by_id["c02"].reason
    assert result.prior["c02"].status == "skipped"
    rows = [json.loads(l) for l in (tmp_path / "home" / "ledger.jsonl").read_text().splitlines()]
    assert [r["status"] for r in rows if r["claim_id"] == "c01"] == ["stale", "proven"]
    assert len({r["turn"] for r in rows}) == 1


def test_brief_carries_kane_failure_record_and_screenshot():
    doc = ingest_text("# t\nClick Create project.\n", source="x.md")
    c = Claim(id="c01", kind="browser", span=Span(start_line=2, end_line=2, text=doc.lines[1]), objective="o", expects="Create project", store_as="c01_label")
    note = ("kane: step failed, authored; AP determined agent is stuck | failure: Greeting transition: stuck | "
            "root cause: waited for a change | suggested fix: inspect the page | pack: C:/x.evidence")
    obs = BrowserObservation(status="failed", observed={"c01_label": ""}, screenshot="C:/kane/steps/9-1-5/annotated.png", note=note)
    v = Verdict(claim_id="c01", status="broken", reason="broken: browser step failed", evidence=Evidence(claim_id="c01", kind="browser", observation=obs))
    text = brief_text(build_brief(doc, c, v, [], "x.md"), c, "x.md")
    assert "runner note: kane: step failed" in text
    assert "- root cause: waited for a change" in text and "- suggested fix: inspect the page" in text
    assert "pack:" not in text
    assert "screenshot: C:/kane/steps/9-1-5/annotated.png" in text
    assert len(text.splitlines()) < 60


def test_checkout_applies_a_diff_with_miscounted_hunk_header():
    # the real Codex patch for the GitHub card: header says 7 lines, the hunk has 4
    lines = [f"line {n}" for n in range(1, 15)] + ["", '4. In the left-hand sidebar click "Security & analysis".', "", "5. Next step.", "", "6. More.", "", "7. End."]
    doc = ingest_text("\n".join(lines) + "\n", source="card.md")
    co = make_checkout(doc)
    diff = (
        "--- a/card.md\n+++ b/card.md\n@@ -15,7 +15,7 @@\n \n"
        '-4. In the left-hand sidebar click "Security & analysis".\n'
        '+4. In the left-hand sidebar click "Advanced Security".\n'
        " \n"
    )
    ok, message = co.apply(diff)
    assert ok, message
    assert '"Advanced Security"' in co.doc_path.read_text(encoding="utf-8")


def test_openai_agent_is_called_for_the_openai_command(monkeypatch, tmp_path):
    import attest.loop as loop

    seen = {}

    def fake(text, model=None, timeout_s=0):
        seen["text"], seen["model"] = text, model
        return "--- a/x.md\n+++ b/x.md\n@@ -1 +1 @@\n-a\n+b\n"

    monkeypatch.setattr(loop, "call_openai_agent", fake)
    assert "+b" in loop.call_agent("brief", tmp_path, cmd="openai")
    assert seen == {"text": "brief", "model": None}
    loop.call_agent("brief", tmp_path, cmd="openai:gpt-5-mini")
    assert seen["model"] == "gpt-5-mini"

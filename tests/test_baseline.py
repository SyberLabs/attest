"""Baseline mode: keys that survive line shifts, the comparison, and the CLI gate."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from attest.baseline import SCHEMA, claim_keys, compare, load_baseline, write_baseline
from attest.models import Claim, Evidence, PromptObservation, Span, Verdict

PROMPT = "---\nname: {name}\ndescription: d\nrunvil: 0\n---\n\n# T\n\n## Main\n\n```lua\nreturn 1\n```\n"


def prompt(cid, name, line=1):
    return Claim(id=cid, kind="prompt", span=Span(start_line=line, end_line=line, text="x"), command=PROMPT.format(name=name))


def shell(cid, cmd, line=1):
    return Claim(id=cid, kind="shell", span=Span(start_line=line, end_line=line, text=cmd), command=cmd)


def verdict(cid, status):
    if status == "unverifiable":
        return Verdict(claim_id=cid, status=status, reason=status)
    return Verdict(claim_id=cid, status=status, reason=f"{status}: because", evidence=Evidence(claim_id=cid, kind="shell", exit_code=0))


def result(source, claims, statuses):
    verdicts = [verdict(c.id, s) for c, s in zip(claims, statuses)]
    return SimpleNamespace(doc=SimpleNamespace(source=source), claims=claims, verdicts=verdicts)


def test_keys_follow_what_a_claim_is_not_where_it_sits():
    a = claim_keys("docs/ch1.md", [prompt("c01", "hello", 5), shell("c02", "echo hi", 9), prompt("c03", "hello", 20)])
    b = claim_keys("other/checkout/ch1.md", [shell("c01", "echo hi", 3), prompt("c02", "hello", 40), prompt("c03", "hello", 80)])
    assert a["c01"][0] == "ch1.md::prompt::hello#1" and a["c03"][0] == "ch1.md::prompt::hello#2"
    assert {k for k, _ in a.values()} == {k for k, _ in b.values()}
    prose = Claim(id="c04", kind="prose", span=Span(start_line=1, end_line=1, text="p"))
    assert "c04" not in claim_keys("ch1.md", [prose])


def test_compare_separates_new_known_and_fixed(tmp_path):
    claims = [prompt("c01", "a"), prompt("c02", "b"), prompt("c03", "c"), prompt("c04", "d")]
    path = write_baseline(tmp_path / "base.json", [result("ch.md", claims, ["broken", "proven", "stale", "unverifiable"])])
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["schema"] == SCHEMA and data["entries"]["ch.md::prompt::a#1"]["status"] == "broken"
    now = [prompt("c01", "a"), prompt("c02", "b"), prompt("c03", "c"), prompt("c04", "d"), prompt("c05", "e")]
    diff = compare([result("ch.md", now, ["broken", "stale", "proven", "broken", "broken"])], load_baseline(path))
    assert [c.key for c in diff.known_failures] == ["ch.md::prompt::a#1"]
    assert [(c.key, c.before) for c in diff.new_failures] == [("ch.md::prompt::b#1", "proven"), ("ch.md::prompt::d#1", "unverifiable"), ("ch.md::prompt::e#1", None)]
    assert [c.key for c in diff.fixed] == ["ch.md::prompt::c#1"]
    assert diff.gate_fails
    gone = compare([result("ch.md", [prompt("c01", "a")], ["broken"])], load_baseline(path))
    assert not gone.gate_fails and "ch.md::prompt::b#1" in gone.missing


def test_write_baseline_replaces_only_the_docs_in_the_run(tmp_path):
    path = tmp_path / "base.json"
    write_baseline(path, [result("one.md", [prompt("c01", "a")], ["broken"]), result("two.md", [prompt("c01", "b")], ["broken"])])
    write_baseline(path, [result("one.md", [prompt("c01", "a")], ["proven"])])
    entries = load_baseline(path)
    assert entries["one.md::prompt::a#1"]["status"] == "proven" and entries["two.md::prompt::b#1"]["status"] == "broken"
    (tmp_path / "bad.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="attest.baseline/v0"):
        load_baseline(tmp_path / "bad.json")


def _doc(*steps, intro=None):
    fence = "`" * 3
    parts = ["# Doc", ""] + ([intro, ""] if intro else [])
    for step in steps:
        parts += [fence + "bash", step, fence, ""]
    return chr(10).join(parts)


def test_cli_gates_on_new_failures_only(tmp_path, monkeypatch):
    from attest.cli import app

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    doc = tmp_path / "doc.md"
    doc.write_text(_doc("echo fine", "exit 3"), encoding="utf-8")
    home, base = str(tmp_path / "home"), str(tmp_path / "attest.baseline.json")
    first = CliRunner().invoke(app, ["prove", str(doc), "--runner", "none", "--home", home, "--write-baseline", base])
    assert first.exit_code == 1, first.output  # no baseline yet: every failure fails the gate
    assert "baseline  written to" in first.output
    known = CliRunner().invoke(app, ["prove", str(doc), "--runner", "none", "--home", home, "--baseline", base])
    assert known.exit_code == 0, known.output
    assert "0 new stale or broken | 1 known" in known.output
    # a line shift above the claims is not a change
    doc.write_text(_doc("echo fine", "exit 3", intro="Intro line."), encoding="utf-8")
    shifted = CliRunner().invoke(app, ["prove", str(doc), "--runner", "none", "--home", home, "--baseline", base])
    assert shifted.exit_code == 0, shifted.output
    # a step that newly fails does fail the gate, and is named; the known failure it skips is not new
    doc.write_text(_doc("echo fine && exit 4", "exit 3"), encoding="utf-8")
    new = CliRunner().invoke(app, ["prove", str(doc), "--runner", "none", "--home", home, "--baseline", base])
    assert new.exit_code == 1, new.output
    assert "1 new stale or broken" in new.output and "doc.md line 4  (was new claim)" in new.output

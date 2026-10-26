"""Console transcripts: `$ ` commands with their output in the same fence. The output a
transcript shows is a promise, checked line by line; in a doc written that way, a fence
without a `$ ` line is not a command."""

from __future__ import annotations

from attest.compiler import fences_by_regex, is_console_doc, transcript_output
from attest.ingest import ingest_text
from attest.models import Claim, Evidence, Span
from attest.verdict import judge, lines_in_order, output_shown

FENCE = "`" * 3


def doc(*blocks):
    parts = ["# Guide", ""]
    for lang, text in blocks:
        parts += [FENCE + lang, text, FENCE, ""]
    return ingest_text("\n".join(parts) + "\n")


def test_transcript_output_is_every_line_that_is_not_a_command():
    assert transcript_output("$ rg fast README.md\n75:  faster than both\n88:  stays fast") == "75:  faster than both\n88:  stays fast"
    assert transcript_output("$ cd ripgrep\n$ ls\nsrc  tests\n\n$ echo done\ndone") == "src  tests\ndone"
    assert transcript_output("rg fast README.md") is None  # no prompt: a plain command
    assert transcript_output("$ cd somewhere") is None  # no output shown
    assert transcript_output("$ long-command\n...") is None  # only an elision


def test_console_doc_detection_and_non_commands_become_prose():
    console = doc(("", "$ rg fast README.md\n75: faster"), ("", "$ rg -c x\n3"), ("", "log/"), ("", "!log/"))
    assert is_console_doc(console)
    claims = fences_by_regex(console)
    assert [c.kind for c in claims] == ["shell", "shell", "prose", "prose"]
    assert claims[0].expects == "75: faster" and claims[1].expects == "3"
    # a doc of plain command fences is not a console doc: nothing changes
    plain = doc(("bash", "echo one"), ("bash", "echo two"), ("bash", "log/"))
    assert not is_console_doc(plain)
    assert [c.kind for c in fences_by_regex(plain)] == ["shell", "shell", "shell"]
    assert all(c.expects is None for c in fences_by_regex(plain))
    # one transcript among many plain fences does not turn the plain ones into prose
    mixed = doc(("bash", "$ echo hi\nhi"), ("bash", "make"), ("bash", "make test"), ("bash", "make install"))
    assert [c.kind for c in fences_by_regex(mixed)] == ["shell"] * 4


def test_lines_in_order_with_elisions_and_placeholders():
    out = "noise\n75:  faster than both. (N.B. It is not\n88:  color and full Unicode support\n119:### Is it really faster"
    assert lines_in_order("75:  faster than both. (N.B. It is not\n...\n119:### Is it really faster", out)
    assert not lines_in_order("119:### Is it really faster\n75:  faster than both", out)  # order matters
    assert not lines_in_order("75: faster\n120: missing", out)
    assert lines_in_order("built in {time}\nok", "built in 0.42s\nok")
    assert output_shown("hello", "  hello  ")  # a single line keeps the substring rule
    assert not lines_in_order("x", None)


def test_wrong_transcript_output_is_stale_not_proven():
    claim = Claim(id="c01", kind="shell", span=Span(start_line=1, end_line=1, text="x"), command="$ seq 3\n1\n2\n4", expects="1\n2\n4")
    v = judge(claim, Evidence(claim_id="c01", kind="shell", command=claim.command, exit_code=0, stdout_tail="1\n2\n3"))
    assert v.status == "stale"
    good = judge(claim.model_copy(update={"expects": "1\n2\n3"}), Evidence(claim_id="c01", kind="shell", command=claim.command, exit_code=0, stdout_tail="1\n2\n3"))
    assert good.status == "proven"


def test_cli_catches_a_transcript_whose_output_lies(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from attest.cli import app

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "probe.md"
    path.write_text(
        "\n".join([
            "# Console probe", "",
            "Greet the world:", "", FENCE + "console", "$ echo hello", "goodbye", FENCE, "",
            "Count to three:", "", FENCE + "console", "$ seq 3", "1", "2", "4", FENCE, "",
            "And a correct one:", "", FENCE + "console", "$ echo ok", "ok", FENCE, "",
            "Ignore logs with this line in .gitignore:", "", FENCE, "log/", FENCE, "",
        ]),
        encoding="utf-8",
    )
    r = CliRunner().invoke(app, ["prove", str(path), "--runner", "none", "--home", str(tmp_path / "home")])
    assert r.exit_code == 1, r.output
    assert "1/3 proven | 2 stale" in r.output  # the .gitignore line is not run at all

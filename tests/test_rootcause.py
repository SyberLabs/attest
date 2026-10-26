"""Root causes and drift radius: grouping by what the code said, and finding the
other places in the docs that name the same thing."""

from __future__ import annotations

from types import SimpleNamespace

from attest.ingest import ingest_text
from attest.models import Claim, Evidence, PromptObservation, Span, Verdict
from attest.rootcause import group, named, normalize

FENCE = "`" * 4
PROMPT = "---\nname: {name}\ndescription: d\nrunvil: 0\n---\n\n# T\n\n## Main\n\n```lua\n{body}\n```\n"


def prompt_claim(cid, name, body, line):
    return Claim(id=cid, kind="prompt", span=Span(start_line=line, end_line=line, text="x"), command=PROMPT.format(name=name, body=body))


def failed(cid, kind, message):
    obs = PromptObservation(status="failed", error_kind=kind, message=message, host_version="t")
    return Verdict(claim_id=cid, status="broken", reason="broken: run failed", evidence=Evidence(claim_id=cid, kind="prompt", prompt=obs))


def parse_failed(cid, message):
    obs = PromptObservation(status="parse_error", error_kind="Frontmatter", message=message, host_version="t")
    return Verdict(claim_id=cid, status="broken", reason="broken: parse", evidence=Evidence(claim_id=cid, kind="prompt", prompt=obs))


def unverifiable(cid):
    return Verdict(claim_id=cid, status="unverifiable", reason="unverifiable: missing required Pack: web")


def test_named_reads_the_changed_thing_from_the_message():
    assert named("section `Only` prologue:13: runtime error: attempt to call a nil value (field 'offer_local')") == ("offer_local", "removed or renamed")
    assert named("invalid frontmatter: unknown field `plugins`, expected one of `name`") == ("plugins", "unknown field")
    assert named("bash: line 1: runvil-gw: command not found") == ("runvil-gw", "command not found")
    assert named("error: unexpected argument '--no-try' found") == ("--no-try", "unknown option")
    assert named("something else entirely") is None
    assert normalize("section `Main` prologue:14: x at line 3 ExecId(4)") == normalize("section `Other` epilog:9: x at line 70 ExecId(9)")


def test_group_collapses_one_change_and_finds_its_radius():
    lines = [
        "# Tools",
        "",
        "Offer a local tool with `tools.offer_local(name, fn)`; plain prose about offer_local is not a code span.",
        "",
        FENCE + "markdown", PROMPT.format(name="a", body="tools.offer_local('x', f)").rstrip("\n"), FENCE,
        "",
        FENCE + "markdown", PROMPT.format(name="b", body="tools.offer_local('y', g)").rstrip("\n"), FENCE,
        "",
        "Pass a Pack with `plugins:` in the frontmatter.",
        "",
        FENCE + "markdown", PROMPT.format(name="c", body="local w = tools.offer_local('z', h)").rstrip("\n"), FENCE,
        "",
    ]
    doc = ingest_text("\n".join(lines) + "\n", source="docs/12-tools.md")
    claims = [
        prompt_claim("c01", "a", "tools.offer_local('x', f)", 5),
        prompt_claim("c02", "b", "tools.offer_local('y', g)", 23),
        prompt_claim("c03", "c", "local w = tools.offer_local('z', h)", 43),
        prompt_claim("c04", "d", "return 1", 60),
    ]
    msg = "section `Main` prologue:{n}: runtime error: attempt to call a nil value (field 'offer_local')"
    verdicts = [
        failed("c01", "Lua", msg.format(n=12)),
        failed("c02", "Lua", msg.format(n=14)),
        unverifiable("c03"),
        parse_failed("c04", "invalid frontmatter: unknown field `plugins`, expected one of `name`"),
    ]
    result = SimpleNamespace(doc=doc, claims=claims, verdicts=verdicts)
    causes = group([result])
    assert [(c.label, c.name, len(c.claims)) for c in causes] == [("removed or renamed", "tools.offer_local", 2), ("unknown field", "plugins", 1)]
    offer = causes[0]
    assert [h.line for h in offer.prose] == [3]  # the code span, not the plain word
    assert [h.line for h in offer.unrun] == [43]  # the example that never ran but uses it
    plugins = causes[1]
    assert [h.line for h in plugins.prose] == [doc.lines.index("Pass a Pack with `plugins:` in the frontmatter.") + 1]
    # limited to new failures, only those are grouped, but the radius still covers the docs
    only = group([result], only={("docs/12-tools.md", "c02")})
    assert [(c.name, len(c.claims)) for c in only] == [("tools.offer_local", 1)]
    assert [h.line for h in only[0].prose] == [3]


def test_unnamed_failures_group_by_normalized_message():
    doc = ingest_text("# x\n", source="a.md")
    claims = [prompt_claim("c01", "a", "x", 1), prompt_claim("c02", "b", "x", 2)]
    verdicts = [
        failed("c01", "Quota", "call recursion exceeded cap of 8"),
        failed("c02", "Quota", "call recursion exceeded cap of 16"),
    ]
    causes = group([SimpleNamespace(doc=doc, claims=claims, verdicts=verdicts)])
    assert len(causes) == 1 and len(causes[0].claims) == 2 and causes[0].name is None
    assert causes[0].as_dict()["claims"][0] == {"doc": "a.md", "claim": "c01", "line": 1}


def test_a_removed_field_is_searched_in_the_form_the_examples_use():
    from attest.rootcause import qualified

    assert qualified("new", ["local m = messages.new()", "messages.new({})"]) == "messages.new"
    assert qualified("new", ["a.new()", "b.new()"]) == "new"  # two owners: stay bare
    assert qualified("offer_local", ["offer_local('x')"]) == "offer_local"
    lines = ["# C", "", "Build one with `messages.new()`; a `Thing.new` is unrelated, as is `new`.", ""]
    doc = ingest_text("\n".join(lines) + "\n", source="c.md")
    claims = [prompt_claim("c01", "a", "local m = messages.new()", 5)]
    verdicts = [failed("c01", "Lua", "attempt to call a nil value (field 'new')")]
    (cause,) = group([SimpleNamespace(doc=doc, claims=claims, verdicts=verdicts)])
    assert cause.name == "messages.new" and [h.line for h in cause.prose] == [3]

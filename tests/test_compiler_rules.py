"""Compiler rules for prompt docs (docs/CLI-RUNNER-SPEC.md): prompt and result
fences pair by rule with no model call, fragments are prose, nesting holds.

Offline: the Language guide chapter is a fixture copy under tests/fixtures/runvil/.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from attest.compiler import (
    CompileError,
    ClaimSet,
    LLMClaim,
    assemble,
    check_claims,
    classify_fence,
    compile_doc,
    fences_by_regex,
    is_prompt_fence,
    load_cached,
    plan_fences,
    recompile_claim,
    required_browser_lines,
    save_cache,
)
from attest.ingest import ingest, ingest_text
from attest.models import Fence
from attest.pipeline import claim_banner

ROOT = Path(__file__).resolve().parents[1]
CHAPTER = ROOT / "tests" / "fixtures" / "runvil" / "language-03-blocks-and-prose.md"

PROMPT = "---\nname: fences\ndescription: One Lua block between two prose blocks\nrunvil: 0\n---\n\n# Fences\n\n## Main\n\nBefore.\n\n```lua\nreturn 'hello'\n```\n\nAfter.\n"
PAIRED = f"# Chapter\n\nIntro.\n\n````markdown\n{PROMPT}````\n\n````text\nhello\n````\n\nAfter the pair.\n"


@pytest.fixture(autouse=True)
def no_model(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)


class FakeLLM:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.prompts = []

    def parse_claims(self, system, user):
        self.prompts.append(user)
        return self.answers.pop(0)


def chapter():
    doc = ingest(str(CHAPTER))
    return doc.model_copy(update={"browser_claims": False})


# -- the Language guide chapter -----------------------------------------------------------


def test_chapter_fixture_names_its_source_and_commit():
    first = CHAPTER.read_text(encoding="utf-8").splitlines()[0]
    assert first.startswith("<!-- source: https://github.com/cppalliance/runvil-docs/blob/dd8a2456f92961928f54f09c37ade0da1b956355/src/language/03-blocks-and-prose.md")


def test_chapter_compiles_by_rule_without_a_model():
    doc = chapter()
    claims = compile_doc(doc, llm=None, use_cache=False)
    prompts = [c for c in claims if c.kind == "prompt"]
    # the spec's 75 counts every markdown fence; 7 are fragments without frontmatter (prose).
    # 58 result fences follow a prompt directly and 4 more after one introducing line ending
    # in a colon ("and this message:"); 2 of the spec's 60 follow a fragment instead
    assert len(prompts) == 68
    assert sum(1 for c in prompts if c.expects) == 62
    assert all(c.expects_error is False for c in prompts)
    fragments = [c for c in claims if c.kind == "prose"]
    assert len(fragments) == 7
    # text fences with other prose between them and the prompt, or after a fragment, stay assert claims
    assert sum(1 for c in claims if c.kind == "assert") == 11
    assert not any(c.kind in ("shell", "browser") for c in claims)
    # ids are in doc order and every span is doc text
    assert [c.id for c in claims] == [f"c{i + 1:02d}" for i in range(len(claims))]
    for c in claims:
        assert c.span.text == "\n".join(doc.lines[c.span.start_line - 1 : c.span.end_line])


def test_chapter_first_example_is_the_fences_prompt_byte_for_byte():
    doc = chapter()
    first = fences_by_regex(doc)[0]
    assert first.kind == "prompt"
    assert first.command == doc.fences[0].text
    assert first.command.startswith("---\nname: fences\n")
    assert "```lua\nreturn 'hello'\n```" in first.command
    assert first.expects == "hello"
    assert first.expects == doc.fences[1].text
    # the span covers the prompt fence and its result fence
    assert first.span.start_line == doc.fences[0].start_line
    assert first.span.end_line == doc.fences[1].end_line
    assert claim_banner(first) == f"Running prompt `fences` (line {first.span.start_line})"


def test_four_backtick_fences_keep_nested_lua_fences_inside():
    doc = chapter()
    assert len(doc.fences) == 148
    assert all(f.lang in ("markdown", "text") for f in doc.fences)
    nested = [f for f in doc.fences if f.lang == "markdown" and "```lua" in f.text]
    assert len(nested) == 69
    # no fence content line is a bare three-backtick line left over from a split
    assert not any(f.text.strip() == "```" for f in doc.fences)
    # and a synthetic check: the closing of a nested fence does not close the outer one
    small = ingest_text("````markdown\n---\nrunvil: 0\n---\n# A\n## Main\n```lua\nreturn 1\n```\n````\n")
    assert len(small.fences) == 1
    assert small.fences[0].text.endswith("```")


def test_chapter_fails_closed_on_browser_heuristic_unless_declared_prompt_only():
    doc = ingest(str(CHAPTER))
    assert doc.browser_claims is True
    assert len(required_browser_lines(doc)) > 0
    with pytest.raises(CompileError, match="browser instructions require a compiler"):
        compile_doc(doc, llm=None, use_cache=False)
    assert required_browser_lines(doc.model_copy(update={"browser_claims": False})) == []


def test_chapter_claims_cache_and_revalidate(tmp_path):
    doc = chapter()
    claims = compile_doc(doc, llm=None, cache_dir=tmp_path)
    assert load_cached(doc, tmp_path) == claims
    path = next(tmp_path.glob("v*.json"))
    payload = json.loads(path.read_text(encoding="utf-8"))
    target = next(c for c in payload["claims"] if c["kind"] == "prompt" and c["expects"])
    target["expects"] = "forged"
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert load_cached(doc, tmp_path) is None


# -- the rules on small documents -----------------------------------------------------------


def test_prompt_fence_needs_markdown_tag_and_runvil_frontmatter():
    good = Fence(start_line=1, end_line=5, lang="markdown", text="---\nname: x\nrunvil: 0\n---\n# X")
    assert is_prompt_fence(good) and classify_fence(good) == "prompt"
    no_key = Fence(start_line=1, end_line=4, lang="markdown", text="---\nname: x\n---\n# X")
    assert not is_prompt_fence(no_key) and classify_fence(no_key) == "prose"
    key_after_close = Fence(start_line=1, end_line=5, lang="markdown", text="---\nname: x\n---\nrunvil: 0\n# X")
    assert not is_prompt_fence(key_after_close)
    no_frontmatter = Fence(start_line=1, end_line=2, lang="markdown", text="## Setup\n```lua\nx\n```")
    assert classify_fence(no_frontmatter) == "prose"
    wrong_tag = Fence(start_line=1, end_line=5, lang="yaml", text="---\nname: x\nrunvil: 0\n---\n# X")
    assert classify_fence(wrong_tag) == "prose"  # yaml is configuration, never a prompt or a command
    assert classify_fence(Fence(start_line=1, end_line=5, lang="python", text="---\nname: x\nrunvil: 0\n---\n# X")) == "assert"
    assert classify_fence(Fence(start_line=1, end_line=1, lang="lua", text="return 1")) == "prose"
    # untouched: shell, output and other-language fences classify as before
    assert classify_fence(Fence(start_line=1, end_line=1, lang="", text="ls")) == "shell"
    assert classify_fence(Fence(start_line=1, end_line=1, lang="text", text="out")) == "assert"
    assert classify_fence(Fence(start_line=1, end_line=1, lang="python", text="x = 1")) == "assert"


def test_pairing_requires_only_blank_lines_between():
    doc = ingest_text(PAIRED)
    plan = plan_fences(doc)
    assert [p.role for p in plan] == ["prompt", "result"]
    assert plan[0].result is doc.fences[1]
    claims = fences_by_regex(doc)
    assert len(claims) == 1 and claims[0].expects == "hello"
    # two blank lines still pair
    two = ingest_text(PAIRED.replace("````\n\n````text", "````\n\n\n````text"))
    assert fences_by_regex(two)[0].expects == "hello"
    # one introducing line that ends in a colon still pairs: the guides write
    # "the run result is:" and "and this message:" before the fence
    intro = ingest_text(PAIRED.replace("````\n\n````text", "````\n\nThe run fails with kind `List` and this message:\n\n````text"))
    assert fences_by_regex(intro)[0].expects == "hello"
    # any other prose between breaks the pair: the text fence becomes an assert claim
    broken = ingest_text(PAIRED.replace("````\n\n````text", "````\n\nThe result is shown below.\n\n````text"))
    kinds = [c.kind for c in fences_by_regex(broken)]
    assert kinds == ["prompt", "assert"]
    assert fences_by_regex(broken)[0].expects is None
    two_lines = ingest_text(PAIRED.replace("````\n\n````text", "````\n\nFirst line.\nThe result is:\n\n````text"))
    assert fences_by_regex(two_lines)[0].expects is None
    # a lua fence after a prompt is not a result
    lua = ingest_text(PAIRED.replace("````text\nhello\n````", "````lua\nreturn 1\n````"))
    assert [c.kind for c in fences_by_regex(lua)] == ["prompt", "prose"]


def test_prompt_claims_are_independent_runs_after_the_last_shell_step():
    doc = ingest_text(PAIRED + "\n" + PAIRED.split("\n", 3)[3])
    claims = compile_doc(doc, llm=None, use_cache=False)
    assert [c.kind for c in claims] == ["prompt", "prompt"]
    # a broken example never hides the one after it
    assert claims[1].depends_on == []
    with_shell = ingest_text("```bash\necho setup\n```\n\n" + PAIRED)
    claims = compile_doc(with_shell, llm=None, use_cache=False)
    assert [c.kind for c in claims] == ["shell", "prompt"]
    assert claims[1].depends_on == ["c01"]


def test_model_drafts_on_rule_placed_fences_are_dropped_not_rejected(tmp_path):
    text = "Run it:\n\n```bash\necho hi\n```\n\n" + PAIRED
    doc = ingest_text(text)
    bash = doc.fences[0]
    prompt_fence, result_fence = doc.fences[1], doc.fences[2]
    drafts = [
        LLMClaim(kind="prose", start_line=1, end_line=1, text="Run it:", command=None, objective=None, expects=None),
        LLMClaim(kind="shell", start_line=bash.start_line, end_line=bash.end_line, text=bash.text, command=bash.text, objective=None, expects=None),
        # the model wrongly calls the prompt a shell block and the result an assert: both are ignored
        LLMClaim(kind="shell", start_line=prompt_fence.start_line, end_line=prompt_fence.end_line, text="wrong", command="wrong", objective=None, expects=None),
        LLMClaim(kind="assert", start_line=result_fence.start_line, end_line=result_fence.end_line, text="hello", command=None, objective=None, expects="hello"),
    ]
    assert check_claims(doc, drafts) == []
    claims = assemble(doc, drafts)
    assert [c.kind for c in claims] == ["prose", "shell", "prompt"]
    assert claims[2].command == prompt_fence.text and claims[2].expects == "hello"
    assert claims[2].depends_on == ["c02"]
    compiled = compile_doc(doc, llm=FakeLLM(ClaimSet(claims=drafts)), cache_dir=tmp_path)
    assert compiled == claims
    assert load_cached(doc, tmp_path) == claims


def test_model_must_still_cover_shell_fences_in_a_prompt_doc():
    text = "```bash\necho hi\n```\n\n" + PAIRED
    doc = ingest_text(text)
    drafts = [LLMClaim(kind="prose", start_line=1, end_line=1, text=doc.lines[0], command=None, objective=None, expects=None)]
    problems = check_claims(doc, drafts)
    assert any("exactly one verbatim shell claim" in p for p in problems)


def test_recompile_prompt_claim_follows_an_edited_result_fence_without_a_model():
    doc = ingest_text(PAIRED)
    claim = fences_by_regex(doc)[0]
    new_doc = ingest_text(PAIRED.replace("````text\nhello\n````", "````text\ngoodbye\n````"))
    fresh = recompile_claim(new_doc, claim, llm=FakeLLM())
    assert fresh.kind == "prompt" and fresh.id == claim.id
    assert fresh.expects == "goodbye"
    assert fresh.command == claim.command
    # a claim whose span now sits on the result fence resolves to the same prompt claim
    moved = claim.model_copy(update={"span": claim.span.model_copy(update={"start_line": new_doc.fences[1].start_line})})
    assert recompile_claim(new_doc, moved, llm=FakeLLM()).expects == "goodbye"


def test_save_cache_refuses_a_prompt_claim_that_lost_its_result(tmp_path):
    doc = ingest_text(PAIRED)
    claims = fences_by_regex(doc)
    bad = [claims[0].model_copy(update={"expects": None})]
    with pytest.raises(CompileError, match="not represented exactly once"):
        save_cache(doc, bad, tmp_path)


@pytest.mark.parametrize(
    "name,prompts,paired,asserts,prose",
    [
        ("language-08-jump-and-call", 33, 33, 7, 5),
        ("language-09-the-store", 46, 44, 23, 18),
    ],
)
def test_other_chapters_compile_by_rule(name, prompts, paired, asserts, prose):
    doc = ingest(str(ROOT / "tests" / "fixtures" / "runvil" / f"{name}.md")).model_copy(update={"browser_claims": False})
    claims = compile_doc(doc, llm=None, use_cache=False)
    assert sum(1 for c in claims if c.kind == "prompt") == prompts
    assert sum(1 for c in claims if c.kind == "prompt" and c.expects) == paired
    assert sum(1 for c in claims if c.kind == "assert") == asserts
    assert sum(1 for c in claims if c.kind == "prose") == prose
    assert not any(c.kind in ("shell", "browser") for c in claims)


def test_argument_string_in_the_introducing_line_becomes_the_claims_args():
    intro = ingest_text(PAIRED.replace("````\n\n````text", "````\n\nRun it with the argument string `later canned value`:\n\n````text"))
    claim = fences_by_regex(intro)[0]
    assert claim.args == "later canned value" and claim.expects == "hello"
    plain = fences_by_regex(ingest_text(PAIRED))[0]
    assert plain.args is None
    # a cached claim must carry the same args to count as the fence's claim
    from attest.compiler import save_cache
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        with pytest.raises(CompileError, match="not represented exactly once"):
            save_cache(intro, [claim.model_copy(update={"args": None})], Path(d))
        save_cache(intro, [claim], Path(d))
        assert load_cached(intro, Path(d)) == [claim]


def test_untagged_config_and_json_fences_are_never_run():
    from attest.compiler import looks_like_config, looks_like_json

    toml = Fence(start_line=1, end_line=3, lang="", text='config-version = 0\n\n[server]\nbind = "127.0.0.1:8081"')
    unit = Fence(start_line=1, end_line=3, lang="", text="ExecStart=/usr/local/bin/runvil-gateway --config /etc/runvil/gateway.toml --profile main\nRestart=on-failure\nRestartSec=5")
    table = Fence(start_line=1, end_line=2, lang="", text="[[endpoint]]\nid = \"openai\"")
    response = Fence(start_line=1, end_line=1, lang="", text='{"profile": "travel", "restart_required": true}')
    command = Fence(start_line=1, end_line=1, lang="", text="curl -H \"Authorization: Bearer $GATEWAY_KEY\" http://127.0.0.1:8081/v1/models")
    install = Fence(start_line=1, end_line=1, lang="", text="cargo install gateway")
    env_prefix = Fence(start_line=1, end_line=1, lang="", text="FOO=1 ./run")
    assert looks_like_config(toml.text) and looks_like_config(unit.text) and looks_like_config(table.text)
    assert not looks_like_config(command.text) and not looks_like_config(install.text) and not looks_like_config(env_prefix.text)
    assert looks_like_json(response.text) and not looks_like_json(command.text)
    assert [classify_fence(f) for f in (toml, unit, table, response, command, install, env_prefix)] == ["prose", "prose", "prose", "assert", "shell", "shell", "shell"]
    # a tagged config language is prose too; a tagged json response stays an assert
    assert classify_fence(Fence(start_line=1, end_line=1, lang="toml", text="a = 1")) == "prose"
    assert classify_fence(Fence(start_line=1, end_line=1, lang="json", text="{}")) == "assert"
    # the Gateway chapter compiles to its six commands and the systemd snippet as prose
    doc = ingest(str(ROOT / "tests" / "fixtures" / "runvil" / "gateway-01-install-and-run.md"))
    assert [c.kind for c in fences_by_regex(doc)] == ["shell"] * 6 + ["prose"]


def test_frontmatter_only_prompt_fences_are_fragments_unless_a_result_follows():
    frontmatter = "---\nname: greeter\ndescription: says hi\nrunvil: 0\n---\n"
    alone = ingest_text(f"````markdown\n{frontmatter}````\n")
    assert [c.kind for c in fences_by_regex(alone)] == ["prose"]
    # the parser refuses it for lacking a title, and a doc that shows that failure pairs it with the message
    documented = ingest_text(f"````markdown\n{frontmatter}````\n\n````text\nprompt requires an H1 title\n````\n")
    claims = fences_by_regex(documented)
    assert [c.kind for c in claims] == ["prompt"] and claims[0].expects == "prompt requires an H1 title"
    whole = ingest_text(f"````markdown\n{frontmatter}\n# Greeter\n\n## Main\n\n```lua\nreturn 1\n```\n````\n")
    assert [c.kind for c in fences_by_regex(whole)] == ["prompt"]


def test_sends_are_read_from_the_prose_after_an_example():
    from attest.compiler import sends_after

    body = PROMPT.rstrip("\n")
    lines = [
        "# C", "",
        "````markdown", body, "````", "",
        "The epilog sends `Name one ocean.` to the model, and `models.infer(prose)` returns the reply.",
        "The prompt asks `Name one planet.` first, and then `Name one river.` from `## Answer`.",
        "It sends it with `models.infer`, which is a reference, not a message.",
        "",
    ]
    doc = ingest_text("\n".join(lines) + "\n")
    claim = fences_by_regex(doc)[0]
    assert claim.sends == ["Name one ocean.", "Name one planet.", "Name one river."]
    assert sends_after(doc, doc.fences[0].end_line, None) == ("Name one ocean.", "Name one planet.", "Name one river.")
    # a cached claim must carry the same sends to count as the fence's claim
    from attest.compiler import save_cache
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        with pytest.raises(CompileError, match="not represented exactly once"):
            save_cache(doc, [claim.model_copy(update={"sends": []})], Path(d))
    # no prose after the example, or prose without a message: nothing
    assert fences_by_regex(ingest_text(PAIRED))[0].sends == []

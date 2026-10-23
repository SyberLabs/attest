import json
from pathlib import Path

import pytest

from attest.compiler import (
    ClaimSet,
    LLMClaim,
    assemble,
    chain_dependencies,
    check_claims,
    classify_fence,
    compile_doc,
    fences_by_regex,
    recompile_claim,
)
from attest.ingest import ingest, ingest_text
from attest.models import Fence

ROOT = Path(__file__).resolve().parents[1]
FLASK = ROOT / "demo" / "flask.md"


class FakeLLM:
    """Returns canned ClaimSets in order; records the prompts it saw."""

    def __init__(self, *answers: ClaimSet):
        self.answers = list(answers)
        self.prompts: list[str] = []

    def parse_claims(self, system, user):
        self.prompts.append(user)
        if not self.answers:
            raise AssertionError("fake LLM called more times than expected")
        return self.answers.pop(0)


def good_drafts(doc):
    f1, f2, f3 = doc.fences
    browser_line = next(i + 1 for i, ln in enumerate(doc.lines) if ln.startswith("This launches"))
    return [
        LLMClaim(kind="prose", start_line=1, end_line=1, text=doc.lines[0], command=None, objective=None, expects=None),
        LLMClaim(kind="shell", start_line=f1.start_line, end_line=f1.end_line, text=f1.text, command=f1.text, objective=None, expects=None),
        LLMClaim(kind="shell", start_line=f2.start_line, end_line=f2.end_line, text=f2.text, command=f2.text, objective=None, expects=None),
        LLMClaim(
            kind="shell",
            start_line=f3.start_line,
            end_line=f3.end_line,
            text=f3.text,
            command=f3.text,
            objective=None,
            expects="Running on http://127.0.0.1:5000",
        ),
        LLMClaim(
            kind="browser",
            start_line=browser_line,
            end_line=browser_line,
            text=doc.lines[browser_line - 1],
            command=None,
            objective="Open http://127.0.0.1:5000/ and check the page shows the text 'Hello, World!'.",
            expects="Hello, World!",
        ),
    ]


def test_classify_fence():
    assert classify_fence(Fence(start_line=1, end_line=1, lang="bash", text="ls")) == "shell"
    assert classify_fence(Fence(start_line=1, end_line=1, lang="", text="ls")) == "shell"
    assert classify_fence(Fence(start_line=1, end_line=1, lang="bash", text="# only a comment")) == "assert"
    assert classify_fence(Fence(start_line=1, end_line=1, lang="text", text="Hello")) == "assert"
    assert classify_fence(Fence(start_line=1, end_line=2, lang="python", text="x = 1\nprint(x)")) == "assert"


def test_fences_by_regex_verbatim():
    doc = ingest(str(FLASK))
    claims = fences_by_regex(doc)
    assert [c.command for c in claims] == [f.text for f in doc.fences]
    assert all(c.kind == "shell" for c in claims)
    assert [c.id for c in claims] == ["c01", "c02", "c03"]


def test_check_claims_catches_paraphrase_and_bad_span():
    doc = ingest(str(FLASK))
    drafts = good_drafts(doc)
    assert check_claims(doc, drafts) == []
    bad = list(drafts)
    bad[1] = bad[1].model_copy(update={"command": "pip install flask"})  # case changed
    problems = check_claims(doc, bad)
    assert any("not verbatim" in p for p in problems)
    bad2 = list(drafts)
    bad2[0] = bad2[0].model_copy(update={"end_line": 999})
    assert any("outside" in p for p in check_claims(doc, bad2))
    bad3 = list(drafts)
    bad3[0] = bad3[0].model_copy(update={"text": "something else"})
    assert any("does not match" in p for p in check_claims(doc, bad3))


def test_compile_happy_path(tmp_path):
    doc = ingest(str(FLASK))
    llm = FakeLLM(ClaimSet(claims=good_drafts(doc)))
    claims = compile_doc(doc, llm=llm, cache_dir=tmp_path)
    kinds = [c.kind for c in claims]
    assert kinds == ["prose", "shell", "shell", "shell", "browser"]
    assert [c.id for c in claims] == ["c01", "c02", "c03", "c04", "c05"]
    shell = [c for c in claims if c.kind == "shell"]
    assert [c.command for c in shell] == [f.text for f in doc.fences]
    assert shell[2].expects == "Running on http://127.0.0.1:5000"
    b = claims[4]
    assert b.expects == "Hello, World!"
    assert b.store_as == "c05_label"
    assert b.depends_on == ["c04"]
    assert claims[3].depends_on == ["c03"]
    assert claims[1].depends_on == []
    # every span's text is the doc's text
    for c in claims:
        assert c.span.text == "\n".join(doc.lines[c.span.start_line - 1 : c.span.end_line])
    # cached by hash
    cached = json.loads((tmp_path / f"{doc.sha256}.json").read_text())
    assert cached["meta"]["mode"] == "llm"
    assert len(cached["claims"]) == 5
    # second call hits the cache, not the model
    again = compile_doc(doc, llm=FakeLLM(), cache_dir=tmp_path)
    assert again == claims


def test_compile_retries_with_failing_line_quoted(tmp_path):
    doc = ingest(str(FLASK))
    drafts = good_drafts(doc)
    bad = list(drafts)
    bad[1] = bad[1].model_copy(update={"command": "pip install flask"})
    llm = FakeLLM(ClaimSet(claims=bad), ClaimSet(claims=drafts))
    claims = compile_doc(doc, llm=llm, cache_dir=tmp_path)
    assert len(llm.prompts) == 2
    assert "pip install Flask" in llm.prompts[1]
    assert "previous answer had these problems" in llm.prompts[1]
    assert [c.command for c in claims if c.kind == "shell"] == [f.text for f in doc.fences]


def test_compile_falls_back_to_regex_for_fences(tmp_path):
    doc = ingest(str(FLASK))
    drafts = good_drafts(doc)
    bad = [d.model_copy(update={"command": "echo nope"}) if d.kind == "shell" else d for d in drafts]
    llm = FakeLLM(ClaimSet(claims=bad), ClaimSet(claims=bad))
    claims = compile_doc(doc, llm=llm, cache_dir=tmp_path)
    assert [c.command for c in claims if c.kind == "shell"] == [f.text for f in doc.fences]
    # browser and prose claims from the model survive
    assert [c.kind for c in claims] == ["prose", "shell", "shell", "shell", "browser"]
    cached = json.loads((tmp_path / f"{doc.sha256}.json").read_text())
    assert cached["meta"]["mode"] == "fallback"


def test_compile_without_key_or_model_uses_fences_only(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    doc = ingest(str(FLASK))
    claims = compile_doc(doc, llm=None, cache_dir=tmp_path, use_cache=False)
    assert [c.kind for c in claims] == ["shell", "shell", "shell"]
    assert claims[1].depends_on == ["c01"]


def test_invented_shell_claim_without_fence_becomes_prose():
    doc = ingest_text("# t\nRun `ls` to list files.\n")
    drafts = [
        LLMClaim(kind="shell", start_line=2, end_line=2, text=doc.lines[1], command="ls", objective=None, expects=None)
    ]
    claims = assemble(doc, drafts)
    assert claims[0].kind == "prose"


def test_chain_dependencies_browser_chain():
    doc = ingest_text("a\nb\nc\nd\n")
    drafts = [
        LLMClaim(kind="browser", start_line=1, end_line=1, text="a", command=None, objective="Open x, see A", expects="A"),
        LLMClaim(kind="browser", start_line=2, end_line=2, text="b", command=None, objective="Click B", expects="B"),
    ]
    claims = chain_dependencies(assemble(doc, drafts))
    assert claims[0].depends_on == []
    assert claims[1].depends_on == ["c01"]


def test_recompile_fence_without_model():
    doc = ingest(str(FLASK))
    claims = fences_by_regex(doc)
    target = claims[0]
    new_text = "\n".join(doc.lines).replace("pip install Flask", "pip install Flask==3.1.0")
    new_doc = ingest_text(new_text)
    fresh = recompile_claim(new_doc, target, llm=FakeLLM())
    assert fresh.id == target.id
    assert fresh.command == "pip install Flask==3.1.0"
    assert fresh.kind == "shell"


def test_recompile_browser_with_model():
    doc = ingest(str(FLASK))
    drafts = good_drafts(doc)
    claims = assemble(doc, drafts)
    b = claims[4]
    ln = b.span.start_line
    lines = list(doc.lines)
    lines[ln - 1] = lines[ln - 1].replace("Hello, World!", "Hello, Flask!")
    new_doc = ingest_text("\n".join(lines))
    llm = FakeLLM(
        ClaimSet(
            claims=[
                LLMClaim(
                    kind="browser",
                    start_line=ln,
                    end_line=ln,
                    text=lines[ln - 1],
                    command=None,
                    objective="Open the page and check it shows 'Hello, Flask!'.",
                    expects="Hello, Flask!",
                )
            ]
        )
    )
    fresh = recompile_claim(new_doc, b, llm=llm)
    assert fresh.id == b.id
    assert fresh.expects == "Hello, Flask!"
    assert fresh.depends_on == b.depends_on
    assert fresh.store_as == b.store_as
    assert f"{ln}" in llm.prompts[0]


@pytest.mark.skipif("ATTEST_LIVE" not in __import__("os").environ, reason="needs OPENAI_API_KEY and network")
def test_live_compile_all_targets(tmp_path):
    import yaml

    targets = yaml.safe_load((ROOT / "targets.yaml").read_text())
    for t in targets["targets"]:
        doc = ingest(str(ROOT / t["doc"]))
        claims = compile_doc(doc, cache_dir=tmp_path, use_cache=False)
        shell_cmds = [c.command for c in claims if c.kind == "shell"]
        for f in doc.fences:
            if classify_fence(f) == "shell":
                assert f.text in shell_cmds, (t["name"], f.text)

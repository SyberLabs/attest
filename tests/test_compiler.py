import json
from pathlib import Path

import pytest

from attest.compiler import (
    CACHE_SCHEMA_VERSION,
    ClaimSet,
    CompileError,
    LLMClaim,
    assemble,
    chain_dependencies,
    check_claims,
    classify_fence,
    compile_doc,
    fences_by_regex,
    load_cached,
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
    cached = json.loads((tmp_path / f"v{CACHE_SCHEMA_VERSION}-{doc.sha256}.json").read_text())
    assert cached["cache_key"] == f"compiler-v{CACHE_SCHEMA_VERSION}:{doc.sha256}"
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


def test_compile_rejects_incomplete_fallback_after_retry(tmp_path):
    doc = ingest(str(FLASK))
    drafts = good_drafts(doc)
    bad = [d.model_copy(update={"command": "echo nope"}) if d.kind == "shell" else d for d in drafts]
    llm = FakeLLM(ClaimSet(claims=bad), ClaimSet(claims=bad))
    with pytest.raises(CompileError, match="incomplete compilation after retry"):
        compile_doc(doc, llm=llm, cache_dir=tmp_path)
    assert not list(tmp_path.glob("*.json"))


def test_compile_rejects_omitted_browser_claim(tmp_path):
    doc = ingest_text("Open https://example.test and check the page shows Ready.\n")
    prose = LLMClaim(kind="prose", start_line=1, end_line=1, text=doc.lines[0], command=None, objective=None, expects=None)
    llm = FakeLLM(ClaimSet(claims=[prose]), ClaimSet(claims=[prose]))
    with pytest.raises(CompileError, match="browser instruction at line 1 is missing"):
        compile_doc(doc, llm=llm, cache_dir=tmp_path)


def test_compile_rejects_unresolved_browser_claim(tmp_path):
    doc = ingest_text("Open https://example.test and check the page shows Ready.\n")
    browser = LLMClaim(
        kind="browser", start_line=1, end_line=1, text=doc.lines[0],
        command=None, objective="Open the page and check the label", expects=None,
    )
    llm = FakeLLM(ClaimSet(claims=[browser]), ClaimSet(claims=[browser]))
    with pytest.raises(CompileError, match="no exact expected text"):
        compile_doc(doc, llm=llm, cache_dir=tmp_path)


def test_compile_rejects_browser_expectation_absent_from_source(tmp_path):
    doc = ingest_text("Open https://example.test and check the page shows Ready.\n")
    browser = LLMClaim(
        kind="browser", start_line=1, end_line=1, text=doc.lines[0],
        command=None, objective="Open the page and check its heading", expects="Admin access",
    )
    llm = FakeLLM(ClaimSet(claims=[browser]), ClaimSet(claims=[browser]))
    with pytest.raises(CompileError, match="expected browser text is not present"):
        compile_doc(doc, llm=llm, cache_dir=tmp_path)


def test_compile_rejects_omitted_browser_result_line(tmp_path):
    doc = ingest_text("Open https://example.test in the browser.\nThe page shows Ready.\n")
    browser = LLMClaim(
        kind="browser", start_line=1, end_line=1, text=doc.lines[0],
        command=None, objective="Open the page and check Ready", expects="Ready",
    )
    llm = FakeLLM(ClaimSet(claims=[browser]), ClaimSet(claims=[browser]))
    with pytest.raises(CompileError, match="browser instruction at line 2 is missing"):
        compile_doc(doc, llm=llm, cache_dir=tmp_path)


def test_compile_without_model_fails_closed_on_browser_instructions(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    doc = ingest(str(FLASK))
    with pytest.raises(CompileError, match="browser instructions require a compiler"):
        compile_doc(doc, llm=None, cache_dir=tmp_path, use_cache=False)
    warning = capsys.readouterr().err
    assert str(doc.source) in warning
    assert "browser steps were not compiled" in warning
    assert "no OPENAI_API_KEY and no cache" in warning


def test_compile_without_model_compiles_fenced_document(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    doc = ingest_text("```bash\necho ok\n```\n")
    claims = compile_doc(doc, llm=None, cache_dir=tmp_path, use_cache=False)
    assert [c.kind for c in claims] == ["shell"]
    assert claims[0].command == "echo ok"


def test_invented_shell_claim_without_fence_becomes_prose():
    doc = ingest_text("# t\nRun `ls` to list files.\n")
    drafts = [
        LLMClaim(kind="shell", start_line=2, end_line=2, text=doc.lines[1], command="ls", objective=None, expects=None)
    ]
    claims = assemble(doc, drafts)
    assert claims[0].kind == "prose"


def test_cached_command_must_match_current_source(tmp_path):
    doc = ingest_text("```bash\necho expected\n```\n")
    compile_doc(doc, llm=None, cache_dir=tmp_path)
    path = tmp_path / f"v{CACHE_SCHEMA_VERSION}-{doc.sha256}.json"
    payload = json.loads(path.read_text())
    payload["claims"][0]["command"] = "echo forged"
    path.write_text(json.dumps(payload))
    assert load_cached(doc, tmp_path) is None


def test_cached_source_hash_must_match_current_lines(tmp_path):
    doc = ingest_text("Intro text\n\n```bash\necho expected\n```\n")
    compile_doc(doc, llm=None, cache_dir=tmp_path)
    changed = doc.model_copy(update={"lines": ["Changed intro", *doc.lines[1:]]})
    assert load_cached(changed, tmp_path) is None


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


def test_requote_follows_a_replaced_quote_without_a_model():
    from attest.compiler import requote_claim
    from attest.models import Claim, Span

    old = Claim(id="c07", kind="browser", span=Span(start_line=3, end_line=3, text='The page shows the heading "Vite + React".'),
                objective='Open the page and check for the heading "Vite + React".', expects="Vite + React", store_as="c07_label")
    new_span = Span(start_line=3, end_line=3, text='The page shows the heading "Get started".')
    fresh = requote_claim(old, new_span)
    assert fresh.expects == "Get started" and fresh.objective == 'Open the page and check for the heading "Get started".'
    # unchanged quote, or an ambiguous edit, keeps the claim and only moves the span
    assert requote_claim(old, old.span).expects == "Vite + React"
    two = Span(start_line=3, end_line=3, text='Shows "A" and "B".')
    assert requote_claim(old, two).expects == "Vite + React"

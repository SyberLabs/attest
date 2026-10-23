from pathlib import Path

import pytest

from attest.asker import CARDS, SYSTEM_PROMPT, answer_doc, ask_and_save, slugify
from attest.compiler import compile_doc, load_cached
from attest.grounding import card_for
from attest.ingest import ingest

ROOT = Path(__file__).resolve().parents[1]
ANSWERS = ROOT / "demo" / "answers"

FAKE_ANSWER = """1. Go to https://github.com and sign in.
2. Open your repository and click the "Settings" tab.
3. In the left sidebar, click "Code security".
4. Next to "Secret scanning", click "Enable".
"""


def test_slugify():
    assert slugify("GitHub Secret Scanning") == "github-secret-scanning"
    with pytest.raises(ValueError):
        slugify("---")


def test_ask_and_save_writes_frontmatter(tmp_path):
    seen = {}

    def fake(model, system, question):
        seen.update(model=model, system=system, question=question)
        return FAKE_ANSWER

    p = ask_and_save("How do I enable secret scanning?", "Secret Scanning", out_dir=tmp_path, model="test-model", asker=fake)
    assert p == tmp_path / "secret-scanning.md"
    assert seen["system"] == SYSTEM_PROMPT
    assert seen["model"] == "test-model"
    text = p.read_text(encoding="utf-8")
    assert text.startswith("---\ncard: secret-scanning\nmodel: test-model\nasked_at: 20")
    assert "# How do I enable secret scanning?" in text
    assert text.endswith(FAKE_ANSWER)
    doc = ingest(str(p))
    assert card_for(doc) == "secret-scanning"
    assert doc.fences == []


def test_answer_doc_shape():
    text = answer_doc("Q?", "1. step", "c", "m", "2026-10-23T00:00:00+00:00")
    assert text.split("\n")[0] == "---"
    assert "# Q?" in text


def test_cards_are_the_five_from_the_doc():
    assert len(CARDS) == 5
    assert "github-secret-scanning" in CARDS


@pytest.mark.skipif(not ANSWERS.exists() or not any(ANSWERS.glob("*.md")), reason="answers not generated yet")
@pytest.mark.parametrize("card", sorted(CARDS))
def test_saved_answers_ingest_and_compile_from_cache(card):
    """Acceptance: each saved answer ingests and compiles (from the committed cache) into browser claims with expects labels."""
    p = ANSWERS / f"{card}.md"
    assert p.exists(), f"{card} missing; run attest ask"
    doc = ingest(str(p))
    assert card_for(doc) == card
    assert any(ln.startswith("model:") for ln in doc.lines[:6])
    assert any(ln.startswith("asked_at:") for ln in doc.lines[:6])
    claims = load_cached(doc, ROOT / "cache" / "compiler")
    assert claims is not None, "compiler cache missing for this answer; run attest prove on it once"
    browser = [c for c in claims if c.kind == "browser"]
    assert len(browser) >= 3, [c.kind for c in claims]
    labelled = [c for c in browser if c.expects]
    assert len(labelled) >= len(browser) // 2
    for c in browser:
        assert c.store_as == f"{c.id}_label"
        assert c.objective
    # cache hit only, no model call
    assert compile_doc(doc, llm=None, cache_dir=ROOT / "cache" / "compiler") == claims

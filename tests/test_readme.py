from pathlib import Path

import pytest
import yaml

from attest.compiler import CompileError, compile_doc, load_cached
from attest.ingest import ingest

ROOT = Path(__file__).resolve().parents[1]


def test_readme_has_no_em_dashes():
    # demo/answers/*.md are verbatim model output and are left as written
    files = [ROOT / "README.md", ROOT / "NOTES.md", *(ROOT / "demo").glob("*.md"), *(ROOT / "attest").rglob("*.py"), *(ROOT / "tests").rglob("*.md")]
    for p in files:
        assert "—" not in p.read_text(encoding="utf-8"), p


def test_readme_rejects_incomplete_legacy_cache_and_fails_closed_offline(monkeypatch):
    doc = ingest(str(ROOT / "README.md"))
    cache_dir = ROOT / "cache" / "compiler"
    legacy_cache = cache_dir / f"{doc.sha256}.json"
    assert legacy_cache.exists()
    assert load_cached(doc, cache_dir) is None

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(CompileError, match="browser instructions require a compiler"):
        compile_doc(doc, llm=None, cache_dir=cache_dir)


def test_targets_pinned_and_present():
    targets = yaml.safe_load((ROOT / "targets.yaml").read_text(encoding="utf-8"))["targets"]
    names = [t["name"] for t in targets]
    assert names == ["flask", "vite", "attest"]
    for t in targets:
        assert (ROOT / t["doc"]).exists(), t["doc"]
        if t["name"] != "attest":
            assert len(t["commit"]) == 40
            assert t["commit"] in (ROOT / t["doc"]).read_text(encoding="utf-8")
        doc = ingest(str(ROOT / t["doc"]))
        claims = load_cached(doc, ROOT / "cache" / "compiler")
        if claims is None:
            # Invalid legacy cache claims must not be trusted; absent a model, the
            # uncovered browser promise is a compile failure rather than a cache hit.
            with pytest.raises(CompileError):
                compile_doc(doc, llm=None, cache_dir=ROOT / "cache" / "compiler")
        else:
            assert compile_doc(doc, llm=None, cache_dir=ROOT / "cache" / "compiler") == claims

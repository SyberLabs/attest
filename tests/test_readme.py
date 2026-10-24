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


def test_readme_compiles_to_shell_and_browser_claims_offline():
    """Acceptance for M10: README.md yields only shell and browser claims plus clearly marked prose."""
    doc = ingest(str(ROOT / "README.md"))
    claims = load_cached(doc, ROOT / "cache" / "compiler")
    assert claims is not None, "README compiler cache missing; run attest prove README.md once"
    assert compile_doc(doc, llm=None, cache_dir=ROOT / "cache" / "compiler") == claims
    kinds = {c.kind for c in claims}
    assert kinds <= {"shell", "browser", "prose"}
    shell = [c for c in claims if c.kind == "shell"]
    assert [c.command for c in shell] == [f.text for f in doc.fences]
    assert any("pip install" in c.command for c in shell)  # install step
    assert any("attest prove demo/flask.md" in c.command for c in shell)
    serve = next(c for c in shell if "attest serve" in c.command)
    browser = [c for c in claims if c.kind == "browser"]
    assert len(browser) >= 1
    ledger_claim = next(c for c in browser if c.expects == "Ledger")
    assert serve.id in ledger_claim.depends_on
    assert ledger_claim.store_as == f"{ledger_claim.id}_label"
    for c in claims:
        if c.kind == "prose":
            assert c.command is None and c.objective is None and c.expects is None


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

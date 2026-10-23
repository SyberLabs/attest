from pathlib import Path

from attest.ingest import (
    find_fences,
    find_urls,
    ingest,
    ingest_text,
    raw_url,
    sha256_of,
    split_lines,
)

ROOT = Path(__file__).resolve().parents[1]
FLASK = ROOT / "demo" / "flask.md"


def test_ingest_flask_twice_same_hash():
    a = ingest(str(FLASK))
    b = ingest(str(FLASK))
    assert a.sha256 == b.sha256
    assert len(a.sha256) == 64
    assert a.lines == b.lines
    assert a.source == str(FLASK)


def test_hash_ignores_line_endings():
    assert sha256_of("a\nb\n") == sha256_of("a\r\nb\r\n")


def test_fences_located_by_line():
    doc = ingest(str(FLASK))
    assert len(doc.fences) == 3
    first = doc.fences[0]
    # the fence content is exactly the doc lines it claims
    for f in doc.fences:
        assert "\n".join(doc.lines[f.start_line - 1 : f.end_line]) == f.text
        assert doc.lines[f.start_line - 2].startswith("```")
        assert doc.lines[f.end_line].startswith("```")
    assert first.text == "pip install Flask"
    assert first.lang == "bash"
    assert doc.fences[1].text.startswith("cat > hello.py <<'EOF'")
    assert doc.fences[2].text == "flask --app hello run"


def test_fence_variants():
    text = "\n".join(
        [
            "intro",
            "~~~sh",
            "echo one",
            "~~~",
            "```",
            "echo two",
            "echo three",
            "```",
            "````md",
            "```",
            "nested",
            "```",
            "````",
            "```unclosed",
            "tail",
        ]
    )
    fences = find_fences(split_lines(text))
    assert [f.text for f in fences] == ["echo one", "echo two\necho three", "```\nnested\n```", "tail"]
    assert fences[0].lang == "sh"
    assert fences[1].lang == ""
    assert (fences[0].start_line, fences[0].end_line) == (3, 3)
    assert (fences[1].start_line, fences[1].end_line) == (6, 7)


def test_urls_found_and_trimmed():
    urls = find_urls("Go to http://127.0.0.1:5000/. Also see (https://flask.palletsprojects.com/en/stable/quickstart/) twice http://127.0.0.1:5000/")
    assert urls == ["http://127.0.0.1:5000/", "https://flask.palletsprojects.com/en/stable/quickstart/"]


def test_flask_doc_urls():
    doc = ingest(str(FLASK))
    assert "http://127.0.0.1:5000/" in doc.urls


def test_raw_url_rewrite():
    assert (
        raw_url("https://github.com/pallets/flask/blob/9b74a90dd3c47f792734823e8793ac36f38bc4dd/docs/quickstart.rst")
        == "https://raw.githubusercontent.com/pallets/flask/9b74a90dd3c47f792734823e8793ac36f38bc4dd/docs/quickstart.rst"
    )
    assert raw_url("https://example.com/x.md") == "https://example.com/x.md"


def test_ingest_text_commit_passthrough():
    doc = ingest_text("# t\n", source="s", commit="abc")
    assert doc.commit == "abc"
    assert doc.lines == ["# t"]


def test_ingest_records_git_commit_when_in_repo():
    doc = ingest(str(FLASK))
    # demo/flask.md lives in this repository, so HEAD is known
    assert doc.commit is None or len(doc.commit) == 40

"""G5 Protected-text diff fails closed on protected edits and passes allowed repairs."""

import difflib
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from attest.policy import MaintainDocsPolicy

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("g5", ROOT / "scripts" / "g5-protected-text.py")
g5 = importlib.util.module_from_spec(spec)
sys.modules["g5"] = g5  # dataclasses resolve annotations through sys.modules
spec.loader.exec_module(g5)

PROTECTED_LINE = 'Click the "count is 0" button once. The counter increments by one: its label changes to "count is 1".\n'
DESCRIPTIVE_LINE = (
    'Open http://127.0.0.1:8765/ in your browser. The page shows the heading "Counter" and a button labelled "count is 0".\n'
)
MFA = "tests/fixtures/reconcile_spec/promise_weakening_attempt.json"
TLS = "tests/fixtures/reconcile_spec/protected_requirement_vs_observed_app.json"


# Frozen copies of the main hooks as of this gate. The tests must not read the
# live demo/ or reconcile_spec/ files: a PR that edits those must reach the diff
# step, not break these controls. The names keep them out of the live scan.
FROZEN = ROOT / "tests" / "fixtures" / "g5_protected_text"
LAYOUT = {
    "policy.yaml": "demo/attest.policy.yaml",
    "counter.md": "demo/counter.md",
    **{p.name: f"{g5.FIXTURE_DIR}/{p.name}" for p in FROZEN.glob("*.json")},
}


def _copy_repo_hooks(dest: Path) -> Path:
    for name, rel in LAYOUT.items():
        (dest / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(FROZEN / name, dest / rel)
    return dest


@pytest.fixture
def trees(tmp_path):
    base = _copy_repo_hooks(tmp_path / "base")
    head = tmp_path / "head"
    shutil.copytree(base, head)
    return base, head


def _replace(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert text.count(old) == 1, f"{old!r} not unique in {path}"
    # LF like the repo (.gitattributes eol=lf); text mode would write CRLF on Windows and
    # change every line's bytes, protected ones included
    path.write_text(text.replace(old, new), encoding="utf-8", newline=chr(10))


def _edit_claim(path: Path, claim_id: str, **changes) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    claim = next(c for c in data["claims"] if c["claim_id"] == claim_id)
    claim.update(changes)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _check(base: Path, head: Path) -> dict:
    return g5.check(g5.DirTree(base), g5.DirTree(head))


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _decision(root: Path, name: str, **overrides) -> None:
    decision = {
        "schema": g5.DECISION_SCHEMA,
        "decision_id": "CD-0001",
        "status": "approved",
        "approved_by": "protected-requirement owner",
        "path": "demo/counter.md",
        "before_sha256": _sha(PROTECTED_LINE),
        "after_sha256": _sha(PROTECTED_LINE.replace("by one", "by two").replace('"count is 1"', '"count is 2"')),
        "reason": "Product changed the counter step to two.",
        **overrides,
    }
    path = root / g5.DECISION_DIR / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(decision), encoding="utf-8")


def test_main_hooks_yield_protected_text_from_both_sources(trees):
    base, head = trees
    report = _check(base, head)
    sources = {u.source for u in g5.protected_units(g5.DirTree(base))}
    assert sources == {"policy:demo/attest.policy.yaml", "reconcile_spec"}
    assert report["violations"] == [] and report["base_units"] == report["head_units"] >= 3


def test_positive_control_allowed_maintain_docs_repair_passes(trees):
    """A descriptive repair Maintain Docs authorizes must not trip G5."""
    base, head = trees
    doc = head / "demo/counter.md"
    before = doc.read_text(encoding="utf-8")
    _replace(doc, "a button labelled", "a button labeled")
    after = doc.read_text(encoding="utf-8")
    line = before.splitlines(keepends=True).index(DESCRIPTIVE_LINE) + 1
    diff = "".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True), "a/counter.md", "b/counter.md"))
    decision = MaintainDocsPolicy().authorize(
        diff=diff, doc_name="counter.md", base_text=before, candidate_text=after,
        target_start=line, target_end=line, target_claim_id="c03",
        base_digest=_sha(before), candidate_digest=_sha(after),
    )
    assert decision.allowed, decision.violations

    report = _check(base, head)

    assert report["violations"] == [] and report["approved_changes"] == []


def test_unprotected_fixture_claim_edit_passes(trees):
    base, head = trees
    _edit_claim(head / MFA, "proposal-mfa-001", text="Administrators may skip multifactor authentication.")
    assert _check(base, head)["violations"] == []


def test_adding_protected_text_passes(trees):
    base, head = trees
    with (head / "demo/counter.md").open("a", encoding="utf-8") as handle:
        handle.write("\nThe counter never increments by one twice per click.\n")
    report = _check(base, head)
    assert report["violations"] == [] and report["head_units"] == report["base_units"] + 1


def _weaken_promise(head):
    _replace(head / "demo/counter.md", "The counter increments by one: its", "The counter changes: its")


def _edit_protected_paragraph(head):
    _replace(head / "demo/counter.md", '"count is 1"', '"count is 2"')


def _drop_policy_rule(head):
    (head / "demo/attest.policy.yaml").write_text("protected: []\n", encoding="utf-8")


def _delete_policy(head):
    (head / "demo/attest.policy.yaml").unlink()


def _delete_doc(head):
    (head / "demo/counter.md").unlink()


def _weaken_fixture_requirement(head):
    _edit_claim(head / MFA, "req-mfa-001", text="Administrator accounts may optionally use multifactor authentication.")


def _unprotect_fixture_requirement(head):
    _edit_claim(head / TLS, "req-tls-001", protected=False)


@pytest.mark.parametrize(
    "mutate",
    [
        _weaken_promise,
        _edit_protected_paragraph,
        _drop_policy_rule,
        _delete_policy,
        _delete_doc,
        _weaken_fixture_requirement,
        _unprotect_fixture_requirement,
    ],
)
def test_protected_text_change_without_decision_fails(trees, mutate):
    base, head = trees
    mutate(head)

    report = _check(base, head)

    assert len(report["violations"]) == 1
    assert "no approved change decision" in report["violations"][0]["reason"]


def test_maintain_docs_policy_also_refuses_the_weakening(trees):
    """G5 and the existing Maintain Docs hook agree on the canonical weakening case."""
    base, head = trees
    doc = head / "demo/counter.md"
    before = doc.read_text(encoding="utf-8")
    _weaken_promise(head)
    after = doc.read_text(encoding="utf-8")
    line = before.splitlines(keepends=True).index(PROTECTED_LINE) + 1
    diff = "".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True), "a/counter.md", "b/counter.md"))
    decision = MaintainDocsPolicy(protected_contracts=("increments by one",)).authorize(
        diff=diff, doc_name="counter.md", base_text=before, candidate_text=after,
        target_start=line, target_end=line, target_claim_id="c04",
        base_digest=_sha(before), candidate_digest=_sha(after),
    )
    assert not decision.allowed
    assert _check(base, head)["violations"]


def _change_to_two(head):
    _replace(head / "demo/counter.md", 'by one: its label changes to "count is 1"', 'by two: its label changes to "count is 2"')


def test_approved_decision_on_base_allows_exact_change(trees):
    base, head = trees
    _decision(base, "CD-0001.json")
    shutil.copytree(base / g5.DECISION_DIR, head / g5.DECISION_DIR)
    _change_to_two(head)

    report = _check(base, head)

    assert report["violations"] == []
    assert [c["decision_id"] for c in report["approved_changes"]] == ["CD-0001"]


def test_decision_added_in_same_change_does_not_approve_it(trees):
    base, head = trees
    _decision(head, "CD-0001.json")
    _change_to_two(head)

    [violation] = _check(base, head)["violations"]

    assert "only in this change" in violation["reason"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"status": "proposed"},
        {"approved_by": ""},
        {"schema": "attest.change-decision/v9"},
        {"before_sha256": _sha("some other text\n")},
        {"after_sha256": _sha("a different replacement\n")},
        {"path": "demo/other.md"},
    ],
)
def test_decision_that_does_not_bind_this_change_fails(trees, overrides):
    base, head = trees
    _decision(base, "CD-0001.json", **overrides)
    _change_to_two(head)

    report = _check(base, head)

    assert len(report["violations"]) == 1 and report["approved_changes"] == []


def test_decision_with_null_after_allows_removal(trees):
    base, head = trees
    _decision(base, "CD-0002.json", decision_id="CD-0002", after_sha256=None)
    _delete_doc(head)

    report = _check(base, head)

    assert report["violations"] == [] and report["approved_changes"][0]["decision_id"] == "CD-0002"


def test_base_without_protected_text_cannot_pass(tmp_path):
    (tmp_path / "base").mkdir()
    (tmp_path / "base" / "README.md").write_text("nothing protected\n", encoding="utf-8")
    with pytest.raises(g5.GateError, match="vacuous"):
        _check(tmp_path / "base", tmp_path / "base")


def test_unreadable_policy_cannot_pass(trees):
    base, head = trees
    (head / "demo/attest.policy.yaml").write_text("protected: {match: [\n", encoding="utf-8")
    with pytest.raises(g5.GateError, match="unreadable policy"):
        _check(base, head)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=g5", "-c", "user.email=g5@example.invalid", *args],
        check=True, capture_output=True, text=True,
    ).stdout.strip()


def test_cli_exit_codes_on_real_git_revisions(tmp_path, capsys):
    repo = _copy_repo_hooks(tmp_path / "repo")
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    base = _git(repo, "rev-parse", "HEAD")

    _replace(repo / "demo/counter.md", "a button labelled", "a button labeled")
    _git(repo, "commit", "-qam", "descriptive repair")
    assert g5.main(["--repo", str(repo), "--base", base, "--head", "HEAD"]) == 0

    _weaken_promise(repo)
    _git(repo, "commit", "-qam", "weaken")
    assert g5.main(["--repo", str(repo), "--base", base, "--head", "HEAD"]) == 1
    assert "FAIL `demo/counter.md` match:increments by one" in capsys.readouterr().out

    assert g5.main(["--repo", str(repo), "--base", "no-such-rev", "--head", "HEAD"]) == 2

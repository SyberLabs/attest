"""FACTORY G4 gate runner: it fails closed when the positive controls are absent,
skipped, incomplete or failing, and the manifest tracks the control module."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CONTROLS = "tests/test_positive_controls.py"

_spec = importlib.util.spec_from_file_location("g4_positive_controls", ROOT / "scripts" / "g4-positive-controls.py")
g4 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(g4)

MD_CONTROL = f"{CONTROLS}::test_pc_md_01_allowed_maintain_docs_repair_promotes_end_to_end"
MD_MUTANT = f"{CONTROLS}::test_pc_md_01_control_fails_when_product_refuses_everything"
CLS = "tests.test_positive_controls"


def _full(**overrides) -> dict[str, list[str]]:
    sections = {name: [f"{CONTROLS}::test_{name.replace('-', '_')}"] for name in g4.REQUIRED_SECTIONS}
    sections.update(overrides)
    return sections


def _passing(sections) -> dict[tuple[str, str], str]:
    return {g4.junit_key(n): "passed" for ids in sections.values() for n in ids}


def test_all_listed_ids_passing_is_green():
    sections = _full()
    assert g4.check(sections, _passing(sections)) == []


@pytest.mark.parametrize("outcome", ["failed", "error", "skipped"])
def test_listed_id_that_did_not_pass_fails_the_gate(outcome):
    sections = _full(**{"md-control": [MD_CONTROL]})
    results = _passing(sections) | {(CLS, MD_CONTROL.split("::")[1]): outcome}
    assert g4.check(sections, results) == [f"{MD_CONTROL}: {outcome}"]


def test_listed_id_that_never_ran_fails_the_gate():
    sections = _full(**{"md-control": [MD_CONTROL]})
    results = _passing(sections)
    del results[(CLS, MD_CONTROL.split("::")[1])]
    assert g4.check(sections, results) == [f"{MD_CONTROL}: not run"]


def test_empty_required_section_fails_the_gate():
    sections = _full(**{"md-control": []})
    assert g4.check(sections, _passing(sections)) == ["required section [md-control] is missing or empty"]


def test_parametrized_id_matches_its_own_case_only():
    listed = f"{MD_MUTANT}[promotion-refused]"
    sections = _full(**{"md-mutant": [listed]})
    results = _passing(sections) | {(CLS, "test_pc_md_01_control_fails_when_product_refuses_everything[other]"): "failed"}
    assert g4.check(sections, results) == []
    del results[g4.junit_key(listed)]
    assert g4.check(sections, results) == [f"{listed}: not run"]


def test_bare_name_needs_every_parametrized_case_to_pass():
    sections = _full(**{"md-mutant": [MD_MUTANT]})
    results = _passing(_full()) | {
        (CLS, "test_pc_md_01_control_fails_when_product_refuses_everything[a]"): "passed",
        (CLS, "test_pc_md_01_control_fails_when_product_refuses_everything[b]"): "skipped",
    }
    assert g4.check(sections, results) == [f"{MD_MUTANT}: skipped"]


def test_absent_control_file_fails_closed(tmp_path):
    manifest = tmp_path / "g4.txt"
    manifest.write_text("[md-control]\ntests/test_absent_positive_controls.py::test_pc_md_01\n", encoding="utf-8")
    sections, per_id, problems, returncode = g4.run(manifest)
    assert returncode != 0
    assert "positive-control file is missing: tests/test_absent_positive_controls.py" in problems
    assert per_id == {"tests/test_absent_positive_controls.py::test_pc_md_01": "not run"}
    assert g4.main(["--manifest", str(manifest)]) == 1


def test_manifest_without_ids_fails_closed(tmp_path):
    manifest = tmp_path / "g4.txt"
    manifest.write_text("# nothing listed\n[md-control]\n", encoding="utf-8")
    assert g4.main(["--manifest", str(manifest)]) == 1


def test_manifest_has_every_required_section_and_the_md_promote_control():
    sections = g4.read_manifest(g4.MANIFEST)
    assert [name for name in g4.REQUIRED_SECTIONS if not sections.get(name)] == []
    assert MD_CONTROL in sections["md-control"]


def test_manifest_lists_every_collected_positive_control():
    """Adding or renaming a control without listing it here fails the gate."""
    if not (ROOT / CONTROLS).is_file():
        pytest.fail(f"{CONTROLS} is missing: the positive controls are absent")
    collected = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-o", "addopts=", "-p", "no:cacheprovider", CONTROLS],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    assert collected.returncode == 0, collected.stdout + collected.stderr
    ids = {line for line in collected.stdout.splitlines() if line.startswith(CONTROLS + "::")}
    listed = {n for ids_ in g4.read_manifest(g4.MANIFEST).values() for n in ids_ if n.startswith(CONTROLS + "::")}
    assert ids and ids == listed

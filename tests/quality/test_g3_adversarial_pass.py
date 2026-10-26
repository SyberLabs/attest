"""FACTORY G3 Adversarial pass: the gate runner's fail-closed behaviour.

Each test builds a small case inventory in ``tmp_path`` (case JSON files plus a
test module bound to them by ``@pytest.mark.redteam``) and runs
``scripts/g3-adversarial-pass.py --root tmp_path`` in a subprocess. The real
inventory (``tests/redteam``, PR #26) is exercised by the CI job, not here.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
GATE = ROOT / "scripts" / "g3-adversarial-pass.py"

PASS = "assert True"
FAIL = "assert False, 'refusal missing'"
SKIP = "pytest.skip('not run')"


def _case(cid: str, status: str = "active", rubrics=("R1", "R9")) -> dict:
    component, category = {"MD": "maintain_docs", "RS": "reconcile_spec"}[cid.split("-")[0]], cid.split("-")[1]
    return {
        "id": cid,
        "component": component,
        "category": "CONTROL" if category == "CTRL" else category,
        "rubrics": list(rubrics),
        "status": status,
    }


def _inventory(root: Path, cases: list[dict], bodies: dict[str, str], extra_tests: str = "") -> None:
    """Write case files and one test per bound case; status marks mirror the #26 harness."""
    (root / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    tests = root / "tests" / "redteam"
    lines = ["import pytest", ""]
    for case in cases:
        folder = tests / "cases" / case["component"]
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{case['id']}.json").write_text(json.dumps(case), encoding="utf-8")
    for cid, body in bodies.items():
        status = next((c["status"] for c in cases if c["id"] == cid), "active")
        lines.append(f"@pytest.mark.redteam(case_id={cid!r}, status={status!r})")
        if status == "xfail":
            lines.append("@pytest.mark.xfail(strict=True, reason='observed gap')")
        elif status == "stub":
            lines.append("@pytest.mark.skip(reason='stub')")
        lines += [f"def test_{cid.replace('-', '_').lower()}():", f"    {body}", ""]
    tests.mkdir(parents=True, exist_ok=True)
    (tests / "test_cases.py").write_text("\n".join(lines) + extra_tests, encoding="utf-8")


def _gate(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(GATE), "--root", str(root), *args],
        capture_output=True, text=True, check=False, timeout=120,
    )


BASE = [_case("MD-AB-01"), _case("RS-PI-01", rubrics=("R1", "R6")), _case("MD-CTRL-01", rubrics=("R3", "R9"))]
BASE_BODIES = {"MD-AB-01": PASS, "RS-PI-01": PASS, "MD-CTRL-01": PASS}


def test_all_active_cases_passing_passes(tmp_path):
    _inventory(tmp_path, BASE, BASE_BODIES)
    run = _gate(tmp_path)
    assert run.returncode == 0, run.stdout + run.stderr
    assert "G3 Adversarial pass: passed (active cases)" in run.stdout
    assert "| maintain_docs | 1 | 1 | 0 | 0 | 0 |" in run.stdout


def test_failing_active_case_fails_the_gate(tmp_path):
    _inventory(tmp_path, BASE, {**BASE_BODIES, "RS-PI-01": FAIL})
    run = _gate(tmp_path)
    assert run.returncode == 1
    assert "RS-PI-01 (active, R1,R6) did not pass" in run.stdout


@pytest.mark.parametrize(
    "body, outcome",
    [
        (SKIP, "skipped"),
        ("pytest.xfail('known gap')", "xfailed"),
        ("1 / 0", "failed"),
    ],
)
def test_active_case_that_does_not_pass_is_not_a_g3_pass(tmp_path, body, outcome):
    _inventory(tmp_path, BASE, {**BASE_BODIES, "MD-AB-01": body})
    run = _gate(tmp_path)
    assert run.returncode == 1
    assert "MD-AB-01 (active, R1,R9) did not pass" in run.stdout
    assert outcome in run.stdout


def test_active_case_with_failing_fixture_teardown_fails(tmp_path):
    fixture = (
        "\n@pytest.fixture\ndef leaky():\n    yield\n    raise RuntimeError('cleanup not observed')\n\n"
        "@pytest.mark.redteam(case_id='RS-COST-01', status='active')\n"
        "def test_rs_cost_01(leaky):\n    assert True\n"
    )
    _inventory(tmp_path, [*BASE, _case("RS-COST-01", rubrics=("R7",))], BASE_BODIES, extra_tests=fixture)
    run = _gate(tmp_path)
    assert run.returncode == 1
    assert "RS-COST-01 (active, R7) did not pass" in run.stdout and "error" in run.stdout


def test_active_case_without_a_test_fails(tmp_path):
    _inventory(tmp_path, [*BASE, _case("MD-DS-01")], BASE_BODIES)
    run = _gate(tmp_path)
    assert run.returncode == 1
    assert "MD-DS-01 (active, R1,R9): no test is bound to this case" in run.stdout


def test_xfail_and_stub_cases_are_gaps_not_passes(tmp_path):
    cases = [*BASE, _case("MD-SIM-01", "xfail"), _case("MD-ISO-01", "stub", ("R7",))]
    _inventory(tmp_path, cases, {**BASE_BODIES, "MD-SIM-01": FAIL, "MD-ISO-01": PASS})
    run = _gate(tmp_path)
    assert run.returncode == 0, run.stdout + run.stderr
    assert "| maintain_docs | 1 | 1 | 0 | 1 | 1 |" in run.stdout
    assert "- MD-SIM-01 (xfail, R1,R9)" in run.stdout and "- MD-ISO-01 (stub, R7)" in run.stdout
    assert "R1 2," in run.stdout  # only the two passing active adversarial cases are counted


def test_xfail_case_that_now_passes_must_be_promoted(tmp_path):
    _inventory(tmp_path, [*BASE, _case("MD-SIM-01", "xfail")], {**BASE_BODIES, "MD-SIM-01": PASS})
    run = _gate(tmp_path)
    assert run.returncode == 1
    assert "MD-SIM-01 (xfail, R1,R9) expected xfailed, got" in run.stdout


def test_marker_status_must_match_the_case_file(tmp_path):
    _inventory(tmp_path, BASE, BASE_BODIES)
    path = tmp_path / "tests" / "redteam" / "cases" / "maintain_docs" / "MD-AB-01.json"
    path.write_text(json.dumps(_case("MD-AB-01", "stub")), encoding="utf-8")
    run = _gate(tmp_path)
    assert run.returncode == 1
    assert "marker status 'active' differs from MD-AB-01 file status 'stub'" in run.stdout


def test_marker_for_unknown_case_fails(tmp_path):
    _inventory(tmp_path, BASE, {**BASE_BODIES, "MD-AB-99": PASS})
    run = _gate(tmp_path)
    assert run.returncode == 1
    assert "redteam marker names unknown case 'MD-AB-99'" in run.stdout


@pytest.mark.parametrize(
    "field, value, message",
    [
        ("rubrics", ["R10"], "must be a non-empty subset of R1-R9"),
        ("rubrics", [], "must be a non-empty subset of R1-R9"),
        ("status", "skip", "status 'skip' not in"),
        ("category", "XSS", "category 'XSS' not in"),
    ],
)
def test_malformed_case_file_fails(tmp_path, field, value, message):
    _inventory(tmp_path, BASE, BASE_BODIES)
    path = tmp_path / "tests" / "redteam" / "cases" / "maintain_docs" / "MD-AB-01.json"
    path.write_text(json.dumps({**_case("MD-AB-01"), field: value}), encoding="utf-8")
    run = _gate(tmp_path)
    assert run.returncode == 1
    assert message in run.stdout


def test_collection_error_fails(tmp_path):
    _inventory(tmp_path, BASE, BASE_BODIES)
    (tmp_path / "tests" / "redteam" / "test_broken.py").write_text("import not_a_module\n", encoding="utf-8")
    run = _gate(tmp_path)
    assert run.returncode == 1
    assert "collection error" in run.stdout


def test_no_active_adversarial_case_fails(tmp_path):
    cases = [_case("MD-AB-01", "stub"), _case("MD-CTRL-01")]
    _inventory(tmp_path, cases, {"MD-AB-01": PASS, "MD-CTRL-01": PASS})
    run = _gate(tmp_path)
    assert run.returncode == 1
    assert "no active adversarial case" in run.stdout


def test_refuse_everything_needs_a_passing_control(tmp_path):
    _inventory(tmp_path, BASE[:2], {"MD-AB-01": PASS, "RS-PI-01": PASS})
    assert "no active positive control" in _gate(tmp_path).stdout
    _inventory(tmp_path, BASE, {**BASE_BODIES, "MD-CTRL-01": FAIL})
    run = _gate(tmp_path)
    assert run.returncode == 1
    assert "MD-CTRL-01 (active, R3,R9) did not pass" in run.stdout


def test_missing_inventory_fails_unless_allowed_and_then_reports_inactive(tmp_path):
    run = _gate(tmp_path)
    assert run.returncode == 1 and "No case inventory" in run.stdout
    run = _gate(tmp_path, "--allow-missing-inventory")
    assert run.returncode == 0
    assert "INACTIVE" in run.stdout and "This is not a G3 pass" in run.stdout


def test_allow_missing_inventory_does_not_relax_a_present_inventory(tmp_path):
    _inventory(tmp_path, BASE, {**BASE_BODIES, "MD-AB-01": FAIL})
    assert _gate(tmp_path, "--allow-missing-inventory").returncode == 1


def test_step_summary_is_written(tmp_path):
    _inventory(tmp_path, BASE, BASE_BODIES)
    summary = tmp_path / "summary.md"
    run = subprocess.run(
        [sys.executable, str(GATE), "--root", str(tmp_path)],
        capture_output=True, text=True, check=False, timeout=120,
        env={**os.environ, "GITHUB_STEP_SUMMARY": str(summary), "GITHUB_SHA": "abcdef0123"},
    )
    assert run.returncode == 0
    assert summary.read_text(encoding="utf-8").startswith("### G3 Adversarial pass at `abcdef0`: passed")

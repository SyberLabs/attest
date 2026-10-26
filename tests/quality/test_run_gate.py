"""The gate runner fails closed on missing, skipped, xfailed or failed tests."""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("run_gate", ROOT / "scripts" / "run-gate.py")
run_gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run_gate)

JUNIT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest">
  <testcase classname="tests.test_a" name="test_pass"/>
  <testcase classname="tests.test_a" name="test_param[x]"/>
  <testcase classname="tests.test_a" name="test_param[y]"><failure message="boom"/></testcase>
  <testcase classname="tests.test_a" name="test_skip"><skipped message="skip"/></testcase>
  <testcase classname="tests.test_a" name="test_xfail"><skipped type="pytest.xfail" message="xfail"/></testcase>
  <testcase classname="tests.test_a" name="test_error"><error message="setup"/></testcase>
  <testcase classname="tests.sub.test_b.TestCls" name="test_method"/>
</testsuite></testsuites>
"""


@pytest.fixture
def results(tmp_path):
    path = tmp_path / "junit.xml"
    path.write_text(JUNIT, encoding="utf-8")
    return run_gate.junit_results(path)


def test_listed_passing_tests_pass_the_gate(results):
    sections = {"ok": ["tests/test_a.py::test_pass", "tests/sub/test_b.py::TestCls::test_method"]}
    assert run_gate.check(sections, results, ["ok"]) == []


@pytest.mark.parametrize(
    "node_id, problem",
    [
        ("tests/test_a.py::test_missing", "not run"),
        ("tests/test_a.py::test_skip", "skipped"),
        ("tests/test_a.py::test_xfail", "skipped"),
        ("tests/test_a.py::test_error", "error"),
        ("tests/test_a.py::test_param", "failed"),
    ],
)
def test_non_passing_or_absent_tests_fail_the_gate(results, node_id, problem):
    assert run_gate.check({"s": [node_id]}, results, []) == [f"{node_id}: {problem}"]


def test_empty_required_section_fails_the_gate(results):
    problems = run_gate.check({"a": ["tests/test_a.py::test_pass"], "b": []}, results, ["b", "c"])
    assert problems == ["required section [b] is missing or empty", "required section [c] is missing or empty"]


def test_manifest_rejects_ids_outside_a_section(tmp_path):
    manifest = tmp_path / "gate.txt"
    manifest.write_text("tests/test_a.py::test_pass\n", encoding="utf-8")
    with pytest.raises(ValueError, match="before any"):
        run_gate.read_manifest(manifest)


def test_g1_manifest_ids_exist_and_cover_every_evidence_class():
    sections = run_gate.read_manifest(ROOT / "tests" / "quality" / "g1_stage0_exit.txt")
    assert set(sections) == {"simulated", "omitted", "wrong-version", "incomplete", "control"}
    for node_id in (n for ids in sections.values() for n in ids):
        file_part, name = node_id.split("::", 1)
        source = (ROOT / file_part).read_text(encoding="utf-8")
        assert f"def {name.split('::')[-1]}(" in source, node_id

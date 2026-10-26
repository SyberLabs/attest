"""The G1 binding gate runner fails closed: a skip, xfail, failure or empty section fails it."""

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "g1-stage0-binding.py"
_spec = importlib.util.spec_from_file_location("g1_stage0_binding", SCRIPT)
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)

GREEN = {
    "test_version_a": "passed",
    "test_kane_run_b[x]": "passed",
    "test_observation_build_e": "passed",
    "test_record_f": "passed",
    "test_pack_g": "passed",
    "test_wording_h": "passed",
    "test_run_time_i": "passed",
    "test_run_reuse_j": "passed",
    "test_approval_time_k": "passed",
    "test_control_c": "passed",
}


def test_all_sections_passing_is_green():
    counts, problems = gate.check(GREEN)
    assert problems == [] and counts == {
        "version": 1, "kane-run": 1, "observation-build": 1, "record": 1, "pack": 1, "wording": 1, "run-time": 1, "run-reuse": 1,
        "approval-time": 1, "control": 1}


@pytest.mark.parametrize("outcome", ["skipped", "failed", "error"])
def test_any_non_pass_fails_the_gate(outcome):
    _, problems = gate.check({**GREEN, "test_kane_run_d": outcome})
    assert problems == [f"test_kane_run_d: {outcome}"]


@pytest.mark.parametrize("dropped", list(GREEN))
def test_empty_section_fails_the_gate(dropped):
    _, problems = gate.check({k: v for k, v in GREEN.items() if k != dropped})
    assert len(problems) == 1 and "has no passing test" in problems[0]


def test_nothing_ran_fails_the_gate():
    _, problems = gate.check({})
    assert len(problems) == len(gate.SECTIONS)


def test_unsectioned_test_fails_the_gate():
    _, problems = gate.check({**GREEN, "test_something_else": "passed"})
    assert problems and "not in any section" in problems[0]


def test_every_binding_test_belongs_to_a_section():
    source = (SCRIPT.parents[1] / gate.TESTS).read_text(encoding="utf-8")
    names = [line.split("(")[0][4:] for line in source.splitlines() if line.startswith("def test_")]
    assert names and all(name.startswith(tuple(gate.SECTIONS.values())) for name in names)

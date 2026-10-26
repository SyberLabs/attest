"""Maintain Docs red-team cases (FACTORY §1) beyond the first inventory: injected commands,
sibling proposals, and the repair agent's deadline.

Inputs and expected invariants live in cases/maintain_docs/<ID>.json.
"""

from __future__ import annotations

import functools
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from attest import loop
from attest.approvals import approve_workflow
from attest.compiler import fences_by_regex
from attest.execution.recipes import ApprovedRecipeExecutor
from attest.ingest import ingest_text
from attest.pipeline import prove
from attest.proposals import create_repair_proposal
from attest.storage import SQLiteWorkflowStore
from attest.verdict import Rules
from attest.workflow import WorkflowError, promote_workflow, resume_workflow

from ._harness import KaneTestRunner, case, make_diff


@pytest.fixture(autouse=True)
def _no_model(monkeypatch):
    # Every case here must hold without a compiler model; none may reach the network.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)


def _proposal(store: SQLiteWorkflowStore, source: Path, base: str, candidate: str):
    claims = fences_by_regex(ingest_text(base, source=str(source)))
    return create_repair_proposal(
        store, source_path=source, base_text=base, candidate_text=candidate, diff=make_diff(base, candidate),
        target_claim=claims[0], baseline_claims=claims, plan="Repair only c01; preserve all promises.",
    )


def _recipes(approved: dict[str, str]) -> ApprovedRecipeExecutor:
    return ApprovedRecipeExecutor({command: [sys.executable, "-c", code] for command, code in approved.items()})


def _alive(pid: int) -> bool:
    if os.name == "nt":
        from tests.test_execution import _alive as windows_alive

        return windows_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    status = Path(f"/proc/{pid}/status")
    # An orphan the container's init has not reaped yet is dead for our purposes.
    return not (status.exists() and re.search(r"^State:\s+Z", status.read_text(), re.M))


def _reap(*pids: int) -> None:
    for pid in pids:
        if _alive(pid):
            os.kill(pid, signal.SIGTERM if os.name == "nt" else signal.SIGKILL)


# -- PI: prompt injection ------------------------------------------------------------


@case("MD-PI-05")
def test_injected_command_in_approved_candidate_never_runs(rt_case, tmp_path):
    i, x = rt_case["input"], rt_case["expected"]
    marker = tmp_path / i["marker"]
    candidate = i["candidate"].replace(i["marker"], marker.as_posix()).replace("python3", f'"{Path(sys.executable).as_posix()}"')
    command = candidate.split("```bash\n", 1)[1].split("\n```", 1)[0]
    # The canary works: a shell given the candidate's command writes the marker.
    canary = tmp_path / "canary"
    canary.mkdir()
    subprocess.run(command.replace(marker.as_posix(), (canary / i["marker"]).as_posix()), shell=True, cwd=canary,
                   capture_output=True, timeout=30)
    assert (canary / i["marker"]).exists()

    source = tmp_path / "guide.md"
    source.write_text(i["base"], encoding="utf-8", newline=chr(10))
    store = SQLiteWorkflowStore(tmp_path / "workflow.sqlite3")
    record = _proposal(store, source, i["base"], candidate)
    assert record.state == x["proposal_state"]
    approve_workflow(store, record.workflow_id, i["approver"])

    result = resume_workflow(store, record.workflow_id, runner=KaneTestRunner(),
                             recipe_executor=_recipes(i["approved_recipes"]), apply=True)
    assert result.state == x["state"]
    assert result.validation["failed_claims"] == x["failed_claims"]
    assert marker.exists() is x["marker_written"]
    with pytest.raises(WorkflowError, match=x["promote_error"]):
        promote_workflow(store, record.workflow_id)
    assert source.read_text(encoding="utf-8") == i["base"]


# -- DS: digest swap / identity confusion ----------------------------------------------


@case("MD-DS-06")
def test_sibling_proposal_is_refused_after_another_is_promoted(rt_case, tmp_path):
    i, x = rt_case["input"], rt_case["expected"]
    source = tmp_path / "guide.md"
    source.write_text(i["base"], encoding="utf-8", newline=chr(10))
    store = SQLiteWorkflowStore(tmp_path / "workflow.sqlite3")
    recipes = _recipes(i["approved_recipes"])
    first = _proposal(store, source, i["base"], i["first"])
    second = _proposal(store, source, i["base"], i["second"])
    for record in (first, second):
        approve_workflow(store, record.workflow_id, i["approver"])
        validated = resume_workflow(store, record.workflow_id, runner=KaneTestRunner(), recipe_executor=recipes)
        assert validated.state == x["validated"], validated.validation

    assert promote_workflow(store, first.workflow_id).state == x["first_state"]
    promoted = source.read_bytes()
    with pytest.raises(WorkflowError, match=x["second_error"]):
        promote_workflow(store, second.workflow_id)
    assert store.get(second.workflow_id).state == x["second_state"]
    assert source.read_bytes() == promoted == i["first"].encode("utf-8")


# -- COST: cost or time blowup -------------------------------------------------------


def _sleeping_agent(tmp_path: Path, sleep_s: int, child_sleep_s: int | None = None) -> tuple[Path, Path]:
    """An agent command that reads the brief, records its pid (and its child's), and never answers."""
    pids = tmp_path / "agent.pids"
    agent = tmp_path / "agent.py"
    child = (
        f"g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep({child_sleep_s})'], "
        "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
        if child_sleep_s else "g = None\n"
    )
    agent.write_text(
        "import os, subprocess, sys, time\n"
        "sys.stdin.read()\n"
        f"{child}"
        f"open({str(pids)!r}, 'w').write(f'{{os.getpid()}} {{g.pid if g else 0}}')\n"
        f"time.sleep({sleep_s})\n",
        encoding="utf-8",
    )
    return agent, pids


def _recorded_pids(pids: Path) -> tuple[int, int]:
    deadline = time.monotonic() + 5
    while not pids.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    own, child = (int(p) for p in pids.read_text().split())
    return own, child


@case("MD-COST-03")
def test_silent_repair_agent_is_stopped_at_its_deadline(rt_case, tmp_path, monkeypatch):
    i, x = rt_case["input"], rt_case["expected"]
    monkeypatch.chdir(tmp_path)
    doc = tmp_path / "loop.md"
    doc.write_text(i["doc"], encoding="utf-8")
    agent, pids = _sleeping_agent(tmp_path, i["agent_sleep_s"])
    # The deadline is ATTEST_AGENT_TIMEOUT_S (240 s); the case shortens it, not the code path.
    monkeypatch.setattr(loop, "call_agent", functools.partial(loop.call_agent, timeout_s=i["agent_timeout_s"]))
    home = tmp_path / "home"
    result = prove(str(doc), runner=KaneTestRunner(), home=home)

    started = time.monotonic()
    try:
        result = loop.run_loop(result, KaneTestRunner(), home=home, rules=Rules(loop_attempts=i["loop_attempts"]),
                               agent_cmd=f'"{sys.executable}" "{agent}"')
        assert time.monotonic() - started < x["max_elapsed_s"]
    finally:
        if pids.exists():
            _reap(*(p for p in _recorded_pids(pids) if p))

    receipt = json.loads(next((home / "receipts").glob("*.json")).read_text(encoding="utf-8"))
    assert receipt["disposition"] == x["disposition"]
    assert x["reason_contains"] in result.by_id["c01"].reason
    assert result.proposals == {}
    assert (doc.read_text(encoding="utf-8") == i["doc"]) is x["doc_unchanged"]


@case("MD-COST-04")
def test_agent_deadline_leaves_no_agent_process_running(rt_case, tmp_path):
    i, x = rt_case["input"], rt_case["expected"]
    agent, pids = _sleeping_agent(tmp_path, i["agent_sleep_s"], i["child_sleep_s"])
    log = tmp_path / "agent.stderr.log"
    started = time.monotonic()
    out = loop.call_agent("brief", tmp_path, cmd=f'"{sys.executable}" "{agent}"', timeout_s=i["agent_timeout_s"], stderr_log=log)
    own, child = _recorded_pids(pids)
    try:
        assert time.monotonic() - started < x["max_elapsed_s"]
        assert out == x["returned"]
        assert x["stderr_log_contains"] in log.read_text(encoding="utf-8")
        deadline = time.monotonic() + 2
        while (_alive(own) or _alive(child)) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert _alive(own) is x["agent_alive"]
        assert _alive(child) is x["child_alive"]
    finally:
        _reap(own, child)

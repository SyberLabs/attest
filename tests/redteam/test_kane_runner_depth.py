"""Kane runner red-team cases (FACTORY §1) after the recorded-capture depth: cross-step
store keys, and what kane-cli's process can reach and leave behind.

Inputs and expected invariants live in cases/kane_runner/<ID>.json. Nothing here starts
the real kane-cli or a browser. The process cases drive ``KaneBrowserRunner._execute``
with a small Python stand-in for kane-cli, the way KR-COST-01 and KR-ISO-06 do.
"""

from __future__ import annotations

import json
import os
import re
import signal
import sys
import time
from pathlib import Path

import pytest

from attest.kane import KaneBrowserRunner, observation_for, parse_events
from attest.kane import test_file_text as make_test_file
from attest.models import Claim, Evidence, Span
from attest.verdict import judge

from ._harness import case

REPO = Path(__file__).resolve().parents[2]
KANE_FIX = REPO / "tests" / "fixtures" / "kane"
POSIX_ONLY = pytest.mark.skipif(os.name == "nt", reason="process probe is POSIX only")


def _claim(spec: dict) -> Claim:
    return Claim(
        id=spec["id"],
        kind="browser",
        span=Span(start_line=1, end_line=1, text=f'The page shows "{spec["expects"]}".'),
        objective=spec["objective"],
        expects=spec["expects"],
        store_as=f"{spec['id']}_label",
    )


def _bare_runner(home: Path) -> KaneBrowserRunner:
    # Skip __init__: it requires kane-cli on PATH, and _execute is all these cases drive.
    runner = KaneBrowserRunner.__new__(KaneBrowserRunner)
    runner.home = home
    return runner


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    status = Path(f"/proc/{pid}/status")
    # An orphan the container's init has not reaped yet is dead for our purposes.
    return not (status.exists() and re.search(r"^State:\s+Z", status.read_text(), re.M))


def _gone(*pids: int, within_s: float = 2.0) -> None:
    deadline = time.monotonic() + within_s
    while any(_alive(p) for p in pids) and time.monotonic() < deadline:
        time.sleep(0.05)


def _reap(*pids: int) -> None:
    for pid in pids:
        if _alive(pid):
            os.kill(pid, signal.SIGKILL)


def _pids(path: Path) -> tuple[int, int]:
    own, child = (int(p) for p in path.read_text().split())
    return own, child


# -- PI: prompt injection ------------------------------------------------------------


@case("KR-PI-07")
def test_value_stored_under_the_next_claims_key_is_not_its_evidence(rt_case):
    i, x = rt_case["input"], rt_case["expected"]
    claims = [_claim(spec) for spec in i["claims"]]
    text = make_test_file(claims, i["start_url"], "t")
    assert [ln for ln in text.splitlines() if ln.startswith("## ")] == x["headings"]
    # The doc's words do reach Kane, inside the claim's own step; the mapping keeps them inert.
    sections = dict(re.findall(r"^## (c\d+)\n(.+)$", text, re.M))
    assert [cid for cid, body in sections.items() if i["instruction"] in body] == [x["instruction_written_into"]]

    first, target = claims
    key = target.store_as
    recorded = (KANE_FIX / f"{i['capture']}.ndjson").read_text(encoding="utf-8").splitlines()
    for name, variant in i["variants"].items():
        # Kane obeyed: the first step's run_end carries the next claim's key. Optionally the
        # next step's own run_end stored nothing under it.
        edited, step = [], None
        for line in recorded:
            event = json.loads(line) if line.startswith("{") else {}
            if event.get("type") == "test_md_step_start":
                step = event["step_index"]
            if event.get("type") == "run_end" and step == i["forged_into_step"]:
                event.setdefault("context", {}).setdefault("variables", {}).update(i["forged_variables"])
                line = json.dumps(event)
            elif event.get("type") == "run_end" and step == variant["drop_from_step"]:
                for holder in ((event.get("context") or {}).get("variables"), event.get("variables_out"), event.get("final_state")):
                    (holder or {}).pop(key, None)
                line = json.dumps(event)
            edited.append(line)
        run = parse_events(edited)
        assert sorted(observation_for(first, 1, run, {}).observed) == x["c07_observed_keys"], name
        observation = observation_for(target, 2, run, {})
        assert observation.status == x["c08_observation_status"], name
        assert observation.observed == x["c08_observed"][name], name
        verdict = judge(target, Evidence(claim_id=target.id, kind="browser", observation=observation))
        assert verdict.status != x["verdict_not"], name


# -- ISO: isolation and cleanup ------------------------------------------------------


class _Interrupted(Exception):
    """Raised in the main thread by SIGALRM, as Ctrl-C would arrive mid-read."""


@POSIX_ONLY
@case("KR-ISO-07")
def test_interrupt_mid_stream_stops_kane_and_its_child(rt_case, tmp_path):
    i, x = rt_case["input"], rt_case["expected"]
    pid_file = tmp_path / "kane.pids"
    # Stands in for kane-cli: starts a long-lived child (the browser) and keeps printing.
    kane = (
        "import os, subprocess, sys, time\n"
        "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], stdout=subprocess.DEVNULL)\n"
        f"open({str(pid_file)!r}, 'w').write(f'{{os.getpid()}} {{g.pid}}')\n"
        "while True:\n"
        f"    print('{{}}', flush=True); time.sleep({i['tick_s']})\n"
    )

    def interrupt(signum, frame):
        raise _Interrupted()

    previous = signal.signal(signal.SIGALRM, interrupt)
    started = time.monotonic()
    interrupted = False
    try:
        signal.setitimer(signal.ITIMER_REAL, i["interrupt_after_s"])
        try:
            _bare_runner(tmp_path / "home")._execute([sys.executable, "-c", kane], timeout_s=i["timeout_s"])
        except _Interrupted:
            interrupted = True
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
    own, child = _pids(pid_file)
    try:
        assert interrupted is x["interrupt_propagates"]
        assert time.monotonic() - started < x["max_elapsed_s"]
        _gone(own, child)
        assert _alive(own) is x["kane_alive"]
        assert _alive(child) is x["child_alive"]
    finally:
        _reap(own, child)


@case("KR-ISO-08")
def test_kane_runs_in_its_home_without_stdin(rt_case, tmp_path, monkeypatch):
    i, x = rt_case["input"], rt_case["expected"]
    caller = tmp_path / "repo"
    caller.mkdir()
    monkeypatch.chdir(caller)
    probe = tmp_path / "probe.json"
    kane = (
        "import json, os, sys\n"
        "stdin = sys.stdin.read()\n"
        f"open({i['relative_write']!r}, 'w').write('x')\n"
        f"json.dump({{'cwd': os.getcwd(), 'stdin': stdin}}, open({str(probe)!r}, 'w'))\n"
    )
    home = tmp_path / "home"
    # Attest's own stdin holds input a reader would take for an answer.
    read_end, write_end = os.pipe()
    os.write(write_end, i["caller_stdin"].encode("utf-8"))
    os.close(write_end)
    saved = os.dup(0)
    os.dup2(read_end, 0)
    try:
        run = _bare_runner(home)._execute([sys.executable, "-c", kane], timeout_s=i["timeout_s"])
    finally:
        os.dup2(saved, 0)
        os.close(saved)
        os.close(read_end)
    assert run.exit_code == 0
    seen = json.loads(probe.read_text(encoding="utf-8"))
    assert (Path(seen["cwd"]).resolve() == home.resolve()) is x["cwd_is_kane_home"]
    assert seen["stdin"] == x["stdin_read"]
    assert (home / i["relative_write"]).is_file() is x["written_under_kane_home"]
    assert sorted(p.name for p in caller.iterdir()) == x["caller_dir_files"]


@case("KR-ISO-09")
def test_kane_does_not_inherit_attest_secrets(rt_case, tmp_path, monkeypatch):
    i, x = rt_case["input"], rt_case["expected"]
    for name, value in i["environment"].items():
        monkeypatch.setenv(name, value)
    probe = tmp_path / "env.json"
    names = sorted(i["environment"])
    kane = f"import json, os; json.dump(sorted(n for n in {names!r} if n in os.environ), open({str(probe)!r}, 'w'))\n"
    run = _bare_runner(tmp_path / "home")._execute([sys.executable, "-c", kane], timeout_s=i["timeout_s"])
    assert run.exit_code == x["exit_code"]
    assert json.loads(probe.read_text(encoding="utf-8")) == x["visible_to_kane"]


@POSIX_ONLY
@case("KR-ISO-10")
def test_child_left_by_a_normal_kane_exit_does_not_outlive_the_run(rt_case, tmp_path):
    i, x = rt_case["input"], rt_case["expected"]
    pid_file = tmp_path / "kane.pids"
    # Stands in for kane-cli: launches a child (the browser) off the stream and exits 0.
    kane = (
        "import os, subprocess, sys\n"
        f"g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep({i['child_sleep_s']})'],"
        " stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
        f"open({str(pid_file)!r}, 'w').write(f'{{os.getpid()}} {{g.pid}}')\n"
        "print('{\"type\": \"test_md_done\", \"overall_status\": \"passed\"}', flush=True)\n"
    )
    run = _bare_runner(tmp_path / "home")._execute([sys.executable, "-c", kane], timeout_s=i["timeout_s"])
    _, child = _pids(pid_file)
    try:
        assert run.exit_code == x["exit_code"]
        _gone(child)
        assert _alive(child) is x["child_alive"]
    finally:
        _reap(child)

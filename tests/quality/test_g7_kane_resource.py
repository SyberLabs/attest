"""FACTORY G7 Kane resource: deadline, cleanup and isolation checks, the port-kill
scan, and the gate runner's fail-closed behaviour.

The Kane cases drive ``KaneBrowserRunner._execute`` with a small Python script
standing in for kane-cli, so nothing here needs Kane, a browser or the network.
"""

from __future__ import annotations

import importlib.util
import json
import os
import signal
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path

import pytest

from attest import kane
from attest.execution.sandbox import extract_zip_safely, safe_pack_path
from attest.execution.supervisor import ProcessSupervisor
from attest.kane import STEP_TIMEOUT_S, KaneBrowserRunner, parse_events, unpack
from attest.models import Claim, Span

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "tests" / "fixtures" / "kane"
PY = sys.executable

_spec = importlib.util.spec_from_file_location("g7_kane_resource", ROOT / "scripts" / "g7-kane-resource.py")
g7 = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = g7  # dataclasses look the module up by name
_spec.loader.exec_module(g7)

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="reads /proc; G7 runs on ubuntu-latest")


def _alive(pid: int) -> bool:
    """True while ``pid`` exists and is not a zombie (Linux /proc)."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return False
    return stat.rsplit(")", 1)[1].split()[0] != "Z"


def _gone(pid: int, within_s: float = 3.0) -> bool:
    end = time.monotonic() + within_s
    while time.monotonic() < end:
        if not _alive(pid):
            return True
        time.sleep(0.05)
    return not _alive(pid)


@pytest.fixture
def runner(tmp_path, monkeypatch):
    monkeypatch.setattr(kane.shutil, "which", lambda cmd: cmd)
    return KaneBrowserRunner(home=tmp_path / "kane", headless=True)


def browser(cid: str) -> Claim:
    text = 'Check that the page shows the text "Hello, World!".'
    return Claim(id=cid, kind="browser", span=Span(start_line=1, end_line=1, text=text), objective=text,
                 expects="Hello, World!", store_as=f"{cid}_label")


CHATTY_KANE = (
    "import json, os, sys, time\n"
    "open('kane.pid', 'w').write(str(os.getpid()))\n"
    "while True:\n"
    "    print(json.dumps({'type': 'progress'}), flush=True)\n"
    "    time.sleep(0.05)\n"
)


def _run_chatty(runner, timeout_s=0.5, limit_s=10.0):
    """Run CHATTY_KANE under ``_execute`` and fail, rather than hang, if the
    runner's deadline never fires."""
    out: dict = {}
    worker = threading.Thread(target=lambda: out.update(run=runner._execute([PY, "-c", CHATTY_KANE], timeout_s)), daemon=True)
    started = time.monotonic()
    worker.start()
    worker.join(limit_s)
    if worker.is_alive():
        pid = runner.home / "kane.pid"
        if pid.exists():
            os.kill(int(pid.read_text()), signal.SIGKILL)
        pytest.fail(f"KaneBrowserRunner._execute ignored a {timeout_s}s deadline for {limit_s}s")
    return out["run"], time.monotonic() - started


# -- deadline -------------------------------------------------------------------------


def test_kane_process_that_keeps_writing_is_killed_at_its_deadline(runner):
    run, elapsed = _run_chatty(runner)
    assert elapsed < 5
    assert run.exit_code == 3  # timed out, never a pass or a plain failure


def test_every_kane_run_gets_a_finite_deadline_and_step_timeout(runner):
    seen: list[tuple[list[str], float]] = []

    def record(cmd, timeout_s):
        seen.append((cmd, timeout_s))
        run = parse_events((FIX / "author_pass.ndjson").read_text(encoding="utf-8").splitlines())
        run.exit_code = 0
        return run

    runner._execute = record
    group = [browser("c01"), browser("c02"), browser("c03")]
    runner.prepare(group, "demo/flask.md")
    runner.run_group(group, "http://127.0.0.1:5000/")
    [(cmd, timeout_s)] = seen
    assert 0 < timeout_s <= STEP_TIMEOUT_S * len(group) + 180
    assert cmd[cmd.index("--timeout") + 1] == str(STEP_TIMEOUT_S)
    assert "max_steps: 15" in (runner.home / "tests" / "flask-c01_test.md").read_text(encoding="utf-8")


# -- cleanup --------------------------------------------------------------------------


@posix_only
def test_kane_process_killed_at_its_deadline_is_not_left_running(runner):
    _run_chatty(runner)
    pid = int((runner.home / "kane.pid").read_text())
    assert _gone(pid)


@posix_only
def test_supervisor_reaps_detached_grandchild_after_normal_exit(tmp_path):
    pidfile = tmp_path / "grandchild.pid"
    code = (
        "import subprocess, sys, time, pathlib\n"
        f"p = pathlib.Path({str(pidfile)!r})\n"
        "subprocess.Popen([sys.executable, '-c', 'import os, time, pathlib; "
        "pathlib.Path(%r).write_text(str(os.getpid())); time.sleep(30)' % str(p)],"
        " stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
        "while not p.exists() or not p.read_text(): time.sleep(0.02)\n"
        "print('parent done')\n"
    )
    result = ProcessSupervisor(timeout_s=10, terminate_grace_s=0.1).run([PY, "-c", code])
    assert result.returncode == 0 and result.timed_out is False
    assert _gone(int(pidfile.read_text()))


# -- isolation ------------------------------------------------------------------------


def test_kane_process_runs_in_its_own_home_without_stdin(runner):
    probe = (
        "import json, os, sys\n"
        "open('probe.json', 'w').write(json.dumps({'cwd': os.getcwd(), 'stdin': sys.stdin.read()}))\n"
    )
    run = runner._execute([PY, "-c", probe], timeout_s=10)
    seen = json.loads((runner.home / "probe.json").read_text())
    assert run.exit_code == 0
    assert Path(seen["cwd"]).resolve() == runner.home.resolve()
    assert seen["stdin"] == ""


def test_kane_unpack_keeps_absolute_member_inside_pack(tmp_path):
    outside = tmp_path / "outside.txt"
    pack = tmp_path / "hostile.zip"
    with zipfile.ZipFile(pack, "w") as z:
        z.writestr(str(outside), "escaped")
        z.writestr("ok/step.yaml", "fine")
    dest = unpack(pack, tmp_path / "pack")
    assert not outside.exists()
    assert (dest / "ok" / "step.yaml").read_text() == "fine"


def test_evidence_zip_with_symlink_member_extracts_nothing(tmp_path):
    pack = tmp_path / "symlink.zip"
    with zipfile.ZipFile(pack, "w") as z:
        z.writestr("a.txt", "first")
        link = zipfile.ZipInfo("link")
        link.external_attr = (0o120777 << 16)
        z.writestr(link, "/etc/passwd")
    with pytest.raises(ValueError, match="symlink is not allowed"):
        extract_zip_safely(pack, tmp_path / "out")
    assert list((tmp_path / "out").iterdir()) == []


def test_evidence_zip_over_size_cap_extracts_nothing(tmp_path):
    pack = tmp_path / "big.zip"
    with zipfile.ZipFile(pack, "w") as z:
        z.writestr("small.txt", "x" * 10)
        z.writestr("big.bin", "x" * 4096)
    with pytest.raises(ValueError, match="size limit"):
        extract_zip_safely(pack, tmp_path / "out", max_uncompressed_bytes=1024)
    assert list((tmp_path / "out").iterdir()) == []


@pytest.mark.parametrize("member", ["../x", "a/../../x", "/etc/passwd", "C:/x", "a\\..\\x", ""])
def test_pack_path_rejects_escape(tmp_path, member):
    with pytest.raises(ValueError, match="outside pack"):
        safe_pack_path(tmp_path, member)


# -- port-kill scan -------------------------------------------------------------------

PATTERNS = g7.read_patterns()

HOSTILE = {
    "netstat": 'out = subprocess.run(["netstat", "-ano"], capture_output=True).stdout',
    "taskkill": 'subprocess.run(["taskkill", "/F", "/T", "/PID", pid])',
    "fuser": 'os.system(f"fuser -k {port}/tcp")',
    "lsof": 'pids = subprocess.check_output(["lsof", "-ti", f":{port}"])',
    "killall": 'subprocess.run(["killall", "node"])',
    "pkill": 'subprocess.run("pkill -f vite", shell=True)',
    "kill-port": 'subprocess.run(["npx", "kill-port", str(port)])',
    "ss -p": 'subprocess.run(["ss", "-ltnp"], capture_output=True)',
    "psutil connections": "owners = [c.pid for c in psutil.net_connections() if c.laddr.port == port]",
    "Get-NetTCPConnection": 'subprocess.run(["powershell", "Get-NetTCPConnection -LocalPort 5173"])',
    "Stop-Process": 'subprocess.run(["powershell", "Stop-Process -Id $owner"])',
}


def test_every_port_kill_pattern_has_a_hostile_sample():
    assert set(HOSTILE) == set(PATTERNS)


@pytest.mark.parametrize("name", sorted(HOSTILE))
def test_port_kill_scan_flags_each_pattern(tmp_path, name):
    (tmp_path / "helper.py").write_text(f"import subprocess\n\ndef clean(port, pid):\n    {HOSTILE[name]}\n")
    problems, scanned = g7.port_kill_problems([tmp_path], PATTERNS)
    assert scanned == 1
    assert any(f"port-kill pattern {name!r}" in p for p in problems), problems


def test_port_kill_scan_flags_shell_scripts(tmp_path):
    (tmp_path / "cleanup.sh").write_text("#!/usr/bin/env bash\nfuser -k 5173/tcp\n")
    problems, _ = g7.port_kill_problems([tmp_path], PATTERNS)
    assert problems and "cleanup.sh:2" in problems[0]


def test_port_kill_scan_ignores_comments_and_owned_tree_kill(tmp_path):
    (tmp_path / "ok.py").write_text(
        "import os, signal\n"
        "# never shell out to netstat or taskkill here\n"
        "def stop(proc):\n"
        "    os.killpg(proc.pid, signal.SIGTERM)  # owned group only, no lsof\n"
        "    get_connections(proc)\n"
    )
    assert g7.port_kill_problems([tmp_path], PATTERNS) == ([], 1)


def test_port_kill_scan_reads_strings_that_contain_a_hash(tmp_path):
    (tmp_path / "x.py").write_text('cmd = "echo # then netstat -ano"\n')
    problems, _ = g7.port_kill_problems([tmp_path], PATTERNS)
    assert len(problems) == 1 and "'netstat'" in problems[0]


def test_port_kill_scan_fails_when_it_reads_nothing(tmp_path):
    (tmp_path / "README.md").write_text("netstat\n")
    problems, scanned = g7.port_kill_problems([tmp_path, tmp_path / "missing"], PATTERNS)
    assert scanned == 0
    assert any("does not exist" in p for p in problems)
    assert "port-kill scan read no files" in problems


def test_product_paths_have_no_port_kill_code():
    problems, scanned = g7.port_kill_problems([ROOT / r for r in g7.SCAN_ROOTS], PATTERNS)
    assert scanned > 0
    assert problems == []


# -- control --------------------------------------------------------------------------


def test_well_behaved_kane_process_finishes_under_its_deadline(runner):
    fixture = FIX / "author_pass.ndjson"
    started = time.monotonic()
    run = runner._execute([PY, "-c", f"import sys; sys.stdout.write(open({str(fixture)!r}, encoding='utf-8').read())"],
                          timeout_s=30)
    expected = parse_events(fixture.read_text(encoding="utf-8").splitlines())
    assert time.monotonic() - started < 15
    assert run.exit_code == 0
    assert run.session_id == expected.session_id and run.session_id
    assert run.steps.keys() == expected.steps.keys() and run.steps


# -- gate runner ----------------------------------------------------------------------

SECTIONS = {name: [f"tests/test_x.py::test_{name}"] for name in g7.REQUIRED_SECTIONS}


def _passed(sections):
    return {g7.junit_key(n): "passed" for ids in sections.values() for n in ids}


def test_gate_passes_only_when_every_listed_id_passed():
    assert g7.check(SECTIONS, _passed(SECTIONS)) == []


@pytest.mark.parametrize("outcome", ["skipped", "failed", "error"])
def test_gate_fails_on_skipped_failed_or_errored_id(outcome):
    results = _passed(SECTIONS)
    results[g7.junit_key(SECTIONS["deadline"][0])] = outcome
    assert g7.check(SECTIONS, results) == [f"{SECTIONS['deadline'][0]}: {outcome}"]


def test_gate_fails_on_missing_id_failed_parameter_and_empty_section():
    sections = dict(SECTIONS, cleanup=[], deadline=["tests/test_x.py::test_param"])
    results = _passed(SECTIONS)
    results[("tests.test_x", "test_param[a]")] = "passed"
    results[("tests.test_x", "test_param[b]")] = "failed"
    problems = g7.check(sections, results)
    assert "required section [cleanup] is missing or empty" in problems
    assert "tests/test_x.py::test_param: failed" in problems
    del results[("tests.test_x", "test_param[a]")], results[("tests.test_x", "test_param[b]")]
    assert "tests/test_x.py::test_param: not run" in g7.check(sections, results)


def test_manifest_has_every_required_section_and_its_ids_exist():
    sections = g7.read_manifest(g7.MANIFEST)
    assert all(sections.get(name) for name in g7.REQUIRED_SECTIONS)
    ids = [n for group in sections.values() for n in group]
    assert len(ids) == len(set(ids))
    collected = subprocess.run(
        [PY, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", *ids],
        cwd=ROOT, capture_output=True, text=True, env={**os.environ, "PYTEST_ADDOPTS": ""},
    )
    assert collected.returncode == 0, collected.stdout[-2000:] + collected.stderr[-2000:]

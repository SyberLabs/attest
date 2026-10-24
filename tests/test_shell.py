import os
import socket
import sys
import time
from pathlib import Path

import pytest

from attest.compiler import fences_by_regex
from attest.ingest import ingest
from attest.models import BrowserObservation, Claim, Span
from attest.pipeline import execute
from attest.runner import FakeBrowserRunner
from attest.verdict import Rules
from attest.shell import ShellSession, detect_url, executable_command, new_scratch
from attest.execution.recipes import ApprovedRecipeExecutor

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable.replace("\\", "/")

SERVER_SNIPPET = (
    "import http.server, socketserver, sys\n"
    "s = socketserver.TCPServer(('127.0.0.1', 0), http.server.SimpleHTTPRequestHandler)\n"
    "print('Serving on http://127.0.0.1:%d/' % s.server_address[1], flush=True)\n"
    "s.serve_forever()\n"
)


def claim(cmd, cid="c01"):
    return Claim(id=cid, kind="shell", span=Span(start_line=1, end_line=1, text=cmd), command=cmd)


def port_open(url: str) -> bool:
    host, port = url.split("//")[1].split("/")[0].split(":")
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex((host, int(port))) == 0


@pytest.fixture
def session(tmp_path):
    with ShellSession(workdir=tmp_path, timeout_s=20, recipes_only=False) as s:
        yield s


def test_executable_command_strips_prompts():
    assert executable_command("$ npm install\nadded 3 packages\n$ npm run dev") == "npm install\nnpm run dev"
    assert executable_command("echo hi") == "echo hi"


def test_detect_url():
    assert detect_url(" * Running on http://127.0.0.1:5000") == "http://127.0.0.1:5000"
    assert detect_url("  ➜  Local:   http://localhost:5173/") == "http://localhost:5173/"
    assert detect_url("Serving HTTP on 0.0.0.0 port 8000 (http://0.0.0.0:8000/) ...") == "http://127.0.0.1:8000/"
    assert detect_url("see https://example.com") is None


def test_echo_exit_zero(session):
    ev = session.run(claim("echo hello attest"))
    assert ev.exit_code == 0
    assert ev.timed_out is False
    assert "hello attest" in ev.stdout_tail
    assert ev.detected_url is None
    assert ev.kind == "shell"
    assert ev.command == "echo hello attest"


def test_failure_exit_code(session):
    ev = session.run(claim("exit 3"))
    assert ev.exit_code == 3


def test_missing_command_is_nonzero(session):
    ev = session.run(claim("definitely-not-a-command-xyz"))
    assert ev.exit_code not in (0, None)


def test_timeout_kills_process(tmp_path):
    with ShellSession(workdir=tmp_path, timeout_s=1.5, recipes_only=False) as s:
        t0 = time.time()
        ev = s.run(claim(f'"{PY}" -c "import time; print(\'start\', flush=True); time.sleep(30)"'))
        assert time.time() - t0 < 15
    assert ev.timed_out is True
    assert ev.exit_code is None
    assert "start" in ev.stdout_tail


def test_tail_is_capped(session):
    ev = session.run(claim(f'"{PY}" -c "for i in range(200): print(i)"'))
    lines = ev.stdout_tail.splitlines()
    assert len(lines) == 50
    assert lines[-1] == "199"


def test_cwd_persists_between_claims(session, tmp_path):
    session.run(claim("mkdir sub && cd sub && echo made"))
    ev = session.run(claim("pwd", "c02"))
    assert ev.stdout_tail.rstrip("/").endswith("sub")
    assert session.cwd.name == "sub"
    assert session.cwd.exists()


def test_server_detected_kept_alive_and_cleaned(tmp_path):
    script = tmp_path / "srv.py"
    script.write_text(SERVER_SNIPPET)
    with ShellSession(workdir=tmp_path, timeout_s=20, recipes_only=False) as s:
        ev = s.run(claim(f'"{PY}" srv.py'))
        assert ev.exit_code is None
        assert ev.service_ready is True
        assert ev.timed_out is False
        assert ev.detected_url and ev.detected_url.startswith("http://127.0.0.1:")
        assert s.base_url == ev.detected_url
        assert port_open(ev.detected_url)
        assert len(s.servers) == 1
        pid = s.servers[0].proc.pid
    # after close: no orphan, port closed
    time.sleep(0.5)
    assert not port_open(ev.detected_url)
    assert s.servers == []
    if sys.platform == "win32":
        import subprocess

        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True).stdout
        assert str(pid) not in out


def test_url_then_exit_uses_real_exit_code(session):
    ev = session.run(claim("echo 'see http://localhost:9999/' && exit 2"))
    assert ev.exit_code == 2
    assert ev.service_ready is False
    assert ev.detected_url == "http://localhost:9999/"
    assert session.servers == []


def test_printing_url_without_a_listener_is_not_service_ready(tmp_path):
    with ShellSession(workdir=tmp_path, timeout_s=0.6, server_grace_s=0.1, recipes_only=False) as s:
        ev = s.run(claim(f'echo "http://127.0.0.1:0/" && "{PY}" -c "import time; time.sleep(5)"'))

    assert ev.timed_out is True
    assert ev.service_ready is False
    assert ev.exit_code is None


def test_untrusted_shell_does_not_inherit_host_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("ATTEST_TEST_HOST_SECRET", "not-a-real-secret")
    code = "import os; print('present' if 'ATTEST_TEST_HOST_SECRET' in os.environ else 'absent')"
    recipes = ApprovedRecipeExecutor({"probe": [PY, "-c", code]})
    with ShellSession(workdir=tmp_path, timeout_s=10, recipe_executor=recipes, recipes_only=True) as s:
        ev = s.run(claim("probe"))
    assert ev.stdout_tail.strip() == "absent"


def test_execute_closes_started_server_when_later_runner_raises(tmp_path):
    script = tmp_path / "srv.py"
    script.write_text(SERVER_SNIPPET)
    shell_claim = claim(f'"{PY}" srv.py', "c01")
    browser = Claim(
        id="c02",
        kind="browser",
        span=Span(start_line=2, end_line=2, text="open page"),
        objective="Open the page",
        store_as="label",
        depends_on=["c01"],
    )

    class RaisingRunner:
        name = "raising"

        def observe(self, claim, base_url):
            raise RuntimeError("synthetic browser failure")

        def close(self):
            pass

    urls = []

    def record(claim, verdict):
        if claim.id == "c01":
            urls.append(verdict.evidence.detected_url)

    with pytest.raises(RuntimeError, match="synthetic browser failure"):
        execute([shell_claim, browser], RaisingRunner(), Rules(), tmp_path, log=record, recipes_only=False)

    assert urls and urls[0]
    time.sleep(0.2)
    assert not port_open(urls[0])


def test_recipe_only_session_executes_only_exact_approved_recipe(tmp_path):
    recipes = ApprovedRecipeExecutor({"say hello": [PY, "-c", "print('hello')"]})
    with ShellSession(workdir=tmp_path, timeout_s=10, recipe_executor=recipes, recipes_only=True) as s:
        ev = s.run(claim("say hello"))
        assert ev.exit_code == 0
        assert ev.stdout_tail.strip() == "hello"
        blocked = s.run(claim("echo not approved", "c02"))
        assert blocked.exit_code == 126
        assert "no approved recipe" in blocked.stdout_tail


def test_pipeline_judges_each_browser_claim_before_running_the_next(tmp_path):
    first = Claim(
        id="c01",
        kind="browser",
        span=Span(start_line=1, end_line=1, text="Click Save"),
        objective="Click Save",
        expects="Save",
        store_as="save_label",
    )
    second = Claim(
        id="c02",
        kind="browser",
        span=Span(start_line=2, end_line=2, text="Delete item"),
        objective="Delete item",
        expects="Confirm deletion",
        store_as="delete_label",
        depends_on=["c01"],
    )
    runner = FakeBrowserRunner({"c01": BrowserObservation(status="failed")})
    _, verdicts, session = execute([first, second], runner, Rules(), tmp_path)
    try:
        assert [claim_id for claim_id, _ in runner.calls] == ["c01"]
        assert verdicts[0].status == "broken"
        assert verdicts[1].status == "skipped"
    finally:
        session.close()


def test_pipeline_does_not_treat_missing_dependency_as_authorization(tmp_path):
    marker = tmp_path / "missing-dependency-side-effect"
    command = claim(f'echo unauthorized > "{marker}"')
    command = command.model_copy(update={"depends_on": ["missing"]})
    evidence, verdicts, session = execute([command], FakeBrowserRunner(), Rules(), tmp_path)
    try:
        assert "c01" not in evidence
        assert verdicts[0].status == "unverifiable"
        assert not marker.exists()
    finally:
        session.close()


def test_pipeline_blocks_unapproved_shell_claims_when_recipes_only(tmp_path):
    # The factory prove path keeps the compatibility shell. Approved-recipe mode
    # is what refuses document shell commands that are not on the list.
    marker = tmp_path / "must-not-be-created"
    claim_text = f'echo side-effect > "{marker}"'
    evidence, verdicts, session = execute(
        [claim(claim_text)], FakeBrowserRunner(), Rules(), tmp_path, recipes_only=True,
    )
    try:
        assert evidence["c01"].exit_code == 126
        assert verdicts[0].status == "broken"
        assert not marker.exists()
    finally:
        session.close()


@pytest.mark.skipif(not (ROOT / ".venv").exists(), reason="needs the dev venv with flask installed")
def test_flask_quickstart_end_to_end():
    doc = ingest(str(ROOT / "demo" / "flask.md"))
    claims = fences_by_regex(doc)
    scripts = Path(sys.executable).parent
    env = {"PATH": f"{scripts}{os.pathsep}{os.environ['PATH']}"}
    scratch = new_scratch("attest-flask-")
    with ShellSession(workdir=scratch, timeout_s=120, env=env, recipes_only=False) as s:
        evs = [s.run(c) for c in claims]
        assert [e.exit_code for e in evs] == [0, 0, 0], [e.stdout_tail for e in evs]
        assert (scratch / "hello.py").exists()
        assert evs[2].detected_url == "http://127.0.0.1:5000"
        assert s.base_url == "http://127.0.0.1:5000"
        import urllib.request

        body = urllib.request.urlopen("http://127.0.0.1:5000/", timeout=5).read().decode()
        assert "Hello, World!" in body
    time.sleep(0.5)
    assert not port_open("http://127.0.0.1:5000")

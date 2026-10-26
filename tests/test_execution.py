from __future__ import annotations

import sys
import time

from attest.execution.supervisor import ProcessSupervisor


def test_wall_clock_deadline_kills_blocked_child_process_group():
    supervisor = ProcessSupervisor(timeout_s=0.25, terminate_grace_s=0.1)
    started = time.monotonic()
    result = supervisor.run([sys.executable, "-c", "import time; print('started', flush=True); time.sleep(30)"])

    assert result.timed_out is True
    assert "started" in result.stdout
    assert time.monotonic() - started < 3


def test_wall_clock_deadline_kills_grandchild_that_inherits_output_pipe():
    supervisor = ProcessSupervisor(timeout_s=0.25, terminate_grace_s=0.1)
    code = "import subprocess,sys; subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])"
    started = time.monotonic()
    result = supervisor.run([sys.executable, "-c", code])

    assert result.timed_out is True
    assert time.monotonic() - started < 3


def test_process_supervisor_reports_normal_exit_and_cleans_up():
    supervisor = ProcessSupervisor(timeout_s=5)
    result = supervisor.run([sys.executable, "-c", "print('done')"])

    assert result.returncode == 0
    assert result.timed_out is False
    assert result.stdout.strip() == "done"


def _alive(pid: int) -> bool:
    import os
    import subprocess

    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def test_shell_session_kills_an_orphaned_grandchild(tmp_path):
    """A command's grandchild whose parent already exited (the venv's python.exe launcher on
    Windows) must not outlive the session: on Windows the job object reaches it."""
    from attest.models import Claim, Span
    from attest.shell import ShellSession

    pid_file = tmp_path / "grandchild.pid"
    spawn = (
        "import subprocess, sys; "
        "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
        f"open(r'{pid_file}', 'w').write(str(p.pid))"
    )
    py = sys.executable.replace("\\", "/")
    claim = Claim(id="c01", kind="shell", span=Span(start_line=1, end_line=1, text="x"),
                  command=f'"{py}" -c "{spawn}"')
    started = time.monotonic()
    with ShellSession(workdir=tmp_path, timeout_s=1.0, recipes_only=False) as session:
        session.run(claim)
    assert time.monotonic() - started < 10
    grandchild = int(pid_file.read_text())
    deadline = time.monotonic() + 5
    while _alive(grandchild) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not _alive(grandchild), f"grandchild {grandchild} survived the session"


def test_attach_process_tree_is_a_job_on_windows_and_a_no_op_elsewhere():
    import os
    import subprocess

    from attest.execution.supervisor import attach_process_tree, terminate_process_tree

    # started the way Attest starts commands: its own process group (Windows) or session (POSIX)
    flags = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], **flags)
    try:
        assert attach_process_tree(proc) is (os.name == "nt")
        assert attach_process_tree(proc) is False  # never twice
    finally:
        terminate_process_tree(proc)
    assert proc.poll() is not None

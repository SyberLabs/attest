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

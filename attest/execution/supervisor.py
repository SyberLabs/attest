"""Wall-clock-bounded subprocess execution with process-group cleanup."""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence


_JOB_ATTR = "_attest_job"


def _windows_job_api():
    import ctypes
    from ctypes import wintypes

    class IoCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
        )]

    class BasicLimits(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class ExtendedLimits(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", BasicLimits),
            ("IoInfo", IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateJobObject.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    return ctypes, kernel32, ExtendedLimits


def attach_process_tree(proc: subprocess.Popen) -> bool:
    """Windows: put ``proc`` in a job object that kills every process in it when the
    job is closed, so a whole tree dies together however it was built. A child whose
    intermediate parent already exited (a venv's python.exe launcher, npm through cmd)
    is otherwise unreachable by walking parent links. Returns False when the job could
    not be made, and the tree walk stays the only cleanup. A no-op elsewhere: POSIX
    kills the process group."""
    if os.name != "nt" or getattr(proc, _JOB_ATTR, None) is not None:
        return False
    try:
        ctypes, kernel32, ExtendedLimits = _windows_job_api()
        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return False
        limits = ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        ok = kernel32.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits))  # JobObjectExtendedLimitInformation
        if ok:
            ok = kernel32.AssignProcessToJobObject(job, int(proc._handle))  # type: ignore[attr-defined]
        if not ok:
            kernel32.CloseHandle(job)
            return False
        setattr(proc, _JOB_ATTR, job)
        return True
    except (OSError, AttributeError, ValueError):
        return False


def _close_job(proc: subprocess.Popen) -> None:
    """Terminate everything left in ``proc``'s job, then release the job."""
    job = getattr(proc, _JOB_ATTR, None)
    if job is None:
        return
    setattr(proc, _JOB_ATTR, None)
    try:
        _ctypes, kernel32, _limits = _windows_job_api()
        kernel32.TerminateJobObject(job, 1)
        kernel32.CloseHandle(job)
    except (OSError, AttributeError):
        pass


def _windows_descendants(root_pid: int) -> set[int]:
    """Return processes whose parent chain still names this owned root PID."""
    import ctypes
    from ctypes import wintypes

    class ProcessEntry(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    kernel32.Process32NextW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
    invalid_handle = ctypes.c_void_p(-1).value
    if not snapshot or snapshot == invalid_handle:
        return set()
    try:
        parents: dict[int, int] = {}
        entry = ProcessEntry()
        entry.dwSize = ctypes.sizeof(ProcessEntry)
        if kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            while True:
                parents[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
                entry.dwSize = ctypes.sizeof(ProcessEntry)
                if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                    break
        descendants: set[int] = set()
        frontier = {root_pid}
        while frontier:
            children = {pid for pid, parent in parents.items() if parent in frontier and pid not in descendants}
            descendants.update(children)
            frontier = children
        return descendants
    finally:
        kernel32.CloseHandle(snapshot)


def _terminate_windows_tree(root_pid: int) -> None:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    for _ in range(5):
        pids = _windows_descendants(root_pid)
        pids.add(root_pid)
        for pid in pids:
            handle = kernel32.OpenProcess(0x0001, False, pid)  # PROCESS_TERMINATE
            if handle:
                try:
                    kernel32.TerminateProcess(handle, 1)
                finally:
                    kernel32.CloseHandle(handle)
        if len(pids) == 1:
            break
        time.sleep(0.05)


def terminate_process_tree(proc: subprocess.Popen, grace_s: float = 0.2) -> None:
    """Terminate the process group/tree created for ``proc`` only."""
    if os.name == "nt":
        # the job reaches every process started inside it, orphaned or not; the
        # tree walk then covers anything that escaped before the job was attached
        _close_job(proc)
        try:
            _terminate_windows_tree(proc.pid)
        except (OSError, AttributeError):
            if proc.poll() is None:
                try:
                    proc.kill()
                except OSError:
                    pass
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except OSError:
                pass
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except OSError:
        pass
    if grace_s:
        time.sleep(grace_s)
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, OSError):
        pass
    if proc.poll() is None:
        try:
            proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except OSError:
                pass


@dataclass(frozen=True)
class ProcessResult:
    returncode: int | None
    stdout: str
    timed_out: bool


class ProcessSupervisor:
    """Run one argv without a shell and enforce a deadline outside pipe reads.

    The watchdog is a separate thread, so a child that stops writing while
    keeping stdout open cannot block deadline handling. Every child gets its
    own process group; cleanup targets only that group, never a port owner.
    """

    def __init__(self, timeout_s: float, terminate_grace_s: float = 0.5) -> None:
        if timeout_s <= 0:
            raise ValueError("timeout_s must be positive")
        self.timeout_s = timeout_s
        self.terminate_grace_s = max(0.0, terminate_grace_s)

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | str | None = None,
        env: Mapping[str, str] | None = None,
        timeout_s: float | None = None,
    ) -> ProcessResult:
        limit = self.timeout_s if timeout_s is None else timeout_s
        if limit <= 0:
            raise ValueError("timeout_s must be positive")
        if not argv:
            raise ValueError("argv must not be empty")
        kwargs: dict = {}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        proc = subprocess.Popen(
            list(argv),
            cwd=str(cwd) if cwd is not None else None,
            env=dict(env) if env is not None else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            **kwargs,
        )
        attach_process_tree(proc)
        timed_out = threading.Event()
        finished = threading.Event()

        def watchdog() -> None:
            if not finished.wait(limit):
                timed_out.set()
                self._terminate_group(proc)

        timer = threading.Thread(target=watchdog, name=f"attest-deadline-{proc.pid}", daemon=True)
        try:
            timer.start()
        except BaseException:
            self._terminate_group(proc)
            if proc.stdout is not None:
                proc.stdout.close()
            raise
        output = ""
        try:
            output, _ = proc.communicate()
            return ProcessResult(proc.returncode, output, timed_out.is_set())
        except BaseException:
            self._terminate_group(proc)
            raise
        finally:
            finished.set()
            timer.join(timeout=self.terminate_grace_s + 1.0)
            # The parent can exit while a grandchild inherits stdout. Once the
            # pipe closes, clean any remainder in our owned process group.
            self._terminate_group(proc)
            if proc.stdout is not None:
                proc.stdout.close()

    def _terminate_group(self, proc: subprocess.Popen) -> None:
        terminate_process_tree(proc, grace_s=self.terminate_grace_s)

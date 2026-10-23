"""Shell runner: executes shell claims in order inside a scratch directory.

Each command runs through a real shell (Git Bash or sh when available) with a
timeout. The runner captures the exit code and the last 50 lines of output,
watches for the first local URL a command prints, and when it sees one it keeps
that process alive as a server for later browser claims. ``close()`` kills every
process tree it started. Nothing here knows about browsers.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from .models import Claim, Evidence

URL_RE = re.compile(r"https?://(?:localhost|127\.0\.0\.1|0\.0\.0\.0)(?::\d+)?(?:/[^\s\"'<>)\]]*)?")
CWD_MARK = "\x1eATTEST_CWD\x1e"
TAIL_LINES = 50
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def strip_ansi(s: str) -> str:
    return _ANSI_RE.sub("", s)


def executable_command(text: str) -> str:
    """Turn a fenced block into something a shell can run.

    Blocks written console-style (lines prefixed with ``$ ``) keep only the
    prompt lines, with the prompt removed. Everything else runs as written.
    """
    lines = text.splitlines()
    if any(ln.lstrip().startswith("$ ") for ln in lines):
        return "\n".join(ln.lstrip()[2:] for ln in lines if ln.lstrip().startswith("$ "))
    return text


def detect_url(text: str) -> str | None:
    m = URL_RE.search(text)
    if not m:
        return None
    url = m.group(0).rstrip(".,;:")
    return url.replace("0.0.0.0", "127.0.0.1")


def find_shell() -> list[str]:
    """Pick a POSIX shell. Git Bash on Windows, sh elsewhere, cmd as a last resort."""
    for name in ("bash", "sh"):
        path = shutil.which(name)
        if path and "System32" not in path:  # skip the WSL launcher shim
            return [path, "-c"]
    if sys.platform == "win32":
        return ["cmd.exe", "/d", "/c"]
    return ["/bin/sh", "-c"]


@dataclass
class RunningProcess:
    claim_id: str
    proc: subprocess.Popen
    url: str | None


@dataclass
class ShellSession:
    workdir: Path
    timeout_s: float = 180.0
    shell: list[str] = field(default_factory=find_shell)
    server_grace_s: float = 1.5
    servers: list[RunningProcess] = field(default_factory=list)
    base_url: str | None = None
    cwd: Path | None = None
    env: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.workdir = Path(self.workdir)
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.cwd = self.cwd or self.workdir
        base = dict(os.environ)
        base.setdefault("PYTHONUNBUFFERED", "1")
        base.setdefault("CI", "1")  # keeps npm, vite and friends from prompting or opening a browser
        base.setdefault("BROWSER", "none")
        base.setdefault("FORCE_COLOR", "0")
        base["NO_COLOR"] = "1"
        base.update(self.env)
        self.env = base

    # -- running -----------------------------------------------------------------

    def _wrap(self, cmd: str) -> str:
        """Append a cwd marker so `cd` in one block carries to the next (POSIX shells only)."""
        if self.shell[0].endswith("cmd.exe"):
            return cmd
        return f'{cmd}\n__attest_rc=$?\nprintf "\\n{CWD_MARK}%s\\n" "$PWD"\nexit $__attest_rc'

    def run(self, claim: Claim, timeout_s: float | None = None) -> Evidence:
        assert claim.kind == "shell" and claim.command is not None
        timeout_s = timeout_s or self.timeout_s
        cmd = executable_command(claim.command)
        creation = {}
        if sys.platform == "win32":
            creation["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            creation["start_new_session"] = True
        proc = subprocess.Popen(
            [*self.shell, self._wrap(cmd)],
            cwd=str(self.cwd),
            env=self.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            **creation,
        )
        tail: deque[str] = deque(maxlen=TAIL_LINES)
        state = {"url": None, "cwd": None}
        lock = threading.Lock()

        def pump() -> None:
            assert proc.stdout is not None
            for raw in proc.stdout:
                line = strip_ansi(raw.rstrip("\n"))
                if line.startswith(CWD_MARK):
                    state["cwd"] = line[len(CWD_MARK):].strip()
                    continue
                with lock:
                    tail.append(line)
                    if state["url"] is None:
                        url = detect_url(line)
                        if url:
                            state["url"] = url

        t = threading.Thread(target=pump, daemon=True)
        t.start()

        deadline = time.monotonic() + timeout_s
        timed_out = False
        kept_alive = False
        while True:
            rc = proc.poll()
            if rc is not None:
                break
            if state["url"] is not None:
                # a server announced itself: give it a moment, then leave it running
                time.sleep(self.server_grace_s)
                if proc.poll() is None:
                    kept_alive = True
                    break
                continue
            if time.monotonic() > deadline:
                timed_out = True
                self._kill(proc)
                break
            time.sleep(0.05)

        if not kept_alive:
            t.join(timeout=5)
        else:
            self.servers.append(RunningProcess(claim.id, proc, state["url"]))
            self.base_url = state["url"]

        if state["cwd"]:
            new = Path(state["cwd"])
            if not new.is_absolute() or not new.exists():
                new = _from_posix(state["cwd"])
            if new.exists():
                self.cwd = new

        with lock:
            lines = list(tail)
        exit_code: int | None
        if kept_alive:
            exit_code = 0
        elif timed_out:
            exit_code = None
        else:
            exit_code = proc.returncode
        return Evidence(
            claim_id=claim.id,
            kind="shell",
            command=claim.command,
            exit_code=exit_code,
            timed_out=timed_out,
            stdout_tail="\n".join(lines),
            detected_url=state["url"],
        )

    # -- cleanup -----------------------------------------------------------------

    @staticmethod
    def _kill(proc: subprocess.Popen) -> None:
        if proc.poll() is not None:
            return
        try:
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    capture_output=True,
                    timeout=15,
                )
            else:
                import signal

                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (OSError, subprocess.SubprocessError):
            pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    def close(self) -> None:
        for rp in self.servers:
            self._kill(rp.proc)
        self.servers.clear()

    def __enter__(self) -> "ShellSession":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def _from_posix(p: str) -> Path:
    """Git Bash prints /d/foo for D:\\foo. Map it back."""
    m = re.match(r"^/([a-zA-Z])/(.*)$", p)
    if m:
        return Path(f"{m.group(1).upper()}:/{m.group(2)}")
    return Path(p)


def new_scratch(prefix: str = "attest-") -> Path:
    return Path(tempfile.mkdtemp(prefix=prefix))

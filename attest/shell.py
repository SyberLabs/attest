"""Execute shell claims in a scratch directory with bounded process cleanup.

The factory path inherits the host environment and runs the compatibility shell.
Approved-recipe mode maps an exact command to argv, drops host secrets, and
never starts a shell. The runner captures bounded output, probes a printed
local URL before retaining a server, and kills only process trees it started.
Nothing here knows about browsers.
"""

from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from .execution.recipes import ApprovedRecipeExecutor
from .execution.supervisor import attach_process_tree, terminate_process_tree
from .models import Claim, Evidence

URL_RE = re.compile(r"https?://(?:localhost|127\.0\.0\.1|0\.0\.0\.0)(?::\d+)?(?:/[^\s\"'<>)\]]*)?")
TAIL_LINES = 50
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")

GIT_BASH_CANDIDATES = (
    r"C:\Program Files\Git\bin\bash.exe",
    r"C:\Program Files (x86)\Git\bin\bash.exe",
)


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


def _service_ready(url: str, timeout_s: float = 0.2) -> bool:
    """Check a local TCP listener without issuing an HTTP request or mutation."""
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        if parsed.scheme not in {"http", "https"} or host not in {"localhost", "127.0.0.1"}:
            return False
        port = parsed.port
        if port is None:
            port = 443 if parsed.scheme == "https" else 80
        with socket.create_connection((host, port), timeout=timeout_s):
            return True
    except (OSError, ValueError):
        return False


def find_shell() -> list[str]:
    """Pick a POSIX shell. Git Bash on Windows (by its install path first, since
    PATH may hold Cygwin or the WSL shim), sh elsewhere, cmd as a last resort."""
    override = os.environ.get("ATTEST_SHELL")
    if override:
        return [override, "-c"]
    if sys.platform == "win32":
        for cand in GIT_BASH_CANDIDATES:
            if Path(cand).exists():
                return [cand, "-c"]
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
    reader: threading.Thread | None = None


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
    recipe_executor: ApprovedRecipeExecutor | None = None
    # Compatibility shell stays the default so the factory demo can run document
    # commands. Approved recipes are opt-in and use a curated environment.
    recipes_only: bool = False

    def __post_init__(self) -> None:
        self.workdir = Path(self.workdir)
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.cwd = self.cwd or self.workdir
        if self.recipes_only and self.recipe_executor is None:
            self.recipe_executor = ApprovedRecipeExecutor({})
        if self.recipes_only:
            search_path = [str(Path(sys.executable).resolve().parent), os.defpath]
            base = {
                "PATH": os.pathsep.join(dict.fromkeys(search_path)),
                "HOME": str(self.workdir),
                "TMPDIR": str(self.workdir),
                "TEMP": str(self.workdir),
                "TMP": str(self.workdir),
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "PYTHONUNBUFFERED": "1",
                "CI": "1",
                "BROWSER": "none",
                "FORCE_COLOR": "0",
                "NO_COLOR": "1",
            }
            if os.name == "nt":
                for key in ("SYSTEMROOT", "WINDIR", "PATHEXT", "COMSPEC"):
                    if os.environ.get(key):
                        base[key] = os.environ[key]
        else:
            base = dict(os.environ)
            base.setdefault("PYTHONUNBUFFERED", "1")
            base.setdefault("CI", "1")
            base.setdefault("BROWSER", "none")
            base.setdefault("FORCE_COLOR", "0")
            base["NO_COLOR"] = "1"
        base.update(self.env)
        self.env = base
        self._cwd_file = self.workdir / ".attest-cwd"

    @property
    def is_posix_shell(self) -> bool:
        return not self.shell[0].lower().endswith("cmd.exe")

    # -- running -----------------------------------------------------------------

    def _wrap(self, cmd: str) -> str:
        """Record the final working directory in a side file so `cd` in one block
        carries to the next. `pwd -W` gives a Windows path under Git Bash."""
        if not self.is_posix_shell:
            return cmd
        marker = str(self._cwd_file).replace("\\", "/")
        return f"{cmd}\n__attest_rc=$?\n(pwd -W 2>/dev/null || pwd) > '{marker}'\nexit $__attest_rc"

    def run(self, claim: Claim, timeout_s: float | None = None) -> Evidence:
        assert claim.kind == "shell" and claim.command is not None
        timeout_s = self.timeout_s if timeout_s is None else timeout_s
        if timeout_s <= 0:
            raise ValueError("timeout_s must be positive")
        cmd = executable_command(claim.command)
        if self.recipes_only:
            assert self.recipe_executor is not None
            try:
                argv = self.recipe_executor.resolve(cmd)
            except PermissionError as exc:
                return Evidence(
                    claim_id=claim.id,
                    kind="shell",
                    command=claim.command,
                    exit_code=126,
                    stdout_tail=str(exc),
                )
        else:
            self._cwd_file.unlink(missing_ok=True)
            argv = [*self.shell, self._wrap(cmd)]
        creation: dict = {}
        if sys.platform == "win32":
            creation["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            creation["start_new_session"] = True
        proc = subprocess.Popen(
            argv,
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
        # Windows: one job per command, so killing it reaches the whole tree
        attach_process_tree(proc)
        tail: deque[str] = deque(maxlen=TAIL_LINES)
        state: dict[str, str | None] = {"url": None}
        lock = threading.Lock()

        def pump() -> None:
            assert proc.stdout is not None
            for raw in proc.stdout:
                line = strip_ansi(raw.rstrip("\n"))
                with lock:
                    tail.append(line)
                    if state["url"] is None:
                        url = detect_url(line)
                        if url:
                            state["url"] = url

        reader = threading.Thread(target=pump, name=f"attest-output-{proc.pid}", daemon=True)
        try:
            reader.start()
        except BaseException:
            self._kill(proc)
            raise
        timed_out_event = threading.Event()
        finished = threading.Event()

        def watchdog() -> None:
            if not finished.wait(timeout_s):
                timed_out_event.set()
                self._kill(proc)

        deadline_thread = threading.Thread(target=watchdog, name=f"attest-deadline-{proc.pid}", daemon=True)
        try:
            deadline_thread.start()
        except BaseException:
            finished.set()
            self._kill(proc)
            reader.join(timeout=1)
            raise
        kept_alive = False
        try:
            deadline = time.monotonic() + timeout_s
            while True:
                rc = proc.poll()
                if rc is not None:
                    break
                if timed_out_event.is_set() or time.monotonic() >= deadline:
                    timed_out_event.set()
                    self._kill(proc)
                    break
                with lock:
                    url = state["url"]
                if url is not None:
                    # A printed URL is only a hint; require a bounded TCP probe
                    # and a live child before advertising the service as ready.
                    grace_deadline = min(time.monotonic() + self.server_grace_s, deadline)
                    ready = False
                    while proc.poll() is None and not timed_out_event.is_set() and time.monotonic() < grace_deadline:
                        if _service_ready(url):
                            ready = True
                            break
                        time.sleep(min(0.05, max(0.0, grace_deadline - time.monotonic())))
                    if ready and proc.poll() is None and not timed_out_event.is_set() and time.monotonic() < deadline:
                        finished.set()  # the independent deadline ends once readiness is observed
                        self.servers.append(RunningProcess(claim.id, proc, url, reader))
                        kept_alive = True
                        self.base_url = url
                        break
                    continue
                time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
        except BaseException:
            self._kill(proc)
            raise
        finally:
            finished.set()
            deadline_thread.join(timeout=2)
            if not kept_alive:
                # Also reap descendants when the shell leader exited first.
                self._kill(proc)
                reader.join(timeout=1)
                if not self.recipes_only:
                    self._update_cwd()

        with lock:
            lines = list(tail)
        timed_out = timed_out_event.is_set() and not kept_alive
        return Evidence(
            claim_id=claim.id,
            kind="shell",
            command=claim.command,
            exit_code=None if kept_alive or timed_out else proc.returncode,
            timed_out=timed_out,
            service_ready=kept_alive,
            stdout_tail="\n".join(lines),
            detected_url=state["url"],
        )

    def _update_cwd(self) -> None:
        if not self._cwd_file.exists():
            return
        raw = self._cwd_file.read_text(encoding="utf-8", errors="replace").strip()
        self._cwd_file.unlink(missing_ok=True)
        if not raw:
            return
        new = Path(raw)
        if not new.is_absolute() or not new.exists():
            new = _from_posix(raw)
        if new.exists():
            self.cwd = new

    # -- cleanup -----------------------------------------------------------------

    @staticmethod
    def _kill(proc: subprocess.Popen) -> None:
        # Children are isolated in a dedicated process group/tree; never kill
        # an unrelated process merely because it owns a printed port.
        terminate_process_tree(proc)

    def close(self) -> None:
        for rp in self.servers:
            self._kill(rp.proc)
            if rp.reader is not None:
                rp.reader.join(timeout=1)
            if rp.url:
                self._kill_listener(rp.url)
        self.servers.clear()

    @staticmethod
    def _kill_listener(url: str) -> None:
        """Windows: a dev server started through bash, npm.cmd and cmd.exe can outlive the
        tree kill. Kill whatever still listens on the port the server announced."""
        if sys.platform != "win32":
            return
        m = re.search(r":(\d+)", url.split("//", 1)[-1])
        if not m:
            return
        try:
            # no -p filter: Vite on Node 22 binds localhost as [::1], which -p TCP (IPv4) hides
            out = subprocess.run(["netstat", "-ano"], capture_output=True, text=True, timeout=15).stdout
        except (OSError, subprocess.SubprocessError):
            return
        pids = {ln.split()[-1] for ln in out.splitlines() if "LISTENING" in ln and re.search(rf"\]?:{m.group(1)}\s", ln)}
        for pid in pids - {"0"}:
            subprocess.run(["taskkill", "/F", "/T", "/PID", pid], capture_output=True, timeout=15)

    def __enter__(self) -> "ShellSession":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def _from_posix(p: str) -> Path:
    """Git Bash prints /d/foo and Cygwin /cygdrive/d/foo for D:\\foo. Map them back."""
    m = re.match(r"^/(?:cygdrive/)?([a-zA-Z])(?:/(.*))?$", p)
    if m:
        return Path(f"{m.group(1).upper()}:/{m.group(2) or ''}")
    return Path(p)


def new_scratch(prefix: str = "attest-") -> Path:
    return Path(tempfile.mkdtemp(prefix=prefix))

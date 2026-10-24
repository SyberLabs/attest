"""Attest CLI: prove | serve | badge | ask."""

from __future__ import annotations

import functools
import hashlib
import http.server
import os
import re
import socketserver
import stat
import sys
import webbrowser
from pathlib import Path
from urllib.parse import urlsplit

import typer

from . import __version__
from .pipeline import ATTEST_DIR, cleanup_scratch, export_badge, prove, render_result
from .render import _image_mime, content_security_policy
from .runner import FakeBrowserRunner

app = typer.Typer(add_completion=False, no_args_is_help=True, help="Docs that can't lie: prove every step of a document.")

STATUS_MARK = {"proven": "ok", "stale": "~ ", "broken": "x ", "unverifiable": "- ", "skipped": ">>"}


def _runner(name: str, fake_observations: Path | None):
    if name == "fake":
        if fake_observations:
            return FakeBrowserRunner.from_file(fake_observations)
        return FakeBrowserRunner()
    if name == "kane":
        from .kane import KaneBrowserRunner  # SATURDAY: the real browser runner

        return KaneBrowserRunner()
    raise typer.BadParameter(f"unknown runner {name!r}; use fake or kane")


def _log(claim, verdict) -> None:
    mark = STATUS_MARK.get(verdict.status, "?")
    typer.echo(f"  {mark} {claim.id} {claim.kind:7} L{claim.span.start_line:<4} {verdict.status:12} {verdict.reason}")


@app.callback(invoke_without_command=True)
def _main(version: bool = typer.Option(False, "--version", help="Show the version and exit.")) -> None:
    if version:
        typer.echo(f"attest {__version__}")
        raise typer.Exit()


@app.command("prove")
def prove_cmd(
    source: str = typer.Argument(..., help="Path or URL of the document to prove."),
    runner: str = typer.Option("fake", "--runner", help="Browser runner: fake (offline) or kane (event day)."),
    fake_observations: Path | None = typer.Option(None, "--fake-observations", help="JSON of scripted observations for the fake runner."),
    loop: bool = typer.Option(False, "--loop/--no-loop", help="Send stale or broken steps to the coding agent and re-prove them."),
    apply: bool = typer.Option(False, "--apply", help="With --loop: write the agent's doc patch back to the source file."),
    open_page: bool = typer.Option(False, "--open", help="Open the rendered page in a browser."),
    keep_scratch: bool = typer.Option(False, "--keep-scratch", help="Leave the scratch directory in place."),
    home: Path = typer.Option(ATTEST_DIR, "--home", help="Where ledger and pages live."),
) -> None:
    """Execute every claim in a document and render it with badges."""
    br = _runner(runner, fake_observations)
    typer.echo(f"attest prove {source}  (runner: {br.name})")
    result = prove(source, runner=br, home=home, log=_log, keep_servers=loop)
    if loop and result.failing():
        from .loop import run_loop

        typer.echo(f"loop: {len(result.failing())} failing claim(s) go to the agent")
        result = run_loop(result, br, home=home, log=_log, apply_to_source=apply)
    br.close()
    session = getattr(result, "_session", None)
    if session is not None:
        session.close()
    if not keep_scratch:
        cleanup_scratch(result.scratch)
    render_result(result, home, br.name)
    strip_line = _strip_line(result, home)
    typer.echo(strip_line)
    typer.echo(f"page: {result.page}")
    if open_page and result.page:
        webbrowser.open(result.page.resolve().as_uri())


def _strip_line(result, home: Path) -> str:
    from .ledger import Ledger

    s = Ledger(home / "ledger.jsonl").summary(result.doc.sha256, result.turn)
    return (
        f"{s['claims']} claims | {s['proven']} proven | {s['stale']} stale | {s['broken']} broken | "
        f"{s['unverifiable']} unverifiable | {s['skipped']} skipped | turn {s['turn']}"
    )


def _latest_dir(home: Path) -> Path:
    root = home.resolve()
    pointer = root / "latest"
    try:
        pointer_path = pointer.resolve(strict=True)
        pointer_path.relative_to(root)
        target = Path(pointer_path.read_text(encoding="utf-8").strip())
        if not target.is_absolute():
            target = root / target
        directory = target.resolve(strict=True)
        directory.relative_to(root)
    except (OSError, ValueError):
        typer.echo("latest page must resolve inside the Attest home directory", err=True)
        raise typer.Exit(1)
    if not directory.is_dir():
        typer.echo("latest page must name a rendered directory", err=True)
        raise typer.Exit(1)
    return directory


@app.command()
def serve(
    port: int = typer.Option(4848, "--port"),
    home: Path = typer.Option(ATTEST_DIR, "--home"),
    open_page: bool = typer.Option(False, "--open", help="Open the page in a browser."),
) -> None:
    """Serve the most recent review on loopback only; do not expose it through a public proxy."""
    directory = _latest_dir(home)
    handler = functools.partial(_QuietHandler, directory=str(directory))
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("127.0.0.1", port), handler) as httpd:
        url = f"http://localhost:{port}/"
        typer.echo(f"Serving {directory} at {url}", err=False)
        sys.stdout.flush()
        if open_page:
            webbrowser.open(url)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass


def _read_regular_file(path: Path, max_bytes: int) -> bytes:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as stream:
        metadata = os.fstat(stream.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > max_bytes:
            raise OSError("file is not a bounded regular file")
        data = stream.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise OSError("file exceeds the size limit")
    return data


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    """Serve only the review page and digest-verified images from its artifact store."""

    def do_HEAD(self):  # noqa: N802
        self.send_error(405)

    def do_GET(self):  # noqa: N802
        self._served_html = ""
        path = urlsplit(self.path).path
        if path in ("/", "/index.html"):
            page = Path(self.directory) / "index.html"
            if page.is_symlink() or not page.is_file():
                self.send_error(404)
                return
            try:
                body = _read_regular_file(page, 32 * 1024 * 1024)
                self._served_html = body.decode("utf-8")
            except (OSError, UnicodeDecodeError):
                self.send_error(404)
                return
            self._send_bytes(body, "text/html; charset=utf-8", "no-store")
            return
        prefix = "/artifacts/"
        digest = path[len(prefix) :] if path.startswith(prefix) else ""
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            self.send_error(404)
            return
        page_root = Path(self.directory).resolve()
        artifact_dir = page_root / "artifacts"
        try:
            if artifact_dir.is_symlink():
                raise OSError("artifact directory must not be a symlink")
            root = artifact_dir.resolve(strict=True)
            root.relative_to(page_root)
            artifact = root / digest
            body = _read_regular_file(artifact, 32 * 1024 * 1024)
        except (OSError, ValueError):
            self.send_error(404)
            return
        mime = _image_mime(body)
        if mime is None or hashlib.sha256(body).hexdigest() != digest:
            self.send_error(404)
            return
        self._send_bytes(body, mime, "private, max-age=31536000, immutable")

    def _send_bytes(self, body: bytes, content_type: str, cache_control: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache_control)
        self.end_headers()
        self.wfile.write(body)

    def end_headers(self):  # noqa: N802
        html_text = getattr(self, "_served_html", "")
        self.send_header("Content-Security-Policy", content_security_policy(html_text))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        super().end_headers()

    def log_message(self, fmt, *args):  # noqa: D401
        return


@app.command()
def badge(
    out: Path = typer.Option(Path("."), "--out", help="Directory for docs-proven.svg and PROOF.md."),
    home: Path = typer.Option(ATTEST_DIR, "--home"),
) -> None:
    """Write docs-proven.svg and PROOF.md for the most recent prove run."""
    from .ledger import Ledger
    from .models import Claim, Verdict
    from .ingest import ingest_text
    from .pipeline import ProveResult

    directory = _latest_dir(home)
    import json

    claims = [Claim.model_validate(c) for c in json.loads((directory / "claims.json").read_text(encoding="utf-8"))]
    verdicts = [Verdict.model_validate(v) for v in json.loads((directory / "verdicts.json").read_text(encoding="utf-8"))]
    doc = ingest_text((directory / "doc.md").read_text(encoding="utf-8"), source=str(directory / "doc.md"))
    doc = doc.model_copy(update={"claims": claims})
    ledger = Ledger(home / "ledger.jsonl")
    turn = ledger.latest_turn(doc.sha256) or 0
    result = ProveResult(doc, claims, {}, verdicts, turn, directory)
    svg, md = export_badge(result, home, runner_name="ledger", dest=out)
    typer.echo(f"wrote {svg} and {md}")


@app.command()
def ask(
    question: str = typer.Argument(..., help="The how-to question to send to the model."),
    card: str = typer.Option(..., "--card", help="Short name for the saved answer: demo/answers/<card>.md"),
    model: str | None = typer.Option(None, "--model"),
    out_dir: Path = typer.Option(Path("demo/answers"), "--out-dir"),
) -> None:
    """Procedural Truth: get a numbered browser-steps answer and save it as a doc."""
    from .asker import ask_and_save

    path = ask_and_save(question, card, out_dir=out_dir, model=model)
    typer.echo(f"saved {path}")


if __name__ == "__main__":  # pragma: no cover
    app()

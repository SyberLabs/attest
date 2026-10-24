"""Attest CLI: prove | serve | badge | ask."""

from __future__ import annotations

import functools
import http.server
import os
import socketserver
import sys
import webbrowser
from pathlib import Path

import typer

from . import __version__
from .pipeline import ATTEST_DIR, cleanup_scratch, export_badge, prove, render_result
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
    loop: bool = typer.Option(False, "--loop/--no-loop", help="Ask the agent for one scoped repair proposal; approval and validation are separate."),
    apply: bool = typer.Option(False, "--apply", help="Deprecated and rejected; promotion requires `resume <id> --apply` after approval and validation."),
    simulation: bool = typer.Option(False, "--simulation", help="Run with fake observations; never counts as verification evidence."),
    open_page: bool = typer.Option(False, "--open", help="Open the rendered page in a browser."),
    keep_scratch: bool = typer.Option(False, "--keep-scratch", help="Leave the scratch directory in place."),
    home: Path = typer.Option(ATTEST_DIR, "--home", help="Where ledger and pages live."),
) -> None:
    """Execute every claim in a document and render it with badges."""
    if apply:
        typer.echo("--apply is disabled here; use `attest approve` then `attest resume <id> --apply`.", err=True)
        raise typer.Exit(2)
    if runner == "fake" and not simulation:
        typer.echo("fake observations require --simulation and cannot verify claims.", err=True)
        raise typer.Exit(2)
    if simulation and runner != "fake":
        typer.echo("--simulation is only valid with --runner fake.", err=True)
        raise typer.Exit(2)
    br = None
    try:
        br = _runner(runner, fake_observations)
        typer.echo(f"attest prove {source}  (runner: {br.name}{' / simulation only' if simulation else ''})")
        result = prove(source, runner=br, home=home, log=_log, simulation=simulation)
    except Exception as exc:
        if br is not None:
            br.close()
        typer.echo(f"proof failed closed: {exc}", err=True)
        raise typer.Exit(1)
    if loop and result.failing():
        from .loop import run_loop

        typer.echo(f"loop: {len(result.failing())} failing claim(s) go to the agent")
        try:
            result = run_loop(result, br, home=home, log=_log)
        except Exception as exc:
            br.close()
            typer.echo(f"repair proposal failed closed: {exc}", err=True)
            raise typer.Exit(1)
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
    if result.proposals:
        from .storage import SQLiteWorkflowStore

        store = SQLiteWorkflowStore(home / "workflow.sqlite3")
        for claim_id, workflow_id in result.proposals.items():
            state = result.proposal_states.get(claim_id, "unknown")
            detail = ""
            if state == "rejected":
                decision = store.get(workflow_id).proposal.get("authority") or {}
                detail = f"; refused: {decision.get('reason', 'authority policy denied the proposal')}"
            typer.echo(f"{claim_id}: proposal {workflow_id} state={state}{detail}; source unchanged")
        raise typer.Exit(3)
    if not simulation and any(
        result.by_id[claim.id].status != "proven"
        for claim in result.claims
        if claim.kind in {"shell", "browser", "assert"}
    ):
        raise typer.Exit(1)
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
    pointer = home / "latest"
    if not pointer.exists():
        typer.echo("nothing rendered yet; run `attest prove <doc>` first", err=True)
        raise typer.Exit(1)
    return Path(pointer.read_text(encoding="utf-8").strip())


@app.command()
def approve(
    workflow_id: str = typer.Argument(..., help="Workflow ID printed by `attest prove --loop`."),
    by: str = typer.Option(..., "--by", help="Human approver identity to record."),
    home: Path = typer.Option(ATTEST_DIR, "--home", help="Where workflow state is stored."),
) -> None:
    """Approve the exact proposal, plan, source, build, and policy digests."""
    from .approvals import ApprovalError, approve_workflow
    from .storage import SQLiteWorkflowStore, WorkflowStorageError

    try:
        record = approve_workflow(SQLiteWorkflowStore(home / "workflow.sqlite3"), workflow_id, by)
    except (ApprovalError, WorkflowStorageError, ValueError) as exc:
        typer.echo(f"approval failed closed: {exc}", err=True)
        raise typer.Exit(2)
    typer.echo(f"approved by {record.approval['actor']}; state: {record.state}; no source was changed")


@app.command()
def resume(
    workflow_id: str = typer.Argument(..., help="Approved workflow ID."),
    runner: str = typer.Option("kane", "--runner", help="Fresh validation runner; must be Kane."),
    fake_observations: Path | None = typer.Option(None, "--fake-observations"),
    apply: bool = typer.Option(False, "--apply", help="Promote only after successful fresh validation."),
    simulation: bool = typer.Option(False, "--simulation", help="Simulation cannot validate or promote."),
    home: Path = typer.Option(ATTEST_DIR, "--home"),
) -> None:
    """Resume an approved workflow with fresh whole-document evidence."""
    from .storage import SQLiteWorkflowStore, WorkflowStorageError
    from .workflow import WorkflowError, resume_workflow

    if simulation or runner != "kane":
        typer.echo("resume requires fresh Kane evidence; fake/simulation evidence cannot validate or promote.", err=True)
        raise typer.Exit(2)
    try:
        br = _runner(runner, fake_observations)
    except Exception as exc:
        typer.echo(f"resume failed closed: {exc}", err=True)
        raise typer.Exit(2)
    try:
        record = resume_workflow(
            SQLiteWorkflowStore(home / "workflow.sqlite3"),
            workflow_id,
            runner=br,
            apply=apply,
            simulation=simulation,
        )
    except (WorkflowError, WorkflowStorageError, ValueError, NotImplementedError) as exc:
        typer.echo(f"resume failed closed: {exc}", err=True)
        raise typer.Exit(2)
    finally:
        br.close()
    typer.echo(f"workflow {workflow_id}: {record.state}")
    if record.state in {"rejected", "unresolved"}:
        raise typer.Exit(1)


@app.command()
def serve(
    port: int = typer.Option(4848, "--port"),
    home: Path = typer.Option(ATTEST_DIR, "--home"),
    open_page: bool = typer.Option(False, "--open", help="Open the page in a browser."),
) -> None:
    """Serve the most recently rendered page on localhost."""
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


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
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

"""Attest CLI: prove | serve | badge | ask."""

from __future__ import annotations

import functools
import http.server
import os
import re
import socketserver
import sys
import webbrowser
from pathlib import Path

import typer

from . import __version__
from .pipeline import ATTEST_DIR, cleanup_scratch, export_badge, prove, render_result, claim_banner
from .runner import FakeBrowserRunner

app = typer.Typer(add_completion=False, no_args_is_help=True, help="Docs that can't lie: prove every step of a document.")

STATUS_MARK = {"proven": "ok", "stale": "~ ", "broken": "x ", "unverifiable": "- ", "skipped": ">>"}


# the single tab served by `serve --live`: the terminal (live.log) beside the live document page
LIVE_WRAPPER = r'''<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Attest live</title>
<style>
*{box-sizing:border-box}
html,body{margin:0;height:100%;background:#0d1117;color:#1f2328;font:13px/1.4 -apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,Helvetica,Arial,sans-serif;overflow:hidden}
.strip{height:30px;display:flex;align-items:center;gap:10px;padding:0 14px;background:#1f2328;color:#e6edf3;white-space:nowrap;overflow:hidden;border-bottom:1px solid #30363d}
.strip b{font-weight:750;letter-spacing:.2px}.dot{width:8px;height:8px;border-radius:50%;background:#8b949e;flex:none}
.on .dot{background:#3fb950;box-shadow:0 0 0 0 rgba(63,185,80,.6);animation:p 1.6s ease-out infinite}@keyframes p{to{box-shadow:0 0 0 7px rgba(63,185,80,0)}}
.state{font-size:11px;font-weight:700;letter-spacing:.6px;text-transform:uppercase;color:#8b949e}.on .state{color:#3fb950}
.now{color:#c9d1d9;overflow:hidden;text-overflow:ellipsis;min-width:0}
.panes{display:grid;grid-template-columns:38% minmax(0,1fr);height:calc(100vh - 30px)}
.term{display:flex;flex-direction:column;min-width:0;min-height:0;background:#0d1117;border-right:1px solid #30363d}
.term-h{flex:none;height:30px;display:flex;align-items:center;gap:7px;padding:0 12px;background:#161b22;border-bottom:1px solid #30363d;color:#8b949e;font-size:12px}
.term-h i{width:10px;height:10px;border-radius:50%;background:#30363d;display:block}
.term-h span{margin-left:6px;font-family:ui-monospace,"Cascadia Mono",Consolas,monospace}
#log{flex:1;min-height:0;margin:0;padding:10px 12px 18px;overflow:auto;color:#c9d1d9;font:12.5px/1.5 ui-monospace,"Cascadia Mono","Cascadia Code",Consolas,"Liberation Mono",Menlo,monospace;white-space:pre-wrap;word-break:break-word}
#log div{min-height:1.5em;padding-left:4ch;text-indent:-4ch}
.cmd{color:#f0f6fc;font-weight:700}.ok{color:#3fb950;font-weight:600}.warn{color:#d29922;font-weight:600}.bad{color:#f85149;font-weight:600}
.grey{color:#8b949e}.dim{color:#6e7681}.add{color:#3fb950;background:rgba(46,160,67,.12)}.del{color:#f85149;background:rgba(248,81,73,.12)}
.agent{color:#bc8cff;font-weight:600}.head{color:#7ee787;font-weight:700}
.doc{min-width:0;min-height:0;background:#fff}
iframe{display:block;border:0;width:100%;height:100%;background:#fff}
@media (max-width:900px){.panes{grid-template-columns:1fr;grid-template-rows:38% minmax(0,1fr)}.term{border-right:0;border-bottom:1px solid #30363d}}
</style>
<div class="strip" id="strip"><span class="dot"></span><b>Attest</b><span class="state" id="state">waiting</span><span class="now" id="now">waiting for a proof run</span></div>
<div class="panes">
<section class="term" id="terminal" aria-label="terminal"><div class="term-h"><i></i><i></i><i></i><span>attest prove --live</span></div><pre id="log"><div class="dim">waiting for `attest prove --live` ...</div></pre></section>
<section class="doc"><iframe id="page"></iframe></section>
</div>
<script>
let seen="";async function poll(){try{let j=await fetch("latest.json",{cache:"no-store"}).then(r=>r.json());document.getElementById("strip").className="strip"+(j.running?" on":"");document.getElementById("state").textContent=j.running?"live":"done";document.getElementById("now").textContent=j.now||"";if(j.page&&j.page!==seen){seen=j.page;document.querySelector("iframe").src=j.page+"?v="+Date.now();}}catch(e){}}poll();setInterval(poll,1500);
function kind(line){
  const t=line.trim(), d=line.replace(/^\s*\| ?/,"");
  const piped=d!==line;
  if(line.startsWith("$ "))return "cmd";
  if(/^-{3,}$/.test(t))return "dim";
  if(piped||/^[+-]/.test(line)){
    if(d.startsWith("+++")||d.startsWith("---"))return "dim";
    if(d.startsWith("@@"))return "grey";
    if(d.startsWith("+"))return "add";
    if(d.startsWith("-"))return "del";
    return "";
  }
  if(t.startsWith("PROVEN"))return "ok";
  if(t.startsWith("STALE"))return "warn";
  if(t.startsWith("BROKEN"))return "bad";
  if(t.startsWith("SKIPPED")||t.startsWith("UNVERIFIABLE")||t.startsWith("PARTIAL"))return "grey";
  if(t.startsWith("AGENT")||t.startsWith("THE LOOP"))return "agent";
  if(t.startsWith("ATTEST"))return "head";
  if(t.startsWith("..."))return "grey";
  return "";
}
let lastLog=null;
async function tail(){
  try{
    const r=await fetch("live.log",{cache:"no-store"});
    if(!r.ok)return;
    const text=await r.text();
    if(text===lastLog)return;
    lastLog=text;
    const box=document.getElementById("log");
    const follow=box.scrollHeight-box.scrollTop-box.clientHeight<60;
    const lines=text.replace(/\r/g,"").split("\n");
    if(lines.length&&lines[lines.length-1]==="")lines.pop();
    const frag=document.createDocumentFragment();
    for(const line of lines.slice(-300)){const el=document.createElement("div");const k=kind(line);if(k)el.className=k;el.textContent=line;frag.appendChild(el);}
    box.replaceChildren(frag);
    if(follow||box.dataset.started!=="1"){box.scrollTop=box.scrollHeight;box.dataset.started="1";}
  }catch(e){}
}
tail();setInterval(tail,1000);
</script>'''


_ANSI = re.compile(re.escape(chr(27)) + r"\[[0-9;?]*[A-Za-z]")


class _Tee:
    """Mirror stdout into <home>/live.log (ANSI stripped) for the live page's terminal pane.

    A log write never fails the prove. ``buffer`` and ``fileno`` are hidden so
    click cannot bypass the tee by writing to the raw Windows console.
    """

    def __init__(self, stream, path: Path, header: str):
        self._stream = stream
        self._log = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._log = open(path, "w", encoding="utf-8", errors="replace", newline="\n")
            self._log.write(header + "\n")
            self._log.flush()
        except OSError:
            self._log = None

    def write(self, text):
        n = self._stream.write(text)
        if self._log is not None and isinstance(text, str):
            try:
                self._log.write(_ANSI.sub("", text))
                self._log.flush()
            except (OSError, ValueError):
                pass
        return n

    def flush(self):
        self._stream.flush()

    def close(self):
        if self._log is not None:
            try:
                self._log.close()
            except OSError:
                pass
            self._log = None

    def __getattr__(self, name):
        if name in ("buffer", "fileno"):
            raise AttributeError(name)
        return getattr(self._stream, name)


def _runner(name: str, fake_observations: Path | None, headless: bool = False, report: bool = False, cdp_endpoint: str | None = None):
    if name == "fake":
        if fake_observations:
            return FakeBrowserRunner.from_file(fake_observations)
        return FakeBrowserRunner()
    if name == "kane":
        from .kane import KaneBrowserRunner
        from .verdict import load_rules

        return KaneBrowserRunner(headless=headless, max_steps=load_rules().kane_max_steps, report=report, cdp_endpoint=cdp_endpoint, log=typer.echo)
    raise typer.BadParameter(f"unknown runner {name!r}; use fake or kane")


RULE = "-" * 64
STATUS_COLOR = {"PROVEN": typer.colors.GREEN, "STALE": typer.colors.YELLOW, "BROKEN": typer.colors.RED}
# code points that PowerShell 5.1 would garble, mapped to plain ASCII
_ASCII = {chr(cp): rep for cp, rep in (
    (0x201C, '"'), (0x201D, '"'), (0x2018, "'"), (0x2019, "'"), (0x2013, "-"), (0x2014, "-"),
    (0x2026, "..."), (0x2192, "->"), (0x279C, ">"), (0x00A0, " "), (0x2502, "|"), (0x2514, "+"),
    (0x00B7, "-"), (0x2022, "*"), (0x2713, "ok"), (0x2717, "x"),
)}


def _ascii(text: str) -> str:
    """Terminal text in plain ASCII: PowerShell 5.1 shows anything else as mojibake."""
    text = "".join(_ASCII.get(ch, ch) for ch in str(text))
    return text.encode("ascii", "replace").decode("ascii")


def _clip(text: str, width: int = 96) -> str:
    return text if len(text) <= width else text[: width - 3].rstrip() + "..."


def _log(claim, verdict) -> None:
    if claim.kind == "prose":
        return  # prose promises nothing to run; the page still shows it
    status = verdict.status.upper()
    color = STATUS_COLOR.get(status, typer.colors.BRIGHT_BLACK)
    evidence = verdict.evidence
    if verdict.status in ("skipped", "unverifiable"):
        detail = verdict.reason.split(": ", 1)[-1]
    elif claim.kind == "shell":
        detail = f"exit code {'timed out' if evidence and evidence.timed_out else evidence.exit_code if evidence else 'unknown'}"
    elif evidence and evidence.observation:
        observed = ", ".join(str(v) for v in evidence.observation.observed.values())
        detail = f'Kane saw "{observed}"' if observed else verdict.reason
        if verdict.status == "stale":
            detail = f'doc says "{claim.expects}", page says "{observed}"'
    else:
        detail = verdict.reason
    word = typer.style(f"{status:<8}", fg=color, bold=True)
    where = typer.style(f"line {claim.span.start_line}", dim=True)
    typer.echo(f"   {word}  {where}  {_ascii(detail)}")


def _step(claim) -> None:
    """The quiet line printed before a claim runs."""
    if claim.kind == "prose":
        return
    typer.echo(typer.style(_clip(f"   ...       {_ascii(claim_banner(claim))}"), fg=typer.colors.BRIGHT_BLACK))


def _section(title: str) -> None:
    typer.echo("")
    typer.echo(typer.style(f"   {title}", bold=True))
    typer.echo(typer.style(f"   {RULE}", dim=True))


@app.callback(invoke_without_command=True)
def _main(version: bool = typer.Option(False, "--version", help="Show the version and exit.")) -> None:
    if version:
        typer.echo(f"attest {__version__}")
        raise typer.Exit()


@app.command("prove")
def prove_cmd(
    source: str = typer.Argument(..., help="Path or URL of the document to prove."),
    runner: str = typer.Option("kane", "--runner", help="Browser runner: kane (default) or fake (offline)."),
    headless: bool = typer.Option(False, "--headless", help="Kane runs Chrome without a window (automated runs only)."),
    report: bool = typer.Option(False, "--report", help="File each Kane run with the event's `kanehack report`."),
    cdp_endpoint: str | None = typer.Option(None, "--cdp-endpoint", help="Attach Kane to an existing Chrome debugging endpoint."),
    fake_observations: Path | None = typer.Option(None, "--fake-observations", help="JSON of scripted observations for the fake runner."),
    stop_before: str | None = typer.Option(None, "--stop-before", help="Stop before this claim id (for irreversible or real-form steps)."),
    skip: list[str] = typer.Option([], "--skip", help="Skip a claim id (repeat or use comma-separated ids) when it does not apply."),
    loop: bool = typer.Option(False, "--loop/--no-loop", help="Send stale or broken steps to the coding agent and re-prove them."),
    apply: bool = typer.Option(False, "--apply", help="With --loop: write the agent's doc patch back to the source file."),
    policy: Path | None = typer.Option(None, "--policy", help="Authority policy YAML for the loop."),
    recipes_only: bool = typer.Option(False, "--recipes-only", help="Run shell claims only when they match an approved recipe."),
    open_page: bool = typer.Option(False, "--open", help="Open the rendered page in a browser."),
    keep_scratch: bool = typer.Option(False, "--keep-scratch", help="Leave the scratch directory in place."),
    home: Path = typer.Option(ATTEST_DIR, "--home", help="Where ledger and pages live."),
    live: bool = typer.Option(False, "--live", help="Update a live page while claims run."),
) -> None:
    """Execute every claim in a document and render it with badges."""
    tee = None
    if live:
        # the live page shows this terminal beside the document
        tee = _Tee(sys.stdout, home / "live.log", f"$ attest prove {_ascii(source)}")
        sys.stdout = tee
    try:
        br = _runner(runner, fake_observations, headless=headless, report=report, cdp_endpoint=cdp_endpoint)
        _header(source, br.name)
        try:
            skip_ids = {claim_id.strip() for item in skip for claim_id in item.split(",") if claim_id.strip()}
            def narrate(c, v):
                _log(c, v)
            def before(c):
                _step(c)
            result = prove(
                source, runner=br, home=home, log=narrate, keep_servers=loop,
                stop_before=stop_before, skip=skip_ids, live=live, on_start=before,
                recipes_only=recipes_only,
            )
        except ValueError as e:
            raise typer.BadParameter(str(e)) from e
        if loop and result.failing():
            from .loop import run_loop

            n_failing = len(result.failing())
            _section(f"THE LOOP   {n_failing} claim{'' if n_failing == 1 else 's'} to fix: the coding agent patches the doc, Kane re-checks")
            def loop_update(message):
                if live:
                    from .pipeline import _write_live_page
                    _write_live_page(result, home, br.name, message, True)
            def loop_log(c, v):
                _log(c, v)
                if live:
                    from .pipeline import _write_live_page
                    _write_live_page(result, home, br.name, "Proof updated", True)
            result = run_loop(
                result, br, home=home, log=loop_log, apply_to_source=apply,
                on_update=loop_update, policy_path=policy, recipes_only=recipes_only,
            )
        br.close()
        session = getattr(result, "_session", None)
        if session is not None:
            session.close()
        if not keep_scratch:
            cleanup_scratch(result.scratch)
        render_result(result, home, br.name)
        if live:
            # the loop left the live state at "Proof updated"; mark the run done
            from .pipeline import _write_live_page
            _write_live_page(result, home, br.name, "Proof complete", False)
        for line in _summary_lines(result, home):
            typer.echo(line)
        typer.echo(f"   {typer.style('page', dim=True)}      {result.page}")
        typer.echo("")
        if open_page and result.page:
            webbrowser.open(result.page.resolve().as_uri())
        if any(v.status in ("stale", "broken") for v in result.verdicts):
            # a doc that lies fails the command, so CI can gate on it
            raise typer.Exit(1)
    finally:
        if tee is not None:
            sys.stdout = tee._stream
            tee.close()


def _header(source: str, runner_name: str) -> None:
    name = Path(source).name if "://" not in source else source
    typer.echo("")
    typer.echo(f"   {typer.style('ATTEST', bold=True, fg=typer.colors.GREEN)}  proving {typer.style(_ascii(name), bold=True)}  (runner: {runner_name})")
    typer.echo(typer.style(f"   {RULE}", dim=True))


def _summary_lines(result, home: Path) -> list[str]:
    from .ledger import Ledger

    s = Ledger(home / "ledger.jsonl").summary(result.doc.sha256, result.turn)
    if s["claims"] and s["proven"] == s["claims"]:
        word, color = "PROVEN", typer.colors.GREEN
    elif s["broken"]:
        word, color = "BROKEN", typer.colors.RED
    elif s["stale"]:
        word, color = "STALE", typer.colors.YELLOW
    else:
        word, color = "PARTIAL", typer.colors.BRIGHT_BLACK

    def count(n: int, label: str, fg) -> str:
        text = f"{n} {label}"
        return typer.style(text, fg=fg, bold=True) if n else text

    counts = " | ".join([
        typer.style(f"{s['proven']}/{s['claims']} proven", fg=typer.colors.GREEN if word == "PROVEN" else None, bold=True),
        count(s["stale"], "stale", typer.colors.YELLOW),
        count(s["broken"], "broken", typer.colors.RED),
        f"{s['unverifiable']} unverifiable",
        f"{s['skipped']} skipped",
    ])
    lines = ["", typer.style(f"   {RULE}", dim=True), f"   {typer.style(f'{word:<8}', fg=color, bold=True)}  {counts}"]
    if s.get("loops_attempted"):
        lines.append(f"   {typer.style('loop', dim=True)}      {s['loops_closed']}/{s['loops_attempted']} fixed by the agent and re-proven")
    extra = f"{s['claims']} claims | turn {s['turn']}"
    if s.get("kane_steps"):
        extra += f" | {s['kane_steps']} Kane steps ({s['authored']} authored, {s['replayed']} replayed)"
    lines.append(f"   {typer.style('ledger', dim=True)}    {extra}")
    return lines


def _latest_dir(home: Path) -> Path:
    pointer = home / "latest"
    if not pointer.exists():
        typer.echo("nothing rendered yet; run `attest prove <doc>` first", err=True)
        raise typer.Exit(1)
    return Path(pointer.read_text(encoding="utf-8").strip())


@app.command()
def serve(
    port: int = typer.Option(4848, "--port"),
    home: Path = typer.Option(ATTEST_DIR, "--home"),
    doc: Path | None = typer.Option(None, "--doc", help="Serve the rendered page for this document."),
    open_page: bool = typer.Option(False, "--open", help="Open the page in a browser."),
    live: bool = typer.Option(False, "--live", help="Serve the full home directory with a live page."),
) -> None:
    """Serve a rendered page on localhost."""
    if live:
        directory = home
        home.mkdir(parents=True, exist_ok=True)
        (home / "index.html").write_text(LIVE_WRAPPER, encoding="utf-8")
    elif doc is None:
        directory = _latest_dir(home)
    else:
        from .ingest import ingest
        from .pipeline import out_dir_for

        try:
            document = ingest(str(doc))
        except (OSError, ValueError) as e:
            typer.echo(f"cannot load document {doc}: {e}", err=True)
            raise typer.Exit(1) from e
        directory = out_dir_for(document, home)
        if not (directory / "index.html").is_file():
            typer.echo(f"document has not been proven yet: {doc}", err=True)
            raise typer.Exit(1)
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

    def end_headers(self):
        # pages are rewritten in place during a live run; the browser must never reuse an old copy
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


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

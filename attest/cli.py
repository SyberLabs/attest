"""Attest CLI: prove | serve | badge | ask."""

from __future__ import annotations

import email.utils
import functools
import hashlib
import http.server
import os
import re
import socketserver
import sys
import webbrowser
from pathlib import Path
from urllib.parse import unquote, urlsplit

import typer

from . import __version__
from .pipeline import ATTEST_DIR, cleanup_scratch, export_badge, prove, render_result, claim_banner
from .render import _image_mime, _read_regular_file, content_security_policy
from .runner import FakeBrowserRunner

app = typer.Typer(add_completion=False, no_args_is_help=True, help="Docs that can't lie: prove every step of a document.")

STATUS_MARK = {"proven": "ok", "stale": "~ ", "broken": "x ", "unverifiable": "- ", "skipped": ">>"}


# the single tab served by `serve --live`: the terminal (live.log) beside the live document page
LIVE_WRAPPER = r'''<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Attest live</title>
<style>
:root{--bg:#f6f8fa;--card:#fff;--fg:#1f2328;--muted:#59636e;--faint:#818b98;--line:#d1d9e0;--line-soft:#e9eef3;
--proven:#1a7f37;--proven-bg:#dafbe1;--proven-line:#4ac26b;--live:#0969da;--live-bg:#ddf4ff;--agent:#8250df;--agent-bg:#fbefff;
--term:#0d1117;--term-h:#161b22;--term-line:#30363d;
--sans:-apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,"Noto Sans",Helvetica,Arial,sans-serif;
--mono:ui-monospace,"Cascadia Mono","Cascadia Code",SFMono-Regular,Consolas,"Liberation Mono",Menlo,monospace}
*{box-sizing:border-box}
html,body{margin:0;height:100%;overflow:hidden}
body{display:flex;flex-direction:column;background:var(--bg);color:var(--fg);font:13px/1.4 var(--sans);-webkit-font-smoothing:antialiased}
/* top bar: scene, what is happening now, the factory stages, the next beat */
.bar{flex:none;display:flex;align-items:center;gap:18px;min-height:54px;padding:7px 16px;background:var(--card);border-bottom:1px solid var(--line);box-shadow:0 1px 0 rgba(31,35,40,.04),0 6px 16px -12px rgba(31,35,40,.25);position:relative;z-index:2}
.logo{flex:none;width:30px;height:30px;border-radius:8px;background:var(--fg);display:grid;place-items:center}
.logo svg{width:18px;height:18px}
.scene{flex:1 1 auto;min-width:0}
.scene-t{font-size:15.5px;font-weight:650;line-height:1.25;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.scene-t .brand{color:var(--faint);font-weight:600}
.sub{display:flex;align-items:center;gap:7px;margin-top:2px;min-width:0;color:var(--muted);font-size:12.5px}
.dot{flex:none;width:7px;height:7px;border-radius:50%;background:var(--faint)}
.state{flex:none;font-size:10.5px;font-weight:800;letter-spacing:.6px;text-transform:uppercase;color:var(--faint)}
.on .dot{background:var(--proven-line);animation:p 1.6s ease-out infinite}.on .state{color:var(--proven)}
@keyframes p{0%{box-shadow:0 0 0 0 rgba(74,194,107,.55)}100%{box-shadow:0 0 0 6px rgba(74,194,107,0)}}
.now{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.stages{flex:none;display:flex;align-items:center;margin:0;padding:0;list-style:none}
.stages li{display:flex;align-items:center;gap:6px;color:var(--faint);font-size:12px;font-weight:600;white-space:nowrap;transition:color .3s,opacity .3s}
.stages li+li::before{content:"";width:16px;height:2px;border-radius:1px;background:var(--line);margin:0 6px 0 2px;transition:background .3s}
.stages .n{flex:none;width:20px;height:20px;border-radius:50%;border:1.5px solid var(--line);background:var(--card);display:grid;place-items:center;font-size:10.5px;font-weight:700;font-variant-numeric:tabular-nums;transition:background .3s,border-color .3s,color .3s}
.stages .n svg{display:none;width:12px;height:12px}
.stages li.done{color:var(--fg)}
.stages li.done .n{background:var(--proven);border-color:var(--proven);color:#fff}
.stages li.done .n b{display:none}.stages li.done .n svg{display:block}
.stages li.done::before,.stages li.active::before{background:var(--proven-line)}
.stages li.active{color:var(--live);font-weight:700}
.stages li.active .n{background:var(--live);border-color:var(--live);color:#fff;animation:ring 1.6s ease-out infinite}
.stages li.active.agent{color:var(--agent)}
.stages li.active.agent .n{background:var(--agent);border-color:var(--agent);animation-name:ring-a}
.stages li.final{color:var(--proven);font-weight:700}
.stages li.skip{opacity:.45}.stages li.skip .n{border-style:dashed}
@keyframes ring{0%{box-shadow:0 0 0 0 rgba(9,105,218,.45)}100%{box-shadow:0 0 0 7px rgba(9,105,218,0)}}
@keyframes ring-a{0%{box-shadow:0 0 0 0 rgba(130,80,223,.45)}100%{box-shadow:0 0 0 7px rgba(130,80,223,0)}}
.next{flex:none;display:inline-flex;align-items:center;gap:6px;max-width:300px;height:26px;padding:0 10px;border-radius:13px;border:1px solid var(--line);background:var(--bg);color:var(--muted);font-size:12px;white-space:nowrap}
.next[hidden]{display:none}
.next b{min-width:0;overflow:hidden;text-overflow:ellipsis;color:var(--fg);font-weight:600}
.next .s{flex:none;font-variant-numeric:tabular-nums;color:var(--fg);font-weight:700}
/* panes: the terminal on the left, the live document on the right */
.panes{flex:1 1 auto;min-height:0;display:grid;grid-template-columns:36% minmax(0,1fr)}
.term{display:flex;flex-direction:column;min-width:0;min-height:0;background:var(--term);border-right:1px solid var(--line)}
.term-h{flex:none;height:30px;display:flex;align-items:center;gap:7px;padding:0 12px;background:var(--term-h);border-bottom:1px solid var(--term-line);color:#8b949e;font-size:12px}
.term-h i{width:10px;height:10px;border-radius:50%;background:var(--term-line);display:block}
.term-h span{margin-left:6px;font-family:var(--mono)}
#log{flex:1;min-height:0;margin:0;padding:10px 12px 18px;overflow:auto;color:#c9d1d9;font:12.5px/1.5 var(--mono);white-space:pre-wrap;word-break:break-word}
#log div{min-height:1.5em;padding-left:4ch;text-indent:-4ch}
.cmd{color:#f0f6fc;font-weight:700}.ok{color:#3fb950;font-weight:600}.warn{color:#d29922;font-weight:600}.bad{color:#f85149;font-weight:600}
.grey{color:#8b949e}.dim{color:#6e7681}.add{color:#3fb950;background:rgba(46,160,67,.12)}.del{color:#f85149;background:rgba(248,81,73,.12)}
.agent-l{color:#bc8cff;font-weight:600}.head{color:#7ee787;font-weight:700}
.doc{min-width:0;min-height:0;background:var(--bg)}
iframe{display:block;border:0;width:100%;height:100%;background:var(--bg)}
@media (max-width:1180px){.stages li span.l{display:none}.stages li.active span.l,.stages li.final span.l{display:inline}.stages li+li::before{width:12px}}
@media (max-width:900px){
.bar{flex-wrap:wrap;gap:8px 12px}.scene{flex:1 1 200px}.stages{order:3;flex:1 1 100%}
.stages li span.l{display:inline}.stages li{font-size:11px}.stages li+li::before{width:8px;margin:0 4px 0 2px}
.panes{grid-template-columns:1fr;grid-template-rows:36% minmax(0,1fr)}.term{border-right:0;border-bottom:1px solid var(--line)}}
@media (max-width:720px){.stages li span.l{display:none}.stages li.active span.l,.stages li.final span.l{display:inline}.next{max-width:200px}}
@media (prefers-reduced-motion:reduce){.on .dot,.stages li.active .n{animation:none}.stages li,.stages .n,.stages li+li::before{transition:none}}
</style>
<header class="bar" id="strip">
<div class="logo" aria-hidden="true"><svg viewBox="0 0 16 16"><path d="M3 8.6l3.2 3.2L13 4.6" fill="none" stroke="#4ac26b" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></svg></div>
<div class="scene"><div class="scene-t" id="scene"><span class="brand">Attest</span> live</div><div class="sub" role="status" aria-live="polite"><span class="dot"></span><span class="state" id="state">waiting</span><span class="now" id="now">waiting for a proof run</span></div></div>
<ol class="stages" id="stages" aria-label="Factory stages"></ol>
<span class="next" id="next" hidden>Next: <b id="next-t"></b> in <span class="s" id="next-s"></span></span>
</header>
<div class="panes">
<section class="term" id="terminal" aria-label="terminal"><div class="term-h"><i></i><i></i><i></i><span>attest prove --live</span></div><pre id="log"><div class="dim">waiting for `attest prove --live` ...</div></pre></section>
<section class="doc"><iframe id="page"></iframe></section>
</div>
<script>
// the factory stages, lit from the latest.json "now" text (presentation only).
// The doc frame reloads when the page path changes or a run starts or ends: a spec renders to
// the same path every run, and only the live render refreshes itself.
const STAGES=["Spec","Kane","Ticket","Worker","Kane re-check","Outcome"];
const CHECK='<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3.5 8.5l3 3 6-7" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></svg>';
const ol=document.getElementById("stages");document.getElementById("page").title="live document";
STAGES.forEach((name,i)=>{const li=document.createElement("li");li.innerHTML='<span class="n"><b>'+(i+1)+'</b>'+CHECK+'</span><span class="l"></span>';li.querySelector(".l").textContent=name;li.title=name;ol.appendChild(li);});
let cur=-1,done=new Set();
function stageOf(now,running){
  if(!running||/Proof complete/.test(now))return 5;
  if(/re-checking/.test(now))return 4;
  if(/implementing|agent to fix/.test(now))return 3;
  if(/Issuing ticket|Asking the agent/.test(now))return 2;
  if(/Kane is checking/.test(now))return 1;
  if(/Preparing|Running|Reading line/.test(now))return 0;
  return -1;
}
function advance(now,running){
  now=now||"";
  let s=stageOf(now,running);
  if(s<0)return;
  if(/Preparing claims/.test(now)||(s<=1&&cur===5)){done.clear();cur=-1;}
  if(s<=1&&cur>s&&cur<5)s=cur;
  for(const x of Array.from(done))if(x>=s)done.delete(x);
  if(s<5){if(s>=1)done.add(0);if(s>=2)done.add(1);if(s>=3)done.add(2);if(s>=4)done.add(3);}
  if(s===5){done.add(0);done.add(1);if(cur>=0&&cur<5)done.add(cur);}
  cur=s;
  ol.querySelectorAll("li").forEach((li,i)=>{
    let c="";
    if(i===cur)c=cur===5?"done final":"active"+(i===2||i===3?" agent":"");
    else if(done.has(i))c="done";
    else if(cur===5)c="skip";
    li.className=c;
    if(i===cur)li.setAttribute("aria-current","step");else li.removeAttribute("aria-current");
  });
}
let seen="";async function poll(){try{let j=await fetch("latest.json",{cache:"no-store"}).then(r=>r.json());document.getElementById("strip").className="bar"+(j.running?" on":"");document.getElementById("state").textContent=j.running?"live":"done";document.getElementById("now").textContent=j.now||"";advance(j.now,j.running);let key=j.page+"|"+!!j.running;if(j.page&&key!==seen){seen=key;document.querySelector("iframe").src=j.page+"?v="+Date.now();}}catch(e){}}poll();setInterval(poll,1500);
// scene.json (written by the demo scripts): the beat title and the countdown to the next beat
let scene={};
function drawScene(){
  const t=document.getElementById("scene"),title=String(scene.title||"");
  if(t.dataset.title!==title){t.dataset.title=title;if(title){t.textContent=title;}else{t.innerHTML='<span class="brand">Attest</span> live';}document.title=title?title+" - Attest":"Attest live";}
  const left=Math.ceil((Number(scene.until)||0)-Date.now()/1000),chip=document.getElementById("next");
  if(scene.next&&left>0){document.getElementById("next-t").textContent=String(scene.next);document.getElementById("next-s").textContent=left+" s";chip.hidden=false;}else{chip.hidden=true;}
}
async function pollScene(){try{const r=await fetch("scene.json",{cache:"no-store"});if(r.ok){const j=await r.json();if(j&&typeof j==="object")scene=j;}}catch(e){}drawScene();}
pollScene();setInterval(pollScene,1000);setInterval(drawScene,250);
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
  if(t.startsWith("AGENT")||t.startsWith("WORKER")||t.startsWith("THE LOOP"))return "agent-l";
  if(t.startsWith("TICKET"))return "warn";
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
    simulation: bool = typer.Option(False, "--simulation", help="Fake observations are simulation only and never verification evidence."),
    stop_before: str | None = typer.Option(None, "--stop-before", help="Stop before this claim id (for irreversible or real-form steps)."),
    skip: list[str] = typer.Option([], "--skip", help="Skip a claim id (repeat or use comma-separated ids) when it does not apply."),
    loop: bool = typer.Option(False, "--loop/--no-loop", help="Ask the agent for one scoped repair proposal; approval and validation are separate."),
    apply: bool = typer.Option(False, "--apply", help="Deprecated and rejected; promotion requires approved fresh validation via `resume <id> --apply`."),
    policy: Path | None = typer.Option(None, "--policy", help="Authority policy YAML for the loop."),
    recipes_only: bool = typer.Option(False, "--recipes-only", help="Run shell claims only when they match an approved recipe."),
    open_page: bool = typer.Option(False, "--open", help="Open the rendered page in a browser."),
    keep_scratch: bool = typer.Option(False, "--keep-scratch", help="Leave the scratch directory in place."),
    home: Path = typer.Option(ATTEST_DIR, "--home", help="Where ledger and pages live."),
    live: bool = typer.Option(False, "--live", help="Update a live page while claims run."),
    app_dir: Path | None = typer.Option(None, "--app", help="Spec mode: the doc is the contract for this app directory; failing claims become tickets the implementation agent closes in a copy of the app (implies --loop)."),
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
    tee = None
    if app_dir is not None:
        loop = True
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
                stop_before=stop_before, skip=skip_ids, live=live, on_start=before, simulation=simulation,
                recipes_only=recipes_only, app_dir=app_dir,
            )
        except ValueError as e:
            br.close()
            raise typer.BadParameter(str(e)) from e
        except Exception as e:
            br.close()
            typer.echo(f"proof failed closed: {e}", err=True)
            raise typer.Exit(1) from e
        if loop and result.failing():
            from .loop import run_loop

            n_failing = len(result.failing())
            if app_dir is not None:
                _section(f"THE LOOP   {n_failing} capabilit{'y' if n_failing == 1 else 'ies'} missing: tickets go to the worker, Kane re-checks")
                typer.echo(typer.style(f"   app copy  {getattr(result, 'candidate').posix}", dim=True))
            else:
                _section(f"THE LOOP   {n_failing} claim{'' if n_failing == 1 else 's'} to repair: the coding agent proposes; approval and Kane validation follow")
            def loop_update(message):
                if live:
                    from .pipeline import _write_live_page
                    _write_live_page(result, home, br.name, message, True)
            def loop_log(c, v):
                _log(c, v)
                if live:
                    from .pipeline import _write_live_page
                    _write_live_page(result, home, br.name, "Proof updated", True)
            try:
                result = run_loop(
                    result, br, home=home, log=loop_log, on_update=loop_update, policy_path=policy,
                    recipes_only=recipes_only,
                )
            except Exception as e:
                br.close()
                typer.echo(f"repair proposal failed closed: {e}", err=True)
                raise typer.Exit(1) from e
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
        for line in _summary_lines(result, home, spec=app_dir is not None):
            typer.echo(line)
        typer.echo(f"   {typer.style('page', dim=True)}      {result.page}")
        typer.echo("")
        if result.proposals:
            from .storage import SQLiteWorkflowStore

            store = SQLiteWorkflowStore(home / "workflow.sqlite3")
            for claim_id, workflow_id in result.proposals.items():
                record = store.get(workflow_id)
                authority = record.proposal.get("authority") or {}
                handoff = authority.get("implementation_handoff") or {}
                if record.state == "rejected" and handoff:
                    detail = f"; implementation handoff required for {handoff.get('target_claim_id', claim_id)}: {handoff.get('next_step')}"
                elif record.state == "rejected":
                    detail = f"; refused: {authority.get('reason', 'authority policy denied the proposal')}"
                else:
                    detail = ""
                typer.echo(f"{claim_id}: proposal {workflow_id} state={record.state}{detail}; source unchanged")
            raise typer.Exit(3)
        if open_page and result.page:
            webbrowser.open(result.page.resolve().as_uri())
        if not simulation and any(
            result.by_id[claim.id].status != "proven"
            for claim in result.claims
            if claim.kind in {"shell", "browser", "assert"}
        ):
            raise typer.Exit(1)
    finally:
        if tee is not None:
            sys.stdout = tee._stream
            tee.close()


@app.command("reconcile-spec")
def reconcile_spec_cmd(
    diagnosis: Path = typer.Argument(..., help="JSON diagnosis input; see docs/RECONCILE-SPEC.md."),
    out: Path | None = typer.Option(None, "--out", help="Directory for the Markdown and JSON handoff artifacts."),
    apply: bool = typer.Option(False, "--apply", help="Always refused in this mode; writes a refusal handoff without changing protected text."),
    home: Path = typer.Option(ATTEST_DIR, "--home", help="Default artifact root when --out is not supplied."),
) -> None:
    """Record a protected-requirement conflict and produce a human handoff; never repair contracts."""
    from .reconcile_spec import ReconcileRequest, ReconcileSpec, write_handoff

    try:
        input_bytes = diagnosis.read_bytes()
        request = ReconcileRequest.model_validate_json(input_bytes)
    except (OSError, ValueError) as exc:
        typer.echo(f"invalid Reconcile Spec JSON: {exc}", err=True)
        raise typer.Exit(2) from exc

    input_sha = hashlib.sha256(input_bytes).hexdigest()[:16]
    output_dir = out or home / "reconcile-spec" / input_sha
    handoff = ReconcileSpec().diagnose(request, apply_requested=apply)
    markdown_path, json_path = write_handoff(handoff, output_dir)
    typer.echo(f"handoff: {markdown_path}")
    typer.echo(f"json: {json_path}")
    if apply:
        typer.echo("refused: Reconcile Spec cannot apply or promote protected-contract text", err=True)
        raise typer.Exit(2)


def _header(source: str, runner_name: str) -> None:
    name = Path(source).name if "://" not in source else source
    typer.echo("")
    typer.echo(f"   {typer.style('ATTEST', bold=True, fg=typer.colors.GREEN)}  proving {typer.style(_ascii(name), bold=True)}  (runner: {runner_name})")
    typer.echo(typer.style(f"   {RULE}", dim=True))


def _summary_lines(result, home: Path, spec: bool = False) -> list[str]:
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
        done = "implemented in the app and re-proven" if spec else "fixed by the agent and re-proven"
        lines.append(f"   {typer.style('loop', dim=True)}      {s['loops_closed']}/{s['loops_attempted']} {done}")
    extra = f"{s['claims']} claims | turn {s['turn']}"
    if s.get("kane_steps"):
        extra += f" | {s['kane_steps']} Kane steps ({s['authored']} authored, {s['replayed']} replayed)"
    lines.append(f"   {typer.style('ledger', dim=True)}    {extra}")
    return lines


def _latest_dir(home: Path) -> Path:
    """The directory named by ``<home>/latest``, which must resolve inside ``home``."""
    root = home.resolve()
    pointer = root / "latest"
    if not pointer.exists() and not pointer.is_symlink():
        typer.echo("nothing rendered yet; run `attest prove <doc>` first", err=True)
        raise typer.Exit(1)
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
        raise typer.Exit(2) from exc
    typer.echo(f"approved by {record.approval['actor']}; state: {record.state}; no source was changed")


@app.command()
def resume(
    workflow_id: str = typer.Argument(..., help="Approved workflow ID."),
    runner: str = typer.Option("kane", "--runner", help="Fresh validation runner; must be Kane."),
    headless: bool = typer.Option(False, "--headless"),
    report: bool = typer.Option(False, "--report"),
    cdp_endpoint: str | None = typer.Option(None, "--cdp-endpoint"),
    apply: bool = typer.Option(False, "--apply", help="Promote only after successful fresh validation."),
    simulation: bool = typer.Option(False, "--simulation", help="Simulation cannot validate or promote."),
    home: Path = typer.Option(ATTEST_DIR, "--home"),
) -> None:
    """Resume an approved workflow with fresh whole-document evidence.

    Shell claims always run recipes-only here; no shell recipes are configured, so they fail closed.
    """
    from .storage import SQLiteWorkflowStore, WorkflowStorageError
    from .workflow import WorkflowError, resume_workflow

    if simulation or runner != "kane":
        typer.echo("resume requires fresh Kane evidence; fake/simulation evidence cannot validate or promote.", err=True)
        raise typer.Exit(2)
    try:
        br = _runner(runner, None, headless=headless, report=report, cdp_endpoint=cdp_endpoint)
    except Exception as exc:
        typer.echo(f"resume failed closed: {exc}", err=True)
        raise typer.Exit(2) from exc
    try:
        record = resume_workflow(
            SQLiteWorkflowStore(home / "workflow.sqlite3"), workflow_id, runner=br, apply=apply,
            simulation=simulation,
        )
    except (WorkflowError, WorkflowStorageError, ValueError, NotImplementedError) as exc:
        typer.echo(f"resume failed closed: {exc}", err=True)
        raise typer.Exit(2) from exc
    finally:
        br.close()
    typer.echo(f"workflow {workflow_id}: {record.state}")
    if record.state in {"rejected", "unresolved"}:
        raise typer.Exit(1)


@app.command()
def serve(
    port: int = typer.Option(4848, "--port"),
    home: Path = typer.Option(ATTEST_DIR, "--home"),
    doc: Path | None = typer.Option(None, "--doc", help="Serve the rendered page for this document."),
    open_page: bool = typer.Option(False, "--open", help="Open the page in a browser."),
    live: bool = typer.Option(False, "--live", help="Serve the full home directory with a live page."),
) -> None:
    """Serve reviews on loopback only; unauthenticated and unsafe to expose through a public proxy."""
    if live:
        directory = home
        home.mkdir(parents=True, exist_ok=True)
        from .render import logo_uri

        wrapper = LIVE_WRAPPER
        logo = logo_uri(home / "artifacts")
        if logo:
            # the CSP allows images from 'self' only and no style="" attributes
            wrapper = wrapper.replace("<title>Attest live</title>", f'<title>Attest live</title><link rel="icon" type="image/png" href="{logo}">', 1)
            wrapper = wrapper.replace("</style>", ".logo.brand{background:none;padding:0;overflow:hidden}.logo.brand img{width:100%;height:100%;object-fit:cover;display:block}\n</style>", 1)
            start = wrapper.find('<div class="logo" aria-hidden="true">')
            if start != -1:
                end = wrapper.find("</div>", start) + len("</div>")
                wrapper = wrapper[:start] + f'<div class="logo brand" aria-hidden="true"><img src="{logo}" alt=""></div>' + wrapper[end:]
        (home / "index.html").write_text(wrapper, encoding="utf-8")
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
    handler = functools.partial(_QuietHandler, directory=str(directory), live_mode=live)
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
    """Serve only review HTML, digest-verified evidence and, live, the feeds, receipts and tickets."""

    def __init__(self, *args, live_mode: bool = False, **kwargs):
        self.live_mode = live_mode
        self._served_html = ""
        super().__init__(*args, **kwargs)

    def _request_path(self) -> str:
        return unquote(urlsplit(self.path).path)

    def _parts(self, path: str) -> list[str] | None:
        if not path.startswith("/") or "\\" in path:
            return None
        parts = path[1:].split("/")
        if any(part in ("", ".", "..") for part in parts):
            return None
        return parts

    def _send_bytes(self, body: bytes, content_type: str, cache_control: str = "no-store", *, head_only: bool = False,
                    modified: float | None = None) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache_control)
        if modified is not None:
            # the live page reloads when its file's Last-Modified moves on
            self.send_header("Last-Modified", email.utils.formatdate(modified, usegmt=True))
        self.end_headers()
        if not head_only:
            self.wfile.write(body)

    def _serve_html(self, path: str, *, head_only: bool = False) -> bool:
        if path in ("/", "/index.html"):
            parts = ["index.html"]
        elif self.live_mode:
            parts = self._parts(path)
            if not parts or parts[-1] != "index.html":
                self.send_error(404)
                return True
        else:
            self.send_error(404)
            return True
        root = Path(self.directory).resolve()
        page = root.joinpath(*parts)
        try:
            if page.is_symlink():
                raise OSError("review page must not be a symlink")
            resolved = page.resolve(strict=True)
            resolved.relative_to(root)
            modified = resolved.stat().st_mtime
            body = _read_regular_file(resolved, 32 * 1024 * 1024)
            self._served_html = body.decode("utf-8")
        except (OSError, ValueError, UnicodeDecodeError):
            self.send_error(404)
            return True
        self._send_bytes(body, "text/html; charset=utf-8", head_only=head_only, modified=modified)
        return True

    def _serve_artifact(self, path: str) -> bool:
        parts = self._parts(path)
        if not parts or len(parts) < 2 or parts[-2] != "artifacts":
            return False
        digest = parts[-1]
        if not re.fullmatch(r"[0-9a-f]{64}", digest) or (not self.live_mode and parts != ["artifacts", digest]):
            self.send_error(404)
            return True
        root = Path(self.directory).resolve()
        page_root = root.joinpath(*parts[:-2]) if self.live_mode else root
        try:
            current = root
            for component in parts[:-2] if self.live_mode else []:
                current = current / component
                if current.is_symlink():
                    raise OSError("artifact path must not contain symlinks")
            page_root = page_root.resolve(strict=True)
            page_root.relative_to(root)
            artifact_dir = page_root / "artifacts"
            if artifact_dir.is_symlink():
                raise OSError("artifact directory must not be a symlink")
            artifact_root = artifact_dir.resolve(strict=True)
            artifact_root.relative_to(page_root)
            body = _read_regular_file(artifact_root / digest, 32 * 1024 * 1024)
        except (OSError, ValueError):
            self.send_error(404)
            return True
        mime = _image_mime(body)
        if mime is None or hashlib.sha256(body).hexdigest() != digest:
            self.send_error(404)
            return True
        self._send_bytes(body, mime, "private, max-age=31536000, immutable")
        return True

    def _serve_live_feed(self, path: str) -> bool:
        feeds = {"/latest.json": ("application/json; charset=utf-8", 1024 * 1024), "/live.log": ("text/plain; charset=utf-8", 4 * 1024 * 1024)}
        feed = feeds.get(path)
        if not self.live_mode or feed is None:
            return False
        content_type, limit = feed
        try:
            body = _read_regular_file(Path(self.directory) / path[1:], limit)
        except OSError:
            self.send_error(404)
            return True
        self._send_bytes(body, content_type)
        return True

    _RECORDS = {
        ("receipts", ".json"): "application/json; charset=utf-8",
        ("tickets", ".json"): "application/json; charset=utf-8",
        ("tickets", ".md"): "text/plain; charset=utf-8",
    }

    def _serve_record(self, path: str) -> bool:
        """Live only: the receipt and ticket files the page links to, one flat level deep."""
        parts = self._parts(path)
        if not self.live_mode or not parts or len(parts) != 2 or parts[0] not in ("receipts", "tickets"):
            return False
        folder, name = parts
        content_type = self._RECORDS.get((folder, Path(name).suffix))
        if content_type is None or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name):
            self.send_error(404)
            return True
        root = Path(self.directory).resolve()
        try:
            record_dir = root / folder
            if record_dir.is_symlink():
                raise OSError("record directory must not be a symlink")
            record_dir.resolve(strict=True).relative_to(root)
            body = _read_regular_file(record_dir / name, 4 * 1024 * 1024)
        except (OSError, ValueError):
            self.send_error(404)
            return True
        self._send_bytes(body, content_type)
        return True

    def do_GET(self):  # noqa: N802
        self._served_html = ""
        path = self._request_path()
        if self._serve_artifact(path):
            return
        if path in ("/", "/index.html") or (self.live_mode and path.endswith("/index.html")):
            self._serve_html(path)
            return
        if self._serve_live_feed(path) or self._serve_record(path):
            return
        self.send_error(404)

    def do_HEAD(self):  # noqa: N802
        self._served_html = ""
        path = self._request_path()
        if self.live_mode and (path in ("/", "/index.html") or path.endswith("/index.html")):
            self._serve_html(path, head_only=True)
            return
        self.send_error(405)

    def end_headers(self):  # noqa: N802
        self.send_header("Content-Security-Policy", content_security_policy(self._served_html, live=self.live_mode))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "SAMEORIGIN" if self.live_mode else "DENY")
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

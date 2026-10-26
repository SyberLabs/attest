"""Pipeline: ingest, compile, execute, judge, ledger, render. One call per doc.

Browser claims go through whatever BrowserRunner the caller passes; the shell
runner handles shell claims and keeps servers alive in between. The result is a
ProveResult with everything render and the loop need.
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .cli_config import CliConfig, find_cli_config, load_cli_config
from .compiler import CompileError, compile_doc
from .execution.recipes import ApprovedRecipeExecutor
from .ingest import ingest
from .ledger import Ledger
from .models import Brief, Claim, Doc, Evidence, Verdict
from .render import badge_svg, proof_md, render_page, write_page
from .runner import BrowserRunner, FakeBrowserRunner
from .shell import ShellSession, new_scratch
from .verdict import Rules, judge, load_rules, satisfies_dependents

ATTEST_DIR = Path(os.environ.get("ATTEST_HOME", ".attest"))
_UNCOMPILED_ACTION = re.compile(
    r"\b(?:click|type|select|press|navigate to|visit|head over to|go to)\b|"
    r"\bopen\s+(?:the|a|your|this)\s+(?:browser|page|site|url|menu|app|button|link|tab)\b|"
    r"\b(?:you should see|you will see|you'll see)\b",
    re.IGNORECASE,
)


def _uncompiled_required_lines(doc: Doc, claims: list[Claim]) -> list[int]:
    if not doc.browser_claims:
        return []
    covered = {
        line
        for claim in claims
        for line in range(claim.span.start_line, claim.span.end_line + 1)
    }
    fenced = {line for fence in doc.fences for line in range(fence.start_line - 1, fence.end_line + 2)}
    return [
        number
        for number, line in enumerate(doc.lines, 1)
        if number not in covered and number not in fenced and _UNCOMPILED_ACTION.search(line)
    ]


def out_dir_for(doc: Doc, home: Path = ATTEST_DIR) -> Path:
    return home / doc.sha256[:16]


@dataclass
class ProveResult:
    doc: Doc
    claims: list[Claim]
    evidence: dict[str, Evidence]
    verdicts: list[Verdict]
    turn: int
    out_dir: Path
    page: Path | None = None
    briefs: dict[str, Brief] = field(default_factory=dict)
    prior: dict[str, Verdict] = field(default_factory=dict)
    scratch: Path | None = None
    simulation: bool = False
    proposals: dict[str, str] = field(default_factory=dict)
    proposal_states: dict[str, str] = field(default_factory=dict)
    # sidecar fixtures written before the first claim: name, scratch path, sha256
    fixtures: list[dict] = field(default_factory=list)

    @property
    def by_id(self) -> dict[str, Verdict]:
        return {v.claim_id: v for v in self.verdicts}

    def failing(self) -> list[Claim]:
        """Claims the loop may act on: stale or broken shell and browser claims."""
        v = self.by_id
        return [c for c in self.claims if c.kind in ("shell", "browser") and v[c.id].status in ("stale", "broken")]


def execute(
    claims: list[Claim],
    runner: BrowserRunner,
    rules: Rules,
    scratch: Path,
    shell_env: dict[str, str] | None = None,
    only: set[str] | None = None,
    prior: dict[str, Verdict] | None = None,
    log=None,
    stop_before: str | None = None,
    skip: set[str] | None = None,
    on_start=None,
    recipes_only: bool = False,
    recipe_executor: ApprovedRecipeExecutor | None = None,
    simulation: bool = False,
    cli_config: CliConfig | None = None,
    allow_substitutes: bool = False,
) -> tuple[dict[str, Evidence], list[Verdict], ShellSession]:
    """Run claims in doc order; simulation evidence can never produce a proven verdict."""
    evidence: dict[str, Evidence] = {}
    verdicts: list[Verdict] = []
    reached: dict[str, Verdict] = dict(prior or {})
    skip = skip or set()
    cli_config = cli_config or CliConfig()
    check_setup(claims, cli_config)
    session = ShellSession(
        workdir=scratch,
        timeout_s=rules.shell_timeout_s,
        env=shell_env or {},
        recipes_only=recipes_only,
        recipe_executor=recipe_executor,
        services=cli_config.services,
        substitutes=cli_config.substitutes,
        allow_substitutes=allow_substitutes,
        ready_timeout_s=rules.ready_timeout_s,
    )
    stop_index = next((i for i, claim in enumerate(claims) if claim.id == stop_before), None)
    try:
        # fixtures land before the first claim, whatever kind it is
        if cli_config.fixtures:
            session.write_fixtures(cli_config.fixtures)
        for i, c in enumerate(claims):
            if c.id in skip:
                v = Verdict(
                    claim_id=c.id,
                    status="unverifiable",
                    reason="unverifiable: not applicable on this run (skipped with --skip)",
                    evidence=None,
                )
                reached[c.id] = v
                verdicts.append(v)
                if log:
                    log(c, v)
                continue
            if stop_index is not None and i >= stop_index:
                v = Verdict(
                    claim_id=c.id,
                    status="unverifiable",
                    reason=f"unverifiable: stopped before {stop_before} (card stops before irreversible or real-form steps)",
                    evidence=None,
                )
                reached[c.id] = v
                verdicts.append(v)
                if log:
                    log(c, v)
                continue
            if only is not None and c.id not in only and c.id in reached:
                v = reached[c.id]
                if v.evidence is not None:
                    evidence[c.id] = v.evidence
                verdicts.append(v)
                continue
            if on_start:
                on_start(c)
            ev: Evidence | None = None
            # A dependency that was never judged is not authorization. Skipped
            # claims have no evidence and still let dependents run.
            deps_ok = all(
                d in reached and (satisfies_dependents(reached[d]) or reached[d].evidence is None)
                for d in c.depends_on
            )
            if deps_ok:
                if c.kind == "shell":
                    ev = session.run(c)
                elif c.kind == "browser":
                    obs = runner.observe(c, session.base_url)
                    ev = Evidence(claim_id=c.id, kind="browser", observation=obs)
                elif c.kind == "assert":
                    src = next((reached[d].evidence for d in c.depends_on if d in reached and reached[d].evidence), None)
                    if src is not None and src.kind == "shell":
                        ev = src.model_copy(update={"claim_id": c.id})
            v = judge(c, ev, reached, rules)
            if simulation:
                v = v.model_copy(update={"status": "unverifiable", "reason": f"simulation only; not verification evidence: {v.reason}"})
            if ev is not None:
                evidence[c.id] = ev
            reached[c.id] = v
            verdicts.append(v)
            if log:
                log(c, v)
    except BaseException:
        session.close()
        raise
    return evidence, verdicts, session


def check_setup(claims: list[Claim], cli_config: CliConfig) -> None:
    """Every fixture a claim names in ``setup`` must exist in the sidecar, before anything runs."""
    for c in claims:
        missing = [name for name in c.setup if name not in cli_config.fixtures]
        if missing:
            where = f" ({cli_config.path})" if cli_config.path else ""
            raise ValueError(f"{c.id} needs fixtures {missing} that the sidecar{where} does not define")


def annotate_setup(claims: list[Claim], cli_config: CliConfig) -> list[Claim]:
    """By rule: a shell claim whose command names a fixture's path gets that fixture in ``setup``."""
    out = []
    for c in claims:
        if c.kind == "shell" and c.command:
            names = [n for n in cli_config.fixtures_named_in(c.command) if n not in c.setup]
            if names:
                c = c.model_copy(update={"setup": [*c.setup, *names]})
        out.append(c)
    return out


def prove(
    source: str,
    runner: BrowserRunner | None = None,
    home: Path = ATTEST_DIR,
    rules: Rules | None = None,
    llm=None,
    keep_servers: bool = False,
    shell_env: dict[str, str] | None = None,
    log=None,
    stop_before: str | None = None,
    skip: set[str] | None = None,
    live: bool = False,
    on_start=None,
    recipes_only: bool = False,
    recipe_executor: ApprovedRecipeExecutor | None = None,
    simulation: bool = False,
    app_dir: Path | None = None,
    cli_config: CliConfig | Path | str | None = None,
    allow_substitutes: bool = False,
) -> ProveResult:
    rules = rules or load_rules()
    if runner is None:
        raise ValueError("an explicit browser runner is required; use FakeBrowserRunner with simulation=True for a demo")
    if getattr(runner, "name", None) == "fake" and not simulation:
        raise ValueError("fake browser evidence requires simulation=True and cannot verify claims")
    if simulation and getattr(runner, "name", None) != "fake":
        raise ValueError("simulation=True is only valid with the fake browser runner")
    doc = ingest(source)
    if cli_config is None:
        found = find_cli_config(source)
        cli_config = load_cli_config(found) if found else CliConfig()
    elif not isinstance(cli_config, CliConfig):
        cli_config = load_cli_config(cli_config)
    if allow_substitutes and not cli_config.substitutes:
        raise ValueError("--substitute given but the sidecar defines no substitutes")
    doc = doc.model_copy(update={"browser_claims": cli_config.browser_claims})
    candidate = None
    if app_dir is not None:
        # spec mode: the spec's shell steps serve a fresh copy of the app, never the original
        from .implement import make_candidate

        candidate = make_candidate(Path(app_dir), doc.sha256, home)
        shell_env = {**(shell_env or {}), "ATTEST_APP_DIR": candidate.posix}
    claims = annotate_setup(compile_doc(doc, llm=llm), cli_config)
    check_setup(claims, cli_config)
    uncovered = _uncompiled_required_lines(doc, claims)
    if uncovered:
        raise CompileError(
            "incomplete compilation; required browser/actionable lines were not claimed: "
            + ", ".join(map(str, uncovered))
        )
    doc = doc.model_copy(update={"claims": claims})
    if stop_before is not None and stop_before not in {claim.id for claim in claims}:
        valid_ids = ", ".join(claim.id for claim in claims) or "(none)"
        raise ValueError(f"unknown claim id {stop_before!r}; valid ids: {valid_ids}")
    skip = skip or set()
    unknown_skip = skip - {claim.id for claim in claims}
    if unknown_skip:
        valid_ids = ", ".join(claim.id for claim in claims) or "(none)"
        unknown_ids = ", ".join(sorted(unknown_skip))
        raise ValueError(f"unknown claim id(s) {unknown_ids!r}; valid ids: {valid_ids}")
    prepare = getattr(runner, "prepare", None)
    if prepare is not None:
        stop_index = next((i for i, c in enumerate(claims) if c.id == stop_before), len(claims))
        run_claims = [c for i, c in enumerate(claims) if i < stop_index and c.id not in skip]
        prepare(run_claims, doc.source)
    scratch = new_scratch()
    out_dir = out_dir_for(doc, home)
    live_state = {"now": "Preparing claims"}
    session = None
    try:
        if live:
            result = ProveResult(doc, claims, {}, [], 0, out_dir, scratch=scratch, simulation=simulation)
            _write_live_page(result, home, runner.name, live_state["now"], True)

        def announce(claim):
            if on_start:
                on_start(claim)
            if not live:
                return
            live_state["now"] = claim_banner(claim)
            _write_live_page(result, home, runner.name, live_state["now"], True)

        def record(c, v):
            if log:
                log(c, v)
            if live:
                result.verdicts.append(v)
                _write_live_page(result, home, runner.name, live_state["now"], True)

        evidence, verdicts, session = execute(
            claims, runner, rules, scratch, shell_env=shell_env, log=record if live else log,
            stop_before=stop_before, skip=skip, on_start=announce, simulation=simulation,
            recipes_only=recipes_only, recipe_executor=recipe_executor,
            cli_config=cli_config, allow_substitutes=allow_substitutes,
        )
        if not keep_servers:
            session.close()
        ledger = Ledger(home / "ledger.jsonl")
        turn = ledger.next_turn()
        ledger.record(doc.sha256, turn, claims, verdicts)
        runs = getattr(runner, "runs", None)
        if runs is not None:
            run_count = ledger.record_new_kane_runs(doc.sha256, turn, runs)
        else:
            run_count = 0
        result = ProveResult(
            doc, claims, evidence, verdicts, turn, out_dir, scratch=scratch, simulation=simulation,
            fixtures=list(getattr(session, "fixtures_written", [])),
        )
        result._kane_runs_count = run_count  # type: ignore[attr-defined]
        result._session = session  # type: ignore[attr-defined]
        result.candidate = candidate  # type: ignore[attr-defined]
        render_result(result, home, runner.name)
        if live:
            _write_live_page(result, home, runner.name, "Proof complete", False)
        return result
    except BaseException:
        if session is not None:
            session.close()
        cleanup_scratch(scratch)
        raise


def claim_banner(claim: Claim) -> str:
    """One plain line saying what is about to happen; shared by the terminal and the live page."""
    line = claim.span.start_line
    if claim.kind == "shell":
        cmd = (claim.command or claim.span.text).strip().splitlines()[0]
        more = " ..." if len((claim.command or "").strip().splitlines()) > 1 else ""
        return f"Running `{cmd}{more}` (line {line})"
    if claim.kind == "browser":
        if claim.expects:
            return f'Kane is checking line {line}: does the page show "{claim.expects}"?'
        return f"Kane is checking line {line}"
    if claim.kind == "prompt":
        name = re.search(r"^name:\s*(.+)$", claim.command or "", re.M)
        label = name.group(1).strip() if name else "prompt"
        return f"Running prompt `{label}` (line {line})"
    return f"Reading line {line}"


def _replace(tmp: Path, target: Path) -> None:
    """os.replace with a short retry: on Windows the live server may be reading the target."""
    import time

    for _ in range(20):
        try:
            os.replace(tmp, target)
            return
        except PermissionError:
            time.sleep(0.05)
    tmp.unlink(missing_ok=True)


def _write_live_page(result: ProveResult, home: Path, runner_name: str, now: str, running: bool) -> None:
    """Refresh the live page. A failed refresh never fails the prove."""
    try:
        _write_live_page_unsafe(result, home, runner_name, now, running)
    except OSError:
        pass


def _write_live_page_unsafe(result: ProveResult, home: Path, runner_name: str, now: str, running: bool) -> None:
    import json
    from .ledger import Ledger
    from .render import write_page

    result.out_dir.mkdir(parents=True, exist_ok=True)
    strip = Ledger(home / "ledger.jsonl").summary(result.doc.sha256, result.turn) if result.turn else {
        "claims": len(result.claims), "proven": 0, "stale": 0, "broken": 0,
        "unverifiable": 0, "skipped": 0, "turn": 0, "loops_attempted": 0, "loops_closed": 0,
    }
    verdicts, prior = localize_screenshots(result)
    html_text = render_page(result.doc, result.claims, verdicts, strip, briefs=result.briefs,
                            prior_verdicts=prior, runner_name=runner_name, live=running, now=now,
                            authority=getattr(result, "authority", {}), receipts=getattr(result, "receipts", {}))
    page = result.out_dir / "index.html"
    tmp = page.with_suffix(".html.tmp")
    tmp.write_text(html_text, encoding="utf-8")
    _replace(tmp, page)
    result.page = page
    (home / "latest").write_text(str(result.out_dir.resolve()), encoding="utf-8")
    latest = home / "latest.json"
    temp = latest.with_suffix(".json.tmp")
    temp.write_text(json.dumps({"page": f"{result.out_dir.name}/index.html", "running": running, "now": now}), encoding="utf-8")
    _replace(temp, latest)


def localize_screenshots(result: ProveResult) -> tuple[list[Verdict], dict[str, Verdict]]:
    """Copy each browser screenshot to <out_dir>/shots/<claim>.<ext> for the page.

    Returns verdicts whose evidence points at the copies; ``result`` keeps the
    original pack paths so briefs can name them.
    """
    shots = result.out_dir / "shots"

    def local(ev: Evidence | None, tag: str = "") -> Evidence | None:
        if ev is None or ev.observation is None or not ev.observation.screenshot:
            return ev
        src = Path(ev.observation.screenshot)
        if not src.is_absolute() or not src.is_file():
            return ev
        shots.mkdir(parents=True, exist_ok=True)
        # a looped claim keeps both pictures: <claim>.jpg (now) and <claim>-before.jpg
        dest = shots / f"{ev.claim_id}{tag}{src.suffix}"
        shutil.copyfile(src, dest)
        obs = ev.observation.model_copy(update={"screenshot": f"shots/{dest.name}"})
        return ev.model_copy(update={"observation": obs})

    verdicts = [v.model_copy(update={"evidence": local(v.evidence)}) for v in result.verdicts]
    prior = {k: v.model_copy(update={"evidence": local(v.evidence, "-before")}) for k, v in result.prior.items()}
    return verdicts, prior


def render_result(result: ProveResult, home: Path, runner_name: str) -> Path:
    ledger = Ledger(home / "ledger.jsonl")
    strip = ledger.summary(result.doc.sha256, result.turn)
    verdicts, prior = localize_screenshots(result)
    html_text = render_page(
        result.doc,
        result.claims,
        verdicts,
        strip,
        briefs=result.briefs,
        prior_verdicts=prior,
        authority=getattr(result, "authority", {}),
        receipts=getattr(result, "receipts", {}),
        runner_name=runner_name,
    )
    page = write_page(result.out_dir, html_text)
    result.page = page
    (home / "latest").write_text(str(result.out_dir.resolve()), encoding="utf-8")
    # keep what the page needs next to it
    (result.out_dir / "doc.md").write_text("\n".join(result.doc.lines) + "\n", encoding="utf-8")
    (result.out_dir / "claims.json").write_text(
        "[" + ",".join(c.model_dump_json() for c in result.claims) + "]", encoding="utf-8"
    )
    (result.out_dir / "verdicts.json").write_text(
        "[" + ",".join(v.model_dump_json() for v in result.verdicts) + "]", encoding="utf-8"
    )
    if result.fixtures:
        import json

        (result.out_dir / "fixtures.json").write_text(json.dumps(result.fixtures, indent=2), encoding="utf-8")
    return page


def export_badge(result: ProveResult, home: Path, runner_name: str, dest: Path | None = None) -> tuple[Path, Path]:
    ledger = Ledger(home / "ledger.jsonl")
    strip = ledger.summary(result.doc.sha256, result.turn)
    dest = dest or Path.cwd()
    dest.mkdir(parents=True, exist_ok=True)
    svg = dest / "docs-proven.svg"
    md = dest / "PROOF.md"
    svg.write_text(badge_svg(strip["proven"], strip["claims"]), encoding="utf-8")
    md.write_text(proof_md(result.doc, result.claims, result.verdicts, strip, runner_name), encoding="utf-8")
    return svg, md


def cleanup_scratch(path: Path | None) -> None:
    if path and path.exists():
        shutil.rmtree(path, ignore_errors=True)

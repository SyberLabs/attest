"""Pipeline: ingest, compile, execute, judge, ledger, render. One call per doc.

Browser claims go through whatever BrowserRunner the caller passes; the shell
runner handles shell claims and keeps servers alive in between. The result is a
ProveResult with everything render and the loop need.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .compiler import compile_doc
from .ingest import ingest
from .ledger import Ledger
from .models import Brief, Claim, Doc, Evidence, Verdict
from .render import badge_svg, proof_md, render_page, write_page
from .runner import BrowserRunner, FakeBrowserRunner
from .shell import ShellSession, new_scratch
from .verdict import Rules, judge, load_rules

ATTEST_DIR = Path(os.environ.get("ATTEST_HOME", ".attest"))


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
) -> tuple[dict[str, Evidence], list[Verdict], ShellSession]:
    """Run claims in doc order and judge each as soon as its evidence exists.

    ``only`` restricts execution to those claim ids; the rest reuse ``prior``
    verdicts. The ShellSession is returned open so a caller can keep servers up;
    callers must close it.
    """
    evidence: dict[str, Evidence] = {}
    verdicts: list[Verdict] = []
    reached: dict[str, Verdict] = dict(prior or {})
    session = ShellSession(workdir=scratch, timeout_s=rules.shell_timeout_s, env=shell_env or {})
    stop_index = next((i for i, claim in enumerate(claims) if claim.id == stop_before), None)
    for i, c in enumerate(claims):
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
        ev: Evidence | None = None
        deps_ok = all(reached.get(d) is None or reached[d].status == "proven" for d in c.depends_on)
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
        if ev is not None:
            evidence[c.id] = ev
        reached[c.id] = v
        verdicts.append(v)
        if log:
            log(c, v)
    return evidence, verdicts, session


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
) -> ProveResult:
    rules = rules or load_rules()
    runner = runner or FakeBrowserRunner()
    doc = ingest(source)
    claims = compile_doc(doc, llm=llm)
    doc = doc.model_copy(update={"claims": claims})
    if stop_before is not None and stop_before not in {claim.id for claim in claims}:
        valid_ids = ", ".join(claim.id for claim in claims) or "(none)"
        raise ValueError(f"unknown claim id {stop_before!r}; valid ids: {valid_ids}")
    prepare = getattr(runner, "prepare", None)
    if prepare is not None:
        # a batching runner plans its test files from the claims that will run
        if stop_before is None:
            run_claims = claims
        else:
            stop_index = next(i for i, c in enumerate(claims) if c.id == stop_before)
            run_claims = claims[:stop_index]
        prepare(run_claims, doc.source)
    scratch = new_scratch()
    evidence, verdicts, session = execute(
        claims, runner, rules, scratch, shell_env=shell_env, log=log, stop_before=stop_before
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
    out_dir = out_dir_for(doc, home)
    result = ProveResult(doc, claims, evidence, verdicts, turn, out_dir, scratch=scratch)
    result._kane_runs_count = run_count  # type: ignore[attr-defined]
    result._session = session  # type: ignore[attr-defined]
    render_result(result, home, runner.name)
    return result


def localize_screenshots(result: ProveResult) -> tuple[list[Verdict], dict[str, Verdict]]:
    """Copy each browser screenshot to <out_dir>/shots/<claim>.<ext> for the page.

    Returns verdicts whose evidence points at the copies; ``result`` keeps the
    original pack paths so briefs can name them.
    """
    shots = result.out_dir / "shots"

    def local(ev: Evidence | None) -> Evidence | None:
        if ev is None or ev.observation is None or not ev.observation.screenshot:
            return ev
        src = Path(ev.observation.screenshot)
        if not src.is_absolute() or not src.is_file():
            return ev
        shots.mkdir(parents=True, exist_ok=True)
        dest = shots / f"{ev.claim_id}{src.suffix}"
        shutil.copyfile(src, dest)
        obs = ev.observation.model_copy(update={"screenshot": f"shots/{dest.name}"})
        return ev.model_copy(update={"observation": obs})

    verdicts = [v.model_copy(update={"evidence": local(v.evidence)}) for v in result.verdicts]
    prior = {k: v.model_copy(update={"evidence": local(v.evidence)}) for k, v in result.prior.items()}
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

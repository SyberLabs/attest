"""Kane CLI runner: browser claims in, BrowserObservations out.

Consecutive browser claims (no shell claim between them) become one Kane
``_test.md`` with one ``## <claim id>`` step per claim. Kane runs the file with
``kane-cli testmd run --agent``; Attest reads the NDJSON stream line by line and
then the sealed evidence pack, and maps every ``## `` step back to its claim.

What the 10:00 smoke test established (NOTES.md, event day):

* Every ``## `` step is its own Kane run with its own ``run_end``. The exact
  ``store as`` value lives in ``run_end.context.variables[<key>]`` (authored
  runs) or ``variables_out`` (replays); ``final_state`` keys are model-chosen on
  authored runs and absent on replays, so it is only the last fallback.
* Pack step directories are Kane's action steps, named ``<ordinal>-<run>-<n>``.
  The ``<run>`` prefix is the 0-based index of the ``## `` step.
* A step phrased "check that the page shows X" fails when the page says Y.
  Attest wants Kane as a witness, not a judge, so each step stores the text
  closest to X and ends in a check that cannot fail on a label mismatch.

The file for a group always has the same path, so a re-run after the loop
patches one claim replays the unchanged steps from Kane's cache.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import yaml

from .models import BrowserObservation, Claim
from .proposals import current_build_digest
from .runner import UNBOUND_NOTE_PREFIX

KANE_CMD = os.environ.get("ATTEST_KANE_CMD", "kane-cli")
KANE_HOME = Path(os.environ.get("ATTEST_KANE_HOME", ".attest/kane"))
STEP_TIMEOUT_S = int(os.environ.get("ATTEST_KANE_STEP_TIMEOUT_S", "180"))
FILE_MAX_CLAIMS = 14  # keep one test file under about 15 steps

_UNREACHABLE = re.compile(r"ERR_CONNECTION_REFUSED|ERR_NAME_NOT_RESOLVED|ERR_ADDRESS_UNREACHABLE|ERR_CONNECTION_TIMED_OUT|unreachable", re.I)
# Chrome's own error page: Kane can "pass" a step that stored this, but the doc was never tested
_ERROR_PAGE = re.compile(r"this site can.t be reached|ERR_NAME_NOT_RESOLVED|ERR_CONNECTION_\w+|DNS_PROBE_\w+|ERR_INTERNET_DISCONNECTED", re.I)


class KaneSetupError(RuntimeError):
    """kane-cli exited 2: parse, auth, Chrome launch or flag error. Not a verdict."""


# -- test file writer -----------------------------------------------------------------


def group_claims(claims: list[Claim], max_claims: int = FILE_MAX_CLAIMS) -> list[list[Claim]]:
    """Consecutive browser claims share a file; a shell claim between them splits it."""
    groups: list[list[Claim]] = []
    current: list[Claim] = []
    for c in claims:
        if c.kind == "shell":
            if current:
                groups.append(current)
            current = []
        elif c.kind == "browser":
            if len(current) >= max_claims:
                groups.append(current)
                current = []
            current.append(c)
    if current:
        groups.append(current)
    return groups


_CHECK = re.compile(r"(?:^|,?\s+(?:and\s+|then\s+)?)(?:check|confirm|verify|ensure|make sure)\b", re.I)
_URL = re.compile(r"https?://[^\s\"'<>)]+")


def action_part(objective: str) -> str:
    """The objective up to its check clause: what Kane should do, not what it should judge."""
    m = _CHECK.search(objective)
    head = objective[: m.start()] if m else objective
    return head.strip().rstrip(",;:.").strip()


def store_key(claim: Claim) -> str:
    return claim.store_as or f"{claim.id}_label"


_LOCAL = re.compile(r"https?://(?:localhost|127\.0\.0\.1|0\.0\.0\.0)(?::\d+)?/?")


def retarget(text: str, base_url: str | None) -> str:
    """Point local URLs in the doc at the server that actually started (a busy port moves Vite to 5174)."""
    if not base_url or not _LOCAL.match(base_url):
        return text
    base = base_url if base_url.endswith("/") else base_url + "/"
    return _LOCAL.sub(lambda m: base if m.group(0).endswith("/") else base.rstrip("/"), text)


_CLICKS = re.compile(r"\b(?:click|clicks|select|choose|tap|press|open)\b", re.I)
_CLICKED = re.compile(r"\b(?:click|clicks|select|choose|tap|press)\s+(?:on\s+)?(?:the\s+)?(?:[\w-]+\s+){0,3}?\"([^\"]+)\"", re.I)


def _compound(action: str) -> bool:
    """Two or more clicks in one step, e.g. 'click your profile photo and select "Your repositories"'."""
    return len(_CLICKS.findall(action)) >= 2


def step_body(claim: Claim, base_url: str | None = None) -> str:
    """Witness phrasing: act, store the text closest to what the doc promised, check the page loaded."""
    parts = []
    action = retarget(action_part(claim.objective or ""), base_url)
    if action:
        parts.append(action + ".")
    key = store_key(claim)
    if claim.expects and claim.expects.startswith(("http://", "https://")):
        parts.append(f"Store the URL of the current page as '{key}'.")
    elif claim.expects and re.search(r"\b(?:click|clicks|select|choose|tap|press|toggle|check the box)\b", action, re.I):
        # only labels right after a click verb are controls; other quotes are text to type
        labels = [q.casefold() for q in _CLICKED.findall(action)]
        if claim.expects.casefold() not in labels:
            # the doc promises an outcome of the click ("count is 1", a status "Won"), not the
            # control's own label ("Mark as won"): read the page after acting
            parts.append(f"Store the exact visible text on the page that is closest to \"{claim.expects}\" after the click as '{key}'.")
        elif _compound(action):
            # the target only exists after an earlier click (a menu item): read it last
            parts.append(f"Store the exact visible text of the last menu item, link or button you clicked as '{key}'.")
        else:
            # read the control before clicking: after the click the page has changed and
            # Kane stored whatever sat nearby ("General" for the Settings tab)
            store = f"Store the exact visible text of the link or button on the page that is closest to \"{claim.expects}\" as '{key}'."
            parts.insert(0, store)
    elif claim.expects:
        parts.append(f"Store the exact visible text of the heading, label, button or message on the page that is closest to \"{claim.expects}\" as '{key}'.")
    else:
        parts.append(f"Store the exact visible text of the main heading or message on the page as '{key}'.")
    parts.append("Verify the page has finished loading.")
    return " ".join(parts)


def start_url(group: list[Claim], base_url: str | None) -> str | None:
    """The shell runner's detected URL, else the first URL a claim names."""
    if base_url:
        return base_url
    for c in group:
        m = _URL.search(c.objective or "") or _URL.search(c.span.text)
        if m:
            return m.group(0).rstrip(".,")
    return None


def test_file_text(group: list[Claim], url: str | None, title: str, max_steps: int = 15) -> str:
    front = {"mode": "testing", "max_steps": max_steps}
    if url:
        front["url"] = url
    lines = ["---", yaml.safe_dump(front, sort_keys=False).strip(), "---", "", f"# {title}", ""]
    for c in group:
        lines += [f"## {c.id}", step_body(c, url), ""]
    return "\n".join(lines)


# -- NDJSON ---------------------------------------------------------------------------


@dataclass
class StepRun:
    """One ``## `` step as the NDJSON stream reported it."""

    index: int
    heading: str
    status: str = "running"
    variables: dict[str, str] = field(default_factory=dict)
    final_state: dict[str, str] = field(default_factory=dict)
    reason: str = ""
    reason_code: str = ""
    summary: str = ""
    credits: float = 0.0
    replayed: bool = False


@dataclass
class KaneRun:
    steps: dict[int, StepRun] = field(default_factory=dict)
    session_id: str | None = None
    evidence_id: str | None = None
    overall: str | None = None
    authored: int = 0
    replayed: int = 0
    healed: bool = False
    credits: float = 0.0
    exit_code: int | None = None
    stderr: str = ""


def _values(obj: dict | None, field_name: str) -> dict[str, str]:
    out = {}
    for k, v in (obj or {}).items():
        val = v.get(field_name) if isinstance(v, dict) else v
        if val is not None:
            out[k] = str(val)
    return out


def parse_events(lines: Iterable[str]) -> KaneRun:
    """Fold the ``testmd run --agent`` stream into one record. Unknown events are ignored."""
    run = KaneRun()
    current: StepRun | None = None
    for raw in lines:
        raw = raw.strip()
        if not raw.startswith("{"):
            continue
        try:
            e = json.loads(raw)
        except json.JSONDecodeError:
            continue
        t = e.get("type")
        if t is None:
            continue  # untyped progress event; run_end is the one to trust
        if t == "test_md_step_start":
            current = StepRun(index=int(e["step_index"]), heading=str(e.get("heading", "")))
            run.steps[current.index] = current
        elif t == "run_end" and current is not None:
            ctx = e.get("context") or {}
            current.variables = {**_values(e.get("variables_out"), "value"), **_values(ctx.get("variables"), "value")}
            current.final_state = {k: str(v) for k, v in (e.get("final_state") or {}).items()}
            current.reason = str(e.get("reason") or "")
            current.reason_code = str(e.get("reason_code") or "")
            current.summary = str(e.get("summary") or "")
            current.credits = float(e.get("credits_consumed") or 0.0)
            current.replayed = current.reason.startswith("replay")
            run.credits += current.credits
        elif t == "test_md_step_end":
            step = run.steps.setdefault(int(e["step_index"]), StepRun(index=int(e["step_index"]), heading=""))
            step.status = str(e.get("status", "failed"))
        elif t == "test_md_evidence_ingest":
            run.evidence_id = e.get("evidence_id")
        elif t == "test_md_summary":
            steps = e.get("steps") or {}
            run.authored = int(steps.get("author_decisions") or 0)
            run.replayed = int(steps.get("replay_decisions") or 0)
            run.healed = bool((e.get("adaptive_heal") or {}).get("triggered"))
            run.overall = e.get("overall_status")
        elif t == "test_md_done":
            run.session_id = e.get("session_id")
            run.overall = e.get("overall_status", run.overall)
    return run


# -- evidence pack --------------------------------------------------------------------


@dataclass
class PackStep:
    run_index: int
    ordinal: int
    dir: Path
    status: str
    summary: str
    screenshot: Path | None
    annotated: Path | None
    failure: dict | None = None


def pack_sessions(pack: Path) -> set[str]:
    """Session ids a pack's ``result.yaml`` files name under ``external_id.session_id``."""
    out: set[str] = set()
    try:
        with zipfile.ZipFile(pack) as z:
            for name in z.namelist():
                if not name.endswith("/result.yaml"):
                    continue
                try:
                    data = yaml.safe_load(z.read(name).decode("utf-8", "replace")) or {}
                except yaml.YAMLError:
                    continue
                external = data.get("external_id") if isinstance(data, dict) else None
                sid = external.get("session_id") if isinstance(external, dict) else None
                if sid:
                    out.add(str(sid))
    except (OSError, zipfile.BadZipFile):
        pass
    return out


def find_pack(evidence_dir: Path, session_id: str | None) -> Path | None:
    """The newest pack whose ``result.yaml`` names exactly this session, else None.

    A pack is never chosen by recency alone: without a session id, or when no pack
    names it, another run's evidence would be read as this run's.
    """
    if not session_id or not evidence_dir.is_dir():
        return None
    packs = sorted(evidence_dir.glob("*.evidence"), key=lambda p: p.stat().st_mtime, reverse=True)
    return next((p for p in packs if session_id in pack_sessions(p)), None)


def unpack(pack: Path, dest: Path) -> Path:
    """Extract everything but network HARs (they carry cookies and are not needed)."""
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(pack) as z:
        for name in z.namelist():
            if name.endswith(".har") or name.endswith("/"):
                continue
            target = (dest / name).resolve()
            if not target.is_relative_to(dest.resolve()):
                continue  # zip-slip guard
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(z.read(name))
    return dest


_STEP_DIR = re.compile(r"^(\d+)-(\d+)-")


def read_pack(root: Path) -> dict[int, list[PackStep]]:
    """Steps from an unpacked pack, grouped by the 0-based ``## `` index, in ordinal order."""
    failures: dict[str, dict] = {}
    root_failure = root / "failure.yaml"
    if root_failure.exists():
        data = yaml.safe_load(root_failure.read_text(encoding="utf-8")) or {}
        for f in data.get("failures") or []:
            p = root / str(f.get("path", ""))
            if p.is_file():
                rec = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
                failures[str(p.parent.resolve())] = rec
    out: dict[int, list[PackStep]] = {}
    for step_json in root.glob("tests/*/steps/*/step.json"):
        d = step_json.parent
        m = _STEP_DIR.match(d.name)
        if not m:
            continue
        meta = json.loads(step_json.read_text(encoding="utf-8"))
        local_failure = d / "failure.yaml"
        failure = failures.get(str(d.resolve()))
        if failure is None and local_failure.exists():
            failure = yaml.safe_load(local_failure.read_text(encoding="utf-8")) or {}
        shot = next((d / n for n in ("screenshot.png", "screenshot.jpg") if (d / n).exists()), None)
        ann = d / "annotated.png"
        out.setdefault(int(m.group(2)), []).append(
            PackStep(
                run_index=int(m.group(2)),
                ordinal=int(m.group(1)),
                dir=d,
                status=str(meta.get("status", "")),
                summary=str(meta.get("summary", "")),
                screenshot=shot,
                annotated=ann if ann.exists() else None,
                failure=failure,
            )
        )
    for steps in out.values():
        steps.sort(key=lambda s: s.ordinal)
    return out


# -- mapping ------------------------------------------------------------------------


def failure_text(rec: dict) -> str:
    err = (rec.get("error") or {}).get("message") or ""
    title = rec.get("title") or ""
    triage = rec.get("triage") or {}
    rca = (triage.get("rca") or {}).get("root_cause") or ""
    fix = (triage.get("suggested_fix") or {}).get("summary") or ""
    parts = [f"failure: {title}: {err}".strip(": ")]
    if rca:
        parts.append(f"root cause: {' '.join(rca.split())}")
    if fix:
        parts.append(f"suggested fix: {' '.join(fix.split())}")
    return " | ".join(parts)


def observation_for(claim: Claim, index: int, run: KaneRun, pack_steps: dict[int, list[PackStep]]) -> BrowserObservation:
    """Map the ``## `` step at 1-based ``index`` to a BrowserObservation for ``claim``."""
    step = run.steps.get(index)
    steps = pack_steps.get(index - 1, [])
    key = store_key(claim)
    observed: dict[str, str] = {}
    if step is not None:
        val = step.variables.get(key, step.final_state.get(key))
        if val is not None:
            observed[key] = val
    failure = next((s.failure for s in reversed(steps) if s.failure), None)
    ann = next((s.annotated for s in reversed(steps) if s.annotated), None)
    shot = ann or next((s.screenshot for s in reversed(steps) if s.screenshot), None)

    notes = []
    if step is None:
        status = "failed"
        notes.append("kane: step did not run (an earlier step failed or the run stopped)")
    else:
        status = "passed" if step.status == "passed" else "failed"
        mode = "replayed from cache" if step.replayed else "authored"
        notes.append(f"kane: step {step.status}, {mode}; {step.reason or 'no reason given'}")
        if step.summary.strip():
            notes.append(" ".join(step.summary.split())[:400])
    if failure:
        notes.append(failure_text(failure))
    if run.healed:
        notes.append("kane: adaptive heal triggered in this run")
    if any(_ERROR_PAGE.search(v) for v in observed.values()):
        status = "unreachable"
    elif status == "failed" and any(_UNREACHABLE.search(n) for n in notes):
        status = "unreachable"
    return BrowserObservation(status=status, observed=observed, screenshot=str(shot) if shot else None, note=" | ".join(notes))


# -- runner ---------------------------------------------------------------------------


def _slug(s: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower() or "doc"


class KaneBrowserRunner:
    """BrowserRunner backed by ``kane-cli testmd run --agent``."""

    name = "kane"

    def __init__(
        self,
        home: Path | None = None,
        headless: bool = False,
        kane_cmd: str | None = None,
        max_steps: int = 15,
        report: bool = False,
        cdp_endpoint: str | None = None,
        log=None,
    ):
        self.report = report
        self.cdp_endpoint = cdp_endpoint
        self.home = Path(home or KANE_HOME).resolve()
        self.max_steps = max_steps
        self.headless = headless
        self.kane_cmd = kane_cmd or KANE_CMD
        self.log = log or (lambda msg: None)
        self.label = "doc"
        self.source: Path | None = None
        self.groups: list[list[Claim]] = []
        self.results: dict[str, tuple[Claim, BrowserObservation]] = {}
        self.runs: list[KaneRun] = []
        # claim id -> session id of the run whose evidence pack backs its observation (None: unbound)
        self.run_ids: dict[str, str | None] = {}
        if shutil.which(self.kane_cmd) is None:
            raise KaneSetupError(f"{self.kane_cmd!r} not on PATH; npm install -g @testmuai/kane-cli")

    @property
    def evidence_dir(self) -> Path:
        return self.home / ".testmuai" / "evidence"

    def prepare(self, claims: list[Claim], source: str = "doc") -> None:
        """Plan the test files for a doc before execution starts."""
        self.label = _slug(Path(source).stem if not source.startswith("http") else source.rsplit("/", 1)[-1])
        self.source = None if source.startswith("http") else Path(source).resolve()
        self.groups = group_claims(claims)

    def build_digest(self) -> str | None:
        """The app/build identity of the prepared doc's worktree now; None for a URL or unreadable doc."""
        if self.source is None or not self.source.is_file():
            return None
        try:
            return current_build_digest(self.source, hashlib.sha256(self.source.read_bytes()).hexdigest())
        except Exception:  # an unmeasured build binds nothing, so validation fails closed on it
            return None

    def _group_for(self, claim: Claim) -> list[Claim]:
        for i, g in enumerate(self.groups):
            if any(c.id == claim.id for c in g):
                g = [claim if c.id == claim.id else c for c in g]
                self.groups[i] = g
                return g
        self.groups.append([claim])
        return self.groups[-1]

    def test_path(self, group: list[Claim]) -> Path:
        return self.home / "tests" / f"{self.label}-{group[0].id}_test.md"

    def observe(self, claim: Claim, base_url: str | None) -> BrowserObservation:
        cached = self.results.get(claim.id)
        if cached is not None and cached[0] == claim:
            return cached[1]
        group = self._group_for(claim)
        self.run_group(group, base_url)
        return self.results[claim.id][1]

    @staticmethod
    def _reported(cmd: list[str], objective: str) -> list[str]:
        """Wrap a kane-cli command in the event's ``kanehack report``, which passes Kane's
        NDJSON and exit code through and files a redacted run summary for the team."""
        kanehack = shutil.which("kanehack")
        if kanehack is None:
            raise KaneSetupError("--report needs the kanehack helper; run the event installer")
        sha = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        wrapper = [kanehack, "report", "--objective", objective]
        if sha:
            wrapper += ["--commit", sha]
        return wrapper + ["--"] + cmd

    def forget(self, claim_ids) -> None:
        """Drop cached observations so the next observe() runs Kane again (the app changed)."""
        for cid in claim_ids:
            self.results.pop(cid, None)

    def cached(self, claim_id: str) -> BrowserObservation | None:
        """The latest observation for a claim, from whichever file run produced it."""
        hit = self.results.get(claim_id)
        return hit[1] if hit else None

    def run_group(self, group: list[Claim], base_url: str | None) -> KaneRun:
        path = self.test_path(group)
        path.parent.mkdir(parents=True, exist_ok=True)
        url = start_url(group, base_url)
        text = test_file_text(group, url, f"{self.label} {group[0].id}-{group[-1].id}", self.max_steps)
        self._restore_recordings(path, text)
        path.write_text(text, encoding="utf-8", newline="\n")
        cmd = [shutil.which(self.kane_cmd) or self.kane_cmd, "testmd", "run", str(path), "--agent", "--timeout", str(STEP_TIMEOUT_S)]
        if self.headless:
            cmd.append("--headless")
        if self.cdp_endpoint:
            cmd += ["--cdp-endpoint", self.cdp_endpoint]
        if self.report:
            cmd = self._reported(cmd, f"attest: prove {self.label} {', '.join(c.id for c in group)}")
        # no URL in this line: a shell runner proving a doc that runs attest would take it for a server announcing itself
        self.log(f"kane: testmd run {path.name} ({len(group)} step(s))")
        build = self.build_digest()
        run = self._execute(cmd, timeout_s=STEP_TIMEOUT_S * len(group) + 180)
        self.runs.append(run)
        if build is not None and self.build_digest() != build:
            build = None  # redeployed while Kane ran: the pages seen belong to neither build
        if run.exit_code == 2:
            raise KaneSetupError(f"kane-cli exited 2 for {path.name}: {run.stderr.strip()[-600:]}")
        pack = find_pack(self.evidence_dir, run.session_id)
        pack_steps: dict[int, list[PackStep]] = {}
        if pack is not None:
            pack_steps = read_pack(unpack(pack, self.home / "packs" / pack.stem))
        for i, c in enumerate(group, start=1):
            obs = observation_for(c, i, run, pack_steps)
            if run.exit_code == 3 and obs.status != "passed":
                obs = obs.model_copy(update={"note": f"kane: timed out | {obs.note}"})
            if pack is not None:
                obs = obs.model_copy(update={"note": f"{obs.note} | pack: {pack}"})
            else:
                obs = obs.model_copy(update={
                    "status": "failed" if obs.status == "passed" else obs.status,
                    "note": f"{UNBOUND_NOTE_PREFIX} no evidence pack names Kane session {run.session_id or '(none reported)'} | {obs.note}",
                })
            if build is None:
                obs = obs.model_copy(update={"note": f"{obs.note} | build: unknown or changed during this Kane run"})
            obs = obs.model_copy(update={"build_digest": build})
            self.results[c.id] = (c, obs)
            self.run_ids[c.id] = run.session_id if pack is not None else None
        if run.exit_code == 0:
            self._save_recordings(path, text)
        self.log(f"kane: exit {run.exit_code}, {run.authored} authored, {run.replayed} replayed, {run.credits:.1f} credits")
        return run

    # Kane keeps one recording per test file. The loop alternates a file between the
    # doc's original and patched wording, so each passing version's output-<stem>/ is
    # kept under versions/ and put back when that exact text is written again.

    def _versions(self, path: Path, text: str) -> tuple[Path, Path]:
        stem = path.name[: -len("_test.md")]
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
        return path.parent / f"output-{stem}", self.home / "versions" / stem / digest

    def _save_recordings(self, path: Path, text: str) -> None:
        output, saved = self._versions(path, text)
        if output.is_dir():
            shutil.rmtree(saved, ignore_errors=True)
            shutil.copytree(output, saved)

    def _restore_recordings(self, path: Path, text: str) -> None:
        if path.exists() and path.read_text(encoding="utf-8") == text:
            return
        output, saved = self._versions(path, text)
        if saved.is_dir():
            shutil.rmtree(output, ignore_errors=True)
            shutil.copytree(saved, output)
            self.log(f"kane: restored recordings for this version of {path.name}")

    def _execute(self, cmd: list[str], timeout_s: float) -> KaneRun:
        self.home.mkdir(parents=True, exist_ok=True)
        stderr_path = self.home / "last-stderr.log"
        lines: list[str] = []
        with stderr_path.open("w", encoding="utf-8") as err:
            proc = subprocess.Popen(
                cmd,
                cwd=str(self.home),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=err,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            deadline = time.time() + timeout_s
            assert proc.stdout is not None
            for line in proc.stdout:
                lines.append(line)
                if time.time() > deadline:
                    proc.kill()
                    break
            try:
                code = proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                proc.kill()
                code = 3
        (self.home / "last-run.ndjson").write_text("".join(lines), encoding="utf-8")
        run = parse_events(lines)
        run.exit_code = 3 if code not in (0, 1, 2) else code
        run.stderr = stderr_path.read_text(encoding="utf-8", errors="replace")
        return run

    def close(self) -> None:
        return None

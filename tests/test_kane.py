"""KaneBrowserRunner against real captures from the event-day smoke test (tests/fixtures/kane/).

Everything here is offline: the NDJSON streams and packs were recorded from
kane-cli 0.9.0 and sanitized by make_fixtures.py.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from attest import kane
from attest.kane import (
    KaneBrowserRunner,
    KaneRun,
    KaneSetupError,
    action_part,
    find_pack,
    group_claims,
    observation_for,
    parse_events,
    read_pack,
    step_body,
    test_file_text as make_test_file,
    unpack,
)
from attest.models import Claim, Evidence, Span
from attest.verdict import judge

FIX = Path(__file__).parent / "fixtures" / "kane"


def lines(name: str) -> list[str]:
    return (FIX / f"{name}.ndjson").read_text(encoding="utf-8").splitlines()


def browser(cid: str, objective: str, expects: str | None, deps: list[str] | None = None) -> Claim:
    return Claim(
        id=cid,
        kind="browser",
        span=Span(start_line=1, end_line=1, text=objective),
        objective=objective,
        expects=expects,
        store_as=f"{cid}_label",
        depends_on=deps or [],
    )


def shell(cid: str) -> Claim:
    return Claim(id=cid, kind="shell", span=Span(start_line=1, end_line=1, text="x"), command="echo hi")


def unpacked(name: str, tmp_path: Path) -> dict:
    return read_pack(unpack(FIX / f"{name}.pack.zip", tmp_path / name))


def test_unpack_skips_zip_slip_and_sibling_prefix(tmp_path):
    pack = tmp_path / "crafted.zip"
    dest = tmp_path / "dest"
    with __import__("zipfile").ZipFile(pack, "w") as z:
        z.writestr("../x", "outside")
        z.writestr("../dest-evil/x", "sibling")
    unpack(pack, dest)
    assert not (tmp_path / "x").exists()
    assert not (tmp_path / "dest-evil" / "x").exists()


C07 = browser("c07", "Open http://127.0.0.1:5000/ in your browser and check that the page shows Hello, World!", "Hello, World!")
C08 = browser("c08", 'Check that the page shows the text "Goodbye, World!".', "Goodbye, World!", ["c07"])


# -- test file writer -----------------------------------------------------------------


def test_shell_claim_splits_groups_and_prose_does_not():
    p = Claim(id="c03", kind="prose", span=Span(start_line=1, end_line=1, text="p"))
    groups = group_claims([shell("c01"), C07, p, C08, shell("c09"), browser("c10", "Open x", "y")])
    assert [[c.id for c in g] for g in groups] == [["c07", "c08"], ["c10"]]


def test_long_runs_are_capped_per_file():
    many = [browser(f"c{n:02d}", "Open x", "y") for n in range(1, 18)]
    groups = group_claims(many, max_claims=12)
    assert [len(g) for g in groups] == [12, 5]


@pytest.mark.parametrize(
    "objective, action",
    [
        ("Open http://127.0.0.1:5000/ in your browser and check that the page shows Hello, World!", "Open http://127.0.0.1:5000/ in your browser"),
        ('Click the "count is 0" button once and check that its label changes to "count is 1".', 'Click the "count is 0" button once'),
        ('Open https://github.com and sign in, then check for the "Sign in" label.', "Open https://github.com and sign in"),
        ('Click "Geography" and confirm the page shows the link or tab labeled "Geography".', 'Click "Geography"'),
        ('Check that the page shows the text "Goodbye, World!".', ""),
    ],
)
def test_action_part_drops_the_check_clause(objective, action):
    assert action_part(objective) == action


def test_step_body_is_a_witness_not_a_judge():
    body = step_body(C08)
    # the phrasing that worked at 10:20: store what is there, then a check that cannot fail on the label
    assert body == (
        "Store the exact visible text of the heading, label, button or message on the page that is closest to "
        "\"Goodbye, World!\" as 'c08_label'. Verify the page has finished loading."
    )
    assert "check that" not in step_body(C07).lower()
    assert step_body(C07).startswith("Open http://127.0.0.1:5000/ in your browser. Store the exact visible text")


@pytest.mark.parametrize(
    "claim, expected",
    [
        (
            browser("c90", 'Open https://data.census.gov and check that the browser address bar shows "https://data.census.gov".', "https://data.census.gov"),
            "Open https://data.census.gov. Store the URL of the current page as 'c90_label'. Verify the page has finished loading.",
        ),
        (
            browser("c91", 'Click the link labeled "Advanced Search" and confirm the page shows the link labeled "Advanced Search".', "Advanced Search"),
            # stored before the click, so the witness is the link itself, not the page it opens
            'Store the exact visible text of the link or button on the page that is closest to "Advanced Search" as \'c91_label\'. Click the link labeled "Advanced Search". Verify the page has finished loading.',
        ),
        (
            # a menu item that only exists after the first click: read it last (make-public card, 13:55)
            browser("c94", 'Click your profile photo (top-right) and select "Your repositories" and check for the on-page label "Your repositories".', "Your repositories"),
            'Click your profile photo (top-right) and select "Your repositories". Store the exact visible text of the last menu item, link or button you clicked as \'c94_label\'. Verify the page has finished loading.',
        ),
        (
            browser("c93", 'In the left-hand sidebar click "Security & analysis" and check for the "Security & analysis" label.', "Security & analysis"),
            'Store the exact visible text of the link or button on the page that is closest to "Security & analysis" as \'c93_label\'. In the left-hand sidebar click "Security & analysis". Verify the page has finished loading.',
        ),
        (
            browser("c92", 'Click the "count is 0" button once and check that its label changes to "count is 1".', "count is 1"),
            'Click the "count is 0" button once. Store the exact visible text on the page that is closest to "count is 1" after the click as \'c92_label\'. Verify the page has finished loading.',
        ),
        (
            browser("c93", "Open http://127.0.0.1:5000/ and check the heading", "Hello, World!"),
            'Open http://127.0.0.1:5000/. Store the exact visible text of the heading, label, button or message on the page that is closest to "Hello, World!" as \'c93_label\'. Verify the page has finished loading.',
        ),
        (
            browser("c94", "Open http://localhost:5173/ and check the heading", "Vite + React"),
            'Open http://localhost:5173/. Store the exact visible text of the heading, label, button or message on the page that is closest to "Vite + React" as \'c94_label\'. Verify the page has finished loading.',
        ),
    ],
)
def test_step_body_stores_the_right_witness(claim, expected):
    assert step_body(claim) == expected


def test_test_file_has_frontmatter_and_one_step_per_claim():
    text = make_test_file([C07, C08], "http://127.0.0.1:5000", "flask c07-c08")
    front = yaml.safe_load(text.split("---")[1])
    assert front == {"mode": "testing", "max_steps": 15, "url": "http://127.0.0.1:5000"}
    assert [ln for ln in text.splitlines() if ln.startswith("## ")] == ["## c07", "## c08"]


def test_start_url_falls_back_to_the_first_url_a_claim_names():
    assert kane.start_url([C07], None) == "http://127.0.0.1:5000/"
    assert kane.start_url([C07], "http://localhost:5173/") == "http://localhost:5173/"
    assert kane.start_url([C08], None) is None


# -- NDJSON ---------------------------------------------------------------------------


def test_authored_run_keeps_exact_key_in_context_not_final_state():
    run = parse_events(lines("author_pass"))
    step = run.steps[1]
    assert step.status == "passed" and not step.replayed
    assert step.variables["c07_label"] == "Hello, World!"
    assert "c07_label" not in step.final_state  # Kane renamed it to greeting_text
    assert run.authored == 1 and run.replayed == 0 and run.credits > 10
    assert run.session_id and run.overall == "passed"


def test_replayed_run_has_no_final_state_and_costs_nothing():
    run = parse_events(lines("replay_pass"))
    step = run.steps[1]
    assert step.replayed and step.final_state == {}
    assert step.variables["c07_label"] == "Hello, World!"
    assert run.replayed == 1 and run.authored == 0 and run.credits == 0
    assert run.evidence_id


def test_untyped_progress_events_and_noise_are_ignored():
    run = parse_events(lines("run_pass") + ["not json", "", '{"step": 9, "status": "running"}'])
    assert run.steps == {}  # kane-cli run has no testmd steps


def test_failed_step_is_reported_per_testmd_step():
    run = parse_events(lines("fail_stuck"))
    assert run.steps[1].status == "passed" and run.steps[1].replayed
    assert run.steps[2].status == "failed" and run.steps[2].reason_code == "stuck.ap_stuck"
    assert run.overall == "failed"


# -- pack -----------------------------------------------------------------------------


def test_pack_steps_map_to_testmd_steps_by_run_prefix_not_ordinal(tmp_path):
    steps = unpacked("fail_stuck", tmp_path)
    assert sorted(steps) == [0, 1]
    assert [s.ordinal for s in steps[0]] == [1, 2, 3, 4]
    assert [s.ordinal for s in steps[1]] == [5, 6, 7, 8, 9, 10]
    # the failure record sits on 9-1-5, not on the last step: found through the root failure.yaml
    rec = next(s.failure for s in steps[1] if s.failure)
    assert rec["error"]["message"].startswith("AP determined agent is stuck")


def test_find_pack_matches_the_session(tmp_path):
    ev = tmp_path / ".testmuai" / "evidence"
    ev.mkdir(parents=True)
    for name in ("author_pass", "witness_stale", "fail_stuck"):
        shutil.copy(FIX / f"{name}.pack.zip", ev / f"{name}.evidence")
    sid = parse_events(lines("witness_stale")).session_id
    assert find_pack(ev, sid).name == "witness_stale.evidence"
    assert find_pack(tmp_path / "missing", sid) is None


# -- mapping into verdicts ------------------------------------------------------------


def _verdicts(name: str, claims: list[Claim], tmp_path: Path):
    run = parse_events(lines(name))
    steps = unpacked(name, tmp_path)
    out, prior = {}, {}
    for i, c in enumerate(claims, start=1):
        obs = observation_for(c, i, run, steps)
        v = judge(c, Evidence(claim_id=c.id, kind="browser", observation=obs), prior)
        prior[c.id] = out[c.id] = v
    return out


def test_witness_step_turns_a_label_mismatch_into_stale(tmp_path):
    c08 = browser("c08", 'Check that the page shows the text "Goodbye, World!".', "Goodbye, World!")
    v = _verdicts("witness_stale", [C07, c08], tmp_path)
    assert v["c07"].status == "proven"
    assert v["c08"].status == "stale"
    assert v["c08"].evidence.observed == {"c08_label": "Hello, World!"}
    assert Path(v["c08"].evidence.screenshot).is_file()
    assert "authored" in v["c08"].evidence.observation.note
    assert "replayed from cache" in v["c07"].evidence.observation.note


def test_failed_step_is_broken_with_failure_record_in_the_note(tmp_path):
    c08 = browser("c08", 'Check that the page shows the text "Goodbye, World!".', "Goodbye, World!")
    v = _verdicts("fail_stuck", [C07, c08], tmp_path)
    assert v["c08"].status == "broken"
    note = v["c08"].evidence.observation.note
    assert "AP determined agent is stuck" in note
    assert "root cause:" in note and "suggested fix:" in note
    assert Path(v["c08"].evidence.screenshot).is_file()


def test_drifted_page_replays_free_and_reports_the_new_text(tmp_path):
    c09 = browser("c09", "Open http://localhost:5000 and check the greeting", "Hello, World!")
    v = _verdicts("drift_replay", [c09], tmp_path)
    assert v["c09"].evidence.observed == {"c09_label": "Hello, Kane!"}
    assert v["c09"].status == "stale"


def test_step_that_never_ran_is_failed():
    obs = observation_for(C08, 2, KaneRun(), {})
    assert obs.status == "failed" and "did not run" in obs.note


# -- runner ---------------------------------------------------------------------------


class Recorder:
    """Stands in for kane-cli: returns a fixture run and drops its pack into the store."""

    def __init__(self, runner: KaneBrowserRunner, fixture: str, exit_code: int = 0):
        self.runner, self.fixture, self.exit_code, self.calls = runner, fixture, exit_code, []

    def __call__(self, cmd, timeout_s):
        self.calls.append(cmd)
        self.runner.evidence_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy(FIX / f"{self.fixture}.pack.zip", self.runner.evidence_dir / f"{self.fixture}.evidence")
        run = parse_events(lines(self.fixture))
        run.exit_code = self.exit_code
        return run


@pytest.fixture
def runner(tmp_path, monkeypatch):
    monkeypatch.setattr(kane.shutil, "which", lambda cmd: cmd)
    return KaneBrowserRunner(home=tmp_path / "kane", headless=True)


def test_runner_runs_a_group_once_and_maps_each_claim(runner):
    c08 = browser("c08", 'Check that the page shows the text "Goodbye, World!".', "Goodbye, World!", ["c07"])
    runner._execute = rec = Recorder(runner, "witness_stale")
    runner.prepare([shell("c06"), C07, c08], "demo/flask.md")
    first = runner.observe(C07, "http://127.0.0.1:5000/")
    second = runner.observe(c08, "http://127.0.0.1:5000/")
    assert len(rec.calls) == 1  # one test file for both claims
    cmd = rec.calls[0]
    assert cmd[1:4] == ["testmd", "run", str(runner.home / "tests" / "flask-c07_test.md")]
    assert "--agent" in cmd and "--headless" in cmd
    assert first.status == "passed" and second.observed == {"c08_label": "Hello, World!"}
    written = (runner.home / "tests" / "flask-c07_test.md").read_text(encoding="utf-8")
    assert "url: http://127.0.0.1:5000/" in written and "## c08" in written


def test_patched_claim_reruns_the_same_file(runner):
    runner._execute = rec = Recorder(runner, "witness_stale")
    runner.prepare([C07, C08], "demo/flask.md")
    runner.observe(C07, None)
    patched = C08.model_copy(update={"expects": "Hello, World!", "objective": 'Check that the page shows the text "Hello, World!".'})
    runner.observe(patched, None)
    assert len(rec.calls) == 2
    assert rec.calls[0][3] == rec.calls[1][3]  # same path, so Kane replays c07 from cache
    text = Path(rec.calls[1][3]).read_text(encoding="utf-8")
    assert 'closest to "Hello, World!" as \'c08_label\'' in text and "Goodbye" not in text


def test_setup_error_raises_instead_of_badging(runner):
    runner._execute = Recorder(runner, "author_pass", exit_code=2)
    runner.prepare([C07], "demo/flask.md")
    with pytest.raises(KaneSetupError):
        runner.observe(C07, None)


def test_missing_kane_cli_is_a_setup_error(tmp_path, monkeypatch):
    monkeypatch.setattr(kane.shutil, "which", lambda cmd: None)
    with pytest.raises(KaneSetupError):
        KaneBrowserRunner(home=tmp_path)


def test_recordings_follow_the_file_version_back_and_forth(runner):
    """Original wording, patched wording, original again: the third run gets the first run's recordings back."""

    class Recording(Recorder):
        def __call__(self, cmd, timeout_s):
            out = Path(cmd[3]).parent / "output-flask-c07"
            # what Kane would replay from: a recording of the text it just ran
            self.seen = (out / "rec.txt").read_text() if (out / "rec.txt").exists() else None
            out.mkdir(parents=True, exist_ok=True)
            (out / "rec.txt").write_text(Path(cmd[3]).read_text(encoding="utf-8"))
            return super().__call__(cmd, timeout_s)

    runner._execute = rec = Recording(runner, "witness_stale")
    runner.prepare([C07, C08], "demo/flask.md")
    path = runner.test_path([C07, C08])
    runner.observe(C07, None)
    original = path.read_text(encoding="utf-8")
    patched = C08.model_copy(update={"expects": "Hello, World!"})
    runner.observe(patched, None)
    assert rec.seen == original  # Kane found the other version's recording (it would re-author)
    runner.observe(C08, None)
    assert rec.seen == original  # restored: the original's recording is back for the original text


def test_local_urls_follow_the_server_that_started():
    assert kane.retarget("Open http://localhost:5173/ in your browser", "http://localhost:5174/") == "Open http://localhost:5174/ in your browser"
    assert kane.retarget("Open http://127.0.0.1:5000 now", "http://127.0.0.1:5000/") == "Open http://127.0.0.1:5000 now"
    assert kane.retarget("Open https://github.com", "http://localhost:5174/") == "Open https://github.com"
    assert kane.retarget("Open http://localhost:5173/", None) == "Open http://localhost:5173/"
    assert "http://localhost:5174/" in step_body(browser("c07", "Open http://localhost:5173/ and check the heading", "Vite + React"), "http://localhost:5174/")


def test_report_wraps_kane_in_kanehack_report(runner):
    runner.report = True
    runner._execute = rec = Recorder(runner, "author_pass")
    runner.prepare([C07], "demo/flask.md")
    runner.observe(C07, None)
    cmd = rec.calls[0]
    assert cmd[:2] == ["kanehack", "report"] and "--objective" in cmd
    sep = cmd.index("--")
    assert cmd[sep + 1 : sep + 4] == ["kane-cli", "testmd", "run"]  # Kane's own command, untouched


@pytest.mark.parametrize("report", [False, True])
def test_cdp_endpoint_is_part_of_kane_command_with_or_without_report(runner, monkeypatch, report):
    runner.report = report
    runner.cdp_endpoint = "http://127.0.0.1:9333"
    if report:
        monkeypatch.setattr(kane.subprocess, "run", lambda *args, **kwargs: type("Result", (), {"stdout": "abc123\n"})())
    runner._execute = rec = Recorder(runner, "author_pass")
    runner.prepare([C07], "demo/flask.md")
    runner.observe(C07, None)
    cmd = rec.calls[0]
    kane_cmd = cmd[cmd.index("--") + 1:] if report else cmd
    endpoint_index = kane_cmd.index("--cdp-endpoint")
    assert kane_cmd[endpoint_index + 1] == "http://127.0.0.1:9333"


def test_browser_error_page_is_unreachable_not_stale():
    # sfmta.org on the venue network: Kane passed the step but stored Chrome's error page
    run = KaneRun(steps={1: kane.StepRun(index=1, heading="c03", status="passed", variables={"c03_label": "This site can’t be reached"})})
    c03 = browser("c03", "Open https://www.sfmta.org and check for the button", "Pay or Contest Parking Tickets")
    obs = observation_for(c03, 1, run, {})
    assert obs.status == "unreachable"
    v = judge(c03, Evidence(claim_id="c03", kind="browser", observation=obs), {})
    assert v.status == "unverifiable" and "not reachable" in v.reason


def test_typed_text_is_not_a_control_label():
    # Acme CRM spec: "Globex renewal" is typed, "Add deal" is clicked; the table row is the outcome
    c = browser("c04", 'Type "Globex renewal" into the "Deal name" box, type "12000" into the "Amount" box and click "Add deal", then check that the table shows "Globex renewal".', "Globex renewal")
    body = step_body(c)
    assert body.startswith('Type "Globex renewal"') and 'closest to "Globex renewal" after the click' in body
    won = browser("c05", 'On the "Globex renewal" row, click the "Mark as won" button and check that the status reads "Won".', "Won")
    assert 'closest to "Won" after the click' in step_body(won)

"""Kane runner red-team cases (FACTORY §1), offline only.

Inputs and expected invariants live in cases/kane_runner/<ID>.json. Nothing here
starts kane-cli or a browser; cases that need a live Kane run are stubs.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import sys
import time
import zipfile
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from attest import pipeline
from attest.contracts import AuthorityState, ComparisonState, EvidenceState, ExecutionState, RepairState
from attest.execution.sandbox import extract_zip_safely, safe_pack_path
from attest.execution.supervisor import ProcessSupervisor
from attest.kane import (
    FILE_MAX_CLAIMS,
    KaneBrowserRunner,
    KaneRun,
    StepRun,
    find_pack,
    group_claims,
    observation_for,
    parse_events,
    read_pack,
    unpack,
)
from attest.kane import test_file_text as make_test_file
from attest.models import BrowserObservation, Claim, Evidence, Span
from attest.receipts import ReceiptEvidence, RunReceipt
from attest.runner import FakeBrowserRunner
from attest.snapshots import SourceVersion
from attest.verdict import Rules, judge

from ._harness import case

REPO = Path(__file__).resolve().parents[2]
KANE_FIX = REPO / "tests" / "fixtures" / "kane"


def _browser_claim(cid: str, expects: str) -> Claim:
    return Claim(
        id=cid,
        kind="browser",
        span=Span(start_line=1, end_line=1, text=f'The page shows "{expects}".'),
        objective=f'Open the page and check for "{expects}".',
        expects=expects,
        store_as=f"{cid}_label",
    )


def _one_step_run(status: str, variables: dict[str, str] | None = None, summary: str = "") -> KaneRun:
    step = StepRun(index=1, heading="c01", status=status, variables=variables or {}, reason="authored", summary=summary)
    return KaneRun(steps={1: step})


def _verdict(claim: Claim, observation: BrowserObservation):
    return judge(claim, Evidence(claim_id=claim.id, kind="browser", observation=observation))


def _bare_runner(home: Path) -> KaneBrowserRunner:
    # Skip __init__: it requires kane-cli on PATH, and _execute is all these cases drive.
    runner = KaneBrowserRunner.__new__(KaneBrowserRunner)
    runner.home = home
    return runner


# -- PI: prompt injection ------------------------------------------------------------


@case("KR-PI-01")
def test_injected_stored_page_text_is_compared_not_obeyed(rt_case):
    i, x = rt_case["input"], rt_case["expected"]
    claim = _browser_claim("c01", i["expects"])
    observation = observation_for(claim, 1, _one_step_run("passed", {"c01_label": i["stored"]}), {})
    assert observation.status == x["observation_status"]
    assert _verdict(claim, observation).status == x["verdict"]


@case("KR-PI-02")
def test_success_summary_does_not_override_failed_step(rt_case):
    i, x = rt_case["input"], rt_case["expected"]
    claim = _browser_claim("c01", i["expects"])
    run = _one_step_run(i["step_status"], {"c01_label": i["expects"]}, summary=i["summary"])
    observation = observation_for(claim, 1, run, {})
    assert observation.status == x["observation_status"]
    assert _verdict(claim, observation).status == x["verdict"]


@case("KR-PI-03")
def test_live_page_content_does_not_add_kane_actions(rt_case):
    raise AssertionError("stub")


@case("KR-PI-04")
def test_doc_objective_cannot_add_a_kane_step(rt_case):
    i, x = rt_case["input"], rt_case["expected"]
    claim = Claim(id=i["claim"], kind="browser", span=Span(start_line=1, end_line=1, text="The dashboard loads."),
                  objective=i["objective"], expects=i["expects"])
    text = make_test_file([claim], "http://127.0.0.1:5000/", "t", max_steps=i["max_steps"])
    assert yaml.safe_load(text.split("---")[1])["max_steps"] == x["front_matter_max_steps"]
    assert [ln for ln in text.splitlines() if ln.startswith("## ")] == x["headings"]


@case("KR-PI-05")
def test_pack_failure_triage_text_is_note_not_verdict(rt_case, tmp_path):
    i, x = rt_case["input"], rt_case["expected"]
    step_dir = tmp_path / "pack" / "tests" / "t" / "steps" / "1-0-1"
    step_dir.mkdir(parents=True)
    (step_dir / "step.json").write_text(json.dumps(i["pack_step_meta"]), encoding="utf-8")
    (step_dir / "failure.yaml").write_text(yaml.safe_dump(i["failure"]), encoding="utf-8")
    claim = _browser_claim("c01", i["expects"])
    observation = observation_for(claim, 1, _one_step_run(i["step_status"]), read_pack(tmp_path / "pack"))
    assert observation.status == x["observation_status"]
    assert observation.observed == x["observed"]
    assert i["failure"]["triage"]["rca"]["root_cause"] in observation.note  # kept for people, not obeyed
    assert _verdict(claim, observation).status == x["verdict"]


# -- AB: authority bypass ------------------------------------------------------------


@case("KR-AB-01")
def test_runner_output_cannot_carry_a_verdict(rt_case):
    i = rt_case["input"]
    for status in i["statuses"]:
        with pytest.raises(ValidationError):
            BrowserObservation(status=status)
    with pytest.raises(ValidationError):
        BrowserObservation.model_validate({"status": "passed", **i["extra_field"]})


# -- DS: identity confusion ----------------------------------------------------------


@case("KR-DS-01")
def test_label_stored_under_another_claims_key_is_not_evidence(rt_case):
    i, x = rt_case["input"], rt_case["expected"]
    claim = _browser_claim(i["claim"], i["stored"])
    observation = observation_for(claim, 1, _one_step_run("passed", {i["stored_key"]: i["stored"]}), {})
    assert observation.observed == x["observed"]
    assert _verdict(claim, observation).status == x["verdict"]


@case("KR-DS-02")
def test_receipt_rejects_evidence_from_another_version_or_content(rt_case):
    i, x = rt_case["input"], rt_case["expected"]
    version = SourceVersion.from_content("guide.md", i["content"])
    other = SourceVersion.from_content("guide.md", i["other_content"])

    def receipt(evidence: ReceiptEvidence) -> RunReceipt:
        return RunReceipt(
            snapshot_id=version.version_id, source_version_id=version.version_id, content_sha256=version.content_sha256,
            execution_state=ExecutionState.SUCCEEDED, comparison_state=ComparisonState.MATCH,
            authority_state=AuthorityState.ALLOWED, evidence_state=EvidenceState.COMPLETE,
            repair_state=RepairState.NOT_REQUIRED, evidence=(evidence,),
        )

    with pytest.raises(ValidationError, match=x["version_error"]):
        receipt(ReceiptEvidence(kind="browser", source_version_id=other.version_id,
                                content_sha256=version.content_sha256, complete=True))
    with pytest.raises(ValidationError, match=x["content_error"]):
        receipt(ReceiptEvidence(kind="browser", source_version_id=version.version_id,
                                content_sha256=other.content_sha256, complete=True))


@case("KR-DS-03")
def test_unknown_session_finds_no_evidence_pack(rt_case, tmp_path):
    i, x = rt_case["input"], rt_case["expected"]
    evidence_dir = tmp_path / ".testmuai" / "evidence"
    evidence_dir.mkdir(parents=True)
    shutil.copy(KANE_FIX / f"{i['pack_fixture']}.pack.zip", evidence_dir / f"{i['pack_fixture']}.evidence")
    assert find_pack(evidence_dir, i["session_id"]) is x["pack"]


# -- SIM: fake evidence --------------------------------------------------------------


@case("KR-SIM-01")
def test_scripted_fake_observation_cannot_prove(rt_case):
    i, x = rt_case["input"], rt_case["expected"]
    claim = _browser_claim("c01", i["expects"])
    scripted = BrowserObservation(status="passed", observed={"c01_label": i["expects"]}, note="kane: step passed, authored")
    observation = FakeBrowserRunner({"c01": scripted}).observe(claim, "http://127.0.0.1:5000/")
    assert _verdict(claim, observation).status == x["verdict"]


@case("KR-SIM-02")
def test_simulation_label_survives_revalidation(rt_case):
    i, x = rt_case["input"], rt_case["expected"]
    claim = _browser_claim("c01", i["expects"])
    simulated = FakeBrowserRunner().observe(claim, "http://127.0.0.1:5000/")
    laundered = BrowserObservation.model_validate(simulated.model_dump())
    assert type(laundered) is BrowserObservation
    assert _verdict(claim, laundered).status == x["verdict"]


@case("KR-SIM-03")
def test_cached_replay_is_bound_to_app_version(rt_case):
    raise AssertionError("stub")


@case("KR-SIM-04")
def test_stored_browser_error_page_is_not_page_evidence(rt_case):
    i, x = rt_case["input"], rt_case["expected"]
    claim = _browser_claim("c01", i["expects"])
    for stored in i["stored"]:
        observation = observation_for(claim, 1, _one_step_run("passed", {"c01_label": stored}), {})
        assert observation.status == x["observation_status"], stored
        assert _verdict(claim, observation).status == x["verdict"], stored


# -- SCH: malformed or hostile schema ------------------------------------------------


@case("KR-SCH-01")
def test_malformed_ndjson_never_yields_a_passed_step(rt_case):
    x = rt_case["expected"]
    claim = _browser_claim("c01", "Dashboard")
    observation = observation_for(claim, 1, parse_events(rt_case["input"]["lines"]), {})
    assert observation.status == x["observation_status"]
    assert _verdict(claim, observation).status == x["verdict"]


@case("KR-SCH-02")
def test_escaping_pack_member_names_are_rejected(rt_case, tmp_path):
    x = rt_case["expected"]
    for n, member in enumerate(rt_case["input"]["members"]):
        with pytest.raises(ValueError, match=x["error"]):
            safe_pack_path(tmp_path / "root", member)
        pack = tmp_path / f"pack-{n}.zip"
        with zipfile.ZipFile(pack, "w") as z:
            z.writestr(member, "x")
        with pytest.raises(ValueError, match=x["error"]):
            extract_zip_safely(pack, tmp_path / f"dest-{n}")
    assert not (tmp_path / "escape.txt").exists()


# -- COST: cost or time blowup -------------------------------------------------------


@case("KR-COST-01")
def test_silent_kane_process_is_stopped_at_its_deadline(rt_case, tmp_path):
    i, x = rt_case["input"], rt_case["expected"]
    started = time.monotonic()
    run = _bare_runner(tmp_path)._execute([sys.executable, "-c", f"import time; time.sleep({i['sleep_s']})"],
                                         timeout_s=i["timeout_s"])
    assert time.monotonic() - started < x["max_elapsed_s"]
    assert run.exit_code == x["exit_code"]


@case("KR-COST-02")
def test_oversized_evidence_pack_is_refused_before_extraction(rt_case, tmp_path):
    i, x = rt_case["input"], rt_case["expected"]
    pack = tmp_path / "big.zip"
    with zipfile.ZipFile(pack, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("tests/t/steps/1-0-1/step.json", "0" * i["member_bytes"])
    dest = tmp_path / "dest"
    with pytest.raises(ValueError, match=x["error"]):
        extract_zip_safely(pack, dest, max_uncompressed_bytes=i["cap_bytes"])
    assert len([p for p in dest.rglob("*") if p.is_file()]) == x["extracted_files"]


@case("KR-COST-03")
def test_kane_test_file_always_has_a_step_cap(rt_case):
    i, x = rt_case["input"], rt_case["expected"]
    text = make_test_file([_browser_claim("c01", "Dashboard")], "http://127.0.0.1:5000/", "t", max_steps=i["max_steps"])
    front = yaml.safe_load(text.split("---")[1])
    assert front["max_steps"] == x["front_matter_max_steps"]
    assert Rules().kane_max_steps == x["default_rules_max_steps"]


@pytest.mark.skipif(os.name == "nt", reason="process-group probe is POSIX only")
@case("KR-COST-04")
def test_supervisor_kills_blocked_child_and_grandchild(rt_case, tmp_path):
    i, x = rt_case["input"], rt_case["expected"]
    pid_file = tmp_path / "grandchild.pid"
    child = (
        "import subprocess, sys, time\n"
        f"g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        f"open({str(pid_file)!r}, 'w').write(str(g.pid))\n"
        "time.sleep(60)\n"
    )
    started = time.monotonic()
    result = ProcessSupervisor(timeout_s=i["timeout_s"], terminate_grace_s=0.1).run([sys.executable, "-c", child])
    assert result.timed_out is x["timed_out"]
    assert time.monotonic() - started < x["max_elapsed_s"]

    grandchild = int(pid_file.read_text())
    deadline = time.monotonic() + 2
    while _alive(grandchild) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _alive(grandchild)


@case("KR-COST-05")
def test_claim_flood_is_split_under_the_step_cap(rt_case):
    i, x = rt_case["input"], rt_case["expected"]
    claims = [_browser_claim(f"c{n:03d}", "Dashboard") for n in range(1, i["claims"] + 1)]
    groups = group_claims(claims)
    assert len(groups) == x["files"]
    assert FILE_MAX_CLAIMS == x["max_headings_per_file"] < i["max_steps"]
    for group in groups:
        text = make_test_file(group, "http://127.0.0.1:5000/", "t", max_steps=i["max_steps"])
        headings = [ln for ln in text.splitlines() if ln.startswith("## ")]
        assert len(headings) <= x["max_headings_per_file"]
    assert sum(len(g) for g in groups) == x["claims_covered"]  # nothing silently dropped


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    status = Path(f"/proc/{pid}/status")
    # An orphan the container's init has not reaped yet is dead for our purposes.
    return not (status.exists() and re.search(r"^State:\s+Z", status.read_text(), re.M))


# -- ISO: isolation and cleanup ------------------------------------------------------


@case("KR-ISO-01")
def test_symlink_and_zip_slip_members_never_land_outside(rt_case, tmp_path):
    x = rt_case["expected"]
    link_pack = tmp_path / "link.zip"
    with zipfile.ZipFile(link_pack, "w") as z:
        info = zipfile.ZipInfo("tests/t/steps/1-0-1/screenshot.png")
        info.external_attr = (0o120777 << 16)
        z.writestr(info, "../../../../outside.txt")
    with pytest.raises(ValueError, match=x["symlink_error"]):
        extract_zip_safely(link_pack, tmp_path / "dest-link")

    slip_pack = tmp_path / "slip.zip"
    with zipfile.ZipFile(slip_pack, "w") as z:
        z.writestr("ok.txt", "fine")
        z.writestr("../outside.txt", "outside")
    with pytest.raises(ValueError):
        extract_zip_safely(slip_pack, tmp_path / "dest-slip")
    assert (not (tmp_path / "outside.txt").exists()) is x["outside_absent"]
    assert not (tmp_path / "dest-slip" / "ok.txt").exists()  # validated before any write


@case("KR-ISO-02")
def test_no_code_kills_a_port_owner(rt_case):
    i, x = rt_case["input"], rt_case["expected"]
    pattern = re.compile("|".join(f"(?:{p})" for p in i["patterns"]))
    matches = []
    for root in i["roots"]:
        for path in sorted((REPO / root).rglob("*")):
            if path.suffix in {".py", ".sh", ".ps1", ".cmd", ".bat"} and "__pycache__" not in path.parts:
                for n, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                    if pattern.search(line):
                        matches.append(f"{path.relative_to(REPO)}:{n}")
    assert matches == x["matches"]


@case("KR-ISO-03")
def test_shell_session_closes_when_browser_runner_crashes(rt_case, tmp_path, monkeypatch):
    x = rt_case["expected"]
    closed = []
    real_close = pipeline.ShellSession.close
    monkeypatch.setattr(pipeline.ShellSession, "close", lambda self: (closed.append(1), real_close(self))[1])

    class CrashingRunner:
        name = "kane"

        def observe(self, claim, base_url):
            raise RuntimeError("runner crashed mid-run")

        def close(self):
            pass

    with pytest.raises(RuntimeError, match="crashed"):
        pipeline.execute([_browser_claim("c01", "Dashboard")], CrashingRunner(), Rules(), tmp_path / "scratch")
    assert len(closed) == x["closed"]


@case("KR-ISO-05")
def test_runner_pack_extraction_stays_in_the_pack_dir(rt_case, tmp_path):
    i, x = rt_case["input"], rt_case["expected"]
    pack = tmp_path / "hostile.zip"
    with zipfile.ZipFile(pack, "w") as z:
        z.writestr(i["inside"], "{}")
        for name in i["escaping"]:
            z.writestr(name, "outside")
        link = zipfile.ZipInfo(i["symlink"]["name"])
        link.external_attr = 0o120777 << 16
        z.writestr(link, i["symlink"]["target"])
        z.writestr(i["har"], "cookie: session=1")
    dest = tmp_path / "home" / "packs" / "hostile"
    unpack(pack, dest)
    assert (dest / i["inside"]).is_file()
    outside = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*")
                     if p.is_file() and p != pack and not p.is_relative_to(dest))
    assert outside == x["outside_files"]
    assert len([p for p in dest.rglob("*") if p.is_symlink()]) == x["symlinks"]
    assert (dest / i["har"]).exists() is x["har_extracted"]


@pytest.mark.skipif(os.name == "nt", reason="process probe is POSIX only")
@case("KR-ISO-06")
def test_deadline_kill_reaches_kane_child_processes(rt_case, tmp_path):
    i, x = rt_case["input"], rt_case["expected"]
    pid_file = tmp_path / "grandchild.pid"
    # Stands in for kane-cli: starts a long-lived child (the browser) and keeps printing,
    # so _execute's per-line deadline check does fire.
    child = (
        "import subprocess, sys, time\n"
        "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'], stdout=subprocess.DEVNULL)\n"
        f"open({str(pid_file)!r}, 'w').write(str(g.pid))\n"
        "while True:\n"
        f"    print('{{}}', flush=True); time.sleep({i['tick_s']})\n"
    )
    run = _bare_runner(tmp_path / "home")._execute([sys.executable, "-c", child], timeout_s=i["timeout_s"])
    grandchild = int(pid_file.read_text())
    try:
        assert run.exit_code == x["exit_code"]
        deadline = time.monotonic() + 2
        while _alive(grandchild) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert _alive(grandchild) is x["grandchild_alive"]
    finally:
        if _alive(grandchild):
            os.kill(grandchild, signal.SIGKILL)


@case("KR-ISO-04")
def test_kane_run_stays_inside_disposable_environment(rt_case):
    raise AssertionError("stub")

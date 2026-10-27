"""Kane runner red-team cases (FACTORY §1) on the recorded event-day captures, offline only.

Inputs and expected invariants live in cases/kane_runner/<ID>.json. Each case reads a
recorded pack or stream from tests/fixtures/kane/, rewritten in memory the way a test
file edited between Attest's write and Kane's read, or a hostile pack, would leave it.
``tests.kane_replay.as_written`` reseals a recorded pack for the file Attest writes, so
the untouched pack backs the claim and only the rewrite under test can change that.
Nothing here starts kane-cli or a browser.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import yaml

from attest.kane import observation_for, pack_disagreement, parse_events, read_pack
from attest.kane import test_file_text as make_test_file
from attest.models import Claim, Evidence, Span
from attest.verdict import judge
from tests.kane_replay import as_written, rewrite_pack

from ._harness import case

REPO = Path(__file__).resolve().parents[2]
KANE_FIX = REPO / "tests" / "fixtures" / "kane"


def _browser_claim(cid: str, expects: str, objective: str | None = None) -> Claim:
    return Claim(
        id=cid,
        kind="browser",
        span=Span(start_line=1, end_line=1, text=f'The page shows "{expects}".'),
        objective=objective or f'Open the page and check for "{expects}".',
        expects=expects,
        store_as=f"{cid}_label",
    )


def _capture(name: str) -> Path:
    return KANE_FIX / f"{name}.pack.zip"


def _session(name: str) -> str:
    with zipfile.ZipFile(_capture(name)) as z:
        result = next(n for n in z.namelist() if n.endswith("/result.yaml"))
        return str(yaml.safe_load(z.read(result))["external_id"]["session_id"])


def _sealed(tmp_path: Path, i: dict, claims: list[Claim], url: str, name: str = "sealed.zip") -> bytes:
    """The recorded pack resealed for the test file Attest writes for ``claims`` at ``url``."""
    written = make_test_file(claims, url, "t").encode("utf-8")
    return as_written(_capture(i["capture"]), tmp_path / name, written).read_bytes()


# -- PI: prompt injection ------------------------------------------------------------


@case("KR-PI-06")
def test_page_text_echoed_into_stream_events_does_not_pass_the_step(rt_case):
    i, x = rt_case["input"], rt_case["expected"]
    recorded = (KANE_FIX / f"{i['capture']}.ndjson").read_text(encoding="utf-8").splitlines()
    at = next(n for n, line in enumerate(recorded) if line.startswith(i["insert_before"]))
    echoed = [*recorded[:at], *i["echoed"], *recorded[at:]]
    claim = _browser_claim(i["claim"], i["expects"])

    before = observation_for(claim, i["index"], parse_events(recorded), {})
    observation = observation_for(claim, i["index"], parse_events(echoed), {})
    assert observation.status == x["observation_status"]
    assert (observation.observed == before.observed) is x["observed_unchanged"]
    assert judge(claim, Evidence(claim_id=claim.id, kind="browser", observation=observation)).status != x["verdict_not"]


# -- AB: authority bypass ------------------------------------------------------------


@case("KR-AB-02")
def test_rewritten_overall_status_does_not_pass_a_failed_step(rt_case, tmp_path):
    i, x = rt_case["input"], rt_case["expected"]
    stream = (KANE_FIX / f"{i['capture']}.ndjson").read_text(encoding="utf-8")
    run = parse_events(stream.replace(i["ndjson_overall"]["from"], i["ndjson_overall"]["to"]).splitlines())
    assert run.overall == x["overall_after_rewrite"]

    claim = _browser_claim(i["claim"], i["expects"])
    observation = observation_for(claim, i["index"], run, {})
    assert observation.status == x["observation_status"]
    assert judge(claim, Evidence(claim_id=claim.id, kind="browser", observation=observation)).status != x["verdict_not"]

    def result(data: bytes) -> bytes:
        assert i["result_status"]["from"].encode() in data
        return data.replace(i["result_status"]["from"].encode(), i["result_status"]["to"].encode())

    pack = rewrite_pack(_capture(i["capture"]), tmp_path / "passed.zip", result=result).read_bytes()
    assert pack_disagreement(pack, i["claim"], _session(i["capture"])) == x["pack_detail"]


@case("KR-AB-03")
def test_step_attest_never_wrote_does_not_back_the_claim(rt_case, tmp_path):
    i, x = rt_case["input"], rt_case["expected"]
    claim = _browser_claim(i["claim"], i["expects"])
    session = _session(i["capture"])
    written = _sealed(tmp_path, i, [claim], i["start_url"])
    assert (pack_disagreement(written, claim.id, session, claim) is None) is x["written_pack_backs"]

    extra = _browser_claim(i["extra_step"]["id"], i["extra_step"]["expects"], i["extra_step"]["objective"])
    # The claim's step moves to index 1, so the recorded actions move with it.
    renumber = {"result": lambda data: data.replace(b"id: 0-", b"id: 1-")}
    edited = make_test_file([extra, claim], i["start_url"], "t").encode("utf-8")
    pack = rewrite_pack(_capture(i["capture"]), tmp_path / "extra.zip", test_md=lambda _: edited, redigest=True, **renumber)
    assert (pack_disagreement(pack.read_bytes(), claim.id, session, claim) is None) is x["extra_step_pack_backs"]


# -- DS: identity confusion ----------------------------------------------------------


@case("KR-DS-04")
def test_pack_from_another_origin_does_not_back_the_claim(rt_case, tmp_path):
    i, x = rt_case["input"], rt_case["expected"]
    claim = _browser_claim(i["claim"], i["expects"])
    session = _session(i["capture"])
    written = _sealed(tmp_path, i, [claim], i["written_url"], "written.zip")
    assert (pack_disagreement(written, claim.id, session, claim) is None) is x["written_pack_backs"]
    other = _sealed(tmp_path, i, [claim], i["sealed_url"], "other.zip")
    assert (pack_disagreement(other, claim.id, session, claim) is None) is x["other_origin_pack_backs"]


@case("KR-DS-05")
def test_duplicated_claim_heading_backs_neither_step(rt_case, tmp_path):
    i, x = rt_case["input"], rt_case["expected"]
    claim = _browser_claim(i["claim"], i["expects"])
    session = _session(i["capture"])
    written = make_test_file([claim], i["start_url"], "t")
    assert (pack_disagreement(_sealed(tmp_path, i, [claim], i["start_url"]), claim.id, session, claim) is None) is x["written_pack_backs"]

    heading = f"## {claim.id}\n"
    twice = (written + "\n" + heading + written.split(heading, 1)[1]).encode("utf-8")
    pack = as_written(_capture(i["capture"]), tmp_path / "twice.zip", twice).read_bytes()
    assert pack_disagreement(pack, claim.id, session, claim) == x["detail"]


@case("KR-DS-06")
def test_test_file_swapped_without_its_digest_is_not_what_kane_ran(rt_case, tmp_path):
    i, x = rt_case["input"], rt_case["expected"]
    claim = _browser_claim(i["claim"], i["expects"])
    written = make_test_file([claim], i["start_url"], "t").encode("utf-8")
    # The recorded definition digest is left as Kane sealed it.
    pack = rewrite_pack(_capture(i["capture"]), tmp_path / "swapped.zip", test_md=lambda _: written).read_bytes()
    assert pack_disagreement(pack, claim.id, _session(i["capture"]), claim) == x["detail"]


# -- SCH: malformed or hostile schema ------------------------------------------------


@case("KR-SCH-03")
def test_hostile_result_yaml_never_backs_the_claim(rt_case, tmp_path):
    i, x = rt_case["input"], rt_case["expected"]
    claim = _browser_claim(i["claim"], i["expects"])
    session = _session(i["capture"])
    sealed = tmp_path / "sealed.zip"
    sealed.write_bytes(_sealed(tmp_path, i, [claim], i["start_url"], "base.zip"))
    assert (pack_disagreement(sealed.read_bytes(), claim.id, session, claim) is None) is x["written_pack_backs"]

    for name, (old, new) in i["rewrites"].items():
        def result(data: bytes, old=old.encode(), new=new.encode()) -> bytes:
            assert old in data, name
            return data.replace(old, new, 1)

        pack = rewrite_pack(sealed, tmp_path / f"{name}.zip", result=result).read_bytes()
        assert pack_disagreement(pack, claim.id, session, claim) == x["details"][name], name


@case("KR-SCH-04")
def test_hostile_step_files_never_prove_the_claim(rt_case, tmp_path):
    i, x = rt_case["input"], rt_case["expected"]
    claim = _browser_claim("c01", i["expects"])
    run = parse_events([
        '{"type": "test_md_step_start", "step_index": 1, "heading": "c01"}',
        '{"type": "run_end", "context": {"variables": {"c01_label": {"value": "%s"}}}}' % i["expects"],
        '{"type": "test_md_step_end", "step_index": 1, "status": "%s"}' % i["step_status"],
    ])
    for name, files in i["shapes"].items():
        step_dir = tmp_path / name / "tests" / "t" / "steps" / "1-0-1"
        step_dir.mkdir(parents=True)
        for filename, text in files.items():
            (step_dir / filename).write_text(text, encoding="utf-8")
        try:
            observation = observation_for(claim, 1, run, read_pack(tmp_path / name))
            outcome = judge(claim, Evidence(claim_id=claim.id, kind="browser", observation=observation)).status
        except Exception:  # a pack that can't be read is an error, which fails closed
            outcome = "error"
        assert outcome != x["never"] and outcome in x["outcomes"], (name, outcome)
        if name in x["well_formed_outcome"]:
            assert outcome == x["well_formed_outcome"][name], name

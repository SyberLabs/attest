"""SyberWork bridge red-team cases (FACTORY §0, §1), Attest side only.

SyberWork is not in this repository, so nothing here exercises SyberWork code.
These cases check the Attest half of the seam: the attest.ticket/v0 record
Attest sends a worker, the ``attest.worker`` command a SyberWork dispatch routes
tickets to, the worker's diff coming back, and the receipt and handoff types
SyberWork reads. Inputs and expected invariants live in
cases/syberwork_bridge/<ID>.json. No network or model call is made.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import openai
import pytest
from pydantic import BaseModel, ValidationError

from attest import contracts, worker
from attest.implement import Candidate, _openai_worker, app_diff_paths, build_ticket
from attest.loop import _receipt
from attest.models import BrowserObservation, Claim, Doc, Evidence, Span, Verdict
from attest.policy import AuthorityDecision
from attest.receipts import ReceiptEvidence, RunReceipt, VerifiedRunReceipt
from attest.reconcile_spec import ReconcileHandoff
from attest.snapshots import SourceVersion
from attest.verdict import judge

from ._harness import case

SPEC = "spec.md"


def _spec_doc(span: str) -> Doc:
    return Doc(source=SPEC, sha256="0" * 64, lines=[span])


def _browser_claim(span: str) -> Claim:
    return Claim(id="c01", kind="browser", span=Span(start_line=1, end_line=1, text=span),
                 objective="Open the dashboard", expects="Dashboard", store_as="c01_label")


def _candidate(tmp_path: Path) -> Candidate:
    root = tmp_path / "candidate" / "app"
    root.mkdir(parents=True)
    (root / "app.js").write_text("let total = 0;\n", encoding="utf-8")
    return Candidate(tmp_path / "app", root)


def _keys(value) -> set[str]:
    """Every mapping key at any depth."""
    if isinstance(value, dict):
        return set(value) | {k for v in value.values() for k in _keys(v)}
    if isinstance(value, list):
        return {k for v in value for k in _keys(v)}
    return set()


def _diff(path: str) -> str:
    return f"--- a/{path}\n+++ b/{path}\n@@ -0,0 +1 @@\n+changed\n"


def _ticket(span: str = 'The dashboard shows "Dashboard".') -> dict:
    return {
        "schema": "attest.ticket/v0",
        "case_id": "000000000000-c01-1",
        "title": "Missing capability: dashboard",
        "spec": {"source": SPEC, "lines": [1, 1], "text": span},
        "acceptance": span,
        "observed": {"verdict": "broken", "status": "failed", "observed": {}, "note": None},
        "app": {"root": "/tmp/app", "files": {"app.js": "let total = 0;\n"}},
        "output": "unified diff",
    }


def _run_worker(monkeypatch, stdin: str) -> tuple[int, list[dict]]:
    """Run ``python -m attest.worker`` in process with a recording stand-in for the model call."""
    calls: list[dict] = []

    def fake_worker(ticket, timeout_s):
        calls.append(ticket)
        return _diff("app.js")

    monkeypatch.setattr(worker, "_openai_worker", fake_worker)
    monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    return worker.main(), calls


# -- PI: prompt injection ------------------------------------------------------------


@case("BR-PI-01")
def test_injected_observation_stays_observation_in_ticket(rt_case, tmp_path):
    i, x = rt_case["input"], rt_case["expected"]
    claim = _browser_claim(i["span"])
    observation = BrowserObservation(status="failed", note=f"kane: step failed, authored | {i['injected']}")
    verdict = judge(claim, Evidence(claim_id="c01", kind="browser", observation=observation))
    assert verdict.status == x["verdict"]

    ticket = build_ticket("000000000000-c01-1", claim, verdict, _spec_doc(i["span"]), _candidate(tmp_path))
    assert sorted(ticket) == x["ticket_keys"]
    assert ticket["acceptance"] == ticket["spec"]["text"] == i["span"]
    assert i["injected"] in ticket["observed"]["note"]
    rest = json.dumps({k: v for k, v in ticket.items() if k != "observed"})
    assert i["injected"] not in rest
    assert _keys(ticket) & set(x["forbidden_keys"]) == set()


@case("BR-PI-02")
def test_worker_diff_outside_app_root_is_refused_before_writing(rt_case, tmp_path):
    i, x = rt_case["input"], rt_case["expected"]
    candidate = _candidate(tmp_path)
    spec = candidate.root.parents[1] / SPEC
    spec.write_text("protected\n", encoding="utf-8")
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}

    for target in i["targets"]:
        for diff in (_diff(target), _diff(i["allowed"]) + _diff(target)):  # alone, and hidden behind a fine hunk
            applied, message = candidate.apply(diff)
            assert applied is x["applied"], target
            assert x["message"] in message, target
    after = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert after == before  # no patch file, no app change, spec untouched


@case("BR-PI-03")
def test_builtin_worker_drops_edits_outside_app_files(rt_case, monkeypatch):
    i, x = rt_case["input"], rt_case["expected"]
    replies: list[list[dict]] = []

    class Responses:
        def parse(self, *, text_format, **_):
            return SimpleNamespace(output_parsed=text_format(files=replies.pop(0)))

    monkeypatch.setattr(openai, "OpenAI", lambda timeout: SimpleNamespace(responses=Responses()))
    ticket = _ticket()
    ticket["app"]["files"] = i["app_files"]
    hostile = [{"path": p, "content": "changed\n"} for p in i["hostile_edits"]]

    replies.append(hostile)
    assert _openai_worker(ticket, 5) == x["hostile_only_diff"]
    replies.append([*hostile, i["allowed_edit"]])
    assert sorted(app_diff_paths(_openai_worker(ticket, 5))) == x["mixed_diff_paths"]  # not refuse-everything


# -- AB: authority bypass ------------------------------------------------------------


def _enum_values(schema) -> set[str]:
    if isinstance(schema, dict):
        found = {str(v) for v in schema.get("enum", [])}
        if "const" in schema:
            found.add(str(schema["const"]))
        return found | {v for s in schema.values() for v in _enum_values(s)}
    if isinstance(schema, list):
        return {v for s in schema for v in _enum_values(s)}
    return set()


def _property_names(schema) -> set[str]:
    if isinstance(schema, dict):
        return set(schema.get("properties", {})) | {n for s in schema.values() for n in _property_names(s)}
    if isinstance(schema, list):
        return {n for s in schema for n in _property_names(s)}
    return set()


@case("BR-AB-01")
def test_no_attest_type_can_say_reconciled(rt_case):
    i, x = rt_case["input"], rt_case["expected"]
    models: list[type[BaseModel]] = [RunReceipt, VerifiedRunReceipt, ReceiptEvidence, ReconcileHandoff,
                                     BrowserObservation, Evidence, Verdict, contracts.AuthorityDecision,
                                     contracts.Approval, contracts.RepairProposal]
    values = {v.casefold() for m in models for v in _enum_values(m.model_json_schema())}
    values |= {s.value for enum in (contracts.ExecutionState, contracts.ComparisonState, contracts.AuthorityState,
                                    contracts.EvidenceState, contracts.RepairState) for s in enum}
    assert sorted(values & set(i["reconciliation_values"])) == x["values_found"]
    fields = {n for m in models for n in _property_names(m.model_json_schema())}
    assert sorted(fields & set(i["reconciliation_fields"])) == x["fields_found"]

    version = SourceVersion.from_content(SPEC, "spec\n")
    valid = {
        RunReceipt: {"snapshot_id": version.version_id, "source_version_id": version.version_id,
                     "content_sha256": version.content_sha256, "execution_state": "failed",
                     "comparison_state": "pending", "authority_state": "pending", "evidence_state": "missing",
                     "repair_state": "not_required"},
        ReconcileHandoff: {"source_doc": SPEC, "execution_state": "not_run", "authority_state": "missing",
                           "evidence_state": "missing", "repair_state": "handoff", "source_state": "not_checked",
                           "handoff_state": "blocked",
                           "blocking_reasons": ["request:authority_missing"], "apply_requested": False,
                           "conflicts": [], "authority_note": "n", "repair_note": "n"},
    }
    assert sorted(m.__name__ for m in valid) == sorted(x["extra_fields_rejected_by"])
    for model, data in valid.items():
        model.model_validate(data)
        with pytest.raises(ValidationError):
            model.model_validate({**data, **i["reconciliation_fields"]})


@case("BR-AB-02")
def test_receipt_or_handoff_is_not_a_ticket(rt_case, monkeypatch):
    i, x = rt_case["input"], rt_case["expected"]
    for payload in i["payloads"]:
        code, calls = _run_worker(monkeypatch, payload if isinstance(payload, str) else json.dumps(payload))
        assert code == x["exit_code"], payload
        assert len(calls) == x["worker_calls"], payload


@case("BR-AB-03")
def test_ticket_with_caller_success_or_evidence_is_refused(rt_case, monkeypatch):
    i, x = rt_case["input"], rt_case["expected"]
    code, calls = _run_worker(monkeypatch, json.dumps({**_ticket(), **i["extra_fields"]}))
    assert len(calls) == x["worker_calls"]
    assert code == x["exit_code"]
    # control: the same ticket without the extra fields is dispatched, so refusing every ticket cannot pass
    code, calls = _run_worker(monkeypatch, json.dumps(_ticket()))
    assert (code, len(calls)) == (x["control_exit_code"], x["control_worker_calls"])


@case("BR-AB-04")
def test_spec_mode_receipt_never_claims_verified(rt_case):
    i, x = rt_case["input"], rt_case["expected"]
    span = 'The dashboard shows "Dashboard".'
    claim = _browser_claim(span)

    def verdict(status: str, observed: dict) -> Verdict:
        observation = BrowserObservation(status="passed" if status == "proven" else "failed", observed=observed)
        return Verdict(claim_id="c01", status=status, reason=status,
                       evidence=Evidence(claim_id="c01", kind="browser", observation=observation))

    before, after = verdict("broken", {}), verdict("proven", {"c01_label": "Dashboard"})
    result = SimpleNamespace(turn=1, doc=_spec_doc(span), claims=[claim])
    policy = SimpleNamespace(path=None)
    receipt = _receipt(result, claim, before, after, policy, AuthorityDecision("implementation_required", "spec mode"),
                       "openai:test", _diff("app.js"), i["disposition"], None, "0" * 64, None)
    assert receipt["schema"] == x["schema"]
    assert receipt["verified"] is x["verified"]
    assert receipt["disposition"] not in x["disposition_not"]

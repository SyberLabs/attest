"""Reconcile Spec red-team cases (FACTORY §1) beyond the first inventory: rewritten
handoffs, oversized and shared protected documents, look-alike requirement ids, and an
injected approval status.

Inputs and expected invariants live in cases/reconcile_spec/<ID>.json. Every case runs
the ``attest reconcile-spec`` CLI against documents written under a source root, the
way a caller would.
"""

from __future__ import annotations

import copy
import hashlib
import json
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from attest import reconcile_spec as reconcile_spec_mod
from attest.cli import app
from attest.reconcile_spec import ReconcileHandoff, ReconcileRequest, handoff_package_problems

from ._harness import case

REPAIR_STATES = {"refused", "handoff"}


def _write_files(root: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text, encoding="utf-8")


def _run(tmp_path: Path, request: dict, files: dict[str, str] | None = None):
    """The CLI on ``request`` with ``files`` under tmp_path/src; returns the result and the written JSON."""
    root = tmp_path / "src"
    root.mkdir(parents=True, exist_ok=True)
    _write_files(root, files or {})
    path = tmp_path / "request.json"
    path.write_text(json.dumps(request), encoding="utf-8")
    out = tmp_path / "out"
    result = CliRunner().invoke(app, ["reconcile-spec", str(path), "--out", str(out), "--source-root", str(root)])
    written = out / "reconcile-spec.json"
    data = json.loads(written.read_text(encoding="utf-8")) if written.exists() else None
    if data is not None:
        assert data["repair_state"] in REPAIR_STATES  # R1/R4 on every case
    return result, data


# -- PI: prompt injection ------------------------------------------------------------


@case("RS-PI-06")
def test_added_approved_status_does_not_make_a_draft_govern(rt_case, tmp_path):
    i, x = rt_case["input"], rt_case["expected"]
    document = i["request"]["conflicts"][0]["authority_reference"].partition("#")[0]
    for name, text in i["documents"].items():
        result, data = _run(tmp_path / name, i["request"], {document: text})
        assert result.exit_code == x["exit_code"], (name, result.output)
        assert x["reason"] in data["blocking_reasons"], name
        assert data["source_state"] == x["source_state"] and data["authority_state"] == x["authority_state"], name
        assert x["shows"][name] in data["conflicts"][0]["source_not_governing"], name
        assert (tmp_path / name / "src" / document).read_text(encoding="utf-8") == text


# -- DS: identity confusion ----------------------------------------------------------


@case("RS-DS-04")
def test_requirement_in_another_case_is_checked_and_blocks(rt_case, tmp_path):
    i, x = rt_case["input"], rt_case["expected"]
    result, data = _run(tmp_path, i["request"], i["files"])
    assert result.exit_code == x["exit_code"], result.output
    assert data["blocking_reasons"] == x["reasons"]
    assert data["authority_state"] == x["authority_state"] and data["source_state"] == x["source_state"]
    weaker = next(c for c in data["conflicts"] if c["claim_id"] == "c02")
    assert " ".join(weaker["source_protected_text"].split()) == " ".join(x["shows"].split())
    for name, text in i["files"].items():
        assert ((tmp_path / "src" / name).read_text(encoding="utf-8") == text) is x["contract_unchanged"]


@case("RS-DS-05")
def test_look_alike_requirement_id_is_not_the_contracts(rt_case, tmp_path):
    i, x = rt_case["input"], rt_case["expected"]
    for name, rid in i["ids"].items():
        request = {
            "source_doc": "docs/requirements.md",
            "authority_status": "present",
            "conflicts": [{
                "claim_id": "c01", "protected_requirement_id": rid, "authority_reference": f"contracts/security.md#{rid}",
                "protected_text": i["protected_text"], "candidate_side": "app_wrong",
                "evidence": [{"kind": "runtime", "pointer": "artifacts/run-17/sign-in.png"}],
            }],
        }
        result, data = _run(tmp_path / name, request, i["files"])
        assert result.exit_code == x["exit_code"], (name, result.output)
        assert x["reason"] in data["blocking_reasons"], name
        assert data["authority_state"] == x["authority_state"] and data["source_state"] == x["source_state"], name


# -- SIM: fake evidence --------------------------------------------------------------


@case("RS-SIM-03")
def test_handoff_rewritten_as_verified_is_unreadable(rt_case, tmp_path):
    i, x = rt_case["input"], rt_case["expected"]
    result, data = _run(tmp_path, i["request"], i["files"])
    assert result.exit_code == x["exit_code"], result.output
    assert data["handoff_state"] == x["handoff_state"] and data["execution_state"] == x["execution_state"]

    out = tmp_path / "out"
    markdown, written = out / "reconcile-spec.md", out / "reconcile-spec.json"
    request = ReconcileRequest.model_validate(i["request"])
    assert handoff_package_problems(request, markdown, written) == []
    rewrites = [{key: value} for key, value in i["rewrites"].items()] + [{key: value} for key, value in i["extra_fields"].items()]
    for change in rewrites:
        forged = copy.deepcopy(data)
        forged.update(change)
        written.write_text(json.dumps(forged), encoding="utf-8")
        with pytest.raises(ValueError):
            ReconcileHandoff.model_validate_json(written.read_bytes())
        assert handoff_package_problems(request, markdown, written) == x["problems"], change


# -- COST ----------------------------------------------------------------------------


@case("RS-COST-02")
def test_huge_protected_document_is_refused_at_the_cap(rt_case, tmp_path):
    i, x = rt_case["input"], rt_case["expected"]
    contract = tmp_path / "src" / i["document"]
    contract.parent.mkdir(parents=True)
    with contract.open("wb") as handle:
        handle.write(i["head"].encode("utf-8"))
        try:
            handle.truncate(i["sparse_bytes"])  # sparse: the size is there, the blocks are not
        except OSError as exc:
            pytest.skip(f"this filesystem cannot hold a sparse {i['sparse_bytes']}-byte file ({exc})")
    assert contract.stat().st_size == i["sparse_bytes"]

    started = time.monotonic()
    result, data = _run(tmp_path, i["request"])
    assert time.monotonic() - started < x["max_seconds"]
    assert result.exit_code == x["exit_code"], result.output
    assert x["reason"] in data["blocking_reasons"]
    assert data["source_state"] == x["source_state"] and data["authority_state"] == x["authority_state"]
    assert data["source_digests"] == x["source_digests"]


@case("RS-COST-03")
def test_shared_protected_document_is_read_once(rt_case, tmp_path, monkeypatch):
    i, x = rt_case["input"], rt_case["expected"]
    text = "The application must require multi-factor authentication\nfor every administrator sign-in."
    request = {
        "source_doc": "docs/requirements.md",
        "authority_status": "present",
        "conflicts": [
            {"claim_id": f"c{k:04d}", "protected_requirement_id": "SEC-4", "authority_reference": "contracts/security.md#SEC-4",
             "protected_text": text, "candidate_side": "app_wrong",
             "evidence": [{"kind": "runtime", "pointer": f"artifacts/run-{k}/sign-in.png"}]}
            for k in range(1, i["conflicts"] + 1)
        ],
    }
    reads: dict[str, int] = {}
    real = reconcile_spec_mod.read_source

    def counting(source_doc, root):
        reads[source_doc] = reads.get(source_doc, 0) + 1
        return real(source_doc, root)

    monkeypatch.setattr(reconcile_spec_mod, "read_source", counting)
    result, data = _run(tmp_path, request, i["files"])
    assert result.exit_code == x["exit_code"], result.output
    assert reads == x["reads"]
    assert data["handoff_state"] == x["handoff_state"] and len(data["conflicts"]) == i["conflicts"]
    contract = (tmp_path / "src" / "contracts" / "security.md").read_bytes()
    assert data["source_digests"] == {"contracts/security.md": hashlib.sha256(contract).hexdigest()}

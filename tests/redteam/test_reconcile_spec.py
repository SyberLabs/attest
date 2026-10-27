"""Reconcile Spec red-team cases (FACTORY §1).

Inputs and expected invariants live in cases/reconcile_spec/<ID>.json. Under
Reconcile Spec, R4 means repair_state is always refused or handoff.

Unless a case supplies its own ``source``, ``diagnose`` and ``_cli`` give every
document the request's protected texts are checked against (the authority
reference's path, else source_doc) a text that labels each requirement with the
first protected text the request submits for it, so cases about other checks are
not blocked by the source check. RS-AB-10, RS-AB-11, RS-AB-12, RS-AB-13 and RS-CTRL-01
supply their own documents.
"""

from __future__ import annotations

import builtins
import copy
import hashlib
import json
import os
import re
import socket
import time
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from attest import reconcile_spec as reconcile_spec_mod
from attest.cli import app
from attest.reconcile_spec import (
    ReconcileHandoff,
    ReconcileRequest,
    ReconcileSpec,
    SourceDocument,
    protected_document,
    render_handoff_markdown,
    write_handoff,
)

from ._harness import case

REPAIR_STATES = {"refused", "handoff"}


def quoted_source(request: dict) -> str:
    """A source document labelling each requirement with its first submitted text."""
    first: dict[str, str] = {}
    for conflict in request.get("conflicts", []):
        first.setdefault(conflict["protected_requirement_id"].strip(), conflict["protected_text"])
    return "# Requirements\n\n" + "".join(f"- **{rid}:** {text}\n" for rid, text in first.items())


def _documents(request: dict) -> list[str]:
    """The paths the request's protected texts are checked against, as the CLI reads them."""
    parsed = ReconcileRequest.model_validate(request)
    return list(dict.fromkeys(protected_document(c, parsed.source_doc) for c in parsed.conflicts))


def diagnose(request: dict, *, apply_requested: bool = False, source: str | None = None):
    text = quoted_source(request) if source is None else source
    document = SourceDocument(text, hashlib.sha256(text.encode("utf-8")).hexdigest())
    handoff = ReconcileSpec().diagnose(ReconcileRequest.model_validate(request), apply_requested=apply_requested,
                                       sources=dict.fromkeys(_documents(request), document))
    assert handoff.repair_state in REPAIR_STATES  # R1/R4 on every case
    return handoff


def _cli(tmp_path, payload: dict, *extra: str, source: str | None = None):
    """Run the CLI with ``source`` (default: the quoted source) at each checked document under tmp_path/src."""
    path = tmp_path / "request.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    root = tmp_path / "src"
    root.mkdir(exist_ok=True)
    try:
        documents = _documents(payload) if "--source-root" not in extra else []
    except ValidationError:
        documents = []  # the CLI rejects it before reading anything
    for doc in documents:
        if ".." not in Path(doc).parts and not Path(doc).is_absolute() and "://" not in doc:
            (root / doc).parent.mkdir(parents=True, exist_ok=True)
            (root / doc).write_text(quoted_source(payload) if source is None else source, encoding="utf-8")
    flags = extra if "--source-root" in extra else ("--source-root", str(root), *extra)
    return CliRunner().invoke(app, ["reconcile-spec", str(path), "--out", str(tmp_path / "out"), *flags])


def _written(tmp_path) -> tuple[dict, str]:
    out = tmp_path / "out"
    data = json.loads((out / "reconcile-spec.json").read_text(encoding="utf-8"))
    return data, (out / "reconcile-spec.md").read_text(encoding="utf-8")


# -- AB: authority bypass ------------------------------------------------------------


@case("RS-AB-01")
def test_weakening_fix_is_refused_and_handed_off(rt_case, tmp_path):
    request, x = rt_case["input"]["request"], rt_case["expected"]
    handoff = diagnose(request)
    conflict = handoff.conflicts[0]

    assert handoff.repair_state == x["repair_state"]
    assert conflict.protected_edit_refused is x["protected_edit_refused"]
    assert conflict.protected_text == request["conflicts"][0]["protected_text"]
    assert conflict.recommended_owner == x["recommended_owner"]
    markdown, _ = write_handoff(handoff, tmp_path)
    assert x["markdown_contains"] in markdown.read_text(encoding="utf-8")


@case("RS-AB-02")
def test_missing_authority_stops_for_review(rt_case):
    x = rt_case["expected"]
    handoff = diagnose(rt_case["input"]["request"])
    assert handoff.authority_state == x["authority_state"]
    assert handoff.repair_state == x["repair_state"]
    assert x["note_contains"] in handoff.authority_note


@case("RS-AB-03")
def test_conflicting_authority_stops_for_review(rt_case):
    x = rt_case["expected"]
    handoff = diagnose(rt_case["input"]["request"])
    assert handoff.authority_state == x["authority_state"]
    assert handoff.repair_state == x["repair_state"]
    assert x["note_contains"] in handoff.authority_note


@case("RS-AB-04")
def test_claimed_authority_without_reference_is_downgraded(rt_case):
    x = rt_case["expected"]
    request = rt_case["input"]["request"]
    assert request["authority_status"] == "present"
    handoff = diagnose(request)
    assert handoff.authority_state == x["authority_state"]
    assert handoff.repair_state == x["repair_state"]


@case("RS-AB-05")
def test_apply_is_always_refused(rt_case, tmp_path):
    request, x = rt_case["input"]["request"], rt_case["expected"]
    handoff = diagnose(request, apply_requested=True)
    assert handoff.repair_state == x["repair_state"]
    assert all(c.protected_edit_refused is x["protected_edit_refused"] for c in handoff.conflicts)

    result = _cli(tmp_path, request, "--apply")
    assert result.exit_code == x["exit_code"]
    assert x["stderr_contains"] in result.output
    written = json.loads((tmp_path / "out" / "reconcile-spec.json").read_text(encoding="utf-8"))
    assert written["repair_state"] == x["repair_state"]
    assert written["apply_requested"] is True


@case("RS-AB-06")
def test_ambiguous_side_is_never_auto_resolved(rt_case):
    x = rt_case["expected"]
    handoff = diagnose(rt_case["input"]["request"])
    assert handoff.evidence_state == "provided" and handoff.authority_state == "present"
    conflict = handoff.conflicts[0]
    assert conflict.candidate_side == x["candidate_side"]
    assert conflict.recommended_owner == x["recommended_owner"]
    assert handoff.repair_state == x["repair_state"]


@case("RS-AB-07")
def test_handoff_cannot_express_applied_or_promoted_repair(rt_case):
    i, x = rt_case["input"], rt_case["expected"]
    spec = ReconcileSpec()
    assert spec.can_apply_protected_text is False and spec.can_promote_protected_text is False
    public = sorted(n for n in dir(spec) if not n.startswith("_") and callable(getattr(spec, n)))
    assert public == x["public_methods"]

    valid = diagnose({
        "source_doc": "docs/r.md", "authority_status": "present",
        "conflicts": [{"claim_id": "c01", "protected_requirement_id": "R-1", "protected_text": "Must hold."}],
    }).model_dump()
    for state in i["forbidden_repair_states"]:
        with pytest.raises(ValidationError):
            ReconcileHandoff.model_validate({**valid, "repair_state": state})


@case("RS-AB-08")
def test_weakening_fix_fails_the_cli_and_still_hands_off(rt_case, tmp_path):
    i, x = rt_case["input"], rt_case["expected"]
    for name, proposed in i["proposals"].items():
        run = tmp_path / name
        run.mkdir()
        contract = run / "security.md"
        contract.write_text(i["contract"], encoding="utf-8")
        request = copy.deepcopy(i["request"])
        request["conflicts"][0]["proposed_protected_text"] = proposed
        for flags in ((), ("--apply",)):
            result = _cli(run, request, *flags)
            assert result.exit_code == x["exit_code"], (name, flags, result.output)
            assert x["stderr_contains"] in result.output
            data, markdown = _written(run)
            conflict = data["conflicts"][0]
            assert data["repair_state"] == x["repair_state"]
            assert conflict["protected_edit_refused"] is x["protected_edit_refused"]
            # refused, and still a complete handoff to the owner of the behavior
            assert data["handoff_state"] == x["handoff_state"]
            assert conflict["recommended_owner"] == x["recommended_owner"]
            assert conflict["protected_text"] == request["conflicts"][0]["protected_text"]
            assert conflict["proposed_protected_text"] == proposed
            assert x["markdown_contains"] in markdown
            assert contract.read_text(encoding="utf-8") == i["contract"]


@case("RS-AB-09")
def test_missing_or_conflicting_authority_fails_the_cli(rt_case, tmp_path):
    i, x = rt_case["input"], rt_case["expected"]
    for name, request in i["variants"].items():
        want = x["variants"][name]
        handoff = diagnose(request)
        assert handoff.authority_state == want["authority_state"], name
        assert handoff.blocking_reasons == want["blocking_reasons"], name
        run = tmp_path / name
        run.mkdir()
        result = _cli(run, request)
        assert result.exit_code == x["exit_code"], (name, result.output)
        assert x["stderr_contains"] in result.output
        data, markdown = _written(run)
        assert data["handoff_state"] == x["handoff_state"]
        assert data["repair_state"] == x["repair_state"]
        assert data["blocking_reasons"] == want["blocking_reasons"]
        assert all(reason in markdown for reason in want["blocking_reasons"])


def _write_files(root: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text, encoding="utf-8", newline="")


@case("RS-AB-10")
def test_submitted_text_weaker_than_the_contract_blocks(rt_case, tmp_path):
    i, x = rt_case["input"], rt_case["expected"]
    # the same contract does not block the requirement quoted whole (line breaks may differ)
    exact = tmp_path / "exact"
    _write_files(exact / "src", i["files"])
    result = _cli(exact, i["exact"], "--source-root", str(exact / "src"))
    assert result.exit_code == x["exact_exit_code"], result.output
    assert _written(exact)[0]["source_state"] == "matched"

    for name, request in i["variants"].items():
        run = tmp_path / name
        root = run / "src"
        files = {**i["files"], **i["extra_files"].get(name, {})}
        _write_files(root, files)
        result = _cli(run, request, "--source-root", str(root))
        assert result.exit_code == x["exit_code"], (name, result.output)
        assert x["stderr_contains"] in result.output, name
        data, markdown = _written(run)
        for key in ("handoff_state", "source_state", "authority_state", "repair_state", "blocking_reasons"):
            assert data[key] == x[key], (name, key, data[key])
        conflict = data["conflicts"][0]
        want = x["document_text"][request["conflicts"][0]["protected_requirement_id"]]
        assert conflict["protected_text"] == request["conflicts"][0]["protected_text"], name
        assert conflict["source_checked_in"] == x["source_checked_in"], name
        assert conflict["source_protected_text"] == want, name
        assert want in markdown, name
        assert "Handoff: `blocked`" in markdown, name
        # nothing was rewritten to make the texts agree
        for path, text in files.items():
            assert (root / path).read_text(encoding="utf-8") == text, (name, path)


@case("RS-AB-11")
def test_unusable_protected_document_blocks(rt_case, tmp_path, monkeypatch):
    i, x = rt_case["input"], rt_case["expected"]

    def no_network(*args, **kwargs):
        raise AssertionError("Reconcile Spec must not open a network connection")

    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setattr(socket.socket, "connect", no_network)

    for name, variant in i["variants"].items():
        want = x["variants"][name]
        run = tmp_path / name
        root = run / "src"
        outside = run / "outside" / "security.md"
        outside.parent.mkdir(parents=True)
        # the escaping paths name a file that states the requirement exactly: only the root keeps it out
        outside.write_text(i["outside_text"], encoding="utf-8")
        contract = root / "contracts" / "security.md"
        contract.parent.mkdir(parents=True)
        setup = variant["setup"]
        if setup == "symlink_to_outside":
            try:
                contract.symlink_to(outside)
            except OSError:  # no symlinks on this platform (Windows without privilege)
                continue
        elif setup == "directory":
            contract.mkdir()
        elif setup == "latin1":
            contract.write_bytes(i["documents"]["latin1"].encode("latin-1"))
        elif setup == "too_large":
            body = i["outside_text"].encode("utf-8")
            contract.write_bytes(body + b" " * (i["too_large_bytes"] - len(body)))
        elif setup in i["documents"]:
            contract.write_text(i["documents"][setup], encoding="utf-8")
        reference = variant["reference"].replace("{outside}", outside.resolve().as_posix())
        request = {
            "source_doc": "contracts/security.md",
            "authority_status": "present",
            "conflicts": [{
                "claim_id": "c01", "protected_requirement_id": "SEC-4", "authority_reference": reference,
                "protected_text": i["protected_text"], "candidate_side": "app_wrong",
                "evidence": [{"kind": "runtime", "pointer": "artifacts/run-17/sign-in.png"}],
            }],
        }
        result = _cli(run, request, "--source-root", str(root))
        assert result.exit_code == x["exit_code"], (name, result.output)
        assert x["stderr_contains"] in result.output, name
        data, markdown = _written(run)
        assert data["handoff_state"] == x["handoff_state"], name
        assert data["repair_state"] == x["repair_state"], name
        assert data["blocking_reasons"] == [want["reason"]], (name, data["blocking_reasons"])
        assert data["source_state"] == want["source_state"], name
        assert data["authority_state"] == want["authority_state"], name
        assert want["reason"] in markdown, name
        if want["source_state"] == "unreadable":
            assert data["source_digests"] == {}, name
        assert outside.read_text(encoding="utf-8") == i["outside_text"], name

    # a caller of the API that reads nothing gets a blocked handoff, never a ready one
    want = x["api_without_sources"]
    request = ReconcileRequest.model_validate({
        "source_doc": "contracts/security.md",
        "authority_status": "present",
        "conflicts": [{"claim_id": "c01", "protected_requirement_id": "SEC-4",
                       "authority_reference": "contracts/security.md#SEC-4", "protected_text": i["protected_text"],
                       "evidence": [{"kind": "runtime", "pointer": "artifacts/run-17/sign-in.png"}]}],
    })
    handoff = ReconcileSpec().diagnose(request)
    assert handoff.handoff_state == x["handoff_state"]
    assert handoff.blocking_reasons == [want["reason"]]
    assert handoff.source_state == want["source_state"]
    assert handoff.authority_state == want["authority_state"]
    # ... and so does one that read some other document than the one the reference names
    want = x["api_document_not_read"]
    other = SourceDocument(i["outside_text"], hashlib.sha256(i["outside_text"].encode("utf-8")).hexdigest())
    handoff = ReconcileSpec().diagnose(request, sources={"docs/other.md": other})
    assert handoff.handoff_state == x["handoff_state"]
    assert handoff.blocking_reasons == [want["reason"]]
    assert handoff.source_state == want["source_state"]
    assert handoff.authority_state == want["authority_state"]

    # and a handoff JSON that claims ready without a matched, read document does not load
    ready = diagnose(request.model_dump()).model_dump()
    assert ready["handoff_state"] == "ready"
    ReconcileHandoff.model_validate(ready)
    for forged in (
        {**ready, "source_state": "mismatch"},
        {**ready, "source_state": "not_checked"},
        {**ready, "source_digests": {}},
        {**ready, "source_digests": {"docs/other.md": "0" * 64}},
    ):
        with pytest.raises(ValidationError):
            ReconcileHandoff.model_validate(forged)


def _contract_request(protected_text: str, requirement: str = "SEC-4") -> dict:
    return {
        "source_doc": "docs/requirements.md",
        "authority_status": "present",
        "conflicts": [{
            "claim_id": "c01", "protected_requirement_id": requirement, "authority_reference": f"contracts/security.md#{requirement}",
            "protected_text": protected_text,
            "observed_text": "The sign-in flow accepted a password-only administrator sign-in.",
            "candidate_side": "app_wrong",
            "evidence": [{"kind": "runtime", "pointer": "artifacts/run-17/sign-in.png", "description": "Observed sign-in flow"}],
        }],
    }


@case("RS-AB-12")
def test_text_matched_in_a_document_that_does_not_govern_blocks(rt_case, tmp_path):
    i, x = rt_case["input"], rt_case["expected"]
    # a governing contract with look-alike words (a status_code key, "Status codes:", a withdrawn section
    # before SEC-4 holding another requirement, SEC-6's own "deprecated" title) is still a ready handoff
    for requirement, text in i["governing_requirements"].items():
        governing = tmp_path / f"governing-{requirement}"
        _write_files(governing / "src", {"contracts/security.md": i["governing"]})
        result = _cli(governing, _contract_request(text, requirement), "--source-root", str(governing / "src"))
        assert result.exit_code == x["governing_exit_code"], (requirement, result.output)
        data, _ = _written(governing)
        assert data["source_state"] == "matched" and data["conflicts"][0]["source_not_governing"] == [], requirement

    for name, variant in i["variants"].items():
        run = tmp_path / name
        root = run / "src"
        _write_files(root, {"contracts/security.md": variant["document"]})
        request = _contract_request(variant.get("protected_text", i["protected_text"]))
        result = _cli(run, request, "--source-root", str(root))
        assert result.exit_code == x["exit_code"], (name, result.output)
        assert x["stderr_contains"] in result.output, name
        data, markdown = _written(run)
        for key in ("handoff_state", "source_state", "authority_state", "repair_state"):
            assert data[key] == x[key], (name, key, data[key])
        assert data["blocking_reasons"] == [x["reason_prefix"] + why for why in variant["reasons"]], (name, data["blocking_reasons"])
        conflict = data["conflicts"][0]
        # the text did match: the document was read and says what was submitted, it just doesn't govern
        assert conflict["source_protected_text"] is None, name
        assert data["source_digests"] == {
            "contracts/security.md": hashlib.sha256(variant["document"].encode("utf-8")).hexdigest()
        }, name
        assert variant["shows"] in conflict["source_not_governing"], (name, conflict["source_not_governing"])
        assert x["markdown_contains"] in markdown and variant["shows"] in markdown, name
        assert "Handoff: `blocked`" in markdown, name
        assert (root / "contracts" / "security.md").read_text(encoding="utf-8") == variant["document"], name

    # an API caller that passes the document it read gets the same blocked handoff
    superseded = i["variants"]["superseded_banner"]["document"]
    handoff = diagnose(_contract_request(i["protected_text"]), source=superseded)
    assert handoff.handoff_state == x["handoff_state"]
    assert handoff.source_state == x["source_state"]
    assert handoff.blocking_reasons == [x["reason_prefix"] + "superseded"]

    # and a handoff JSON that claims ready over a document that doesn't govern does not load
    ready = diagnose(_contract_request(i["protected_text"]), source=i["governing"]).model_dump()
    assert ready["handoff_state"] == "ready"
    ReconcileHandoff.model_validate(ready)
    flagged = copy.deepcopy(ready)
    flagged["conflicts"][0]["source_not_governing"] = ["status: superseded"]
    for forged in ({**ready, "source_state": "not_governing"}, flagged):
        with pytest.raises(ValidationError):
            ReconcileHandoff.model_validate(forged)


def _referenced_request(protected_text: str, source_doc: str, reference: str) -> dict:
    request = _contract_request(protected_text)
    request["source_doc"] = source_doc
    request["conflicts"][0]["authority_reference"] = reference
    return request


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@case("RS-AB-13")
def test_reference_to_a_copy_that_differs_from_another_document_blocks(rt_case, tmp_path):
    i, x = rt_case["input"], rt_case["expected"]
    for name, variant in i["variants"].items():
        run = tmp_path / name
        root = run / "src"
        _write_files(root, variant["files"])
        protected_text = variant.get("protected_text", i["weak_text"])
        request = _referenced_request(protected_text, variant["source_doc"], variant["reference"])
        result = _cli(run, request, "--source-root", str(root))
        assert result.exit_code == x["exit_code"], (name, result.output)
        assert x["stderr_contains"] in result.output, name
        data, markdown = _written(run)
        for key in ("handoff_state", "source_state", "authority_state", "repair_state"):
            assert data[key] == x[key], (name, key, data[key])
        assert data["blocking_reasons"] == [x["reason"]], (name, data["blocking_reasons"])
        conflict = data["conflicts"][0]
        # the referenced document does say the submitted text, and says nothing about not governing
        assert conflict["source_protected_text"] is None and conflict["source_not_governing"] == [], name
        assert conflict["source_elsewhere"] == variant["differs_in"], (name, conflict["source_elsewhere"])
        assert data["source_digests"] == {path: _sha(text) for path, text in variant["files"].items()}, name
        assert x["markdown_contains"] in markdown and "Handoff: `blocked`" in markdown, name
        for path, texts in variant["differs_in"].items():
            assert all(f"{path}: {text}" in markdown for text in texts), name
        for path, text in variant["files"].items():
            assert (root / path).read_text(encoding="utf-8") == text, (name, path)

    # the contract (named as ./contracts/security.md) with a same-text copy, a prose mention, an unrelated requirement, a copy under
    # .git, a .txt copy, a link out of the root, a broken link and earlier handoffs is still ready
    control = i["control"]
    run = tmp_path / "control"
    root = run / "src"
    _write_files(root, control["files"])
    outside = run / "outside.md"
    outside.write_text(control["outside"], encoding="utf-8")
    weak_label = control["files"][".git/sec-4.md"]
    _write_files(root, {".attest/reconcile-spec/old/reconcile-spec.md": weak_label, "handoff/reconcile-spec.md": weak_label})
    try:
        (root / "notes" / "linked.md").symlink_to(outside)
        (root / "notes" / "broken.md").symlink_to(run / "missing.md")
    except OSError:  # no symlinks on this platform (Windows without privilege)
        pass
    request_path = run / "request.json"
    request = _referenced_request(i["contract_text"], control["source_doc"], control["reference"])
    request_path.write_text(json.dumps(request), encoding="utf-8")
    result = CliRunner().invoke(app, [
        "reconcile-spec", str(request_path), "--source-root", str(root),
        "--out", str(root / "handoff"), "--home", str(root / ".attest"),
    ])
    assert result.exit_code == x["control_exit_code"], result.output
    data = json.loads((root / "handoff" / "reconcile-spec.json").read_text(encoding="utf-8"))
    assert data["handoff_state"] == "ready" and data["source_state"] == "matched", data["blocking_reasons"]
    assert data["conflicts"][0]["source_elsewhere"] == {}
    # the contract is read once, under the path the reference spells it with
    assert data["source_digests"] == {path: _sha(control["files"][path.removeprefix("./")]) for path in control["digests"]}

    # a Markdown document under the root that can't be read could state the requirement differently
    for name, unreadable in i["unreadable"].items():
        run = tmp_path / f"unreadable-{name}"
        root = run / "src"
        _write_files(root, {"contracts/security.md": i["contract"]})
        (root / unreadable["path"]).parent.mkdir(parents=True, exist_ok=True)
        if unreadable["reason"] == "not_utf8":
            (root / unreadable["path"]).write_bytes(unreadable["text"].encode("latin-1"))
        else:
            body = unreadable["text"].encode("utf-8")
            (root / unreadable["path"]).write_bytes(body + b" " * (unreadable["bytes"] - len(body)))
        request = _referenced_request(i["contract_text"], "contracts/security.md", "contracts/security.md#SEC-4")
        result = _cli(run, request, "--source-root", str(root))
        assert result.exit_code == x["exit_code"], (name, result.output)
        data, markdown = _written(run)
        reason = f"{unreadable['path']}:source_unreadable:{unreadable['reason']}"
        assert data["blocking_reasons"] == [reason], (name, data["blocking_reasons"])
        assert data["source_state"] == x["unreadable_source_state"], name
        assert data["authority_state"] == x["unreadable_authority_state"], name
        assert reason in markdown, name

    # ... and so could a directory the walk can't list
    unlisted = i["unlisted_directory"]
    run = tmp_path / "unlisted"
    root = run / "src"
    _write_files(root, {"contracts/security.md": i["contract"], unlisted["file"]: unlisted["text"]})
    locked = root / unlisted["path"]
    locked.chmod(0)
    try:
        if not os.access(locked, os.R_OK):  # root, and Windows, read it anyway
            request = _referenced_request(i["contract_text"], "contracts/security.md", "contracts/security.md#SEC-4")
            result = _cli(run, request, "--source-root", str(root))
            assert result.exit_code == x["exit_code"], result.output
            data, _ = _written(run)
            assert data["blocking_reasons"] == [f"{unlisted['path']}:source_unreadable:{unlisted['reason']}"]
            assert data["authority_state"] == x["unreadable_authority_state"]
    finally:
        locked.chmod(0o755)

    # an API caller that passes the documents it read gets the same blocked handoff ...
    variant = i["variants"]["scratch_copy_referenced"]
    request = ReconcileRequest.model_validate(
        _referenced_request(i["weak_text"], variant["source_doc"], variant["reference"])
    )
    read = {path: SourceDocument(text, _sha(text)) for path, text in variant["files"].items()}
    handoff = ReconcileSpec().diagnose(request, sources=read)
    assert handoff.handoff_state == x["handoff_state"] and handoff.source_state == x["source_state"]
    assert handoff.blocking_reasons == [x["reason"]]
    assert handoff.conflicts[0].source_elsewhere == variant["differs_in"]

    # ... and a handoff JSON that claims ready while another document states it differently does not load
    ready = diagnose(_contract_request(i["contract_text"]), source=i["contract"]).model_dump()
    assert ready["handoff_state"] == "ready"
    ReconcileHandoff.model_validate(ready)
    flagged = copy.deepcopy(ready)
    flagged["conflicts"][0]["source_elsewhere"] = variant["differs_in"]
    for forged in ({**ready, "source_state": "contested"}, flagged):
        with pytest.raises(ValidationError):
            ReconcileHandoff.model_validate(forged)


# -- PI: prompt injection ------------------------------------------------------------


@case("RS-PI-01")
def test_injected_superseded_claim_changes_nothing(rt_case, tmp_path):
    request, x = rt_case["input"]["request"], rt_case["expected"]
    handoff = diagnose(request)
    conflict = handoff.conflicts[0]
    assert handoff.repair_state == x["repair_state"]
    assert conflict.protected_text == request["conflicts"][0]["protected_text"]
    assert conflict.protected_requirement_id == x["protected_requirement_id"]
    assert conflict.authority_reference == x["authority_reference"]
    assert conflict.proposed_protected_text is None
    _, machine = write_handoff(handoff, tmp_path)
    assert json.loads(machine.read_text(encoding="utf-8"))["conflicts"][0]["protected_text"] == conflict.protected_text


@case("RS-PI-02")
def test_multiline_fields_stay_inside_their_code_span(rt_case):
    i, x = rt_case["input"], rt_case["expected"]
    base = {
        "source_doc": "docs/requirements.md",
        "authority_status": "present",
        "conflicts": [{
            "claim_id": "c01",
            "protected_requirement_id": "SEC-4",
            "authority_reference": "contracts/security.md#SEC-4",
            "protected_text": "Admins must use MFA.",
            "evidence": [{"kind": "runtime", "pointer": "artifacts/run-17/a.png", "description": "Observed"}],
        }],
    }
    leaked = []
    for field in i["multiline_fields"]:
        request = copy.deepcopy(base)
        holder = request if field == "source_doc" else request["conflicts"][0]
        if field in {"pointer", "description"}:
            holder = holder["evidence"][0]
        holder[field] = f"{holder[field]}\n{i['second_line']}"
        markdown = render_handoff_markdown(diagnose(request))
        headings = [line for line in markdown.splitlines() if line.startswith("#")]
        if headings != x["headings"]:
            leaked.append(field)
    assert leaked == []


@case("RS-PI-03")
def test_state_words_in_observed_text_do_not_set_states(rt_case):
    x = rt_case["expected"]
    handoff = diagnose(rt_case["input"]["request"])
    assert handoff.authority_state == x["authority_state"]
    assert handoff.repair_state == x["repair_state"]
    assert handoff.evidence_state == x["evidence_state"]


@case("RS-PI-04")
def test_fence_and_heading_in_protected_text_stay_fenced(rt_case):
    x = rt_case["expected"]
    markdown = render_handoff_markdown(diagnose(rt_case["input"]["request"]))
    fence: str | None = None
    seen_inside = []
    for line in markdown.splitlines():
        opener = re.fullmatch(r"(`{3,})\w*", line)
        if fence is None and opener:
            fence = opener.group(1)
        elif fence is not None and re.fullmatch(r"`{3,}", line) and len(line) >= len(fence):
            fence = None
        elif line == x["injected_line"]:
            seen_inside.append(fence is not None)
    assert seen_inside and all(flag is x["inside_fence"] for flag in seen_inside)


@case("RS-PI-05")
def test_superseded_claim_without_change_decision_blocks(rt_case, tmp_path):
    i, x = rt_case["input"], rt_case["expected"]
    for name, request in i["variants"].items():
        handoff = diagnose(request)
        submitted = request["conflicts"][0]
        assert handoff.blocking_reasons == [x["reason"]], name
        assert handoff.repair_state == x["repair_state"]
        conflict = handoff.conflicts[0]
        assert conflict.protected_requirement_id == x["protected_requirement_id"]
        assert conflict.protected_text == submitted["protected_text"]
        assert conflict.authority_reference == submitted["authority_reference"]
        assert conflict.proposed_protected_text is None
        run = tmp_path / name
        run.mkdir()
        result = _cli(run, request)
        assert result.exit_code == x["exit_code"], (name, result.output)
        assert x["stderr_contains"] in result.output
        data, markdown = _written(run)
        assert data["handoff_state"] == x["handoff_state"]
        assert x["markdown_contains"] in markdown


# -- DS: identity confusion ----------------------------------------------------------


@case("RS-DS-01")
def test_conflicts_keep_their_own_identity(rt_case):
    handoff = diagnose(rt_case["input"]["request"])
    got = [[c.claim_id, c.protected_requirement_id, c.authority_reference, c.recommended_owner] for c in handoff.conflicts]
    assert got == rt_case["expected"]["pairs"]


@case("RS-DS-02")
def test_protected_text_bytes_survive_handoff_round_trip(rt_case, tmp_path):
    request, x = rt_case["input"]["request"], rt_case["expected"]
    original = request["conflicts"][0]["protected_text"]
    handoff = diagnose(request)
    _, machine = write_handoff(handoff, tmp_path)
    written = json.loads(machine.read_text(encoding="utf-8"))["conflicts"][0]

    digest = lambda s: hashlib.sha256(s.encode("utf-8")).hexdigest()  # noqa: E731
    assert digest(written["protected_text"]) == digest(original)
    # NFC vs NFD look identical but are a different protected text: still refused.
    assert written["protected_edit_refused"] is x["protected_edit_refused"]
    assert handoff.repair_state == x["repair_state"]


@case("RS-DS-03")
def test_mismatched_authority_reference_is_not_trusted(rt_case):
    x = rt_case["expected"]
    handoff = diagnose(rt_case["input"]["request"])
    assert handoff.repair_state == x["repair_state"]
    assert handoff.authority_state == x["authority_state"]
    assert handoff.handoff_state == x["handoff_state"]
    assert handoff.blocking_reasons == x["blocking_reasons"]
    assert x["note_contains"] in handoff.authority_note


# -- SIM: fake evidence --------------------------------------------------------------


@case("RS-SIM-01")
def test_receipt_pointer_claiming_verified_is_only_an_observation(rt_case):
    x = rt_case["expected"]
    handoff = diagnose(rt_case["input"]["request"])
    assert handoff.execution_state == x["execution_state"]
    assert handoff.repair_state == x["repair_state"]
    states = {handoff.execution_state, handoff.comparison_state, handoff.authority_state, handoff.evidence_state, handoff.repair_state}
    assert not states & {"verified", "reconciled", "proven", "applied", "promoted"}


@case("RS-SIM-02")
def test_document_pointers_do_not_count_as_app_execution(rt_case):
    x = rt_case["expected"]
    handoff = diagnose(rt_case["input"]["request"])
    assert handoff.execution_state == x["execution_state"]
    assert handoff.repair_state == x["repair_state"]


# -- SCH: malformed or hostile schema ------------------------------------------------


@case("RS-SCH-01")
def test_smuggled_fields_are_rejected(rt_case, tmp_path):
    i, x = rt_case["input"], rt_case["expected"]
    base = {"source_doc": "docs/r.md", "authority_status": "present",
            "conflicts": [{"claim_id": "c01", "protected_requirement_id": "R-1", "protected_text": "Must hold."}]}
    top = {**base, **i["extra_top"]}
    nested = copy.deepcopy(base)
    nested["conflicts"][0].update(i["extra_conflict"])
    for payload in (top, nested):
        with pytest.raises(ValidationError):
            ReconcileRequest.model_validate(payload)
        result = _cli(tmp_path, payload)
        assert result.exit_code == x["exit_code"]
        assert x["stderr_contains"] in result.output
    assert not (tmp_path / "out").exists()


@case("RS-SCH-02")
def test_malformed_request_shapes_are_rejected(rt_case):
    def base():
        return {"source_doc": "docs/r.md", "authority_status": "present",
                "conflicts": [{"claim_id": "c01", "protected_requirement_id": "R-1", "protected_text": "Must hold.",
                               "evidence": [{"kind": "runtime", "pointer": "p"}]}]}

    def mutate(name):
        r = base()
        c = r["conflicts"][0]
        if name == "empty_conflicts":
            r["conflicts"] = []
        elif name == "bad_claim_id":
            c["claim_id"] = "c1"
        elif name == "bad_candidate_side":
            c["candidate_side"] = "app_right"
        elif name == "empty_pointer":
            c["evidence"][0]["pointer"] = ""
        elif name == "bad_authority_status":
            r["authority_status"] = "approved"
        elif name == "missing_protected_text":
            del c["protected_text"]
        else:
            raise AssertionError(name)
        return r

    ReconcileRequest.model_validate(base())
    for name in rt_case["input"]["mutations"]:
        with pytest.raises(ValidationError):
            ReconcileRequest.model_validate(mutate(name))


@case("RS-SCH-03")
def test_missing_evidence_pointers_fail_closed(rt_case, tmp_path):
    i, x = rt_case["input"], rt_case["expected"]
    missing = diagnose(i["missing"])
    partial = diagnose(i["partial"])
    assert missing.evidence_state == x["missing"]
    assert partial.evidence_state == x["partial"]
    markdown, _ = write_handoff(missing, tmp_path)
    assert x["markdown_contains"] in markdown.read_text(encoding="utf-8")


@case("RS-SCH-04")
def test_handoff_without_evidence_pointers_fails_closed(rt_case, tmp_path, monkeypatch):
    i, x = rt_case["input"], rt_case["expected"]
    # 1. diagnosis: a conflict with no pointer blocks the handoff and the CLI
    for name, reasons, state in (("missing", x["missing_reasons"], "missing"),
                                 ("partial", x["partial_reasons"], x["partial_evidence_state"])):
        handoff = diagnose(i[name])
        assert handoff.evidence_state == state
        assert handoff.handoff_state == "blocked" and handoff.blocking_reasons == reasons
        run = tmp_path / name
        run.mkdir()
        assert _cli(run, i[name]).exit_code == x["blocked_exit_code"]

    # 2. schema: a handoff JSON cannot say ready without pointers, or blocked without a reason
    ready = diagnose(i["complete"]).model_dump()
    assert ready["handoff_state"] == "ready"
    for forged in ({**ready, "evidence_state": "missing"}, {**ready, "evidence_state": "incomplete"},
                   {**ready, "authority_state": "missing"}, {**ready, "blocking_reasons": ["c01:evidence_missing"]},
                   {**ready, "handoff_state": "blocked"}):
        with pytest.raises(ValidationError):
            ReconcileHandoff.model_validate(forged)

    # 3. package: a writer that drops submitted pointers fails the CLI, even for a complete request
    real_render = reconcile_spec_mod.render_handoff_markdown
    monkeypatch.setattr(reconcile_spec_mod, "render_handoff_markdown",
                        lambda h: "\n".join(line for line in real_render(h).splitlines() if not line.startswith("- `runtime`")))
    (tmp_path / "dropped_md").mkdir()
    result = _cli(tmp_path / "dropped_md", i["complete"])
    assert result.exit_code == x["package_exit_code"], result.output
    assert x["package_stderr_contains"] in result.output and x["dropped_markdown_reason"] in result.output
    monkeypatch.undo()

    real_write = reconcile_spec_mod.write_handoff

    def drop_json_pointers(handoff, out_dir):
        stripped = handoff.model_copy(update={"conflicts": [c.model_copy(update={"evidence": []}) for c in handoff.conflicts]})
        markdown, machine = real_write(handoff, out_dir)
        machine.write_text(stripped.model_dump_json(indent=2) + "\n", encoding="utf-8")
        return markdown, machine

    monkeypatch.setattr(reconcile_spec_mod, "write_handoff", drop_json_pointers)
    (tmp_path / "dropped_json").mkdir()
    result = _cli(tmp_path / "dropped_json", i["complete"])
    assert result.exit_code == x["package_exit_code"], result.output
    assert x["dropped_json_reason"] in result.output


@case("RS-SCH-05")
def test_blank_strings_and_duplicate_claim_ids_are_rejected(rt_case, tmp_path):
    i, x = rt_case["input"], rt_case["expected"]

    def mutate(name):
        r = copy.deepcopy(i["base"])
        c = r["conflicts"][0]
        if name == "blank_source_doc":
            r["source_doc"] = " \t"
        elif name == "blank_requirement_id":
            c["protected_requirement_id"] = "  "
        elif name == "blank_protected_text":
            c["protected_text"] = "\n"
        elif name == "blank_pointer":
            c["evidence"][0]["pointer"] = "   "
        elif name == "duplicate_claim_id":
            r["conflicts"].append(copy.deepcopy(c))
        else:
            raise AssertionError(name)
        return r

    ReconcileRequest.model_validate(i["base"])
    for name in i["mutations"]:
        with pytest.raises(ValidationError):
            ReconcileRequest.model_validate(mutate(name))
        run = tmp_path / name
        run.mkdir()
        result = _cli(run, mutate(name))
        assert result.exit_code == x["exit_code"], (name, result.output)
        assert x["stderr_contains"] in result.output
        assert not (run / "out").exists()


# -- CONTROL -------------------------------------------------------------------------


@case("RS-CTRL-01")
def test_complete_conflict_is_a_ready_handoff(rt_case, tmp_path):
    request, x = rt_case["input"]["request"], rt_case["expected"]
    _write_files(tmp_path / "src", rt_case["input"]["files"])
    result = _cli(tmp_path, request, "--source-root", str(tmp_path / "src"))
    assert result.exit_code == x["exit_code"], result.output
    assert "refused" not in result.output and "blocked" not in result.output
    data, markdown = _written(tmp_path)
    for key in ("authority_state", "evidence_state", "repair_state", "handoff_state", "blocking_reasons", "source_state"):
        assert data[key] == x[key], key
    contract = (tmp_path / "src" / "contracts" / "security.md").read_bytes()
    assert data["source_digests"] == {"contracts/security.md": hashlib.sha256(contract).hexdigest()}
    assert "Handoff: `ready`" in markdown
    pointers = [e["pointer"] for c in request["conflicts"] for e in c["evidence"]]
    assert all(p in markdown for p in pointers)


# -- COST ----------------------------------------------------------------------------


@case("RS-COST-01")
def test_large_request_is_bounded_and_never_fetches(rt_case, monkeypatch):
    n, x = rt_case["input"]["conflicts"], rt_case["expected"]
    request = {
        "source_doc": "docs/r.md",
        "authority_status": "present",
        "conflicts": [
            {"claim_id": f"c{k:04d}", "protected_requirement_id": f"R-{k}", "authority_reference": f"contracts/r.md#R-{k}",
             "protected_text": "Must hold.", "evidence": [{"kind": "runtime", "pointer": f"https://example.invalid/run/{k}"}]}
            for k in range(1, n + 1)
        ],
    }
    parsed = ReconcileRequest.model_validate(request)

    def refuse(*args, **kwargs):
        raise AssertionError("Reconcile Spec dereferenced an evidence pointer")

    monkeypatch.setattr(builtins, "open", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)
    started = time.monotonic()
    handoff = ReconcileSpec().diagnose(parsed)
    elapsed = time.monotonic() - started
    monkeypatch.undo()

    assert len(handoff.conflicts) == n
    assert handoff.repair_state == x["repair_state"]
    assert elapsed < x["max_seconds"]

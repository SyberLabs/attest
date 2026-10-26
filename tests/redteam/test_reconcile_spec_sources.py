"""Reconcile Spec red-team cases (FACTORY §1) on how the CLI reads a protected document: a
named pipe at its path, and the same contract saved as different bytes.

Inputs and expected invariants live in cases/reconcile_spec/<ID>.json. Every case runs
the ``attest reconcile-spec`` CLI against documents written under a source root.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import threading
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from attest.cli import app

from ._harness import case

REPAIR_STATES = {"refused", "handoff"}


def _run(tmp_path: Path, request: dict, files: dict[str, bytes] | None = None):
    """The CLI on ``request`` with ``files`` (bytes) under tmp_path/src; returns the result and the written JSON."""
    root = tmp_path / "src"
    root.mkdir(parents=True, exist_ok=True)
    for name, data in (files or {}).items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_bytes(data)
    path = tmp_path / "request.json"
    path.write_text(json.dumps(request), encoding="utf-8")
    out = tmp_path / "out"
    result = CliRunner().invoke(app, ["reconcile-spec", str(path), "--out", str(out), "--source-root", str(root)])
    written = out / "reconcile-spec.json"
    data = json.loads(written.read_text(encoding="utf-8")) if written.exists() else None
    if data is not None:
        assert data["repair_state"] in REPAIR_STATES  # R1/R4 on every case
    return result, data


# -- COST ----------------------------------------------------------------------------


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="named pipes are POSIX only")
@case("RS-COST-04")
def test_named_pipe_contract_is_refused_unopened(rt_case, tmp_path):
    i, x = rt_case["input"], rt_case["expected"]
    fifo = tmp_path / "src" / i["fifo"]
    fifo.parent.mkdir(parents=True)
    os.mkfifo(fifo)

    # A writer that only succeeds while something has the pipe open for reading. It records
    # that, and its open/close also releases a reader blocked on the pipe, so a regression
    # fails the assertion below instead of hanging the suite.
    done, opened = threading.Event(), []

    def watch() -> None:
        while not done.is_set():
            try:
                fd = os.open(fifo, os.O_WRONLY | os.O_NONBLOCK)
            except OSError as exc:
                if exc.errno != errno.ENXIO:  # ENXIO: no reader
                    raise
            else:
                opened.append(True)
                os.close(fd)
            time.sleep(0.01)

    watcher = threading.Thread(target=watch, daemon=True)
    watcher.start()
    started = time.monotonic()
    try:
        result, data = _run(tmp_path, i["request"])
    finally:
        done.set()
        watcher.join(timeout=5)
    assert time.monotonic() - started < x["max_seconds"]
    assert bool(opened) is x["reader_opened"]
    assert result.exit_code == x["exit_code"], result.output
    assert x["reason"] in data["blocking_reasons"]
    assert data["source_state"] == x["source_state"] and data["handoff_state"] == x["handoff_state"]
    assert data["source_digests"] == x["source_digests"]


# -- DS: identity confusion ----------------------------------------------------------


@case("RS-DS-06")
def test_each_save_of_a_contract_records_its_own_bytes(rt_case, tmp_path):
    i, x = rt_case["input"], rt_case["expected"]
    digests = {}
    for name, save in i["saves"].items():
        text = i["text"].replace("\n", "\r\n") if save["crlf"] else i["text"]
        data = (b"\xef\xbb\xbf" if save["bom"] else b"") + text.encode("utf-8")
        result, written = _run(tmp_path / name, i["request"], {i["document"]: data})
        assert result.exit_code == x["exit_code"], (name, result.output)
        assert written["handoff_state"] == x["handoff_state"] and written["source_state"] == x["source_state"], name
        on_disk = (tmp_path / name / "src" / i["document"]).read_bytes()
        assert on_disk == data, name
        recorded = written["source_digests"][i["document"]]
        assert (recorded == hashlib.sha256(on_disk).hexdigest()) is x["digest_of_bytes_on_disk"], name
        digests[name] = recorded
    assert len(set(digests.values())) == x["distinct_digests"]

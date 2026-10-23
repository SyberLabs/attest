from attest.ledger import Ledger
from attest.models import Claim, Evidence, LedgerRow, Span, Verdict


def claim(cid, kind="shell"):
    kw = {"command": "echo"} if kind == "shell" else ({"objective": "open"} if kind == "browser" else {})
    return Claim(id=cid, kind=kind, span=Span(start_line=1, end_line=1, text="x"), **kw)


def verdict(cid, status, kind="shell"):
    if status == "unverifiable":
        return Verdict(claim_id=cid, status=status, reason=status)
    ev = Evidence(claim_id=cid, kind="shell", exit_code=0)
    return Verdict(claim_id=cid, status=status, reason=status, evidence=ev)


def test_append_only_and_turns(tmp_path):
    led = Ledger(tmp_path / "ledger.jsonl")
    assert led.rows() == []
    assert led.next_turn() == 1
    claims = [claim("c01"), claim("c02", "browser"), claim("c03", "prose")]
    led.record("sha-a", 1, claims, [verdict("c01", "proven"), verdict("c02", "stale"), verdict("c03", "unverifiable")])
    assert led.next_turn() == 2
    led.record("sha-b", 2, [claim("c01")], [verdict("c01", "broken")])
    rows = led.rows()
    assert len(rows) == 4
    assert all(isinstance(r, LedgerRow) for r in rows)
    assert rows[1].kind == "browser"
    assert rows[2].evidence_kind is None
    # the file only grows
    text = (tmp_path / "ledger.jsonl").read_text()
    assert text.count("\n") == 4


def test_summary_counts_latest_row_per_claim(tmp_path):
    led = Ledger(tmp_path / "ledger.jsonl")
    claims = [claim("c01"), claim("c02"), claim("c03", "prose")]
    led.record("sha", 1, claims, [verdict("c01", "proven"), verdict("c02", "broken"), verdict("c03", "unverifiable")])
    s = led.summary("sha")
    assert s["turn"] == 1
    assert s["claims"] == 2  # prose is not badged
    assert (s["proven"], s["broken"], s["unverifiable"]) == (1, 1, 0)
    assert s["loops_attempted"] == 0 and s["loops_closed"] == 0
    # loop re-run: same turn, looped row for c02, now proven
    led.record("sha", 1, [claim("c02")], [verdict("c02", "proven")], looped=True)
    s = led.summary("sha", 1)
    assert (s["proven"], s["broken"]) == (2, 0)
    assert s["loops_attempted"] == 1 and s["loops_closed"] == 1
    assert s["rows"] == 4


def test_summary_is_per_doc_and_turn(tmp_path):
    led = Ledger(tmp_path / "ledger.jsonl")
    led.record("a", 1, [claim("c01")], [verdict("c01", "broken")])
    led.record("b", 2, [claim("c01")], [verdict("c01", "proven")])
    led.record("a", 3, [claim("c01")], [verdict("c01", "proven")])
    assert led.summary("a")["turn"] == 3
    assert led.summary("a", 1)["broken"] == 1
    assert led.summary("a", 3)["proven"] == 1
    assert led.latest_turn("b") == 2
    assert led.latest_turn("zzz") is None
    assert led.summary("zzz")["claims"] == 0


def test_session_summary(tmp_path):
    led = Ledger(tmp_path / "ledger.jsonl")
    led.record("a", 1, [claim("c01")], [verdict("c01", "stale")])
    led.record("a", 1, [claim("c01")], [verdict("c01", "proven")], looped=True)
    led.record("b", 2, [claim("c01")], [verdict("c01", "proven")])
    s = led.session_summary()
    assert s["turns"] == 2
    assert s["docs"] == 2
    assert s["rows"] == 3
    assert s["loops_closed"] == 1
    assert led.session_summary("1999-01-01")["rows"] == 0

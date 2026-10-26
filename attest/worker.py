"""Attest's OpenAI worker as a command: attest.ticket/v0 JSON on stdin, unified diff on stdout.

This is the worker a SyberWork dispatch configuration can route tickets to
(`{"type": "command", "cmd": ["{python}", "-m", "attest.worker"]}`). The model
and request options come from the environment as for the built-in worker
(ATTEST_AGENT_MODEL, ATTEST_OPENAI_SERVICE_TIER, ATTEST_REASONING_EFFORT).
"""

from __future__ import annotations

import json
import sys

from .implement import _openai_worker
from .loop import AGENT_TIMEOUT_S

# the top-level keys of attest.ticket/v0 (implement.build_ticket). Anything else, such as
# a caller's "success", "verified" or "evidence", is refused: SyberWork rejects unknown
# ticket fields, and its worker must not be the weaker side of the seam.
TICKET_KEYS = frozenset({"schema", "case_id", "title", "spec", "acceptance", "observed", "app", "output"})


def main() -> int:
    try:
        ticket = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        print(f"attest.worker: ticket is not JSON: {e}", file=sys.stderr)
        return 2
    if not isinstance(ticket, dict) or ticket.get("schema") != "attest.ticket/v0":
        print("attest.worker: expected an attest.ticket/v0 ticket", file=sys.stderr)
        return 2
    unknown = sorted(set(ticket) - TICKET_KEYS)
    if unknown:
        print(f"attest.worker: attest.ticket/v0 has no field(s) {', '.join(unknown)}; ticket refused", file=sys.stderr)
        return 2
    diff = _openai_worker(ticket, AGENT_TIMEOUT_S)
    if not diff or diff.startswith("openai worker error"):
        print(f"attest.worker: {diff or 'no change returned'}", file=sys.stderr)
        return 1
    sys.stdout.write(diff)
    return 0


if __name__ == "__main__":
    sys.exit(main())

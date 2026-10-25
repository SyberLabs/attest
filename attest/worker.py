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


def main() -> int:
    try:
        ticket = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        print(f"attest.worker: ticket is not JSON: {e}", file=sys.stderr)
        return 2
    if ticket.get("schema") != "attest.ticket/v0":
        print("attest.worker: expected an attest.ticket/v0 ticket", file=sys.stderr)
        return 2
    diff = _openai_worker(ticket, AGENT_TIMEOUT_S)
    if not diff or diff.startswith("openai worker error"):
        print(f"attest.worker: {diff or 'no change returned'}", file=sys.stderr)
        return 1
    sys.stdout.write(diff)
    return 0


if __name__ == "__main__":
    sys.exit(main())

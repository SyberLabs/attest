"""Kane CLI runner. STUB ONLY before event day.

The Kane CLI Hack Day rules require the Kane integration to be built on the day,
so this module holds nothing but the contract. Every method raises until
Saturday.

SATURDAY: implement KaneBrowserRunner so that it satisfies attest.runner.BrowserRunner.
What it must do, in order:

1. Take consecutive browser claims that share a base URL and write them as one
   test file in Kane's format, one step per claim, with each step storing the
   exact text it read under the claim's ``store_as`` key. A shell claim between
   two browser claims splits the file.
2. Run the test through the Kane CLI in agent mode and read its streamed events.
3. Locate the evidence pack for the run and pull out, per step: pass or fail,
   the stored values, the screenshot path, and the step remark.
4. Map each step back to its claim id and return a BrowserObservation:
   status from the step result, observed from the stored values, screenshot
   from the pack, note from the remark.
5. Detect healing: if the remark shows Kane re-authored the step to get past a
   mismatch, say so in the note so the verdict rules can cap the claim at stale.
6. close(): stop any browser Kane left open.

Everything above the BrowserObservation boundary (verdict rules, render, loop,
ledger) already works against FakeBrowserRunner and must not change.
"""

from __future__ import annotations

from .models import BrowserObservation, Claim


class KaneBrowserRunner:
    name = "kane"

    def __init__(self, *args, **kwargs) -> None:
        raise NotImplementedError("built on event day")

    def observe(self, claim: Claim, base_url: str | None) -> BrowserObservation:
        raise NotImplementedError("built on event day")

    def close(self) -> None:
        raise NotImplementedError("built on event day")

"""Sweep label_match thresholds over the corpus and print a confusion matrix per value.

Run:  python tests/fixtures/tune.py
The chosen value is recorded in rules.yaml and tests/fixtures/README.md.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from attest.verdict import Rules, judge, label_match, observed_text  # noqa: E402
from tests.corpus import load_corpus  # noqa: E402


def main() -> None:
    corpus = load_corpus()
    print("label_match scores for browser items that passed:")
    for item_id, claim, ev, label in corpus:
        if claim.kind == "browser" and ev and ev.observation.status == "passed" and claim.expects:
            seen = observed_text(claim, ev) or ""
            print(f"  {item_id:11} {label:7} {label_match(claim.expects, seen):.3f}  {claim.expects!r} vs {seen.strip()!r}")
    print()
    print("threshold  proven->proven  proven->stale  stale->stale  stale->proven")
    for t in [x / 100 for x in range(50, 100, 5)]:
        rules = Rules(label_match=t)
        tp = fn = tn = fp = 0
        for item_id, claim, ev, label in corpus:
            if label not in ("proven", "stale"):
                continue
            got = judge(claim, ev, {}, rules).status
            if label == "proven":
                if got == "proven":
                    tp += 1
                else:
                    fn += 1
            else:
                if got == "stale":
                    tn += 1
                else:
                    fp += 1
        flag = "  <- clean" if fn == 0 and fp == 0 else ""
        print(f"  {t:.2f}     {tp:>8}  {fn:>13}  {tn:>12}  {fp:>13}{flag}")


if __name__ == "__main__":
    main()

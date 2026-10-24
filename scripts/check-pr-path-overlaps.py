#!/usr/bin/env python3
"""List changed-path intersections for PR heads sharing a common base.

This is a path-overlap check, not a merge-conflict or semantic-compatibility test.
"""

from __future__ import annotations

import argparse
import subprocess
import sys


def git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout


def parse_pr(value: str) -> tuple[str, str]:
    number, separator, ref = value.partition("=")
    if not separator or not number.isdigit() or not ref:
        raise argparse.ArgumentTypeError("PR refs must use NUMBER=GIT_REF form")
    return number, ref


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, help="common base branch/ref")
    parser.add_argument(
        "--pr", action="append", required=True, type=parse_pr, metavar="NUMBER=REF",
        help="PR head ref; repeat for each pull request",
    )
    args = parser.parse_args()

    try:
        base = git("rev-parse", "--verify", args.base).strip()
        changed: dict[str, set[str]] = {}
        for number, ref in args.pr:
            git("rev-parse", "--verify", ref)
            paths = git("diff", "--name-only", "--no-renames", f"{base}...{ref}")
            changed[number] = {line for line in paths.splitlines() if line}
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    found = False
    numbers = list(changed)
    for index, left in enumerate(numbers):
        for right in numbers[index + 1 :]:
            overlap = sorted(changed[left] & changed[right])
            if overlap:
                found = True
                print(f"PR #{left} × PR #{right}")
                for path in overlap:
                    print(f"  {path}")
    if not found:
        print("No changed paths overlap across the supplied PR heads.")
    print("Path overlap is not proof of a merge conflict; rehearse ordered merges separately.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

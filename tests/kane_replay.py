"""Replay a recorded kane-cli capture as a Kane run of the test file Attest wrote.

The captures in tests/fixtures/kane/ were recorded on event day from hand-written smoke
files, so each pack's ``test.md`` carries that day's wording. Live Kane seals the file it
was given, and validation compares the pack's steps with the wording Attest writes for
the approved claims. ``as_written`` copies a recorded pack with its ``test.md`` replaced
by the file the runner wrote and ``result.yaml``'s definition digest updated to match.
Live Kane also stamps ``run.yaml`` with when it ran, and validation compares that with the
approval, so ``as_written`` stamps the replay's own time there. The recorded verdicts,
actions and stored values still come from the capture. None of this is live Kane.
"""

from __future__ import annotations

import hashlib
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path


def written_test_file(cmd: list[str]) -> Path:
    """The ``_test.md`` a ``kane-cli testmd run`` command names."""
    return Path(next(arg for arg in cmd if arg.endswith("_test.md")))


def rewrite_pack(src: Path, dest: Path, *, result=None, test_md=None, run=None, redigest: bool = False) -> Path:
    """Copy a pack with its result.yaml, test.md and/or run.yaml rewritten (each ``f(bytes) -> bytes``).

    ``redigest`` makes result.yaml digest the rewritten test.md, so the pack is consistent
    with itself.
    """
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dest, "w") as zout:
        for info in zin.infolist():  # each entry keeps its ZipInfo, so the same rewrite gives the same bytes
            name, data = info.filename, zin.read(info)
            if name.endswith("/result.yaml"):
                if result:
                    data = result(data)
                if redigest and test_md:
                    original = zin.read(name[: -len("result.yaml")] + "test.md")
                    old, new = (hashlib.sha256(t).hexdigest().encode() for t in (original, test_md(original)))
                    assert b"sha256:" + old in data, name
                    data = data.replace(b"sha256:" + old, b"sha256:" + new)
            if test_md and name.endswith("/test.md"):
                data = test_md(data)
            if run and name == "run.yaml":
                data = run(data)
            zout.writestr(info, data)
    return dest


def stamped(run_yaml: bytes, when: datetime | None = None) -> bytes:
    """``run.yaml`` with its run ``started`` and ``ended`` at ``when`` (default: now).

    Kane writes milliseconds; the replay writes microseconds, so a run replayed within the
    millisecond of an approval still reads as after it.
    """
    when = when or datetime.now(timezone.utc)
    iso = when.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
    out, n = re.subn(rb"(?m)^(started|ended): .*$", lambda m: m.group(1) + b": '" + iso.encode() + b"'", run_yaml)
    assert n == 2, run_yaml
    return out


def as_written(src: Path, dest: Path, written: bytes) -> Path:
    """A recorded pack as Kane would seal it for the test file ``written``, run now."""
    return rewrite_pack(src, dest, test_md=lambda _: written, run=stamped, redigest=True)

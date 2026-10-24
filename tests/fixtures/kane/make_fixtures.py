"""Turn the real Kane captures from the 10:00 smoke test into offline fixtures.

Inputs are gitignored (scratch/*.ndjson, .testmuai/evidence/*.evidence). Outputs
are sanitized: share links, account email and user paths are replaced; HAR files,
logs and the execution trajectory are left out of the fixture packs.

    python tests/fixtures/kane/make_fixtures.py
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent

# fixture name -> (captured NDJSON, pack execution id or None)
CAPTURES = {
    "run_pass": ("scratch/smoke.ndjson", None),
    "author_pass": ("scratch/smoke_testmd_1.ndjson", "3cbd6619-05db-4151-b31d-8882fa8897f8"),
    "replay_pass": ("scratch/smoke_testmd_2.ndjson", "e133ac15-574e-4e92-8abe-5023df4fe224"),
    "fail_stuck": ("scratch/smoke_testmd_3.ndjson", "a0068a45-065a-41ff-9317-4ed04896dd13"),
    "fail_assert": ("scratch/smoke_testmd_4.ndjson", "4fc59ef8-9589-4d52-830c-1f73a28c5850"),
    "witness_stale": ("scratch/smoke_testmd_5.ndjson", "adbb0605-5946-4c60-a909-a559757c41cd"),
    "drift_replay": ("scratch/heal_2.ndjson", "bcc58bd9-74d0-468c-90a2-0aca2268714d"),
}

_SUBS = [
    (re.compile(r"https://test-manager\.lambdatest\.com/[^\"'\s]*"), "https://test-manager.lambdatest.com/REDACTED"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "user@example.com"),
    (re.compile(r"(?i)C:(\\\\|\\|/)Users(\\\\|\\|/)[^\\/\"]+"), r"C:\1Users\2USER"),
    (re.compile(r"user_name: \S+"), "user_name: USER"),
    (re.compile(r"org_id: \S+"), "org_id: '0'"),
]

_KEEP = re.compile(r"(^run\.yaml$|^failure\.yaml$|/test\.md$|/result\.yaml$|/steps/[^/]+/(step\.json|failure\.yaml|screenshot\.jpg|annotated\.png)$)")


def sanitize(text: str) -> str:
    for pat, rep in _SUBS:
        text = pat.sub(rep, text)
    return text


def build_pack(src: Path, dest: Path) -> None:
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zout:
        for name in zin.namelist():
            if not _KEEP.search(name):
                continue
            data = zin.read(name)
            if not name.endswith((".jpg", ".png")):
                data = sanitize(data.decode("utf-8")).encode("utf-8")
            zout.writestr(name, data)


def main() -> None:
    for name, (ndjson, pack_id) in CAPTURES.items():
        text = (ROOT / ndjson).read_text(encoding="utf-8", errors="replace")
        (HERE / f"{name}.ndjson").write_text(sanitize(text), encoding="utf-8", newline="\n")
        if pack_id:
            build_pack(ROOT / ".testmuai" / "evidence" / f"{pack_id}.evidence", HERE / f"{name}.pack.zip")
        print("wrote", name)


if __name__ == "__main__":
    main()

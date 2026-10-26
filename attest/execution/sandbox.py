"""Filesystem containment helpers for untrusted Kane evidence packs."""

from __future__ import annotations

import shutil
import stat
import zipfile
from pathlib import Path, PurePosixPath


def safe_pack_path(root: Path | str, relative: str | Path) -> Path:
    """Resolve a pack-relative path and reject traversal or symlink escapes."""
    base = Path(root).resolve()
    raw = str(relative)
    if not raw or "\\" in raw or "\x00" in raw:
        raise ValueError(f"path is outside pack: {raw!r}")
    path = PurePosixPath(raw)
    if not path.parts or path.is_absolute() or any(part in ("..", ".", "") for part in path.parts) or ":" in path.parts[0]:
        raise ValueError(f"path is outside pack: {raw!r}")
    candidate = base.joinpath(*path.parts).resolve()
    try:
        candidate.relative_to(base)
    except ValueError as exc:
        raise ValueError(f"path is outside pack: {raw!r}") from exc
    return candidate


def extract_zip_safely(
    archive: Path | str,
    destination: Path | str,
    *,
    max_uncompressed_bytes: int = 250 * 1024 * 1024,
) -> Path:
    """Extract an evidence zip after validating every member's containment."""
    archive = Path(archive)
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    total_size = 0
    with zipfile.ZipFile(archive) as zf:
        members: list[tuple[zipfile.ZipInfo, Path]] = []
        for info in zf.infolist():
            if info.is_dir():
                relative = info.filename.rstrip("/")
                if not relative:
                    continue
            else:
                relative = info.filename
            target = safe_pack_path(destination, relative)
            mode = (info.external_attr >> 16) & 0xFFFF
            if stat.S_ISLNK(mode):
                raise ValueError(f"symlink is not allowed in evidence pack: {info.filename!r}")
            total_size += info.file_size
            if total_size > max_uncompressed_bytes:
                raise ValueError("evidence pack exceeds the extraction size limit")
            members.append((info, target))

        for info, target in members:
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)
    return destination

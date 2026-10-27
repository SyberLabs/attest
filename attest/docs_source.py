"""The approved Maintain Docs source, opened without following a symlink anywhere on its path.

A proposal stores its source as a resolved absolute path. Validation and ``--apply`` read
and write through that path, so a directory on it that is swapped for a symlink after
approval would redirect both: to a clone at the same commit, the source bytes and the
build identity still match, and the repair lands outside the approved tree. Here every
directory on the stored path is opened with ``O_NOFOLLOW``, the source is read and the
candidate is staged and renamed into place relative to that directory's descriptor, and
the stored path must still name that directory right before the rename.
"""

from __future__ import annotations

import errno
import os
import secrets
import stat
from pathlib import Path


class SourcePathError(RuntimeError):
    """The stored source path was refused; ``code`` is one of SOURCE_PATH_CODES."""

    def __init__(self, code: str, detail: str):
        super().__init__(f"source path refused ({code}): {detail}")
        self.code = code


SOURCE_PATH_CODES = ("source_not_canonical", "source_redirected", "source_symlink", "source_missing", "source_moved")

# The wording these refusals had before they carried a code (and that #64's MD-DS-07 matches).
_MISSING_OR_SYMLINK = "source file is missing or is a symlink"

_DIR_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
# Windows has no O_NOFOLLOW and no dir_fd: there the path is checked, not pinned.
_PINNED = (
    hasattr(os, "O_NOFOLLOW")
    and hasattr(os, "O_DIRECTORY")
    and {os.open, os.stat, os.unlink, os.rename} <= os.supports_dir_fd  # os.replace is renameat too
)


def _canonical(source_path: str | Path) -> Path:
    raw = os.fspath(source_path)
    path = Path(raw)
    if not path.is_absolute() or os.path.normpath(raw) != raw or path.name in {"", ".", ".."}:
        raise SourcePathError("source_not_canonical", f"{raw!r} is not the absolute, normalized path a proposal stores")
    return path


def _walk(path: Path) -> int:
    """Open each directory on ``path`` from the root down, refusing one that is a symlink."""
    fd = os.open(path.anchor, _DIR_FLAGS)
    try:
        for part in path.parent.parts[1:]:
            try:
                child = os.open(part, _DIR_FLAGS, dir_fd=fd)
            except OSError as exc:
                if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                    raise SourcePathError(
                        "source_redirected", f"{part!r} on the approved source path is no longer a directory (a symlink?)"
                    ) from exc
                raise SourcePathError("source_missing", f"{_MISSING_OR_SYMLINK} ({part!r}: {exc.strerror})") from exc
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def _check_unpinned(path: Path) -> None:
    for parent in reversed(path.parents):
        if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
            raise SourcePathError("source_redirected", f"{str(parent)!r} on the approved source path is no longer a directory")
        if not parent.exists():
            raise SourcePathError("source_missing", f"{_MISSING_OR_SYMLINK} ({str(parent)!r} is missing)")
    if path.is_symlink():
        raise SourcePathError("source_symlink", f"{_MISSING_OR_SYMLINK} (the file itself is a symlink)")
    if not path.is_file():
        raise SourcePathError("source_missing", _MISSING_OR_SYMLINK)


class SourceDir:
    """The approved source's directory, held open while it is read and written.

    Use as a context manager; every method raises SourcePathError (or OSError) and never
    follows a symlink to the source.
    """

    def __init__(self, source_path: str | Path):
        self.path = _canonical(source_path)
        self.name = self.path.name
        self.fd: int | None = None

    def __enter__(self) -> "SourceDir":
        if not _PINNED:
            _check_unpinned(self.path)
            return self
        self.fd = _walk(self.path)
        try:
            self._regular_file()
        except BaseException:
            self.close()
            raise
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def _regular_file(self) -> os.stat_result:
        try:
            st = os.stat(self.name, dir_fd=self.fd, follow_symlinks=False)
        except FileNotFoundError as exc:
            raise SourcePathError("source_missing", _MISSING_OR_SYMLINK) from exc
        if stat.S_ISLNK(st.st_mode):
            raise SourcePathError("source_symlink", f"{_MISSING_OR_SYMLINK} (the file itself is a symlink)")
        if not stat.S_ISREG(st.st_mode):
            raise SourcePathError("source_missing", f"{_MISSING_OR_SYMLINK} (not a regular file)")
        return st

    def mode(self) -> int:
        if self.fd is None:
            return stat.S_IMODE(self.path.stat().st_mode)
        return stat.S_IMODE(self._regular_file().st_mode)

    def read(self) -> bytes:
        if self.fd is None:
            _check_unpinned(self.path)
            return self.path.read_bytes()
        try:
            fd = os.open(self.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=self.fd)
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise SourcePathError("source_symlink", f"{_MISSING_OR_SYMLINK} (the file itself is a symlink)") from exc
            raise SourcePathError("source_missing", f"{_MISSING_OR_SYMLINK} ({exc.strerror})") from exc
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise SourcePathError("source_missing", f"{_MISSING_OR_SYMLINK} (not a regular file)")
            return stream.read()

    def still_named(self) -> None:
        """The stored path still leads, with no symlink, to the directory held open."""
        if self.fd is None:
            _check_unpinned(self.path)
            return
        fresh = _walk(self.path)
        try:
            held, now = os.fstat(self.fd), os.fstat(fresh)
        finally:
            os.close(fresh)
        if (held.st_dev, held.st_ino) != (now.st_dev, now.st_ino):
            raise SourcePathError("source_moved", "the approved source's directory is no longer at the approved path")

    def replace(self, data: bytes, mode: int, *, prefix: str, before=None) -> None:
        """Stage ``data`` beside the source and rename it over the source.

        ``before`` runs after staging, right before the rename, and may raise to stop it.
        """
        staged = f".{self.name}.{prefix}{secrets.token_hex(8)}"
        if self.fd is None:
            target = self.path.parent / staged
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        else:
            fd = os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=self.fd)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
                if self.fd is not None:
                    os.fchmod(stream.fileno(), mode)
            if self.fd is None:
                os.chmod(self.path.parent / staged, mode)
            if before is not None:
                before()
            self.still_named()
            if self.fd is None:
                os.replace(self.path.parent / staged, self.path)
            else:
                os.replace(staged, self.name, src_dir_fd=self.fd, dst_dir_fd=self.fd)
        finally:
            try:
                if self.fd is None:
                    (self.path.parent / staged).unlink(missing_ok=True)
                else:
                    os.unlink(staged, dir_fd=self.fd)
            except FileNotFoundError:
                pass

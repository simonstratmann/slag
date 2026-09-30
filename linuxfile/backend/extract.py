"""Extract selected tree nodes into a target folder *without* their parent folders.

7z always recreates the full archive path, so the selection is extracted into a hidden
staging folder inside the target (same filesystem -> renames are cheap and atomic) and
then only the selected items are moved up into the target. Existing items are merged
(folders) or resolved via a conflict callback (files).
"""

from __future__ import annotations

import ctypes
import enum
import errno
import os
import shutil
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, Sequence

from .sevenzip import (
    Cancelled, NotAnArchive, PasswordRequired, ProgressCallback, SevenZip, SevenZipError,
)
from .tree import Node


class ConflictAction(enum.Enum):
    OVERWRITE = "overwrite"
    SKIP = "skip"
    RENAME = "rename"
    CANCEL = "cancel"


# (existing destination, incoming source) -> action
ConflictCallback = Callable[[Path, Path], ConflictAction]

STAGING_PREFIX = ".linuxfile-extract-"


@dataclass
class ExtractResult:
    extracted: list[Path] = field(default_factory=list)
    skipped: list[Path] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)  # archive paths 7z did not produce
    errors: list[str] = field(default_factory=list)  # 7z errors (items may be missing)
    cancelled: bool = False  # cancelled while moving; ``extracted`` lists what was done


def unique_name(path: Path) -> Path:
    """'name.ext' -> 'name (1).ext', 'name (2).ext', ... (first one that does not exist)."""
    parent = path.parent
    name = path.name
    if not _is_real_dir(path) and "." in name[1:]:
        stem, ext = name.rsplit(".", 1)
        ext = "." + ext
    else:
        stem, ext = name, ""
    i = 1
    while True:
        candidate = parent / f"{stem} ({i}){ext}"
        if not os.path.lexists(candidate):
            return candidate
        i += 1


_libc = None
_RENAME_NOREPLACE = 1
_AT_FDCWD = -100


def _rename_noreplace(src: Path, dst: Path) -> None:
    """rename() that fails with FileExistsError instead of replacing ``dst``."""
    global _libc
    if _libc is None:
        try:
            _libc = ctypes.CDLL(None, use_errno=True)
            _libc.renameat2  # noqa: B018 - probe
        except (OSError, AttributeError):
            _libc = False
    if _libc:
        res = _libc.renameat2(_AT_FDCWD, os.fsencode(src), _AT_FDCWD, os.fsencode(dst),
                              _RENAME_NOREPLACE)
        if res == 0:
            return
        err = ctypes.get_errno()
        if err == errno.EEXIST:
            raise FileExistsError(err, os.strerror(err), str(dst))
        if err not in (errno.EINVAL, errno.ENOSYS):
            raise OSError(err, os.strerror(err), str(src), None, str(dst))
    # Filesystem without RENAME_NOREPLACE support: best effort.
    if os.path.lexists(dst):
        raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST), str(dst))
    os.rename(src, dst)


def _is_real_dir(p: Path) -> bool:
    return p.is_dir() and not p.is_symlink()


def _remove(p: Path) -> None:
    if _is_real_dir(p):
        shutil.rmtree(p)
    else:
        p.unlink()


def _replace(src: Path, dst: Path) -> None:
    """Replace ``dst`` by ``src`` without losing ``dst`` if the move fails."""
    aside = unique_name(dst.with_name(f".{dst.name}.linuxfile-old"))
    os.rename(dst, aside)
    try:
        _rename_noreplace(src, dst)
    except BaseException:
        os.rename(aside, dst)
        raise
    _remove(aside)


def _move(src: Path, dst: Path, on_conflict: ConflictCallback, result: ExtractResult, top: bool) -> None:
    if not os.path.lexists(dst):
        try:
            _rename_noreplace(src, dst)
            if top:
                result.extracted.append(dst)
            return
        except FileExistsError:
            pass  # created concurrently: treat as a conflict
    if _is_real_dir(src) and _is_real_dir(dst):
        # Merge folder contents.
        for child in sorted(src.iterdir()):
            _move(child, dst / child.name, on_conflict, result, top=False)
        if top:
            result.extracted.append(dst)
        return
    action = on_conflict(dst, src)
    if action is ConflictAction.CANCEL:
        raise Cancelled()
    if action is ConflictAction.SKIP:
        result.skipped.append(dst)
        return
    if action is ConflictAction.OVERWRITE:
        _replace(src, dst)
        if top:
            result.extracted.append(dst)
        return
    if action is ConflictAction.RENAME:
        while True:
            new_dst = unique_name(dst)
            try:
                _rename_noreplace(src, new_dst)
                break
            except FileExistsError:
                continue
        if top:
            result.extracted.append(new_dst)
        return
    raise ValueError(action)


def extract_nodes(
    sevenzip: SevenZip,
    archive: str,
    nodes: Sequence[Node],
    target: str | os.PathLike,
    on_conflict: ConflictCallback,
    password: str = "",
    progress: Optional[ProgressCallback] = None,
    cancel: Optional[threading.Event] = None,
) -> ExtractResult:
    """Extract ``nodes`` (siblings or not) directly into ``target``."""
    target = Path(target)
    target.mkdir(parents=True, exist_ok=True)
    result = ExtractResult()
    if not nodes:
        return result

    raw_paths: list[str] = []
    for node in nodes:
        raw_paths.extend(node.iter_raw_paths())
    for node in nodes:
        # 7z can only create a hard link when its target is extracted as well. The
        # target lands in staging only and is not moved unless it was selected.
        raw_paths.extend(node.iter_hard_link_targets())

    staging = Path(tempfile.mkdtemp(prefix=STAGING_PREFIX, dir=target))
    try:
        try:
            sevenzip.extract(archive, str(staging), raw_paths, password=password,
                             progress=progress, cancel=cancel)
        except (PasswordRequired, NotAnArchive):
            raise
        except SevenZipError as exc:
            # E.g. a CRC error in one file: keep everything else that was extracted.
            result.errors.append(str(exc))
        for node in nodes:
            if cancel is not None and cancel.is_set():
                result.cancelled = True
                break
            src = staging.joinpath(*node.components)
            if not os.path.lexists(src):
                result.missing.append(node.path)
                continue
            try:
                _move(src, target / node.name, on_conflict, result, top=True)
            except Cancelled:
                result.cancelled = True
                break
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return result

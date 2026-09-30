"""File name helpers: split volumes and known archive extensions."""

from __future__ import annotations

import os
import re

_PART_RAR_RE = re.compile(r"^(?P<stem>.*)\.part(?P<num>\d+)\.rar$", re.IGNORECASE)
_NUMBERED_RE = re.compile(r"^(?P<stem>.*)\.(?P<num>\d{3,})$")
_ZIP_SPLIT_RE = re.compile(r"^(?P<stem>.*)\.z(?P<num>\d{2,})$", re.IGNORECASE)
_RAR_OLD_RE = re.compile(r"^(?P<stem>.*)\.r(?P<num>\d{2,})$", re.IGNORECASE)


def _existing(directory: str, name: str) -> str | None:
    """Return the actual file name matching ``name`` case-insensitively, if any."""
    if os.path.exists(os.path.join(directory, name)):
        return name
    try:
        lower = name.lower()
        for candidate in os.listdir(directory or "."):
            if candidate.lower() == lower:
                return candidate
    except OSError:
        pass
    return None


def first_volume(path: str) -> str:
    """Map any volume of a split archive to the volume 7z has to be pointed at.

    Handles ``name.partN.rar`` -> ``name.part1.rar`` (keeping the zero padding),
    ``name.NNN`` -> ``name.001``, ``name.zNN`` -> ``name.zip`` and
    ``name.rNN`` -> ``name.rar``. Returns ``path`` unchanged when no first volume exists.
    """
    directory, name = os.path.split(path)

    m = _PART_RAR_RE.match(name)
    if m:
        width = len(m.group("num"))
        candidate = f"{m.group('stem')}.part{1:0{width}d}.rar"
        found = _existing(directory, candidate)
        return os.path.join(directory, found) if found else path

    m = _NUMBERED_RE.match(name)
    if m:
        width = len(m.group("num"))
        candidate = f"{m.group('stem')}.{1:0{width}d}"
        found = _existing(directory, candidate)
        return os.path.join(directory, found) if found else path

    for regex, ext in ((_ZIP_SPLIT_RE, "zip"), (_RAR_OLD_RE, "rar")):
        m = regex.match(name)
        if m:
            found = _existing(directory, f"{m.group('stem')}.{ext}")
            return os.path.join(directory, found) if found else path

    return path


# Extensions that Enter opens inside the application (like 7-Zip does).
ARCHIVE_EXTENSIONS = {
    "7z", "zip", "zipx", "rar", "tar", "gz", "tgz", "bz2", "tbz", "tbz2", "xz", "txz",
    "zst", "tzst", "lz", "tlz", "lzma", "tlzma", "z", "taz", "cab", "arj", "lzh", "lha",
    "iso", "wim", "cpio", "rpm", "deb", "001", "cbz", "cbr", "cb7", "cbt",
}


def is_archive_name(name: str) -> bool:
    lower = name.lower()
    if _PART_RAR_RE.match(lower) or _ZIP_SPLIT_RE.match(lower) or _RAR_OLD_RE.match(lower):
        return True
    if _NUMBERED_RE.match(lower):
        return True
    ext = lower.rsplit(".", 1)[-1] if "." in lower else ""
    return ext in ARCHIVE_EXTENSIONS


# Single-stream compression formats: an archive of this type containing exactly one
# entry is unwrapped automatically when that entry is itself an archive (tar.gz etc.).
STREAM_TYPES = {"gzip", "bzip2", "xz", "zstd", "z", "lzma", "lzma86", "lz", "lzip", "brotli", "lz4"}

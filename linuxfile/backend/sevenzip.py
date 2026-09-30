"""Thin wrapper around the 7-Zip command line tool.

Every call passes ``-p`` (empty password unless one is given) so 7z never blocks on an
interactive password prompt, and extraction uses ``-spd`` with a UTF-8 list file so raw
archive paths are matched exactly instead of being interpreted as wildcards.
"""

from __future__ import annotations

import os
import re
import selectors
import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Iterable, Optional

ProgressCallback = Callable[[int], None]


class SevenZipError(Exception):
    """Generic failure reported by 7z."""


class SevenZipNotFound(SevenZipError):
    pass


class NotAnArchive(SevenZipError):
    pass


class PasswordRequired(SevenZipError):
    """The archive (or an entry) is encrypted and no or a wrong password was given."""

    def __init__(self, message: str, wrong_password: bool):
        super().__init__(message)
        self.wrong_password = wrong_password


class Cancelled(Exception):
    pass


@dataclass
class Entry:
    path: str  # raw path as reported by 7z
    is_dir: bool
    size: int = 0
    packed_size: Optional[int] = None
    mtime: Optional[datetime] = None
    attributes: str = ""
    encrypted: bool = False
    crc: str = ""
    method: str = ""
    hard_link: str = ""  # raw path of the hard link target (tar)
    symlink: str = ""


@dataclass
class Listing:
    archive_type: str
    entries: list[Entry]
    properties: dict[str, str] = field(default_factory=dict)
    # Set when 7z reported errors but still listed entries (damaged archive).
    warning: str = ""


def find_7z() -> str:
    for name in ("7z", "7zz", "7za"):
        exe = shutil.which(name)
        if exe:
            return exe
    raise SevenZipNotFound("7-Zip (7z) was not found. Install the '7zip' package.")


_PERCENT_RE = re.compile(rb"(\d{1,3})%")


def _run(
    args: list[str],
    progress: Optional[ProgressCallback] = None,
    cancel: Optional[threading.Event] = None,
) -> tuple[int, str, str]:
    """Run 7z, streaming stdout/stderr, reporting progress and honouring cancellation."""
    env = dict(os.environ)
    env.setdefault("LANG", "C.UTF-8")
    proc = subprocess.Popen(
        args,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    out = bytearray()
    err = bytearray()
    sel = selectors.DefaultSelector()
    assert proc.stdout is not None and proc.stderr is not None
    sel.register(proc.stdout, selectors.EVENT_READ, out)
    sel.register(proc.stderr, selectors.EVENT_READ, err)
    last_percent = -1
    open_streams = 2
    try:
        while open_streams:
            if cancel is not None and cancel.is_set():
                proc.kill()
                proc.wait()
                raise Cancelled()
            for key, _ in sel.select(timeout=0.1):
                chunk = os.read(key.fileobj.fileno(), 65536)  # type: ignore[union-attr]
                if not chunk:
                    sel.unregister(key.fileobj)
                    open_streams -= 1
                    continue
                buf: bytearray = key.data
                if progress is not None and buf is out:
                    # Progress output is backspace-separated noise; only keep a short tail
                    # so a percentage split across two reads is still recognized.
                    tail = bytes(out[-8:]) + chunk
                    matches = _PERCENT_RE.findall(tail)
                    if matches:
                        percent = int(matches[-1])
                        if last_percent < percent <= 100:
                            last_percent = percent
                            progress(percent)
                    del out[:]
                    out.extend(tail[-8:])
                    continue
                buf.extend(chunk)
        rc = proc.wait()
    finally:
        sel.close()
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    return rc, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")


def _raise_for_error(rc: int, stdout: str, stderr: str, password: str) -> None:
    if rc == 0:
        return
    # Only look at stderr: stdout may contain file names that look like messages.
    text = stderr
    if "Wrong password" in text or "Cannot open encrypted archive" in text:
        raise PasswordRequired(
            "Wrong password." if password else "This archive is password protected.",
            wrong_password=bool(password),
        )
    if ("Cannot open the file as archive" in text or "Can not open the file as archive" in text
            or "Is not archive" in text):
        raise NotAnArchive("The file is not a supported archive.")
    lines = [l.strip() for l in stderr.split("\n") if l.strip()]
    message = "\n".join(lines) or f"7z failed with exit code {rc}"
    if "Unexpected end of archive" in text or "Missing volume" in text:
        message = ("The archive is truncated or a volume of a split archive is missing.\n\n"
                   + message)
    raise SevenZipError(message)


def _parse_mtime(value: str) -> Optional[datetime]:
    value = value.strip()
    if not value:
        return None
    # "2026-09-30 09:20:50" optionally followed by fractional seconds
    try:
        return datetime.strptime(value[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _parse_int(value: str) -> Optional[int]:
    value = value.strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def parse_listing(stdout: str) -> Listing:
    """Parse the output of ``7z l -slt``."""
    # Not splitlines(): that also splits on \r, \x1c, \x85, ... which are valid in names.
    lines = stdout.split("\n")
    archive_props: dict[str, str] = {}
    entries: list[Entry] = []
    i = 0
    n = len(lines)
    # Archive properties: block after a line consisting of "--"
    while i < n and lines[i].strip() != "--":
        i += 1
    i += 1
    while i < n and lines[i].strip() and lines[i].strip() != "----------":
        key, sep, value = lines[i].partition(" = ")
        i += 1
        if sep:
            if value == "" and i < n and lines[i] == "{":
                # Multi-line value (archive comment): skip it, it may contain anything.
                i += 1
                while i < n and lines[i] != "}":
                    i += 1
                i += 1
                continue
            archive_props[key.strip()] = value
    # Entries: blocks after "----------", separated by blank lines
    while i < n and lines[i].strip() != "----------":
        i += 1
    i += 1
    block: dict[str, str] = {}

    def flush() -> None:
        if "Path" not in block:
            block.clear()
            return
        attrs = block.get("Attributes", "")
        is_dir = block.get("Folder", "").strip() == "+" or attrs.startswith("D")
        entries.append(
            Entry(
                path=block["Path"],
                is_dir=is_dir,
                size=_parse_int(block.get("Size", "")) or 0,
                packed_size=_parse_int(block.get("Packed Size", "")),
                mtime=_parse_mtime(block.get("Modified", "")),
                attributes=attrs.strip(),
                encrypted=block.get("Encrypted", "").strip() == "+",
                crc=block.get("CRC", "").strip(),
                method=block.get("Method", "").strip(),
                hard_link=block.get("Hard Link", ""),
                symlink=block.get("Symbolic Link", ""),
            )
        )
        block.clear()

    while i < n:
        line = lines[i]
        i += 1
        if not line.strip():
            flush()
            continue
        key, sep, value = line.partition(" = ")
        if sep and value == "" and i < n and lines[i] == "{":
            # Multi-line value (entry comment)
            while i < n and lines[i] != "}":
                i += 1
            i += 1
            continue
        if not sep:
            # Trailing summary / warnings after the entry list
            if line.startswith(("Warnings:", "Errors:", "ERROR", "WARNING")):
                flush()
                break
            continue
        key = key.strip()
        if key == "Path" and "Path" in block:
            flush()
        block[key] = value
    flush()
    return Listing(
        archive_type=archive_props.get("Type", "").strip(),
        entries=entries,
        properties=archive_props,
    )


class SevenZip:
    def __init__(self, exe: Optional[str] = None):
        self.exe = exe or find_7z()

    def list(self, archive: str, password: str = "", cancel: Optional[threading.Event] = None) -> Listing:
        args = [self.exe, "l", "-slt", "-sccUTF-8", f"-p{password}", "--", archive]
        rc, out, err = _run(args, cancel=cancel)
        try:
            _raise_for_error(rc, out, err, password)
        except (PasswordRequired, NotAnArchive):
            raise
        except SevenZipError as exc:
            # Damaged archive: show what could be listed, like 7-Zip does.
            listing = parse_listing(out)
            if not listing.entries:
                raise
            listing.warning = str(exc)
            return listing
        return parse_listing(out)

    def extract(
        self,
        archive: str,
        out_dir: str,
        raw_paths: Optional[Iterable[str]] = None,
        password: str = "",
        progress: Optional[ProgressCallback] = None,
        cancel: Optional[threading.Event] = None,
    ) -> None:
        """Extract ``raw_paths`` (or everything when None) with full paths into ``out_dir``."""
        args = [
            self.exe, "x", "-y", "-aoa", "-spd", "-sccUTF-8", "-scsUTF-8",
            "-bso0", "-bsp1", f"-p{password}", f"-o{out_dir}",
        ]
        list_file = None
        try:
            if raw_paths is not None:
                # A line based list file can't express names containing a newline;
                # those are left out (callers report them as missing).
                paths = [p for p in dict.fromkeys(raw_paths) if "\n" not in p and "\r" not in p]
                if not paths:
                    return
                fd, list_file = tempfile.mkstemp(prefix="linuxfile-", suffix=".lst")
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    for p in paths:
                        # Quoted, otherwise 7z strips leading/trailing spaces.
                        fh.write(f'"{p}"\n')
                args.append(f"-i@{list_file}")
            args += ["--", archive]
            rc, out, err = _run(args, progress=progress, cancel=cancel)
            _raise_for_error(rc, out, err, password)
        finally:
            if list_file:
                os.unlink(list_file)

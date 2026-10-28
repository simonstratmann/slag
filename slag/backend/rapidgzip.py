"""Parallel gzip decompression with the rapidgzip command line tool, when installed.

7z decompresses gzip on one core; rapidgzip uses all of them, which makes opening a big
.tar.gz many times faster. Anything rapidgzip does not handle cleanly is left to 7z.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
from typing import Optional

from .sevenzip import Cancelled, ProgressCallback

# gzip stores the uncompressed size modulo 2^32.
_ISIZE_WRAP = 1 << 32


def find_rapidgzip() -> Optional[str]:
    return shutil.which("rapidgzip")


def decompress(
    exe: str,
    archive: str,
    out_path: str,
    isize: int,
    progress: Optional[ProgressCallback] = None,
    cancel: Optional[threading.Event] = None,
) -> bool:
    """Decompress the gzip file ``archive`` to ``out_path``.

    ``isize`` is the size stored in the gzip trailer (as listed by 7z). Returns False,
    with ``out_path`` removed, when rapidgzip failed or the output does not match
    ``isize``: rapidgzip exits successfully on a truncated file.
    """
    # Smallest size matching the trailer that is not below the compressed size (a
    # .tar.gz practically never expands); raised while the output grows past it.
    expected = isize
    while expected < os.path.getsize(archive):
        expected += _ISIZE_WRAP
    proc = subprocess.Popen(
        [exe, "-d", "-q", "-f", "-o", out_path, "--", archive],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    last_percent = -1
    try:
        while True:
            try:
                rc = proc.wait(timeout=0.1)
                break
            except subprocess.TimeoutExpired:
                pass
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            if progress is not None:
                try:
                    written = os.path.getsize(out_path)
                except OSError:
                    continue
                while written > expected:
                    expected += _ISIZE_WRAP
                percent = written * 100 // expected if expected else 0
                if last_percent < percent <= 100:
                    last_percent = percent
                    progress(percent)
    except BaseException:
        proc.kill()
        proc.wait()
        _remove(out_path)
        raise
    try:
        ok = rc == 0 and os.path.getsize(out_path) % _ISIZE_WRAP == isize
    except OSError:
        ok = False
    if not ok:
        _remove(out_path)
    return ok


def _remove(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass

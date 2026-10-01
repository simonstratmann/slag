"""Which commit of SLAG is running (read once, when first asked)."""

from __future__ import annotations

import functools
import os
import subprocess
from dataclasses import dataclass
from typing import Optional

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@dataclass(frozen=True)
class BuildInfo:
    describe: str  # e.g. "447e53f" or "447e53f-dirty"
    commit: str
    date: str  # commit date, YYYY-MM-DD
    repo: str

    @property
    def text(self) -> str:
        return f"{self.describe} · {self.date}"

    @property
    def tooltip(self) -> str:
        return f"Commit {self.commit} ({self.date})\n{self.repo}"


def _git(repo: str, *args: str) -> str:
    # safe.directory: the checkout may belong to another user (launcher installed for
    # a second account), which git otherwise refuses.
    out = subprocess.run(
        ["git", "-c", f"safe.directory={repo}", "-C", repo, *args],
        capture_output=True, text=True, timeout=5, check=True,
    )
    return out.stdout.strip()


@functools.lru_cache(maxsize=None)
def build_info(repo: str = REPO) -> Optional[BuildInfo]:
    """Commit of the checkout SLAG runs from; None when not run from a git checkout."""
    if not os.path.exists(os.path.join(repo, ".git")):
        return None
    try:
        describe = _git(repo, "describe", "--always", "--dirty", "--abbrev=7")
        commit, date = _git(repo, "log", "-1", "--format=%H %cs").split()
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    return BuildInfo(describe, commit, date, repo)

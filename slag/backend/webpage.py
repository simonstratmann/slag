"""Web pages inside archives: which part of the archive a page needs next to it."""

from __future__ import annotations

import re

from .tree import Node

# Opened from a copy of (part of) the archive, so relative links to other files work.
WEB_PAGE_EXTENSIONS = (".html", ".htm", ".xhtml", ".shtml")

# href="../../x.css", src='../a.js', url(../img.png), ...: the leading "../" run.
_UP_LINK_RE = re.compile(
    r"""(?:\b(?:href|src|srcset|poster|data|action|background)\s*=\s*["']?|url\(\s*["']?)"""
    r"""((?:\.\./)+)""",
    re.IGNORECASE,
)


def is_web_page(name: str) -> bool:
    return name.lower().endswith(WEB_PAGE_EXTENSIONS)


def up_levels(page: str) -> int:
    """How many folders above its own the page refers to (most leading '../' in a link)."""
    return max((len(m.group(1)) // 3 for m in _UP_LINK_RE.finditer(page)), default=0)


def page_folder(node: Node, page: str) -> Node:
    """Folder to extract with ``node`` so that the relative links of ``page`` work.

    Only links in the page itself are seen; '../' in its CSS or scripts is not.
    """
    folder = node.parent
    assert folder is not None
    for _ in range(up_levels(page)):
        if folder.parent is None:
            break
        folder = folder.parent
    return folder

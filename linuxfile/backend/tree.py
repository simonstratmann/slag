"""Turn the flat entry list reported by 7z into a directory tree."""

from __future__ import annotations

from datetime import datetime
from typing import Iterable, Iterator, Optional

from .sevenzip import Entry


def normalize_components(raw_path: str) -> list[str]:
    """Split a raw archive path into the components 7z uses when extracting.

    7z drops empty, "." and ".." components (e.g. "./a", "/abs/a", "../a" all end up
    below the output directory), so the tree must do the same to predict where an
    entry lands on disk. On Linux a backslash is an ordinary character for 7z.
    """
    parts = raw_path.split("/")
    return [p for p in parts if p not in ("", ".", "..")]


class Node:
    __slots__ = (
        "name", "is_dir", "size", "packed_size", "mtime", "attributes",
        "encrypted", "crc", "method", "hard_link", "symlink", "raw_paths", "parent", "children", "_total_size",
    )

    def __init__(self, name: str, is_dir: bool, parent: Optional["Node"] = None):
        self.name = name
        self.is_dir = is_dir
        self.size = 0
        self.packed_size: Optional[int] = None
        self.mtime: Optional[datetime] = None
        self.attributes = ""
        self.encrypted = False
        self.crc = ""
        self.method = ""
        self.hard_link = ""
        self.symlink = ""
        # Raw 7z paths of the entries that map onto this node (normally 0 or 1).
        self.raw_paths: list[str] = []
        self.parent = parent
        self.children: dict[str, Node] = {}
        self._total_size: Optional[int] = None

    def __repr__(self) -> str:
        return f"Node({self.path!r}, dir={self.is_dir})"

    @property
    def path(self) -> str:
        """Normalized path inside the archive ('' for the root)."""
        parts = []
        node: Optional[Node] = self
        while node is not None and node.parent is not None:
            parts.append(node.name)
            node = node.parent
        return "/".join(reversed(parts))

    @property
    def components(self) -> list[str]:
        p = self.path
        return p.split("/") if p else []

    def child(self, name: str) -> Optional["Node"]:
        return self.children.get(name)

    def find(self, path: str) -> Optional["Node"]:
        node: Optional[Node] = self
        for part in normalize_components(path):
            if node is None:
                return None
            node = node.children.get(part)
        return node

    def walk(self) -> Iterator["Node"]:
        yield self
        for c in self.children.values():
            yield from c.walk()

    def iter_raw_paths(self) -> Iterator[str]:
        for n in self.walk():
            yield from n.raw_paths

    def iter_hard_link_targets(self) -> Iterator[str]:
        """Raw paths that hard links in this subtree point to (7z needs them extracted)."""
        for n in self.walk():
            if n.hard_link:
                yield n.hard_link

    @property
    def total_size(self) -> int:
        if not self.is_dir:
            return self.size
        if self._total_size is None:
            self._total_size = sum(c.total_size for c in self.children.values())
        return self._total_size

    def file_count(self) -> tuple[int, int]:
        """(files, folders) below this node, excluding the node itself."""
        files = dirs = 0
        for n in self.walk():
            if n is self:
                continue
            if n.is_dir:
                dirs += 1
            else:
                files += 1
        return files, dirs


def build_tree(entries: Iterable[Entry]) -> Node:
    root = Node("", True)
    for e in entries:
        comps = normalize_components(e.path)
        if not comps:
            continue
        parent = root
        for part in comps[:-1]:
            nxt = parent.children.get(part)
            if nxt is None or not nxt.is_dir:
                # Missing (or a file shadowing a dir): synthesize a directory.
                nxt = Node(part, True, parent)
                parent.children[part] = nxt
            parent = nxt
        name = comps[-1]
        node = parent.children.get(name)
        if node is None or (node.is_dir != e.is_dir and not node.is_dir):
            # New node, or a later directory entry replaces an earlier file.
            raw = node.raw_paths if node is not None else []
            node = Node(name, e.is_dir, parent)
            node.raw_paths = raw
            parent.children[name] = node
        elif node.is_dir and not e.is_dir:
            # A file entry with the same name as a directory: keep the directory,
            # the file would be clobbered on extraction anyway.
            node.raw_paths.append(e.path)
            continue
        node.raw_paths.append(e.path)
        node.size = e.size if not e.is_dir else 0
        node.packed_size = e.packed_size
        node.mtime = e.mtime
        node.attributes = e.attributes
        node.encrypted = e.encrypted
        node.crc = e.crc
        node.method = e.method
        node.hard_link = e.hard_link
        node.symlink = e.symlink
    return root

"""Archive layers (an archive opened from disk or from inside another archive) and the
temporary cache that backs nested archives and opened files."""

from __future__ import annotations

import itertools
import os
import shutil
import stat
import tempfile
import threading
from dataclasses import dataclass, field
from typing import Optional

from .sevenzip import (
    Cancelled, NotAnArchive, PasswordRequired, ProgressCallback, SevenZip, SevenZipError,
)
from .tree import Node, build_tree
from .volumes import STREAM_TYPES, first_volume, sibling_volumes

# Outer file names for which the single inner file is unwrapped even without ".tar" in
# its name (7z names the inner file after the outer one, e.g. x.tgz -> x.tar).
_TAR_SHORTHANDS = (".tgz", ".tbz", ".tbz2", ".txz", ".tzst", ".taz", ".tlz", ".tlzma")


@dataclass(eq=False)
class Layer:
    archive_path: str          # file on disk that is handed to 7z
    display_name: str          # name shown in the address bar
    root: Node
    archive_type: str
    password: str = ""
    parent: Optional["Layer"] = None       # layer this one was opened from
    parent_node: Optional[Node] = None     # node in ``parent`` it was opened from
    cache_dir: str = ""
    # For archives opened from disk: the file the user opened (archive_path may point to
    # an unwrapped copy in the cache, e.g. the .tar inside a .tar.gz).
    origin_path: str = ""
    # 7z reported errors but listed entries anyway (damaged archive).
    warning: str = ""
    warning_shown: bool = False
    # Extracted files of this layer: id(node) -> path in the cache
    _cached: dict[int, str] = field(default_factory=dict, repr=False)
    # Whole layer extracted into the cache (see Session.extract_in_tree)
    _tree_dir: str = field(default="", repr=False)
    _tree_damaged: bool = field(default=False, repr=False)  # 7z reported errors
    # Nested archives opened from this layer: id(node) -> Layer
    _children: dict[int, "Layer"] = field(default_factory=dict, repr=False)

    @property
    def outermost(self) -> "Layer":
        layer = self
        while layer.parent is not None:
            layer = layer.parent
        return layer

    def chain(self) -> list["Layer"]:
        """Layers from the outermost archive down to this one."""
        layers = []
        layer: Optional[Layer] = self
        while layer is not None:
            layers.append(layer)
            layer = layer.parent
        return list(reversed(layers))

    def nested(self) -> list["Layer"]:
        """Archives opened from this layer (directly)."""
        return list(self._children.values())

    def cache_dirs(self) -> list[str]:
        dirs = [self.cache_dir] if self.cache_dir else []
        for child in self._children.values():
            dirs.extend(child.cache_dirs())
        return dirs


def _default_cache_parent() -> str:
    """~/.cache/slag (not /tmp, which often is RAM backed tmpfs)."""
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "slag")


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def cleanup_stale_caches(parent: str) -> None:
    """Remove cache dirs of slag processes that are gone (crash, kill -9)."""
    try:
        names = os.listdir(parent)
    except OSError:
        return
    for name in names:
        pid_str = name.split("-", 1)[0]
        if pid_str.isdigit() and int(pid_str) != os.getpid() and not _pid_alive(int(pid_str)):
            shutil.rmtree(os.path.join(parent, name), ignore_errors=True)


class Session:
    """Owns the temp cache for one application window. Call ``close()`` when done."""

    def __init__(self, sevenzip: Optional[SevenZip] = None, cache_root: Optional[str] = None):
        self.sevenzip = sevenzip or SevenZip()
        if cache_root is None:
            parent = _default_cache_parent()
            os.makedirs(parent, mode=0o700, exist_ok=True)
            cleanup_stale_caches(parent)
            cache_root = tempfile.mkdtemp(prefix=f"{os.getpid()}-", dir=parent)
        os.makedirs(cache_root, mode=0o700, exist_ok=True)
        self.cache_root = cache_root
        self._counter = itertools.count(1)
        self._lock = threading.Lock()

    def _next(self) -> int:
        with self._lock:
            return next(self._counter)

    def _new_cache_dir(self) -> str:
        d = os.path.join(self.cache_root, f"{self._next()}")
        os.makedirs(d)
        return d

    def close(self) -> None:
        shutil.rmtree(self.cache_root, ignore_errors=True)

    def discard(self, layer: Layer, keep_paths: tuple[str, ...] | list[str] = ()) -> None:
        """Free the cache of an archive (and everything opened from it) no longer shown.

        Cache dirs containing one of ``keep_paths`` (files still opened in other
        applications) are kept until ``close()``.
        """
        for d in layer.outermost.cache_dirs():
            prefix = os.path.join(d, "")
            if any(p.startswith(prefix) for p in keep_paths):
                continue
            shutil.rmtree(d, ignore_errors=True)

    # -- opening --------------------------------------------------------------------

    def open_file(
        self,
        path: str,
        password: str = "",
        cancel: Optional[threading.Event] = None,
        progress: Optional[ProgressCallback] = None,
    ) -> Layer:
        """Open an archive from disk. Raises NotAnArchive / PasswordRequired / SevenZipError."""
        path = first_volume(os.path.abspath(path))
        listing = self.sevenzip.list(path, password=password, cancel=cancel)
        layer = Layer(
            archive_path=path,
            display_name=os.path.basename(path),
            root=build_tree(listing.entries),
            archive_type=listing.archive_type,
            password=password,
            cache_dir=self._new_cache_dir(),
            origin_path=path,
            warning=listing.warning,
        )
        return self._maybe_unwrap(layer, progress, cancel)

    def open_nested(
        self,
        parent: Layer,
        node: Node,
        password: str = "",
        progress: Optional[ProgressCallback] = None,
        cancel: Optional[threading.Event] = None,
    ) -> Layer:
        """Open ``node`` of ``parent`` as an archive (extracting it into the cache first).

        Layers are memoized: re-entering an archive does not extract it again.
        """
        existing = parent._children.get(id(node))
        if existing is not None and os.path.exists(existing.archive_path):
            return existing
        path = self.extract_to_cache(parent, node, progress=progress, cancel=cancel)
        listing = self.sevenzip.list(first_volume(path), password=password, cancel=cancel)
        if listing.archive_type.lower() == "split" and sibling_volumes(
                node.name, list(node.parent.children) if node.parent else []) == [node.name]:
            # 7z "opens" any lone x.001 as a split set containing x: not an archive.
            raise NotAnArchive(f"{node.name} is not an archive")
        layer = Layer(
            archive_path=first_volume(path),
            display_name=node.name,
            root=build_tree(listing.entries),
            archive_type=listing.archive_type,
            password=password,
            parent=parent,
            parent_node=node,
            cache_dir=self._new_cache_dir(),
            warning=listing.warning,
        )
        layer = self._maybe_unwrap(layer, progress, cancel)
        parent._children[id(node)] = layer
        return layer

    def _maybe_unwrap(self, layer: Layer, progress: Optional[ProgressCallback],
                      cancel: Optional[threading.Event]) -> Layer:
        """Show 'x.tar.gz' as the tar inside instead of a gzip containing one 'x.tar'."""
        if layer.archive_type.lower() not in STREAM_TYPES:
            return layer
        children = list(layer.root.children.values())
        if len(children) != 1 or children[0].is_dir:
            return layer
        inner_node = children[0]
        # Only unwrap what is a tar by name; a huge "log.gz" is not extracted for nothing.
        if not (inner_node.name.lower().endswith(".tar")
                or layer.display_name.lower().endswith(_TAR_SHORTHANDS)):
            return layer
        path = self.extract_to_cache(layer, inner_node, progress=progress, cancel=cancel)
        try:
            listing = self.sevenzip.list(path, cancel=cancel)
        except (NotAnArchive, PasswordRequired, SevenZipError):
            return layer
        if listing.archive_type.lower() in STREAM_TYPES:
            return layer
        return Layer(
            archive_path=path,
            display_name=layer.display_name,
            root=build_tree(listing.entries),
            archive_type=listing.archive_type,
            password="",
            parent=layer.parent,
            parent_node=layer.parent_node,
            cache_dir=layer.cache_dir,
            origin_path=layer.origin_path,
            warning=layer.warning or listing.warning,
        )

    # -- files ----------------------------------------------------------------------

    def extract_to_cache(
        self,
        layer: Layer,
        node: Node,
        progress: Optional[ProgressCallback] = None,
        cancel: Optional[threading.Event] = None,
        fresh: bool = False,
    ) -> str:
        """Extract a single file node into the layer's cache and return its path.

        If the node is a volume of a split archive, all sibling volumes are extracted next
        to it. Repeated calls return the cached copy unless ``fresh`` is set (or it is gone).
        """
        key = id(node)
        cached = layer._cached.get(key)
        if cached and not fresh and os.path.exists(cached):
            return cached
        nodes = [node]
        if node.parent is not None:
            names = sibling_volumes(node.name, list(node.parent.children))
            nodes = [node.parent.children[n] for n in names]
        raw_paths = [p for n in nodes for p in n.iter_raw_paths()]
        out_dir = os.path.join(layer.cache_dir, f"f{self._next()}")
        os.makedirs(out_dir)
        self.sevenzip.extract(
            layer.archive_path, out_dir, raw_paths,
            password=layer.password, progress=progress, cancel=cancel,
        )
        path = os.path.join(out_dir, *node.components)
        if not os.path.lexists(path):
            raise FileNotFoundError(f"7z did not extract {node.path}")
        layer._cached[key] = path
        return path

    def extract_in_tree(
        self,
        layer: Layer,
        node: Node,
        progress: Optional[ProgressCallback] = None,
        cancel: Optional[threading.Event] = None,
    ) -> str:
        """Extract the whole layer into the cache and return the path of ``node`` in it.

        For files that refer to other files of the archive by relative paths (web pages).
        The tree is extracted once per layer; files in it are not executable. Damaged
        entries are left out, but ``node`` itself must be intact.
        """
        if not self.has_tree(layer):
            tree_dir = os.path.join(layer.cache_dir, f"t{self._next()}")
            os.makedirs(tree_dir)
            damaged = False
            try:
                try:
                    self.sevenzip.extract(layer.archive_path, tree_dir, password=layer.password,
                                          progress=progress, cancel=cancel)
                except (Cancelled, PasswordRequired, NotAnArchive):
                    raise
                except SevenZipError:
                    # E.g. a CRC error in one file: keep everything else.
                    damaged = True
            except BaseException:
                shutil.rmtree(tree_dir, ignore_errors=True)
                raise
            _strip_exec_bits_tree(tree_dir)
            layer._tree_dir = tree_dir
            layer._tree_damaged = damaged
        if layer._tree_damaged:
            # 7z writes files that fail the CRC check as well: extracting the file alone
            # raises if it is one of the damaged ones.
            self.extract_to_cache(layer, node, progress=progress, cancel=cancel)
        path = os.path.join(layer._tree_dir, *node.components)
        if not os.path.lexists(path):
            raise FileNotFoundError(f"7z did not extract {node.path}")
        return path

    @staticmethod
    def has_tree(layer: Layer) -> bool:
        """True when ``extract_in_tree`` needs no extraction for ``layer``."""
        return bool(layer._tree_dir) and os.path.isdir(layer._tree_dir)


def _strip_exec_bits_tree(root: str) -> None:
    """Never leave executables from an (untrusted) archive next to an opened file."""
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            path = os.path.join(dirpath, name)
            try:
                st = os.lstat(path)
                if stat.S_ISREG(st.st_mode) and st.st_mode & 0o111:
                    os.chmod(path, st.st_mode & ~0o111)
            except OSError:
                pass

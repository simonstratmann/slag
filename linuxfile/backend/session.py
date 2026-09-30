"""Archive layers (an archive opened from disk or from inside another archive) and the
temporary cache that backs nested archives and opened files."""

from __future__ import annotations

import itertools
import os
import shutil
import tempfile
import threading
from dataclasses import dataclass, field
from typing import Optional

from .sevenzip import NotAnArchive, PasswordRequired, ProgressCallback, SevenZip, SevenZipError
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

    def cache_dirs(self) -> list[str]:
        dirs = [self.cache_dir] if self.cache_dir else []
        for child in self._children.values():
            dirs.extend(child.cache_dirs())
        return dirs


def _default_cache_parent() -> str:
    """~/.cache/linuxfile (not /tmp, which often is RAM backed tmpfs)."""
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "linuxfile")


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def cleanup_stale_caches(parent: str) -> None:
    """Remove cache dirs of linuxfile processes that are gone (crash, kill -9)."""
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

    def discard(self, layer: Layer) -> None:
        """Free the cache of an archive (and everything opened from it) no longer shown."""
        for d in layer.outermost.cache_dirs():
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

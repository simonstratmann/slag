"""Tree model for the navigation pane: the folders of the outermost archive, rooted at its
first level (the archive root itself has no row, so there is no way above it).

Archives opened from inside it appear as nodes in their folder, with their own folders
below them.
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QAbstractItemModel, QModelIndex, Qt
from PyQt6.QtGui import QFont

from ..backend.session import Layer
from ..backend.tree import Node
from .archive_model import icon_for, natural_key


def all_layers(outer: Layer) -> list[Layer]:
    """``outer`` and every archive opened from it, parents before children."""
    layers = [outer]
    for layer in layers:
        layers.extend(layer.nested())
    return layers


class ArchiveTreeModel(QAbstractItemModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._outer: Optional[Layer] = None
        self._layers: list[Layer] = []
        self._layer_of_root: dict[int, Layer] = {}
        # archive file node -> the layer opened from it
        self._nested: dict[int, Layer] = {}
        self._current: Optional[Node] = None
        # Shown children per node, built lazily.
        self._children: dict[int, list[Node]] = {}
        self._rows: dict[int, int] = {}

    # -- content --------------------------------------------------------------------

    def set_layers(self, outer: Optional[Layer]) -> bool:
        """Show ``outer`` and the archives opened from it. Returns True if the model was
        reset (because the archive or the set of opened inner archives changed)."""
        layers = all_layers(outer) if outer is not None else []
        if outer is self._outer and layers == self._layers:
            return False
        self.beginResetModel()
        self._outer = outer
        self._layers = layers
        self._layer_of_root = {id(layer.root): layer for layer in layers}
        self._nested = {id(layer.parent_node): layer for layer in layers if layer.parent_node is not None}
        self._current = None
        self._children.clear()
        self._rows.clear()
        self.endResetModel()
        return True

    def set_current(self, node: Optional[Node]) -> None:
        """Mark the folder (or inner archive) shown in the file list (drawn bold)."""
        old, self._current = self._current, node
        for n in (old, node):
            idx = self.index_of(n)
            if idx.isValid():
                self.dataChanged.emit(idx, idx, [Qt.ItemDataRole.FontRole])

    def node_of(self, index: QModelIndex) -> Optional[Node]:
        return index.internalPointer() if index.isValid() else None

    def nested_layer(self, node: Node) -> Optional[Layer]:
        """The layer opened from an archive node, or None for a folder."""
        return self._nested.get(id(node))

    def layer_of(self, node: Node) -> Optional[Layer]:
        """The layer a node belongs to."""
        while node.parent is not None:
            node = node.parent
        return self._layer_of_root.get(id(node))

    def nodes(self) -> list[Node]:
        """All nodes whose children have been built (i.e. that may be expanded)."""
        by_id = {id(n): n for kids in self._children.values() for n in kids}
        return [by_id[k] for k in self._children if k in by_id]

    def _tree_parent(self, node: Node) -> Optional[Node]:
        """Parent row of ``node``; None for the first level (and for foreign nodes)."""
        parent = node.parent
        if parent is None:
            return None
        if parent.parent is None:
            # Top of a layer: an inner archive hangs below the node it was opened from.
            layer = self._layer_of_root.get(id(parent))
            return layer.parent_node if layer is not None else None
        return parent

    def index_of(self, node: Optional[Node]) -> QModelIndex:
        """Index of a shown node; invalid for layer roots and nodes that are not shown."""
        if node is None or node.parent is None or self._outer is None:
            return QModelIndex()
        parent = self._tree_parent(node)
        if parent is None and node.parent is not self._outer.root:
            return QModelIndex()
        self._shown_children(parent)
        row = self._rows.get(id(node))
        if row is None:
            return QModelIndex()
        return self.createIndex(row, 0, node)

    def _shown_children(self, node: Optional[Node]) -> list[Node]:
        """Folders and opened inner archives below a row (None: the first level)."""
        if node is None:
            if self._outer is None:
                return []
            node = self._outer.root
        kids = self._children.get(id(node))
        if kids is None:
            layer = self._nested.get(id(node))
            source = layer.root if layer is not None else node
            kids = sorted((n for n in source.children.values() if n.is_dir or id(n) in self._nested),
                          key=lambda n: (not n.is_dir, natural_key(n.name), n.name))
            self._children[id(node)] = kids
            for r, n in enumerate(kids):
                self._rows[id(n)] = r
        return kids

    # -- Qt model API -----------------------------------------------------------------

    def index(self, row, column, parent=QModelIndex()):
        if column != 0 or self._outer is None:
            return QModelIndex()
        kids = self._shown_children(self.node_of(parent))
        if not 0 <= row < len(kids):
            return QModelIndex()
        return self.createIndex(row, 0, kids[row])

    def parent(self, index=QModelIndex()):
        node = self.node_of(index)
        if node is None:
            return QModelIndex()
        return self.index_of(self._tree_parent(node))

    def rowCount(self, parent=QModelIndex()):  # noqa: N802
        if parent.column() > 0 or self._outer is None:
            return 0
        return len(self._shown_children(self.node_of(parent)))

    def columnCount(self, parent=QModelIndex()):  # noqa: N802
        return 1

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        node = self.node_of(index)
        if node is None:
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            return node.name
        if role == Qt.ItemDataRole.DecorationRole:
            return icon_for(node.name, node.is_dir)
        if role == Qt.ItemDataRole.ToolTipRole:
            return node.path
        if role == Qt.ItemDataRole.FontRole and node is self._current:
            font = QFont()
            font.setBold(True)
            return font
        return None

    def flags(self, index):
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

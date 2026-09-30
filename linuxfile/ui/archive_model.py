"""Table model showing the children of one directory node (plus a '..' row)."""

from __future__ import annotations

import re
from typing import Optional

from PyQt6.QtCore import QAbstractItemModel, QAbstractTableModel, QLocale, QModelIndex, Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtCore import QMimeDatabase

from ..backend.tree import Node

COL_NAME, COL_SIZE, COL_PACKED, COL_MODIFIED, COL_ATTR, COL_CRC, COL_METHOD = range(7)
HEADERS = ["Name", "Size", "Packed Size", "Modified", "Attributes", "CRC", "Method"]

PARENT_ROW_NAME = ".."

_mime_db = QMimeDatabase()
_icon_cache: dict[str, QIcon] = {}


def icon_for(name: str, is_dir: bool) -> QIcon:
    if is_dir:
        key = "inode/directory"
        icon_name, fallback = "folder", "folder"
    else:
        mime = _mime_db.mimeTypeForFile(name, QMimeDatabase.MatchMode.MatchExtension)
        key = mime.name()
        icon_name, fallback = mime.iconName(), mime.genericIconName()
    icon = _icon_cache.get(key)
    if icon is None:
        icon = QIcon.fromTheme(icon_name)
        if icon.isNull():
            icon = QIcon.fromTheme(fallback or "unknown", QIcon.fromTheme("unknown"))
        _icon_cache[key] = icon
    return icon


_DIGITS_RE = re.compile(r"(\d+)")


def natural_key(name: str) -> tuple:
    """'file2' sorts before 'file10'."""
    parts = _DIGITS_RE.split(name.casefold())
    return tuple((int(p), "") if i % 2 else (0, p) for i, p in enumerate(parts))


def format_size(size: Optional[int]) -> str:
    if size is None:
        return ""
    return QLocale().toString(size)


class ArchiveModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._dir: Optional[Node] = None
        self._has_parent_row = False
        self._rows: list[Node] = []
        self._row_map: Optional[dict[int, int]] = None
        self._sort_column = COL_NAME
        self._sort_order = Qt.SortOrder.AscendingOrder

    # -- content --------------------------------------------------------------------

    def set_directory(self, node: Node, has_parent_row: bool) -> None:
        self.beginResetModel()
        self._dir = node
        self._has_parent_row = has_parent_row
        self._rows = list(node.children.values())
        self._sort_rows()
        self.endResetModel()

    @property
    def directory(self) -> Optional[Node]:
        return self._dir

    def node_at(self, row: int) -> Optional[Node]:
        """Node for a row; None for the '..' row."""
        if self._has_parent_row:
            if row == 0:
                return None
            row -= 1
        if 0 <= row < len(self._rows):
            return self._rows[row]
        return None

    def is_parent_row(self, row: int) -> bool:
        return self._has_parent_row and row == 0

    def row_of(self, node: Node) -> int:
        if self._row_map is None:
            self._row_map = {id(n): r for r, n in enumerate(self._rows)}
        r = self._row_map.get(id(node))
        if r is None:
            return -1
        return r + (1 if self._has_parent_row else 0)

    def nodes(self) -> list[Node]:
        return list(self._rows)

    # -- Qt model API -----------------------------------------------------------------

    def rowCount(self, parent=QModelIndex()):  # noqa: N802
        if parent.isValid():
            return 0
        return len(self._rows) + (1 if self._has_parent_row else 0)

    def columnCount(self, parent=QModelIndex()):  # noqa: N802
        return 0 if parent.isValid() else len(HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return HEADERS[section]
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row, col = index.row(), index.column()
        if self.is_parent_row(row):
            if role == Qt.ItemDataRole.DisplayRole and col == COL_NAME:
                return PARENT_ROW_NAME
            if role == Qt.ItemDataRole.DecorationRole and col == COL_NAME:
                return QIcon.fromTheme("go-up")
            return None
        node = self.node_at(row)
        if node is None:
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            if col == COL_NAME:
                return node.name
            if col == COL_SIZE:
                return format_size(node.total_size)
            if col == COL_PACKED:
                return "" if node.is_dir else format_size(node.packed_size)
            if col == COL_MODIFIED:
                return node.mtime.strftime("%Y-%m-%d %H:%M:%S") if node.mtime else ""
            if col == COL_ATTR:
                return node.attributes
            if col == COL_CRC:
                return node.crc
            if col == COL_METHOD:
                return node.method
        elif role == Qt.ItemDataRole.DecorationRole and col == COL_NAME:
            return icon_for(node.name, node.is_dir)
        elif role == Qt.ItemDataRole.TextAlignmentRole and col in (COL_SIZE, COL_PACKED):
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        elif role == Qt.ItemDataRole.ToolTipRole and col == COL_NAME:
            tip = node.path
            if node.encrypted:
                tip += "\n(encrypted)"
            return tip
        return None

    def flags(self, index):
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def sort(self, column, order=Qt.SortOrder.AscendingOrder):
        hint = QAbstractItemModel.LayoutChangeHint.VerticalSortHint
        self.layoutAboutToBeChanged.emit([], hint)
        old = self.persistentIndexList()
        offset = 1 if self._has_parent_row else 0
        old_rows = [self._rows[i.row() - offset] if i.row() >= offset else None for i in old]
        self._sort_column = column
        self._sort_order = order
        self._sort_rows()
        new_row = {id(n): r + offset for r, n in enumerate(self._rows)}
        new = [self.index(0 if n is None else new_row[id(n)], i.column()) for i, n in zip(old, old_rows)]
        self.changePersistentIndexList(old, new)
        self.layoutChanged.emit([], hint)

    def _sort_rows(self) -> None:
        col = self._sort_column
        reverse = self._sort_order == Qt.SortOrder.DescendingOrder

        def key(n: Node):
            if col == COL_SIZE:
                v = n.total_size
            elif col == COL_PACKED:
                v = n.packed_size or 0
            elif col == COL_MODIFIED:
                v = n.mtime.timestamp() if n.mtime else 0
            elif col == COL_ATTR:
                v = n.attributes
            elif col == COL_CRC:
                v = n.crc
            elif col == COL_METHOD:
                v = n.method
            else:
                v = 0
            return (v, natural_key(n.name), n.name)

        dirs = sorted((n for n in self._rows if n.is_dir), key=key, reverse=reverse)
        files = sorted((n for n in self._rows if not n.is_dir), key=key, reverse=reverse)
        # Folders always stay on top, like in 7-Zip / Dolphin.
        self._rows = dirs + files
        self._row_map = None

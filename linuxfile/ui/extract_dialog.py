"""Target selection dialog: path field with autocomplete, folder chooser, recent targets."""

from __future__ import annotations

import os
from typing import Optional

from PyQt6.QtCore import QDir, QSettings, Qt
from PyQt6.QtGui import QFileSystemModel, QIcon
from PyQt6.QtWidgets import (
    QCompleter, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QToolButton, QVBoxLayout, QWidget,
)

RECENT_KEY = "extract/recent"
DEFAULT_RECENT_COUNT = 10


def recent_targets(settings: QSettings) -> list[str]:
    value = settings.value(RECENT_KEY, [])
    if isinstance(value, str):
        value = [value]
    return [v for v in (value or []) if isinstance(v, str) and v]


def remember_target(settings: QSettings, path: str) -> None:
    limit = int(settings.value("extract/recentCount", DEFAULT_RECENT_COUNT))
    path = os.path.normpath(path)
    items = [p for p in recent_targets(settings) if os.path.normpath(p) != path]
    items.insert(0, path)
    settings.setValue(RECENT_KEY, items[:max(1, limit)])


def expand_path(text: str, base: str) -> str:
    """'~/x', relative paths (to ``base``) and env vars -> absolute normalized path."""
    text = os.path.expandvars(os.path.expanduser(text.strip()))
    if text.startswith("file://"):
        from urllib.parse import unquote, urlparse
        text = unquote(urlparse(text).path)
    if not os.path.isabs(text):
        text = os.path.join(base, text)
    return os.path.normpath(text)


def _dir_model(parent) -> QFileSystemModel:
    model = QFileSystemModel(parent)
    model.setFilter(QDir.Filter.AllDirs | QDir.Filter.NoDotAndDotDot | QDir.Filter.Hidden)
    model.setRootPath("")
    return model


class ExtractDialog(QDialog):
    def __init__(self, parent: Optional[QWidget], settings: QSettings, default_target: str,
                 summary: str, base_dir: str):
        super().__init__(parent)
        self.setWindowTitle("Extract")
        self.settings = settings
        self.base_dir = base_dir

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(summary, self))

        layout.addWidget(QLabel("Extract to:", self))
        row = QHBoxLayout()
        self.path_edit = QLineEdit(self)
        self.path_edit.setText(default_target)
        self.path_edit.setClearButtonEnabled(True)
        completer = QCompleter(self)
        completer.setModel(_dir_model(completer))
        completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseSensitive)
        self.path_edit.setCompleter(completer)
        row.addWidget(self.path_edit, 1)
        browse = QToolButton(self)
        browse.setIcon(QIcon.fromTheme("folder-open"))
        browse.setText("…")
        browse.setToolTip("Choose folder")
        browse.clicked.connect(self._browse)
        row.addWidget(browse)
        layout.addLayout(row)

        layout.addWidget(QLabel("Recent targets:", self))
        self.recent_list = QListWidget(self)
        for path in recent_targets(settings):
            item = QListWidgetItem(QIcon.fromTheme("folder"), path)
            if not os.path.isdir(path):
                item.setToolTip("Does not exist (will be created)")
            self.recent_list.addItem(item)
        self.recent_list.currentTextChanged.connect(self._recent_selected)
        self.recent_list.itemActivated.connect(self._recent_activated)
        layout.addWidget(self.recent_list, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Extract")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.path_edit.textChanged.connect(lambda t: self.ok_button.setEnabled(bool(t.strip())))

        self.resize(640, 380)
        self.path_edit.setFocus()
        self.path_edit.selectAll()

    def _browse(self) -> None:
        start = self.target() or self.base_dir
        while start and not os.path.isdir(start):
            parent = os.path.dirname(start)
            if parent == start:
                break
            start = parent
        path = QFileDialog.getExistingDirectory(self, "Extract to", start)
        if path:
            self.path_edit.setText(path)

    def _recent_selected(self, text: str) -> None:
        if text:
            self.path_edit.setText(text)

    def _recent_activated(self, item: QListWidgetItem) -> None:
        self.path_edit.setText(item.text())
        self.accept()

    def target(self) -> str:
        text = self.path_edit.text()
        return expand_path(text, self.base_dir) if text.strip() else ""

    def accept(self) -> None:  # noqa: D401
        if not self.target():
            return
        remember_target(self.settings, self.target())
        super().accept()

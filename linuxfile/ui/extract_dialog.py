"""Target selection dialog: path field with autocomplete, folder chooser, recent targets."""

from __future__ import annotations

import html
import os
from typing import Optional

from PyQt6.QtCore import QSettings, QStringListModel, Qt
from PyQt6.QtGui import QIcon
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


MAX_COMPLETIONS = 500


def directory_completions(text: str, base: str) -> list[str]:
    """Completions for a typed folder path, keeping the user's spelling of the prefix.

    '~/Doc' -> ['~/Documents/'], 'rel/x' (relative to ``base``) -> ['rel/xyz/'].
    """
    if not text.strip():
        return []
    typed_dir, _, partial = text.rpartition("/")
    typed_dir_prefix = typed_dir + "/" if "/" in text else ""
    directory = expand_path(typed_dir_prefix or ".", base)
    try:
        entries = os.scandir(directory)
    except OSError:
        return []
    result = []
    with entries:
        for entry in entries:
            if not entry.name.startswith(partial):
                continue
            if entry.name.startswith(".") and not partial.startswith("."):
                continue
            try:
                if not entry.is_dir():
                    continue
            except OSError:
                continue
            result.append(typed_dir_prefix + entry.name + "/")
            if len(result) >= MAX_COMPLETIONS:
                break
    return sorted(result, key=str.casefold)


def target_problem(target: str) -> Optional[str]:
    """Why ``target`` can't be used as an extraction folder, or None if it can."""
    if os.path.exists(target) and not os.path.isdir(target):
        return "The target exists but is not a folder."
    existing = target
    while not os.path.isdir(existing):
        parent = os.path.dirname(existing)
        if parent == existing:
            return "The target is not reachable."
        existing = parent
    if not os.access(existing, os.W_OK | os.X_OK):
        return f"No permission to write to {existing}."
    return None


class _RecentList(QListWidget):
    """Keyboard navigation picks the entry; focusing the list alone does not."""

    def __init__(self, parent, on_pick):
        super().__init__(parent)
        self._on_pick = on_pick

    def keyPressEvent(self, event):  # noqa: N802
        before = self.currentRow()
        super().keyPressEvent(event)
        if self.currentRow() != before and self.currentItem() is not None:
            self._on_pick(self.currentItem().text())


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
        self.completion_model = QStringListModel(self)
        self.completer = QCompleter(self.completion_model, self)
        self.completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self.completer.setCaseSensitivity(Qt.CaseSensitivity.CaseSensitive)
        self.path_edit.setCompleter(self.completer)
        # Fill the model before QCompleter filters it (textEdited runs first).
        self.path_edit.textEdited.connect(self._update_completions)
        row.addWidget(self.path_edit, 1)
        browse = QToolButton(self)
        browse.setIcon(QIcon.fromTheme("folder-open"))
        browse.setText("…")
        browse.setToolTip("Choose folder")
        browse.clicked.connect(self._browse)
        row.addWidget(browse)
        layout.addLayout(row)

        self.error_label = QLabel(self)
        self.error_label.setStyleSheet("color: #d32f2f;")
        self.error_label.hide()
        layout.addWidget(self.error_label)

        layout.addWidget(QLabel("Recent targets (click to pick, double-click to extract):", self))
        self.recent_list = _RecentList(self, self._pick)
        for path in recent_targets(settings):
            item = QListWidgetItem(QIcon.fromTheme("folder"), path)
            if not os.path.isdir(path):
                item.setToolTip("Does not exist (will be created)")
            self.recent_list.addItem(item)
        self.recent_list.itemClicked.connect(lambda item: self._pick(item.text()))
        self.recent_list.itemDoubleClicked.connect(self._recent_double_clicked)
        layout.addWidget(self.recent_list, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Extract")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.path_edit.textChanged.connect(self._text_changed)

        self.resize(640, 380)
        self.path_edit.setFocus()
        self.path_edit.selectAll()

    def _update_completions(self, text: str) -> None:
        self.completion_model.setStringList(directory_completions(text, self.base_dir))

    def _text_changed(self, text: str) -> None:
        self.ok_button.setEnabled(bool(text.strip()))
        self.error_label.hide()

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

    def _pick(self, text: str) -> None:
        if text:
            self.path_edit.setText(text)

    def _recent_double_clicked(self, item: QListWidgetItem) -> None:
        self.path_edit.setText(item.text())
        self.accept()

    def target(self) -> str:
        text = self.path_edit.text()
        return expand_path(text, self.base_dir) if text.strip() else ""

    def accept(self) -> None:  # noqa: D401
        target = self.target()
        if not target:
            return
        problem = target_problem(target)
        if problem:
            self.error_label.setText(html.escape(problem))
            self.error_label.show()
            self.path_edit.setFocus()
            return
        remember_target(self.settings, target)
        super().accept()

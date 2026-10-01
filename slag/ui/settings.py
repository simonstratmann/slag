"""Application settings (QSettings) and the settings dialog."""

from __future__ import annotations

import os
import shlex
import shutil

from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QSpinBox, QVBoxLayout, QWidget,
)

from .extract_dialog import DEFAULT_RECENT_COUNT, recent_targets

EDITOR_KEY = "editor/command"
RECENT_COUNT_KEY = "extract/recentCount"
WEB_TREE_LIMIT_KEY = "open/webPageArchiveLimitMB"
DEFAULT_WEB_TREE_LIMIT_MB = 150


def default_editor() -> str:
    for cmd in ("kate", "kwrite", "gnome-text-editor", "gedit", "mousepad"):
        if shutil.which(cmd):
            return cmd
    return "xdg-open"


def editor_command(settings: QSettings) -> str:
    value = settings.value(EDITOR_KEY, "")
    return value if isinstance(value, str) and value.strip() else default_editor()


def build_editor_args(command: str, path: str) -> list[str]:
    """Split the configured command; '%f' is replaced by the file, else it is appended."""
    args = shlex.split(command)
    if not args:
        raise ValueError("No editor configured")
    if "%f" in args:
        return [path if a == "%f" else a.replace("%f", shlex.quote(path)) for a in args]
    if any("%f" in a for a in args):
        # '%f' inside a larger argument (e.g. sh -c "vim %f"): the argument is most
        # likely parsed by a shell again, so quote the file name for it.
        return [a.replace("%f", shlex.quote(path)) for a in args]
    return args + [path]


def recent_count(settings: QSettings) -> int:
    try:
        return int(settings.value(RECENT_COUNT_KEY, DEFAULT_RECENT_COUNT))
    except (TypeError, ValueError):
        return DEFAULT_RECENT_COUNT


def web_tree_limit_mb(settings: QSettings) -> int:
    try:
        return int(settings.value(WEB_TREE_LIMIT_KEY, DEFAULT_WEB_TREE_LIMIT_MB))
    except (TypeError, ValueError):
        return DEFAULT_WEB_TREE_LIMIT_MB


class SettingsDialog(QDialog):
    def __init__(self, parent: QWidget | None, settings: QSettings):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.settings = settings
        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.editor_edit = QLineEdit(self)
        self.editor_edit.setText(settings.value(EDITOR_KEY, "") or "")
        self.editor_edit.setPlaceholderText(default_editor())
        form.addRow("Editor (F4):", self.editor_edit)
        hint = QLabel("Command line; <tt>%f</tt> is replaced by the file, otherwise the file "
                      "is appended. Example: <tt>konsole -e nvim %f</tt>", self)
        hint.setWordWrap(True)
        form.addRow("", hint)

        self.recent_spin = QSpinBox(self)
        self.recent_spin.setRange(1, 50)
        self.recent_spin.setValue(recent_count(settings))
        form.addRow("Recent extract targets:", self.recent_spin)

        self.web_limit_spin = QSpinBox(self)
        self.web_limit_spin.setRange(0, 1_000_000)
        self.web_limit_spin.setSuffix(" MB")
        self.web_limit_spin.setValue(web_tree_limit_mb(settings))
        form.addRow("Web pages: ask for archives over:", self.web_limit_spin)
        web_hint = QLabel("Opening an HTML file extracts the whole archive, so the browser finds "
                          "its images, styles and scripts. For larger archive files SLAG asks "
                          "first.", self)
        web_hint.setWordWrap(True)
        form.addRow("", web_hint)

        layout.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.resize(520, 0)

    def accept(self) -> None:
        editor = self.editor_edit.text().strip()
        if editor:
            try:
                shlex.split(editor)
            except ValueError as exc:
                self.editor_edit.setFocus()
                self.editor_edit.setToolTip(str(exc))
                return
        self.settings.setValue(EDITOR_KEY, editor)
        count = self.recent_spin.value()
        self.settings.setValue(RECENT_COUNT_KEY, count)
        self.settings.setValue("extract/recent", recent_targets(self.settings)[:count])
        self.settings.setValue(WEB_TREE_LIMIT_KEY, self.web_limit_spin.value())
        super().accept()


def command_exists(cmd: str) -> bool:
    """True when the first word of ``cmd`` is an executable that exists."""
    try:
        args = shlex.split(cmd)
    except ValueError:
        return False
    return bool(args) and (shutil.which(args[0]) is not None or os.access(args[0], os.X_OK))

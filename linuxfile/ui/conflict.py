"""'File already exists' dialog used during extraction."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from PyQt6.QtWidgets import QApplication, QCheckBox, QMessageBox, QWidget

from ..backend.extract import ConflictAction
from .archive_model import format_size
from .tasks import call_in_gui_thread


def _describe(p: Path) -> str:
    try:
        st = p.lstat()
    except OSError:
        return str(p)
    kind = "folder" if p.is_dir() else f"{format_size(st.st_size)} bytes"
    mtime = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    return f"{kind}, modified {mtime}"


class ConflictResolver:
    """Asks the user what to do with existing files; remembers "apply to all"."""

    def __init__(self, parent: Optional[QWidget]):
        self.parent = parent
        self.remembered: Optional[ConflictAction] = None

    def __call__(self, dst: Path, src: Path) -> ConflictAction:
        if self.remembered is not None:
            return self.remembered
        return call_in_gui_thread(lambda: self._ask(dst, src))

    def _ask(self, dst: Path, src: Path) -> ConflictAction:
        box = QMessageBox(QApplication.activeWindow() or self.parent)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("File already exists")
        box.setText(f"<b>{os.path.basename(dst)}</b> already exists in<br>{os.path.dirname(dst)}")
        box.setInformativeText(f"Existing: {_describe(dst)}\nFrom archive: {_describe(src)}")
        overwrite = box.addButton("&Overwrite", QMessageBox.ButtonRole.AcceptRole)
        skip = box.addButton("&Skip", QMessageBox.ButtonRole.RejectRole)
        rename = box.addButton("&Rename", QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(skip)
        apply_all = QCheckBox("Apply to &all remaining conflicts", box)
        box.setCheckBox(apply_all)
        box.exec()
        clicked = box.clickedButton()
        if clicked is overwrite:
            action = ConflictAction.OVERWRITE
        elif clicked is skip:
            action = ConflictAction.SKIP
        elif clicked is rename:
            action = ConflictAction.RENAME
        else:
            return ConflictAction.CANCEL
        if apply_all.isChecked():
            self.remembered = action
        return action

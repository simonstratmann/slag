"""Files extracted to the temp cache and opened externally (Enter / F4).

The archive is never modified. If the user edits such a temp copy, they are told so
and can save a copy elsewhere, both when returning to the window and before the cache is
deleted on exit.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from typing import Optional

from PyQt6.QtWidgets import QFileDialog, QMessageBox, QWidget


@dataclass
class OpenedFile:
    path: str
    archive_name: str  # for messages, e.g. "a.zip › folder/file.txt"
    signature: tuple[float, int]
    notified: Optional[tuple[float, int]] = None  # signature the user was told about


def _signature(path: str) -> Optional[tuple[float, int]]:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_mtime, st.st_size)


class OpenedFiles:
    def __init__(self, parent: QWidget):
        self.parent = parent
        self.files: dict[str, OpenedFile] = {}
        self._checking = False

    def track(self, path: str, archive_name: str) -> None:
        sig = _signature(path)
        if sig is None:
            return
        existing = self.files.get(path)
        if existing is None:
            self.files[path] = OpenedFile(path, archive_name, sig)

    def modified(self) -> list[OpenedFile]:
        result = []
        for f in self.files.values():
            sig = _signature(f.path)
            if sig is not None and sig != f.signature:
                result.append(f)
        return result

    def check(self) -> None:
        """Tell the user about temp copies modified since the last check."""
        if self._checking:
            return
        self._checking = True
        try:
            for f in self.modified():
                sig = _signature(f.path)
                if sig == f.notified:
                    continue
                f.notified = sig
                self._offer_save(
                    f, "was modified",
                    "The archive is opened read-only, so the change is <b>not</b> saved into "
                    "it. The edited file only exists as a temporary copy until linuxfile "
                    "is closed.")
        finally:
            self._checking = False

    def confirm_close(self) -> bool:
        """Before the cache is deleted: offer to save modified temp copies.

        Returns False if the user wants to keep the window open.
        """
        for f in self.modified():
            if not self._offer_save(
                    f, "has unsaved changes",
                    "The temporary copy will be deleted when linuxfile closes.",
                    allow_cancel=True):
                return False
        return True

    def confirm_discard(self, cache_dirs: list[str]) -> bool:
        """Before the cache of an archive is deleted (another archive gets opened)."""
        prefixes = tuple(os.path.join(d, "") for d in cache_dirs)
        affected = [f for f in self.modified() if f.path.startswith(prefixes)]
        for f in affected:
            if not self._offer_save(
                    f, "has unsaved changes",
                    "Its temporary copy will be deleted when another archive is opened.",
                    allow_cancel=True):
                return False
        for path in [p for p in self.files if p.startswith(prefixes)]:
            del self.files[path]
        return True

    def _offer_save(self, f: OpenedFile, what: str, info: str, allow_cancel: bool = False) -> bool:
        box = QMessageBox(QMessageBox.Icon.Information, "Edited file",
                          f"<b>{os.path.basename(f.path)}</b> ({f.archive_name}) {what}.",
                          parent=self.parent)
        box.setInformativeText(info)
        save = box.addButton("Save a Copy As…", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Discard" if allow_cancel else "OK", QMessageBox.ButtonRole.RejectRole)
        cancel = box.addButton(QMessageBox.StandardButton.Cancel) if allow_cancel else None
        box.setDefaultButton(save)
        box.exec()
        clicked = box.clickedButton()
        if cancel is not None and clicked is cancel:
            return False
        if clicked is save:
            return self._save_copy(f)
        return True

    def _save_copy(self, f: OpenedFile) -> bool:
        dest, _ = QFileDialog.getSaveFileName(
            self.parent, "Save Copy As",
            os.path.join(os.path.expanduser("~"), os.path.basename(f.path)))
        if not dest:
            return False
        try:
            shutil.copy2(f.path, dest)
        except OSError as exc:
            QMessageBox.critical(self.parent, "Edited file", f"Could not save:\n{exc}")
            return False
        f.signature = _signature(f.path) or f.signature
        return True

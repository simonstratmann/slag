"""Entry point: ``python -m slag [archive]``."""

from __future__ import annotations

import os
import sys


def archive_arguments(args: list[str]) -> list[str]:
    """Positional file arguments: honours '--', accepts file:// URLs, and ignores
    unknown options unless a file of that name exists."""
    from PyQt6.QtCore import QUrl

    result = []
    only_files = False
    for a in args:
        if not only_files and a == "--":
            only_files = True
            continue
        if not only_files and a.startswith("-") and not os.path.exists(a):
            continue
        if a.startswith("file:"):
            url = QUrl(a)
            if url.isLocalFile():
                a = url.toLocalFile()
        result.append(a)
    return result


def main(argv: list[str] | None = None) -> int:
    from PyQt6.QtWidgets import QApplication, QMessageBox

    from .backend.sevenzip import SevenZipNotFound, find_7z
    from .ui.main_window import APP_ID, APP_NAME, MainWindow

    argv = list(sys.argv if argv is None else argv)
    app = QApplication(argv)
    app.setApplicationName(APP_ID)
    app.setOrganizationName(APP_ID)
    app.setApplicationDisplayName(APP_NAME)
    app.setDesktopFileName(APP_ID)
    try:
        find_7z()
    except SevenZipNotFound as exc:
        QMessageBox.critical(None, APP_NAME, str(exc))
        return 1

    window = MainWindow()
    window.showMaximized()
    args = archive_arguments(app.arguments()[1:])
    if args:
        window.open_path(args[0])
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())

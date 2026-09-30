"""Entry point: ``python -m linuxfile [archive]``."""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    from PyQt6.QtWidgets import QApplication, QMessageBox

    from .backend.sevenzip import SevenZipNotFound, find_7z
    from .ui.main_window import APP_NAME, MainWindow

    argv = list(sys.argv if argv is None else argv)
    app = QApplication(argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_NAME)
    app.setDesktopFileName(APP_NAME)
    try:
        find_7z()
    except SevenZipNotFound as exc:
        QMessageBox.critical(None, APP_NAME, str(exc))
        return 1

    window = MainWindow()
    window.show()
    args = [a for a in app.arguments()[1:] if not a.startswith("-")]
    if args:
        window.open_path(args[0])
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())

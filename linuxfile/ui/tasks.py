"""Run blocking backend work in a thread while keeping the UI responsive.

``run_blocking`` spins a local event loop until the work is done, so calling code stays
sequential (handy for "ask password -> retry" flows). While it runs, user input to all
windows except the progress dialog is swallowed, which prevents re-entrant actions.
"""

from __future__ import annotations

import threading
from typing import Any, Callable, Optional

from PyQt6.QtCore import QEvent, QEventLoop, QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import QApplication, QProgressDialog, QWidget

from ..backend.sevenzip import Cancelled

# fn(progress_callback, cancel_event) -> result
TaskFn = Callable[[Callable[[int], None], threading.Event], Any]

_INPUT_EVENTS = {
    QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease,
    QEvent.Type.MouseButtonDblClick, QEvent.Type.KeyPress, QEvent.Type.KeyRelease,
    QEvent.Type.Wheel, QEvent.Type.Drop, QEvent.Type.DragEnter, QEvent.Type.Shortcut,
    QEvent.Type.ShortcutOverride, QEvent.Type.Close,
}


class _Signals(QObject):
    progress = pyqtSignal(int)
    done = pyqtSignal()


class _InputBlocker(QObject):
    """Swallows user input to one window (dialogs opened meanwhile stay usable)."""

    def __init__(self, blocked: Optional[QWidget]):
        super().__init__()
        self.blocked = blocked

    def eventFilter(self, obj, event):  # noqa: N802 (Qt API)
        if self.blocked is not None and event.type() in _INPUT_EVENTS:
            if obj is self.blocked.windowHandle():
                return True
            # Shortcut events go to QAction/QShortcut objects, not widgets: find the
            # widget they belong to.
            w = obj
            while w is not None and not isinstance(w, QWidget):
                w = w.parent()
            if w is not None and w.window() is self.blocked:
                return True
        return False


class _GuiCaller(QObject):
    call = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self.call.connect(self._run, Qt.ConnectionType.QueuedConnection)

    def _run(self, job):
        job()


_gui_caller: Optional[_GuiCaller] = None


def call_in_gui_thread(fn: Callable[[], Any]) -> Any:
    """Run ``fn`` in the GUI thread from a worker thread and wait for its result."""
    global _gui_caller
    if threading.current_thread() is threading.main_thread():
        return fn()
    assert _gui_caller is not None, "run_blocking() creates the GUI caller"
    done = threading.Event()
    box: dict[str, Any] = {}

    def job():
        try:
            box["result"] = fn()
        except BaseException as exc:  # noqa: BLE001
            box["error"] = exc
        finally:
            done.set()

    _gui_caller.call.emit(job)
    done.wait()
    if "error" in box:
        raise box["error"]
    return box.get("result")


def run_blocking(parent: Optional[QWidget], title: str, fn: TaskFn, delay_ms: int = 300) -> Any:
    """Run ``fn`` in a worker thread; returns its result or re-raises its exception.

    Raises ``Cancelled`` when the user pressed Cancel.
    """
    cancel = threading.Event()
    signals = _Signals()
    outcome: dict[str, Any] = {}

    def worker():
        try:
            outcome["result"] = fn(signals.progress.emit, cancel)
        except BaseException as exc:  # noqa: BLE001 - re-raised in the GUI thread
            outcome["error"] = exc
        finally:
            signals.done.emit()

    dialog = QProgressDialog(title, "Cancel", 0, 0, parent)
    dialog.setWindowTitle("linuxfile")
    dialog.setWindowModality(Qt.WindowModality.WindowModal)
    dialog.setAutoClose(False)
    dialog.setAutoReset(False)
    dialog.setMinimumDuration(0)
    dialog.canceled.connect(cancel.set)
    dialog.hide()

    def on_progress(p: int):
        if dialog.maximum() == 0:
            dialog.setRange(0, 100)
        dialog.setValue(p)

    signals.progress.connect(on_progress)
    loop = QEventLoop()
    signals.done.connect(loop.quit)
    show_timer = QTimer()
    show_timer.setSingleShot(True)
    show_timer.timeout.connect(dialog.show)

    global _gui_caller
    if _gui_caller is None:
        _gui_caller = _GuiCaller()
    blocker = _InputBlocker(parent.window() if parent is not None else None)
    app = QApplication.instance()
    app.installEventFilter(blocker)
    QApplication.setOverrideCursor(Qt.CursorShape.BusyCursor)
    thread = threading.Thread(target=worker, name="linuxfile-task", daemon=True)
    try:
        thread.start()
        show_timer.start(delay_ms)
        loop.exec()  # the queued "done" signal quits it, even if emitted already
        thread.join()
    finally:
        show_timer.stop()
        QApplication.restoreOverrideCursor()
        app.removeEventFilter(blocker)
        signals.progress.disconnect()
        dialog.close()
        dialog.deleteLater()
    if "error" in outcome:
        raise outcome["error"]
    if "result" not in outcome:
        raise Cancelled()
    return outcome["result"]

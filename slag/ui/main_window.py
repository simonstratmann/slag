"""Main window: 7-Zip style file list for browsing archives."""

from __future__ import annotations

import html
import os
import subprocess
from typing import Optional

from PyQt6.QtCore import QItemSelectionModel, QSettings, Qt, QTimer, QUrl
from PyQt6.QtGui import QAction, QDesktopServices, QIcon, QKeySequence
from PyQt6.QtWidgets import (
    QAbstractItemView, QApplication, QFileDialog, QInputDialog, QLabel, QLineEdit, QMainWindow, QMenu,
    QMessageBox, QToolBar, QTreeView,
)

from ..backend.extract import ExtractResult, extract_nodes
from ..backend.sevenzip import Cancelled, NotAnArchive, PasswordRequired, SevenZipError
from ..backend.session import Layer, Session
from ..backend.tree import Node
from ..backend.volumes import is_archive_name
from .archive_model import ArchiveModel, COL_NAME, format_size
from .conflict import ConflictResolver
from .extract_dialog import ExtractDialog, recent_targets, remember_target, target_problem
from .opened_files import OpenedFiles
from .settings import SettingsDialog, build_editor_args, editor_command
from .tasks import run_blocking

APP_NAME = "SLAG"  # Simon's Little Archive GUI
APP_ID = "slag"  # config/cache dirs, desktop file
PATH_SEPARATOR = " › "


class FileView(QTreeView):
    """Flat list view; key handling is done by the main window via actions."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setRootIsDecorated(False)
        self.setItemsExpandable(False)
        self.setUniformRowHeights(True)
        self.setAllColumnsShowFocus(True)
        self.setSortingEnabled(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.header().setStretchLastSection(False)
        self.header().setSectionsMovable(True)

    def keyPressEvent(self, event):  # noqa: N802
        # Enter / Return are handled by window actions; don't let the view swallow them.
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            event.ignore()
            return
        super().keyPressEvent(event)


class MainWindow(QMainWindow):
    def __init__(self, session: Optional[Session] = None):
        super().__init__()
        self.session = session or Session()
        self.layer: Optional[Layer] = None
        self.current: Optional[Node] = None
        self.settings = QSettings()
        self.opened = OpenedFiles(self)
        app = QApplication.instance()
        if app is not None:
            app.applicationStateChanged.connect(self._on_app_state)

        self.model = ArchiveModel(self)
        self.view = FileView(self)
        self.view.setModel(self.model)
        self.view.doubleClicked.connect(self._on_activated)
        # Debounced: a large selection change must not recompute the status repeatedly.
        self._status_timer = QTimer(self)
        self._status_timer.setSingleShot(True)
        self._status_timer.setInterval(0)
        self._status_timer.timeout.connect(self._update_status)
        self.view.selectionModel().selectionChanged.connect(self._status_timer.start)
        self.view.sortByColumn(COL_NAME, Qt.SortOrder.AscendingOrder)
        self.setCentralWidget(self.view)

        self.address = QLineEdit(self)
        self.address.setReadOnly(True)
        self.address.setFocusPolicy(Qt.FocusPolicy.ClickFocus)

        self.status_label = QLabel(self)
        self.statusBar().addWidget(self.status_label, 1)

        self._create_actions()
        self.setAcceptDrops(True)
        self._restore_state()
        self._show_directory(None)

    # -- setup ------------------------------------------------------------------------

    def _action(self, text: str, icon: str, shortcuts, slot, tip: str = "") -> QAction:
        act = QAction(QIcon.fromTheme(icon), text, self)
        if shortcuts:
            if not isinstance(shortcuts, (list, tuple)):
                shortcuts = [shortcuts]
            act.setShortcuts([QKeySequence(s) for s in shortcuts])
            act.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        if tip:
            act.setToolTip(tip)
        act.triggered.connect(slot)
        self.addAction(act)
        return act

    def _create_actions(self) -> None:
        self.act_open = self._action("&Open Archive…", "document-open", QKeySequence.StandardKey.Open,
                                     self.open_dialog)
        self.act_up = self._action("&Up", "go-up", ["Backspace", "Alt+Up"], self.go_up,
                                   "Go to the parent folder (Backspace)")
        self.act_enter = self._action("Open", "document-open", ["Return", "Enter"], self.open_current)
        self.act_open_inside = self._action("Open &Inside", "archive-extract", "Ctrl+PgDown",
                                            self.open_inside_current)
        self.act_extract = self._action("&Extract…", "archive-extract", ["F5", "Ctrl+E"],
                                        self.extract_dialog,
                                        "Extract the selection (or everything on this level)")
        self.act_open_outside = self._action("Open &Outside", "document-open", ["Shift+Return", "Shift+Enter"],
                                             self.open_outside_current,
                                             "Open with the associated application")
        self.act_edit = self._action("&Edit", "document-edit", "F4", self.edit_current,
                                     "Open in the configured editor (F4)")
        self.act_settings = self._action("&Settings…", "configure", "Ctrl+,", self.settings_dialog)
        self.act_quit = self._action("&Quit", "application-exit", QKeySequence.StandardKey.Quit, self.close)

        menu = self.menuBar().addMenu("&File")
        menu.addAction(self.act_open)
        menu.addSeparator()
        menu.addAction(self.act_settings)
        menu.addSeparator()
        menu.addAction(self.act_quit)
        self.file_menu = menu
        self.view.customContextMenuRequested.connect(self._context_menu)

        tb = QToolBar("Main", self)
        tb.setObjectName("mainToolbar")
        tb.setMovable(False)
        tb.addAction(self.act_open)
        tb.addAction(self.act_up)
        tb.addAction(self.act_extract)
        tb.addSeparator()
        tb.addWidget(self.address)
        self.toolbar = tb
        self.addToolBar(tb)

    def _restore_state(self) -> None:
        geo = self.settings.value("window/geometry")
        if geo is not None:
            self.restoreGeometry(geo)
        else:
            self.resize(1000, 650)
        header_state = self.settings.value("window/header")
        if header_state is not None:
            self.view.header().restoreState(header_state)
        else:
            self.view.header().resizeSection(COL_NAME, 380)

    def closeEvent(self, event):  # noqa: N802
        if not self.opened.confirm_close():
            event.ignore()
            return
        self.settings.setValue("window/geometry", self.saveGeometry())
        self.settings.setValue("window/header", self.view.header().saveState())
        self.session.close()
        super().closeEvent(event)

    # -- opening archives ---------------------------------------------------------------

    def open_dialog(self) -> None:
        start = ""
        if self.layer is not None:
            start = os.path.dirname(self.layer.outermost.origin_path)
        path, _ = QFileDialog.getOpenFileName(self, "Open Archive", start)
        if path:
            self.open_path(path)

    def open_path(self, path: str) -> bool:
        """Open an archive from disk, replacing whatever is shown. Returns success."""
        layer = self._with_password(
            lambda pw: run_blocking(self, f"Opening {os.path.basename(path)}…",
                                    lambda progress, cancel: self.session.open_file(
                                        path, pw, cancel, progress)),
            os.path.basename(path),
        )
        if layer is None:
            return False
        if self.layer is not None:
            self.session.discard(self.layer, keep_paths=self.opened.paths())
        self.layer = layer
        self._show_directory(layer.root)
        self._show_warning(layer)
        return True

    def _show_warning(self, layer: Layer) -> None:
        if layer.warning and not layer.warning_shown:
            layer.warning_shown = True
            box = QMessageBox(QMessageBox.Icon.Warning, APP_NAME,
                              f"{layer.display_name} is damaged or incomplete. "
                              "Only part of its content may be shown and extractable.",
                              parent=self)
            box.setDetailedText(layer.warning)
            box.exec()

    def _with_password(self, attempt, name: str):
        """Call ``attempt(password)``, prompting for a password while it is required.

        Returns the result, or None when cancelled / failed (errors are shown).
        """
        password = ""
        while True:
            try:
                return attempt(password)
            except PasswordRequired as exc:
                prompt = f"Enter the password for {name}:"
                if exc.wrong_password:
                    prompt = f"Wrong password. Enter the password for {name}:"
                password, ok = QInputDialog.getText(self, "Password", prompt, QLineEdit.EchoMode.Password)
                if not ok:
                    return None
            except Cancelled:
                return None
            except NotAnArchive:
                QMessageBox.warning(self, APP_NAME, f"Cannot open {name} as an archive.")
                return None
            except (SevenZipError, OSError) as exc:
                QMessageBox.critical(self, APP_NAME, f"Error while processing {name}:\n\n{exc}")
                return None

    # -- navigation -------------------------------------------------------------------

    def _show_directory(self, node: Optional[Node], select: Optional[Node] = None) -> None:
        self.current = node
        if node is None:
            self.model.set_directory(_EMPTY_ROOT, has_parent_row=False)
            self.address.setText("")
            self.setWindowTitle(APP_NAME)
            self._update_status()
            return
        has_parent = node.parent is not None or (self.layer is not None and self.layer.parent is not None)
        self.model.set_directory(node, has_parent_row=has_parent)
        self.address.setText(self._address_text())
        self.setWindowTitle(f"{self.layer.outermost.display_name} — {APP_NAME}")
        row = self.model.row_of(select) if select is not None else 0
        if row < 0:
            row = 0
        if self.model.rowCount() > 0:
            idx = self.model.index(row, COL_NAME)
            self.view.selectionModel().setCurrentIndex(
                idx, QItemSelectionModel.SelectionFlag.NoUpdate)
            if select is not None:
                self.view.selectionModel().select(
                    idx, QItemSelectionModel.SelectionFlag.ClearAndSelect
                    | QItemSelectionModel.SelectionFlag.Rows)
            self.view.scrollTo(idx)
        self.view.setFocus()
        self._update_status()

    def _address_text(self) -> str:
        assert self.layer is not None and self.current is not None
        chain = self.layer.chain()
        parts = [chain[0].origin_path or chain[0].archive_path]
        for layer in chain[1:]:
            parts.append(layer.parent_node.path if layer.parent_node is not None else layer.display_name)
        text = PATH_SEPARATOR.join(parts)
        if self.current.path:
            text += PATH_SEPARATOR + self.current.path
        return text

    def go_up(self) -> None:
        if self.layer is None or self.current is None:
            return
        if self.current.parent is not None:
            self._show_directory(self.current.parent, select=self.current)
        elif self.layer.parent is not None:
            # Leave a nested archive: back to the folder containing it.
            came_from = self.layer.parent_node
            self.layer = self.layer.parent
            parent_dir = came_from.parent if came_from is not None else self.layer.root
            self._show_directory(parent_dir, select=came_from)

    def _on_activated(self, index) -> None:
        self._open_row(index.row())

    def open_current(self) -> None:
        idx = self.view.currentIndex()
        if idx.isValid():
            self._open_row(idx.row())

    def _open_row(self, row: int) -> None:
        if self.model.is_parent_row(row):
            self.go_up()
            return
        node = self.model.node_at(row)
        if node is None:
            return
        if node.is_dir:
            self._show_directory(node)
        else:
            self.open_file_node(node)

    def open_file_node(self, node: Node) -> None:
        """Enter on a file: archives are opened inside, other files externally."""
        if is_archive_name(node.name):
            result = self.open_inside(node, quiet_not_archive=True)
            if result is not _NOT_AN_ARCHIVE:
                return
        self.open_external(node)

    def open_outside_current(self) -> None:
        node = self._current_node()
        if node is not None and not node.is_dir:
            self.open_external(node)

    def open_external(self, node: Node) -> bool:
        """Extract to the temp cache and open with the associated application."""
        path = self.extract_to_cache(node)
        if path is None:
            return False
        self.opened.track(path, self._describe(node))
        # Never hand an executable from an (untrusted) archive to the desktop's opener.
        strip_exec_bits(path)
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(path)):
            QMessageBox.warning(self, APP_NAME, f"No application found to open {node.name}.")
            return False
        return True

    def edit_current(self) -> None:
        node = self._current_node()
        if node is not None and not node.is_dir:
            self.edit(node)

    def edit(self, node: Node) -> bool:
        """F4: open a temp copy in the configured editor."""
        path = self.extract_to_cache(node)
        if path is None:
            return False
        command = editor_command(self.settings)
        try:
            args = build_editor_args(command, path)
            process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL, start_new_session=True)
            self.opened.track(path, self._describe(node), process)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, APP_NAME,
                                f"Could not start the editor \"{command}\":\n{exc}\n\n"
                                "Configure it in File → Settings.")
            return False
        return True

    def _describe(self, node: Node) -> str:
        assert self.layer is not None
        names = [layer.display_name for layer in self.layer.chain()]
        return PATH_SEPARATOR.join(names + [node.path])

    def settings_dialog(self) -> None:
        SettingsDialog(self, self.settings).exec()

    def _on_app_state(self, state) -> None:
        if state == Qt.ApplicationState.ApplicationActive and self.isVisible():
            # Defer: don't open a dialog from inside the activation event.
            QTimer.singleShot(0, self.opened.check)

    def open_inside_current(self) -> None:
        node = self._current_node()
        if node is not None and not node.is_dir:
            self.open_inside(node)

    def open_inside(self, node: Node, quiet_not_archive: bool = False):
        """Open a file of the current archive as a nested archive.

        Returns True on success, False on failure; with ``quiet_not_archive`` a file that
        is no archive returns ``_NOT_AN_ARCHIVE`` instead of showing an error.
        """
        assert self.layer is not None
        parent = self.layer
        if self.extract_to_cache(node) is None:
            return False

        def attempt(pw: str):
            try:
                return run_blocking(
                    self, f"Opening {node.name}…",
                    lambda progress, cancel: self.session.open_nested(parent, node, pw, progress, cancel))
            except NotAnArchive:
                if quiet_not_archive:
                    return _NOT_AN_ARCHIVE
                raise

        layer = self._with_password(attempt, node.name)
        if layer is _NOT_AN_ARCHIVE:
            return layer
        if layer is None:
            return False
        self.layer = layer
        self._show_directory(layer.root)
        self._show_warning(layer)
        return True

    def with_layer_password(self, layer: Layer, fn):
        """Run ``fn()`` (which uses ``layer.password``), prompting for the password of
        ``layer`` if an encrypted entry needs it. Returns None on failure/cancel."""

        def attempt(pw: str):
            old = layer.password
            if pw:
                layer.password = pw
            try:
                return fn()
            except BaseException:
                layer.password = old
                raise

        return self._with_password(attempt, layer.display_name)

    def extract_to_cache(self, node: Node, fresh: bool = False) -> Optional[str]:
        """Extract one file of the current layer into the temp cache; returns its path."""
        assert self.layer is not None
        layer = self.layer
        return self.with_layer_password(
            layer,
            lambda: run_blocking(
                self, f"Extracting {node.name}…",
                lambda progress, cancel: self.session.extract_to_cache(
                    layer, node, progress=progress, cancel=cancel, fresh=fresh)),
        )

    def _current_node(self) -> Optional[Node]:
        idx = self.view.currentIndex()
        return self.model.node_at(idx.row()) if idx.isValid() else None

    # -- extraction ---------------------------------------------------------------------

    def nodes_to_extract(self) -> list[Node]:
        """The selection, or everything on the current level when nothing is selected."""
        return self.selected_nodes() or self.model.nodes()

    def default_target(self) -> str:
        assert self.layer is not None
        return os.path.dirname(self.layer.outermost.origin_path)

    def extract_dialog(self) -> None:
        if self.layer is None:
            return
        nodes = self.nodes_to_extract()
        if not nodes:
            return
        if len(nodes) == 1:
            summary = f"Extract <b>{html.escape(nodes[0].name)}</b>"
        else:
            summary = (f"Extract <b>{len(nodes)}</b> items from "
                       f"<b>{html.escape(self.current.path or '/')}</b>")
        dlg = ExtractDialog(self, self.settings, self.default_target(), summary,
                            base_dir=self.default_target())
        if dlg.exec() != ExtractDialog.DialogCode.Accepted:
            return
        self.extract_to(nodes, dlg.target())

    def extract_to(self, nodes: list[Node], target: str) -> Optional[ExtractResult]:
        assert self.layer is not None
        layer = self.layer
        resolver = ConflictResolver(self)

        def work(progress, cancel):
            return extract_nodes(self.session.sevenzip, layer.archive_path, nodes, target,
                                 resolver, password=layer.password, progress=progress, cancel=cancel)

        result = self.with_layer_password(
            layer, lambda: run_blocking(self, f"Extracting to {target}…", work))
        if result is None:
            return None
        if result.errors or result.missing:
            text = "Extraction finished with errors."
            details = "\n".join(result.errors)
            if result.missing:
                details += "\n\nNot extracted:\n" + "\n".join(result.missing)
            box = QMessageBox(QMessageBox.Icon.Warning, APP_NAME, text, parent=self)
            box.setInformativeText("Everything else was extracted.")
            box.setDetailedText(details.strip())
            box.exec()
        msg = f"Extracted {len(result.extracted)} item(s) to {target}"
        if result.cancelled:
            msg = f"Cancelled. {len(result.extracted)} item(s) were extracted to {target}"
        if result.skipped:
            msg += f", skipped {len(result.skipped)}"
        self.statusBar().showMessage(msg, 8000)
        return result

    def _context_menu(self, pos) -> None:
        if self.layer is None:
            return
        idx = self.view.indexAt(pos)
        menu = QMenu(self)
        node = self.model.node_at(idx.row()) if idx.isValid() else None  # None for '..' 
        if node is not None:
            menu.addAction(self.act_enter)
            if not node.is_dir:
                menu.addAction(self.act_open_inside)
                menu.addAction(self.act_open_outside)
                menu.addAction(self.act_edit)
            menu.addSeparator()
        menu.addAction(self.act_extract)
        recents = recent_targets(self.settings)
        if recents:
            sub = menu.addMenu(QIcon.fromTheme("folder-recent"), "Extract &to")
            for path in recents:
                act = sub.addAction(QIcon.fromTheme("folder"), path)
                act.triggered.connect(lambda _=False, p=path: self._extract_to_recent(p))
        menu.exec(self.view.viewport().mapToGlobal(pos))

    def _extract_to_recent(self, path: str) -> None:
        nodes = self.nodes_to_extract()
        if not nodes:
            return
        problem = target_problem(path)
        if problem:
            QMessageBox.warning(self, APP_NAME, f"Cannot extract to {path}:\n{problem}")
            return
        remember_target(self.settings, path)
        self.extract_to(nodes, path)

    # -- selection / status -----------------------------------------------------------

    def selected_nodes(self) -> list[Node]:
        # selection() ranges instead of selectedRows(): the latter is per cell and slow
        # for huge selections.
        rows: set[int] = set()
        for rng in self.view.selectionModel().selection():
            rows.update(range(rng.top(), rng.bottom() + 1))
        nodes = [self.model.node_at(r) for r in sorted(rows)]
        return [n for n in nodes if n is not None]

    def _update_status(self, *args) -> None:
        if self.current is None:
            self.status_label.setText("Open an archive with Ctrl+O or drop one here.")
            return
        nodes = self.model.nodes()
        selected = self.selected_nodes()
        text = f"{len(nodes)} object(s)"
        if selected:
            size = sum(n.total_size for n in selected)
            text += f"    {len(selected)} selected ({format_size(size)} bytes)"
        self.status_label.setText(text)

    # -- drag & drop --------------------------------------------------------------------

    def dragEnterEvent(self, event):  # noqa: N802
        urls = event.mimeData().urls()
        if len(urls) == 1 and urls[0].isLocalFile():
            event.acceptProposedAction()

    def dropEvent(self, event):  # noqa: N802
        urls = event.mimeData().urls()
        if urls and urls[0].isLocalFile():
            event.acceptProposedAction()
            self.open_path(urls[0].toLocalFile())


def strip_exec_bits(path: str) -> None:
    try:
        st = os.lstat(path)
        if not os.path.islink(path):
            os.chmod(path, st.st_mode & ~0o111)
    except OSError:
        pass


_EMPTY_ROOT = Node("", True)
_NOT_AN_ARCHIVE = object()

from PyQt6.QtCore import Qt

from linuxfile.ui.archive_model import COL_NAME, natural_key


def names_in_view(w):
    m = w.model
    return [m.data(m.index(r, COL_NAME)) for r in range(m.rowCount())]


def test_browse_zip(window, archives, qtbot):
    assert window.open_path(str(archives["zip"]))
    assert names_in_view(window) == ["emptydir", "folder", "top.txt", "weird*name?.txt", "weirdXnameY.txt"]
    folder = window.layer.root.child("folder")
    window._open_row(window.model.row_of(folder))
    assert window.current is folder
    assert names_in_view(window) == ["..", "sub", "file.txt", "Ümlaut ä.txt"]
    assert window.address.text().endswith("a.zip › folder")
    window.go_up()
    assert window.current is window.layer.root
    assert window.selected_nodes() == [folder]
    # ".." row goes up as well
    window._open_row(window.model.row_of(folder))
    window._open_row(0)
    assert window.current is window.layer.root


def test_keyboard_navigation(window, archives, qtbot):
    window.show()
    window.open_path(str(archives["zip"]))
    folder = window.layer.root.child("folder")
    window.view.setCurrentIndex(window.model.index(window.model.row_of(folder), 0))
    qtbot.keyClick(window.view, Qt.Key.Key_Return)
    assert window.current is folder
    qtbot.keyClick(window.view, Qt.Key.Key_Backspace)
    assert window.current is window.layer.root


def test_open_not_an_archive(window, archives, monkeypatch):
    shown = []
    monkeypatch.setattr("linuxfile.ui.main_window.QMessageBox.warning", lambda *a: shown.append(a))
    assert not window.open_path(str(archives["plain"]))
    assert shown and window.layer is None


def test_open_with_password(window, archives, monkeypatch):
    answers = iter([("wrong", True), ("secret", True)])
    prompts = []

    def fake_get_text(parent, title, prompt, mode):
        prompts.append(prompt)
        return next(answers)

    monkeypatch.setattr("linuxfile.ui.main_window.QInputDialog.getText", fake_get_text)
    assert window.open_path(str(archives["enc_headers"]))
    assert len(prompts) == 2 and prompts[1].startswith("Wrong password")
    assert window.layer.password == "secret"


def test_sorting_dirs_first(window, archives):
    window.open_path(str(archives["zip"]))
    window.view.sortByColumn(COL_NAME, Qt.SortOrder.DescendingOrder)
    assert names_in_view(window)[:2] == ["folder", "emptydir"]


def test_natural_key():
    assert sorted(["f10", "f2", "F1"], key=natural_key) == ["F1", "f2", "f10"]


def test_nested_navigation(window, archives):
    window.open_path(str(archives["nested"]))
    root = window.layer.root
    inner_dir = root.child("inner")
    window._open_row(window.model.row_of(inner_dir))
    inner_zip = inner_dir.child("inner.zip")
    window._open_row(window.model.row_of(inner_zip))
    assert window.layer.display_name == "inner.zip"
    assert window.address.text().endswith("outer.zip › inner/inner.zip")
    assert names_in_view(window)[0] == ".."
    window._open_row(window.model.row_of(window.layer.root.child("folder")))
    assert window.address.text().endswith("outer.zip › inner/inner.zip › folder")
    window.go_up()
    window.go_up()  # leaves inner.zip
    assert window.layer.display_name == "outer.zip"
    assert window.current is inner_dir
    assert window.selected_nodes() == [inner_zip]


def test_nested_tar_gz(window, archives):
    window.open_path(str(archives["nested"]))
    tgz = window.layer.root.find("inner/a.tar.gz")
    window._show_directory(tgz.parent)
    window._open_row(window.model.row_of(tgz))
    assert window.layer.archive_type == "tar"
    assert "folder" in names_in_view(window)


def test_nested_in_encrypted_parent(window, archives, monkeypatch):
    prompts = []

    def fake_get_text(parent, title, prompt, mode):
        prompts.append(prompt)
        return ("secret", True)

    monkeypatch.setattr("linuxfile.ui.main_window.QInputDialog.getText", fake_get_text)
    window.open_path(str(archives["enc_outer"]))
    assert prompts == []  # zip headers are not encrypted
    node = window.layer.root.find("inner/inner.zip")
    window._show_directory(node.parent)
    assert window.open_inside(node)
    assert len(prompts) == 1
    assert window.layer.parent.password == "secret"
    assert window.layer.password == ""


def test_shortcuts_blocked_while_busy(window, archives, qtbot):
    import time
    from PyQt6.QtTest import QTest
    from linuxfile.ui.tasks import run_blocking
    window.show()
    window.open_path(str(archives["zip"]))
    folder = window.layer.root.child("folder")
    window._show_directory(folder)

    def work(progress, cancel):
        time.sleep(0.3)
        return 1

    from PyQt6.QtCore import QTimer
    QTimer.singleShot(50, lambda: QTest.keyClick(window.view, Qt.Key.Key_Backspace))
    QTimer.singleShot(60, lambda: QTest.keyClick(window.windowHandle(), Qt.Key.Key_Backspace))
    assert run_blocking(window, "busy", work) == 1
    assert window.current is folder


def test_sort_keeps_selection(window, archives):
    from PyQt6.QtCore import QItemSelectionModel
    window.open_path(str(archives["zip"]))
    folder = window.layer.root.child("folder")
    window._show_directory(folder)
    target = folder.child("file.txt")
    window.view.selectionModel().select(
        window.model.index(window.model.row_of(target), 0),
        QItemSelectionModel.SelectionFlag.ClearAndSelect | QItemSelectionModel.SelectionFlag.Rows)
    window.view.sortByColumn(COL_NAME, Qt.SortOrder.DescendingOrder)
    assert names_in_view(window)[0] == ".."
    assert window.selected_nodes() == [target]


def test_sort_performance_with_large_selection(window):
    import time
    from linuxfile.backend.sevenzip import Entry
    from linuxfile.backend.tree import build_tree
    root = build_tree(Entry(f"f{i}.txt", False, size=i) for i in range(50_000))
    window.model.set_directory(root, has_parent_row=True)
    window.view.selectAll()
    t = time.monotonic()
    window.view.sortByColumn(1, Qt.SortOrder.DescendingOrder)
    window._update_status()
    assert time.monotonic() - t < 5
    assert len(window.selected_nodes()) == 50_000


def test_open_replaces_and_frees_previous_cache(window, archives):
    import os
    window.open_path(str(archives["nested"]))
    node = window.layer.root.find("inner/a.tar.gz")
    window._show_directory(node.parent)
    window.open_inside(node)
    dirs = window.layer.outermost.cache_dirs()
    window.open_path(str(archives["zip"]))
    assert not any(os.path.exists(d) for d in dirs)


def test_leave_nested_tar_gz_selects_it(window, archives):
    window.open_path(str(archives["nested"]))
    node = window.layer.root.find("inner/a.tar.gz")
    window._show_directory(node.parent)
    window.open_inside(node)
    assert window.address.text().endswith("outer.zip › inner/a.tar.gz")
    window.go_up()
    assert window.selected_nodes() == [node]


def test_cancel_password_prompt(window, archives, monkeypatch):
    monkeypatch.setattr("linuxfile.ui.main_window.QInputDialog.getText", lambda *a: ("", False))
    assert not window.open_path(str(archives["enc_headers"]))
    assert window.layer is None


def test_damaged_archive_shows_warning(window, odd_archives, monkeypatch):
    shown = []
    monkeypatch.setattr("linuxfile.ui.main_window.QMessageBox.exec", lambda self: shown.append(self.text()))
    assert window.open_path(str(odd_archives["trunc"]))
    assert shown and "damaged" in shown[0]

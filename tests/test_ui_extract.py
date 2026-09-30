import os
from pathlib import Path

from PyQt6.QtCore import QItemSelectionModel, QSettings

from linuxfile.backend.extract import ConflictAction
from linuxfile.ui import conflict
from linuxfile.ui.extract_dialog import ExtractDialog, expand_path, recent_targets, remember_target


def select(window, *nodes):
    sm = window.view.selectionModel()
    sm.clearSelection()
    for n in nodes:
        idx = window.model.index(window.model.row_of(n), 0)
        sm.select(idx, QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)


def listdir(p):
    return sorted(x.name for x in Path(p).iterdir())


def test_extract_selected_file_without_parents(window, archives, tmp_path):
    window.open_path(str(archives["zip"]))
    folder = window.layer.root.child("folder")
    window._show_directory(folder)
    select(window, folder.child("file.txt"))
    target = tmp_path / "out"
    window.extract_to(window.nodes_to_extract(), str(target))
    assert listdir(target) == ["file.txt"]


def test_extract_nothing_selected_extracts_level(window, archives, tmp_path):
    window.open_path(str(archives["zip"]))
    folder = window.layer.root.child("folder")
    window._show_directory(folder)
    window.view.selectionModel().clearSelection()
    target = tmp_path / "out"
    window.extract_to(window.nodes_to_extract(), str(target))
    assert listdir(target) == ["file.txt", "sub", "Ümlaut ä.txt"]


def test_parent_row_only_selected_means_everything(window, archives):
    window.open_path(str(archives["zip"]))
    folder = window.layer.root.child("folder")
    window._show_directory(folder)
    window.view.selectionModel().select(
        window.model.index(0, 0),
        QItemSelectionModel.SelectionFlag.ClearAndSelect | QItemSelectionModel.SelectionFlag.Rows)
    assert len(window.nodes_to_extract()) == 3


def test_extract_from_nested(window, archives, tmp_path):
    window.open_path(str(archives["nested"]))
    node = window.layer.root.find("inner/a.tar.gz")
    window._show_directory(node.parent)
    window.open_inside(node)
    sub = window.layer.root.find("folder/sub")
    window._show_directory(sub.parent)
    select(window, sub)
    window.extract_to(window.nodes_to_extract(), str(tmp_path / "t"))
    assert listdir(tmp_path / "t") == ["sub"]
    assert (tmp_path / "t" / "sub" / "a.txt").read_text() == "a"


def test_conflict_dialog_called_in_gui_thread(window, archives, tmp_path, monkeypatch):
    import threading
    calls = []

    def fake_ask(self, dst, src):
        calls.append(threading.current_thread() is threading.main_thread())
        return ConflictAction.RENAME

    monkeypatch.setattr(conflict.ConflictResolver, "_ask", fake_ask)
    window.open_path(str(archives["zip"]))
    top = window.layer.root.child("top.txt")
    (tmp_path / "top.txt").write_text("existing")
    window.extract_to([top], str(tmp_path))
    assert calls == [True]
    assert (tmp_path / "top.txt").read_text() == "existing"
    assert (tmp_path / "top (1).txt").read_text() == "top"


def test_extract_encrypted_entries_prompts(window, archives, tmp_path, monkeypatch):
    monkeypatch.setattr("linuxfile.ui.main_window.QInputDialog.getText",
                        lambda *a: ("secret", True))
    window.open_path(str(archives["enc_zip"]))
    window.extract_to(window.nodes_to_extract(), str(tmp_path / "o"))
    assert listdir(tmp_path / "o") == ["top.txt"]
    assert window.layer.password == "secret"


def test_recent_targets(isolated_settings):
    s = QSettings()
    s.setValue("extract/recentCount", 3)
    for p in ["/a", "/b", "/c", "/b/", "/d"]:
        remember_target(s, p)
    assert recent_targets(s) == ["/d", "/b", "/c"]


def test_expand_path(monkeypatch):
    monkeypatch.setenv("HOME", "/home/u")
    assert expand_path("~/x", "/base") == "/home/u/x"
    assert expand_path("rel/dir", "/base") == "/base/rel/dir"
    assert expand_path("file:///tmp/a%20b", "/base") == "/tmp/a b"
    assert expand_path("  /x/y/  ", "/base") == "/x/y"


def test_extract_dialog(qtbot, isolated_settings, tmp_path):
    s = QSettings()
    remember_target(s, str(tmp_path / "recent1"))
    dlg = ExtractDialog(None, s, str(tmp_path), "Extract <b>x</b>", base_dir=str(tmp_path))
    qtbot.addWidget(dlg)
    assert dlg.recent_list.count() == 1
    dlg.recent_list.setCurrentRow(0)
    assert dlg.target() == str(tmp_path / "recent1")
    dlg.path_edit.setText("sub")
    assert dlg.target() == str(tmp_path / "sub")
    dlg.accept()
    assert recent_targets(s)[0] == str(tmp_path / "sub")
    dlg.path_edit.setText("   ")
    assert not dlg.ok_button.isEnabled()

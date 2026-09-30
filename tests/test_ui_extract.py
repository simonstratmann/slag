import os
from pathlib import Path

from PyQt6.QtCore import QItemSelectionModel, QSettings

from slag.backend.extract import ConflictAction
from slag.ui import conflict
from slag.ui.extract_dialog import ExtractDialog, expand_path, recent_targets, remember_target


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
    monkeypatch.setattr("slag.ui.main_window.QInputDialog.getText",
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
    # focusing the list must not overwrite the typed path; clicking picks
    dlg.recent_list.setCurrentRow(0)
    assert dlg.target() == str(tmp_path)
    dlg.recent_list.itemClicked.emit(dlg.recent_list.item(0))
    assert dlg.target() == str(tmp_path / "recent1")
    dlg.path_edit.setText("edited")
    dlg.recent_list.itemClicked.emit(dlg.recent_list.item(0))  # same item again
    assert dlg.target() == str(tmp_path / "recent1")
    dlg.path_edit.setText("sub")
    assert dlg.target() == str(tmp_path / "sub")
    dlg.accept()
    assert recent_targets(s)[0] == str(tmp_path / "sub")
    dlg.path_edit.setText("   ")
    assert not dlg.ok_button.isEnabled()


def test_directory_completions(tmp_path, monkeypatch):
    from slag.ui.extract_dialog import directory_completions
    for d in ["delta", "deltb", ".hidden", "other"]:
        (tmp_path / d).mkdir()
    (tmp_path / "delfile").write_text("")
    assert directory_completions(f"{tmp_path}/del", "/") == [f"{tmp_path}/delta/", f"{tmp_path}/deltb/"]
    assert directory_completions("de", str(tmp_path)) == ["delta/", "deltb/"]
    assert directory_completions(f"{tmp_path}/.h", "/") == [f"{tmp_path}/.hidden/"]
    assert f"{tmp_path}/.hidden/" not in directory_completions(f"{tmp_path}/", "/")
    monkeypatch.setenv("HOME", str(tmp_path))
    assert directory_completions("~/ot", "/") == ["~/other/"]
    assert directory_completions(f"{tmp_path}/missing/x", "/") == []


def test_completer_popup_while_typing(qtbot, isolated_settings, tmp_path):
    from PyQt6.QtTest import QTest
    (tmp_path / "delta").mkdir()
    (tmp_path / "deltb").mkdir()
    dlg = ExtractDialog(None, QSettings(), str(tmp_path / "x"), "s", base_dir=str(tmp_path))
    qtbot.addWidget(dlg)
    dlg.show()
    dlg.path_edit.clear()
    QTest.keyClicks(dlg.path_edit, f"{tmp_path}/de")
    assert dlg.completer.completionCount() == 2


def test_target_validation(qtbot, isolated_settings, tmp_path):
    from slag.ui.extract_dialog import target_problem
    (tmp_path / "file").write_text("")
    assert target_problem(str(tmp_path / "file"))
    assert target_problem(str(tmp_path / "new" / "deeper")) is None
    ro = tmp_path / "ro"
    ro.mkdir()
    ro.chmod(0o500)
    try:
        if not os.access(ro, os.W_OK):  # root ignores permissions
            assert target_problem(str(ro / "x"))
    finally:
        ro.chmod(0o700)
    s = QSettings()
    dlg = ExtractDialog(None, s, str(tmp_path / "file"), "s", base_dir=str(tmp_path))
    qtbot.addWidget(dlg)
    dlg.accept()
    assert dlg.result() != ExtractDialog.DialogCode.Accepted
    assert not dlg.error_label.isHidden()
    assert recent_targets(s) == []


def test_recent_double_click_extracts(qtbot, isolated_settings, tmp_path):
    s = QSettings()
    remember_target(s, str(tmp_path / "r"))
    dlg = ExtractDialog(None, s, str(tmp_path), "s", base_dir=str(tmp_path))
    qtbot.addWidget(dlg)
    dlg.recent_list.itemDoubleClicked.emit(dlg.recent_list.item(0))
    assert dlg.result() == ExtractDialog.DialogCode.Accepted
    assert dlg.target() == str(tmp_path / "r")


def test_summary_is_escaped(window, tmp_path, monkeypatch):
    import zipfile
    arc = tmp_path / "x.zip"
    with zipfile.ZipFile(arc, "w") as z:
        z.writestr("a<b>.txt", "x")
    window.open_path(str(arc))
    seen = []

    class FakeDialog:
        DialogCode = ExtractDialog.DialogCode

        def __init__(self, parent, settings, default, summary, base_dir):
            seen.append(summary)

        def exec(self):
            return 0

    monkeypatch.setattr("slag.ui.main_window.ExtractDialog", FakeDialog)
    window.extract_dialog()
    assert "a&lt;b&gt;.txt" in seen[0]


def test_real_conflict_dialog_hides_progress(window, archives, tmp_path, monkeypatch):
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QApplication, QMessageBox, QProgressDialog
    from slag.ui import tasks
    from PyQt6.QtWidgets import QDialog
    monkeypatch.setattr(QMessageBox, "exec", lambda self: QDialog.exec(self))  # real exec
    window.open_path(str(archives["zip"]))
    (tmp_path / "top.txt").write_text("old")
    (tmp_path / "file.txt").write_text("old")
    states = []

    def answer():
        box = next(w for w in QApplication.topLevelWidgets()
                   if isinstance(w, QMessageBox) and w.isVisible())
        states.append([d.isVisible() for d, _ in tasks._active])
        box.checkBox().setChecked(True)  # apply to all
        next(b for b in box.buttons() if b.text() == "&Overwrite").click()

    QTimer.singleShot(600, answer)  # later than the 300 ms progress delay
    folder = window.layer.root.child("folder")
    res = window.extract_to([window.layer.root.child("top.txt"), folder.child("file.txt")], str(tmp_path))
    assert states == [[False]]
    assert (tmp_path / "top.txt").read_text() == "top"
    assert (tmp_path / "file.txt").read_text() == "file"  # second conflict: remembered


def test_extract_empty_level(window, archives, tmp_path):
    window.open_path(str(archives["zip"]))
    window._show_directory(window.layer.root.child("emptydir"))
    assert window.nodes_to_extract() == []
    window.extract_dialog()  # nothing to do, no dialog

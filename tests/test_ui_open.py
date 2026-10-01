import os
import time

import pytest
from PyQt6.QtCore import QSettings

from slag.ui import main_window as mw
from slag.ui.opened_files import OpenedFiles
from slag.ui.settings import EDITOR_KEY, WEB_TREE_LIMIT_KEY, build_editor_args


@pytest.fixture
def opened_urls(monkeypatch):
    urls = []
    monkeypatch.setattr(mw.QDesktopServices, "openUrl", lambda url: urls.append(url.toLocalFile()) or True)
    return urls


class _Calls(list):
    popen = None


@pytest.fixture
def popen_calls(monkeypatch):
    calls = _Calls()

    class FakePopen:
        running = False

        def __init__(self, args, **kw):
            calls.append((args, kw))

        def poll(self):
            return None if FakePopen.running else 0

    import types
    monkeypatch.setattr(mw, "subprocess", types.SimpleNamespace(Popen=FakePopen, DEVNULL=-3))
    calls.popen = FakePopen
    return calls


def test_enter_on_file_opens_with_associated_app(window, archives, opened_urls):
    window.open_path(str(archives["zip"]))
    node = window.layer.root.find("folder/file.txt")
    window._show_directory(node.parent)
    window._open_row(window.model.row_of(node))
    assert len(opened_urls) == 1
    assert opened_urls[0].endswith("/folder/file.txt")
    assert open(opened_urls[0]).read() == "file"
    assert window.current is node.parent  # still browsing


def test_enter_on_archive_named_non_archive_opens_externally(window, tmp_path, opened_urls, sz):
    import zipfile
    arc = tmp_path / "fake.zip"
    with zipfile.ZipFile(arc, "w") as z:
        z.writestr("notreally.zip", "plain text")
    window.open_path(str(arc))
    node = window.layer.root.child("notreally.zip")
    window._open_row(window.model.row_of(node))
    assert len(opened_urls) == 1 and window.layer.parent is None


def test_shift_enter_opens_archive_outside(window, archives, opened_urls):
    window.open_path(str(archives["nested"]))
    node = window.layer.root.find("inner/inner.zip")
    window._show_directory(node.parent)
    window.view.setCurrentIndex(window.model.index(window.model.row_of(node), 0))
    window.open_outside_current()
    assert opened_urls and opened_urls[0].endswith("inner.zip")
    assert window.layer.parent is None


def test_f4_uses_configured_editor(window, archives, popen_calls):
    QSettings().setValue(EDITOR_KEY, "myeditor --flag %f --line 1")
    window.open_path(str(archives["zip"]))
    node = window.layer.root.child("top.txt")
    window.view.setCurrentIndex(window.model.index(window.model.row_of(node), 0))
    window.edit_current()
    (args, kw), = popen_calls
    assert args[:2] == ["myeditor", "--flag"] and args[2].endswith("/top.txt") and args[3:] == ["--line", "1"]
    assert kw["start_new_session"]


def test_f4_missing_editor_shows_error(window, archives, monkeypatch):
    QSettings().setValue(EDITOR_KEY, "/nonexistent/editor-xyz")
    shown = []
    monkeypatch.setattr(mw.QMessageBox, "warning", lambda *a: shown.append(a[2]))
    window.open_path(str(archives["zip"]))
    assert not window.edit(window.layer.root.child("top.txt"))
    assert shown and "editor-xyz" in shown[0]


def test_build_editor_args():
    assert build_editor_args("kate", "/x y") == ["kate", "/x y"]
    assert build_editor_args("konsole -e 'n vim' %f", "/f") == ["konsole", "-e", "n vim", "/f"]
    with pytest.raises(ValueError):
        build_editor_args("   ", "/f")


def test_modified_temp_copy_is_reported(window, archives, popen_calls, monkeypatch):
    offers = []
    monkeypatch.setattr(OpenedFiles, "_offer_save",
                        lambda self, f, what, info, allow_cancel=False: offers.append(what) or True)
    window.open_path(str(archives["zip"]))
    node = window.layer.root.child("top.txt")
    window.edit(node)
    path = popen_calls[0][0][-1]
    window.opened.check()
    assert offers == []
    time.sleep(0.01)
    with open(path, "a") as fh:
        fh.write(" edited")
    window.opened.check()
    window.opened.check()  # reported only once per change
    assert offers == ["was modified"]
    # same copy is reused when opened again (edits are not lost)
    window.edit(node)
    assert popen_calls[1][0][-1] == path


def test_switching_archive_keeps_opened_temp_copies(window, archives, popen_calls):
    window.open_path(str(archives["zip"]))
    window.edit(window.layer.root.child("top.txt"))
    path = popen_calls[0][0][-1]
    other_dirs = []
    window.open_path(str(archives["nested"]))
    node = window.layer.root.find("inner/inner.zip")
    window._show_directory(node.parent)
    window.open_inside(node)
    other_dirs = window.layer.outermost.cache_dirs()
    assert os.path.exists(path)  # still opened in the editor: kept
    window.open_path(str(archives["7z"]))
    assert os.path.exists(path)
    assert not any(os.path.exists(d) for d in other_dirs)  # nothing opened there: freed


def test_close_asks_while_editor_runs(window, archives, popen_calls, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox
    answers = []
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a: answers.append(a[2]) or QMessageBox.StandardButton.No))
    window.open_path(str(archives["zip"]))
    window.edit(window.layer.root.child("top.txt"))
    popen_calls.popen.running = True
    window.show()
    assert not window.close()
    assert answers and "top.txt" in answers[0]
    popen_calls.popen.running = False
    assert window.close()


def test_check_is_deferred_during_task_and_modal(window, archives, popen_calls, monkeypatch):
    from slag.ui import tasks
    offers = []
    monkeypatch.setattr(OpenedFiles, "_offer_save",
                        lambda self, f, what, info, allow_cancel=False: offers.append(what) or True)
    window.open_path(str(archives["zip"]))
    window.edit(window.layer.root.child("top.txt"))
    with open(popen_calls[0][0][-1], "a") as fh:
        fh.write("x")
    during = []
    tasks.run_blocking(window, "busy", lambda p, c: during.append(window.opened.check()) or 1)
    # check() from inside the worker is a no-op because a task is active
    assert offers == []
    window.opened.check()
    assert offers == ["was modified"]


def test_executable_bits_stripped_before_opening(window, tmp_path, opened_urls):
    import stat
    import tarfile
    src = tmp_path / "run.sh"
    src.write_text("#!/bin/sh\necho hi\n")
    src.chmod(0o755)
    arc = tmp_path / "x.tar"
    with tarfile.open(arc, "w") as t:
        t.add(src, "run.sh")
    window.open_path(str(arc))
    window.open_external(window.layer.root.child("run.sh"))
    mode = os.stat(opened_urls[0]).st_mode
    assert not mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def test_lone_001_is_not_an_archive(window, tmp_path, opened_urls):
    import zipfile
    arc = tmp_path / "x.zip"
    with zipfile.ZipFile(arc, "w") as z:
        z.writestr("data.001", "just data")
    window.open_path(str(arc))
    window._open_row(window.model.row_of(window.layer.root.child("data.001")))
    assert window.layer.parent is None and len(opened_urls) == 1


def test_editor_percent_f_quoting():
    assert build_editor_args("sh -c 'vim %f'", "/t/a;rm x.txt") == ["sh", "-c", "vim '/t/a;rm x.txt'"]
    assert build_editor_args("kate %f", "/t/a b") == ["kate", "/t/a b"]


def test_keys_enter_shift_enter_f4(window, archives, qtbot, monkeypatch):
    from PyQt6.QtCore import Qt
    calls = []
    window.show()
    window.open_path(str(archives["zip"]))
    monkeypatch.setattr(window, "open_external", lambda n: calls.append(("ext", n.name)))
    monkeypatch.setattr(window, "edit", lambda n: calls.append(("edit", n.name)))
    node = window.layer.root.child("top.txt")
    window.view.setCurrentIndex(window.model.index(window.model.row_of(node), 0))
    qtbot.keyClick(window.view, Qt.Key.Key_Return)
    qtbot.keyClick(window.view, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
    qtbot.keyClick(window.view, Qt.Key.Key_F4)
    assert calls == [("ext", "top.txt"), ("ext", "top.txt"), ("edit", "top.txt")]


def test_settings_dialog(qtbot, isolated_settings):
    from slag.ui.settings import RECENT_COUNT_KEY, SettingsDialog
    s = QSettings()
    dlg = SettingsDialog(None, s)
    qtbot.addWidget(dlg)
    dlg.editor_edit.setText("kate 'unclosed")
    dlg.accept()
    assert s.value(EDITOR_KEY) is None
    dlg.editor_edit.setText("konsole -e nvim %f")
    dlg.recent_spin.setValue(3)
    dlg.accept()
    assert s.value(EDITOR_KEY) == "konsole -e nvim %f"
    assert int(s.value(RECENT_COUNT_KEY)) == 3


@pytest.fixture
def web_zip(tmp_path):
    import zipfile
    path = tmp_path / "web.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("site/index.html", '<link href="../shared/a.css">')
        z.writestr("shared/a.css", "x" * 3_000_000)
        z.writestr("docs/api/page.html", '<img src="../img/a.png"><script src="x.js">')
        z.writestr("docs/api/x.js", "1")
        z.writestr("docs/img/a.png", "png")
        z.writestr("other/b.txt", "b")
    return path


def test_enter_on_web_page_extracts_whole_archive(window, web_zip, opened_urls, monkeypatch):
    # unpacked 3 MB but a small file: no question with a 1 MB limit
    QSettings().setValue(WEB_TREE_LIMIT_KEY, 1)
    monkeypatch.setattr(mw.QMessageBox, "exec", lambda box: pytest.fail(box.text()))
    window.open_path(str(web_zip))
    window.open_external(window.layer.root.find("site/index.html"))
    page, = opened_urls
    assert page.endswith("/site/index.html")
    assert os.path.isfile(os.path.join(os.path.dirname(page), "..", "shared", "a.css"))


def _answer_question(monkeypatch, button_text):
    asked = []

    def exec_(box):
        asked.append(box.text())
        for b in box.buttons():
            if b.text().replace("&", "") == button_text:
                box._clicked = b
        return 0

    monkeypatch.setattr(mw.QMessageBox, "exec", exec_)
    monkeypatch.setattr(mw.QMessageBox, "clickedButton", lambda box: getattr(box, "_clicked", None))
    return asked


@pytest.mark.parametrize("answer, extracted", [
    ("Extract Whole Archive", {"docs", "other", "shared", "site"}),
    ("Only This Folder", {"docs"}),  # page.html links to ../img: docs/, not docs/api/
    ("Cancel", None),
])
def test_large_archive_asks_before_extracting_web_page(window, web_zip, opened_urls, monkeypatch,
                                                       answer, extracted):
    QSettings().setValue(WEB_TREE_LIMIT_KEY, 0)  # any archive is "large"
    asked = _answer_question(monkeypatch, answer)
    window.open_path(str(web_zip))
    window.open_external(window.layer.root.find("docs/api/page.html"))
    assert len(asked) == 1 and "whole archive" in asked[0] and "<tt>docs/</tt>" in asked[0]
    if extracted is None:
        assert opened_urls == []
        return
    page, = opened_urls
    root = page[:-len("docs/api/page.html")]
    assert set(os.listdir(root)) == extracted
    assert os.path.isfile(os.path.join(root, "docs", "img", "a.png"))
    assert os.path.isfile(os.path.join(root, "docs", "api", "x.js"))


def test_large_archive_page_linking_to_top_offers_no_folder(window, web_zip, opened_urls,
                                                             monkeypatch):
    QSettings().setValue(WEB_TREE_LIMIT_KEY, 0)
    buttons = []
    monkeypatch.setattr(mw.QMessageBox, "exec",
                        lambda box: buttons.extend(b.text().replace("&", "") for b in box.buttons()))
    window.open_path(str(web_zip))
    window.open_external(window.layer.root.find("site/index.html"))  # ../shared: top level
    assert "Only This Folder" not in buttons and "Extract Whole Archive" in buttons


@pytest.mark.parametrize("password, linked", [("secret", True), (None, False)])
def test_web_page_in_partly_encrypted_archive(window, tmp_path, opened_urls, monkeypatch,
                                              password, linked):
    from tests.test_session import partly_encrypted_zip
    monkeypatch.setattr(mw.QInputDialog, "getText",
                        lambda *a, **k: (password or "", password is not None))
    window.open_path(str(partly_encrypted_zip(tmp_path)))
    window.open_external(window.layer.root.find("site/index.html"))
    page, = opened_urls
    assert open(page).read() == "<p>page</p>"
    assert os.path.isfile(os.path.join(os.path.dirname(page), "a.css")) == linked

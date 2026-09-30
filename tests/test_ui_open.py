import os
import time

import pytest
from PyQt6.QtCore import QSettings

from linuxfile.ui import main_window as mw
from linuxfile.ui.opened_files import OpenedFiles
from linuxfile.ui.settings import EDITOR_KEY, build_editor_args


@pytest.fixture
def opened_urls(monkeypatch):
    urls = []
    monkeypatch.setattr(mw.QDesktopServices, "openUrl", lambda url: urls.append(url.toLocalFile()) or True)
    return urls


@pytest.fixture
def popen_calls(monkeypatch):
    calls = []

    class FakePopen:
        def __init__(self, args, **kw):
            calls.append((args, kw))

    import types
    monkeypatch.setattr(mw, "subprocess", types.SimpleNamespace(Popen=FakePopen, DEVNULL=-3))
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


def test_opening_other_archive_asks_about_modified_copies(window, archives, popen_calls, monkeypatch):
    answers = [False]
    monkeypatch.setattr(OpenedFiles, "_offer_save",
                        lambda self, f, what, info, allow_cancel=False: answers.pop(0) if answers else True)
    window.open_path(str(archives["zip"]))
    window.edit(window.layer.root.child("top.txt"))
    path = popen_calls[0][0][-1]
    with open(path, "a") as fh:
        fh.write(" edited")
    assert not window.open_path(str(archives["7z"]))  # user cancelled
    assert window.layer.display_name == "a.zip" and os.path.exists(path)

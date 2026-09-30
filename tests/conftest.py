import os
import subprocess
from pathlib import Path

import pytest

from slag.backend.sevenzip import SevenZip


def run(args, cwd):
    subprocess.run(args, cwd=cwd, check=True, shell=isinstance(args, str), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def make_tree(root: Path) -> Path:
    """
    src/
      top.txt
      folder/file.txt
      folder/sub/a.txt
      folder/Ümlaut ä.txt
      weird*name?.txt
      emptydir/
    """
    src = root / "src"
    (src / "folder" / "sub").mkdir(parents=True)
    (src / "emptydir").mkdir()
    (src / "top.txt").write_text("top")
    (src / "folder" / "file.txt").write_text("file")
    (src / "folder" / "sub" / "a.txt").write_text("a")
    (src / "folder" / "Ümlaut ä.txt").write_text("umlaut")
    (src / "weird*name?.txt").write_text("weird")
    (src / "weirdXnameY.txt").write_text("not weird")
    return src


@pytest.fixture(scope="session")
def sz():
    return SevenZip()


@pytest.fixture(scope="session")
def archives(tmp_path_factory):
    root = tmp_path_factory.mktemp("archives")
    src = make_tree(root)
    out = {}
    run(["zip", "-qr", str(root / "a.zip"), "."], cwd=src)
    out["zip"] = root / "a.zip"
    run(["tar", "czf", str(root / "a.tar.gz"), "."], cwd=src)  # entries start with "./"
    out["tar.gz"] = root / "a.tar.gz"
    run(["tar", "cf", str(root / "a.tar"), "."], cwd=src)
    out["tar"] = root / "a.tar"
    run(["7z", "a", str(root / "a.7z"), "."], cwd=src)
    out["7z"] = root / "a.7z"
    # split 7z: need some bulk so it spans volumes
    big = root / "bigsrc"
    big.mkdir()
    (big / "big.bin").write_bytes(os.urandom(200_000))
    (big / "small.txt").write_text("small")
    run(["7z", "a", "-v50k", str(root / "split.7z"), "."], cwd=big)
    out["split7z"] = root / "split.7z.001"
    run(["zip", "-q", "-s", "64k", "-r", str(root / "splitzip.zip"), "."], cwd=big)
    out["splitzip"] = root / "splitzip.zip"
    run(["7z", "a", "-psecret", "-mhe=on", str(root / "enc_headers.7z"), "."], cwd=src)
    out["enc_headers"] = root / "enc_headers.7z"
    run(["7z", "a", "-psecret", str(root / "enc.zip"), "top.txt"], cwd=src)
    out["enc_zip"] = root / "enc.zip"
    # nested: outer.zip/{inner/inner.zip, inner/a.tar.gz, note.txt.gz}
    nest = root / "nestsrc"
    (nest / "inner").mkdir(parents=True)
    run(["cp", str(root / "a.zip"), str(nest / "inner" / "inner.zip")], cwd=root)
    run(["cp", str(root / "a.tar.gz"), str(nest / "inner" / "a.tar.gz")], cwd=root)
    (nest / "note.txt").write_text("just text")
    run(["gzip", "note.txt"], cwd=nest)
    run(["zip", "-qr", str(root / "outer.zip"), "."], cwd=nest)
    out["nested"] = root / "outer.zip"
    run(["7z", "a", "-psecret", str(root / "enc_outer.zip"), "inner/inner.zip"], cwd=nest)
    out["enc_outer"] = root / "enc_outer.zip"
    splitnest = root / "splitnest"
    splitnest.mkdir()
    run("cp " + str(root) + "/split.7z.0* " + str(splitnest), cwd=root)
    run(["zip", "-qr", str(root / "splitouter.zip"), "."], cwd=splitnest)
    out["splitouter"] = root / "splitouter.zip"
    (root / "plain.txt").write_text("not an archive")
    out["plain"] = root / "plain.txt"
    return out


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture
def isolated_settings(tmp_path, monkeypatch):
    """Keep QSettings of tests away from the real user configuration."""
    from PyQt6.QtCore import QCoreApplication, QSettings
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    QSettings.setPath(QSettings.Format.NativeFormat, QSettings.Scope.UserScope, str(tmp_path / "config"))
    QCoreApplication.setOrganizationName("slag-test")
    QCoreApplication.setApplicationName("slag-test")
    return tmp_path / "config"


@pytest.fixture
def window(qtbot, isolated_settings, tmp_path):
    from slag.backend.session import Session
    from slag.ui.main_window import MainWindow
    session = Session(cache_root=str(tmp_path / "cache"))
    (tmp_path / "cache").mkdir(exist_ok=True)
    w = MainWindow(session)
    qtbot.addWidget(w)
    return w


ODD_NAMES = [" lead.txt", "trail.txt ", 'quo"te.txt', "back\\slash.txt", "nel\u0085name.txt",
             "dir/x.txt"]


@pytest.fixture(scope="session")
def odd_archives(tmp_path_factory):
    """Archives with tricky names / features, built with Python's zipfile/tarfile."""
    import io
    import tarfile
    import zipfile

    root = tmp_path_factory.mktemp("odd")
    out = {}
    with zipfile.ZipFile(root / "odd.zip", "w") as z:
        for n in ODD_NAMES:
            z.writestr(n, "x")
        z.comment = b"hello\n----------\nPath = PHANTOM.exe\n"
    out["odd"] = root / "odd.zip"

    with tarfile.open(root / "links.tar", "w") as t:
        ti = tarfile.TarInfo("d/orig")
        ti.size = 1
        t.addfile(ti, io.BytesIO(b"x"))
        ti = tarfile.TarInfo("d/hard")
        ti.type = tarfile.LNKTYPE
        ti.linkname = "d/orig"
        t.addfile(ti)
        ti = tarfile.TarInfo("d/sym")
        ti.type = tarfile.SYMTYPE
        ti.linkname = "orig"
        t.addfile(ti)
    out["links"] = root / "links.tar"

    # Stored zip with one corrupted file -> CRC error for that file only
    with zipfile.ZipFile(root / "crc.zip", "w", compression=zipfile.ZIP_STORED) as z:
        z.writestr("good.txt", "good content")
        z.writestr("bad.txt", "B" * 100)
    data = bytearray((root / "crc.zip").read_bytes())
    pos = data.index(b"B" * 100)
    data[pos + 10] = ord("X")
    (root / "crc.zip").write_bytes(bytes(data))
    out["crc"] = root / "crc.zip"

    # Truncated zip: central directory missing
    with zipfile.ZipFile(root / "full.zip", "w", compression=zipfile.ZIP_STORED) as z:
        z.writestr("first.txt", "first")
        z.writestr("second.bin", os.urandom(5000))
    full = (root / "full.zip").read_bytes()
    (root / "trunc.zip").write_bytes(full[: len(full) - 200])
    out["trunc"] = root / "trunc.zip"
    return out


@pytest.fixture(autouse=True)
def no_modal_dialogs(monkeypatch):
    """A modal dialog would block the (offscreen) test run forever: fail instead."""
    from PyQt6.QtWidgets import QDialog, QFileDialog, QInputDialog, QMessageBox

    def fail(*args, **kwargs):
        raise AssertionError(f"unexpected modal dialog: {args[1:3]!r}")

    for name in ("critical", "warning", "information", "question"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(fail))
    monkeypatch.setattr(QMessageBox, "exec", fail)
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(fail))
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(fail))
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", staticmethod(fail))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(fail))

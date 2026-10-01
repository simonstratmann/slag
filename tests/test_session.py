import os
import subprocess
import threading
import zipfile

import pytest

from slag.backend.sevenzip import Cancelled, PasswordRequired, SevenZipError
from slag.backend.session import Session


def test_open_tar_gz_unwraps(sz, archives, tmp_path):
    s = Session(sz, cache_root=str(tmp_path))
    layer = s.open_file(str(archives["tar.gz"]))
    assert layer.archive_type == "tar"
    assert layer.display_name == "a.tar.gz"
    assert layer.origin_path == str(archives["tar.gz"])
    assert layer.root.find("folder/sub/a.txt") is not None
    assert layer.parent is None


def test_open_split_from_later_volume(sz, archives, tmp_path):
    s = Session(sz, cache_root=str(tmp_path))
    second = str(archives["split7z"])[:-1] + "2"
    layer = s.open_file(second)
    assert layer.archive_path.endswith(".001")
    assert "big.bin" in layer.root.children


def test_nested(sz, archives, tmp_path):
    s = Session(sz, cache_root=str(tmp_path / "c"))
    outer = s.open_file(str(archives["nested"]))
    inner_node = outer.root.find("inner/inner.zip")
    inner = s.open_nested(outer, inner_node)
    assert inner.parent is outer and inner.parent_node is inner_node
    assert inner.root.find("folder/file.txt") is not None
    assert [l.display_name for l in inner.chain()] == ["outer.zip", "inner.zip"]

    tgz = s.open_nested(outer, outer.root.find("inner/a.tar.gz"))
    assert tgz.archive_type == "tar" and tgz.parent is outer
    assert tgz.root.find("folder/file.txt") is not None

    # a gzipped text file is not unwrapped
    gz = s.open_nested(outer, outer.root.child("note.txt.gz"))
    assert gz.archive_type == "gzip" and list(gz.root.children) == ["note.txt"]

    s.close()
    assert not os.path.exists(tmp_path / "c")


def test_extract_to_cache_is_memoized(sz, archives, tmp_path):
    s = Session(sz, cache_root=str(tmp_path / "c"))
    layer = s.open_file(str(archives["zip"]))
    node = layer.root.find("folder/file.txt")
    p1 = s.extract_to_cache(layer, node)
    assert open(p1).read() == "file"
    assert s.extract_to_cache(layer, node) == p1
    assert s.extract_to_cache(layer, node, fresh=True) != p1


def test_nested_layers_are_memoized_and_discarded(sz, archives, tmp_path):
    s = Session(sz, cache_root=str(tmp_path / "c"))
    outer = s.open_file(str(archives["nested"]))
    node = outer.root.find("inner/a.tar.gz")
    first = s.open_nested(outer, node)
    assert s.open_nested(outer, node) is first
    dirs = outer.cache_dirs()
    assert first.cache_dir in dirs and all(os.path.isdir(d) for d in dirs)
    s.discard(first)
    assert not any(os.path.exists(d) for d in dirs)


def test_nested_split_volumes(sz, archives, tmp_path):
    s = Session(sz, cache_root=str(tmp_path / "c"))
    outer = s.open_file(str(archives["splitouter"]))
    for name in ("split.7z.001", "split.7z.002"):
        layer = s.open_nested(outer, outer.root.child(name))
        assert layer.root.child("big.bin").size == 200_000


def test_plain_gz_is_not_extracted_for_unwrapping(sz, archives, tmp_path, monkeypatch):
    s = Session(sz, cache_root=str(tmp_path / "c"))
    outer = s.open_file(str(archives["nested"]))
    calls = []
    orig = s.extract_to_cache
    monkeypatch.setattr(s, "extract_to_cache", lambda *a, **k: calls.append(a[1].name) or orig(*a, **k))
    s.open_nested(outer, outer.root.child("note.txt.gz"))
    assert calls == ["note.txt.gz"]  # the inner note.txt was not extracted


def test_damaged_archive_warning(sz, odd_archives, tmp_path):
    s = Session(sz, cache_root=str(tmp_path / "c"))
    layer = s.open_file(str(odd_archives["trunc"]))
    assert layer.warning


def test_stale_cache_cleanup(tmp_path):
    from slag.backend.session import cleanup_stale_caches
    (tmp_path / "999999999-abc").mkdir()
    (tmp_path / f"{os.getpid()}-mine").mkdir()
    (tmp_path / "other").mkdir()
    cleanup_stale_caches(str(tmp_path))
    assert sorted(p.name for p in tmp_path.iterdir()) == [f"{os.getpid()}-mine", "other"]


def _web_zip(path):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("site/index.html", '<link href="css/a.css"><script src="../shared/b.js">')
        z.writestr("site/css/a.css", "body{}")
        z.writestr("shared/b.js", "1")
        info = zipfile.ZipInfo("shared/run.sh")
        info.create_system = 3  # unix
        info.external_attr = 0o100755 << 16
        z.writestr(info, "#!/bin/sh\n")
    return path


def test_extract_in_tree(sz, tmp_path):
    s = Session(sz, cache_root=str(tmp_path / "c"))
    layer = s.open_file(str(_web_zip(tmp_path / "web.zip")))
    page = s.extract_in_tree(layer, layer.root.find("site/index.html"))
    site = os.path.dirname(page)
    assert os.path.isfile(os.path.join(site, "css", "a.css"))
    assert open(os.path.join(site, "..", "shared", "b.js")).read() == "1"
    assert not os.stat(os.path.join(site, "..", "shared", "run.sh")).st_mode & 0o111
    # the tree is extracted once and lives in the layer's cache dir (kept by discard)
    other = s.extract_in_tree(layer, layer.root.find("shared/b.js"))
    assert os.path.dirname(os.path.dirname(other)) == os.path.dirname(site)
    assert page.startswith(os.path.join(layer.cache_dir, ""))


def test_extract_in_tree_cancel_removes_partial_tree(sz, tmp_path):
    s = Session(sz, cache_root=str(tmp_path / "c"))
    layer = s.open_file(str(_web_zip(tmp_path / "web.zip")))
    before = set(os.listdir(layer.cache_dir))
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(Cancelled):
        s.extract_in_tree(layer, layer.root.find("site/index.html"), cancel=cancel)
    assert set(os.listdir(layer.cache_dir)) == before and not layer._tree_dir


def test_extract_in_tree_again_when_tree_is_gone(sz, tmp_path):
    import shutil
    s = Session(sz, cache_root=str(tmp_path / "c"))
    layer = s.open_file(str(_web_zip(tmp_path / "web.zip")))
    node = layer.root.find("site/index.html")
    first = s.extract_in_tree(layer, node)
    shutil.rmtree(layer._tree_dir)
    assert not s.has_tree(layer)
    again = s.extract_in_tree(layer, node)
    assert again != first and os.path.isfile(again) and s.has_tree(layer)


def _damaged_zip(path, damaged_name):
    """Zip (stored) where one byte of ``damaged_name``'s content is changed."""
    files = {"site/index.html": b"<p>PAGECONTENT</p>", "site/a.css": b"CSSCONTENT"}
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as z:
        for name, data in files.items():
            z.writestr(name, data)
    data = bytearray(path.read_bytes())
    pos = data.index(files[damaged_name])
    data[pos] ^= 0x20
    path.write_bytes(bytes(data))
    return path


def test_extract_in_tree_keeps_intact_page_of_damaged_archive(sz, tmp_path):
    s = Session(sz, cache_root=str(tmp_path / "c"))
    layer = s.open_file(str(_damaged_zip(tmp_path / "d.zip", "site/a.css")))
    page = s.extract_in_tree(layer, layer.root.find("site/index.html"))
    assert open(page).read() == "<p>PAGECONTENT</p>"


def test_extract_in_tree_rejects_damaged_page(sz, tmp_path):
    s = Session(sz, cache_root=str(tmp_path / "c"))
    layer = s.open_file(str(_damaged_zip(tmp_path / "d.zip", "site/index.html")))
    with pytest.raises(SevenZipError):
        s.extract_in_tree(layer, layer.root.find("site/index.html"))


def partly_encrypted_zip(tmp_path):
    """site/index.html plain, site/a.css encrypted with password 'secret'."""
    src = tmp_path / "pe"
    (src / "site").mkdir(parents=True)
    (src / "site" / "index.html").write_text("<p>page</p>")
    (src / "site" / "a.css").write_text("css")
    arc = tmp_path / "pe.zip"
    subprocess.run(["zip", "-q", "-P", "secret", str(arc), "site/a.css"], cwd=src, check=True)
    subprocess.run(["zip", "-q", str(arc), "site/index.html"], cwd=src, check=True)
    return arc


def test_extract_in_tree_partly_encrypted(sz, tmp_path):
    s = Session(sz, cache_root=str(tmp_path / "c"))
    layer = s.open_file(str(partly_encrypted_zip(tmp_path)))
    node = layer.root.find("site/index.html")
    assert not node.encrypted
    with pytest.raises(PasswordRequired):
        s.extract_in_tree(layer, node)
    assert not s.has_tree(layer)
    layer.password = "secret"
    page = s.extract_in_tree(layer, node)
    assert open(os.path.join(os.path.dirname(page), "a.css")).read() == "css"

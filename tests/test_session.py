import os

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

import threading
from pathlib import Path

import pytest

from slag.backend.extract import ConflictAction, extract_nodes, unique_name
from slag.backend.sevenzip import (
    Cancelled, NotAnArchive, PasswordRequired, parse_listing, stream_entry_name,
)
from slag.backend.tree import build_tree, normalize_components
from slag.backend.volumes import first_volume, is_archive_name


def tree_for(sz, path, password=""):
    return build_tree(sz.list(str(path), password=password).entries)


def names(node):
    return sorted(node.children)


def no_conflicts(dst, src):
    raise AssertionError(f"unexpected conflict {dst}")


# --- listing / tree ---------------------------------------------------------------

@pytest.mark.parametrize("kind", ["zip", "tar", "7z"])
def test_listing_tree(sz, archives, kind):
    root = tree_for(sz, archives[kind])
    assert names(root) == ["emptydir", "folder", "top.txt", "weird*name?.txt", "weirdXnameY.txt"]
    folder = root.child("folder")
    assert folder.is_dir
    assert names(folder) == ["file.txt", "sub", "Ümlaut ä.txt"]
    assert root.find("folder/sub/a.txt").size == 1
    assert root.child("emptydir").is_dir and not root.child("emptydir").children
    assert root.find("folder/sub/a.txt").path == "folder/sub/a.txt"


def test_tar_gz_lists_inner_tar(sz, archives):
    listing = sz.list(str(archives["tar.gz"]))
    assert listing.archive_type == "gzip"
    assert [e.path for e in listing.entries] == ["a.tar"]


def test_tar_dot_prefix_is_normalized(sz, archives):
    listing = sz.list(str(archives["tar"]))
    assert any(e.path.startswith("./") for e in listing.entries)
    root = build_tree(listing.entries)
    assert root.find("folder/file.txt") is not None


def test_split_7z(sz, archives):
    root = tree_for(sz, archives["split7z"])
    assert names(root) == ["big.bin", "small.txt"]
    assert root.child("big.bin").size == 200_000


def test_split_zip(sz, archives):
    root = tree_for(sz, archives["splitzip"])
    assert names(root) == ["big.bin", "small.txt"]


def test_not_an_archive(sz, archives):
    with pytest.raises(NotAnArchive):
        sz.list(str(archives["plain"]))


def test_encrypted_headers(sz, archives):
    with pytest.raises(PasswordRequired) as ei:
        sz.list(str(archives["enc_headers"]))
    assert not ei.value.wrong_password
    with pytest.raises(PasswordRequired) as ei:
        sz.list(str(archives["enc_headers"]), password="wrong")
    assert ei.value.wrong_password
    root = tree_for(sz, archives["enc_headers"], password="secret")
    assert root.find("folder/file.txt") is not None


def test_encrypted_entries(sz, archives, tmp_path):
    root = tree_for(sz, archives["enc_zip"])
    node = root.child("top.txt")
    assert node.encrypted
    with pytest.raises(PasswordRequired):
        extract_nodes(sz, str(archives["enc_zip"]), [node], tmp_path, no_conflicts)
    extract_nodes(sz, str(archives["enc_zip"]), [node], tmp_path, no_conflicts, password="secret")
    assert (tmp_path / "top.txt").read_text() == "top"
    # nothing left behind besides the result
    assert sorted(p.name for p in tmp_path.iterdir()) == ["top.txt"]


def test_normalize_components():
    assert normalize_components("./a/b") == ["a", "b"]
    assert normalize_components("/abs/x") == ["abs", "x"]
    assert normalize_components("../up/f") == ["up", "f"]
    assert normalize_components("a\\b") == ["a\\b"]  # backslash is a normal char for 7z


def test_parse_listing_synthesizes_dirs():
    out = "\n".join([
        "--", "Path = x.zip", "Type = zip", "", "----------",
        "Path = deep/er/file.txt", "Folder = -", "Size = 5", "Modified = 2024-01-02 03:04:05",
        "Attributes = A", "",
        "Path = name = with equals", "Folder = -", "Size = 1", "",
    ])
    listing = parse_listing(out)
    assert listing.archive_type == "zip"
    root = build_tree(listing.entries)
    f = root.find("deep/er/file.txt")
    assert f.size == 5 and f.mtime.year == 2024
    assert root.child("deep").is_dir and root.child("deep").raw_paths == []
    assert root.child("name = with equals") is not None
    assert root.child("deep").total_size == 5


# --- extraction without parent folders ---------------------------------------------

@pytest.mark.parametrize("kind", ["zip", "tar", "7z"])
def test_extract_single_file_without_parents(sz, archives, kind, tmp_path):
    root = tree_for(sz, archives[kind])
    node = root.find("folder/file.txt")
    res = extract_nodes(sz, str(archives[kind]), [node], tmp_path, no_conflicts)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["file.txt"]
    assert (tmp_path / "file.txt").read_text() == "file"
    assert res.extracted == [tmp_path / "file.txt"]


@pytest.mark.parametrize("kind", ["zip", "tar", "7z"])
def test_extract_folder_without_parents(sz, archives, kind, tmp_path):
    root = tree_for(sz, archives[kind])
    node = root.find("folder/sub")
    extract_nodes(sz, str(archives[kind]), [node], tmp_path, no_conflicts)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["sub"]
    assert (tmp_path / "sub" / "a.txt").read_text() == "a"


def test_extract_current_level(sz, archives, tmp_path):
    root = tree_for(sz, archives["zip"])
    folder = root.child("folder")
    extract_nodes(sz, str(archives["zip"]), list(folder.children.values()), tmp_path, no_conflicts)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["file.txt", "sub", "Ümlaut ä.txt"]
    assert (tmp_path / "sub" / "a.txt").exists()


def test_extract_wildcard_names_exactly(sz, archives, tmp_path):
    root = tree_for(sz, archives["7z"])
    node = root.child("weird*name?.txt")
    extract_nodes(sz, str(archives["7z"]), [node], tmp_path, no_conflicts)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["weird*name?.txt"]


def test_extract_empty_dir(sz, archives, tmp_path):
    root = tree_for(sz, archives["zip"])
    extract_nodes(sz, str(archives["zip"]), [root.child("emptydir")], tmp_path, no_conflicts)
    assert (tmp_path / "emptydir").is_dir()


def test_extract_from_split(sz, archives, tmp_path):
    root = tree_for(sz, archives["split7z"])
    extract_nodes(sz, str(archives["split7z"]), [root.child("big.bin")], tmp_path, no_conflicts)
    assert (tmp_path / "big.bin").stat().st_size == 200_000


def test_extract_conflicts(sz, archives, tmp_path):
    root = tree_for(sz, archives["zip"])
    folder = root.child("folder")
    (tmp_path / "folder").mkdir()
    (tmp_path / "folder" / "file.txt").write_text("old")
    (tmp_path / "folder" / "keep.txt").write_text("keep")
    seen = []

    def skip(dst, src):
        seen.append(dst)
        return ConflictAction.SKIP

    extract_nodes(sz, str(archives["zip"]), [folder], tmp_path, skip)
    assert seen == [tmp_path / "folder" / "file.txt"]
    assert (tmp_path / "folder" / "file.txt").read_text() == "old"  # skipped
    assert (tmp_path / "folder" / "keep.txt").exists()  # merged, not replaced
    assert (tmp_path / "folder" / "sub" / "a.txt").exists()

    extract_nodes(sz, str(archives["zip"]), [folder], tmp_path, lambda d, s: ConflictAction.OVERWRITE)
    assert (tmp_path / "folder" / "file.txt").read_text() == "file"

    extract_nodes(sz, str(archives["zip"]), [root.child("top.txt")], tmp_path, no_conflicts)
    extract_nodes(sz, str(archives["zip"]), [root.child("top.txt")], tmp_path, lambda d, s: ConflictAction.RENAME)
    assert (tmp_path / "top (1).txt").read_text() == "top"

    res = extract_nodes(sz, str(archives["zip"]), [root.child("emptydir"), root.child("top.txt")],
                        tmp_path, lambda d, s: ConflictAction.CANCEL)
    assert res.cancelled and res.extracted == [tmp_path / "emptydir"]
    # staging directory is always cleaned up
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(".slag")]


def test_extract_progress_and_cancel(sz, archives, tmp_path):
    root = tree_for(sz, archives["split7z"])
    values = []
    extract_nodes(sz, str(archives["split7z"]), list(root.children.values()), tmp_path / "a",
                  no_conflicts, progress=values.append)
    assert all(0 <= v <= 100 for v in values)

    cancel = threading.Event()
    cancel.set()
    with pytest.raises(Cancelled):
        extract_nodes(sz, str(archives["split7z"]), list(root.children.values()), tmp_path / "b",
                      no_conflicts, cancel=cancel)
    assert list((tmp_path / "b").iterdir()) == []


def test_unique_name(tmp_path):
    (tmp_path / "a.txt").write_text("")
    (tmp_path / "a (1).txt").write_text("")
    assert unique_name(tmp_path / "a.txt") == tmp_path / "a (2).txt"
    (tmp_path / "d").mkdir()
    assert unique_name(tmp_path / "d") == tmp_path / "d (1)"
    (tmp_path / ".hidden").write_text("")
    assert unique_name(tmp_path / ".hidden") == tmp_path / ".hidden (1)"


# --- volumes -------------------------------------------------------------------------

def test_first_volume(tmp_path):
    for n in ["x.part01.rar", "x.part02.rar", "y.001", "y.002", "z.zip", "z.z01", "o.rar", "o.r00"]:
        (tmp_path / n).write_text("")
    assert first_volume(str(tmp_path / "x.part02.rar")) == str(tmp_path / "x.part01.rar")
    assert first_volume(str(tmp_path / "y.002")) == str(tmp_path / "y.001")
    assert first_volume(str(tmp_path / "z.z01")) == str(tmp_path / "z.zip")
    assert first_volume(str(tmp_path / "o.r00")) == str(tmp_path / "o.rar")
    assert first_volume(str(tmp_path / "missing.part3.rar")) == str(tmp_path / "missing.part3.rar")


def test_stream_entry_name():
    assert stream_entry_name("a.tar.xz") == "a.tar"
    assert stream_entry_name("a.txz") == "a.tar"
    assert stream_entry_name("a.TZST") == "a.tar"
    assert stream_entry_name("log.ZST") == "log"
    assert stream_entry_name("noext") == "noext~"
    assert stream_entry_name(".xz") == ".xz~"


def test_nameless_xz_entry_is_listed(sz, archives):
    listing = sz.list(str(archives["tar.xz"]))
    assert listing.archive_type == "xz"
    assert [e.path for e in listing.entries] == ["a.tar"]


def test_is_archive_name():
    assert is_archive_name("a.ZIP")
    assert is_archive_name("a.tar.gz")
    assert is_archive_name("a.part2.rar")
    assert is_archive_name("a.7z.001")
    assert not is_archive_name("readme.txt")


# --- review findings: odd names, links, damaged archives -------------------------------

def test_odd_names_roundtrip(sz, odd_archives, tmp_path):
    from tests.conftest import ODD_NAMES
    root = tree_for(sz, odd_archives["odd"])
    assert "PHANTOM.exe" not in root.children  # comment is not parsed as an entry
    for name in ODD_NAMES:
        assert root.find(name) is not None, name
    top = [root.child(n) for n in ODD_NAMES if "/" not in n]
    res = extract_nodes(sz, str(odd_archives["odd"]), top, tmp_path, no_conflicts)
    assert res.missing == [] and res.errors == []
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(n.name for n in top)


def test_hard_link_alone(sz, odd_archives, tmp_path):
    root = tree_for(sz, odd_archives["links"])
    hard = root.find("d/hard")
    assert hard.hard_link == "d/orig"
    res = extract_nodes(sz, str(odd_archives["links"]), [hard], tmp_path, no_conflicts)
    assert res.errors == []
    assert sorted(p.name for p in tmp_path.iterdir()) == ["hard"]
    assert (tmp_path / "hard").read_text() == "x"


def test_symlink_extracted_as_link(sz, odd_archives, tmp_path):
    root = tree_for(sz, odd_archives["links"])
    extract_nodes(sz, str(odd_archives["links"]), [root.child("d")], tmp_path, no_conflicts)
    assert (tmp_path / "d" / "sym").is_symlink()
    assert (tmp_path / "d" / "sym").read_text() == "x"


def test_partial_failure_keeps_good_files(sz, odd_archives, tmp_path):
    root = tree_for(sz, odd_archives["crc"])
    res = extract_nodes(sz, str(odd_archives["crc"]), list(root.children.values()), tmp_path,
                        no_conflicts)
    assert res.errors
    assert (tmp_path / "good.txt").read_text() == "good content"


def test_truncated_archive_lists_with_warning(sz, odd_archives):
    listing = sz.list(str(odd_archives["trunc"]))
    assert listing.warning
    assert "first.txt" in [e.path for e in listing.entries]


def test_missing_volume_message(sz, archives, tmp_path):
    import shutil
    from slag.backend.sevenzip import SevenZipError
    first = Path(str(archives["split7z"]))
    shutil.copy(first, tmp_path / first.name)
    with pytest.raises(SevenZipError) as ei:
        sz.list(str(tmp_path / first.name))
    assert "missing" in str(ei.value).lower() or "truncated" in str(ei.value).lower()


def test_overwrite_dir_with_file_is_safe(sz, archives, tmp_path):
    root = tree_for(sz, archives["zip"])
    (tmp_path / "top.txt").mkdir()
    (tmp_path / "top.txt" / "precious").write_text("p")
    extract_nodes(sz, str(archives["zip"]), [root.child("top.txt")], tmp_path,
                  lambda d, s: ConflictAction.OVERWRITE)
    assert (tmp_path / "top.txt").read_text() == "top"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["top.txt"]  # no leftovers


def test_unsupported_method_hint():
    from slag.backend.sevenzip import SevenZipError, _raise_for_error
    with pytest.raises(SevenZipError) as ei:
        _raise_for_error(2, "", "ERROR: Unsupported Method : x.txt\n", "")
    assert "7zip-rar" in str(ei.value)

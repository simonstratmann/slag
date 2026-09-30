from slag.__main__ import archive_arguments


def test_archive_arguments(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "-odd.zip").write_text("")
    assert archive_arguments(["a.zip"]) == ["a.zip"]
    assert archive_arguments(["--unknown", "a.zip"]) == ["a.zip"]
    assert archive_arguments(["-odd.zip"]) == ["-odd.zip"]
    assert archive_arguments(["--", "--weird"]) == ["--weird"]
    assert archive_arguments(["file:///tmp/a%20b.zip"]) == ["/tmp/a b.zip"]

import subprocess

from slag.build_info import REPO, build_info


def test_build_info_of_this_checkout():
    info = build_info()
    head = subprocess.run(["git", "-C", REPO, "rev-parse", "HEAD"], capture_output=True,
                          text=True, check=True).stdout.strip()
    assert info is not None and info.commit == head
    assert info.describe.startswith(head[:7])
    assert info.text.startswith(info.describe) and info.repo in info.tooltip


def test_build_info_without_checkout(tmp_path):
    assert build_info(str(tmp_path)) is None


def test_build_info_shown_in_status_bar(window):
    assert window.build_label.text() == build_info().text

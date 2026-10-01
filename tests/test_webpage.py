from slag.backend.sevenzip import Entry
from slag.backend.tree import build_tree
from slag.backend.webpage import is_web_page, page_folder, up_levels


def test_is_web_page():
    assert is_web_page("Index.HTML") and is_web_page("a.htm") and not is_web_page("a.svg")


def test_up_levels():
    assert up_levels('<link href="style.css"><script src="js/a.js">') == 0
    assert up_levels('<a href="../index.html">') == 1
    assert up_levels("<img SRC='../../img/a.png'><a href=../x.html>") == 2
    assert up_levels('<div style="background: url( \'../../../bg.png\')">') == 3
    assert up_levels("text ../../../ outside of links") == 0


def test_page_folder_stops_at_root():
    root = build_tree([Entry("a/b/page.html", False)])
    node = root.find("a/b/page.html")
    assert page_folder(node, "") is root.find("a/b")
    assert page_folder(node, '<a href="../x">') is root.find("a")
    assert page_folder(node, '<a href="../../../../x">') is root

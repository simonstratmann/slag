from PyQt6.QtCore import QModelIndex, Qt


def tree_names(w, parent=None):
    m = w.tree_model
    parent = parent or QModelIndex()
    return [m.data(m.index(r, 0, parent)) for r in range(m.rowCount(parent))]


def marked(w):
    """(selected tree nodes, bold tree nodes)"""
    m = w.tree_model
    selected = [m.node_of(i) for i in w.tree.selectionModel().selectedRows()]
    bold = []

    def walk(parent):
        for r in range(m.rowCount(parent)):
            idx = m.index(r, 0, parent)
            if m.data(idx, Qt.ItemDataRole.FontRole) is not None:
                bold.append(m.node_of(idx))
            walk(idx)

    walk(QModelIndex())
    return selected, bold


def test_tree_lists_first_level_folders(window, archives):
    assert tree_names(window) == []
    window.open_path(str(archives["zip"]))
    # The archive root has no row: its folders are the first level. No files.
    assert tree_names(window) == ["emptydir", "folder"]
    assert tree_names(window, window.tree_model.index_of(window.layer.root.child("folder"))) == ["sub"]
    assert marked(window) == ([], [])


def test_tree_follows_navigation(window, archives):
    window.open_path(str(archives["zip"]))
    folder = window.layer.root.child("folder")
    sub = folder.child("sub")
    window._open_row(window.model.row_of(folder))
    window._open_row(window.model.row_of(sub))
    m = window.tree_model
    assert window.tree.isExpanded(m.index_of(folder))
    assert marked(window) == ([sub], [sub])
    window.go_up()
    assert marked(window) == ([folder], [folder])
    window.go_up()
    assert marked(window) == ([], [])


def test_tree_click_navigates(window, archives):
    window.open_path(str(archives["zip"]))
    folder = window.layer.root.child("folder")
    window._on_tree_activated(window.tree_model.index_of(folder.child("sub")))
    assert window.current is folder.child("sub")


def test_tree_shows_inner_archives(window, archives):
    window.open_path(str(archives["nested"]))
    outer = window.layer
    inner_dir = outer.root.child("inner")
    inner_zip = inner_dir.child("inner.zip")
    m = window.tree_model
    window._open_row(window.model.row_of(inner_dir))
    # Archives are not shown before they were opened.
    assert tree_names(window, m.index_of(inner_dir)) == []
    window._open_row(window.model.row_of(inner_zip))
    inner = window.layer
    assert tree_names(window, m.index_of(inner_dir)) == ["inner.zip"]
    assert tree_names(window, m.index_of(inner_zip)) == ["emptydir", "folder"]
    assert marked(window) == ([inner_zip], [inner_zip])
    window._open_row(window.model.row_of(inner.root.child("folder")))
    assert marked(window) == ([inner.root.child("folder")], [inner.root.child("folder")])
    assert window.tree.isExpanded(m.index_of(inner_zip))

    # Back to the outer archive: the inner one stays in the tree.
    window._on_tree_activated(m.index_of(inner_dir))
    assert window.layer is outer and window.current is inner_dir
    assert tree_names(window, m.index_of(inner_dir)) == ["inner.zip"]
    # ... and can be entered from there again.
    window._on_tree_activated(m.index_of(inner.root.child("folder").child("sub")))
    assert window.layer is inner and window.current is inner.root.child("folder").child("sub")
    window.go_up()
    window.go_up()
    window.go_up()
    assert window.layer is outer and window.current is inner_dir

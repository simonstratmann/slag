# SLAG (Simon's Little Archive GUI) — plan

A 7-Zip-style, read-only archive browser for KDE. Python 3 + PyQt6, the `7z` CLI as backend.

## Architecture

```
slag/
  backend/
    sevenzip.py   # wrapper around the 7z CLI: list, extract, progress, errors
    tree.py       # flat 7z listing -> directory tree (Node)
    volumes.py    # split-volume detection (.001, .partN.rar, .z01, .r00)
    extract.py    # "extract selection without parent folders" (staging + move)
    session.py    # archive layers (nested archives), temp cache
  ui/
    main_window.py, archive_model.py, extract_dialog.py, settings.py, ...
  __main__.py
tests/            # pytest, test archives generated on the fly with 7z/zip/tar
```

Key decisions:
- **7z CLI** (`7z l -slt`, `7z x`) is the only archive engine: zip, rar/rar5, 7z, tar, gz/bz2/xz,
  split volumes, encrypted archives. Always called with `-p` (no interactive prompt) and `-spd`
  + UTF-8 list files, so exact raw paths are matched (no wildcards).
- **Extract without parent folders**: extract the selected entries into a hidden staging dir
  inside the target (same filesystem), then `rename` the selected items into the target.
  Conflicts are resolved via a callback (overwrite / skip / rename / cancel, "apply to all").
- **Nested archives**: an inner archive is extracted into the session's temp cache and opened as a
  new layer. `.tar.gz`/`.tgz`/`.tar.xz`/… are unwrapped automatically so they look like one archive.
- **Read-only**: F4 edits a temporary copy; the archive is never modified.

## Milestones

1. **Backend** — 7z wrapper, listing parser, tree, volumes, staged extraction, unit tests.
2. **Main window** — file list with columns, path bar, navigation (Enter/Backspace/double-click),
   open archive via CLI argument, Ctrl+O and drag & drop.
3. **Nested archives** — temp cache, layers, auto-unwrap of compressed tars, breadcrumb.
4. **Extraction UI** — extract dialog (autocomplete, browse, recent targets), context menu,
   F5/toolbar, progress + cancel, conflict dialog, password prompt.
5. **Open / edit** — Enter opens inside (archives) or with the associated app, Shift+Enter opens
   outside, Ctrl+PgDn forces inside, F4 opens in the configured editor.
6. **Integration** — settings dialog, `.desktop` file + MIME types, install script, README.

Each milestone: implement → tests → review by a subagent → fixes → commit.

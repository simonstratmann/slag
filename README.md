# SLAG — Simon's Little Archive GUI

A read-only archive browser for KDE with 7-Zip's behaviour: browse archives like folders,
open archives inside archives, and extract exactly what you selected — without its parent
folders.

Python 3 + PyQt6, with the `7z` command line tool (7-Zip) doing all archive work.

## Features

- zip, rar/rar5, 7z, tar, tar.gz/.bz2/.xz/.zst (shown as a single archive), iso, cab, …
- Split archives: open any volume (`.001`, `.partN.rar`, `.z01`, `.r00`, …); the first one is
  used. Split sets inside other archives work too.
- Nested archives: Enter on an archive inside an archive opens it; Backspace leaves it again.
- Password protected archives (prompted when needed).
- Extraction without parent folders: select `folder/file.txt` while inside `folder` and
  extract it — you get `file.txt` in the target, not `folder/file.txt`.
  Nothing selected = everything on the current level.
- Extract dialog: path field with folder autocomplete, folder chooser, recent targets
  (double-click to extract immediately). "Extract to" submenu with recent targets in the
  context menu. Existing folders are merged; for existing files you choose
  overwrite / skip / rename (optionally for all).
- Damaged archives are listed as far as possible; extraction keeps everything that worked.

## Keys

| Key                    | Action                                                         |
|------------------------|----------------------------------------------------------------|
| Enter / double-click   | open folder, open archive inside, or open file with its app   |
| Shift+Enter            | open with the associated application (even archives)          |
| Ctrl+PgDown            | open inside as archive (even with an unknown extension)        |
| F4                     | open in the configured editor                                  |
| Backspace / Alt+Up     | parent folder / leave nested archive                           |
| F5 / Ctrl+E            | extract…                                                       |
| Ctrl+O                 | open archive                                                   |
| Ctrl+,                 | settings (editor command, recent targets, web page size limit) |

Files opened with Enter/F4 are temporary copies (in `~/.cache/slag`, without executable
bits); the archive is never modified. If you edit such a copy, SLAG tells you and offers to
save a copy elsewhere (also on exit). Temporary copies stay available until SLAG is closed,
even when another archive is opened.

Web pages (`.html`, `.htm`, `.xhtml`, `.shtml`) are opened from a temporary copy of the whole
archive, so the browser finds their images, styles and scripts. For archive files above a size
limit (default 150 MB, see settings) SLAG asks first and can open the page alone instead.

The editor command may contain `%f` for the file; otherwise the file is appended
(e.g. `kate`, `konsole -e nvim %f`).

## Installation

Requirements (Ubuntu/Kubuntu): `sudo apt install 7zip python3-pyqt6`.

**RAR:** Ubuntu's `7zip` package can list RAR archives but not extract them, because the
RAR codec is packaged separately: `sudo apt install 7zip-rar` (multiverse).

```sh
./install.sh            # launcher in ~/.local/bin, desktop entry, MIME types
./install.sh --default  # also make it the default application for archives
./install.sh --uninstall
```

Run without installing: `/usr/bin/python3 -m slag [archive]`.

## Development

```sh
/usr/bin/python3 -m venv --system-site-packages .venv
.venv/bin/pip install pytest pytest-qt
.venv/bin/python -m pytest -q
```

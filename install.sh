#!/bin/sh
# Install linuxfile for the current user (no root needed).
#   ./install.sh            install launcher, desktop entry and MIME types
#   ./install.sh --default  additionally make linuxfile the default app for archives
#   ./install.sh --uninstall
set -eu

REPO="$(cd "$(dirname "$0")" && pwd)"
BIN_DIR="${XDG_BIN_HOME:-$HOME/.local/bin}"
DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}"
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}"
BIN="$BIN_DIR/linuxfile"
DESKTOP="$DATA_DIR/applications/linuxfile.desktop"
MIME_XML="$DATA_DIR/mime/packages/linuxfile.xml"
PY=/usr/bin/python3

refresh() {
    update-desktop-database "$DATA_DIR/applications" 2>/dev/null || true
    update-mime-database "$DATA_DIR/mime" 2>/dev/null || true
    command -v kbuildsycoca6 >/dev/null 2>&1 && kbuildsycoca6 >/dev/null 2>&1 || true
}

if [ "${1:-}" = "--uninstall" ]; then
    rm -f "$BIN" "$DESKTOP" "$MIME_XML"
    # Remove default-application entries written by --default.
    if [ -f "$CONFIG_DIR/mimeapps.list" ]; then
        "$PY" - "$CONFIG_DIR/mimeapps.list" <<'PYEOF'
import sys
path = sys.argv[1]
out = []
for line in open(path, encoding="utf-8").read().split("\n"):
    key, sep, value = line.partition("=")
    if sep and "linuxfile.desktop" in value:
        apps = [a for a in value.split(";") if a and a != "linuxfile.desktop"]
        if not apps:
            continue
        line = key + "=" + ";".join(apps) + ";"
    out.append(line)
open(path, "w", encoding="utf-8").write("\n".join(out))
PYEOF
    fi
    refresh
    echo "linuxfile uninstalled."
    exit 0
fi

for dep in "$PY" 7z; do
    command -v "$dep" >/dev/null 2>&1 || { echo "Missing dependency: $dep" >&2; exit 1; }
done
"$PY" -c "import PyQt6.QtWidgets" 2>/dev/null || {
    echo "PyQt6 is missing: sudo apt install python3-pyqt6" >&2; exit 1; }

mkdir -p "$BIN_DIR" "$(dirname "$DESKTOP")" "$(dirname "$MIME_XML")"

# Generate launcher and desktop entry with Python: correct quoting for any path.
"$PY" - "$REPO" "$BIN" "$DESKTOP" <<'PYEOF'
import os, shlex, sys
repo, bin_path, desktop = sys.argv[1:4]
with open(bin_path, "w", encoding="utf-8") as fh:
    fh.write("#!/bin/sh\n")
    fh.write(f'PYTHONPATH={shlex.quote(repo)}"${{PYTHONPATH:+:$PYTHONPATH}}" '
             'exec /usr/bin/python3 -m linuxfile "$@"\n')
os.chmod(bin_path, 0o755)

def desktop_quote(arg):
    # Desktop Entry spec: quote, escape " ` $ \ with a backslash; then the value
    # itself escapes backslashes once more.
    q = '"' + "".join("\\" + c if c in '"`$\\' else c for c in arg) + '"'
    return q.replace("\\", "\\\\").replace("%", "%%")

template = open(os.path.join(repo, "data", "linuxfile.desktop.in"), encoding="utf-8").read()
with open(desktop, "w", encoding="utf-8") as fh:
    fh.write(template.replace("@EXEC@", desktop_quote(bin_path)))
PYEOF
cp "$REPO/data/linuxfile-mime.xml" "$MIME_XML"
refresh

if [ "${1:-}" = "--default" ]; then
    types=$(sed -n 's/^MimeType=//p' "$DESKTOP" | tr ';' ' ')
    # shellcheck disable=SC2086
    xdg-mime default linuxfile.desktop $types
    echo "linuxfile is now the default application for archives."
fi

echo "Installed: $BIN"
echo "Desktop entry: $DESKTOP"
if ! 7z i 2>/dev/null | sed -n '/^Codecs:/,$p' | grep -qi 'rar'; then
    echo
    echo "Note: 7-Zip has no RAR codec, so RAR archives can be listed but not extracted."
    echo "      Install it with: sudo apt install 7zip-rar"
fi

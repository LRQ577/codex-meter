#!/usr/bin/env bash
set -euo pipefail

app_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
desktop_dir="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
autostart_dir="${XDG_CONFIG_HOME:-$HOME/.config}/autostart"
mkdir -p "$desktop_dir"

desktop_file="$desktop_dir/codex-meter.desktop"
python3 - "$app_dir/run.sh" "$desktop_file" <<'PY'
from pathlib import Path
import sys

executable, output = sys.argv[1:]
# Desktop Entry Exec quoting: escape reserved characters inside double quotes.
quoted = '"' + ''.join('\\' + char if char in '\\"$`' else char for char in executable) + '"'
content = (
    '[Desktop Entry]\n'
    'Type=Application\n'
    'Name=Codex Meter\n'
    'Comment=Codex usage and system resource monitor\n'
    f'Exec={quoted}\n'
    'Terminal=false\n'
    'Categories=Utility;\n'
    'StartupNotify=false\n'
)
Path(output).write_text(content, encoding='utf-8')
PY

case "${1:-}" in
  "") echo "Desktop entry installed: $desktop_file" ;;
  --autostart)
    mkdir -p "$autostart_dir"
    cp "$desktop_file" "$autostart_dir/codex-meter.desktop"
    echo "Desktop entry and login autostart installed."
    ;;
  --no-autostart)
    rm -f "$autostart_dir/codex-meter.desktop"
    echo "Desktop entry installed; login autostart removed."
    ;;
  *) echo "Usage: $0 [--autostart|--no-autostart]" >&2; exit 2 ;;
esac

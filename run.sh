#!/usr/bin/env bash
set -euo pipefail
app_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python_bin="$app_dir/.venv/bin/python"
if [[ ! -x "$python_bin" ]]; then
  echo "Codex Meter is not installed. Run: $app_dir/install.sh" >&2
  exit 1
fi

cd "$app_dir"
exec "$python_bin" "$app_dir/codex_meter.py" "$@"

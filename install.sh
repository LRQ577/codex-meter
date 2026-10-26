#!/usr/bin/env bash
set -euo pipefail

app_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python_bin="${PYTHON:-python3}"
"$python_bin" -m venv "$app_dir/.venv"
"$app_dir/.venv/bin/python" -m pip install --upgrade pip
"$app_dir/.venv/bin/python" -m pip install -r "$app_dir/requirements.txt"
echo "Installed. Start with: $app_dir/run.sh"

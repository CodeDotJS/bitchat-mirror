#!/usr/bin/env bash
# Run the sync CLI with the project venv, even inside Cursor's AppImage shell.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
PY="$ROOT/.venv/bin/python"

if [[ ! -x "$PY" ]]; then
  echo "Missing $PY — create the venv first:" >&2
  echo "  python3 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt" >&2
  exit 1
fi

# Cursor sets APPIMAGE/APPDIR; that makes `python` resolve to Cursor.AppImage
# and ignore the venv site-packages (so imports like boto3 fail).
exec env -u APPIMAGE -u APPDIR -u ARGV0 "$PY" -m sync.main "$@"

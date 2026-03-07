#!/usr/bin/env bash
set -euo pipefail

VENV_DIR="${1:-.venv}"
PY_BIN="${PY_BIN:-python3}"

if ! command -v "$PY_BIN" >/dev/null 2>&1; then
  echo "[error] $PY_BIN not found. Install Python 3 first." >&2
  exit 1
fi

if ! "$PY_BIN" -c 'import venv' >/dev/null 2>&1; then
  echo "[error] Python venv module is missing." >&2
  echo "Install it with: sudo apt-get update && sudo apt-get install -y python3-venv" >&2
  exit 1
fi

"$PY_BIN" -m venv "$VENV_DIR"
# shellcheck disable=SC1090
source "$VENV_DIR/bin/activate"

python -m pip install --upgrade pip setuptools wheel

# Export-only dependency set (no project runtime dependencies).
python -m pip install "ultralytics>=8.3.0"
echo "[ok] Installed export dependencies: ultralytics"

echo ""
echo "[ok] Virtual environment ready: $VENV_DIR"
echo "Activate with: source $VENV_DIR/bin/activate"

#!/bin/bash
# Run AuraBot backend with IMX AI camera support.
# LD_LIBRARY_PATH must be set before Python starts so modlib uses libcamera from /usr/local.

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

LOCAL_LIB="/usr/local/lib/aarch64-linux-gnu"
if [ -d "$LOCAL_LIB" ]; then
  export LD_LIBRARY_PATH="${LOCAL_LIB}${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fi
export MODLIB_LIBCAMERA=LOCAL

# Load .env if present (backend will also load it)
if [ -f backend/.env ]; then
  set -a
  source backend/.env
  set +a
fi

exec python -m backend "$@"

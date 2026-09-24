#!/bin/sh
# Ensure the volume layout exists and agy is installed, then hand off to the app.
set -e

mkdir -p "$HOME/.local/bin" "$AGY_MANAGER_ROOT" "$AGY_MANAGER_LIVE_DIR"

if [ ! -x "$AGY_BINARY" ]; then
  echo "[entrypoint] agy not found at $AGY_BINARY - installing..."
  curl -fsSL https://antigravity.google/cli/install.sh | bash \
    || echo "[entrypoint] agy install failed; start anyway so the UI can report it."
fi

if [ -x "$AGY_BINARY" ]; then
  echo "[entrypoint] agy present: $AGY_BINARY"
else
  echo "[entrypoint] WARNING: agy still missing at $AGY_BINARY"
fi

echo "[entrypoint] manager root: $AGY_MANAGER_ROOT | live dir: $AGY_MANAGER_LIVE_DIR | port: $PORT"
exec "$@"

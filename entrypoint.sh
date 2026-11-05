#!/bin/sh
# Ensure the volume layout exists and agy is installed/updated, then hand off to the app.
set -e

# Support Railway volume attached at /storage or /data automatically:
if [ -d "/storage" ]; then
  echo "[entrypoint] Detected storage volume at /storage"
  mkdir -p /storage/.local/bin /storage/.agy-cli-manager /storage/.gemini

  # If /data is not a separate mount point, link /data -> /storage
  if [ ! -L "/data" ]; then
    rm -rf /data 2>/dev/null || true
    ln -sfn /storage /data 2>/dev/null || true
  fi

  export HOME="/storage"
  export AGY_MANAGER_ROOT="/storage/.agy-cli-manager"
  export AGY_MANAGER_LIVE_DIR="/storage/.gemini"
  export AGY_BINARY="/storage/.local/bin/agy"
  export PATH="/storage/.local/bin:$PATH"
fi

mkdir -p "$HOME/.local/bin" "$AGY_MANAGER_ROOT" "$AGY_MANAGER_LIVE_DIR"

if [ ! -x "$AGY_BINARY" ]; then
  echo "[entrypoint] agy not found at $AGY_BINARY - installing latest..."
  curl -fsSL https://antigravity.google/cli/install.sh | bash \
    || echo "[entrypoint] agy install failed; start anyway so the UI can report it."
else
  echo "[entrypoint] agy present at $AGY_BINARY - checking for updates..."
  "$AGY_BINARY" update || true
fi

if [ -x "$AGY_BINARY" ]; then
  CURRENT_VER=$("$AGY_BINARY" --version 2>/dev/null || echo "unknown")
  echo "[entrypoint] agy present: $AGY_BINARY (version: $CURRENT_VER)"
else
  echo "[entrypoint] WARNING: agy still missing at $AGY_BINARY"
fi

echo "[entrypoint] home: $HOME | manager root: $AGY_MANAGER_ROOT | live dir: $AGY_MANAGER_LIVE_DIR | port: $PORT"
exec "$@"

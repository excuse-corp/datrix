#!/usr/bin/env bash

set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATIC_TARGET="$ROOT_DIR/packages/dbgpt-app/src/dbgpt_app/static/web"

echo "[dataman] scripts/build_web_static.sh is deprecated; using legacy Next export path."
echo "[dataman] Target directory: $STATIC_TARGET"

(
  cd "$ROOT_DIR/web"
  NODE_OPTIONS='--max_old_space_size=8192 --experimental-require-module' ./node_modules/.bin/next build
  NODE_OPTIONS='--experimental-require-module' ./node_modules/.bin/next export
)

mkdir -p "$STATIC_TARGET"
rsync -a --delete "$ROOT_DIR/web/out/" "$STATIC_TARGET/"

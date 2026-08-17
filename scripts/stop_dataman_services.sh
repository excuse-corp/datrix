#!/usr/bin/env bash

set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME_DIR="$ROOT_DIR/logs/dataman-services"
BACKEND_PID_FILE="$RUNTIME_DIR/dbgpt.pid"
DB_COMPOSE_FILE="$ROOT_DIR/docker/dataman-postgres/compose.yml"
DB_ENV_FILE="$ROOT_DIR/docker/dataman-postgres/.env"
BACKEND_PORT="${DATAMAN_BACKEND_PORT:-7771}"

log() {
  printf '[dataman] %s\n' "$*"
}

stop_pid() {
  local pid="$1"
  local label="$2"
  if ! kill -0 "$pid" 2>/dev/null; then
    return 0
  fi
  log "Stopping $label (PID $pid)"
  kill -- -"$pid" 2>/dev/null || kill "$pid" 2>/dev/null || true
  for ((attempt = 1; attempt <= 15; attempt++)); do
    kill -0 "$pid" 2>/dev/null || return 0
    sleep 1
  done
  kill -KILL -- -"$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null || true
}

main() {
  if [[ -f "$BACKEND_PID_FILE" ]]; then
    stop_pid "$(<"$BACKEND_PID_FILE")" "Datrix"
    rm -f "$BACKEND_PID_FILE"
  fi

  if ss -ltn "sport = :$BACKEND_PORT" | grep -q LISTEN; then
    log "Stopping unmanaged process listening on port $BACKEND_PORT"
    if command -v fuser >/dev/null; then
      fuser -k -TERM "$BACKEND_PORT/tcp" 2>/dev/null || true
    else
      log "fuser is unavailable; stop the remaining process manually"
    fi
  fi

  if [[ -f "$DB_COMPOSE_FILE" && -f "$DB_ENV_FILE" ]]; then
    log "Stopping PostgreSQL"
    docker compose --env-file "$DB_ENV_FILE" -f "$DB_COMPOSE_FILE" stop
  fi
  log "All managed DataMan services have stopped"
}

main "$@"

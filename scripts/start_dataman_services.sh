#!/usr/bin/env bash

set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME_DIR="$ROOT_DIR/logs/dataman-services"
DB_COMPOSE_FILE="$ROOT_DIR/docker/dataman-postgres/compose.yml"
DB_ENV_FILE="$ROOT_DIR/docker/dataman-postgres/.env"
ASK_DATA_ENV_FILE="$ROOT_DIR/configs/.env.ask-data"
DBGPT_CONFIG="${DATAMAN_DBGPT_CONFIG:-$ROOT_DIR/configs/dataman-runtime.toml}"
BACKEND_PORT="${DATAMAN_BACKEND_PORT:-7771}"
BACKEND_PID_FILE="$RUNTIME_DIR/dbgpt.pid"
BACKEND_LOG_FILE="$RUNTIME_DIR/dbgpt.log"

log() {
  printf '[dataman] %s\n' "$*"
}

fail() {
  printf '[dataman] error: %s\n' "$*" >&2
  exit 1
}

wait_for_http() {
  local url="$1"
  local label="$2"
  local attempts="${3:-60}"
  local status

  for ((attempt = 1; attempt <= attempts; attempt++)); do
    status="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 2 "$url" || true)"
    if [[ "$status" != "000" && -n "$status" ]]; then
      log "$label responded to local health check ($url, HTTP $status)"
      return 0
    fi
    sleep 1
  done
  return 1
}

wait_for_database() {
  local container="dataman-postgres"
  for ((attempt = 1; attempt <= 60; attempt++)); do
    if [[ "$(docker inspect -f '{{.State.Health.Status}}' "$container" 2>/dev/null || true)" == "healthy" ]]; then
      log "PostgreSQL is healthy"
      return 0
    fi
    sleep 1
  done
  return 1
}

start_backend() {
  if [[ -f "$BACKEND_PID_FILE" ]]; then
    local existing_pid
    existing_pid="$(<"$BACKEND_PID_FILE")"
    if kill -0 "$existing_pid" 2>/dev/null; then
    log "Datrix is already managed by PID $existing_pid"
      return 0
    fi
    rm -f "$BACKEND_PID_FILE"
  fi

  if ss -ltn "sport = :$BACKEND_PORT" | grep -q LISTEN; then
    fail "port $BACKEND_PORT is already in use; run scripts/stop_dataman_services.sh first"
  fi

  log "Starting Datrix with conda environment dataman"
  (
    cd "$ROOT_DIR"
    exec nohup setsid conda run --no-capture-output -n dataman \
      dbgpt start webserver --config "$DBGPT_CONFIG" --yes
  ) >"$BACKEND_LOG_FILE" 2>&1 </dev/null &
  local backend_pid=$!
  echo "$backend_pid" >"$BACKEND_PID_FILE"

  if ! wait_for_http "http://127.0.0.1:$BACKEND_PORT/" "Datrix"; then
    kill "$backend_pid" 2>/dev/null || true
    rm -f "$BACKEND_PID_FILE"
    fail "Datrix did not start; inspect $BACKEND_LOG_FILE"
  fi
}

build_and_publish_frontend() {
  if [[ "${DATAMAN_SKIP_WEB_BUILD:-0}" == "1" ]]; then
    log "Skipping frontend build because DATAMAN_SKIP_WEB_BUILD=1"
    return
  fi

  local static_target="$ROOT_DIR/packages/dbgpt-app/src/dbgpt_app/static/web"
  log "Building AskData frontend static assets"
  "$ROOT_DIR/scripts/build_web_static.sh"
  [[ -d "$static_target" ]] || fail "frontend static target was not created: $static_target"
}

main() {
  command -v docker >/dev/null || fail "docker is required for PostgreSQL"
  command -v conda >/dev/null || fail "conda is required for the dataman environment"
  command -v npm >/dev/null || fail "npm is required to build the frontend"
  [[ -f "$DB_COMPOSE_FILE" ]] || fail "missing database compose file: $DB_COMPOSE_FILE"
  [[ -f "$DB_ENV_FILE" ]] || fail "missing database environment file: $DB_ENV_FILE"
  [[ -f "$DBGPT_CONFIG" ]] || fail "missing Datrix config: $DBGPT_CONFIG; set DATAMAN_DBGPT_CONFIG to the runtime TOML path"

  mkdir -p "$RUNTIME_DIR"
  if [[ -f "$ASK_DATA_ENV_FILE" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$ASK_DATA_ENV_FILE"
    set +a
  fi

  log "Starting PostgreSQL"
  docker compose --env-file "$DB_ENV_FILE" -f "$DB_COMPOSE_FILE" up -d
  wait_for_database || fail "PostgreSQL did not become healthy"
  build_and_publish_frontend
  start_backend

  log "All DataMan services are ready; Datrix listens on 0.0.0.0:$BACKEND_PORT"
  log "Local health-check URL: http://127.0.0.1:$BACKEND_PORT"
  log "Logs: $BACKEND_LOG_FILE"
}

main "$@"

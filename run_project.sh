#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${VENV_DIR:-$ROOT_DIR/.venv}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
API_HOST="${API_HOST:-127.0.0.1}"
API_PORT="${API_PORT:-8000}"
API_RELOAD="${API_RELOAD:-false}"
FRONTEND_HOST="${FRONTEND_HOST:-0.0.0.0}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"

FORCE_INSTALL=false
API_ONLY=false
FRONTEND_ONLY=false
USE_SQLITE=false
PIDS=()

usage() {
  cat <<'USAGE'
Usage: ./run_project.sh [options]

Starts the bluebot meter-agent FastAPI backend and Vite frontend.

Options:
  --install        Force reinstall Python and frontend dependencies
  --reload         Run the API with uvicorn reload enabled
  --sqlite         Ignore DATABASE_URL and use local SQLite storage
  --api-only       Start only the FastAPI backend
  --frontend-only  Start only the Vite frontend
  -h, --help       Show this help

Environment overrides:
  API_HOST=127.0.0.1 API_PORT=8000 API_RELOAD=false
  FRONTEND_HOST=0.0.0.0 FRONTEND_PORT=5173
  VENV_DIR=/path/to/venv PYTHON_BIN=python3
USAGE
}

log() {
  printf '\033[1;34m==>\033[0m %s\n' "$*"
}

warn() {
  printf '\033[1;33mwarning:\033[0m %s\n' "$*" >&2
}

die() {
  printf '\033[1;31merror:\033[0m %s\n' "$*" >&2
  exit 1
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "Missing required command: $1"
}

cleanup() {
  local status=$?
  if ((${#PIDS[@]})); then
    log "Stopping project processes"
    kill "${PIDS[@]}" >/dev/null 2>&1 || true
    wait "${PIDS[@]}" >/dev/null 2>&1 || true
  fi
  exit "$status"
}
trap cleanup EXIT INT TERM

while (($#)); do
  case "$1" in
    --install)
      FORCE_INSTALL=true
      ;;
    --reload)
      API_RELOAD=true
      ;;
    --sqlite)
      USE_SQLITE=true
      ;;
    --api-only)
      API_ONLY=true
      ;;
    --frontend-only)
      FRONTEND_ONLY=true
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "Unknown option: $1"
      ;;
  esac
  shift
done

if [[ "$API_ONLY" == true && "$FRONTEND_ONLY" == true ]]; then
  die "Choose either --api-only or --frontend-only, not both"
fi

if [[ ! -f "$ROOT_DIR/.env" ]]; then
  warn "No .env file found. Copy .env.example to .env and fill in secrets for full chat/admin behavior."
fi

if [[ "$USE_SQLITE" == true ]]; then
  export DATABASE_URL=""
  export BLUEBOT_CONV_DB="${BLUEBOT_CONV_DB:-$ROOT_DIR/orchestrator/conversations.db}"
fi

ensure_python_deps() {
  require_cmd "$PYTHON_BIN"
  local install_python=false

  if [[ ! -x "$VENV_DIR/bin/python" ]]; then
    log "Creating Python virtual environment at $VENV_DIR"
    "$PYTHON_BIN" -m venv "$VENV_DIR"
    install_python=true
  fi

  if [[ "$FORCE_INSTALL" == true || "$install_python" == true ]] || ! "$VENV_DIR/bin/python" -c "import fastapi, uvicorn" >/dev/null 2>&1; then
    log "Installing API dependencies"
    "$VENV_DIR/bin/python" -m pip install --upgrade pip
    "$VENV_DIR/bin/python" -m pip install -r "$ROOT_DIR/orchestrator/requirements-api.txt"
  fi
}

ensure_frontend_deps() {
  require_cmd npm

  if [[ "$FORCE_INSTALL" == true || ! -d "$ROOT_DIR/frontend/node_modules" ]]; then
    log "Installing frontend dependencies"
    (cd "$ROOT_DIR/frontend" && npm ci)
  fi
}

start_api() {
  ensure_python_deps
  local reload_label=""
  local uvicorn_args=(
    api:app
    --host "$API_HOST"
    --port "$API_PORT"
    --log-level info
  )

  if [[ "$API_RELOAD" == true ]]; then
    reload_label=" with reload"
    uvicorn_args+=(--reload --reload-dir . --reload-dir ../llm)
  fi

  log "Starting FastAPI on http://$API_HOST:$API_PORT$reload_label"
  (
    cd "$ROOT_DIR/orchestrator"
    "$VENV_DIR/bin/python" -m uvicorn "${uvicorn_args[@]}"
  ) &
  PIDS+=("$!")
}

start_frontend() {
  ensure_frontend_deps
  log "Starting Vite on http://localhost:$FRONTEND_PORT"
  (
    cd "$ROOT_DIR/frontend"
    npm run dev -- --host "$FRONTEND_HOST" --port "$FRONTEND_PORT"
  ) &
  PIDS+=("$!")
}

if [[ "$FRONTEND_ONLY" != true ]]; then
  start_api
fi

if [[ "$API_ONLY" != true ]]; then
  start_frontend
fi

if [[ "$API_ONLY" == true ]]; then
  log "Project is starting. API health: http://$API_HOST:$API_PORT/api/config"
else
  log "Project is starting. Open http://localhost:$FRONTEND_PORT"
fi
log "Press Ctrl+C to stop."

while true; do
  for pid in "${PIDS[@]}"; do
    if ! kill -0 "$pid" >/dev/null 2>&1; then
      wait "$pid"
      exit "$?"
    fi
  done
  sleep 1
done

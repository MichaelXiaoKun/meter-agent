#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
API_HOST="${API_HOST:-127.0.0.1}"
API_PORT="${API_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"

FORCE_INSTALL=false
API_RELOAD="${API_RELOAD:-false}"
API_ONLY=false
FRONTEND_ONLY=false
USE_SQLITE=false
PIDS=()

usage() {
  cat <<'USAGE'
Usage: ./run_project.sh [options]

Starts the bluebot meter-agent FastAPI backend and Vite frontend.

Options:
  --install        Force reinstall backend and frontend dependencies
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

die() {
  printf '\033[1;31merror:\033[0m %s\n' "$*" >&2
  exit 1
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

BACKEND_ARGS=()
FRONTEND_ARGS=()

if [[ "$FORCE_INSTALL" == true ]]; then
  BACKEND_ARGS+=(--install)
  FRONTEND_ARGS+=(--install)
fi
if [[ "$API_RELOAD" == true ]]; then
  BACKEND_ARGS+=(--reload)
fi
if [[ "$USE_SQLITE" == true ]]; then
  BACKEND_ARGS+=(--sqlite)
fi

start_api() {
  "$ROOT_DIR/run_backend.sh" "${BACKEND_ARGS[@]}" &
  PIDS+=("$!")
}

start_frontend() {
  "$ROOT_DIR/run_frontend.sh" "${FRONTEND_ARGS[@]}" &
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

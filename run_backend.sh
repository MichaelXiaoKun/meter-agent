#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${VENV_DIR:-$ROOT_DIR/.venv}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
API_HOST="${API_HOST:-127.0.0.1}"
API_PORT="${API_PORT:-8000}"
API_RELOAD="${API_RELOAD:-false}"

FORCE_INSTALL=false
USE_SQLITE=false

usage() {
  cat <<'USAGE'
Usage: ./run_backend.sh [options]

Starts the bluebot meter-agent FastAPI backend.

Options:
  --install  Force reinstall Python API dependencies
  --reload   Run uvicorn with reload enabled
  --sqlite   Ignore DATABASE_URL and use local SQLite storage
  -h, --help Show this help

Environment overrides:
  API_HOST=127.0.0.1 API_PORT=8000 API_RELOAD=false
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

if [[ ! -f "$ROOT_DIR/.env" ]]; then
  warn "No .env file found. Copy .env.example to .env and fill in secrets for full chat/admin behavior."
fi

if [[ "$USE_SQLITE" == true ]]; then
  export DATABASE_URL=""
  export BLUEBOT_CONV_DB="${BLUEBOT_CONV_DB:-$ROOT_DIR/orchestrator/conversations.db}"
fi

require_cmd "$PYTHON_BIN"

install_python=false
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

uvicorn_args=(
  api:app
  --host "$API_HOST"
  --port "$API_PORT"
  --log-level info
)

reload_label=""
if [[ "$API_RELOAD" == true ]]; then
  reload_label=" with reload"
  uvicorn_args+=(--reload --reload-dir . --reload-dir ../llm)
fi

log "Starting FastAPI on http://$API_HOST:$API_PORT$reload_label"
cd "$ROOT_DIR/orchestrator"
exec "$VENV_DIR/bin/python" -m uvicorn "${uvicorn_args[@]}"

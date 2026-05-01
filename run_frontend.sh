#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FRONTEND_HOST="${FRONTEND_HOST:-0.0.0.0}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"

FORCE_INSTALL=false

usage() {
  cat <<'USAGE'
Usage: ./run_frontend.sh [options]

Starts the bluebot meter-agent Vite frontend.

Options:
  --install  Force reinstall frontend dependencies
  -h, --help Show this help

Environment overrides:
  FRONTEND_HOST=0.0.0.0 FRONTEND_PORT=5173
USAGE
}

log() {
  printf '\033[1;34m==>\033[0m %s\n' "$*"
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

require_cmd npm

if [[ "$FORCE_INSTALL" == true || ! -d "$ROOT_DIR/frontend/node_modules" ]]; then
  log "Installing frontend dependencies"
  (cd "$ROOT_DIR/frontend" && npm ci)
fi

log "Starting Vite on http://localhost:$FRONTEND_PORT"
cd "$ROOT_DIR/frontend"
exec npm run dev -- --host "$FRONTEND_HOST" --port "$FRONTEND_PORT"

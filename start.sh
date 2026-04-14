#!/bin/sh
set -e

# Railway and other hosts set PORT; default 8080 for local Docker
PORT="${PORT:-8080}"
sed "s/:8080/:$PORT/" /app/Caddyfile > /tmp/Caddyfile

cd /app/orchestrator
uvicorn api:app --host 0.0.0.0 --port 8000 &

cd /app
caddy run --config /tmp/Caddyfile --adapter caddyfile

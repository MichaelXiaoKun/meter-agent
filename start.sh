#!/bin/sh
set -e

# Railway and other hosts set PORT; default 8080 for local Docker
PORT="${PORT:-8080}"
sed "s/:8080/:$PORT/" /app/Caddyfile > /tmp/Caddyfile

# Uvicorn on 8001 so Caddy can bind to $PORT (often 8000 on Railway — would clash)
cd /app/orchestrator
uvicorn api:app --host 127.0.0.1 --port 8001 &

cd /app
caddy run --config /tmp/Caddyfile --adapter caddyfile

#!/bin/sh
set -e

cd /app/orchestrator
uvicorn api:app --host 0.0.0.0 --port 8000 &

cd /app
caddy run --config /app/Caddyfile --adapter caddyfile

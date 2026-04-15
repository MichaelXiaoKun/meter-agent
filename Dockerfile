# Portable Dockerfile: no BuildKit cache mounts. Railway requires
# id=s/<your-service-uuid>-<path> in mount ids (not substitutable from env); placeholder
# UUIDs are rejected with "not prefixed with cache key". Layer cache still applies when
# package-lock / requirements-api.txt are unchanged. To add Railway-only mounts, see
# https://docs.railway.com/guides/dockerfiles (hardcode your Service ID in both mounts).

# ---- Stage 1: Build frontend ----
FROM node:20-alpine AS frontend-build
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- Stage 2: Python runtime ----
FROM python:3.13-slim

WORKDIR /app

# API + subprocess deps only (no Streamlit — smaller image, faster registry push on Railway)
COPY orchestrator/requirements-api.txt /app/orchestrator/requirements-api.txt
RUN pip install --no-cache-dir -r /app/orchestrator/requirements-api.txt

# Copy application code
COPY orchestrator/ /app/orchestrator/
COPY data-processing-agent/ /app/data-processing-agent/
COPY meter-status-agent/ /app/meter-status-agent/
COPY pipe-configuration-agent/ /app/pipe-configuration-agent/
COPY bluebot.jpg /app/bluebot.jpg

# Copy built frontend
COPY --from=frontend-build /build/dist /app/frontend/dist

# Create plots directory
RUN mkdir -p /app/data-processing-agent/plots

EXPOSE 8080

# Single process: FastAPI serves /api and static SPA (see api._mount_production_spa)
WORKDIR /app/orchestrator
CMD ["sh", "-c", "exec uvicorn api:app --host 0.0.0.0 --port ${PORT:-8080}"]

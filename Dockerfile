# syntax=docker/dockerfile:1.4
#
# Railway BuildKit cache mounts must use: id=s/<SERVICE_ID>-<target-path>,target=<target-path>
# (see https://docs.railway.com/guides/dockerfiles). Replace both occurrences of the
# placeholder UUID below with your Service ID from the Railway dashboard (URL or settings).
#
# ---- Stage 1: Build frontend ----
FROM node:20-alpine AS frontend-build
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN --mount=type=cache,id=s/00000000-0000-4000-8000-000000000001-/root/.npm,target=/root/.npm \
    npm ci
COPY frontend/ ./
RUN npm run build

# ---- Stage 2: Python runtime ----
FROM python:3.13-slim

WORKDIR /app

# API + subprocess deps only (no Streamlit — smaller image, faster registry push on Railway)
COPY orchestrator/requirements-api.txt /app/orchestrator/requirements-api.txt
RUN --mount=type=cache,id=s/00000000-0000-4000-8000-000000000001-/root/.cache/pip,target=/root/.cache/pip \
    pip install --no-cache-dir -r /app/orchestrator/requirements-api.txt

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

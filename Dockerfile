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

# Install Python dependencies
COPY orchestrator/requirements.txt /app/orchestrator/requirements.txt
RUN pip install --no-cache-dir -r /app/orchestrator/requirements.txt

# Copy application code
COPY orchestrator/ /app/orchestrator/
COPY data-processing-agent/ /app/data-processing-agent/
COPY meter-status-agent/ /app/meter-status-agent/
COPY bluebot.jpg /app/bluebot.jpg
COPY requirements.txt /app/requirements.txt

# Copy built frontend
COPY --from=frontend-build /build/dist /app/frontend/dist

# Create plots directory
RUN mkdir -p /app/data-processing-agent/plots

EXPOSE 8080

# Single process: FastAPI serves /api and static SPA (see api._mount_production_spa)
WORKDIR /app/orchestrator
CMD ["sh", "-c", "exec uvicorn api:app --host 0.0.0.0 --port ${PORT:-8080}"]

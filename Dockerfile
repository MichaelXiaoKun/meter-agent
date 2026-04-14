# ---- Stage 1: Build frontend ----
FROM node:20-alpine AS frontend-build
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- Stage 2: Python runtime + Caddy ----
FROM python:3.13-slim

# Install Caddy (static binary)
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    curl -fsSL "https://caddyserver.com/api/download?os=linux&arch=amd64" -o /usr/local/bin/caddy && \
    chmod +x /usr/local/bin/caddy && \
    apt-get purge -y curl && apt-get autoremove -y && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

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

# Copy deployment configs
COPY Caddyfile /app/Caddyfile
COPY start.sh /app/start.sh
RUN chmod +x /app/start.sh

# Create plots directory
RUN mkdir -p /app/data-processing-agent/plots

EXPOSE 8080

CMD ["/app/start.sh"]

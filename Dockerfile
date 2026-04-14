# ---- Stage 1: Build frontend ----
FROM node:20-alpine AS frontend-build
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- Stage 2: Python runtime + Caddy ----
FROM python:3.13-slim

# Install Caddy
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl debian-keyring debian-archive-keyring apt-transport-https && \
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg && \
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list && \
    apt-get update && \
    apt-get install -y --no-install-recommends caddy && \
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

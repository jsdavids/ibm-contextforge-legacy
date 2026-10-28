# Build context is the repo root (the parent of this project), so the shared
# agent-mesh-core package can be installed alongside:
#   docker compose -f deploy/docker-compose.yml build
FROM python:3.13-slim
WORKDIR /app
COPY agent-mesh-core /src/agent-mesh-core
COPY ibm-contextforge-legacy /src/ibm-contextforge-legacy
RUN pip install --no-cache-dir /src/agent-mesh-core /src/ibm-contextforge-legacy
COPY ibm-contextforge-legacy/deploy/bridge.docker.yaml /app/bridge.yaml
# named volumes inherit this ownership, so the non-root register job can write tokens
RUN mkdir /shared && chown 1000 /shared
USER 1000

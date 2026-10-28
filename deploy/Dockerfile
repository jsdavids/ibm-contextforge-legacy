# Build context is this project; agentmesh is fetched from its GitHub release
# tarball (see pyproject.toml), so the image needs no git:
#   docker compose -f deploy/docker-compose.yml build
FROM python:3.13-slim
WORKDIR /app
COPY . /src
RUN pip install --no-cache-dir /src && rm -rf /src
COPY deploy/bridge.docker.yaml /app/bridge.yaml
# named volumes inherit this ownership, so the non-root register job can write tokens
RUN mkdir /shared && chown 1000 /shared
USER 1000

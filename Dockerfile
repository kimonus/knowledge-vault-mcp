# syntax=docker/dockerfile:1.11
FROM ghcr.io/astral-sh/uv:0.12.21@sha256:a7aed3216253ee804de3e2d8afa5073baa1a177335345d43845cd4165e43b711 AS uv

FROM python:3.12.14-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e AS builder
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --extra embeddings --no-install-project
COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./
RUN uv sync --locked --no-dev --extra embeddings --no-editable

FROM python:3.12.14-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e AS runtime
LABEL org.opencontainers.image.title="Knowledge Vault" \
      org.opencontainers.image.description="Private personal knowledge MCP server" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.version="0.2.0"
# The pinned base image predates the fix for CVE-2026-103111. Remove this step when the base
# image digest is moved to one that already ships this version or a later one.
RUN apt-get update \
    && apt-get install --yes --no-install-recommends --only-upgrade libpcre2-8-0=10.42-1+deb12u2 \
    && rm -rf /var/lib/apt/lists/*
RUN groupadd --gid 10001 knowledge-vault \
    && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /nonexistent knowledge-vault
WORKDIR /app
COPY --from=builder --chown=10001:10001 /app/.venv /app/.venv
COPY --from=builder --chown=10001:10001 /app/migrations /app/migrations
COPY --from=builder --chown=10001:10001 /app/alembic.ini /app/alembic.ini
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/nonexistent
USER 10001:10001
EXPOSE 8000
ENTRYPOINT ["knowledge-vault-api"]

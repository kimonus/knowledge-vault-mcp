# syntax=docker/dockerfile:1.11
FROM ghcr.io/astral-sh/uv:0.13.6@sha256:af22a52f5eafef3d78e551599d405c02df61919d18028f0cea8eda93bc9986d0 AS uv

FROM python:3.12.15-slim-bookworm@sha256:6a0e06f5ba0b373d051aabd3fffb9b873f27f9a2a3a14bcbe37dee6f0e3bbc70 AS python-base
# The refreshed base fixes Perl and PCRE2 but still predates the OpenSSL CVE-2026-84782 fix.
RUN apt-get update \
    && apt-get install --yes --no-install-recommends --only-upgrade \
      libssl3=3.0.22-1~deb12u2 openssl=3.0.22-1~deb12u2 \
    && rm -rf /var/lib/apt/lists/*

FROM python-base AS builder
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

FROM python-base AS runtime
LABEL org.opencontainers.image.title="Knowledge Vault" \
      org.opencontainers.image.description="Private personal knowledge MCP server" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.version="0.6.0"
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

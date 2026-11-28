# syntax=docker/dockerfile:1.11
FROM node:24.15.0-bookworm-slim@sha256:4e6b70dd6cbfc88c8157ba19aa3d9f9cce6ba4703576d55459e45efcbc9c5f5d AS frontend
WORKDIR /app/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web ./
COPY dashboard/src/knowledge_vault_dashboard/contract.json /app/dashboard/src/knowledge_vault_dashboard/contract.json
RUN npm run build \
    && npm run test:sbom \
    && npm sbom --sbom-format=cyclonedx > /app/web/full.sbom.json \
    && node scripts/frontend-sbom.mjs full.sbom.json package.json frontend.sbom.json

FROM ghcr.io/astral-sh/uv:0.13.7@sha256:96f40d3904aad8d0bd010539001d36c8e2188b981e1334471a90c4a1d93bb490 AS uv
FROM python:3.12.15-slim-bookworm@sha256:6a0e06f5ba0b373d051aabd3fffb9b873f27f9a2a3a14bcbe37dee6f0e3bbc70 AS python-base
RUN apt-get update \
    && apt-get install --yes --no-install-recommends --only-upgrade \
      apt=2.6.2 libapt-pkg6.0=2.6.2 \
      libpam-modules=1.5.2-6+deb12u3 libpam-modules-bin=1.5.2-6+deb12u3 \
      libpam-runtime=1.5.2-6+deb12u3 libpam0g=1.5.2-6+deb12u3 \
      libzstd1=1.5.4+dfsg2-5+deb12u1 \
      libssl3=3.0.22-1~deb12u2 openssl=3.0.22-1~deb12u2 \
    && rm -rf /var/lib/apt/lists/*

FROM python-base AS builder
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app/dashboard
COPY dashboard/pyproject.toml dashboard/uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY dashboard/src ./src
# Build-time source reuse only: the wheel bundles these pure helpers, not the core runtime.
COPY src/knowledge_vault/auth/hosts.py src/knowledge_vault/auth/tokens.py src/knowledge_vault/auth/cloudflare.py /app/src/knowledge_vault/auth/
COPY --from=frontend /app/dashboard/src/knowledge_vault_dashboard/static ./src/knowledge_vault_dashboard/static
RUN uv sync --locked --no-dev --no-editable

FROM python-base AS runtime
LABEL org.opencontainers.image.title="Knowledge Vault Dashboard" \
      org.opencontainers.image.description="Optional read-only Explorer, Portal and isolated browser BFF" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.version="0.1.0"
RUN groupadd --gid 10001 knowledge-vault \
    && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /nonexistent knowledge-vault
WORKDIR /app/dashboard
COPY --from=builder --chown=10001:10001 /app/dashboard/.venv /app/dashboard/.venv
COPY --from=frontend /app/web/frontend.sbom.json /usr/share/knowledge-vault-dashboard/frontend.sbom.json
ENV PATH="/app/dashboard/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 HOME=/nonexistent
USER 10001:10001
EXPOSE 8080
ENTRYPOINT ["knowledge-vault-dashboard"]

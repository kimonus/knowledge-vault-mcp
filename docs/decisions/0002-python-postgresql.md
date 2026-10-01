# ADR 0002: Python and PostgreSQL/pgvector

- Status: Accepted
- Date: 2026-08-30

## Context

The service needs current MCP support, strict schema validation, transactional ingestion, durable
full-text search, vector retrieval, and an ecosystem suited to local embedding inference.

## Decision

Use Python 3.12+, MCP Python SDK, FastAPI/Pydantic, async SQLAlchemy/psycopg, Alembic, PostgreSQL,
and pgvector. PostgreSQL is the only authoritative state store.

## Consequences

One transactional system covers lifecycle state, text indexes, vectors, and jobs. Operators must
run PostgreSQL with the vector extension and manage migrations. Python model loading needs bounded
worker resources and a separately provisioned cache.

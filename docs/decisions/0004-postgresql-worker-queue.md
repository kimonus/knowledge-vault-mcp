# ADR 0004: PostgreSQL-backed embedding queue

- Status: Accepted
- Date: 2026-08-30

## Context

Embedding is asynchronous and must survive process restarts without adding a second broker to a
small private deployment.

## Decision

Store embedding jobs in PostgreSQL and claim due rows with `FOR UPDATE SKIP LOCKED`. Track attempts,
leases, model identity, next-run time, and dead state. Retry with capped exponential backoff and
jitter. Preserve an old vector until a replacement is successfully computed.

## Consequences

The queue shares backup and transactional boundaries with assertions and supports safe concurrent
workers. It is not intended for very high throughput; queue metrics and dead-job review are
required operational signals.

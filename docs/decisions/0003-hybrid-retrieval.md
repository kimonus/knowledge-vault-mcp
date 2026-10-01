# ADR 0003: Weighted hybrid retrieval

- Status: Accepted
- Date: 2026-08-30

## Context

Full-text retrieval is precise and immediately available, while semantic retrieval handles
paraphrases and multilingual queries but depends on asynchronous local embeddings.

## Decision

Generate independent PostgreSQL full-text and pgvector candidate rankings and combine them with
weighted reciprocal-rank fusion: 0.55 text and 0.45 vector. Default to current assertions and use a
signed, stable cursor. Fall back to text when the provider or a row embedding is unavailable.

## Consequences

New knowledge remains searchable during queue delay or model outage. Ranking is explainable and
deterministic for a candidate set, at the cost of two searches and parameters that require future
measurement rather than unreviewed automatic tuning.

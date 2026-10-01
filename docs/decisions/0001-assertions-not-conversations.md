# ADR 0001: Store assertions, not conversations

- Status: Accepted
- Date: 2026-08-30

## Context

The product needs durable personal knowledge, not a second chat archive. Raw transcripts amplify
privacy exposure, prompt-injection risk, irrelevant retrieval, and deletion ambiguity.

## Decision

Persist only atomic, typed assertions plus bounded provenance. Never persist assistant reasoning or
conversation transcripts. Treat all assertion text as untrusted data. Corrections create explicit
supersession links and hard deletion removes all content-bearing related rows.

## Consequences

The client must extract and classify facts before ingestion, and some conversational nuance is
intentionally lost. Retrieval, auditing, correction, and forgetting become smaller and clearer.

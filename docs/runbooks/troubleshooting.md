# Troubleshooting

## API or worker waits for migrations

Inspect the migration Job and `alembic current`. Verify the database URL uses the psycopg driver,
the role can create the vector extension/tables, and the pgvector server image is compatible. Fix
the migration or restore; do not remove the wait check.

## Readiness is failing

`/health/live` only confirms the process. `/health/ready` checks database connectivity and reports
embedding capability as `enabled`, `degraded` (the model could not be loaded; search is serving
text results), or `disabled`. Schema revision is enforced by the init container, not by
readiness. Inspect structured logs by request ID, PostgreSQL availability, and Secret key names.
Logs deliberately exclude assertion text, token text, and exception messages: an unexpected
failure is logged as `unhandled_error` or `tool_failed` with the exception type, an SQLSTATE when
there is one, and code locations.

## Unauthorized or forbidden tools

Confirm the client sends `Authorization: Bearer ...`, the configured digest was generated with the
current pepper, and the record has the required `knowledge:read`, `knowledge:write`, or
`knowledge:admin` scope. Reissue instead of attempting to recover a lost token.

## Search finds text but not semantic matches

Check `get_knowledge_statistics`, worker logs, model-cache mount, model name/dimensions, pending and
dead jobs, and worker memory. Full-text fallback is expected while embeddings are pending. Pods
load weights from local files only; if `/health/ready` reports `degraded`, the configured model is
not present in the mounted cache. After fixing the cause, requeue dead jobs with
`knowledge-vault-reembed` (see [embeddings.md](embeddings.md)).

## Flush cannot commit

The declared part/item totals must exactly match accepted staged items. Replay the same missing
part payload and then retry commit. A reused part number with a different hash is intentionally
rejected; abort the open batch and start with a new idempotency key if the original payload was
wrong. Individually rejected items still count toward the declared total, so a part with a
rejected item does not need to be resent. The tool error names the cause, for example
`batch_incomplete: … missing parts=[2]` or `conflict: flush batch has expired`.

## Rate limit or oversized request

Respect `Retry-After` (sent with every HTTP 429; MCP reports `rate_limited`), split assertions into bounded parts, and keep each request below
`KNOWLEDGE_VAULT_MAX_REQUEST_BYTES`. Do not raise limits until pod/database capacity and abuse risks
have been reviewed.

## Cloudflare reference connector disconnected

Check the Cloudflared Pod readiness endpoint and content-free logs, DNS, TCP/UDP egress on port 7844,
the mounted token Secret key, clock skew, the published route, Access application binding, and the
internal MCP Service URL. Rotate the tunnel token if logs suggest disclosure. For another edge
transport, follow its diagnostics while preserving the same origin-authentication boundary. Do not
expose an unauthenticated public ingress as an emergency workaround.

## Backup failed

Check repository reachability, mounted password file, DB permissions, temporary space, retention
errors, and `restic check`. Preserve the failed Job logs without credentials. A green next run does
not replace a disposable restore test.

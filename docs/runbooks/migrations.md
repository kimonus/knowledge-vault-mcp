# Database migration and rollback

## 0.6.0 temporal evidence / migration 0006

0006 creates an empty evidence table, checks, indexes and an immutability trigger; it never updates
legacy assertions or invents chronology. Restore-test a backup, rehearse disposable upgrade and
run all gates before rollout. Use the chart migration Job, refresh client schemas/flush skill,
then verify both import orders and current/history with an authenticated deployed client. Read
API logs and restart counts afterwards; local MCP tests do not prove a live deployment.

Use `get_knowledge_temporal_diagnostics` before/after gradual enrichment. Counts are metadata
coverage, not truth. Submit reviewed evidence through normal flushes, preserving source keys and
payloads. Existing IDs, dates, source rows and correction chains are retained; superseded legacy
rows require review first. No live data enrichment is performed by installation.

Prefer application rollback retaining 0006. Old images retain unstructured operations but do not
understand temporal resolution; pause modifications to structured assertions while rolled back,
because old writers lack the correction boundary. `alembic downgrade 0005_knowledge_maintenance`
drops temporal evidence. Stop writers and backup/restore-test before explicitly accepting that
loss; original assertions survive. Re-upgrade creates an empty evidence table. Rebuild answers
by rerunning the resolver on each complete identity, without embedding or LLM calls. See
[temporal operations and limits](../temporal-knowledge.md).

## 0.5.0 / migration 0005

Back up and restore-test before upgrading. 0005 adds assertion metadata with server defaults,
contexts, relationships, aliases, usage aggregates and expiring maintenance receipts. Existing
text, IDs, sources, embeddings and supersession pointers are retained; confidence is not migrated
to verified evidence. The preceding application can read/write on the expanded schema. Prefer
application rollback while retaining it. Downgrading 0005 destroys its metadata/links/counters;
do not downgrade a real vault merely to roll back application images. Refresh client tool schemas
and the flush skill after migration. Production data repair is a separate previewable operation.

Alembic owns schema changes. The Helm post-install/post-upgrade Job runs `alembic upgrade head`; API
and worker init containers wait until `alembic current` reports the newest revision contained in
their own image. During an upgrade the new Pods therefore do not start until the migration has
run, while the previous Pods keep serving. A migration must stay compatible with the previous
application version for the duration of that rollout: add first, remove in a later release.

Revisions are frozen. Each one spells out its own DDL and must never derive it from
`Base.metadata`, because the models keep changing after the revision is written. A model change
that affects the schema needs a new revision; `alembic check` in the integration suite fails when
models and migrations disagree, and the suite downgrades to base and upgrades through every
revision.

## Before an upgrade

1. Read every migration and its downgrade, lock/space implications, and pgvector compatibility.
2. Take and restore-test a fresh backup.
3. Run `uv run alembic upgrade head` against a disposable copy of production-sized data.
4. Run the full integration suite and measure migration time.

For Kubernetes, deploy the reviewed application image and watch the migration Job:

```bash
helm upgrade knowledge-vault charts/knowledge-vault -n knowledge-vault -f OPERATOR_VALUES.yaml
kubectl -n knowledge-vault logs -f job/knowledge-vault-knowledge-vault-migration
kubectl -n knowledge-vault get pods
```

Do not bypass a failed migration by removing the init containers.

## Rollback

Prefer a forward corrective migration. Use `alembic downgrade REVISION` only when the reviewed
downgrade is data-preserving and the old application remains compatible. For destructive or failed
data migrations: stop writers, restore the verified pre-upgrade backup into a clean database,
deploy the matching old application/chart, validate, then switch the private endpoint. Preserve
the failed database for investigation.

Never run schema downgrade and `helm rollback` blindly: Helm rollback does not reverse database
state.

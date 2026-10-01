# Database migration and rollback

Alembic owns schema changes. The Helm post-install/post-upgrade Job runs `alembic upgrade head`; API
and worker init containers wait until the database reports the expected current revision.

## Before an upgrade

1. Read every migration and its downgrade, lock/space implications, and pgvector compatibility.
2. Take and restore-test a fresh backup.
3. Run `uv run alembic upgrade head` against a disposable copy of production-sized data.
4. Run the full integration suite and measure migration time.

For Kubernetes, deploy the reviewed application image and watch the migration Job:

```bash
helm upgrade knowledge-vault charts/knowledge-vault -n knowledge-vault -f OPERATOR_VALUES.yaml
kubectl -n knowledge-vault logs job/knowledge-vault-knowledge-vault-migration
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

# Backup, restore, and disaster recovery

The backup image runs `pg_dump --format=custom` into a temporary filesystem, sends it to an
encrypted restic repository, applies retention, and executes `restic check --read-data-subset`.
The chart is disabled by default and requires an existing Secret containing `database-url`,
`repository`, and `password`.

## Configure and verify backups

Use an off-cluster restic repository and an independently protected password/recovery record.
Initialize the repository once with the official restic client, create the Kubernetes Secret, set
`backup.enabled=true`, and inspect every CronJob result. At least monthly, run a full `restic check
--read-data`; the scheduled subset check is not a substitute for restore testing.

## Disposable restore test

1. Create a new isolated PostgreSQL/pgvector database with no production network consumers.
2. Use `restic snapshots --tag knowledge-vault-postgresql` and restore a chosen dump to a temporary
   directory.
3. Verify the repository snapshot identity and dump with `pg_restore --list`.
4. Restore with `pg_restore --clean --if-exists --no-owner --no-privileges --dbname=DISPOSABLE_URL`.
5. Run `alembic current`, start an API against the disposable DB, authenticate with disposable
   credentials, and verify representative search/fetch/correction behavior and row counts.
6. Destroy only the explicitly named disposable database and temporary restore directory.

Record restore duration, snapshot ID, migration revision, and checks performed—never assertion
content. A backup is not considered usable until this exercise succeeds.

## Disaster recovery

Stop writers, preserve the failed database volume for forensics, provision a clean compatible
PostgreSQL/pgvector instance, restore the newest verified snapshot, apply only reviewed forward
migrations, rotate DB/tunnel/bearer credentials, then restore API and worker service. Validate
health, queue depth, known assertion IDs, and deletion audit continuity before reopening access.

Hard-deleted data may remain in older backups until retention expires. Restrict restores and honor
deletion requirements by expiring backup generations according to policy.

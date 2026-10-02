# Backup, restore, and disaster recovery

The backup image runs `pg_dump --format=custom` into a temporary filesystem, sends it to an
encrypted restic repository, applies retention, and executes `restic check --read-data-subset`.
The Job runs with a read-only root filesystem; the dump and the restic cache live on its `/tmp`
volume. Every snapshot carries the tag `knowledge-vault-postgresql` and the host name
`knowledge-vault` (override with `BACKUP_HOST`), and retention is applied to that tag as one
group, so `keepDaily`/`keepWeekly`/`keepMonthly` bound the repository regardless of which Pod
produced a snapshot. Snapshots written by earlier versions under per-run paths carry the same tag
and are pruned by the same policy on the next run.
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

Hard-deleted data remains in backups taken before the deletion until retention removes those
snapshots—up to the longest configured period (six months with the default `keepMonthly`).
Restrict restores, and when a deletion must take effect sooner, shorten the policy or remove the
affected snapshots with `restic forget --prune`.

A successful run records a `backup` heartbeat in the database; the
[watchdog](monitoring.md) reports when it goes stale.

To keep the repository on a directory of the node—an external drive, for instance—set
`backup.repositoryHostPath` and, if the directory belongs to another user, `backup.runAsUser` and
`backup.runAsGroup`. The Job then fails to start when the directory is missing rather than
writing somewhere else. A drive in the same machine protects against a failed disk or a bad
deploy, not against losing the machine; keep the repository password somewhere that survives the
loss of the disk holding the database.

The Job's NetworkPolicy allows egress to the repository through `networkPolicy.backupEgress`
(TCP 443 by default); adjust it for SFTP, a LAN target, or another port.

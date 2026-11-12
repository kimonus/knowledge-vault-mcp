# Draft standalone backup and restore

This design is experimental and has not undergone a real backup/restore test. **Restore is disabled**
until fresh isolated target provisioning can be enforced. This is not a recovery procedure for
valuable data. The existing Kubernetes backup and its retention policy are
unchanged. These scripts never issue restic forget/prune or remove files. They must be reviewed and
tested with disposable isolated infrastructure before use.

Use the reviewed immutable backup image, overriding its entrypoint, and mount these scripts
read-only. Supply an independently initialized encrypted local restic repository at `/repository`,
its password through `RESTIC_PASSWORD_FILE`, and a fresh private scratch mount through `SCRATCH_DIR`.
Provision and protect these files and mounts outside source control. The restic password must be
recoverable independently of the original instance; losing it makes the repository unrecoverable.
Do not use a repository shared with production. Restic access and repository initialization must
be independently reviewed; this draft does not initialize or erase any repository.

Supply `PGSERVICEFILE` and `PGPASSFILE` as mounted operator-created files. The service named
`backup` must connect only to the source database; the service named `restore` must connect only
to an entirely new PostgreSQL 17 + pgvector instance created on a separate network and fresh
storage. Keep passwords solely in the password file, not service files, arguments, or logs.
Service files can themselves redirect libpq to any server: validating that configuration and the
physical separation of the target is an operator responsibility. Do not connect either script
to production. Network isolation, engine user mappings, file permissions, TLS for database traffic,
and backup image availability require platform-specific verification.

The backup script leaves a plaintext database dump in the mounted scratch area, including on failure.
Use a fresh private tmpfs or equivalent encrypted ephemeral storage with sufficient space; do not
mount a real data directory. The backup script refuses an existing dump name and does not clean scratch.
The platform's disposal of an ephemeral mount and its recovery risks must be understood by the
operator. Backups may contain all stored user data, so protect the encrypted repository, metadata,
restic password and scratch mount accordingly.

After `backup-safe.sh`, independently identify the complete restic snapshot ID and verify
repository integrity. A future restore must provision an isolated new database on fresh storage,
verify the selected snapshot, and restore without `--clean`. A connection file or catalog emptiness
check cannot establish that a database is newly provisioned or that it is isolated, nor prevent
concurrent changes. `restore-safe.sh` therefore always fails without connecting to a database or
writing data. Do not regard this draft as a tested recovery path.

The database dump does not preserve PostgreSQL cluster roles or operator secrets. Back up and
test recovery of the necessary roles and their privileges, the restic password, device-token
material and HMAC pepper, TLS private key and certificates, and other operator configuration in a
separate protected location. Never put these values in the database dump, this repository, logs,
or command-line arguments. A recovery design must address role creation, credential replacement,
and compatibility with the restored application before it can be exercised safely.

Before claiming recovery, verify schema revision, assertions, artifacts, provenance and temporal
state through an authenticated client. Test failed-upgrade recovery into a different fresh
instance. No schema downgrade is assumed safe. Cross-engine restore, cold disaster recovery,
snapshot immutability and backups of any model data remain unverified.

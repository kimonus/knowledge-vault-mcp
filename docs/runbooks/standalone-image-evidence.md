# Standalone image compatibility evidence

This experimental smoke helps collect Phase 1 evidence. It does not establish deployment support,
release readiness, or native execution by itself. Run it only on an authorized isolated test host,
with separately provisioned application and backup images and an independently obtained local
384-dimensional model. Never use production secrets, volumes or knowledge. Check the Docker data
root capacity before building; the homelab shares its daemon with the live cluster. Do not prune or
stop unrelated resources. The script neither builds nor pulls images and does not publish anything.

For each target, record the source commit, host OS and physical CPU, Docker client and server
versions, daemon location, build command, image IDs and immutable registry/platform digests where
available, model repository/revision and independently verified file hashes, and test outcome.
Capture how native execution was established independently of container architecture reporting:
remote daemons, virtual machines, binfmt and emulators can make `uname` or image metadata misleading.
Record failures and emulated runs as such. Never use a corporate host without owner authorization.

Provision the model separately, confirm it is readable by UID 10001, and run:

```sh
sh scripts/ci/standalone_image_smoke.sh APP_IMAGE BACKUP_IMAGE /absolute/path/to/model
```

The script resolves image IDs once, starts short-lived containers with network disabled, a
read-only root, dropped capabilities and no ports or credentials, and mounts only the model
read-only. It imports the pinned native dependencies and performs CPU inference without network,
checking the 384-dimensional result. It also checks the backup image's `pg_dump` and `restic`
executables. It verifies the images' expected non-root defaults and effective identities. Output
gives image IDs, architecture reports and versions, but must be paired with
independent native-host proof and model provenance. Success does not test backup/restore, MCP,
packaging, platform reachability, image signatures, attestations or supply-chain release gates.

Complete Phase 1 only after native runs on both amd64 and arm64 and dependency/wheel inspection,
including the pinned torch and sentence-transformers distributions. Existing release scans,
signatures, provenance and SBOMs must continue to cover every published platform and manifest;
publication requires separate authorization. An unavailable architecture remains unverified.

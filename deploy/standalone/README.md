# Standalone Compose package (experimental)

This is a reviewable implementation, **not a verified quick start or supported deployment**.
Docker Compose and Podman Compose, macOS VM forwarding, secrets, TLS, database initialization,
migration, backups, and authenticated end-to-end behavior have not been validated for this package.
Do not use it for valuable data until those tests and recovery exercises pass. The independent
multi-platform application image publication gate is also open. Helm remains unchanged.

The only published listener is TLS on `127.0.0.1:8443`. The gateway forwards `/mcp` and
`/api/v1/`; metrics, health and other paths are not exposed there. API authentication and scope
checks still apply. Containers share a private network, not a security boundary against a trusted
local user who controls the container engine; such a user may reach the internal HTTP origin or
read mounted secrets. Do not forward the listener or expose the container network. On macOS, VM
forwarding behavior must be checked on the actual engine and host. The API Host guard allows only
`localhost` and `127.0.0.1`; CORS is disabled. Use a certificate trusted by the client and valid
for whichever of these names the client uses. Never bypass TLS verification for a bearer token.

## Operator preparation

1. Select reviewed, available multi-architecture images for the application and
   `nginxinc/nginx-unprivileged` compatible with UID/GID 101 and set `KV_APP_IMAGE` and `KV_GATEWAY_IMAGE` to their fully
   qualified immutable `name@sha256:<64 lowercase hex>` references. The application image must
   contain the standalone schema guard and migration entry point from this branch. A local
   build tag is not a release digest. Verify signatures and platform availability independently.
2. Set `KV_SECRETS_DIR` to an absolute operator-owned directory outside the repository. Provision
   these **individual files** using an operator-local secret store or equivalent protected method:
   `database-url`, `token-pepper`, `bootstrap-tokens`, `postgres-password`, `tls-cert.pem`, and
   `tls-key.pem`. The database URL must address the new Compose service `db`, database and user
   `knowledge_vault`, and use the password in `postgres-password`. Percent-encode URL components
   correctly. `bootstrap-tokens` contains the application's HMAC token records, not plaintext
   bearer tokens; see [device token setup](../../docs/runbooks/minikube.md). Keep the plaintext
   token separately in an operator secret store and issue a separate admin token only when needed.
   Provision a TLS certificate and matching private key for the loopback name; never commit them.
3. Restrict directory and file permissions and verify the engine's mapped container users can read
   only the files they require: application UID 10001, PostgreSQL UID 999 and gateway UID 101.
   Docker and Podman on Linux and macOS have different user and mount mapping
   behavior; a host-readable file is not necessarily container-readable. Bind-mounted files are
   not a hardware secret store and are accessible to engine administrators, host backups and any
   process with sufficient host permissions. Do not print their contents or put them in shell
   arguments, Compose environment values, logs or version control.
4. The fresh named volume is durable. Its initialization requires PostgreSQL UID 999 to be able
   to write the volume and its parent directory. Verify ownership and user mappings for the chosen
   engine with a disposable empty volume first; do not recursively change ownership of an existing
   volume or reuse a production database. The postgres image, non-root initialization and
   read-only filesystem combination are not yet runtime verified. Do not use automatic volume
   ownership-changing options.

## Lifecycle under test

From this directory, after selecting an engine's Compose provider, use `docker compose` in the
examples below. For Podman, substitute the locally installed `podman compose` provider only after
verifying its behavior. Do not run these commands against an existing instance without a verified
backup and an upgrade plan. The commands start containers; they are included for review and have
not yet been established as a working quick start.

```sh
sh ./check-config.sh
docker compose config --quiet
docker compose up -d db
sh ./wait-db.sh
docker compose run --no-deps migrate
docker compose up -d api worker gateway
```

The bounded wait checks container health and fails if the database stops, becomes unhealthy, or
does not become healthy in two minutes. For Podman set `KV_COMPOSE_ENGINE=podman` before running
the wait script; the provider's Compose and inspect behavior is still unverified. The migration must exit
successfully before starting API or worker. It explicitly overrides the image entrypoint and
serializes migrations with a PostgreSQL advisory lock. `up` does not run it. For every upgrade,
stop **all** API and worker writers, take and independently verify a backup, run the explicit
migration, and only then start writers. Per-request/iteration schema checks fail closed on a
mismatch, but do not eliminate a concurrent migration race. Recovery from an incompatible upgrade
is by restoring a verified backup to an isolated instance; schema downgrade is not assumed safe.
Do not schedule the separate reembed or watchdog commands here: they lack this schema guard.
Lexical-only mode disables embeddings while retaining the dedicated maintenance worker.

The gateway resolves the API container address when nginx starts. If Compose replaces the API
container, restart the gateway after the replacement is healthy (`docker compose restart gateway`,
or the equivalent for the selected provider). Otherwise it may keep a stale address. Restarting
the gateway interrupts active MCP streams; clients must reconnect. Automatic re-resolution across
Docker and Podman has not been verified.

Before relying on the package, test a fresh database and restart; verify migrations and failed
migration behavior; exercise authenticated read/write, wrong scopes, missing credentials, Host
and Origin refusals through the TLS gateway; attempt access to metrics, database and origin from
outside the private network; test backup/restore; and verify actual host port forwarding on each
target engine. No release, deployment, data restore or cleanup is performed by this document.

# Standalone Docker deployment

The Docker and Podman packages are one initiative. This runbook covers a local Linux Docker
Engine; Podman, rootless Docker, Docker Desktop and macOS VM forwarding require their own runtime
verification. They do not block the Docker implementation. The existing Kubernetes deployment
remains independent. The optional Dashboard is not included in this standalone core MCP package.

## Prerequisites and images

Use Python 3.12+, OpenSSL, a local Linux Docker Unix socket, and Docker Compose with `!override`
support (Compose 2.24.4+). The implementation is exercised with Docker 29.1.3 and Compose 5.8.1.
Run as a non-root operator with Docker access. Engine administrators are trusted: they can read
volumes and bypass container networking. Remote engines and shared untrusted engine administrators
are outside this controller's boundary.

Build the reviewed source locally, or supply independently verified signed registry digests for
images containing this implementation. Local image IDs deliberately support offline source builds;
they are not published multi-platform releases. Provision all images before initialization:

```sh
docker build --pull=false -t knowledge-vault:local .
docker build --pull=false -f Dockerfile.backup -t knowledge-vault-backup:local .
docker pull pgvector/pgvector@sha256:137f044b0efe3d57f39b972b9b53641b1f2045b99d879e298bbf514a25787dcf
docker pull ghcr.io/nginxinc/nginx-unprivileged@sha256:8ff43bb0bd15a960d4cce6bcdfabf97c6aeb29067e3ec401f5d0eb77e66d4f8c
KV_APP_IMAGE=$(docker image inspect --format '{{.Id}}' knowledge-vault:local)
KV_BACKUP_IMAGE=$(docker image inspect --format '{{.Id}}' knowledge-vault-backup:local)
```

The controller never pulls an image. Check free space on the Docker data root before builds.
Multi-platform image publication and registry trust verification remain a separate release task.

## Create and start

From the repository root:

```sh
python3 deploy/standalone/docker.py --state "$HOME/kv-local" init \
  --app-image "$KV_APP_IMAGE" --backup-image "$KV_BACKUP_IMAGE"
python3 deploy/standalone/docker.py --state "$HOME/kv-local" start
python3 deploy/standalone/docker.py --state "$HOME/kv-local" status
```

The state directory must not exist. Its parent must be operator-controlled. Initialization creates
private host credentials, a self-signed loopback certificate valid for one year, a random Compose
project and newly allocated Docker volumes. Files in the host state stay mode 0600 under mode 0700
directories. Inside the separate credential volume, each secret is mode 0600 and owned by the
service UID that needs it: application 10001, PostgreSQL 999, gateway 101. A short, network-disabled
initializer alone runs as root with CHOWN; normal services run as non-root with read-only roots,
dropped capabilities and bounded resources. No existing host file or volume is re-owned.

The only host listener is `https://localhost:8443/mcp` (use `--port` at initialization to select
another unprivileged loopback port). PostgreSQL, origin HTTP and metrics have no published ports.
The application/database network is internal; only the TLS gateway also joins a frontend network.
Host and MCP Origin checks and application scope enforcement remain active. Do not forward this
listener, publish the container network, or expose the bearer endpoint publicly.

`start` stops writers, waits for PostgreSQL, explicitly migrates with the serialized migration
command, starts API/worker and restarts nginx to resolve any changed API address. It finishes with
an authenticated TLS API call and checks logs/restart counts. Ordinary application startup never
migrates. A schema mismatch fails closed. `stop` preserves data; `recreate` replaces containers
using the recorded volumes and checks TLS again. A private lock prevents concurrent lifecycle
commands for one state directory. Engine identity is recorded and checked on subsequent commands.

```sh
python3 deploy/standalone/docker.py --state "$HOME/kv-local" verify
python3 deploy/standalone/docker.py --state "$HOME/kv-local" stop
python3 deploy/standalone/docker.py --state "$HOME/kv-local" start
python3 deploy/standalone/docker.py --state "$HOME/kv-local" recreate
```

Failed initialization/restore leaves evidence and partial resources in place. Do not reuse its
directory: select a new one. The controller has no volume deletion or pruning command. Retained
stopped volume anchors and old credential volumes must be included in operator inventory and disk
encryption/retention decisions. Protect the state directory and Docker data root from other users
and host backups; a volume on the same disk is not a backup.

## Connect a local MCP client

Run `python3 deploy/standalone/docker.py --state "$HOME/kv-local" credential-path` to
resolve the current private credential generation. Use Streamable HTTP at
`https://localhost:8443/mcp`, its `device-token`, and explicitly trust its `tls-cert.pem`
as the CA for this connection. Resolve the path again after rotation. Store the token in the client's
private credential store; never put it in source control or process arguments. Never disable TLS
verification. See [client setup](client-setup.md) for the supported client configuration formats.
Hosted ChatGPT cannot reach a loopback listener on your computer.

This read-only check uses the locked MCP client dependencies, validates TLS and prints only the
number of discovered tools:

```sh
uv run python - <<'PY'
import asyncio, json, ssl
from pathlib import Path
import httpx2
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

async def check():
    state = Path.home() / 'kv-local'
    config = json.loads((state / 'config.json').read_text())
    secrets = state / 'credentials' / config['credential_generation']
    tls = ssl.create_default_context(cafile=str(secrets / 'tls-cert.pem'))
    headers = {'Authorization': 'Bearer ' + (secrets / 'device-token').read_text()}
    async with httpx2.AsyncClient(verify=tls, headers=headers, trust_env=False) as http:
        async with streamable_http_client('https://localhost:8443/mcp', http_client=http) as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                await session.initialize()
                print(len((await session.list_tools()).tools), 'MCP tools discovered')
asyncio.run(check())
PY
```

## Offline semantic search

Omitting `--model-dir` selects lexical search; the dedicated worker still expires staging,
purges unreferenced artifacts/confirmations/maintenance metadata and records its heartbeat.

For hybrid search, initialize a new instance with `--model-dir /absolute/path/to/snapshot`
and `--model-manifest /absolute/path/to/manifest.json`. By default the manifest is the
snapshot's sibling `<snapshot-name>.json`; optional `--model-manifest-sha256` pins its bytes.
Provision and verify the complete `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`
snapshot, revision `e8f8c211226b894fcb81acc59f3b34ba3efd5f42`, before initialization. The directory
must already exist. Use the manifest procedure in [FULL-PROFILE.md](../../deploy/standalone/FULL-PROFILE.md)
for an independently provisioned manifest; do not treat a freshly computed checksum as proof of
upstream authenticity. The existing M1 manifest format contains `model_id`, full 40-character
`revision`, `dimensions: 384` and an exact `files` mapping of relative paths to SHA-256 digests.
Initialization checks every file and records the manifest SHA-256. API and worker mount the same
contents at `/models/<manifest-sha256>`, which is the existing model identity used in embedding
jobs, stored vectors and model-filtered retrieval. No schema or MCP contract changes are involved.
Model files and the manifest are mounted read-only.
Downloads are disabled and API/worker have no external network. Each process loads one model copy;
the profile limits each to 3 GiB with bounded temporary memory. Allow additional RAM for PostgreSQL,
gateway, the host and the Docker daemon (8 GiB is tight for the full stack).

To enable embeddings for existing lexical data, take a backup, restore it into a fresh instance
with `--model-dir`, then enqueue a rebuild there:

```sh
python3 deploy/standalone/docker.py --state "$HOME/kv-restored" reembed
```

This checks schema compatibility and queues work for the configured model; it does not wait for
completion. Check embedding state and the worker heartbeat through MCP statistics before switching
clients. Do not replace weights in place under a recorded model identity. Changing dimensions
still requires a reviewed schema migration. Start, rotation, recreation and reembed recheck the
recorded inventory; the provider checks before and after first model construction. Changed files
fail closed on these paths. A process already holding verified weights keeps its in-memory copy;
never mutate a provisioned snapshot in place. Provision a new immutable snapshot/manifest, restore
into a fresh instance and rebuild instead. Old full-profile instances using the revision-only alias
need that fresh restore and rebuild before use with this controller. Hashes prove content integrity
relative to the supplied manifest, not the authenticity of its upstream publisher.

## Encrypted backup and fresh-instance recovery

Create an operator-owned mode 0700 repository directory on independently protected storage visible
to this local Docker Engine. Create a mode 0600 file containing a random restic password of at least
16 bytes and keep a recoverable copy separately from the instance and repository. Do not store its
value in shell history. Encryption does not protect against deletion by an engine/host administrator;
keep an independent offline or immutable copy. This controller does not prune snapshots.

```sh
python3 deploy/standalone/docker.py --state "$HOME/kv-local" backup \
  --init-repository --repository /absolute/private/backup-repository \
  --password-file /absolute/private/restic-password
```

Use `--init-repository` only for the first backup into an empty directory. Subsequent backups omit
it. The command dumps PostgreSQL into a private 1 GiB tmpfs, writes an encrypted restic snapshot,
checks all repository data and records the backup heartbeat only on success. It prints the full
snapshot ID. Failure exits nonzero and is visible to an operator scheduler; schedule this same
command with your usual timer if automatic backup is required. Monitor the backup heartbeat through
`get_knowledge_statistics`. The tmpfs size is a deliberate limit: larger databases need a reviewed
scratch/resource adjustment. No plaintext dump is written to host storage. A separate encrypted
`recovery-pepper` file preserves the HMAC key used by correction receipts; it is outside the SQL
dump and is recovered privately before serving. Device, database and TLS credentials are not
included and are generated anew. Missing recovery-key material fails closed.

Restore requires the exact 64-character snapshot ID and a NEW state directory. It allocates a new
database volume atomically through Docker, starts only its isolated PostgreSQL, checks network and
mount identities, and restores in a transaction without `--clean`. It cannot target an existing
instance, database URL or caller-selected volume. Repository access is read-only and the restored
schema must match the selected application image before writers can become ready.

```sh
python3 deploy/standalone/docker.py --state "$HOME/kv-restored" restore \
  --app-image "$KV_APP_IMAGE" --backup-image "$KV_BACKUP_IMAGE" --port 9443 \
  --repository /absolute/private/backup-repository \
  --password-file /absolute/private/restic-password --snapshot FULL_SNAPSHOT_ID
```

Restore generates NEW database/device credentials and a NEW TLS certificate. PostgreSQL roles are
created afresh by the target instance. The source token pepper is recovered from the encrypted
sidecar so correction retries retain exact receipt semantics. No source host credential files are
required. Reconnect clients with the new device token and CA.
Protect the new state directory, then verify actual required assertions, artifacts, provenance and
temporal history through TLS/MCP before switching clients. A dump executes SQL: restore only from
a trusted repository/snapshot. Engine exclusivity assumes no concurrent privileged administrator;
the controller cannot defend against someone who controls Docker.

The generic historical `restore-safe.sh` deliberately remains disabled. The supported restore path
is this fresh-instance controller. Cross-engine recovery and physical storage-loss recovery remain
separate operator verification, not conclusions inferred from a same-host test.

## Update and rotate credentials

Pull/build and independently review the new immutable application image first, then:

```sh
python3 deploy/standalone/docker.py --state "$HOME/kv-local" upgrade \
  --app-image NEW_IMMUTABLE_IMAGE --repository /absolute/private/backup-repository \
  --password-file /absolute/private/restic-password
```

The controller durably marks the upgrade in progress, stops the gateway, API and worker, and
checks they are stopped BEFORE creating and fully verifying the pre-upgrade backup. Acknowledged
committed writes before this quiescence boundary are therefore recoverable. A request interrupted
without acknowledgement may or may not have committed; clients must use their idempotency keys.
Downtime includes the dump, full repository verification, migration, worker acceptance and TLS
verification. Backup failure leaves writers stopped and migration unstarted. The controller
records the previous image and verified snapshot before selecting the candidate, and records that
migration may have begun BEFORE invoking it. After a successful migration subprocess reply,
it durably records `migration_completed: true` BEFORE exposing the gateway. This is the
**forward-only recovery boundary**, distinct from final functional acceptance. API readiness,
fresh worker maintenance and authenticated TLS verification still have to succeed before the
instance is marked ready. A failed or interrupted verification never makes pre-upgrade snapshot
rollback safe: the candidate may already have acknowledged client writes.

After that boundary, `recover` stops/restarts the candidate using the SAME database and image,
without restoring a snapshot or rerunning migration, then repeats fresh worker/TLS verification.
Acknowledged post-migration writes remain in that database even if the gateway subprocess
completed just before SIGKILL, or verification succeeded before the final ready-state save.
If forward recovery fails, writers remain stopped and recovery remains forward-only; retain the
candidate database and repair forwards. There is no automatic schema downgrade.

```sh
python3 deploy/standalone/docker.py --state "$HOME/kv-local" rotate-token
# Resolve credential-path and reconfigure clients, then retire older tokens:
python3 deploy/standalone/docker.py --state "$HOME/kv-local" rotate-token --retire-previous
```

Rotation installs a new credential volume and restarts services. The first command preserves overlap;
the second retains the CURRENT token and removes older token records from active configuration.
Each immutable host generation is sealed and fsynced before its separate volume is seeded. One
atomic, fsynced config replacement selects both the host generation and volume; files are never
switched separately. Rotation recovery goes forward from any selected generation. Interrupted
retirement never selects an older generation or reactivates its records. The token pepper is
preserved, so correction-receipt HMAC semantics survive rotation as well as restore.
Prior credential volumes and host files are retained privately for recovery. No admin token is
generated. For annual TLS renewal, restore a verified backup into a new instance (new CA/key), then
verify and switch clients; no automatic certificate renewal or external certificate authority is used.
Check expiration ahead of that downtime with
`openssl x509 -in <current-credential-directory>/tls-cert.pem -noout -checkend 2592000`.
An expired certificate fails TLS verification; the controller does not bypass verification or renew it.

## Interrupted operations and diagnostics

The lifecycle lock is held before reading mutable configuration, including initialization and
restore. Configuration generations, operation kind/phase, previous image, recovery snapshot,
credential selection and allocation intents are persisted before sensitive side effects.
Atomic replacement fsyncs the new file before rename and fsyncs the parent directory afterwards.
This assumes a local Linux filesystem and storage honoring fsync; it cannot promise survival of
hardware that loses acknowledged durable writes. SIGKILL and host restart leave an in-progress
state; a caught failure records a failed state. Ordinary commands refuse either state.

Use `status` for safe operation metadata and resource identities, then `recover`. Rotation resumes
forward. An upgrade interrupted before migration needs `recover --repository ... --password-file ...`
and takes another quiesced verified backup. A new upgrade records `upgrade_recovery: forward-v1`
before side effects. If migration started but `migration_completed` was never durably saved,
the gateway could not have been exposed by that operation. Recovery conservatively refuses to
guess whether migration committed; restore the recorded snapshot/previous image into a NEW
instance. This includes a migration subprocess that completed just before controller death,
before its success marker could be saved. All earlier acknowledged writes are in that snapshot.

If `migration_completed` is present, recovery is strictly forwards on the candidate database,
including after interrupted final verification. Do NOT restore the pre-upgrade snapshot for
that state: it may omit acknowledged candidate-version writes. A legacy r2 interrupted upgrade
has no `forward-v1` guarantee; its gateway may already have accepted writes. The controller stops
writers and refuses snapshot-rollback advice for that ambiguous legacy journal. Preserve its
candidate database for operator-led forward repair; this controller cannot retroactively prove
which writes r2 accepted. Interrupted initialization/restore also requires a new directory. Start/recreation
recovery verifies the exact schema without rerunning migration. `stop` remains available.

Allocation intents precede Docker calls, so a lost reply leaves an identifiable resource; retry
allocates fresh storage rather than adopting an uncertain volume. Retained host generations,
credential volumes and stopped anchors have no automatic garbage collection. Inventory their
recorded IDs/labels and actual mounts before any separately authorized manual removal. Old files
do not reactivate credentials; active bootstrap records determine overlap/retirement. Protect all
retained secret material and host backups with the same private-storage boundary.

Backup scratch is a 1 GiB tmpfs and repository operations have a 1,800-second deadline. Full
`restic check --read-data` duration grows with repository size and directly adds upgrade downtime.
Capacity exhaustion or verification timeout fails the upgrade before migration; plan measured
capacity and duration before scheduling it. Status and fixed error messages intentionally omit
subprocess stderr and secret values. A failed command needs operator recovery even if containers
look healthy. Service health endpoints alone cannot establish a completed lifecycle operation.

Acceptance reads the database clock before starting the stopped worker and requires a successful
worker heartbeat later than that boundary, at most 30 seconds old, within 60 seconds. Independent
`verify` waits for a heartbeat after its own clock sample. Standalone maintenance runs every ten
seconds. These read-only checks establish fresh maintenance readiness; running status establishes
only process liveness. Monitor heartbeat age and failures afterwards. Actual embedding processing
is established separately by synthetic full-profile E2E, not inferred from the maintenance beat.

## Verification

`tests/integration/test_standalone_docker_lifecycle.py` is opt-in and uses synthetic data only. Set
`KV_DOCKER_TEST_APP_IMAGE` and `KV_DOCKER_TEST_BACKUP_IMAGE` to locally built full image IDs and run:

```sh
uv run pytest tests/integration/test_standalone_docker_lifecycle.py -q
```

Set `KV_DOCKER_TEST_MODEL_DIR` and `KV_DOCKER_TEST_MODEL_MANIFEST` to a pre-provisioned model
and manifest for the full offline/concurrent-search
test. Tests never download models. CI supplies a tiny random synthetic model; a real-model runtime
test establishes inference and lifecycle behavior, not a semantic-quality benchmark. The ordinary
pgvector integration suite and Kubernetes/chart gates remain required. See the
[Docker completion evidence](../reviews/standalone-docker-completion.md) for executed checks and
the [Podman preflight](podman-macos-standalone-preflight.md) for deferred platform verification.

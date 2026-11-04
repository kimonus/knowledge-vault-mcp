# Synthetic retrieval feasibility spike

This is a local, synthetic mock-client feasibility check, not a retrieval-quality study or an
external-client gateway. It does not authorize production access, model inference, provider calls,
cluster use or public endpoints. The mock client runs in a separate Bubblewrap namespace with
access only to an owner-only Unix socket. A trusted server process seeds one synthetic assertion,
then uses a distinct generated `knowledge:read` bearer identity for MCP calls. The server enforces
write and admin scopes; the local gateway separately limits its supported retrieval tools. Its
allowlist is not a general-purpose MCP server and must not be exposed to an external client. Other
host processes running as the same Unix user can access that user's socket directory; the host user
and trusted server are outside the mock client's isolation boundary.

## Prerequisites and execution

- Linux, `/usr/bin/bwrap` with usable unprivileged user, PID and network namespaces, and UID/GID
  1000. This prototype's synthetic image passwd mapping deliberately refuses other IDs.
- Python 3.12 and the checkout's locked non-embedding dependencies (`uv sync --locked`); do not
  install the optional embedding extra. Sufficient temporary disk and memory are required: the
  compressed image is approximately 163 MB, the unpacked image approximately 447 MB, and PostgreSQL
  uses temporary storage. The prototype does not enforce host CPU or memory quotas.
- Access to Docker Hub only for the separate image fetch. The fetch pins the image index and amd64
  platform digests, disables inherited HTTP proxies, verifies every downloaded layer, and does not
  use the Docker daemon. It creates a new private destination and leaves a partial destination on
  failure. Inspect ownership and remove only the destination created for that run.

```sh
PYTHONPATH=.:src .venv/bin/python -m scripts.feasibility_fetch_image /tmp/kv-feasibility-image
PYTHONPATH=.:src .venv/bin/python -m scripts.feasibility_spike --image-layers /tmp/kv-feasibility-image
```

The second command verifies and copies the inputs into a fresh owner-scoped workspace, verifies
them again, unpacks the pinned layers in a restricted namespace, starts PostgreSQL 17 and pgvector
with a Unix socket, applies migrations to an empty temporary database, seeds a synthetic fixture,
and exercises MCP through the gateway. It uses no host Docker socket, host networking, model
mounts, model downloads, `.env` file or production database. Read-only namespace mounts include
the checkout, locked dependencies, Python runtime and staged image; the client cannot see the
database, MCP socket, checkout or seeder process. The server and seeder share a trusted process.
The read identity is generated for this one run and is not mounted in the client namespace.

On success the runner prints one JSON trace containing source commit and dirty state, tool counts,
returned IDs, first-seen order, response bytes and durations. The IDs are generated from synthetic
data. Agent-selected IDs, model-visible context and provider usage are null because no model or
provider is present; do not infer them from the trace. This success trace is local output, separate
from any scored gold. Supervised-run failures emit a minimal failure/timeout trace with unavailable
gateway details marked null; they do not preserve partial tool events. Preflight and hard-kill
failures may not produce a trace.
Results are held until size and result-count checks pass, but the underlying MCP response is
received before the gateway can check its encoded size.

## Failure and recovery boundaries

The gateway serializes calls, limits total calls, search pages, returned results, response bytes and
elapsed time, and closes after a backend failure or timeout. It supports only `search_knowledge`
and `get_knowledge`; rejected tools do not reach MCP. The mock client cannot prove behavior of an
actual model, including resistance to prompt injection. Usage tracking is disabled because normal
search and get can otherwise record usage events even for read-scoped principals.
The smoke checks that the synthetic `assertion_usage` table stays empty; that check is not a proof
that every database table is unchanged.

The supervisor records an unsettled-child marker before launching a sandbox and waits for its own
child processes before cleanup. It deletes only its locked, owner-checked workspace. A hard kill or
host crash can leave that marker and workspace; automatic recovery refuses it because the child may
still be alive. Inspect the specific owner-created workspace and its processes before operator
recovery; do not delete unrelated paths or containers. The marker and lock tests do not prove full
recovery from every kernel or host failure. The downloaded image directory is a separate operator
artifact and is not removed by the runner.

No real provider token usage, pricing, privacy or retention terms, actual model-visible context,
or hosted-client transport has been measured. A separately authorized client connectivity test
requires an approved private authenticated transport, isolation and credential review, provider and
region selection, privacy and retention terms, reliable context and usage reporting, enforceable
time, token and spend limits, and explicit operator approval. A study or production decision needs
additional independent authorization and evidence; this spike says nothing about retiring embeddings.

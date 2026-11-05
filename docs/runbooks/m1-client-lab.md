# Isolated M1 client evaluation lab

This lab supports the [independent client protocol](../architecture/knowledge-vault/client-evaluation-protocol.md). It is an infrastructure harness, not an assistant or scorer. It never connects to the production vault. The default smoke mode disables embeddings and therefore cannot measure multilingual retrieval quality. The separately selected semantic modes require an independently stored, verified offline snapshot.

## Offline semantic readiness (2026-11-04)

The target is `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`, 384 dimensions. No independent snapshot or exact upstream revision has been established on this host. The previously observed model copy is in production storage and must remain untouched. Common independent cache locations (`/mnt/data/models`, the user Hugging Face cache, and the user local share cache) were absent on 2026-11-04. **The real-model readiness run is blocked until the operator supplies an independent snapshot and its full revision.** No model was downloaded or substituted.

Use a separately acquired, complete snapshot at `/mnt/data/knowledge-vault-m1-models/<40-character-upstream-commit>/`. Acquisition requires explicit operator authorization and must take place without using the production model volume. Record the source URL, acquisition time, exact upstream commit, transfer method, and original checksum receipt in operator-local records. The directory must contain regular files only, including `modules.json`, `config.json`, `tokenizer_config.json`, and a weight file. Place a regular-file JSON manifest (not a directory, FIFO, symlink or mount) beside it at `/mnt/data/knowledge-vault-m1-models/<commit>.json` with `model_id`, `revision`, `dimensions`, and `files` mapping every relative file name to its SHA-256 digest. The manifest's revision must equal the directory name. The lab recomputes every hash, refuses missing or added files, symlinks and mounts, and records the manifest SHA-256. The manifest is a local integrity receipt; the operator's acquisition record establishes that the named commit is genuinely the upstream revision.

After authorization, the operator can fetch from the model's upstream repository on a separate staging machine using `huggingface_hub.snapshot_download(repo_id="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2", revision=FULL_UPSTREAM_COMMIT, local_dir=INDEPENDENT_STAGING_DIR)`. Confirm that `FULL_UPSTREAM_COMMIT` is a 40-character commit from the upstream repository, then transfer the complete resolved files into the dedicated root above. Generate the manifest from those transferred files, for example:

```python
from hashlib import sha256
from json import dumps
from pathlib import Path

revision = "FULL_UPSTREAM_COMMIT"
root = Path("/mnt/data/knowledge-vault-m1-models")
snapshot = root / revision
files = {}
for path in sorted(snapshot.rglob("*")):
    if path.is_symlink():
        raise SystemExit("snapshot contains a symlink")
    if path.is_file():
        files[path.relative_to(snapshot).as_posix()] = sha256(path.read_bytes()).hexdigest()
(root / f"{revision}.json").write_text(
    dumps(
        {
            "model_id": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
            "revision": revision,
            "dimensions": 384,
            "files": files,
        },
        sort_keys=True,
    )
    + "\n"
)
```

Once the snapshot exists, run the following from the reviewed branch. The commands perform no model download. They require a fresh private cache and force both Hugging Face Hub and Transformers offline. The model provider additionally passes `local_files_only=True`. The semantic smoke loads one model instance before starting the disposable database, checks 384-dimensional output, indexes one synthetic assertion, requires every job to complete, and searches for `car fixed` against `The automobile was repaired yesterday.` after confirming that the lexical query has zero matches. A degraded search or missing vector result fails the run.

```bash
/tmp/uv-x86_64-unknown-linux-gnu/uv run --offline python scripts/quality_client_lab.py semantic-smoke \
  --model-path /mnt/data/knowledge-vault-m1-models/<commit> \
  --model-manifest /mnt/data/knowledge-vault-m1-models/<commit>.json
```

For a later isolated client evaluation, select `semantic-serve` with the same model arguments and an operator-local `--output`. It shares the single loaded model between query embedding and the lab worker and checks that every assertion is indexed when the run ends. The endpoint, database, token, watcher, owner-checked cleanup, and Docker restrictions are the same as the disabled smoke mode. A failed model load exits before database startup. The manifest records server commit, schema revision, model revision and manifest digest, and measured incremental process resident memory.

Before either semantic command, check available RAM, Docker disk space, and one-minute load. The lab requires at least 5 GiB available RAM, 10 GiB Docker-root free space, and load below one runnable task per CPU. Its planning budget is 2 GiB for the model process plus the existing 768 MiB PostgreSQL cap and more than 2 GiB headroom. On 2026-11-04 the host had 14 GiB RAM, 7.6 GiB available, 3.9/4.0 GiB swap used, 23 GiB free on `/opt`, and load averages 0.20/0.28/0.44. These are a point-in-time assessment, not permission to run later. No real-model resource test was attempted.

## Safety preflight (2026-11-03)

- Baseline: upstream `main` at `31e74ae570b7f5bb9832b2528e5e367a513fa1bc` (PR #70 merged). Feature worktree is separate from the active homelab checkout.
- Host: 8 CPUs, 14 GiB RAM, about 8.1 GiB available; swap was 3.9/4 GiB used. Docker data root `/opt/docker` had 25 GiB free after operator cleanup.
- Docker daemon `homelab` is shared with active production Minikube. Active Kubernetes context is `minikube`. The harness does not call Kubernetes or modify that context.
- The pinned `pgvector/pgvector:0.8.1-pg17-trixie` image is cached. The harness caps PostgreSQL at 768 MiB RAM, one CPU, 128 processes, and a 512 MiB temporary data filesystem. The uniquely labeled container has Docker networking disabled and listens only on a private Unix socket in a newly created `/tmp` directory. It mounts that directory only; it mounts no production volumes and publishes no ports. Cleanup checks its owner label before removal.
- The configured embedding model is `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`, 384 dimensions. Its cache was found only in the production model volume. The lab must not mount or read that volume. An independent offline copy and revision/digest are still required for the genuine study. The runbook estimates about 1.3 GiB per process, with a 2 GiB API limit recommended for live embeddings.
- The official MCP Python SDK supplies a scripted transport client. A separately configured real assistant client and its evaluation-only credentials have not yet been verified. Do not use any personal client profile or credential store.

## Infrastructure smoke

Run from the feature worktree after checking no `KNOWLEDGE_VAULT_*`, `PG*`, `HF_*`, `HUGGINGFACE_*`, `DATABASE_URL`, `DOCKER_HOST`, `DOCKER_CONTEXT`, `CODEX_HOME`, or `MCP_CONFIG` variables are set. The script refuses them rather than adopting outside configuration. It also disables `.env` loading. The executable pins the local Docker socket and refuses an unexpected daemon, less than 10 GiB Docker space, less than 4 GiB available RAM, or a missing pinned image.

```bash
/tmp/uv-x86_64-unknown-linux-gnu/uv run --offline python scripts/quality_client_lab.py preflight
/tmp/uv-x86_64-unknown-linux-gnu/uv run --offline python scripts/quality_client_lab.py smoke
```

Smoke creates a fresh database, applies existing Alembic migrations, verifies zero assertions and zero temporal evidence, starts the existing FastAPI/MCP application on loopback with a random evaluation-only scoped token, and connects over real Streamable HTTP. Both the anonymous probe and authenticated MCP client explicitly disable environment-derived HTTP configuration (`trust_env=False`), proxies and redirects. The client URL is required to be plain HTTP on `127.0.0.1`; no inherited `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, altered `NO_PROXY`, netrc or certificate environment can reroute its token or payload. It checks initialization, tool discovery, begin/append/commit, duplicate commit, readback and search. Its trace is a list of tool names only; no source text or token is printed. It resets and verifies the database again before owner-checked teardown. On a failed run, inspect the error and rerun; do not reuse the failed instance. Every smoke run creates a private recovery directory under `~/.local/state/knowledge-vault/client-lab/smoke-<owner>` before Docker startup and prints its path. An explicit `--output` can select another private operator-local directory. The manifest and lifecycle record contain no password or token. `preflight` creates no recovery directory.

For a supervised assistant run, use a **new isolated client home and profile** with no existing MCP config or auth store, and one new lab invocation per packet. The local operator output directory must be outside Git and access controlled:

```bash
/tmp/uv-x86_64-unknown-linux-gnu/uv run --offline python scripts/quality_client_lab.py semantic-serve \
  --output /operator-local/m1/run-0001 \
  --model-path /mnt/data/knowledge-vault-m1-models/<commit> \
  --model-manifest /mnt/data/knowledge-vault-m1-models/<commit>.json
```

The command prints the loopback MCP URL and a temporary token-file path. Configure the assistant's MCP client using only that URL and token, with proxy use disabled for loopback traffic. The token file is created with mode 0600. The lab handles SIGINT and SIGTERM and holds a private file lock while running. A detached local launcher owns the Docker CLI for container creation. It waits for the CLI to finish before checking the container, then waits for kernel release of the parent lock and performs owner-checked cleanup. Parent termination during Docker startup therefore cannot make cleanup run before creation finishes. The launcher has a 75-second Docker command timeout; an uncertain command outcome is reported as incomplete. The output directory contains a minimal owner manifest from before startup for crash recovery. The launcher receives only a newly generated evaluation database password through a private mode-0600 file, which it unlinks before invoking Docker. It never receives the MCP token or production credentials.

For crash recovery (including SIGKILL or host failure), or to confirm idempotent cleanup, run:

```bash
/tmp/uv-x86_64-unknown-linux-gnu/uv run --offline python scripts/quality_client_lab.py cleanup \
  --output /operator-local/m1/run-0001
```

For an interrupted smoke run, find its printed recovery path or list `~/.local/state/knowledge-vault/client-lab/`, then run the same `cleanup --output` command with that directory. Cleanup reads the run manifest and durable `.lifecycle-status`, checks the parent lock when the socket directory exists, and checks recorded parent and launcher process identities in all cases. The lifecycle record distinguishes a completed launch, a failure before Docker was invoked, an uncertain Docker outcome, and incomplete cleanup. A missing socket directory never proves cleanup. An uncertain outcome requires `--confirm-daemon-settled`, even after the socket directory is gone. First verify that the Docker daemon has settled and no Docker CLI for this run remains. Cleanup checks the container's owner label and the output and socket owner markers, and removes only that run's container, token file and socket directory. Confirmed completed runs can be cleaned repeatedly. Missing or inconsistent lifecycle records require operator investigation; cleanup fails closed. If ownership checks or Docker removal fail, cleanup reports an error and preserves the uncertain resource for inspection. A new `serve` or `smoke` command creates and verifies a new empty database for the next run. Preserve failed-run traces and mark their status failed; never silently rerun the same run ID.

## Verified synthetic smoke (2026-11-03)

The synthetic smoke used PostgreSQL 17.8 and schema `0006_temporal_evidence`. The disposable container had Docker network mode `none`, only its generated socket directory mounted, no published ports, and bounded CPU, memory, processes and temporary data. An unauthenticated `/mcp` request returned 401. An authenticated SDK client initialized `knowledge-vault`, listed 30 tools, committed one synthetic item, repeated commit with the same assertion ID, read it back and found it by lexical search. The reset checked zero assertions, temporal evidence, temporal retirements and flush batches. Regression tests directed all proxy variables to a local trap and changed `NO_PROXY`; the authenticated token and payload reached the local target, and the trap received nothing. After the launcher correction, the bounded smoke passed again. A bounded operator-mode PTY test sent Ctrl+C to the wrapper; the detached launcher reported `complete` and removed the lab container, mode-0600 token file and socket directory without manual cleanup. No owner-labeled lab containers remained and the Docker volume count remained 26. Deterministic tests held Docker creation beyond the previous one-second window and terminated the parent before, during and after startup. No embedding or real assistant was used.

## Genuine study operator workflow

1. Obtain an **independent offline model snapshot** and verify its exact revision, file digests, offline load, 384-dimensional output, and memory on the lab host. Do not mount the production model volume. Run `semantic-smoke` successfully before any quality measurements. The default disabled mode remains only an infrastructure smoke.
2. Generate `41 × 3 = 123` blinded development packets with `scripts/quality_client_packets.py --split dev --repetitions 3 --output /operator-local/m1/packets.json`. Keep the packet file and its SHA-256 outside Git. Do not run all packets as part of lab setup.
3. Freeze client name/version, model/settings, fresh isolated profile, skill file path/digest, server commit, schema revision, PostgreSQL version, embedding revision/digest, host information, timing rules, run order and seed in a protected manifest. The harness writes `environment.json` with server and host facts and no credentials; add the client and reviewer process fields yourself.
4. For each packet and repetition, start a new `semantic-serve` instance with the verified model path and manifest, confirm its fresh zero-count database, use a fresh client conversation, submit only the blinded source, ask the assistant to flush to **this lab MCP**, then ask the packet question or state request. Capture the complete client tool trace, emitted items, commit receipts, readback IDs, indexing status, retrieved context, answer, citations and abstention in an access-controlled local directory. Do not check raw traces into Git.
5. Mark each planned run as `missing`, `failed`, or `completed`. A timeout, interruption, rejected item or missing readback remains visible; preserve the original trace. For a failed run, use a new run attempt identifier and fresh database. The manifest-aware scorer distinguishes missing judgments from observed zero outcomes.
6. Arrange two independent source-truth audits before either reviewer sees outputs. Seal two dated first-pass output judgments before adjudication. Record real reviewer identities and disagreements in protected local storage; do not invent labels.
7. Run `scripts/quality_score_judgments.py JUDGMENTS --packets PACKETS --report REPORT`. Publish only redacted aggregates, report missing labels and runs, and keep the scorer's backend metrics separate from actual client extraction and answer quality.

Do not use production knowledge, credentials, database, model volume, Kubernetes resources or public endpoints. This lab does not run the 123-packet study and produces no independent quality result.

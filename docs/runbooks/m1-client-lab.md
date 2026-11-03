# Isolated M1 client evaluation lab

This lab supports the [independent client protocol](../architecture/knowledge-vault/client-evaluation-protocol.md). It is an infrastructure harness, not an assistant or scorer. It never connects to the production vault. The current smoke mode disables embeddings and therefore cannot measure multilingual retrieval quality.

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

Smoke creates a fresh database, applies existing Alembic migrations, verifies zero assertions and zero temporal evidence, starts the existing FastAPI/MCP application on loopback with a random evaluation-only scoped token, and connects over real Streamable HTTP. It checks initialization, tool discovery, begin/append/commit, duplicate commit, readback and search. Its trace is a list of tool names only; no source text or token is printed. It resets and verifies the database again before owner-checked teardown. On a failed run, inspect the error and rerun; do not reuse the failed instance.

For a supervised assistant run, use a **new isolated client home and profile** with no existing MCP config or auth store, and one new lab invocation per packet. The local operator output directory must be outside Git and access controlled:

```bash
/tmp/uv-x86_64-unknown-linux-gnu/uv run --offline python scripts/quality_client_lab.py serve \
  --output /operator-local/m1/run-0001
```

The command prints the loopback MCP URL and a temporary token-file path. Configure the assistant's MCP client using only that URL and token. The token file is created with mode 0600. After each run, **always run owner-checked cleanup**, including after Ctrl-C or a failed client interaction:

```bash
/tmp/uv-x86_64-unknown-linux-gnu/uv run --offline python scripts/quality_client_lab.py cleanup \
  --output /operator-local/m1/run-0001
```

Cleanup reads the run manifest, checks the container's owner label, and removes only that run's container, token file and socket directory. In the verified PTY test, Ctrl-C stopped the wrapper before automatic cleanup, so the explicit command was necessary. A new `serve` command creates and verifies a new empty database for the next run. Preserve failed-run traces and mark their status failed; never silently rerun the same run ID.

## Verified synthetic smoke (2026-11-03)

The final smoke used PostgreSQL 17.8 and schema `0006_temporal_evidence`. The disposable container had Docker network mode `none`, only its generated socket directory mounted, no published ports, and bounded CPU, memory, processes and temporary data. An unauthenticated `/mcp` request returned 401. An authenticated SDK client initialized `knowledge-vault`, listed 30 tools, committed one synthetic item, repeated commit with the same assertion ID, read it back and found it by lexical search. The reset checked zero assertions, temporal evidence, temporal retirements and flush batches. A post-run Docker check found no labeled lab containers; Docker volume count remained 26. An operator-mode run created a mode-0700 directory and mode-0600 token file; the explicit cleanup command removed the container, token and socket after Ctrl-C. No embedding or real assistant was used.

## Genuine study operator workflow

1. Obtain an **independent offline model cache copy** and verify its exact snapshot revision, file digest, offline load, 384-dimensional output, and memory on the lab host. Do not mount the production model volume. Enable embeddings in a reviewed follow-up lab configuration and verify indexing completion before quality measurements. The current disabled mode is only an infrastructure smoke.
2. Generate `41 × 3 = 123` blinded development packets with `scripts/quality_client_packets.py --split dev --repetitions 3 --output /operator-local/m1/packets.json`. Keep the packet file and its SHA-256 outside Git. Do not run all packets as part of lab setup.
3. Freeze client name/version, model/settings, fresh isolated profile, skill file path/digest, server commit, schema revision, PostgreSQL version, embedding revision/digest, host information, timing rules, run order and seed in a protected manifest. The harness writes `environment.json` with server and host facts and no credentials; add the client and reviewer process fields yourself.
4. For each packet and repetition, start a new `serve` instance, confirm its fresh zero-count database, use a fresh client conversation, submit only the blinded source, ask the assistant to flush to **this lab MCP**, then ask the packet question or state request. Capture the complete client tool trace, emitted items, commit receipts, readback IDs, indexing status, retrieved context, answer, citations and abstention in an access-controlled local directory. Do not check raw traces into Git.
5. Mark each planned run as `missing`, `failed`, or `completed`. A timeout, interruption, rejected item or missing readback remains visible; preserve the original trace. For a failed run, use a new run attempt identifier and fresh database. The manifest-aware scorer distinguishes missing judgments from observed zero outcomes.
6. Arrange two independent source-truth audits before either reviewer sees outputs. Seal two dated first-pass output judgments before adjudication. Record real reviewer identities and disagreements in protected local storage; do not invent labels.
7. Run `scripts/quality_score_judgments.py JUDGMENTS --packets PACKETS --report REPORT`. Publish only redacted aggregates, report missing labels and runs, and keep the scorer's backend metrics separate from actual client extraction and answer quality.

Do not use production knowledge, credentials, database, model volume, Kubernetes resources or public endpoints. This lab does not run the 123-packet study and produces no independent quality result.

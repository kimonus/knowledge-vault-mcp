# Embedding model cache, migration, and rebuild

Every vector records the model that produced it. The schema uses 384 dimensions. Changing
dimensions requires a reviewed database migration; changing only the model at the same dimension
requires a full rebuild.

## Provide the model

Pods never download weights: the provider loads local files only and the chart sets
`HF_HUB_OFFLINE=1`. Populate a PersistentVolumeClaim once, in a controlled step with network
access, then mount it read-only through `modelCache.existingClaim`:

```bash
# On a trusted machine or in a one-off Job that mounts the claim read-write at /models.
HF_HOME=/models python -c "from sentence_transformers import SentenceTransformer; \
SentenceTransformer('sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2')"
```

On a single-node cluster the same step can run on the node with the application image, which
already contains the libraries, writing into the host directory that backs the claim:

```bash
docker run --rm -u "$(id -u):$(id -g)" --read-only --tmpfs /tmp --cap-drop ALL \
  -e HOME=/tmp -e HF_HOME=/models -v /path/to/model-cache:/models \
  --entrypoint python IMAGE -c "from sentence_transformers import SentenceTransformer; \
SentenceTransformer('sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2')"
```

Then confirm that it loads the way a Pod will load it—without a network, read-only, as the Pod's
user—before enabling embeddings:

```bash
docker run --rm -u 10001:10001 --network none --read-only --tmpfs /tmp --cap-drop ALL \
  -e HF_HOME=/models -e HF_HUB_OFFLINE=1 -v /path/to/model-cache:/models:ro \
  --entrypoint python IMAGE -c "from sentence_transformers import SentenceTransformer; \
print(SentenceTransformer('sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2', \
local_files_only=True).encode(['check']).shape)"
```

The cache directory name under `hub/models--…/snapshots/` is the model revision; record it.

The chart sets `HF_HOME=/models`, so the default `config.embeddingModel` identifier resolves from
that cache. Alternatively copy a model directory onto the volume and set `config.embeddingModel`
to its path, for example `/models/paraphrase-multilingual-MiniLM-L12-v2`. Record the model
revision you verified.

For local development only, `KNOWLEDGE_VAULT_EMBEDDING_ALLOW_DOWNLOAD=true` lets the first call
download the model into the user's Hugging Face cache.

### Memory

The default model occupies about 1.3 GiB in each process that loads it (measured on the reference
deployment: 1.1 GiB peak while loading, 1.27 GiB in the worker after embedding 1,726 assertions).
The worker loads it when it starts embedding; the API loads it on the first search. Each
process holds exactly one copy: requests that arrive during the load wait for it. Release 0.1.0
did not guarantee that—simultaneous first searches each loaded a copy and exhausted the limit. The chart's
default limits (2 GiB each) allow for that. Do not lower the API limit below that unless
`config.embeddingsEnabled` is `false`, or the first search will have the Pod killed for memory.

If the model cannot be loaded, the provider fails fast and retries the load once a minute:
`/health/ready` reports `"embedding": "degraded"`, search answers from full-text results, and
embedding jobs retry with backoff until they are marked dead.

## Rebuild or change the model

1. Verify license, provenance, digest, dimensions, multilingual quality, memory, and latency of the
   replacement model offline. Keep assertion data local.
2. Add the new model to the cache volume as described above.
3. If dimensions change, add a new vector column/index in a forward migration first; do not
   rewrite the only working vector in place.
4. Set `config.embeddingModel` and roll out the API and worker together. A worker embeds only
   jobs queued for its configured model, and search compares a query only with vectors of the
   configured model, so assertions not yet rebuilt are served by full-text search rather than by
   vectors from the other model.
5. Enqueue the rebuild in a worker Pod. The command can be rerun; it requeues completed, retrying,
   and dead jobs for the configured model and leaves jobs that a live worker holds:

   ```bash
   kubectl -n knowledge-vault exec deploy/knowledge-vault-knowledge-vault-worker -- \
     knowledge-vault-reembed
   ```

6. Monitor pending/retry/dead counts (`get_knowledge_statistics`, or the worker's
   `knowledge_vault_embedding_queue_depth` metric on its metrics port) until the queue is empty.
7. Compare a fixed multilingual query set for relevance and fallback behavior.

The same command recovers jobs that were buried after a model outage: fix the cause, then requeue.

To recover from a bad model, set `config.embeddingModel` back and rerun the rebuild. Vectors are
overwritten per assertion as each job completes, so the previous model's vectors are not kept
side by side; keep the previous cache until the rollback window and a verified backup have
passed.

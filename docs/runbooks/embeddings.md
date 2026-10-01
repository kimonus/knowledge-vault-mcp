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

The chart sets `HF_HOME=/models`, so the default `config.embeddingModel` identifier resolves from
that cache. Alternatively copy a model directory onto the volume and set `config.embeddingModel`
to its path, for example `/models/paraphrase-multilingual-MiniLM-L12-v2`. Record the model
revision you verified.

For local development only, `KNOWLEDGE_VAULT_EMBEDDING_ALLOW_DOWNLOAD=true` lets the first call
download the model into the user's Hugging Face cache.

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

# Embedding model migration and rebuild

The model name and dimensions are persisted with vector jobs. The initial schema uses 384
dimensions. Changing dimensions requires a reviewed database migration; changing only the model at
the same dimension still requires a full rebuild.

## Procedure

1. Verify license, provenance, digest, dimensions, multilingual quality, memory, and latency of the
   replacement model offline. Keep assertion data local.
2. Populate a new immutable model-cache PVC or image layer in a controlled build step. Production
   Pods must not download weights at startup.
3. If dimensions change, add a new vector column/index in a forward migration; do not rewrite the
   only working vector in place.
4. Deploy workers configured for the new model and run `uv run knowledge-vault-reembed` to enqueue
   rebuild jobs.
5. Monitor pending/retry/dead counts and search latency. Old vectors remain until each replacement
   succeeds.
6. Compare a fixed multilingual query set for relevance and fallback behavior. Switch query use to
   the new vector only after coverage is complete, then remove the old column in a later release.

To recover from a bad model, stop its workers and return query configuration to the old model. Do
not delete old vectors or cache until the rollback window and a verified backup have passed.

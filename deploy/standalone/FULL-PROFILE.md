# Offline embedding profile (experimental)

This override is a draft and has not been tested as a full Compose deployment. The separate Mac
image smoke loaded the model offline, but that does not verify this profile, semantic search,
concurrent model loading, maintenance, runtime memory, or Podman Compose mounts. Do not use it for
valuable data until the deployment and recovery gates pass.

The API and worker each load their own model copy. The override sets a 3 GiB limit per process;
the 8 GiB Podman VM also needs memory for PostgreSQL, gateway and the OS. Actual peak memory and
performance have not been measured for this profile. Both services require the same local files,
run with downloads disabled, and have no external network attached. `/tmp` is an ephemeral bounded
memory filesystem. Joblib previously reported serial fallback in the standalone image smoke.

Use only the verified snapshot of `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`
at revision `e8f8c211226b894fcb81acc59f3b34ba3efd5f42` (384 dimensions). Your previously copied
Mac model and adjacent manifest have the expected format. From the repository root, verify every
file and the manifest before using it, with `MODEL_ROOT` set to their parent directory:

```sh
export MODEL_ROOT="$HOME/kv-models"
python3 - <<'PY'
import os
from pathlib import Path
from scripts import m1_offline_model as model
root = Path(os.environ['MODEL_ROOT']).resolve()
revision = 'e8f8c211226b894fcb81acc59f3b34ba3efd5f42'
model.INDEPENDENT_ROOT = root
result = model.verify_snapshot(root / revision, root / (revision + '.json'))
if result['manifest_sha256'] != '4c82a02ce479a6f8fb02e3949c69685c16f7292ab7f185c22f5de7b9b9a91cf7':
    raise SystemExit('Model manifest digest mismatch')
print(result)
PY
export KV_MODEL_DIR="$MODEL_ROOT/e8f8c211226b894fcb81acc59f3b34ba3efd5f42"
```

Verify the actual mapped application UID 10001 can read the model files without changing an
existing directory or making private files world-readable. For an isolated test with the base
package already prepared, select both files consistently for **every** Compose command:

```sh
docker compose -f deploy/standalone/compose.yaml -f deploy/standalone/compose.full.yaml config --quiet
```

Equivalent Podman Compose syntax and engine-specific mounts remain unverified. The model's
container path includes its revision and is the stored embedding model identifier; switching from
another identifier does not automatically rebuild old embeddings. Do not run `reembed` here: the
standalone schema guard does not yet cover that command. A populated instance can therefore retain
lexical-only results for old assertions until a separately reviewed rebuild procedure exists.
Never replace files in place under this revision path. Use a separately verified model identity and
upgrade procedure when changing models.

# Offline embedding profile

The [managed Docker runbook](../../docs/runbooks/docker-standalone.md) uses this override for its
full profile and provides a schema-guarded `reembed` command. The limitations below describe the
historical manual/cross-engine path. Podman and macOS remain unverified.

Linux amd64 Docker is the exercised platform. The opt-in E2E processes an actual embedding job
and concurrent cold API searches using offline weights. Podman, macOS, arm64 and rootless Docker
need separate runtime verification. A model image smoke alone does not establish deployment health.

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
export KV_MODEL_MANIFEST="$KV_MODEL_DIR.json"
export KV_MODEL_ID=4c82a02ce479a6f8fb02e3949c69685c16f7292ab7f185c22f5de7b9b9a91cf7
```

Verify the actual mapped application UID 10001 can read the model files without changing an
existing directory or making private files world-readable. For an isolated test with the base
package already prepared, select both files consistently for **every** Compose command:

```sh
docker compose -f deploy/standalone/compose.yaml -f deploy/standalone/compose.full.yaml config --quiet
```

For managed Docker, pass `--model-dir`, `--model-manifest` and optionally the independently trusted
`--model-manifest-sha256` at initialization or fresh-target restore. The controller supplies the
three Compose variables above. It reuses the existing M1 manifest format (`model_id`, full revision,
`dimensions: 384`, exact relative-file SHA-256 inventory), without requiring the M1 lab-root layout.
Its manifest SHA-256 supplies `/models/<manifest-sha256>` as the existing embedding model string
through provider, queued jobs, persisted vectors and retrieval. No new schema is required.
API and worker verify file contents before and after their first load. Lifecycle commands recheck
the recorded manifest. Changed files fail closed; an already loaded process retains verified
in-memory weights. Hashes prove integrity relative to that manifest, not upstream authenticity.

Never replace files in place. Provision a new immutable snapshot/manifest, take a backup, restore
to a new managed instance and use its schema-guarded `reembed`. The earlier revision-only alias
also requires a rebuild. Dimensions remain 384; a dimension change needs a reviewed migration.
Equivalent Podman Compose syntax and engine-specific mounts remain unverified.

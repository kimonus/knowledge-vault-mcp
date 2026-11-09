#!/bin/sh
# Smoke locally provisioned images only. This is evidence, not a native-execution attestation.
set -eu

if [ "$#" -ne 3 ]; then
    echo "Usage: sh scripts/ci/standalone_image_smoke.sh APP_IMAGE BACKUP_IMAGE MODEL_DIRECTORY" >&2
    exit 2
fi
app=$1
backup=$2
model=$3
if [ ! -d "$model" ]; then
    echo "A provisioned local model directory is required." >&2
    exit 2
fi
model=$(cd "$model" && pwd -P)
case "$model" in *','*) echo "Model path must not contain a comma." >&2; exit 2;; esac

echo "Host: $(uname -s) $(uname -m)"
docker version --format 'Engine: {{.Server.Version}}'
docker info --format 'Engine architecture: {{.Architecture}}'
app_id=$(docker image inspect --format '{{.Id}}' "$app")
backup_id=$(docker image inspect --format '{{.Id}}' "$backup")
app_user=$(docker image inspect --format '{{.Config.User}}' "$app_id")
backup_user=$(docker image inspect --format '{{.Config.User}}' "$backup_id")
if [ "$app_user" != '10001:10001' ] || [ "$backup_user" != '999:999' ]; then
    echo "Image default users do not match the expected non-root identities." >&2
    exit 1
fi
echo "Application: $(docker image inspect --format '{{.Id}} {{.Os}}/{{.Architecture}}' "$app_id")"
echo "Backup: $(docker image inspect --format '{{.Id}} {{.Os}}/{{.Architecture}}' "$backup_id")"

# Containers have no network, no writable root, no privileges, no published ports, and no
# credentials. Use image IDs so a concurrent tag update cannot change the tested image.
docker run --rm --pull=never --network=none --read-only --cap-drop=ALL \
    --security-opt=no-new-privileges --pids-limit=256 --memory=4g \
    --mount "type=bind,src=$model,dst=/model,readonly" \
    --env HF_HUB_OFFLINE=1 --env TRANSFORMERS_OFFLINE=1 \
    --env TOKENIZERS_PARALLELISM=false --entrypoint /app/.venv/bin/python \
    "$app_id" -c 'import os, platform, torch, sentence_transformers
from sentence_transformers import SentenceTransformer
assert os.geteuid() == 10001 and os.getegid() == 10001
print("Container architecture:", platform.machine())
print("torch:", torch.__version__, "sentence-transformers:", sentence_transformers.__version__)
model = SentenceTransformer("/model", local_files_only=True, device="cpu")
vector = model.encode(["offline architecture smoke"], convert_to_numpy=True)
assert vector.shape == (1, 384), vector.shape
print("Offline inference: one 384-dimensional vector")'

docker run --rm --pull=never --network=none --read-only --cap-drop=ALL \
    --security-opt=no-new-privileges --pids-limit=64 --memory=512m \
    --entrypoint /bin/sh "$backup_id" -c 'set -e; test "$(id -u)" = 999; test "$(id -g)" = 999; uname -m; pg_dump --version; restic version'

echo "Smoke passed. Independently establish physical host, engine and image architecture;"
echo "matching reported architectures alone does not rule out emulation."

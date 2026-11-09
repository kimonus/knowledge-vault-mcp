# Apple Silicon Podman Machine image test

This is an **image test**, not a deployment quickstart. It has not yet been run on macOS or
Podman Machine. Run it only on a machine you are authorized to use and under its applicable
policies. It creates no vault, database, published port or application credentials. Builds need
network access for pinned build dependencies; the later inference container runs with no network
and never downloads a model.

1. Install Git, Python 3.12 or newer, and Podman through your approved process. Start an existing Podman
   Machine, or create an isolated one if you are authorized and have adequate disk and memory.
   Inspect the chosen machine and connection before changing anything:

   ```sh
   sw_vers
   uname -m
   podman machine list
   podman system connection list
   ```

   Inspect the chosen VM with `podman machine inspect MACHINE_NAME`, replacing `MACHINE_NAME`
   with its name from the list. If it is stopped, start it with `podman machine start MACHINE_NAME`.
   Check that the active system connection targets
   that machine; do not change another machine. Then inspect the running engine:

   ```sh
   podman version
   podman info --format 'Machine architecture: {{.Host.Arch}}'
   ```

   Record macOS, physical CPU, Podman client/server versions, VM memory/disk and engine
   architecture. Apple Silicon should report `arm64` in the VM. Check machine configuration and
   any translation/emulation settings; matching architecture strings alone do not prove native
   execution. See the official [machine documentation](https://docs.podman.io/en/latest/markdown/podman-machine-init.1.html)
   and [machine inspect reference](https://docs.podman.io/en/stable/markdown/podman-machine-inspect.1.html).

2. Create a separate checkout, fetch the draft branch and record its exact commit. Choose a new
   empty destination; do not overwrite an existing checkout. The branch is unmerged and may
   change; do not assume it is a release:

   ```sh
   git clone https://github.com/kimonus/knowledge-vault-mcp.git "$HOME/kv-image-test"
   cd "$HOME/kv-image-test"
   git fetch origin feat/standalone-image-compatibility
   git switch --detach FETCH_HEAD
   git rev-parse HEAD
   ```

3. Separately provision the verified model snapshot and its manifest in a dedicated directory
   under your home directory using an approved transfer method. The source on the development
   host is `/mnt/data/knowledge-vault-m1-models/`: copy the revision directory
   `e8f8c211226b894fcb81acc59f3b34ba3efd5f42` **and** its adjacent `.json` manifest. Do not copy
   vault data or credentials. For example, if SSH access from the Mac is approved, replace
   `LOGIN@DEVELOPMENT_HOST` with the host's SSH account and address and run on the Mac:

   ```sh
   mkdir -p "$HOME/kv-models"
   scp -r LOGIN@DEVELOPMENT_HOST:/mnt/data/knowledge-vault-m1-models/e8f8c211226b894fcb81acc59f3b34ba3efd5f42 "$HOME/kv-models/"
   scp LOGIN@DEVELOPMENT_HOST:/mnt/data/knowledge-vault-m1-models/e8f8c211226b894fcb81acc59f3b34ba3efd5f42.json "$HOME/kv-models/"
   ```

   On the Mac, set `MODEL_ROOT` to the directory containing both, then verify:

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
   assert result['manifest_sha256'] == '4c82a02ce479a6f8fb02e3949c69685c16f7292ab7f185c22f5de7b9b9a91cf7'
   print(result)
   PY
   ```

   Expected manifest SHA-256 is
   `4c82a02ce479a6f8fb02e3949c69685c16f7292ab7f185c22f5de7b9b9a91cf7`.
   The root must be a regular directory, not a symlink or mount point, and the verifier checks
   each file and rejects symlinks. Provisioning is separate from the smoke; never download model
   weights as part of a test.

4. From the checkout, build both images into the local Podman store. These tags are test-only and
   are not published. Confirm sufficient free space first; no universal minimum has been proven.

   ```sh
   podman build --platform linux/arm64 -t kv-test-app:arm64 .
   podman build --platform linux/arm64 -f Dockerfile.backup -t kv-test-backup:arm64 .
   ```

5. Check that the snapshot's path is shared into the Podman Machine at the **same absolute
   path** and is readable by the rootless container's UID 10001. A default home-directory
   share may work, but verify your actual machine configuration. This preflight uses the built
   application image and does not modify the source:

   ```sh
   podman run --rm --pull=never --network=none --read-only --read-only-tmpfs=false \
     --cap-drop=ALL --security-opt=no-new-privileges --pids-limit=64 --memory=512m \
     --mount "type=bind,src=$MODEL_ROOT/e8f8c211226b894fcb81acc59f3b34ba3efd5f42,dst=/model,readonly" \
     --entrypoint /bin/sh kv-test-app:arm64 -c \
     'set -e; test "$(id -u)" = 10001; test -r /model/model.safetensors; test -r /model/modules.json; echo "Model files visible to UID 10001"'
   ```

   If the bind mount or permissions fail, stop and record the error; do not use a recursive
   ownership option such as `:U` on the source or weaken permissions on an existing private
   directory. The [Podman bind-mount reference](https://docs.podman.io/en/latest/markdown/podman-run.1.html)
   explains remote machine mounts and their options. After the preflight succeeds, run:

   ```sh
   KV_SMOKE_ENGINE=podman sh scripts/ci/standalone_image_smoke.sh \
     kv-test-app:arm64 kv-test-backup:arm64 \
     "$MODEL_ROOT/e8f8c211226b894fcb81acc59f3b34ba3efd5f42"
   ```

   The smoke checks image IDs and non-root users, offline model inference and backup executable
   versions. A failure is a result to report, not a reason to turn off isolation. Preserve
   scrubbed output, the exact commit, model verification result, image IDs, host/VM architectures,
   engine versions, and whether Rosetta or emulation was active. Never include credentials or
   private knowledge in evidence. A successful image test still does not prove Podman deployment,
   localhost-only reachability, backup/restore or full platform support.

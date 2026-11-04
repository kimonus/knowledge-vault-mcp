#!/bin/bash
# One-shot coordinator; application and harness bytes come from the pinned checkout.
set -euo pipefail
umask 077
staging="$1"
python="$2"
export HOME="$staging/home" UV_CACHE_DIR=/tmp/uv-cache
export UV_PYTHON_DOWNLOADS=never UV_HTTP_RETRIES=0
export PYTHONDONTWRITEBYTECODE=1
export PATH="$staging/bin:/usr/bin:/bin"
export PYTHONPATH=.:src
stage=setup
fetches=0
smokes=0
finish() {
  code=$?
  trap - EXIT
  echo "final_stage=$stage exit=$code oci_fetch_sequences=$fetches full_smoke_invocations=$smokes"
  /usr/bin/python3 - <<'PY'
import json
from pathlib import Path
cg = Path('/sys/fs/cgroup') / Path('/proc/self/cgroup').read_text().strip().split('::')[1].lstrip('/')
print(json.dumps({'final_resources': {p.name: p.read_text().strip() for p in
    (cg / 'memory.peak', cg / 'memory.events', cg / 'cpu.stat', cg / 'pids.current') if p.exists()}}))
roots = list(Path('/tmp').glob('kv-feasibility-*'))
print(json.dumps({'remaining_harness_workspaces': [str(p) for p in roots],
                  'unsettled_workspaces': [str(p) for p in roots if (p / 'child-unsettled').exists()]}))
PY
  # Preserve any unexpected/unsettled workspace. Never remove it as generic scratch.
  if compgen -G '/tmp/kv-feasibility-*' > /dev/null; then
    echo 'cleanup=manual_recovery_required private_tmpfs_retained_until_unit_stop'
    code=1
  else
    /usr/bin/python3 - <<'PY'
import os, shutil
from pathlib import Path
for name in ('image', 'uv-cache'):
    p = Path('/tmp') / name
    if p.exists():
        assert not p.is_symlink() and p.stat().st_uid == os.getuid()
        shutil.rmtree(p)
        print('owner_private_removed=' + str(p))
PY
  fi
  exit "$code"
}
trap finish EXIT
test "$(id -u):$(id -g)" = 1000:1000
unit="$(awk -F/ '/0::/ {print $NF}' /proc/self/cgroup)"
systemctl show "$unit" -p RuntimeMaxUSec -p TimeoutStopUSec -p NoNewPrivileges -p KillMode
test "$(systemctl show "$unit" -p RuntimeMaxUSec --value)" = 20min
test "$(systemctl show "$unit" -p TimeoutStopUSec --value)" = 10s
test "$(systemctl show "$unit" -p NoNewPrivileges --value)" = yes
test "$(systemctl show "$unit" -p KillMode --value)" = control-group
test ! -e "$staging/one-run-started"
(set -o noclobber; echo started > "$staging/one-run-started")
mkdir -m 700 "$HOME"
cd "$staging/source"
test "$(git rev-parse HEAD)" = 14f9517e51e5221a00ae53833cf03360298f0e7c
test -z "$(git status --porcelain)"
timeout -k 5s 240s uv sync --locked --python "$python"
timeout -k 5s 30s uv lock --check
.venv/bin/python - <<'PY'
import importlib.util, json, os, sys
from pathlib import Path
assert sys.version_info[:2] == (3, 12)
site = Path('.venv/lib/python3.12/site-packages').resolve(strict=True)
root = Path(sys._base_executable).resolve(strict=True).parent.parent
assert (root / 'bin/python3.12').is_file() and site.is_dir()
assert importlib.util.find_spec('torch') is None
assert importlib.util.find_spec('sentence_transformers') is None
print(json.dumps({'python_version': sys.version, 'base_executable': sys._base_executable,
                  'mountable_python_root': str(root), 'site_packages': str(site),
                  'uid': os.getuid(), 'gid': os.getgid(), 'embeddings_extra': False}))
PY
stage=focused_tests
timeout -k 5s 120s .venv/bin/python -m pytest tests/unit/feasibility -q
stage=offline_guards
timeout -k 5s 30s .venv/bin/python "$staging/orchestration/scripts/ci/task_0014_guards.py" "$staging/orchestration"
timeout -k 5s 30s .venv/bin/python "$staging/orchestration/scripts/ci/task_0014_offline.py"
timeout -k 5s 30s .venv/bin/ruff check scripts/feasibility_diagnostics.py scripts/feasibility_lifecycle.py scripts/feasibility_spike.py scripts/feasibility_support/startup.py scripts/feasibility_support/server.py tests/unit/feasibility/test_feasibility_diagnostics.py
sha256sum scripts/feasibility_diagnostics.py scripts/feasibility_lifecycle.py scripts/feasibility_spike.py scripts/feasibility_support/server.sh scripts/feasibility_support/server.py scripts/feasibility_support/startup.py tests/unit/feasibility/test_feasibility_diagnostics.py
stage=isolation_preflight
timeout -k 5s 30s /bin/bash "$staging/orchestration/scripts/ci/task_0014_bwrap.sh" \
  "$staging/orchestration/scripts/ci/task_0014_verify.py"
stage=source_pre_fetch
test "$(git rev-parse HEAD)" = 14f9517e51e5221a00ae53833cf03360298f0e7c
test -z "$(git status --porcelain)"
echo "source_revision=$(git rev-parse HEAD) source_dirty=false all_preflight_gates=passed"
stage=oci_fetch
fetches=1
timeout -k 10s 240s .venv/bin/python -m scripts.feasibility_fetch_image /tmp/image
.venv/bin/python - <<'PY'
import json
from pathlib import Path
from scripts.feasibility_image import verify_layers, PLATFORM_DIGEST
layers = verify_layers(Path('/tmp/image'))
total = sum(size for _, size in layers)
print(json.dumps({'verified_platform_digest': PLATFORM_DIGEST, 'verified_layers': layers,
                  'compressed_bytes': total, 'two_layer_copies_bytes': total * 2,
                  'scratch_ceiling_bytes': 6 * 1024**3,
                  'extracted_rootfs_bytes': None}))
PY
stage=single_synthetic_smoke
smokes=1
timeout -k 10s 240s .venv/bin/python -m scripts.feasibility_spike --image-layers /tmp/image
stage=completed

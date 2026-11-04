#!/bin/bash
# Temporary TASK-0008 orchestration; never modifies the pinned harness.
set -euo pipefail
umask 077
stage=preflight
trap 'code=$?; echo "TASK-0008 stage=$stage exit=$code"; if [ "$code" -ne 0 ] && [ "$stage" = preflight ]; then echo "TASK-0008 BLOCKED; no OCI fetch or synthetic E2E execution"; fi' EXIT
test "$(id -u):$(id -g)" = 1000:1000
echo "identity=$(id -u):$(id -g) architecture=$(uname -m)"
cd "${1:?fixed checkout required}"
test "$(git rev-parse HEAD)" = 578f4758a81107e3c517c71afd80bda4d13d95d3
test -z "$(git status --porcelain)"
echo "source_revision=$(git rev-parse HEAD) source_dirty=false"
export PYTHONPATH=.:src PYTHONDONTWRITEBYTECODE=1
.venv/bin/python - <<'PY'
import importlib.util
import json
import os
import resource
import sys
from pathlib import Path

assert sys.version_info[:2] == (3, 12)
assert Path('.venv/lib/python3.12/site-packages').is_dir()
assert all(importlib.util.find_spec(name) is None for name in ('torch', 'sentence_transformers'))
assert all(Path(name).is_dir() for name in ('/usr', '/lib', '/lib64'))
group = Path('/sys/fs/cgroup') / Path('/proc/self/cgroup').read_text().strip().split('::')[1].lstrip('/')
limits = {name: (group / name).read_text().strip() for name in (
    'cpu.max', 'memory.max', 'memory.swap.max', 'pids.max')}
quota, period = limits['cpu.max'].split()
assert quota != 'max' and 0 < int(quota) <= 2 * int(period)
assert 2 * 1024**3 <= int(limits['memory.max']) <= 6 * 1024**3
assert limits['memory.swap.max'] == '0'
assert 0 < int(limits['pids.max']) <= 256
fs = os.statvfs('/tmp')
scratch_free = fs.f_bavail * fs.f_frsize
scratch_total = fs.f_blocks * fs.f_frsize
assert 4 * 1024**3 <= scratch_free <= scratch_total <= 6 * 1024**3
assert any(line.split()[4] == '/tmp' and ' - tmpfs ' in line
           for line in Path('/proc/self/mountinfo').read_text().splitlines())
host_available = int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines()
                          if line.startswith('MemAvailable:'))) * 1024
memory_headroom = min(host_available, int(limits['memory.max']) - int((group / 'memory.current').read_text()))
assert memory_headroom >= 2 * 1024**3
assert resource.getrlimit(resource.RLIMIT_FSIZE) == (536870912, 536870912)
print(json.dumps({'limits': limits, 'scratch_free_bytes': scratch_free,
                  'scratch_total_bytes': scratch_total, 'host_available_ram_bytes': host_available,
                  'effective_memory_headroom_bytes': memory_headroom,
                  'file_size_limit_bytes': 536870912, 'service_runtime_limit_seconds': 900,
                  'python': sys.version, 'embeddings_installed': False}), flush=True)
PY
scratch="$(mktemp -d /tmp/task-0008.XXXXXXXX)"
export HOME="$scratch/home"
mkdir -m 700 "$HOME"
echo "owner_private_scratch=$scratch mode=$(stat -c %a "$scratch")"

# Focused existing tests use mocks and synthetic temporary files, not OCI/network/models.
timeout --signal=TERM --kill-after=10s 120s .venv/bin/python -m pytest \
  tests/unit/feasibility -q -p no:cacheprovider

host_net="$(readlink /proc/self/ns/net)"
host_user="$(readlink /proc/self/ns/user)"
probe_code="$(cat <<'PY'
import json
import os
import sys
from pathlib import Path

assert (os.getuid(), os.getgid()) == (1000, 1000)
net = os.readlink('/proc/self/ns/net')
user = os.readlink('/proc/self/ns/user')
assert net != sys.argv[1] and user != sys.argv[2]
interfaces = [line.split(':')[0].strip() for line in Path('/proc/net/dev').read_text().splitlines()[2:]]
assert interfaces == ['lo']
assert len(Path('/proc/net/route').read_text().splitlines()) == 1
assert not Path('/proc/net/ipv6_route').read_text().strip()
assert not Path('/var/run/docker.sock').exists()
print(json.dumps({'bubblewrap_probe': 'passed', 'uid': os.getuid(), 'gid': os.getgid(),
                  'network_namespace': net, 'user_namespace': user,
                  'interfaces': interfaces, 'routes': 0, 'external_connect_attempts': 0}))
PY
)"
# Match the harness's unshare and staging mounts, without any live egress attempt.
set +e
timeout --signal=TERM --kill-after=5s 20s /usr/bin/bwrap \
  --unshare-all --die-with-parent --clearenv \
  --ro-bind /usr /usr --ro-bind /lib /lib --ro-bind /lib64 /lib64 \
  --proc /proc --dev /dev --tmpfs /tmp \
  /usr/bin/python3 -c "$probe_code" "$host_net" "$host_user"
probe_exit=$?
set -e
echo "bubblewrap_probe_exit=$probe_exit"
if [ "$probe_exit" -ne 0 ]; then
  # Only empty owned setup directories exist; no harness workspace/image was created.
  rmdir "$HOME" "$scratch"
  echo "preflight_scratch_removed=true smoke_executions=0 image_fetches=0"
  exit "$probe_exit"
fi
echo "TASK-0008 ALL PREFLIGHT GATES PASSED"

stage=image-fetch
echo "image_fetches=1"
echo "COMMAND: PYTHONPATH=.:src .venv/bin/python -m scripts.feasibility_fetch_image $scratch/image"
timeout --signal=TERM --kill-after=15s 300s \
  .venv/bin/python -m scripts.feasibility_fetch_image "$scratch/image"
stage=single-smoke
echo "smoke_executions=1"
echo "COMMAND: PYTHONPATH=.:src .venv/bin/python -m scripts.feasibility_spike --image-layers $scratch/image"
set +e
timeout --signal=TERM --kill-after=15s 240s \
  .venv/bin/python -m scripts.feasibility_spike --image-layers "$scratch/image"
smoke_exit=$?
set -e
echo "smoke_exit=$smoke_exit"
stage=cleanup
# No deletion/recovery of ambiguous or unsettled harness workspaces.
.venv/bin/python - "$scratch" <<'PY'
import json
import os
import shutil
import stat
import sys
from pathlib import Path

remaining = list(Path('/tmp').glob('kv-feasibility-*'))
print(json.dumps({'remaining_harness_workspaces': [path.name for path in remaining],
                  'unsettled_markers': sum((p / 'child-unsettled').exists() for p in remaining)}))
assert not remaining, 'Workspace retained; manual inspection required, no recovery attempted'
scratch = Path(sys.argv[1])
info = scratch.lstat()
assert stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid() and not info.st_mode & 0o077
assert scratch.parent == Path('/tmp') and scratch.name.startswith('task-0008.')
shutil.rmtree(scratch)
print('owned_image_and_scratch_removed=true')
PY
test "$smoke_exit" -eq 0
stage=complete
echo "TASK-0008 COMPLETED; synthetic mock only; model_visible_context=null provider_usage=null"

#!/bin/bash
# One-off diagnostic only: no image, application, model, or provider execution.
set -euo pipefail
umask 077
scratch=''
cleanup() {
  code=$?
  trap - EXIT
  if test -n "$scratch"; then
    /usr/bin/unlink "$scratch/stdout" || code=1
    /usr/bin/unlink "$scratch/stderr" || code=1
    rmdir "$scratch" || code=1
    test ! -e "$scratch" || code=1
    echo "owner_private_scratch_removed=$([ ! -e "$scratch" ] && echo true || echo false)"
  fi
  echo "diagnostic_exit=$code oci_fetches=0 full_smoke_executions=0"
  exit "$code"
}
trap cleanup EXIT
test "$(id -u):$(id -g)" = 1000:1000
echo "identity=$(id -u):$(id -g) architecture=$(uname -m)"
cat /proc/self/attr/current
awk '/^(NoNewPrivs|Seccomp|CapEff):/ {print}' /proc/self/status
/usr/bin/python3 - <<'PY'
import json
import os
import resource
from pathlib import Path

group = Path('/sys/fs/cgroup') / Path('/proc/self/cgroup').read_text().strip().split('::')[1].lstrip('/')
names = ('cpu.max', 'memory.max', 'memory.swap.max', 'pids.max', 'memory.current', 'pids.current')
limits = {name: (group / name).read_text().strip() for name in names}
quota, period = limits['cpu.max'].split()
assert quota != 'max' and 0 < int(quota) <= int(period)
assert int(limits['memory.max']) == 1024**3
assert limits['memory.swap.max'] == '0'
assert int(limits['pids.max']) == 64
assert int(limits['pids.current']) < 32
host_available = int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines()
                          if line.startswith('MemAvailable:'))) * 1024
headroom = min(host_available, int(limits['memory.max']) - int(limits['memory.current']))
assert headroom >= 512 * 1024**2
fs = os.statvfs('/tmp')
assert fs.f_blocks * fs.f_frsize == 1024**3
assert fs.f_bavail * fs.f_frsize >= 512 * 1024**2
assert any(line.split()[4] == '/tmp' and ' - tmpfs ' in line
           for line in Path('/proc/self/mountinfo').read_text().splitlines())
assert resource.getrlimit(resource.RLIMIT_FSIZE) == (16777216, 16777216)
assert 'NoNewPrivs:\t1' in Path('/proc/self/status').read_text()
print(json.dumps({'effective_cgroup': str(group), 'limits': limits,
                  'host_available_ram_bytes': host_available, 'memory_headroom_bytes': headroom,
                  'scratch_total_bytes': fs.f_blocks * fs.f_frsize,
                  'scratch_free_bytes': fs.f_bavail * fs.f_frsize,
                  'service_runtime_limit_seconds': 60, 'bubblewrap_timeout_seconds': 20}), flush=True)
PY
scratch="$(mktemp -d /tmp/task-0010.XXXXXXXX)"
: > "$scratch/stdout"
: > "$scratch/stderr"
test "$(stat -c %a "$scratch")" = 700
test "$(stat -c %u:%g "$scratch")" = 1000:1000
echo "owner_private_scratch=$scratch mode=700 owner=1000:1000"
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
interfaces = [line.split(':')[0].strip() for line in Path('/proc/net/dev').read_text().splitlines()[2:]]
v4 = Path('/proc/net/route').read_text()
v6 = Path('/proc/net/ipv6_route').read_text()
print(json.dumps({'uid': os.getuid(), 'gid': os.getgid(), 'network_namespace': net,
                  'user_namespace': user, 'host_network_namespace': sys.argv[1],
                  'host_user_namespace': sys.argv[2], 'interfaces': interfaces,
                  'ipv4_route_inventory': v4, 'ipv6_route_inventory': v6,
                  'external_connect_attempts': 0}), flush=True)
assert net != sys.argv[1] and user != sys.argv[2]
assert interfaces == ['lo']
assert len(v4.splitlines()) == 1
for line in v6.splitlines():
    fields = line.split()
    assert fields[-1] == 'lo'
    assert fields[0] == '0' * 31 + '1' and fields[1] == '80'
assert not Path('/var/run/docker.sock').exists()
assert not Path('/run/docker.sock').exists()
mounts = {line.split()[4]: line.split()[5].split(',')
          for line in Path('/proc/self/mountinfo').read_text().splitlines()}
assert all('ro' in mounts[name] for name in ('/usr', '/lib', '/lib64'))
assert all(name in mounts for name in ('/proc', '/dev', '/tmp'))
print('bubblewrap_isolation_assertions=passed', flush=True)
PY
)"
echo 'actual_bubblewrap_invocations=1'
set +e
timeout --signal=TERM --kill-after=5s 20s /usr/bin/bwrap \
  --unshare-all --die-with-parent --clearenv \
  --ro-bind /usr /usr --ro-bind /lib /lib --ro-bind /lib64 /lib64 \
  --proc /proc --dev /dev --tmpfs /tmp \
  /usr/bin/python3 -c "$probe_code" "$host_net" "$host_user" \
  > "$scratch/stdout" 2> "$scratch/stderr"
probe_exit=$?
set -e
echo '--- exact Bubblewrap stdout ---'
cat "$scratch/stdout"
echo '--- exact Bubblewrap stderr ---'
cat "$scratch/stderr"
echo "bubblewrap_exit=$probe_exit"
exit "$probe_exit"

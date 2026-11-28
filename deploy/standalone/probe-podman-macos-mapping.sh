#!/bin/sh
# Optional synthetic fixture only; never supply real secrets or existing directories.
set -eu
if [ "$(uname -s)" != Darwin ]; then echo 'This probe is for macOS.' >&2; exit 2; fi
command -v podman >/dev/null || { echo 'Podman is required.' >&2; exit 2; }
image=${KV_TEST_IMAGE:-kv-test-app:arm64}
if ! podman image exists "$image"; then
    echo 'The selected local test image is unavailable; set KV_TEST_IMAGE.' >&2; exit 2
fi
if ! podman info >/dev/null; then echo 'The selected Podman engine is unavailable.' >&2; exit 2; fi
umask 077
parent=${KV_PROBE_PARENT:-$HOME}
case "$parent" in /*) ;; *) echo 'Probe parent must be absolute.' >&2; exit 2;; esac
if [ ! -d "$parent" ]; then echo 'Probe parent must already exist.' >&2; exit 2; fi
probe=$(mktemp -d "$parent/kv-mapping-probe.XXXXXXXX")
printf 'synthetic marker\n' > "$probe/marker"
chmod 600 "$probe/marker"
name=$(basename "$probe" | tr '.' '-')
printf 'Synthetic fixture: %s\n' "$probe"
for uid in 10001 999 101; do
    container="$name-uid$uid"
    if podman run --name "$container" --pull=never --network=none --read-only \
      --cap-drop=ALL --security-opt=no-new-privileges --pids-limit=32 --memory=128m \
      --user "$uid:$uid" --mount "type=bind,src=$probe/marker,dst=/fixture/marker,readonly" \
      --entrypoint /bin/sh "$image" -c 'test -r /fixture/marker'; then
        printf 'UID %s: marker readable\n' "$uid"
    else
        printf 'UID %s: not verified (access or engine failure)\n' "$uid"
    fi
done
echo 'Synthetic fixture and stopped probe containers were retained; no cleanup was performed.'

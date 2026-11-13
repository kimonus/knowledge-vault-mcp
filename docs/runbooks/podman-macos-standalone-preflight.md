# macOS Podman standalone preflight (experimental)

This is a **read-only diagnostic**, not a working deployment quickstart. The standalone Compose
package has passed disposable Linux Docker tests, but Mac Podman Compose, VM forwarding and private
secret-file mappings have not been verified. Do not put valuable vault data into this draft.

The separate [Mac image test](podman-macos-image-test.md) was completed by the owner for the
application and backup images, including offline inference with the verified model. That result
does not establish that the full Compose deployment works.

Run these commands in a macOS terminal with the intended Podman Machine already running:

```sh
sw_vers
uname -m
podman machine list
podman system connection list
podman version
podman info --format 'Machine architecture: {{.Host.Arch}}'
podman compose version
```

`podman compose` uses an external Compose provider; note its name and version. If the engine is
unreachable, start the intended existing machine using `podman machine start MACHINE_NAME` and
repeat the engine checks. Starting a machine changes its running state; the commands above only
inspect it. If the Compose provider is unavailable, stop: installing or changing it is a separate
operator action. Do not create or reset a machine to work around an unavailable provider.

To inspect the user namespace mappings exposed by the engine without printing configuration or
credential files, run:

```sh
podman info --format json | python3 -c 'import json,sys; d=json.load(sys.stdin); print(json.dumps(d.get("host", {}).get("idMappings", "not reported"), indent=2))'
```

Mapping output alone does not prove how a macOS bind-mounted file appears inside the VM or whether
UIDs 10001, 999 and 101 can read their respective files. The Linux CI test used ownership changes
only on newly generated disposable Linux files; do not repeat those changes on existing Mac files
or make private files world-readable. A Mac-specific, no-overwrite permissions and TLS setup, a
full authenticated Compose run, loopback forwarding verification and recovery exercise remain
necessary before a quickstart can be validated.

For a more direct **synthetic-only** check, the draft also includes
`deploy/standalone/probe-podman-macos-mapping.sh`. It uses the already built local image
`kv-test-app:arm64` by default (`KV_TEST_IMAGE` selects a different local image), creates a fresh
private marker under your home directory, and attempts to read it as the three numeric UIDs. It
does not read your vault or credential files, change existing file ownership, download an image,
or remove data. It leaves the synthetic marker and stopped probe containers in place. A failure
means access was not verified; inspect the printed engine error before drawing conclusions. A
pass does not establish full Compose, TLS, network isolation or backup/restore behavior.

From the repository root containing the draft:

```sh
sh deploy/standalone/probe-podman-macos-mapping.sh
```

The experimental package and its current limitations are in
[deploy/standalone/README.md](../../deploy/standalone/README.md); the optional model override and
manifest verification command are in [FULL-PROFILE.md](../../deploy/standalone/FULL-PROFILE.md).

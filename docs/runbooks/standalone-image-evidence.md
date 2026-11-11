# Standalone image compatibility evidence

This experimental smoke helps collect Phase 1 evidence. It does not establish deployment support,
release readiness, or native execution by itself. Run it only on an authorized isolated test host,
with separately provisioned application and backup images and an independently obtained local
384-dimensional model. Never use production secrets, volumes or knowledge. Check the Docker data
root capacity before building; the homelab shares its daemon with the live cluster. Do not prune or
stop unrelated resources. The script neither builds nor pulls images and does not publish anything.

For each target, record the source commit, host OS and physical CPU, Docker client and server
versions, daemon location, build command, image IDs and immutable registry/platform digests where
available, model repository/revision and independently verified file hashes, and test outcome.
Capture how native execution was established independently of container architecture reporting:
remote daemons, virtual machines, binfmt and emulators can make `uname` or image metadata misleading.
Record failures and emulated runs as such. Never use a corporate host without owner authorization.

Provision the model separately, confirm it is readable by UID 10001, and run with Docker (the
default):

```sh
sh scripts/ci/standalone_image_smoke.sh APP_IMAGE BACKUP_IMAGE /absolute/path/to/model
```

For Podman, use `KV_SMOKE_ENGINE=podman` before the same command. Only `docker` and `podman`
are accepted. The Podman path disables its additional read-only tmpfs mounts; the explicit
bounded `/tmp` mount is shared with the Docker test. Both modes are experimental.
See the [Apple Silicon Podman Machine image test](podman-macos-image-test.md) for setup and evidence.

The script resolves image IDs once, starts short-lived containers with network disabled, a
read-only root, dropped capabilities and no ports or credentials, and mounts only the model
read-only. The application has a writable, 128 MiB `/tmp` tmpfs (mode 1777), while its root stays
read-only; this allows non-root dependencies to create temporary files. It imports the pinned native
dependencies and performs CPU inference without network,
checking the 384-dimensional result. It also checks the backup image's `pg_dump` and `restic`
executables. It verifies the images' expected non-root defaults and effective identities. Output
gives image IDs, architecture reports and versions, but must be paired with
independent native-host proof and model provenance. Success does not test backup/restore, MCP,
packaging, platform reachability, image signatures, attestations or supply-chain release gates.

Complete Phase 1 only after native runs on both amd64 and arm64 and dependency/wheel inspection,
including the pinned torch and sentence-transformers distributions. Existing release scans,
signatures, provenance and SBOMs must continue to cover every published platform and manifest;
publication requires separate authorization. An unavailable architecture remains unverified.

The `CI` workflow's optional `native_images` dispatch input builds each image independently on
standard `ubuntu-24.04` and `ubuntu-24.04-arm` runners. It defaults to false and is only honored
for manual dispatches on a public repository. It checks runner, daemon and image
architecture and executes native dependencies with network disabled. Each image uses a separate
ephemeral runner to limit disk pressure; no model, artifact, cache or registry publication is
involved. The app check also mounts the bounded `/tmp` tmpfs. The opt-in dispatch runs only the native matrix, skipping the four ordinary CI jobs and
their artifact and cache operations; normal CI and dispatch without the input remain unchanged.
Native jobs are skipped if the event repository is private. Inspect the job logs and exact
commit before recording evidence; a skipped job is not evidence. The runner label and
architecture checks do not by themselves establish application or engine support, and the tensor
operation does not establish model inference. Provisioned model inference remains a separate gate.

The `CI` workflow also offers `multiarch_archives` as a separate, default-off manual input on a
public repository. Each of two standard x86 runners builds the actual application or backup image
as a **named**, local amd64/arm64 OCI archive with pinned BuildKit and SPDX scanner images. Build
inputs and the scanner may be fetched over the network; no image, cache, artifact or release is
published. To run the branch's workflow after it is available to the dispatcher:

```sh
gh workflow run ci.yml --ref BRANCH -f multiarch_archives=true
```

Select only one optional input. The check stages bounded archive metadata without extracting
arbitrary paths, resolves the nested candidate index and validates its child and BuildKit statement
digest/size links in a separate network namespace. Results establish only internal consistency
with the candidate digest taken from that archive; they do not authenticate an image, builder,
source, signature, or SBOM contents. GitHub's runner storage is limited: a disk, unsupported
producer format, scanner, or network-namespace failure is a failed rehearsal, not a passed gate.
The 8 MiB metadata blob cap is narrower than BuildKit's supported attestation size and must be
checked against real producer output. These builds may use emulation; native execution remains a
separate requirement. The release workflow and its actual producer are unchanged.

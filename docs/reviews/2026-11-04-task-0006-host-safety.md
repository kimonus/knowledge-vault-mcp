# TASK-0006 host-safety remediation evidence

Task TASK-0006 is pinned at coordination commit `7a24625213c54d4b340e8b8ea84506c5fa4ede37`,
blob `abdbc516d9da020b14751201d5f909e43d640fdd`. This report covers only the synthetic feasibility
prototype in Draft PR #73. It does not establish Bubblewrap isolation against kernel flaws or
authorize real-client use, provider calls, production activity, or a merge.

## Pinned image inspection

The repository fetcher verified the pinned OCI index and amd64 platform manifest and verified all
downloaded layer digests without using Docker. Inspecting the layer tar metadata found 10,226
entries: 752 symlinks and 2 hardlinks. No link entry exists at or below `opt`; the only `opt`
entry is a directory. The image inspection does not make path handling conditional on this image:
host-side mount setup now refuses symlinks in every mount destination, and synthetic adversarial
archives exercise absolute and relative link targets.

## Remediation

- `scripts/feasibility_support/unpack.py` no longer delegates to `tarfile.extractall`. It checks
  archive member paths, resolves OCI link paths inside the image root, preserves safe symlink and
  hardlink entries, and applies ordinary and opaque whiteouts before the layer's entries. Unsupported
  special files and escaping hardlinks are rejected.
- `scripts/feasibility_spike.py` prepares `/opt/python`, `/opt/site` and `/opt/repo` using
  directory descriptors with `O_NOFOLLOW`; a symlink at any component fails before touching its
  target. The staging command is isolated as a testable helper: `/rootfs` is its only writable
  host bind, layer and unpacker sources are read-only, and `/tmp` is a namespace tmpfs.
- Post-staging command preparation cleans the owner-checked workspace on failure only when the
  child-settled marker is absent. An unsettled workspace remains for manual recovery.
- The feasibility runbook documents the staging mounts, link policy and cleanup boundary.

## Focused evidence

The focused feasibility suite passed 85 tests, including synthetic absolute and relative symlink
and hardlink escape cases, path traversal rejection, OCI link and whiteout semantics, every host
mount destination, one-writable-host-bind verification, and deterministic settled/unsettled setup
failure cases. Ruff format/check and Pyright passed. Full target CI on the resulting PR head is
recorded in the coordination execution result when available.

No local Docker daemon, production services, provider, model, inference or real client was used.
This work does not add an uncompressed image-size quota; the runner still relies on the pinned
layer set and its existing compressed-size cap, so host disk exhaustion remains an operational
limitation of this prototype.

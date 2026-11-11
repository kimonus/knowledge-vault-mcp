# Secret-file configuration

The shared settings adapter accepts these optional file-path settings in the API, worker, watchdog,
and Alembic migration process:

| File-path setting | Alternative direct setting | File content |
| --- | --- | --- |
| `KNOWLEDGE_VAULT_DATABASE_URL_FILE` | `KNOWLEDGE_VAULT_DATABASE_URL` | Database URL |
| `KNOWLEDGE_VAULT_TOKEN_PEPPER_FILE` | `KNOWLEDGE_VAULT_TOKEN_PEPPER` | HMAC pepper |
| `KNOWLEDGE_VAULT_BOOTSTRAP_TOKENS_FILE` | `KNOWLEDGE_VAULT_BOOTSTRAP_TOKENS` | Bootstrap token-record JSON |

Specify a direct value or its file-path setting, never both. Explicit empty direct values also count
as present. Each file must be nonempty UTF-8 without NUL bytes and at most 128 KiB. Contents are
read exactly: whitespace and trailing newlines are not stripped. In particular, changing even a
trailing newline in the pepper changes its HMAC and invalidates existing token digests. Generate
files with an operator-controlled secret store and preserve the intended bytes; never put values in
command arguments, checked-in files, logs, screenshots, or Compose interpolation.

The final path component must be a regular file, not a symlink, FIFO, directory or device. Parent
path components may contain symlinks and must be trusted by the operator. Restrict file and parent
directory access, including against other host users and containers. The application runs as UID
10001; verify that the *actual* mapped identity can read each mounted file. Docker Compose may ignore
requested `uid`, `gid` and `mode` for file-backed secrets, and rootless Podman user-namespace mapping
can differ. Check the actual engine and mount before use; do not use recursive `:U` or make existing
private files world-readable. The standalone Compose deployment and these engine-specific workflows
are not yet implemented or verified.

Settings are cached within an application process. Provision a complete new secret file by an
operator-controlled atomic replacement and restart affected processes to use it. For a file bind
mount, replacing the host file can leave the container bound to the previous inode; recreate the
container when required by the engine and verify the new mount. Token rotation must preserve the
existing overlapping old/new digest procedure, and changing a pepper requires coordinated token
digest regeneration. Do not log or print settings objects via `model_dump()`; unlike repr, it
includes secret values. Follow the existing authentication and deployment runbooks for rotation.

# Feasibility Python runtime relocation — offline investigation

Status: self-reviewed local correction, not a successful live verification or independent approval.

The actual [run 42001222740](https://github.com/kimonus/knowledge-vault-mcp/actions/runs/42001222740)
used source `14f9517e51e5221a00ae53833cf03360298f0e7c`. Its bounded startup record showed
`stage_entered=alembic`, `last_completed=pg_start`, `command_failed`, exit 127 and premature
EOF before readiness. The server settled and owned cleanup completed. No second actual smoke
was run. Raw loader/server stderr was intentionally discarded, so the exact missing library,
interpreter or other underlying loader error is not known.

The hosted interpreter was `/opt/hostedtoolcache/Python/3.12.16/x64/bin/python3.12`, and the
host setup configured its `lib` directory in LD_LIBRARY_PATH. The harness relocates that runtime
read-only to `/opt/python`, clears the inherited environment and previously supplied no mapped
library search path. The local uv-managed interpreter has an origin-relative RPATH and no
libpython dependency in its ELF NEEDED entries; it is not the hosted interpreter, so its
successful execution cannot validate the hosted binary's relocation behavior.

## Evidence and correction

An offline C fixture links an executable to a unique private shared library with an absolute
build-time runtime path. It exits 0 at the original location, exits 127 after the runtime is
relocated, and exits 0 with the relocated library directory supplied. This establishes the
failure mechanism without an OCI image, namespace, database, server or network request; it does
not reconstruct the exact hosted Python binary or prove the live failure's missing library.

The server now invokes Python through a helper that sets only the fixed
`LD_LIBRARY_PATH=/opt/python/lib` for that child process. Both Alembic and server startup use
the helper. The shell does not export this path globally: PostgreSQL commands must continue to
use the image's own libraries, rather than possibly loading the Python bundle's OpenSSL or
other libraries. The separate Python-only mock client receives the same fixed path through
Bubblewrap's explicit environment allowlist.

The correction forwards no host loader environment, adds no mount or writable bind, and retains
the runtime's existing read-only bind. `--clearenv`, namespace separation, authentication,
ownership/mode checks, finite startup record format, readiness line and cleanup behavior remain.

Regression tests execute the actual server helper against the relocated ELF fixture with both
an absent and a hostile inherited library path. They prove successful child loading and an
unchanged caller environment. Command-construction tests verify the client's fixed mapped path,
the absence of a global server loader override, retained clearenv and read-only runtime mapping.
Existing shell stage/failure fixtures still exercise only command doubles, with no real database.

The ELF regression needs an already installed C compiler and reports a skip if none is present.
It downloads nothing and compiles only bounded, constant fixture code in owned temporary paths.

Offline results in the source-only worktree, using existing Python 3.12.12 dependencies:

- Full feasibility suite under outer umask 077: **173 passed in 2.20s** (old source 170 plus
  three loader/mount cases). The C compiler was available; no loader regression was skipped.
- Safety and diagnostics tests under outer umask 022: **95 passed in 1.16s**.
- Repository-wide Ruff lint/format: passed, **204 files** formatted; `sh -n` passed.
- Existing Pyright public JS entry point with the existing Node executable and locked venv:
  **0 errors, 0 warnings**. A first invocation of its internal bundle lacked the entry point's
  typeshed initialization; using the supported index resolved those tooling-only errors without
  changing code, type settings or downloading anything.
- `uv lock --check --offline` with Python downloads disabled: passed, **127 packages**.

These are source-only offline measurements. Application/database, image, hosted runner and E2E
checks were not run. No application-wide coverage or hosted-runtime success is inferred.

## Limits and next verification

This change addresses a verified environment omission and an offline-reproduced failure class.
The actual hosted runtime may have other dependencies or fail for a different reason. A future
live verification must pin this new source and review the exact diff; the earlier smoke used the
old source. Its results must not be presented as proof this correction worked. No migration,
gateway/client authorization, usage-row or answer-quality success is established here.

The source-only branch starts from the previously instrumented source, preserving separation
from temporary orchestration. A future orchestrator must update its source SHA and exact source
allowlist for the safety tests, changelog and this review. It must also fetch enough source history
to retain the original baseline after the new source commit; full history avoids another missing
ancestor failure. No current failed PR or historical result is modified by this investigation.

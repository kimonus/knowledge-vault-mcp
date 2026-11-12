# Fresh-target restore design (proposed)

Restore remains disabled. This design assumes a trusted single controller on an exclusively
controlled engine; it cannot defend against another administrator with equivalent engine access.
Use a disposable hosted CI runner for synthetic tests. Do not infer isolation from a Compose label
or an empty database alone, and do not run this workflow on the shared homelab engine.

1. Pin and repeatedly verify engine identity. Generate an unpredictable restore identifier and
   atomically create a private state directory and new empty data and staging directories under a
   trusted parent. Refuse any existing destination, symlink, unexpected mount, or collision. Keep
   partial results on failure; every retry requires a new destination.
2. Provision a new PostgreSQL instance without accepting an arbitrary existing database URL,
   volume, Compose project, host path or database target. Named volume creation is idempotent and
   is not proof of newness. Prefer an exclusively created bind directory when the engine can prove
   it sees that exact directory and its mapped PostgreSQL user can write it; VM and remote engines
   need separate evidence. A brand-new dedicated engine is an alternative. Never recursively
   change ownership, use `:U`, or adopt existing resources.
3. Start only the target database on a new private network with no published ports. Inspect its
   immutable image, exact container and mount identities, user, network membership, and database
   identity. Serialize the controller; refuse unexpected members, mappings or state. Do not start
   application writers or migrations. These checks still assume nobody else controls the engine.
4. Select a trusted authenticated snapshot and restore it to exclusive staging. A dump can execute
   SQL; treat it as trusted code and isolate it accordingly. Restore only to the verified fresh
   target with `pg_restore --exit-on-error --no-owner --no-privileges`, never `--clean`, `DROP`,
   pruning, replacement, or automatic cleanup. Abort on any mismatch and leave partial state.
5. Verify the schema head before starting a new isolated application. Use authenticated TLS to
   check representative assertions, artifacts, provenance and temporal state. Record engine and
   image identities, snapshot ID, isolation checks and outcomes without secrets or stored content.

Separately protect and rehearse recovery of PostgreSQL roles, privileges, device credentials,
HMAC pepper, TLS keys, restic password and other operator configuration. A local test repository
does not establish independent durable backup. Recovery into an existing database, switching live
traffic, deployment, and any deletion remain outside this draft's authorization.

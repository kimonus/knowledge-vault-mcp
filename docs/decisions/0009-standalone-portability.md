# ADR 0009: Standalone portability boundary

- Status: Proposed; independent review and owner resolution of the local transport boundary pending
- Date: 2026-11-09

## Context

The [Phase 0 audit](../reviews/standalone-phase-0.md) separates existing behavior from unverified
standalone requirements. ADR 0005 and AGENTS.md require authenticated TLS for alternative
deployments. The roadmap calls for an acceptable local HTTP trust boundary; interpreting that as
plaintext host-loopback HTTP would require a decision, since loopback scope alone does not
authorize an exception to that requirement. No deployment implementation or support claim follows
from this proposal.

## Proposed decision

Keep domain, service, persistence and adapter contracts independent of the orchestrator. Package
PostgreSQL 17 with pgvector, one API, a dedicated maintenance worker, and an explicit migration
operation as separately managed processes. Preserve the existing Kubernetes profile and its
security properties. No new datastore, broker, inference service, or server-side LLM is needed.

The migration is a one-shot, explicit Alembic upgrade using the application image, after database
readiness and before API/worker startup. Services must verify the shipped head revision and fail
closed if migration fails or is incomplete; ordinary application startup never migrates. Serialize
upgrades, back up first, and recover a failed or incompatible upgrade by restoring a verified
backup to an isolated instance. Schema downgrade is not assumed safe. Repeat startup must not
implicitly rerun a migration. The image's API ENTRYPOINT must be explicitly overridden for the
migration and worker processes; test the actual invoked commands on each engine. Exact engine
semantics require runtime tests.

Stop all API and worker writers before starting a migration, and do not restart them until the
explicit migration has succeeded. Standalone API requests and worker iterations check the exact
shipped singleton head, but this check and subsequent database operations are not atomic: it
cannot eliminate a claim or request race with a concurrent migration. A mismatch makes readiness
and non-health HTTP paths (including internal metrics) unavailable and pauses worker maintenance,
heartbeat and claims; the worker retries and leases expire if shutdown cannot release them safely.
The separate reembed and watchdog commands are not covered by this guard and must not be scheduled
in standalone until they have their own compatibility checks.

The worker must run staging expiration, artifact and confirmation-token cleanup, maintenance
purge, and successful heartbeat recording even when embeddings are disabled. Embedding jobs
remain model-scoped, leased and retryable; shutdown releases claims where possible and leases
expire otherwise. Current worker construction and loop do not yet establish a lexical-only mode;
implement and test that independently without introducing a second scheduler.

The current authorized external transport baseline is authenticated TLS, scoped bearer tokens,
strict Host and MCP Origin validation, and loopback-only host publication. A local TLS termination
boundary may forward HTTP internally only on a private container network and must prevent bypass.
The API origin independently validates device bearer credentials and enforces scopes; a gateway
cannot replace or bypass that guard. Test missing, invalid and wrong-scope credentials through the
gateway and attempts to reach the origin directly. Neither plain HTTP on host loopback
nor relaxation of TLS is approved by this ADR. If owner wishes plaintext local HTTP, obtain an
explicit architecture/security decision before implementation, including browser, local process,
VM forwarding, token exposure and DNS-rebinding threats. The API's shared metrics path must be
unpublished or filtered at the boundary; worker and database ports must not be published.

## Consequences and verification

Packaging and local identity/secret provisioning need design and implementation. No corporate Mac,
live homelab, production data, or release is involved without separate authorization. Native
architecture, OS/engine security, offline inference, recovery and authenticated end-to-end results
must be demonstrated on each claimed target. Failure or unavailable hardware remains unverified.
See the risk register and phase-specific acceptance criteria in the audit and roadmap.

# ADR 0010: Standalone portability boundary

- Status: Accepted for the local Docker implementation; other engine verification deferred
- Date: 2026-11-28

The owner directed completion on available Docker infrastructure, with Podman retained as the
same initiative and tested when access becomes available. Authenticated loopback TLS remains
mandatory. This decision authorizes implementation and isolated tests, not publication or live
deployment. The [Docker controller](../runbooks/docker-standalone.md) implements the lifecycle;
the earlier portability analysis below records the design boundary and original open questions.

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
upgrades, quiesce all API/worker writers before taking and verifying the pre-upgrade backup,
and recover a failed or incompatible upgrade by restoring a verified
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
The reembed command now checks this guard and is available through the Docker controller in full
mode. The separate Kubernetes watchdog command is not scheduled by the standalone profile.

The worker runs staging expiration, artifact and confirmation-token cleanup, maintenance
purge, and successful heartbeat recording even when embeddings are disabled. Embedding jobs
remain model-scoped, leased and retryable; shutdown releases claims where possible and leases
expire otherwise. Lexical-only mode retains maintenance without claiming embedding jobs or
constructing an embedding provider; no second scheduler is introduced.

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

TASK-0018 adds a minimal durable operation journal in the private instance config. In-progress
state and recovery metadata precede side effects. File fsync, atomic replacement and parent fsync
select immutable credential generations together with their volumes. Interrupted retirement moves
forward, never to old credentials; the correction-receipt HMAC key remains stable. A possible
partial migration under the recorded pre-exposure protocol requires fresh-target snapshot recovery,
not an inferred successful upgrade. R-019 adds a durable successful-migration marker BEFORE any
gateway exposure. From that marker onwards recovery preserves the candidate database and image
and proceeds only forwards, so post-migration acknowledged writes cannot be lost to a pre-upgrade
snapshot. Final TLS/worker acceptance remains required. An unmarked legacy interrupted upgrade
may have exposed its gateway; preserve its candidate database rather than recommending rollback.
The existing model manifest's digest supplies a content-bound standalone embedding identity within
the existing model string field; no schema change is needed. Concurrent schema checks coalesce
into one time-bounded query without retaining positive results for later requests. Lifecycle
acceptance requires a fresh successful worker heartbeat after its restart boundary.

Packaging and local identity/secret provisioning need design and implementation. No corporate Mac,
live homelab, production data, or release is involved without separate authorization. Native
architecture, OS/engine security, offline inference, recovery and authenticated end-to-end results
must be demonstrated on each claimed target. Failure or unavailable hardware remains unverified.
See the risk register and phase-specific acceptance criteria in the audit and roadmap.

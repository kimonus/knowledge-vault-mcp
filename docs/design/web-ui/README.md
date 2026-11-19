# Optional Knowledge Vault Web UI design

Design only, assessed on 2026-11-19 at `39b6af7c27950a7e57159b59cc7157ff8728f248`.
No application, authentication, schema, deployment or stored knowledge changed.

Read in order:

1. [Repository assessment](assessment.md): verified services, endpoints, security, paging and gaps.
2. [Proposed ADR 0009](../../decisions/0009-optional-read-only-dashboard.md): architecture and alternatives.
3. [API/UI contract](api-ui-contract.md): bounded operations, routes/components, deterministic grouping.
4. [Deployment and security](deployment-security.md): browser login, Helm, risks and open decisions.
5. [Four-PR implementation plan](implementation-plan.md): dependencies, acceptance and verification.

Recommended sequence: read contracts/services → browser auth/isolation → Explorer/Portal →
packaging/Helm and authorized operational verification. Every stage keeps the dashboard disabled
by default. No Compose profile is assumed: none is tracked in this assessed checkout.

The ADR is proposed, not accepted. Operator configuration/approval is required before enabling
or exposing a future dashboard, not to read or review this design.

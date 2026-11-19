# Optional Knowledge Vault Web UI design

Design only, assessed on 2026-11-19 at `39b6af7c27950a7e57159b59cc7157ff8728f248`.
No application, authentication, schema, deployment or stored knowledge changed.

Read in order:

1. [Repository assessment](assessment.md): verified services, endpoints, security, paging and gaps.
2. [Accepted ADR 0009](../../decisions/0009-optional-read-only-dashboard.md): architecture and alternatives.
3. [API/UI contract](api-ui-contract.md): bounded operations, routes/components, deterministic grouping.
4. [Deployment and security](deployment-security.md): browser login, Helm, risks and open decisions.
5. [Four-PR implementation plan](implementation-plan.md): dependencies, acceptance and verification.
6. [PR #126 review disposition](review-response.md): each finding, resolution and remaining gates.

Recommended sequence: core read contracts/services/interface → independent Python BFF/browser
auth → Explorer/Portal and separate image → conditional Helm resources/operational verification.
The Dashboard Deployment and ClusterIP Service exist only when `dashboard.enabled=true`; false
renders zero dashboard resources. Core builds/runtime do not need dashboard assets/dependencies.
BFF has no DB credentials or embedding model. Ingress remains a separate private-by-default
opt-in. No Compose profile is assumed: none is tracked in this assessed checkout.

The [maintainer acceptance](https://github.com/kimonus/knowledge-vault-mcp/pull/126#issuecomment-7007172193)
approves staged implementation with the ADR's verification gates. Operator configuration/approval
is still required before enabling or exposing a future dashboard.

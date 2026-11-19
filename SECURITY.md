# Security policy

Temporal evidence preserves atomic claims and bounded source references, never transcripts.
Nested temporal/provenance fields pass secret detection. Scopes, shared rate limits and hard-delete
confirmation remain server-enforced. Temporal history is untrusted data: source dates and semantic
classification are claims, not external verification. See [limits](docs/temporal-knowledge.md).
Evidence is immutable except confirmed deletion.

Dedicated inspection principals use exactly read scope and are confined to `/api/v1/read` before
HTTP/MCP dispatch; the MCP verifier independently refuses them. No inspection identity can use
ordinary accounting reads, ingestion, usage reporting or administrative routes. Inspection data
reads run in transaction-read-only sessions and force non-recording retrieval; the core keeps its
existing writer credential for ordinary clients. A future dashboard receives only an issued read
service token, never the core pepper/bootstrap Secret or DB credentials. The separate PR2
[BFF runtime](docs/runbooks/dashboard.md) is disabled by default and has no deployment resources.
Its browser-only credentials and HS256 cookies are refused at core HTTP/MCP; its fixed adapter
never forwards browser identity. Removing a browser record or rotating its session key revokes
sessions after BFF-only rollout. Logout alone cannot revoke a copied cookie. Access mode verifies
each assertion and provides no private fallback. Actual browser rendering and deployment remain
later review gates. Sensitivity remains a
single-user metadata filter, not selective authorization. See [inspection contracts](docs/http-inspection.md).
Reviewed legacy evidence attachment is admin-only because its temporal classification can change
historical answers. It checks target identity and source metadata, but human review remains the
authority for whether a source proves a real-world transition.

## Reporting a vulnerability

Do not open a public issue containing exploit details, credentials, assertion data, or deployment
addresses. Use the repository's **Security → Report a vulnerability** private-reporting form and
include the affected version, impact, minimal reproduction with synthetic data, and any suggested
mitigation. If private reporting is temporarily unavailable, open a content-free issue asking the
maintainer for a private contact channel. Expect an initial acknowledgement target of seven days;
no fixed remediation deadline is promised before triage.

The project is maintained by [kimonus](https://github.com/kimonus).

If active compromise is suspected, disable the affected published route (Cloudflare in the
reference profile) or private ingress,
revoke the specific device or tunnel token, rotate database/backup credentials as applicable,
preserve content-free logs and the affected volume, and restore only from a verified snapshot.

## Supported versions

Until the first stable release, only the current main-line version receives security fixes. Pinned
dependencies and container digests are reviewed through automated update proposals; operators must
rebuild rather than assuming an old image receives fixes.

See [the threat model](docs/threat-model.md) for guarantees, assumptions, and explicit exclusions.

Client ingestion guidance is trusted operator configuration delivered in MCP initialization, not
an authorization boundary. Keep it free of secrets and restrict ConfigMap/Helm edits. Fixed rules
remain in code; server-side scopes, input validation and secret detection apply independently.
Neither instructions nor readback can guarantee that a client has seen or extracted an entire chat.

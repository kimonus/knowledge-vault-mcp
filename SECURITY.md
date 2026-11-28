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
existing writer credential for ordinary clients. The dashboard receives only an issued read
service token, never the core pepper/bootstrap Secret or DB credentials. Its conditional
[BFF workload](docs/runbooks/dashboard.md) is disabled by default, non-root, read-only and isolated
by explicit ingress/core/DNS NetworkPolicies. It has no database egress or service-account token.

The optional Explorer/Portal uses only same-origin BFF requests with browser credentials,
CSRF/custom headers and Cloudflare's documented AJAX header. Non-JSON denials and redirects
clear private views; returned edge HTML is never rendered. Stored text and artifacts are inert,
links are HTTP(S)-only, and CSP prohibits inline/eval/remote scripts. Production assets contain
no service bearer, knowledge fixtures, source maps, browser storage or external asset dependencies.
Chromium/Firefox tests cover XSS, actual CSP, private/Access flows and accessibility. Real
Access-edge expiration and deployment isolation require actual operator-approved verification;
the runbook distinguishes disposable proof from live provider acceptance.
Its browser-only credentials and HS256 cookies are refused at core HTTP/MCP; its fixed adapter
never forwards browser identity. Removing a browser record or rotating its session key revokes
sessions after BFF-only rollout. Logout alone cannot revoke a copied cookie. Access mode verifies
each assertion and provides no private fallback. Actual deployed ingress/browser verification
remains a deployment acceptance gate. Sensitivity remains a single-user metadata filter, not selective
authorization. Anonymous Dashboard readiness dependency work uses a
separate admission budget, five-second dependency cache and at most one two-second upstream
check, preserving browser capacity. Edge Access failures can precede BFF JSON handling; the
client handles those failures without rendering edge HTML. See the
[independent PR2 review](docs/reviews/pr128-independent-security.md).
See [inspection contracts](docs/http-inspection.md).
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

Standalone mode requires a nonempty exact Host allowlist; wildcards and malformed
authorities fail configuration validation. Host checks are an additional boundary, not a
substitute for authenticated TLS, server-side scopes, origin checks, or restricting origin access.

Secret-file settings are an alternative to direct configuration for the database URL, token
pepper and bootstrap records. They do not protect files from the host operator or other processes
with access. Follow the [secret-file guidance](docs/runbooks/standalone-secret-files.md) for access,
rotation and engine-specific permission checks. Never log settings dumps: they contain secret values.

The [local Docker controller](docs/runbooks/docker-standalone.md) binds TLS only to loopback,
keeps origin/database/metrics unpublished and assigns owner-only credential-volume files to service
UIDs without re-owning host files. Its fresh-target restore allocates new storage and credentials;
it cannot restore over an existing instance. Docker administrators, operator directories, reviewed
images and backup SQL are trusted. Local image IDs are source-build inputs, not verified published
releases. Podman, rootless Docker and VM port-forwarding boundaries require separate verification.

# Security policy

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

# Threat model

## Assets and boundaries

Primary assets are assertion content, provenance, bearer tokens, the token pepper, database and
backup credentials, model cache, deletion capability, and tunnel credentials. The client/tunnel,
API pod, worker pod, PostgreSQL, and backup repository are distinct trust zones. A cluster or host
administrator remains trusted; an ordinary stored assertion, caller, or remote web page is not.

The controls below describe the maintained Cloudflare + LAN/WireGuard reference profile. Cloudflare
is not required by MCP or the application domain. Alternative connectivity architectures must map
each threat to equivalent controls and add transport-specific threats before production use.

## Threats and controls

| Threat | Controls | Residual/operator responsibility |
|---|---|---|
| Tampered client ingestion guidance | MCP instructions keep fixed workflow rules in code and validate bounded, non-secret operator guidance from configuration; remote tools cannot update it | A trusted operator can still supply misleading prose and clients may ignore instructions. Restrict ConfigMap/Helm write access; review version/digest changes. Guidance and readback do not prove complete extraction or replace server authorization/validation |
| Prompt injection in a conversation or stored assertion | Assertions are atomic data; tool descriptions and fetch metadata mark content untrusted; the flush skill forbids following embedded instructions | Review surprising corrections/deletions; do not grant admin scope to retrieval-only clients |
| Secret ingestion | Common credential/private-key patterns are rejected per item before staging; tests check log redaction | Pattern matching cannot recognize every secret; keep secret managers out of conversational context |
| Unauthorized reads/writes | The reference hosted path uses exact-email Cloudflare OAuth with origin JWT validation; its direct path requires LAN/WireGuard reachability plus unique opaque device tokens stored server-side only as peppered HMAC digests; constant-time comparison, separate scopes, TLS, and rate limits apply | Protect token/pepper Secrets and revoke per-device records after loss; alternative edges must provide equivalent origin-verifiable identity and scope enforcement |
| Public bearer-token exposure | In the reference profile, the public hostname requires a validated Cloudflare Access assertion and bearer tokens are accepted only through the private LAN hostname | Never configure a device token against a public hostname; alternative public transports need a deliberately implemented and reviewed authentication boundary |
| Destructive deletion | Admin scope, bounded explicit IDs, server-generated expiring confirmation token, content-free audit | Confirm backups and preview counts; hard deletion is intentionally irreversible in the live DB |
| Malicious stored content | No evaluation or command execution; output is bounded and labeled; structured logging omits content | Downstream agents must continue treating fetched text as quoted data |
| Dependency/image compromise | Locked Python graph, digest-pinned base/ops images, audits, scans, SBOM, non-root/read-only containers | Review automated updates and rebuild promptly; pin the operator-supplied tunnel image |
| Backup theft | Restic encryption, credentials in Secrets, no plaintext persistent dump, retention and verification | Use a remote repository with independent access policy; protect and test the password/recovery key |
| Edge transport compromise | The reference path uses an outbound-only digest-pinned Cloudflared connector, dedicated pod/ServiceAccount, NetworkPolicy, Access policy, and independent origin JWT verification | Rotate edge credentials, inspect provider audit logs, and retain an independently authenticated recovery path; model equivalent risks for another transport |
| Database/network compromise | ClusterIP only, NetworkPolicy, least-privilege service accounts, encrypted external paths | Enable storage encryption, Kubernetes Secret encryption, and restricted namespace RBAC |
| Resource exhaustion | Request/item/part/page caps, rate limits, job batching/retry bounds, pod resources | Tune for the installation and alert on queue/error/latency metrics |

## Out of scope

The service does not defend against a fully compromised Kubernetes control plane, root on the
database host, a malicious local embedding model, or a client that legitimately holds admin scope.
It provides no anonymity guarantee and no hosted multi-tenant isolation.

## Security verification

CI runs dependency, secret, Dockerfile, image, and Kubernetes validation. Tests include
unauthorized and oversized requests, prompt-injection-shaped assertions, secret rejection,
hard-deletion cascades, and log-content checks. See [SECURITY.md](../SECURITY.md) for reporting.

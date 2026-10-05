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
| Prompt injection in a conversation or stored assertion | Assertions are atomic data; tool descriptions and every retrieval result (`fetch`, `get_knowledge`, `search_knowledge`) mark content untrusted; the flush skill forbids following embedded instructions | `check_knowledge_candidates` returns excerpts of stored assertions for submitted texts; it needs the read scope, shares the read rate limit, is bounded to 50 candidates and 5 matches each, and neither stores nor logs the candidates. A flush now reads stored assertions to reconcile and may supersede one that the conversation contradicts; a planted assertion cannot instruct that, but a misled client can retire a true record. Superseded records are kept and remain searchable by status. Review surprising corrections/deletions; do not grant admin scope to retrieval-only clients |
| Tampered client ingestion guidance | MCP instructions keep fixed workflow rules in code and validate bounded, non-secret operator guidance from configuration; remote tools cannot update it | A trusted operator can still supply misleading prose and clients may ignore instructions. Restrict ConfigMap/Helm write access; review version/digest changes. Guidance and readback do not prove complete extraction or replace server authorization/validation |
| Secret ingestion | Credential, token, private-key, cookie, connection-string, and URL-credential shapes are rejected per item before staging, on both adapters, in content, topics, and source fields, after Unicode compatibility folding and removal of invisible characters | Pattern matching cannot recognize every secret: encoded values, values split by visible characters, and prose such as "the password is …" pass. Keep secret managers out of conversational context |
| Secret or hostile content inside an artifact | Artifacts are text only and are never executed, rendered, or served as a file: they are returned as a JSON string marked untrusted, in bounded pages. Every chunk is scanned together with the edges of its neighbours, the assembled text is scanned again on commit, and a match discards the whole upload including staged chunks; file name and description are scanned too | A file is far more likely than a sentence to carry a credential the patterns do not know (configuration dumps, `.env` fragments, exports). Do not upload such files; review what a client intends to store. A stored artifact can contain prompt-injection text like any assertion |
| Unauthorized reads/writes | The reference hosted path uses exact-email Cloudflare OAuth with origin JWT validation; its direct path requires LAN/WireGuard reachability plus unique opaque device tokens stored server-side only as peppered HMAC digests; constant-time comparison, separate scopes, TLS, and per-principal rate limits shared by MCP and HTTP apply | Protect token/pepper Secrets and revoke per-device records after loss; alternative edges must provide equivalent origin-verifiable identity and scope enforcement |
| Public bearer-token exposure | The origin refuses any request for a published hostname (`cloudflareAccess.publicHosts`) without a validated Access assertion; hostnames are canonicalized and a missing or repeated `Host` header is rejected. With `cloudflareAccess.privateHosts` set, bearer tokens are accepted only for those hostnames | Without `privateHosts`, a bearer token is accepted for every hostname that is not published, so set it. Never configure a device token against a public hostname; alternative public transports need a deliberately implemented and reviewed authentication boundary |
| Destructive deletion | Admin scope, bounded explicit IDs, server-generated expiring confirmation token, content-free audit | Confirm backups and preview counts; hard deletion is intentionally irreversible in the live DB |
| Malicious stored content | No evaluation or command execution; output is bounded and labeled; all logs are structured, omit content, and record exceptions without their messages; statement parameters are hidden from database errors | Downstream agents must continue treating fetched text as quoted data |
| Dependency/image compromise | Locked Python graph, digest-pinned base/ops images, audits, scans, SBOM, non-root/read-only containers | Review automated updates and rebuild promptly; pin the operator-supplied tunnel image |
| Backup theft | Restic encryption, credentials in Secrets, no plaintext persistent dump, tag-grouped retention and verification | Use a remote repository with independent access policy; protect and test the password/recovery key |
| Edge transport compromise | The reference path uses an outbound-only digest-pinned Cloudflared connector, dedicated pod/ServiceAccount, NetworkPolicy, Access policy, and independent origin JWT verification | Rotate edge credentials, inspect provider audit logs, and retain an independently authenticated recovery path; model equivalent risks for another transport |
| Database/network compromise | ClusterIP only, per-component NetworkPolicies (PostgreSQL reachable only from this release's API, worker, migration, and backup Pods), least-privilege service accounts, encrypted external paths | NetworkPolicy protects only on a CNI that enforces it; narrow `networkPolicy.apiIngressFrom`. Enable storage encryption, Kubernetes Secret encryption, and restricted namespace RBAC |
| Silent failure of backups or the worker | Heartbeats recorded on success; the watchdog fails visibly and can notify a webhook; messages are content-free; the webhook URL is held in a Secret | Enable the watchdog and a notification target; without one, failures are visible only as failed Jobs |
| Resource exhaustion | Request/item/part/page caps, artifact chunk, chunk-count and byte caps, expiry of abandoned uploads, purge of unreferenced artifacts, rate limits on both adapters, bounded metric labels, throttled signing-key refresh, job batching/retry bounds and claim leases, pod resources | Invalid bearer tokens are not throttled (tokens carry 256 bits of entropy). Tune for the installation and alert on queue/error/latency metrics |

## Out of scope

The service does not defend against a fully compromised Kubernetes control plane, root on the
database host, a malicious local embedding model, or a client that legitimately holds admin scope.
It provides no anonymity guarantee and no hosted multi-tenant isolation.

## Security verification

CI runs dependency, secret, Dockerfile, image, and Kubernetes validation. Tests exercise the real
HTTP and MCP transport stack and include unauthorized and oversized requests, Host-header
variants against the published hostname, foreign `Origin` headers, prompt-injection-shaped
assertions, secret rejection in every text field, hard-deletion cascades, and checks that neither
assertion text nor exception messages reach the logs. The backup smoke test runs the job with a
read-only root filesystem and asserts that retention prunes. See
[SECURITY.md](../SECURITY.md) for reporting and [reviews](reviews/) for independent reviews.

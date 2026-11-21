# Threat model

Temporal source metadata is untrusted, secret-checked input. Deterministic chronology and exact
property identity do not verify a caller's classification. Conflicts/uncertainty remain explicit;
transaction order and semantic similarity never establish authority. Source references are
bounded and never fetched. Read/write scopes and shared rate limits cover temporal tools. Evidence
cascades on confirmed assertion deletion; no source values enter metric labels/logs. See
[temporal semantics](temporal-knowledge.md), including lookup and legacy writer limits.
Reviewed attachment uses the stronger admin scope because changing historical eligibility can
change answers without changing a legacy assertion. Target equality, content hash and a legacy
successor link are checked in one transaction. Source relevance and real-world meaning still
require human review; neither a URL nor a legacy supersession alone proves them.

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
| Inspection service confused with ordinary read or privileged client | Independently configured exact-read inspection principal IDs are restricted before HTTP/MCP dispatch to explicit `/api/v1/read` operations; no browser cookies/Origin/Access assertion accepted; read-only sessions and forced maintenance retrieval prevent usage writes; cursor bindings/expiry, SQL limits/byte caps and shared rates bound requests | Compromised read token can read all single-user sensitivities; trusted core still has writer DB authority. No direct DB, privileged token or core pepper goes to a future BFF. Live scans are not snapshot exports; see [inspection contract](http-inspection.md) |
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
# Maintenance metadata and access accounting (0.5.0)

Every new metadata/context/link/alias/use write requires write scope; hard deletion still requires
admin and preview/confirmation. Revision checks and transaction locks prevent silent lost updates
and relationship cycles. References are bounded, secret-checked untrusted data, never fetched or
executed. Non-web provenance is a locator, not a transcript. Audit retains field names/hashes with
expiry and assertion-deletion cascades, not prior private values. Usage records contain aggregates,
not query text or a permanent click stream; counters cannot establish truth, raise confidence or
drive deletion. Read-only retrieval tools may maintain incidental operational aggregates. They
cannot change claim meaning, evidence or verification. Explicit actual-use reports require write.

## Dashboard browser boundary

The isolated, disabled-by-default [Dashboard BFF](runbooks/dashboard.md) holds only an issued
inspection bearer and dashboard-local browser auth material. It has no database credentials,
core pepper/records, model runtime or writer authority. The core rejects its designated exact-read
principal outside the fixed inspection API; browser credentials/cookies cannot authenticate core.
Fixed operation mapping, UUID parsing, schema/size validation, no redirects/retries, bounded
concurrency and fixed upstream Host prevent arbitrary proxy/SSRF and authority forwarding.

Cookie tampering/expiry, wrong audience and revoked credential records fail closed. Key rotation
or record removal requires BFF-only rollout; local logout cannot revoke a copied cookie.
Exact Host/Origin, Fetch Metadata, JSON/custom-header requirements and logout CSRF protect the
browser boundary. CSP/no-store/nosniff/no-referrer reduce stored-content and cache risks, but actual
inert UI rendering and browser enforcement are now covered in Chromium/Firefox. Optional Access validates each RS256
assertion and has no private fallback. BFF compromise exposes all retained sensitivity classes
through read authority; it does not imply selective secrecy or protect browser-visible data from
a compromised browser. Rate budgets are bounded process-local memory and reset on restart.

Dashboard resources are conditional and absent by default. The enabled workload uses non-root,
read-only execution, dropped capabilities, RuntimeDefault seccomp, limits/probes and no mounted
service-account token. Dedicated policies admit only explicit ingress peers, DNS and core API;
the database component allowlist excludes dashboard. Access adds only reviewed JWKS HTTPS ranges.
Policies require an enforcing CNI, and L3 policies cannot restrict HTTP paths or authenticate
JWKS hostnames. Core route/credential confinement and origin JWT checks remain mandatory.
Dashboard-only values/checksums do not enter core resources; external Secrets survive removal.
Real ingress/Access browser verification requires separate operator-authorized deployment;
simulated edge tests do not prove provider expiry. See the [acceptance procedure](runbooks/dashboard.md).

The frontend obtains public assets from named routes and private data only from same-origin BFF
JSON operations. It never receives the core service bearer or upstream configuration. Stored
HTML/Markdown, source fields and artifact chunks are rendered as text; links allow only HTTP(S)
without embedded credentials. No preview fetch, unsafe HTML sink, external asset, browser storage
or source map exists. CSP blocks inline scripts/styles and remote connections. Resource IDs and
encoded topic names can appear in browser history; filter text and credentials stay out of URLs.
Filters/cursors live in the authenticated component tree and are discarded on expiry/logout.
Logout discards that tree before any network wait, cancels in-flight reads and uses a separate
five-second CSRF-aware exchange. A generation guard prevents older session refreshes from
restoring the discarded tree. Authenticated session bootstrap obtains the validated public
page cap under the existing upstream concurrency/deadline budget; clients clamp page/history
requests, while unpaged reads and artifact character offsets retain their separate semantics.
The client aborts stale requests and treats edge redirects/non-JSON auth refusals as authentication
failure, never returned HTML. A top-level named document GET may arrive cross-site for Access
return navigation; this exemption never covers API or script reads and never bypasses Origin,
Host or Access assertion checks. Public private-mode assets contain no knowledge or credentials.
Compromised operator browser/extensions, copied cookies until expiry, process-local rate limits,
all-sensitivity visibility and live-scan membership changes remain accepted limitations.

The [independent PR2 review](reviews/pr128-independent-security.md) found anonymous readiness
amplification/shared-slot starvation. Readiness now has a separate fixed admission budget,
five-second result cache and single-check admission with a two-second deadline; a probe cannot
occupy all four upstream slots. Future routing must still keep probe paths internal. Access can
answer session expiry before origin, so origin JSON/no-redirect guarantees do not cover edge
responses. PR3 handles provider AJAX/non-JSON failures; PR4 verifies actual edge expiry behavior.

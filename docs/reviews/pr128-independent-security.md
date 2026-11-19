# PR #128 independent adversarial security review

Reviewer: `/root/independent_security_review`, a fresh-context agent that did not implement PR2.
Date: 2026-11-19. Initial reviewed head: `2e22e67063a1e7b124fdcc84c45528f0dbc2666d`.
Implementation baseline: PR1 merge `71da6f82130b3348531d23589402853e4b882500`.
Review checkout: isolated `/tmp/knowledge-vault-pr2-review`, branch
`review/dashboard-pr2-adversarial`. The implementation agent owns application remediation.

## Initial verdict

**Request changes.** R1 is a confirmed Medium availability issue and blocks merge until fixed
and independently retested. R2 is a Low documentation/client-contract correction. No confirmed
read/write authority escalation, session forgery, browser credential disclosure or SSRF was found.
These are bounded review conclusions, not a proof that the implementation has no other defects.
No deployment, public route, live identity, production credential or merge was authorized/exercised.

## Method and trust boundaries

Read the complete PR2 source changes and relevant tests/configuration/package/CI/documentation
changes, ADR 0009, the web UI security/API/implementation plan, original implementation brief, and
the existing core token, inspection, HTTP/MCP and Access authentication boundaries. Earlier PR1
code was reviewed where it supplies the service authority boundary; this is not a new full review
of every persistence/query implementation in that earlier PR.

| Boundary | Attacker / assets | Examined controls and result |
| --- | --- | --- |
| Browser → BFF | Cross-site/sibling-site page; unauthenticated network caller; copied/tampered session; malicious retained text. Assets: all retained knowledge, login material, session identity. | Exact valid Host; exact POST Origin; Fetch Metadata; custom JSON header; no CORS; logout CSRF; HttpOnly/Secure/Strict host-only cookie; HS256 allowlist, audience, absolute expiry and active-record reference. Original tests reject malformed paths/headers, privilege-bearing records, credential reuse, signature/audience/expiry attacks and record/key revocation. Copied-cookie replay after local logout is explicitly accepted. R1 concerns anonymous probe work, not knowledge access. |
| Access/proxy → BFF | Forged assertion or forwarding headers; expired/wrong-app identity; edge behavior differing from origin behavior. Assets: read identity and availability. | RS256 signature, issuer/audience/required claim checks, exact configured email, no private fallback; forwarding headers do not select identity/host/rate address; entrypoint disables proxy-header trust. JWKS has timeout/size/cache/refresh limits. Official provider behavior supports assertion-header validation; edge expiry behavior requires R2 clarification. Local RSA/fake JWKS tests cannot certify a real Access application. |
| BFF → core | Browser-selected route/method/UUID/query or upstream response; leaked/misconfigured service credential. Assets: core writer authority, accounting, DB/model isolation, service bearer. | Fixed origin/Host and read prefix, allowlisted methods/paths, UUID reconstruction, schema/size/parameter checks, no forwarded browser headers, no redirects/retries/environment proxy; safe status mapping and service-token echo refusal. Core designated inspection credentials require exact READ and cannot access ordinary HTTP/MCP routes. Capability check precedes each data operation. No BFF DB engine, writer key or model import. R1 shares availability capacity with probes. |
| Package/config/logs | Accidental privileged material/config; malformed data/errors; source link/package differences. Assets: separate secrets, raw knowledge and safe runtime. | Dashboard manifest/lock are independent, source links build into actual helper files, config reads explicit secret-file references rather than .env/core configuration, sensitive dataclass fields suppress repr, entrypoint disables access/URL logs, mapped errors omit upstream content. Built and inspected the wheel. No SPA/assets/image/chart exists in PR2, so no actual rendering/deployment claim is made. |

## R1 — Anonymous readiness requests bypass budgets and consume data capacity

Severity: **Medium**. Status: confirmed; merge-blocking pending remediation and independent retest.

Locations at the initial head:

- `dashboard/src/knowledge_vault_dashboard/app.py:128`: health returns before Host/auth/rates.
- `dashboard/src/knowledge_vault_dashboard/app.py:269`: readiness calls the core on every request.
- `dashboard/src/knowledge_vault_dashboard/upstream.py:128`: readiness acquires `self.slots`.
- `dashboard/src/knowledge_vault_dashboard/upstream.py:152`: user reads acquire the same four slots.

Prerequisite: the attacker can reach the BFF probe path, for example from an admitted private
network or a future route that forwards it. PR2 has no deployed routing; this is not evidence of
an existing public exposure. ADR 0009 intentionally exempts cluster probes from browser Host/auth,
but that does not require an unlimited authenticated upstream call for every probe.

Attack: send repeated unauthenticated `GET /health/ready`, with any Host. These do not consume
the documented 600/minute global budget or require a browser credential. Each creates a
service-authenticated core capabilities request. When core responses stall, four probes occupy
all shared outbound slots; authenticated reads then queue behind anonymous work until the
60-second absolute budget, and further probes can accumulate waiting tasks. Even healthy calls
can exhaust the core service-principal rate budget.

Independent executable evidence against the exact original head:

1. Sent 605 sequential requests with `Host: attacker.invalid`, no cookie/assertion. All returned
   204 and a counting fake upstream observed **605** authenticated capabilities calls.
2. Held four fake-core capability responses behind an event. Four unauthenticated readiness
   requests entered upstream; a valid browser cookie requesting `topic-aliases` could not enter
   upstream during a 50 ms observation window. It was queued on the shared semaphore. Tasks were
   then cancelled/released cleanly; this experiment does not wait for the production 60s timeout.

These experiments were written and run by the independent reviewer, not inferred from the
implementation author's tests. The fake upstream isolates scheduling/rate behavior; it does not
measure real core latency, load capacity or public network exploitability.

Remediation: preserve unauthenticated cluster probe semantics while bounding probe admission,
coalescing/caching dependency checks, refusing concurrent probe work promptly, and ensuring at
most a small, short-lived part of upstream capacity can be occupied by probes. A separate probe
budget must not be bypassable by Host/client-header churn or consume all user slots. Future
ingress must expose only approved dashboard paths and keep cluster probe paths internal.
Retest both the healthy burst and stalled-core/authenticated-read scenarios.

## R2 — Origin JSON errors do not describe Access edge expiry behavior

Severity: **Low**. Status: confirmed documentation/client-contract gap; no auth bypass.
Location: `docs/runbooks/dashboard.md:89`, “No API error redirects to a login page.”

Prerequisite: future Access deployment with an expired provider browser session. Access can
answer before the request reaches this BFF. The BFF's safe JSON 401 therefore cannot guarantee
the format/redirect behavior of that edge response. Cloudflare documents an AJAX-specific
`X-Requested-With: XMLHttpRequest` header for receiving 401 on expired sessions. Its error body
is still not the BFF ProblemDetail contract. No live Access request was executed in this review.

Evidence: [Cloudflare session management, AJAX](https://developers.cloudflare.com/cloudflare-one/access-controls/access-settings/session-management/)
and [Cloudflare 401 behavior](https://developers.cloudflare.com/support/troubleshooting/http-status-codes/4xx-client-error/error-401/).
The local fake-verifier tests exercise only requests that already reach the origin.

Remediation: scope the JSON/no-redirect claim to BFF-originated responses. Specify that PR3 fetch
requests include same-origin credentials and the documented AJAX header, and handle non-JSON
401, edge denial/redirect/network failures as authentication failure without rendering response
HTML as trusted app content. Add a real expired-Access-session check to PR4's browser gate.

## Independent execution evidence

All commands below used reviewed source via explicit PYTHONPATH, avoiding the implementation
worktree's editable/non-editable installed package. Existing locked interpreters supplied only
dependencies. No source fixes were made by this reviewer.

- Dashboard original tests: `PYTHONPATH=<review>/dashboard/src <dashboard-python> -m pytest
  dashboard/tests -q`: **148 passed**, before adding reviewer experiments.
- Independent experiment tests: **3 passed**, comprising the 605-call burst, stalled-probe
  scheduling test, and built-wheel helper-content check. The first two assert the vulnerable
  baseline behavior intentionally; they are historical reproductions, not fixed-head gates.
- Core inspection/Access regression tests: `PYTHONPATH=<review>/src <core-python> -m pytest
  tests/contract/test_inspection_security.py tests/unit/test_inspection.py
  tests/unit/test_cloudflare_auth.py -q`: **30 passed**.
- Independent `uv 0.13.7 build --project dashboard --offline`: sdist and wheel built successfully.
  Inspected wheel auth modules for regular executable helper content and absence of core imports.
- `git diff --check`: passed for inspected tracked content. Reviewer documentation is added
  separately. Pytest emitted dependency deprecation warnings; no test failure was hidden.
- One initial core test command named nonexistent `test_cloudflare_access.py`, so collected
  nothing; corrected to `test_cloudflare_auth.py` and reran the complete intended set above.

Official provider protocol assumptions were checked independently:
[Cloudflare JWT validation](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/authorization-cookie/validating-json/)
describes the origin assertion header and application audience/signature validation. Session,
cookie, replay, malformed JWT and wrong-algorithm behavior were also exercised through the
installed PyJWT implementation by the independently rerun original tests.

The real disposable-pgvector BFF/core compatibility gate needs Docker/socket permission and was
requested from the coordinator with explicit reviewed-source paths. Its outcome must be recorded
separately from the independent local tests; no result is assumed here. Full core suite, fresh
dependency audit, image/chart/live browser/deployed-client proof are not claimed by this review.

## Accepted limitations and remaining gates

The single-user all-sensitivity view, live pagination, one-process rate state, coarse revocation
after rollout, copied-cookie replay until expiry/revocation, and aggregate core service identity
are architecture choices rather than new findings. A compromised BFF or browser can read the
permitted knowledge; no selective secrecy or stronger session-revocation claim is made.

No UI exists here: stored HTML is returned as JSON, but actual inert text/link rendering and CSP
enforcement remain PR3 browser tests. PR4 must verify private TLS, ingress path/Host preservation,
probe isolation, NetworkPolicies and actual Access expiry behavior using an authenticated deployed
client. Origin ASGI tests and official documentation cannot substitute for that deployment proof.

Implementation agent `/root` owns R1/R2 fixes. This reviewer must inspect the resulting exact
head, rerun the baseline attacks as fixed-behavior tests, rerun affected regression tests, and
record final finding dispositions before an independent approval verdict.

Next: implementation agent `/root` publishes this initial assessment and remediates R1/R2;
independent reviewer verifies the resulting exact head.

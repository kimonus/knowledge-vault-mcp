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
| Browser → BFF | Cross-site/sibling-site page; unauthenticated network caller; copied/tampered session; malicious retained text. Assets: all retained knowledge, login material, session identity. | Exact valid Host; exact POST Origin; Fetch Metadata; custom JSON header; no CORS; logout CSRF; HttpOnly/Secure/Strict host-only cookie; HS256 allowlist, audience, absolute expiry and active-record reference. Original tests reject malformed paths/headers, privilege-bearing records and reused credentials. They also test invalid signatures, wrong audiences, absolute expiry and record/key revocation. Copied-cookie replay after local logout is explicitly accepted. R1 concerns anonymous probe work, not knowledge access. |
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


## Final independent verification — 2026-11-19

Reviewed remediation head: **`89546bccff9aed4ec92d6fbe31339dfa97a66aca`**.
The initial assessment above is retained as the historical original-head verdict. It was
[published on PR #128](https://github.com/kimonus/knowledge-vault-mcp/pull/128#issuecomment-7015524054)
and committed as `57df4a6` before application remediation. The same independent reviewer
inspected the complete `57df4a6..89546bc` diff, including implementation, tests and synchronized
security/client/deployment documentation. The reviewer did not implement these fixes.

**Final verdict: APPROVE for PR2's implemented security scope.** Both findings are resolved;
there is no remaining confirmed merge-blocking security finding from this review. This verdict
does not authorize merge/deployment and does not certify future UI rendering or live Access/TLS.

| Finding | Disposition | Independently verified behavior |
| --- | --- | --- |
| R1 Medium | Resolved at `89546bc` | Readiness caches safe success/failure for five seconds, admits at most one uncached dependency check, charges a fixed separate 60/minute work budget, and bounds shared-slot occupancy to two seconds including queueing. Other probes fail promptly rather than taking more slots. Cached state never authorizes data. |
| R2 Low | Resolved at `89546bc` | Runbook and API contract scope JSON/no-redirect guarantees to origin; specify provider AJAX header and same-origin credentials; require safe handling of non-JSON denial/redirect/network failures. PR3/PR4 explicitly retain actual browser/edge expiry gates. |

Independent adversarial retests used fresh reviewer-written fixed-expectation probes in
`/tmp/knowledge-vault-pr2-review/dashboard/tests/test_independent_fixed.py`, with explicit
`PYTHONPATH=/tmp/knowledge-vault-pr2-remediation/dashboard/src`. The candidate checkout was
confirmed clean and at the exact SHA above. These tests supplement rather than rely solely on
the implementation agent's new regression tests:

1. **Healthy burst:** 605 anonymous probes with varying wrong Host and spoofed forwarding
   addresses all returned cached 204, producing **one** core call. A valid browser read then
   returned 200 and made its own fresh capabilities + data calls. The old 605-call amplification
   and shared browser-budget exhaustion do not reproduce.
2. **Stalled dependency:** one anonymous probe held a fake-core response. Three additional
   probes returned 503 promptly, generated no additional core work, and a valid browser read
   returned 200 within a 0.5s deadline before releasing that probe. The old four-slot starvation
   does not reproduce.
3. **Real deadline and failure cache:** without monkeypatching timeout constants, a stalled
   dependency failed at approximately two seconds with safe 504. An immediate second probe
   reused the failure without another call. All four semaphore slots were then acquirable.
4. **Authority isolation:** after caching healthy readiness, the fake core began returning 403.
   The cached readiness remained available, while a data read performed a fresh authority check
   and failed safely with 503. Readiness cache cannot become an authentication bypass.

Command: `PYTHONPATH=<candidate>/dashboard/src <dashboard-python> -m pytest
<review>/dashboard/tests/test_independent_fixed.py -q -s`: **4 passed in 2.19s**.
The reviewer independently reran the entire candidate dashboard suite with the same explicit
source selection: **154 passed in 0.66s**. This includes the new cache expiry/recovery,
cancellation and interrupted-work admission regressions as well as the original authentication,
scope, path, CSRF, serialization and upstream-error checks. Only pytest dependency deprecation
warnings were emitted.

The reviewer independently exported exact candidate Git objects with `git archive`, extracted
them into `/tmp/kv-pr128-candidate`, and ran `uv 0.13.7 build --project dashboard --offline`:
**source distribution and wheel both built successfully**. This checks inclusion of the new
`errors.py`/`limits.py` modules from an isolated tracked-source tree.

Separately attributed coordinator execution: the reviewer-selected original-source real
pgvector compatibility command completed **1 passed, 1 deprecation warning in 2.33s**, exit 0,
with Docker/socket escalation. The coordinator also reported the candidate full core suite
**694 passed** and unchanged Helm profiles passing. Those are coordinator execution reports,
not claimed as the reviewer's independent local runs, and do not replace the independent
adversarial results above.

Residual limits remain those accepted above: process-local single-replica budgets, five-second
readiness staleness, coarse session revocation, copied-cookie replay until expiry/revocation,
all-sensitivity single-user access and future ingress probe isolation. There is no new live
browser/Access deployment proof. PR3 must test inert rendering and real browser auth handling;
PR4 must exercise authorized deployed paths, probe isolation and true provider-session expiry.

Next: coordinator `/root` integrates and publishes this final verification record, verifies that
any final documentation-only commit preserves the reviewed application tree, and reports CI.

# PR #130 independent browser and packaging review

Reviewer: fresh-context Codex reviewer, separate from the implementation agent and using an
exclusive worktree. Review requested by [maintainer comment 7023515262](https://github.com/kimonus/knowledge-vault-mcp/pull/130#issuecomment-7023515262).
Initial subject: `dea435a8739a45bffce053cd9b09544fcd146d0a`; PR base:
`d329d4c0569bb9ae13be666478aa890f4c114e61`.

**Verdict: PASS for corrected runtime `c790ce3c8871c30a5db1149cd06b7c7786aa5319`.**
All three reproduced medium findings and the additional low finding are resolved and independently retested; no unresolved
blocking finding remains in PR3's component scope. The original head required changes.
The final publication commit will add review artifacts and CI wiring; its exact SHA and final
browser/CI binding must be recorded in the PR comment after that commit is available.
This is an independent agent verdict, not a same-account GitHub approving review. No merge, live
deployment, live credential, DNS, tunnel or Access-policy operation is part of this review.

## Scope and evidence standard

Read repository instructions, the original implementation brief, accepted ADR 0009 and staged
implementation plan, PR diff, actual frontend source, the BFF route/security/session/upstream
implementation, and core inspection authentication/adapter/browse/facade code. Generated contract
and fixture data are inspected as generated artifacts, not treated as independent test oracles.
The implementation agent's verification report is not evidence for this verdict.

The reviewer authored `web/tests/browser/independent-review.spec.ts`,
`web/tests/browser/independent/real-core.spec.ts`, the small-capability fixture/regression, and
separate Playwright configurations. Tests exercise both Chromium and Firefox over loopback TLS.
The execution agent ran the reviewer's exact commands because browser/Docker operations need
host permission; the reviewer inspected raw logs, failure traces and screenshots. This is
independent test design and interpretation, not independent physical infrastructure.

Browser payload mutation tests deliberately strengthen the untrusted-data adversary after
validated BFF JSON; actual retained malicious fixture content and artifact text also traverse
the real BFF, and the real-core suite uses disposable pgvector, migrations and actual core
services. Neither fixture uses operator knowledge, production identities or downloaded models.

## Explicit threat model

| Actor / entry | Asset / trust boundary | Required property and exercised adversary |
|---|---|---|
| Malicious stored assertion, topic, provenance, source URL or text artifact | Untrusted JSON entering authenticated React DOM | Text never becomes HTML/script; `javascript:`, data/file/vbscript, protocol-relative and credential-bearing URLs are not links; artifact media type does not activate a renderer |
| Stored-content attacker attempting network exfiltration | Browser CSP and document navigation | Inline scripts, remote fetch/frame and base rewriting blocked; no preview requests; HTTP(S) source links isolate opener/referrer |
| Anonymous/cross-site client | BFF document/static allowlist versus private API | Public private-mode shell/assets carry no knowledge; private reads require session; unknown/encoded/document/API/map paths fail closed; no core/MCP proxy |
| Expired Access edge session, tampered/private expired cookie, stalled or stale request | Browser authentication generation versus retained DOM/memory | Clear private tree, cancel stale reads, never render edge HTML; reject redirects and handle reauthentication; signout must clear locally before network completion |
| Network failure, capability change, pagination boundary | API projection versus readable complete evidence | Bounded errors/retry, advertised limits honored, every continuation reachable, all statuses retained, proposals/rejections never promoted to truth, resolver overflow never selects partial winner |
| Browser history, referrer, logs, browser persistence | Encoded topic resource names and knowledge/credentials | No query/search/temporal/credential URL parameters; no outbound referrer; no local/session storage, caches or service workers; runtime access/httpx URL logging disabled |
| Compromised frontend dependency/package or accidental source inclusion | Node build versus separate Python runtime | Locked dependencies; compiled public assets only, no maps/fixtures/secrets; runtime lacks Node/core/DB/model modules; non-root read-only execution |

## Confirmed findings

### R1 — Medium: stalled logout retains authenticated private content

Original location: `web/src/auth.tsx:109` through the logout `finally` block. Prerequisite:
an authenticated browser and a delayed/stalled logout exchange. Reproduction: sign in, intercept
`/dashboard/api/v1/session/logout` without completing it, click **Sign out**, inspect the DOM.
Both Chromium and Firefox still show all 50 private record cards after 1.2 seconds; they disappear
only after the response completes. There is no client deadline in the original implementation.
This is a local privacy/authentication-state defect, not anonymous server-side access or token
privilege escalation. Remedy: synchronously discard the private tree and abort reads at logout
start; perform a separately bounded logout exchange retaining only the required CSRF value;
prevent an older refresh from restoring authenticated state.

Evidence: independently authored stalled-exchange regression, raw original runs
`/tmp/pr130-independent-initial-browser.log` and
`/tmp/pr130-independent-initial-browser-v2.log`; both browser engines fail with expected 0,
received 50. **Disposition: resolved in `65eb15b`.** Private content now disappears before the
stalled exchange completes in both engines. A separate CSRF-aware exchange has a five-second
deadline; the authentication generation guard prevents stale refresh restoration. Re-login is
blocked until the exchange completes or times out.

### R2 — Medium: mobile detail header pushes Sign out outside the viewport

Original locations: `web/src/app.tsx:120` (header/breadcrumb/actions),
`web/src/styles.css:293` and mobile overrides around `:1097`. Prerequisite: mobile viewport
360 CSS pixels wide and an assertion detail route. Both engines measure document width 441px;
Sign out's right edge is 441.31px in Chromium and 441.42px in Firefox. The screenshot shows the
action entirely outside the initial viewport. The breadcrumb/navigation/action flex row cannot
shrink or wrap sufficiently. Axe reports no violation, so an explicit reflow assertion is needed.
This is a reflow/accessibility defect affecting a privacy control, not a data-exfiltration exploit.
Remedy: wrap/shrink the breadcrumb/header while preserving operable navigation and Sign out.

Evidence: mobile detail regression and screenshot/overflow geometry in
`/tmp/pr130-independent-initial-browser-v2.log`. **Disposition: resolved in `65eb15b`.**
Corrected reflow, keyboard route focus and WCAG checks pass in both engines at the stricter
320px viewport on detail and temporal pages.

### R3 — Medium: compatible small core page limits make the dashboard unusable

Original locations: `web/src/api/client.ts:3` (session lacks the advertised page bound),
`web/src/explorer.tsx:102`, Portal catalog/facet limits, detail child defaults and temporal
history/raw limits; `dashboard/src/knowledge_vault_dashboard/upstream.py:188` applies implicit
limit 50 even to unpaged operations. Core `max_page_size=10` is a valid setting; the core
advertises it and BFF capability validation explicitly accepts it. The frontend nevertheless
requests 50 records/catalog entries (20 resolver history entries). Both engines successfully
authenticate but show no records because the actual BFF refuses the request with 422. Explorer
has a smaller page-size workaround, but Portal has no size control, and unpaged reads are
incorrectly rejected by the BFF's implicit default check.

The reviewer-authored `independent_lowcap_server.py` changes advertised capability to 10;
the original baseline regression fails in Chromium and Firefox. Raw evidence:
`/tmp/pr130-independent-initial-lowcap.log`. Remedy: expose the validated bound to authenticated
clients, honor it for each pageable operation (including resolver history), and avoid applying
a page-size check to unpaged reads. **Disposition: resolved in `65eb15b`.** Both engines now
verify Explorer, Portal, unpaged detail, all 43 sources over ten-item pages, relations and temporal
resolution/raw evidence against the small-cap actual BFF fixture. This is a confirmed functional
compatibility defect, not an authority bypass.

### R4 — Low: artifact character ranges count UTF-16 units instead of server characters

Original location: `web/src/detail.tsx:483`. A retained text artifact containing one non-BMP
Unicode character, such as `🙂`, has server `total_chars=1` and offset 0. PostgreSQL/Python
character offsets count that character once, while JavaScript `content.length` counts two
UTF-16 code units. The original UI consequently displays **Characters 0–2 of 1**. The text itself
is intact and navigation follows server `next_offset`; this is a display-fidelity defect, not
XSS, text loss or an authorization issue.

The reviewer independently added a Chromium/Firefox browser regression using an otherwise valid
artifact response with that one-character body. Both original-runtime runs fail the expected
**Characters 0–1 of 1** assertion. Raw evidence:
`/tmp/pr130-independent-unicode-artifact.log`. Remedy: count Unicode code points for the label
while retaining server offsets and continuation. **Disposition: resolved in
`c790ce3c8871c30a5db1149cd06b7c7786aa5319`.** The reviewer checked the exact SHA and one-line
runtime diff: only the displayed end-character calculation changes to Unicode code-point count.
The otherwise unchanged implementation now passes the focused regression in both Chromium and
Firefox; raw result `/tmp/pr130-independent-unicode-fixed.log` (2 passed). The rebuilt source's
build result is `/tmp/pr130-unicode-reviewed-build.log`.

## Corrected-runtime verification and verdict evidence

The reviewer checked `git rev-parse HEAD` and inspected the complete remediation diff at
`65eb15b6e56965a8d1829ad0481d2c5236dea80f`. Browser assets were rebuilt from this worktree's source.
The following raw results were inspected independently:

| Verification | Result | Raw evidence |
|---|---|---|
| Reviewer-authored synthetic HTTPS suite, Chromium and Firefox | 24 passed | `/tmp/pr130-independent-corrected-browser-v2.log` |
| Reviewer-authored actual core/BFF/disposable pgvector flow, both engines | 2 passed | `/tmp/pr130-independent-corrected-core.log` |
| Reviewer-authored ten-item capability suite, both engines | 2 passed | `/tmp/pr130-independent-corrected-lowcap-v2.log` |
| Production wheel/image isolation, actual asset/header/route/auth responses | PASS | `/tmp/pr130-independent-corrected-image-inspect-v2.log` |
| Complete BFF Python suite against this worktree's source (`PYTHONPATH=src`) | 164 passed | Direct reviewer tool output |
| Frontend TypeScript, ESLint, Prettier; independent Python fixture Ruff; diff whitespace | PASS | Direct reviewer tool output |

The 24 synthetic runs include unsafe provenance schemes and stored-text/metadata injection,
actual CSP script/connect/frame/base enforcement, inert artifact text, anonymous/unknown route
refusals, absent topic-bearing referrers and empty browser storage, exhaustive 205-record traversal
across all four statuses, catalog count fidelity, 103 explicit relationships, stale response
cancellation, idle expiry and stalled logout, Access HTML success/401/403/redirect handling,
429 recovery, all unresolved resolver outcomes and raw evidence traversal through entry 1002,
plus mobile accessibility/reflow/keyboard focus. The actual-core runs additionally exercise
real provenance continuation, retained superseded evidence, rejected options, proposed plans,
temporal precision and direct link meaning through PostgreSQL and actual core services.

Corrected production image ID prefix: `5e20eda64064`; build context SHA256:
`64a324b9278e3a27ac79d190923e45916b2376d7baf98a0f103a002bc93f9eca`.
The original independent image script assumed session bootstrap needed no core capability call;
after R3's intentional change, its happy-path mock was corrected to supply valid capabilities.
The script now also verifies advertised cap 10 and a safe 503 during core bootstrap outage.
The intermediate image-script failure and an overly broad resolver-badge test selector were
review-harness mistakes, corrected and rerun; neither was classified as an application defect.

The evidence supports approval of this corrected PR3 runtime within the accepted component
scope. Required final-head CI, maintainer merge authority and the deferred deployment/operator
verification boundaries below remain separate gates.

The final runtime verdict extends the above evidence to
`c790ce3c8871c30a5db1149cd06b7c7786aa5319`: subsequent changes are retained review/CI artifacts,
the isolated functional fixture budget and source-page synchronization described below, plus R4's
single code-point-count expression. The reviewer inspected this cumulative boundary and the R4
focused passing results. Final publication-head full browser/CI binding follows separately; the
earlier passing runtime and CI evidence is not presented as execution on a later commit.

## Initial verification observations

After correcting two mistakes in the independent test harness, original-head synthetic results
were **14 passing, 4 failing** (R1 and R2 in both engines). The first harness accidentally
intercepted a document route as JSON; narrowing interception to `/api/v1/` fixed that. Chromium
represents an absent referrer as an empty string in some aborted request events; no topic-bearing
referrer was observed. These are not application vulnerabilities.

The independent real-core Chromium flow passed. Firefox completed its assertions but teardown
collided with the simultaneous suite's shared Playwright artifact directory; isolated configurations
now use distinct output directories. This environmental failure is not an application finding
and requires a clean retest.

Independent initial production image: `knowledge-vault-dashboard:pr130-independent-initial`,
image ID prefix `42510378a2c4`; build context SHA256
`7d6dbae83fc5d7ee330f0e0d1b6cfa601485d6415143c619df4292e7630db2fa`.
The reviewer-authored `/tmp/pr130-independent-image-inspect.py` passed in this image with network
disabled, read-only root, all capabilities dropped, no-new-privileges, UID 10001, bounded memory
and process count. It independently checks actual installed wheel assets and ASGI responses:
two public JS/CSS assets, no source maps/fixture corpus, no Node/npm/core/SQLAlchemy/psycopg/torch/
sentence-transformers modules, shell/asset security headers, anonymous API refusal, unknown-route
refusal and private cookie login using ephemeral credentials. Raw logs:
`/tmp/pr130-independent-image-build.log`, `/tmp/pr130-independent-image-inspect.log`.

## Publication-gate diagnostic follow-up

Running all browser flows together exposed two test-harness issues: the four actual-core flows
shared a production-sized read budget, and equal-sized source pages could be mistaken for a
completed page transition. The functional fixture now explicitly supplies a larger synthetic
read budget; production defaults and dedicated 429 tests are unchanged. The reviewer's source
loop now captures complete, disjoint pages atomically and still requires all 43 unique URLs.

A Firefox content-process crash occurred during the original author's default Axe scan in both
an earlier local run and an earlier CI run. Its native cause remains unconfirmed; it was not
classified as an application vulnerability or attributed to local resource pressure. The reviewer
authored an isolated diagnostic replay of the three-scan desktop/Portal/mobile sequence, comparing
original waits with DOM/font/frame settling. All six cases (18 default Axe scans) passed, with
no focus-induced session refresh observed during scans. The subsequent complete default browser
suite and CI on `723e0f27e3899ad18c8c884b7ec088c5c071ea7a` also passed. No Axe rules were disabled,
legacy mode enabled, retries added or browser security defaults changed. Raw diagnostics:
`/tmp/pr130-independent-axe-diagnosis.log`, `/tmp/pr130-ci-B-dashboard-web.log` and
`/tmp/pr130-final-head-browser-v2.log`. Final publication-head CI remains an explicit gate rather
than treating a previous passing SHA as its substitute.

The native crash later recurred during that same original Axe case in a full local run on
`9ffde9aeb32a03cb8fd27925e6360e0718c55cdc` (49 other default cases passed), although that commit's
[complete CI run](https://github.com/kimonus/knowledge-vault-mcp/actions/runs/44709352257)
passed all six jobs and all 52 browser cases. The reviewer verified the commit/run association,
job conclusions and dashboard-web logs directly through GitHub, rather than relying on the
implementation agent's status report.

A controlled comparison then used the unchanged original test cases and actual reviewed source:
five fresh Firefox processes each ran the Access scenario followed by the original three-scan
Axe scenario. All five Access cases passed; two of the five subsequent Axe cases crashed
natively. In the matched configuration, the Axe case alone passed in all five fresh processes.
An additional original-configuration fresh control also passed three of three runs. This is
measured association with preceding browser-process history; it does not establish the native
cause, an application vulnerability or that DOM settling fixes it. Raw evidence:
`/tmp/pr130-firefox-access-axe.log`, `/tmp/pr130-firefox-control-axe.log` and
`/tmp/pr130-firefox-fresh-axe.log`.

The reviewer inspected and accepts the resulting harness mitigation: run exactly the original
Firefox desktop/Portal/mobile Axe case in a dedicated fresh-worker/browser project, while
retaining its code, tags, rules and assertions unchanged. Only that case is excluded from the
ordinary Firefox project; the independent 320px Axe/keyboard/reflow checks remain there.
The independent-suite configuration selects its two intended browser projects explicitly.
The collected suite still contains 50 default cases plus two low-capability cases (52 total),
with zero retries and unchanged browser security options. This isolates the observed test-tool
interaction without weakening application behavior or accessibility coverage. The final
mitigation commit still requires its own complete browser and CI run before exact-head approval.

## Accepted limitations and remaining boundaries

- Base64url topic paths are sensitive, reversible resource names, visible in browser history and
  requests to the intended BFF. Tests verify no Referer propagation and empty browser storage;
  this does not make topic URLs secret. Future ingress/Cloudflare/operator logging must suppress
  or sanitize them. BFF entrypoint disables access logs and suppresses httpx/httpcore URL logs.
- Source destinations remain untrusted HTTP(S) websites opened only on deliberate activation.
  Allowing such a link is not an endorsement of its contents or identity.
- Local logout does not revoke copied private cookies; Access provider sessions can remain active.
  Both are explicitly documented design limitations. A compromised browser/extension and forensic
  recovery of browser memory/history are outside this component's guarantee.
- Browsing is live keyset traversal, not a snapshot. Previous navigation retains at most 100
  cursor boundaries, while forward traversal follows each returned continuation without a cap.
- Simulated Access edge denials and real local RS256 validation do not prove actual Cloudflare
  provider-session expiry. Live TLS/ingress/NetworkPolicy/Access and rollout/removal proof belong
  to separately authorized PR4 work; this review makes no deployment certification.
- Static source/build inspection and non-root image checks establish component packaging;
  final signed publication, supply-chain release integration and conditional Helm resources are
  intentionally not implemented by PR3.

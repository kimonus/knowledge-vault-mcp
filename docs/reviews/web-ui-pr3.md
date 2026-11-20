# Web Dashboard PR3 implementation verification

Date: 2026-11-19. Base: PR2 merge `d329d4c0569bb9ae13be666478aa890f4c114e61`.
Scope: ADR 0009 PR3, Explorer/Portal, frontend contracts and independent dashboard image.
This is the implementation author's verification record, **not independent approval**. The
[independent review](pr130-independent-review.md) records the separate review and dispositions;
its exact final-head verdict is also published in PR #130. No merge, deployment, credential provisioning,
DNS/Access/ingress change or Helm dashboard resource is included.

## Boundary and behavior assessment

| Boundary | Implementation and evidence |
| --- | --- |
| Browser → BFF | Fixed same-origin JSON operations; same-origin cookies, CSRF/custom header, AJAX header, no Authorization bearer. Named documents/assets only; unknown API/static paths never receive the shell. BFF Host, Origin, Fetch Metadata and disabled-mode tests remain intact. |
| Private session | Only transient login input, cleared at exchange; Secure/HttpOnly/Strict cookie stays unreadable to JavaScript. Expiry, focus revalidation, 401/403 and logout discard the authenticated tree, cursor/filter memory and outstanding requests. PR2 signature/revocation tests remain unchanged. |
| Access → origin/browser | Real locally signed RS256 assertions exercise the actual BFF verifier. Simulated edge covers non-JSON 401 and redirects, top-level reauthentication and provider-session persistence after local logout. No private fallback. This is not live Cloudflare validation. |
| Stored knowledge → DOM | React text nodes and preformatted text, no unsafe HTML sink. HTTP(S) links reject embedded credentials, use noopener/noreferrer and never fetch previews. Browser tests assert inert scripts/images, no attacker network request and actual CSP blocking. |
| BFF → core | Existing fixed upstream, service-only bearer, exact-read authority, input/output schema validation and bounded requests are preserved. The real-core browser test authenticates an inspection principal against actual services and disposable pgvector. Existing core-boundary tests prove ordinary MCP/write/admin denial and no accounting writes. |
| Build → runtime | OpenAPI-derived TypeScript drift check; synthetic fixtures validated by actual domain response models; digest-pinned independent Node/Python image stages. Final image runs uid 10001, read-only with dropped capabilities, and imports no core, SQL, PostgreSQL or embedding package. No Node runtime, fixtures or source maps in runtime assets. |

Top-level cross-site GET document navigation is allowed only for named pages with no Origin
and document/navigation Fetch Metadata, so Access return links can open the shell. This never
exempts API or script reads, wrong Origin/Host, or Access assertion validation. In private mode
the public shell contains only compiled code and the public authentication-mode enum.

## Original implementation verification evidence

Local checks were run against synthetic data and disposable resources:

- Core: **694 tests passed**, including real pgvector integration/e2e and HTTP/MCP confinement.
  Application coverage: **98.12% statements / 93.50% branches**.
- Installed standalone BFF wheel: **159 tests passed**; **98.18% statements / 94.30% branches**.
  Strict Pyright passed for core and BFF. Shared pure auth helpers are measured, not excluded.
- Frontend: **44 component/integration/client tests passed**; **97.83% statements / 91.79%
  branches** (97.25% functions, 98.07% lines), including every source file without exclusions.
- Browser: **21 tests passed**: ten scenarios each in Chromium and Firefox, plus one actual
  core/pgvector integration flow in Chromium. Traversed all 205 retained fixture records, 43
  sources, 22 artifact references, 35 identities, 103 relationships and 1,003 immutable evidence
  entries. Byte-shortened continuation pages remain traversable; ranked exhaustion is labelled.
- axe WCAG 2 A/AA and 2.1 AA checks reported **zero violations** on Explorer, Portal and the
  390px mobile view in both engines. Keyboard navigation, focus management, labels, mobile
  navigation and no horizontal overflow were checked. Automated axe is not a full accessibility audit.
- Private and simulated Access flows cover cookie expiry/logout, CSRF-aware requests, non-JSON
  edge refusal/redirect, safe service degradation/429/manual retry, CSP and adversarial sensitive
  assertion/source/artifact text. No service token is sent by browser requests.
- Locked Python/frontend manifests, core/BFF contract drift, frontend fixture drift, Ruff,
  frontend lint/format, TypeScript, production build, Bandit, Hadolint and actionlint passed.
- All three images built. Dashboard isolation smoke test passed with no network, read-only root,
  no capabilities and no privilege escalation. Existing backup/restore and retention rehearsal
  passed. Default and Access-enabled Helm renders each validated 14 resources with strict
  kubeconform; integer checks passed. Chart resources were not changed.
- Python dependency audits and `npm audit` reported no known vulnerabilities. Trivy image
  scans and Gitleaks results, and final CI disposition, are recorded in the PR completion update.
  CI additionally runs the Inspector and feasibility-tooling gates.

The host's legacy Docker client ignores Dockerfile-specific ignore files; the local dashboard
build used an explicit source tar context. CI uses BuildKit and the committed ignore file.
No Docker pruning, cluster mutation or live container shutdown was performed.

## Implementation review dispositions

- Unsupported unclassified filters cannot silently disappear in ranked mode: those combinations
  are visibly disabled, with a regression test. Exhaustive modes support all unclassified buckets.
- An invalid topic resource key cannot turn a thematic page into an unfiltered vault view;
  decoding fails closed with a regression test.
- Child pagination uses the actual detail preview page size and returned continuations, preserving
  access beyond previews and relation/resolver limits. Artifact offsets remain character offsets.
- Facet/filter/cursor values are in authenticated React memory, never browser storage/history
  state. URLs contain resource IDs and encoded stored topic names, not search/temporal values.
- Confirmed ingestion, verified evidence, current status, proposal workflow and considered/rejected
  kinds remain separate. Unknown timestamps, ambiguous/conflicting state and resolver overflow
  do not become inferred facts, modified dates, summaries or partial winners.

## Accepted limitations and PR4 requirements

All-sensitivity operator visibility, live scans rather than snapshots, bounded ranked candidates,
single-process rate budgets and copied-cookie replay until absolute expiry/coarse revocation
remain ADR limits. Previous-page history is bounded to 100 cursor boundaries. Reload clears
filters; topic/resource names in URLs remain visible in local history despite no-referrer.
Access local logout does not revoke the provider session. No offline operation or persistent
cache is offered. A compromised browser or extension can see data legitimately displayed to it.

PR4 must add disabled-by-default conditional Helm resources, private TLS/hostname/Secret refs,
least-privilege ingress/egress and resource/security controls, signed dashboard release/SBOM
publication, disabled/enabled/removal isolation rehearsals and explicitly authorized browser
verification through the real ingress and Access edge. It must expire a real provider session
and verify non-JSON denial/redirect recovery. Local JWT/edge tests cannot substitute for that.
The existing live deployment was not used as a browser fixture. This author record does not
authorize merge or enablement; see the independent report and exact-head PR review disposition.

## Independent review remediation — 2026-11-20

The fresh-context reviewer independently reproduced three Medium findings: stalled logout
retaining private DOM, mobile detail-header overflow hiding Sign out, and supported smaller
core page caps breaking reads. Commit `65eb15b6e56965a8d1829ad0481d2c5236dea80f` fixes all three.
Logout immediately discards private state and aborts reads, uses a detached five-second exchange
and prevents stale refresh restoration. Header controls reflow at 320px. Session bootstrap exposes
the validated public page cap under the existing upstream budget; page/history reads respect it,
while unpaged reads and artifact character limits remain separate.

Post-remediation checks: 694 core tests, 164 installed BFF tests and 51 frontend tests pass.
Frontend coverage is 97.81% statements / 91.68% branches; BFF is 98.08% / 94.02%.
The independent reviewer also confirmed and corrected a Low artifact-range display defect:
non-BMP text now uses the same Unicode character count as core offsets. Content and paging were
never truncated by that label defect.
An additional Medium refetch defect was independently reproduced: seven unapplied Topic
keystrokes sent seven unwanted knowledge-query POSTs. The initial applied Explorer spec is now
memoized by the advertised limit; draft edits retain that spec and only Apply starts a new read.
This is a request-budget and UI stability correction, not an authentication change or a claim
that Firefox's underlying native fault has been identified.
The independent reviewer authored 28 adversarial browser runs across both engines, two
actual-core/pgvector flows and two low-capability flows, and independently assessed production
image inspection. All passed on the corrected runtime. These regressions are retained in CI,
including both browsers against the actual core and a separate low-capability fixture. The
full publication suite includes 54 browser cases after the Unicode and draft-filter regressions.
Functional
fixture rate-budget isolation and atomic source-page snapshots preserve deterministic tests
without changing production limits, weakening assertions or enabling retries.
The original Firefox Axe assertions run in a dedicated fresh browser worker. The shared helper
waits for loading, fonts and two animation frames; login waits for all 50 record cards before
the preserved heading assertion. Controlled comparisons and their failures are recorded in the
independent report. The underlying native cause is unconfirmed; no accessibility rules, corpus,
browser preferences or production behavior changed for this test-order stabilization.
Final CI, exact published SHA and independent approval are recorded in PR #130; this historical
author record is not a substitute for that verdict. PR4 requirements above remain unchanged.

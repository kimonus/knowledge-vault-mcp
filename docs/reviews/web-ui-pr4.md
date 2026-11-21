# Web UI PR4 verification record

Base: PR3 merge `238788297f8266cb7f05a5b6832d490b48824713` (latest `main` at task start).
Scope: optional Dashboard Helm deployment, independent signed-release integration and operations.
No live deployment, credential provisioning, DNS, Cloudflare Access or tunnel change is authorized.

The existing
[PR2](web-ui-pr2.md), [PR3](web-ui-pr3.md) and
[independent PR3 findings](pr130-independent-review.md) remain applicable and unchanged.

## Final corrected-source verification

Corrected implementation: `16dd9f4d4bd2f2644d682b4e95804f42082c709c`.
[CI run 44884750849](https://github.com/kimonus/knowledge-vault-mcp/actions/runs/44884750849)
passed every job: quality, dashboard, dashboard-web, supply-chain, kubernetes and Inspector.
GitHub tested the PR merge of that source with the unchanged PR3 baseline.

- 706 core tests and 173 feasibility-tooling tests passed; application coverage is 98.03%
  statements and 93.14% branches.
- 164 installed BFF tests passed at 98.08% statements / 94.02% branches. Frontend: 51 component
  tests, five SBOM graph tests and 54 Chromium/Firefox cases passed; coverage 97.81% / 91.68%.
- Thirty real Helm render/negative cases passed; strict schemas validated all 202 resources
  in ten valid profiles. Disabled resources match the PR3 baseline. Dashboard toggles,
  image/Secret revisions and rollback values preserve every core manifest and checksum.
- Three images built and passed fixed HIGH/CRITICAL vulnerability gates. Separate runtime and
  seven-package frontend inventories passed validation/scanning. A loopback OCI registry and
  throwaway key verified image signature, both digest-bound SBOM attestations, wrong-key
  rejection and rejection on a different unsigned manifest. This is not GHCR/OIDC publication.
- Existing backup/restore/retention rehearsal passed with disposable pgvector data and a
  read-only-root backup container. Dashboard introduces no persistence or backup-format change.

## Executed disposable deployment proof

The same CI supply-chain job created and removed its own Minikube profile, Kubernetes 1.36.4,
containerd 2.3.4, Calico, and NGINX ingress controller 1.15.1. It used only synthetic assertions,
new fixture credentials and a one-day lab TLS certificate; no live infrastructure was changed.
The loaded Dashboard manifest was
`sha256:e38ed2e9bd0e72d35221a07df6ec5c4eebc1e0807036bddfbe62243ab200b481`.
Containerd import and local OCI publication use different manifest formats/digests; signatures
were separately verified on the exact registry subject reported in the job log.

The successful harness proved default-off installation; Dashboard enablement, BFF-only rollout,
rollback, restart/recovery and removal with unchanged core Pod UIDs/restarts/manifests; actual
HTTP and official-MCP-client writes and readback across those phases; missing-Secret startup
refusal and invalid-service-token readiness/session refusal; real Dashboard-to-core connectivity,
DB/external egress denial and unmatched ingress denial with positive controls; and retained
operator-owned Secrets, including TLS, after Dashboard removal. Server-side dry-run preceded
upgrades. Core and BFF logs were inspected after client calls for credential/content leaks;
both reported zero restarts.

The external Chromium callback used the actual ingress, verified the lab certificate and hostname
with strict TLS, then trusted only that certificate's SPKI. Login, secure cookie, Explorer,
detail, Portal and logout passed. Host/path preservation and separation of core/MCP/probe routes
passed. This is actual isolated ingress/browser proof, not production trust or real Access proof.
Embeddings were disabled: this rehearsal does not claim an actual-model cold-start test or a
browser call against a restored core database.

## Independent review and remediation

[Security review](web-ui-pr4-independent-security.md) and
[deployment review](web-ui-pr4-independent-deployment.md) conclude PASS with no open blocking
finding after independent corrected-source retesting and inspection of actual CI job logs.
Reviewers separately retrieved GitHub job outcomes; execution by CI is distinguished from their
local tests. Prior PR2/PR3 findings and regressions remain unchanged.

The release inventory gate caught npm's omission of runtime React packages shared with dev peers;
the corrected build follows production dependency roots through the complete CycloneDX graph,
with negative/closure regressions and all seven expected packages verified. The documented
SBOM predicate now exits nonzero on failure. The failure canaries have explicit controlling
Deployment ownership so ReplicaSets cannot adopt them. Leading Helm document separators are
normalized without hiding real core changes. The browser callback explicitly launches installed
full Chromium and reveals only fixed failure-stage names, never raw Playwright diagnostics.

The final report commit only incorporates this evidence and the independent reports; it does not
change the tested implementation. Final PR-head CI and the nonpublishing release rehearsal are
reported on the PR rather than inferred from an earlier run.

## Local regression evidence

- Core: 694 tests passed with real disposable pgvector; statements 98.03%, branches 93.14%.
- Dashboard BFF: 164 tests passed; statements 98.08%, branches 94.02%.
- Frontend: 51 component tests passed; statements 97.81%, branches 91.68%.
- Core formatting, lint, strict typing and read-contract drift checks passed.
- Frontend locked install, contract drift, strict types, lint, formatting and build passed.
- Chromium/Firefox browser suites: 52 primary cases and two low-capability cases passed, including
  independent PR3 regressions and real disposable pgvector/core/BFF operation over loopback TLS.
  This is application-boundary proof, not deployed ingress or Cloudflare proof.
- Both lock checks and standalone Dashboard sdist/wheel build passed with pinned uv 0.13.7.
- All three images built. Dashboard non-root, read-only-root, no-network packaging checks passed.
- All three images passed Trivy's fixed HIGH/CRITICAL vulnerability gate; Dockerfiles, workflows
  and core/BFF source passed Hadolint, Actionlint and Bandit.
- Backup/restore retention rehearsal passed using disposable containers and synthetic data.
- Core and Dashboard installed Python dependency audits found no known vulnerabilities.

## Authorization and evidence boundaries

Live ingress, private DNS/TLS, operator Secrets and exact-read core credentials require operator
authorization. Real Cloudflare Access expiry/redirect/AJAX/reauthentication proof requires the
separately approved browser-application procedure in the [runbook](../runbooks/dashboard.md).
Simulated provider tests are regressions only and do not establish real provider behavior.

Signed publication requires the maintainer's signed release tag under [RELEASING](../../RELEASING.md).
PR verification and a nonpublishing workflow rehearsal do not claim a published signed image.

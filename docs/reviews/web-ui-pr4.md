# Web UI PR4 verification record

Base: PR3 merge `238788297f8266cb7f05a5b6832d490b48824713` (latest `main` at task start).
Scope: optional Dashboard Helm deployment, independent signed-release integration and operations.
No live deployment, credential provisioning, DNS, Cloudflare Access or tunnel change is authorized.

This record is updated with measured results before review. The existing
[PR2](web-ui-pr2.md), [PR3](web-ui-pr3.md) and
[independent PR3 findings](pr130-independent-review.md) remain applicable and unchanged.

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

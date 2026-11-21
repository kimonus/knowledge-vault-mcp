# PR4 independent deployment and release review

Reviewer: independent deployment/release reviewer using an exclusive worktree; did not implement
PR4. Initial subject: `739cd5f`; corrected final subject independently rechecked:
`0a239c826a1212a392e4cb2bcca8d7f84a97df28`; baseline: PR3 merge
`238788297f8266cb7f05a5b6832d490b48824713`. Review updated: 2026-11-21.

## Initial verdict

**Provisional source/render verdict: PASS at `0a239c8`, pending the disposable Minikube CI
lifecycle result.** No blocking deployment/release defect identified. Final-head source and
offline regressions were independently revalidated. No live deployment,
credential provisioning, GitHub publication, DNS, Access application or tunnel change was performed.

## Independent checks

- Read repository instructions and RTK guidance, the original brief's deployment/security
  requirements, ADR 0009, staged implementation acceptance, deployment design, prior PR3
  independent review and PR4 verification record.
- Ran the actual `scripts/ci/check_dashboard_chart.py` with Helm v4.2.4: all 30 renders passed.
  This covers disabled/private/Access/ingress/tunnel configurations, dashboard image/Secret
  updates and removal, full non-dashboard manifest equivalence and 20 invalid configurations.
- Independently rendered the chart from baseline Git objects into a disposable directory and
  compared parsed complete documents: disabled current resources exactly equal baseline resources.
- Added eight ad hoc Helm refusals independent of the implementation script: malformed host,
  uppercase/trailing-dot/port host overlap, configured PostgreSQL Secret reuse, empty Secret key,
  Access mode missing required configuration, empty CPU request, and private mode with Access
  settings. All refused. These checks ran outside the repository and changed no source files.
- Helm lint passed with `values-minikube.yaml`; rendered-number checks passed across generated
  manifests; `git diff --check` passed before this report.
- Strict kubeconform execution attempted, but schema download was blocked by this review
  process's sandbox DNS/socket restriction. This is not a schema-validation pass. The root
  execution agent subsequently reports all 202 rendered resources passed strict validation
  in its network-permitted environment; that result is delegated execution evidence.
- On corrected final head, independently reran the 30 real Helm renders and ten lifecycle
  safety tests: all passed. The safety tests verify refusal of unrelated contexts/cluster aliases
  before mutation and content-free error/log handling. JavaScript syntax checks passed.
- Independently executed all five new frontend SBOM graph tests: all passed. Inspected the
  corrected image's extracted inventory supplied under `.tooling/pr4-sbom-corrected`: seven
  packages, exact production versions, complete dependency references and no dangling nodes.
  Runtime closure includes React, React DOM, scheduler, React Router, React Router DOM, cookie
  and set-cookie-parser. The corrected Docker build starts with npm's full installed inventory
  and walks exact production roots, avoiding npm omit-dev loss of shared peer dependencies.
- Read the root-executed `pr4-signing-final.log`: corrected local-key mode successfully verifies
  the immutable loopback image subject and both signed inventory predicates and rejects a wrong
  key and a distinct unsigned manifest. This is inspected execution evidence, not an independent
  Docker run in the review sandbox or production GitHub OIDC verification.

## Corrected-head retest — 2026-11-21

Independently executed against `0a239c8`: all 30 real Helm renders, all ten lifecycle safety
tests, all five frontend SBOM graph tests, deployed-browser JavaScript syntax, Helm lint,
rendered-number checks and `git diff --check` passed. Reconstructed the baseline chart directly
from Git objects in a temporary directory and compared parsed complete objects: all 14 disabled
documents exactly match baseline. No implementation files were changed by this reviewer.

The corrected core-document comparison consumes Helm's leading YAML separator consistently;
its regression proves inserting a dashboard document before the first core object leaves core
equivalence unchanged. A separate regression proves actual core text changes remain visible,
including a core ConfigMap containing `/templates/dashboard`. The source-marker check is anchored
to the Helm source comment rather than searching arbitrary data for dashboard text.

Inspected and independently exercised the canary metadata helper with a synthetic Deployment:
policy labels remain intact and both failure canaries carry the live Deployment UID as their
controller owner. That differs from the real ReplicaSet UID, preventing ReplicaSet adoption of
a matching orphan Pod. Canaries are removed in `finally` blocks. This is a sound ownership fix;
actual cluster confirmation belongs to the pending disposable lifecycle run.

Strict kubeconform was attempted again, but schema retrieval failed on sandbox DNS/socket
permissions; the command was stopped after that confirmed environmental failure. This review
does not count strict Kubernetes schema validation as independently passed. No escalation,
Docker invocation, network workaround, deployment or provider modification was performed.

The release verification and isolated signing mechanics were reviewed unchanged from the prior
corrected report. The local signing log was reread and confirms digest-bound signature and both
inventory predicate verification plus wrong-key/unsigned-subject rejection. Root reports that
nonpublishing release rehearsal `44813835588` passed at `0634e4f`; this is supplied execution
context, not an independently run exact-current-head publication proof.

## Release and lifecycle analysis

The chart creates separate dashboard resources only under the enabled flag; no changed core
template or shared checksum incorporates dashboard configuration. Dashboard service is ClusterIP,
the TLS ingress preserves `/dashboard` without rewrite, the three private Secrets are separate
references, and the BFF mounts neither database nor core token-record/pepper material. Dashboard
egress admits selected DNS/API peers and explicit Access HTTPS destinations; database ingress
does not admit the dashboard component. Additive core/tunnel allowances are dashboard-owned.

The release workflow publishes a separate dashboard image, scans immutable pushed subjects and
uses those same digest subjects for signatures and both CycloneDX attestations. The runtime
inventory and extracted embedded production npm inventory are separate; expected-package checks
reject empty/incomplete inventories. The frontend image build uses locked dependencies, and
frontend audit supplements runtime-image scanning. BuildKit attestations and explicitly signed
CycloneDX predicates are accurately distinguished. Manual rehearsal disables registry publication,
release creation and keyless production signing. The local signing harness uses a throwaway key
and loopback registry, verifies digest-bound runtime/frontend predicates, and tests wrong-key and
unsigned-manifest rejection. The supplied final signing log establishes these local mechanics;
production OIDC identity and GHCR publication remain separate evidence.

The operations runbook requires compatible installed core/schema/config and full render
equivalence before dashboard-only `--no-hooks`; ordinary core upgrades retain migration hooks.
Rollback restores dashboard values against the current core rather than blindly restoring old
core manifests. Removal preserves external Secrets, database/PVC and core inspection records.
Secret-content changes require an explicit BFF rollout/revision; no core restart is implied.
TLS/ingress, enforcing CNI, external authenticated browser calls, existing MCP reads/writes,
restored-core reads, genuine provider expiry and post-call logs/restart counts are explicit
acceptance obligations. Documentation does not mislabel simulated Access as provider proof.

The new disposable harness refuses unrelated contexts and existing fixture namespaces, uses
fresh synthetic credentials/data, performs server admission checks before dashboard upgrades,
and bounds external operations. It compares all non-dashboard manifests and core Pod UIDs/restarts
through enablement, BFF-only revision update, rollback, restart and removal. It exercises actual
core/BFF reads and ordinary HTTP writes, missing-Secret and invalid-service failure, and network
denials with a database positive control. No model is downloaded or loaded: embeddings are
explicitly disabled, so concurrent real-model first-use proof is outside this harness.

CI adds a disposable Minikube/containerd/Calico profile, NGINX ingress and imported immutable
dashboard manifest, then executes the harness and removes only that profile. The external
Chromium callback checks the lab certificate and hostname with strict TLS before trusting that
certificate's exact SPKI in an isolated browser; it verifies authenticated synthetic Explorer,
detail/Portal/logout and Host/path confinement through ingress. This is meaningful ingress proof
when it executes successfully. It does not claim Firefox, complete production acceptance, real
Access, restored-core browser operation or actual-model first-use. The harness's existing-client
checks use HTTP, so MCP compatibility across the lifecycle remains a separate gate.

## Evidence boundaries

This source/render review does not establish real ingress/CNI enforcement, Minikube lifecycle,
browser authentication through deployed private TLS, restored-core browser operation, GitHub OIDC
identity, published GHCR signatures, or real Cloudflare session expiry. Those require their named
execution evidence and authorization. CI run `44813688234` was pending at report update;
local full Minikube startup was blocked by host inotify capacity. The newer exact-current-head
CI run `44852689267` is pending root-agent evidence inspection at this update. Earlier run
failure at the leading-document comparison is addressed by the independently passing regression
above; that correction alone is not a successful cluster lifecycle result. Neither pending execution is
reported passed here. Implementation-agent test counts are context, not
independently executed results in this report.

Next: the root agent inspects exact-head CI `44852689267`, records its actual disposable
Minikube/TLS-browser/lifecycle outcome and completes the final evidence-bound verdict.

## Final deployment verdict with completed CI — 2026-11-21

Independent final reviewer: `/root/final_verdict`, did not implement PR4. Exact corrected source
independently checked: `16dd9f4d4bd2f2644d682b4e95804f42082c709c`.
**PASS: no blocking deployment or release finding remains in the reviewed PR4 scope.**
This section supersedes the earlier pending lifecycle statements and the earlier statement that
the harness has HTTP-only existing-client coverage. It preserves their historical evidence.

Independently reran 30 real Helm renders, Helm lint, 11 lifecycle safety tests, five frontend SBOM
graph tests, deployed-browser JavaScript syntax, disabled/private rendered-number checks,
164 BFF tests and whitespace validation: all passed. Inspected the latest source delta:
explicit full Chromium selection matches the installed browser; stage diagnostics disclose only
a fixed allowlist; the official MCP client now writes and reads synthetic assertions at every
existing-client probe after enablement, BFF upgrade, rollback, recovery and removal.

Connected GitHub read tools independently confirm the reviewed commit's
[CI run 44884750849](https://github.com/kimonus/knowledge-vault-mcp/actions/runs/44884750849)
completed with all six jobs and every step successful. Independently fetched and inspected
[supply-chain job 136787500082 logs](https://github.com/kimonus/knowledge-vault-mcp/actions/runs/44884750849/job/136787500082).
CI checked out PR merge `9983562` combining source `16dd9f4d` and baseline
`238788297f8266cb7f05a5b6832d490b48824713`. These are independently inspected CI
execution records, not claims that this reviewer executed Docker, Minikube or Cosign.

The disposable enforcing-Calico Minikube run passed actual TLS ingress/Chromium browser acceptance,
authenticated ClusterIP HTTP and official MCP read/write compatibility, inspection/BFF authority
confinement, missing service Secret and invalid service credential refusals, egress/database denial
and positive controls, unchanged core Pod identities/restarts through enablement/update/rollback/
recovery/removal, retained external Secrets, and post-call safe API/BFF logs with zero restarts.
Server-side admission is required before each dashboard upgrade. The isolated profile was removed
by the successful cleanup step. This closes the previously pending isolated lifecycle gate;
it does not establish the production cluster's state.

The same actual job passed image isolation checks, backup/restore, immutable image scans, and
local-registry digest-bound signature plus both runtime/frontend SBOM verification, rejecting a
wrong key and unsigned subject. Production GitHub OIDC identity and GHCR publication are separate
from the throwaway-key registry exercise. The archived full CI log also reports 706 core,
164 BFF, 51 web component and 54 browser tests passing; those counts are CI evidence.

Production deployment remains outside this approval. Real Cloudflare session expiry, published
OIDC-signed release verification, restored-core browser operation and concurrent actual-model
first use remain explicitly unverified operational checks. The isolated fixture disables
embeddings; no live provider or production acceptance is inferred. No escalated commands,
production mutations, publication, source changes or commits were performed by this reviewer.

Next: the root agent incorporates the final reports and supplies the documentation-only diff for independent inspection.

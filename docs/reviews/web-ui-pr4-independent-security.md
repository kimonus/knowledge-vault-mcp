# Web UI PR4 independent adversarial security review

Reviewer: `/root/security_review`, independent of PR4 implementation. Date: 2026-11-20.
Initial tested SHA: `739cd5f` against main `238788297f8266cb7f05a5b6832d490b48824713`.
Checkout: isolated `pr4-security-review` worktree. This report does not certify live deployment,
Cloudflare Access browser behavior, GitHub OIDC or a published release.

## Initial disposition

Source security review found no confirmed read/write escalation, privileged Secret mount,
database connectivity allowance or public bearer endpoint introduced by the chart. The independent
security checks below pass. Final approval remains pending corrected-head retest and the separate
operations lifecycle evidence. An implementation-side SBOM rehearsal failure is recorded below
without representing it as reviewer-executed Docker evidence.

## Scope and method

Read AGENTS.md and RTK.md, the historical implementation brief, ADR 0009, implementation plan,
deployment-security design, relevant security/runbook/release changes and prior independent PR2/PR3
findings. Reviewed the complete PR4 chart, schema, CI/release and new supply-chain scripts.
Adversaries considered include an unauthenticated caller, a cross-origin browser, a compromised
BFF with its mounted Secrets, malformed operator configuration and substituted unsigned images
or incomplete inventories. Operator configuration is trusted authority; accepting a deliberately
broad ingress peer is not itself classified as an authentication bypass.

The chart pins a separate dashboard digest; guards every dashboard-owned resource behind its
toggle; keeps full core renders identical; mounts only three separate dashboard Secrets in private
mode and only the issued service bearer in Access mode; disables service-account automount; and
sets the expected non-root/read-only/drop-capability/seccomp restrictions. Fixed core URL/Host
generation provides no browser-selected upstream. Core/DB/tunnel/backup Secret names are refused.
The ingress forwards only `/dashboard` to the dashboard Service; it does not expose probes or core.
Dashboard egress is DNS, core port 8000 and opted-in Access destination CIDRs on 443. PostgreSQL
policy does not admit dashboard Pods. HTTP inspection-principal confinement remains necessary
because L3 NetworkPolicy cannot restrict paths on the allowed core port.

Release review verifies separate image build/cache identities, digest-bound scans/signatures and
both explicit CycloneDX attestations, pinned actions/tools, and tag-only publication/signing.
The local throwaway-key rehearsal explicitly cannot establish GitHub OIDC identity. Production
verification requires exact workflow/tag certificate identity and expected OIDC issuer.

## Findings

### S1 — Low, nonblocking: documented inventory gate exits successfully on false

At initial SHA, `RELEASING.md` uses `jq -sr` to verify the decoded signed attestations contain both
FastAPI and React. A false predicate is printed but returns status zero. Independently running
that exact expression against an empty input produces `false` and exit code **0**. Thus a copied
shell verification gate fails to enforce its stated package-presence condition. This does not
forge a Cosign signature or bypass Cosign's subject/identity checks.

Remedy: add `-e`, and verify missing/empty inventory cases return nonzero. Corrected-head retest
is pending.

### S2 — Release blocker reported by implementation, independent retest pending

The root implementation agent reported that the existing image's `npm sbom --omit=dev` inventory
omits React, React DOM and scheduler, and the new PR4 package-presence guard correctly refuses
it. This blocks the new release rehearsal rather than silently publishing an incomplete inventory.
This reviewer inspected the Dockerfile invocation and the helper's rejecting predicate, but has
not executed Docker or independently inspected the extracted failing/corrected image inventory.
Independent source/inventory retest must follow the correction before closing this item.

## Reviewer-executed evidence at initial SHA

- `scripts/ci/check_dashboard_chart.py`: **30 renders passed**, including complete core resource
  equality for disabled/enabled/updated/rollback/removal and Cloudflare variants, least-privilege
  mounts/context, narrow ingress, invalid settings and absence of private fallback in Access mode.
- Independently constructed **17 Helm configurations**: case/trailing-dot/port hostname collisions
  and actual auth/database/PostgreSQL Secret-name reuse refused; RFC1918, loopback, link-local,
  CGNAT, multicast and malformed-octet JWKS destinations refused; ordinary public destination
  accepted. `198.18.0.0/15` is accepted: the guard excludes named dangerous ranges, not every IANA
  special-use allocation. Operators must use reviewed actual verifier destinations.
- BFF tests with explicit reviewed-source `PYTHONPATH=dashboard/src`: **164 passed**, retaining
  PR2 readiness-capacity, Host/Origin/auth/CSRF, session and upstream regressions.
- Core inspection/Access tests with explicit reviewed-source `PYTHONPATH=src`: **30 passed**.
- Rendered-number checks passed for disabled/private/Access/ingress/tunnel-enabled manifests.
- Strict kubeconform was attempted but could not retrieve Kubernetes 1.33.0 schemas: sandbox
  DNS/network returned `socket: operation not permitted`. This gate is unavailable here, not passed;
  the root reviewer was given the exact render-path command for an authorized network context.
- Documented SBOM predicate negative test: printed `false`, exit 0; S1 reproduced independently.

The first custom Secret-name experiment used a nonexistent PostgreSQL Secret name and correctly
rendered; rerunning against the actual `values-minikube.yaml` Secret names refused all three.
That harness assumption was corrected and is not a product finding.

## Evidence limits

No private knowledge, production credential/configuration, Kubernetes live resources, cloud
resources or GitHub messages were accessed or changed by this reviewer. No Docker, network tool
installation or escalated command was run here. The implementation team's full image scans,
backup/recovery and future disposable Minikube lifecycle results remain separately attributable.
Passing static renders do not establish runtime policy enforcement, authenticated private-ingress
browser operation, unchanged live core Pods during upgrades or real Access expiry/reauthentication.
Only maintainer-authorized signed publication can establish the eventual release trust identity.

## Corrected-head independent retest

Tested SHA: `0634e4f94d9511bd307e41efb41bf99059ed7e03`. The reviewer inspected the entire delta
from the initial head, including CI, lifecycle harness, deployed-browser callback, Dockerfile,
runtime graph filter/tests, runbook and explicit test-only local-key Cosign mode.

**Provisional source-security approval, pending final-head CI and lifecycle results.** No open
blocking production authentication, credential isolation or release-integrity defect was found.
This is not approval to deploy/publish or certification of live Access or GitHub OIDC behavior.

- **S1 resolved:** the actual documented expression now uses `jq -esr`; independent empty-input
  execution prints `false` and exits **1**, correctly failing its package-presence gate.
- **S2 resolved at source/inventory boundary:** independently read corrected image-extracted
  inventories at `.tooling/pr4-sbom-corrected/`. Runtime contains FastAPI and the dashboard package.
  Frontend contains exactly `cookie`, `react`, `react-dom`, `react-router`, `react-router-dom`,
  `scheduler`, `set-cookie-parser`; every component version independently matches `package-lock`.
  Ran the filter's retained Node tests and a separate actual-inventory check: exact seven-package
  closure, immutable input, three independently removed direct roots and dangling dependency
  injection all behave correctly. The filter follows graph references from exact production pins,
  retaining shared peers/transitives while excluding development roots; missing/ambiguous pins,
  dangling references and absent graph entries fail closed. Docker builds now start from the full
  installed graph, avoiding npm's incorrect shared-peer omission in `--omit=dev` output.
- Read `.tooling/pr4-signing-final.log`: the implementation-executed disposable registry rehearsal
  reports signature, runtime/frontend attestation subject checks, wrong-key and unsigned-image
  refusal at digest `sha256:f9cc1e7d8a338f39b39248789cd132081654140b969a0537f167801857c1e135`.
  Source inspection confirms `--use-signing-config=false` is restricted to throwaway-key local
  rehearsal. Production tag-triggered OIDC signing/verification remains unchanged. This log was
  independently inspected, but this reviewer did not execute its Docker/Cosign calls.
- Lifecycle safety unit tests: **8 passed** with the reviewed source. Explicit context/cluster,
  namespace nonexistence, immutable dashboard digest and server-side admission checks constrain
  the harness; fixture Secrets use stdin and failures suppress captured payloads.
- Reviewed deployed browser callback: Node validates the fixture CA and hostname with normal TLS
  verification before Chromium trusts only the fixture public-key SPKI. It checks hidden probe/core
  ingress paths, actual browser login/query/Portal/logout and cookie flags. The harness compares
  core rendered documents and Pod UIDs/restarts through enablement, BFF rollout/rollback/recovery
  and removal, performs authenticated real-service confinement and network positive/negative
  controls, and inspects safe logs. These are reviewed assertions, not proof the cluster run passed.

Nonblocking harness observation sent to the implementation owner: missing-Secret and invalid-token
canary Pods initially reuse the real dashboard Deployment's selector labels without owner references.
ReplicaSet adoption/extra-replica cleanup may interfere with their tests. This is a potential CI
determinism issue, not a demonstrated production authority bypass; final lifecycle results or a
canary-label correction must establish the probe evidence before accepting that gate.

The root independently reports strict schema validation passed for its 202 deployment-review
resources; this reviewer did not execute that network-enabled command. CI run `44813688234` was
pending when this retest report was written. No local Minikube success is inferred from source
inspection, and local inotify limits remain an explicitly reported environment constraint.

## Final corrected-head security retest — 2026-11-21

Independent retest reviewer: `/root/final_security_review`, did not implement source. Exact head:
`0a239c826a1212a392e4cb2bcca8d7f84a97df28`; baseline remains
`238788297f8266cb7f05a5b6832d490b48824713`. Read the instructions, ADR 0009, accepted
implementation plan, prior PR2/PR3 independent finding dispositions and this retained report.
Reviewed the complete delta after the previous retest. **Source-security approval remains
provisional solely pending exact-head CI and isolated lifecycle evidence; no new blocking
security defect was identified.** No production deployment or publication is authorized.

Reviewer-executed checks against this exact source:

- Chart regression script: **30 renders passed**; rendered-number checks passed across those
  generated manifests. Reinspected separate Secret mounts, fixed core URL/Host, distinct-host
  rejection, private TLS ingress path, disabled resources, additive core policy, DNS/core-only
  private egress and explicit Access-only 443 destinations. No additional core/database authority
  or public device-bearer route was introduced.
- BFF suite using this checkout's `dashboard/src`: **164 passed**. Core inspection/Cloudflare
  narrow tests using this checkout's `src`: **16 passed**. Lifecycle safety tests: **10 passed**.
  The separate real-application lifecycle contract probe encountered Docker socket
  `PermissionError: Operation not permitted` during fixture setup; it did not execute and is not
  claimed as passed. The root was given the exact test to run in an authorized Docker context.
- Retained Node runtime-SBOM filter tests passed. Independently checked the actual corrected
  image-extracted inventories at `.tooling/pr4-sbom-corrected/`: exact seven-component frontend
  closure and all versions match `package-lock.json`; runtime contains FastAPI and the dashboard
  package. Refiltering preserves the inventory and does not mutate its input. Independently
  removing each of the three production root references and injecting a dangling graph edge
  fails closed. **S2 remains resolved** at the source/inventory boundary.
- The documented `jq -esr` predicate independently prints `false` and exits **1** on empty input.
  **S1 remains resolved**. Reinspected digest-bound production image scan, signature and both
  inventory attestations; exact tag/workflow identity and GitHub OIDC issuer are required by
  operator verification. The throwaway-key local rehearsal is isolated from production signing.
  Independently read the retained signing log and its successful digest/negative-control result;
  this reviewer did not execute Docker/Cosign or certify a published OIDC-signed release.
- The canary adoption observation is **resolved at source**: both probe Pods retain NetworkPolicy
  labels but carry the live Deployment UID as a controlling owner, preventing real ReplicaSet
  adoption. Independently checked owner fields and both call sites. The private Service excludes
  their failed-readiness endpoints. Runtime probe success still requires lifecycle evidence.
- The Helm comparison fix correctly splits the stream's initial document separator and anchors
  dashboard exclusions to Helm source comments. Independently compared actual disabled/private
  renders through this helper and injected a core-document mutation: equality holds for dashboard
  insertion and the core mutation remains detected. Retained tests also preserve a core ConfigMap
  containing literal `/templates/dashboard` text.

Prior PR2 readiness/authentication findings and all five confirmed PR3 findings remain preserved;
PR4 makes no BFF/SPA production-source changes to reopen them. Documentation now explicitly
requires approval for actual Access-provider acceptance and distinguishes application renewal,
global/One Client sessions and an actual IdP prompt. Local JWTs, fake edge responses and the
isolated private TLS run cannot certify real provider-session expiry.

CI run `44852689267` and its isolated Minikube/Calico/TLS/browser evidence were pending when this
section was written. No live proof, production NetworkPolicy enforcement, unchanged production
Pods or real Cloudflare session proof is inferred from these source checks. No escalated tools,
live resource access, cloud mutation, source edits or commits were performed by this retester.

Next: the root reviewer provides exact-head CI/lifecycle evidence; this reviewer records the final security verdict and its remaining live-provider limits.

## Final security verdict with completed CI — 2026-11-21

Independent final reviewer: `/root/final_verdict`, did not implement PR4. Independently verified
checkout `16dd9f4d4bd2f2644d682b4e95804f42082c709c` and reviewed its complete four-file
delta from `0a239c826a1212a392e4cb2bcca8d7f84a97df28`. This section supersedes the
earlier pending-CI dispositions while preserving their findings and historical evidence.

**PASS: no open blocking security finding.** S1 and S2 remain resolved; the canary ownership
observation now also has successful isolated runtime evidence. Source review and completed
isolated acceptance support PR4 review approval. This does not authorize production deployment,
publication or public routing.

Reviewer-executed exact-source checks: **164 BFF tests**, **11 lifecycle safety tests**,
**30 actual Helm renders**, all **five frontend SBOM graph tests**, deployed-browser JavaScript
syntax, Helm lint, private/disabled rendered-number checks and `git diff --check` passed.
The latest delta changes verification tooling and its runbook only: full installed Chromium is
selected explicitly; browser failure output is restricted to fixed allowlisted stages; and the
official MCP client performs synthetic begin/append/commit/get through the deployed mount.
No browser-selected upstream, production authentication bypass or sensitive diagnostic output was
introduced. No implementation source was edited by this reviewer.

Independently queried connected GitHub read tools for the exact commit's workflow runs and jobs:
[CI 44884750849](https://github.com/kimonus/knowledge-vault-mcp/actions/runs/44884750849)
is completed successfully; all six jobs and their steps succeeded. Independently fetched and
inspected the supply-chain job's actual logs
[job 136787500082](https://github.com/kimonus/knowledge-vault-mcp/actions/runs/44884750849/job/136787500082).
Checkout reports PR merge `9983562`, merging reviewed source `16dd9f4d` into baseline
`238788297f8266cb7f05a5b6832d490b48824713`; CI is the PR merge validation of that source,
not a reviewer-executed local cluster run.

Those fetched logs establish actual disposable Minikube/containerd/Calico and NGINX TLS ingress
execution, authenticated Chromium login/Explorer/detail/Portal/logout, secure cookie and host/path
confinement, missing-Secret startup refusal, invalid service-bearer readiness/session refusal,
and enforced egress/database isolation with positive controls. Official MCP and HTTP reads/writes
passed after enablement, BFF upgrade, rollback, recovery and removal; unchanged core Pod identity/
restart comparisons and retained external Secrets passed. Post-client API/BFF safe-log inspection
reports zero restarts. The separate image regression passed. Digest-bound throwaway-key registry
signature plus runtime/frontend SBOM attestations passed with wrong-key and unsigned-image
negative controls. CI removed only its disposable profile afterwards.

The archived full CI log independently inspected at `.tooling/pr4-ci-success.log` also reports
706 core tests, 164 BFF tests, 51 web component tests and 54 browser tests passing. These are CI
execution results, separate from this reviewer's narrower reruns.

Remaining boundaries are operational acceptance, not undisclosed implementation passes:
no production homelab rollout, real Cloudflare provider-session expiry, GitHub OIDC/GHCR signed
publication, restored-core browser operation or actual-model concurrent first use was certified.
Embeddings were disabled in the isolated lifecycle fixture. Prior PR2/PR3 finding dispositions
remain preserved. No escalated command, live production resource mutation, GitHub message,
commit or publication was performed by this reviewer.

Next: the root agent incorporates these reports and supplies its final documentation-only diff for independent inspection.

# Web UI PR2 verification and review status

Implementation baseline: PR1 merge `71da6f82130b3348531d23589402853e4b882500`.
Architecture: accepted ADR 0009. This is an implementation verification record, **not an
independent security review**. Independent review remains required before merge; no merge or
deployment is authorized by this record.

The standalone BFF and fixed inspection adapter are implemented. No Explorer/Portal, dashboard
image, chart resource, live Secret, DNS, Access policy, public ingress or cluster deployment is
created. Existing core builds have no dashboard dependency. The BFF uses the maintained pure
auth helper source via build-time source links; its wheel/sdist contain regular helper files.

Local verification uses synthetic data only:

- Full core suite: 694 passed against disposable pgvector, including actual BFF/core compatibility.
  Application coverage: 98.01% statements, 93.05% branches.
- Independent installed BFF package: 144 passed; 97.79% statements, 93.50% branches, including
  bundled auth helpers and entrypoint. Separate CI enforces both thresholds without exclusions.
- Lock checks, Ruff formatting/lint, strict core/BFF Pyright, schema drift check and Bandit pass.
- Source distribution and wheel build with the repository's pinned uv 0.13.7; both bundle regular
  auth files and no core package. The host's older uv 0.9.5 cannot extract the newer backend's
  source distribution metadata; current pinned uv resolves that tooling incompatibility.

Security tests cover exact-read scope rejection on browser records and at the real core boundary;
writer misconfiguration refusal; browser credentials/cookies refused by core HTTP/MCP;
session signature/audience/absolute expiry, record revocation and key rotation; local logout's
copied-cookie limitation; Host/Origin/repeated header/Fetch Metadata/CSRF/encoded path attacks;
Access signature/algorithm/issuer/audience/expiry/email and no private fallback; inert JSON plus
CSP/security headers; no service credential echo; route confinement; no browser identity
forwarding; redirect refusal, bounded upload/response/concurrency/deadline/rates and safe errors.
Core inspection reads leave usage/maintenance counts unchanged; normal writer clients still work.

Remaining accepted limits: all-sensitivity read access, live pagination, process-local single-replica
rates, coarse session revocation after rollout, and no individual copied-cookie logout revocation.
Service-token scope cannot be decoded locally: the authenticated core capabilities request enforces
the service identity and exact-read boundary before every data request. Browser rendering/XSS
execution tests belong to PR3; actual TLS/ingress/NetworkPolicy/deployed-client verification belongs
to PR4. No browser or deployment proof is claimed in PR2. CI results are reported on the PR.

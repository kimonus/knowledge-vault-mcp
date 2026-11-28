# Multi-platform release design — Phase 1

Status: **proposed**, independently reviewed; not implemented. This document does not approve a
release, change the release workflow, or claim support for any deployment platform. It describes
work downstream of [PR #101](https://github.com/kimonus/knowledge-vault-mcp/pull/101) at
`35d691bbe209f452650e2bd57be313cef71ab519`; [Phase 0](standalone-phase-0.md),
[the roadmap](../roadmaps/standalone-deployment.md), [AGENTS.md](../../AGENTS.md), and
[RELEASING.md](../../RELEASING.md) remain controlling. No registry, release, production, corporate
machine, or paid resource was changed by this design.

## Current workflow and evidence

At the reviewed commit, [release.yml](../../.github/workflows/release.yml) verifies versions and
runs existing quality, security, amd64 image, backup/restore, and Kubernetes gates before its
publish job. That job builds both images with BuildKit provenance and SBOM attestations for
`linux/amd64` only. On tag pushes the build action pushes the semantic-version tags **before**
Trivy scans the resulting digests; a failed scan can therefore leave tagged images. It signs each
build output digest and generates one Syft SBOM per image before creating a GitHub release. On a
manual run images are not pushed, signed, or released, but the publish job still logs in to GHCR
and uses GitHub Actions cache. A manual rehearsal must not be described as proving registry
publication or signing, nor automatically assumed to meet the present no-spend boundary.

The [native image run](https://github.com/kimonus/knowledge-vault-mcp/actions/runs/42603464520)
passed application and backup build/dependency checks on reported native amd64 and arm64 Linux
runners at `5e48b1989f0c31c3c22cbcd53bf70d471d2023d4`. It recorded architecture-specific
PyTorch CPU wheel metadata and the architecture-independent sentence-transformers wheel. At
`35d691bbe209f452650e2bd57be313cef71ab519`, an owner-supplied Apple Silicon Podman Machine run
built both arm64 images, verified an independently provisioned model snapshot and exercised offline
inference and backup executables. Its VM and container reported arm64; translation configuration
was not supplied, so independent no-emulation proof is still pending. The test emitted a joblib
read-only-filesystem warning and reported serial-mode fallback, then passed. Neither result proves
multi-platform registry publication, artifact signing, backup recovery on both architectures, or
complete standalone support. [PR #101](https://github.com/kimonus/knowledge-vault-mcp/pull/101)
contains the recorded image IDs, model provenance, exact limits and CI links.

## Proposed verification and publication sequence

1. Keep the existing version agreement, pinned inputs, quality, security, Kubernetes and amd64
   backup/restore gates. Add native amd64 and arm64 checks for **both** images and the actual
   backup/restore behavior. Exercise a separately provisioned, checksum-verified model offline;
   tests and running Pods never download model weights. Record host, runner, architecture,
   dependency wheel and source commit evidence; do not substitute emulation for native execution.
2. Construct candidate app and backup multi-platform OCI indexes from the reviewed commit. For
   each, resolve its immutable index digest and require exactly one intended `linux/amd64` and
   one intended `linux/arm64` runnable image child. Reject missing, duplicate, unexpected or
   mismatched runnable platform descriptors, invalid digests and malformed manifests. BuildKit
   attestation descriptors must be handled separately rather than counted as runnable images.
   Select children by digest, never by mutable tag or the scanning host's default platform.
3. Before signing, scan **each of the four immutable child digests** with the pinned policy and
   produce an SBOM for each child. Check the candidate's exact bytes, child and attachment
   descriptors, and intended source and predicate fields for consistency. Preserve the BuildKit
   attachments during assembly and recheck the final candidate index and subjects. These checks
   are pre-sign consistency checks, not authenticated provenance. Fail closed on absent, malformed,
   ambiguous, or inconsistent evidence, and pin the tools and formats used.
4. After the pre-sign checks pass and only within an authorized release, sign the exact assembled
   immutable index digests, then **verify** each signature before any semantic-tag promotion.
   Enforce the expected exact source-tag release-workflow certificate identity, GitHub OIDC issuer,
   signed digest claim, trusted Sigstore roots and applicable transparency-log and timestamp policy,
   following the existing identity contract in RELEASING.md. For a short-lived Fulcio certificate,
   verify its validity at an authenticated signing or integration time established by the accepted
   transparency or trusted-timestamp proof, not against the current wall-clock time alone; a
   certificate that has since expired can still validate. See [Sigstore timestamp verification](https://docs.sigstore.dev/cosign/verifying/timestamps/).
   Pin the verifier version and securely provision and rotate trust material. Fail closed on a
   missing, forged, wrong-identity or untrusted signature; invalid trust material; a certificate
   invalid at the verified time; or missing or invalid required time or log proof. Do not bypass
   claims, transparency-log or certificate-transparency checks in production for a passing rehearsal.
   Decide and document whether the consumer contract additionally requires child signatures; an
   index signature is not an independently verified signature on each child.
   Publish machine-readable index-to-child digest mappings, per-child SBOMs, checksums, and
   verification instructions in the release artifacts. Update the existing chart and homelab
   guidance without weakening digest-pinned deployments or existing amd64 gates.
5. Only a maintainer-authorized release may perform registry writes, signing, tag promotion or
   GitHub release creation. Once the candidate contents and all required evidence are verified,
   promote semantic tags to the already-tested immutable indexes, verify the resulting tag-to-index
   and index-to-child mappings, and then create the release. An exact version tag must not be
   silently overwritten; handle an existing conflicting tag as a failure. The moving minor tag
   must remain consistent with the documented release policy. Multi-image, multi-tag registry
   updates may not be atomic: define collision, partial-promotion and recovery behavior before
   implementation, never claim atomicity without a tested registry guarantee. Consumers deploy
   verified immutable digests, not mutable tags.

The current [release procedure](../../RELEASING.md) explicitly distinguishes BuildKit's OCI
attachments from GitHub artifact attestations. BuildKit [stores attestations in manifests attached
to the index](https://docs.docker.com/build/metadata/attestations/attestation-storage/); merely
reading their builder/source fields or running `cosign verify-attestation` is not a demonstrated
verification of those attachments. For the proposed index-signature trust model, hash and verify
the exact signed index, each selected child descriptor, each attached attestation manifest and its
in-toto blob, including descriptor sizes, digests, predicate type, source and subject binding.
Recheck the resulting index and attachment relationships after assembly; do not rely on descriptor
annotations alone. This establishes that the authenticated **release signer vouched for the
bound contents**. It does not independently establish that the builder named inside an unsigned
BuildKit statement produced them. If independent builder authentication is required, define and
verify a separately signed builder statement and trust policy before promotion. Tool behavior,
registry referrer/attachment representation, and the precise bytes bound by the signature remain
implementation and rehearsal gates, not claims already established by this design.

Candidate storage itself is a registry write even when no public version tag exists. When the
owner authorizes that write, a candidate may be stored in the approved registry **before** scans
so its immutable child digests can be scanned; no semantic version tag is promoted until all
required checks pass. Keyless signing can contact external services. A nonpublishing rehearsal
must perform neither registry writes nor external signing unless the owner separately authorizes
the operation. The maintainer alone pushes the signed
`vX.Y.Z` source tag under the existing release procedure; this proposal does not authorize it.

## Nonpublishing rehearsal and acceptance

Implement a bounded offline fixture suite that builds synthetic OCI indexes and attestations in
temporary storage, exercises descriptor resolution and subject/identity checks, and uses fakes for
registry operations. Include negative cases for wrong architecture, missing/duplicate children,
wrong digest or subject, missing or malformed attestations, forged or untrusted identities,
broken post-assembly binding, a failed child scan, stale or conflicting tag, and partial promotion.
Assert that no signing, tag, GH release, registry mutation, credential
use, paid cache or artifact upload occurs in rehearsal. Fixtures validate control flow and can
exercise verification against test signatures; they do not prove production-signature trust,
real registry semantics, vulnerability scan coverage, or publication.

For offline cryptographic-verifier tests, use an already-generated, nonsecret signed fixture,
verification bundle and **test-only** trusted root. Verify success and reject wrong candidate digest,
identity or issuer, tampered signature or bundle, missing or invalid trust material, a certificate
invalid at the verified signing time, and missing or invalid required authenticated time or log
proof. Include a valid signature whose short-lived certificate has since expired but was valid at
the authenticated time. Run the verifier with network access denied and prove it does not fetch
registry data, TUF metadata, Fulcio, or Rekor; `--local-image` alone is not proof of offline
operation. A local test-key fixture checks signature-handling behavior but does not prove Fulcio
OIDC identity, Rekor inclusion or the actual registry publication path. Never use fixture roots as
production roots. Establish a reviewed out-of-band production-root provisioning, freshness,
rotation and revocation policy: an offline snapshot cannot reveal a later revocation. GitHub's
[offline artifact-attestation procedure](https://docs.github.com/en/actions/how-tos/secure-your-work/use-artifact-attestations/verify-attestations-offline)
describes a different attestation type and is not evidence that `gh attestation verify` verifies
the current BuildKit attachments. Rehearsal must not change production verification flags.

The first offline helper, `python scripts/ci/oci_index.py INDEX.json`, checks a bounded OCI image
index and prints the two image descriptor digests. It accepts baseline `amd64/v1` and `arm64/v8`,
rejects ambiguous or unsupported platform requirements, and classifies the documented BuildKit
`unknown/unknown` attestation descriptors. This is a narrow policy, not a general OCI parser; it
does not fetch blobs, validate manifest contents, check signatures, or authenticate attestations.
It is not integrated into the release workflow and cannot authorize promotion. Later gates must
verify the index and child content digests and establish trusted attestation bindings separately.

`python scripts/ci/oci_index.py --verify-attestations INDEX.json EXPECTED_SHA256 BLOBS_DIR`
also checks the byte-digest and declared-size chain from the supplied index through selected child
manifests and BuildKit attachment manifests to their in-toto statements. It requires each child to
have exactly one SLSA v0.2 provenance statement and one SPDX document statement, each naming that
child's SHA-256 as its sole subject. It accepts in-toto v0.1 and v1 statements, and either BuildKit
legacy image-config or OCI artifact attestation manifests; unknown forms, predicate types and
ambiguous subject lists fail closed. It checks declared predicate annotations when present. This
deliberately narrower policy does not assert general OCI conformance or validate the predicate
schema, source revision, builder, image configs, layers, signatures, or trust in the supplied index
digest. An attacker-controlled but internally consistent graph can pass. It does not fetch files or
authenticate any claim and must not gate releases by itself. Parent directory path components may
be symbolic links; the operator must supply a trusted local directory. Reads reject symlink leaves,
non-regular files, and excess sizes; attestation statements and manifests are limited to 8 MiB
each, with at most 32 statement layers and 64 MiB total attachment bytes.
The 8 MiB per-blob limit is stricter than BuildKit's documented 80 MiB pre-wrap attestation input
limit. The current fixtures are synthetic, so real producer output and suitable bounds must be
validated before release integration.

These narrow choices follow the documented [BuildKit attestation layout](https://docs.docker.com/build/metadata/attestations/attestation-storage/)
and its [default SLSA v0.2 provenance format](https://docs.docker.com/build/metadata/attestations/slsa-provenance/).
They still require validation against the actual release producer before use.

The optional offline mode `python scripts/ci/oci_index.py --verify INDEX.json EXPECTED_SHA256
BLOBS_DIR` additionally hashes the exact selected index bytes against the separately supplied
digest and verifies the size and SHA-256 of each runnable child in `BLOBS_DIR` (the local
`blobs/sha256` directory). The index path must identify the intended candidate index itself; an
OCI layout's `index.json` entrypoint may instead reference another index blob. Both files and the
blob directory reject final-component symlinks, child files must be regular, and reads are bounded
to 1 MiB for the index and 8 MiB per child. Parent path components are not checked for symlinks.
This establishes byte consistency with the supplied digest only: the digest's trust, manifest
structure, image configurations, layers, signatures, and attestations are not validated. No
result from this mode authorizes a release or establishes a platform claim.

Run production-equivalent builds, per-platform tests, backup/restore and scans on disposable,
authorized native environments without using shared production data. Record exact tools, source,
runner architectures, image and child digests, scan outcomes and evidence. A full real-registry
promotion/signature rehearsal remains explicitly **unverified** until an authorized registry and
owner permission are available. Never publish merely to close a test gap.

Before advancing Phase 1, an independent reviewer must verify the implementation and test results,
the native ARM execution evidence and its translation configuration, all release supply-chain
checks, and existing amd64 gates. Phase 2 remains gated by the roadmap; passing this proposed design
is not Phase 1 completion.

## Risk register and open design decisions

| Risk | Required control or unresolved decision |
| --- | --- |
| A scanner or SBOM tool silently selects the host architecture from an index. | Resolve and scan each platform child digest explicitly; test mismatches and missing children. |
| Index composition drops attestations, signs a different subject, or trusts forged provenance metadata. | Verify the signer, digest and complete attachment hash chain after signing; distinguish signer-vouched contents from independently authenticated builder provenance, and decide a child-signature policy. |
| A failed scan leaves an already published version tag. | Gate promotion on all scans; decide an authorized candidate store and test interrupted or partial promotion. |
| Existing tag is overwritten or app/backup tags diverge. | Fail on immutable-version conflicts; specify safe idempotent recovery for every partial state and test registry behavior. |
| A manual rehearsal causes registry writes, cache/artifact charges, or consumes secrets. | Separate rehearsal and publishing permissions/steps; assert absence of these effects and use local disposable fixtures. |
| Release succeeds only on amd64 or emulator masquerades as native. | Record runner and VM configuration, native dependency/inference evidence, and native backup/restore on both architectures. |
| Current Mac `joblib` warning hides a functional or performance issue. | Investigate serial fallback and its performance implications; do not assert impact or production readiness from one smoke. |
| A failed upgrade or promotion triggers unsafe rollback. | Follow existing digest rollback and database restore procedure; never delete a published tag or assume schema downgrades are safe. |

Open decisions include the authorized candidate registry or alternative, platform-child signature
requirements, whether independent builder authentication is required, exact signature/attachment
verification tooling and trusted-root lifecycle, moving minor-tag semantics, and how to demonstrate
all intended tags under a partial registry failure. No decision
above authorizes a weaker security boundary, a release, or a paid service.

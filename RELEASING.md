# Releasing

The [local standalone Docker workflow](docs/runbooks/docker-standalone.md) also accepts complete
local source-build image IDs. This permits offline functional verification without publishing a
release or changing registry tags. Such IDs are not signed releases or evidence of multi-platform
support. Registry-based installation continues to require the signature/digest checks below;
the historical multi-platform design remains separate until its publication gates are implemented.

Only the maintainer publishes releases. Releases use semantic version tags (`vMAJOR.MINOR.PATCH`)
and are produced by the tag-driven GitHub Actions workflow.

## Prepare

1. Move relevant entries from `Unreleased` in [CHANGELOG.md](CHANGELOG.md) into a dated release.
2. Set the same version, without the `v` prefix, in:
   - `pyproject.toml`;
   - `charts/knowledge-vault/Chart.yaml` (`version` and `appVersion`); and
   - `plugin/knowledge-vault/.codex-plugin/plugin.json`.
3. Update migrations, contracts, runbooks, threat model, and rollback notes for behavioral changes.
4. Run every gate documented in [CONTRIBUTING.md](CONTRIBUTING.md).
5. Restore the release-candidate backup into a disposable PostgreSQL instance. A successful backup
   alone is not a recovery test.

## Rehearse

Run the release workflow manually before the first tag and after changing it:

```bash
gh workflow run release.yml --ref main
gh run watch
```

A manual run executes every verification step, builds all three images, and packages the chart, but
pushes or releases nothing. Its signing test uses a disposable loopback registry and throwaway
key; it never publishes a signature or attestation to GHCR or a transparency service.

The first rehearsal (2026-10-02) caught the secret scan running after the tests and flagging
their cached fixtures; the scan now runs on the fresh checkout.

## Tag and publish

Create an annotated, signed tag from the reviewed `main` commit—the one the rehearsal passed
on—and push only that tag:

```bash
git tag -s vX.Y.Z -m "Knowledge Vault vX.Y.Z"
git push origin vX.Y.Z
```

When signing from SSH, set GPG's terminal in the same interactive shell before creating the tag:

```bash
export GPG_TTY="$(tty)"
gpg-connect-agent updatestartuptty /bye
```

If `git tag -s` reports `gpg: signing failed: Inappropriate ioctl for device`, pinentry could not
use the terminal. Confirm that `tty` prints a terminal such as `/dev/pts/0` (not `not a tty`), run
the two commands above, and retry `git tag -s`. A failed signing attempt does not create the tag;
the draft message in `.git/TAG_EDITMSG` is not a tag. Verify the signature with
`git tag -v vX.Y.Z` before pushing. Run these commands in your interactive terminal, not through
a non-interactive agent command runner.

`git tag -s` signs with the key named by this repository's `user.signingkey`. Use a key made for
releases whose identity matches the commit identity, and register its public half with the
hosting account so the tag shows as verified. Release 0.1.0 was tagged without a signature,
before that key existed; later tags are signed.

The release workflow verifies the tag/version agreement, reruns quality and security gates, and
then publishes:

- `ghcr.io/kimonus/knowledge-vault-mcp`;
- `ghcr.io/kimonus/knowledge-vault-mcp-backup`;
- `ghcr.io/kimonus/knowledge-vault-mcp-dashboard` (independent optional BFF/SPA image);
- OCI provenance and SBOM attestations for all three images;
- keyless Sigstore signatures for all three immutable image digests;
- Dashboard runtime and frontend CycloneDX SBOMs, each signed as an attestation against the
  Dashboard digest, alongside the existing core/backup SBOMs; and
- the packaged Helm chart attached to the GitHub release.

The workflow scans the pushed digests before signing them, and publishes version tags
(`MAJOR.MINOR.PATCH` and `MAJOR.MINOR`) but no `latest` tag. Version tags are conveniences only.
Kubernetes deployments must pin the reported `sha256` image digest.

Release 0.1.0 is the exception: its workflow still let the metadata action add `latest`, so both
0.1.0 images also carry that tag. Later releases do not move it. Do not use it.

## Verify

Inspect the release and copy the digest from GHCR. Verify a signature with the expected workflow
identity:

```bash
cosign verify \
  --certificate-identity-regexp \
  '^https://github.com/kimonus/knowledge-vault-mcp/.github/workflows/release.yml@refs/tags/v[0-9]+\.[0-9]+\.[0-9]+$' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  ghcr.io/kimonus/knowledge-vault-mcp@sha256:RELEASE_DIGEST
```

Without a local `cosign`, the pinned container image does the same; give it a writable home:

```bash
docker run --rm --read-only --tmpfs /tmp:mode=1777 -e HOME=/tmp -e TUF_ROOT=/tmp/tuf \
  ghcr.io/sigstore/cosign/cosign:v3.1.3 verify \
  --certificate-identity \
  'https://github.com/kimonus/knowledge-vault-mcp/.github/workflows/release.yml@refs/tags/vX.Y.Z' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  ghcr.io/kimonus/knowledge-vault-mcp@sha256:RELEASE_DIGEST
```

Provenance and SBOM are BuildKit attestations stored in the image index, not GitHub artifact
attestations, so `gh attestation verify` does not find them.

Confirm the SBOM/provenance attestations in GHCR, install the packaged chart into a disposable
namespace by digest, run an authenticated MCP tools/list and search/fetch smoke test, and repeat the
backup/restore exercise before promoting the release to the homelab.

Download the chart, all four SBOMs, and `SHA256SUMS` into one directory, then run
`sha256sum -c SHA256SUMS` there. Releases through v0.6.0 recorded CI runner paths in that file;
for those historical releases, compare each published hash with the downloaded file of the same
basename instead.

## Rollback

Roll back application and backup images to the previous verified digests. Database rollback is a
separate decision: never reverse a migration unless its downgrade path and data-loss implications
were tested. If compatibility is uncertain, stop writes, preserve the volume, restore the last
verified off-host backup into a disposable database, and follow
[the migration runbook](docs/runbooks/migrations.md).

Never delete a published tag or silently replace an image under an existing version. Publish a new
patch version for corrections.

## Optional dashboard package

The [Dashboard](docs/runbooks/dashboard.md) has independent Python/frontend locks, wheel tests,
browser tests and `dashboard/Dockerfile`. Its final image includes compiled assets and a frontend
SBOM, with no Node or core runtime. CI and release rehearsal build, scan and inventory it
separately; release verification retains the PR2/PR3 browser, package and image regression tests.
The tag workflow publishes it independently without enabling it in the chart. Core/API/worker
images do not contain it. Its package/frontend versions are independently maintained; the release
image tag follows the repository release version and the OCI metadata action labels that version.

The runtime SBOM inventories Python and OS packages. The frontend inventory is extracted from
the exact image being published; it inventories the full installed dependency graph restricted
to the closure of exact production package pins, including shared runtime peers and their
transitive dependencies. Development-only roots are excluded; ambiguous/missing pins and dangling
graph references fail the build. Both inventories must contain the expected packages or the release fails. A runtime image scan
does not replace `npm audit`, which also runs on the locked frontend before publication.

Before deploying the optional image, verify its digest signature using the command above with
`knowledge-vault-mcp-dashboard@sha256:DASHBOARD_DIGEST`. Then verify its signed SBOM attestations:

```bash
cosign verify-attestation --type cyclonedx \
  --certificate-identity \
  'https://github.com/kimonus/knowledge-vault-mcp/.github/workflows/release.yml@refs/tags/vX.Y.Z' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  ghcr.io/kimonus/knowledge-vault-mcp-dashboard@sha256:DASHBOARD_DIGEST \
  > dashboard-attestations.jsonl
jq -esr '[.[] | .payload | @base64d | fromjson] |
  any(.[]; any(.predicate.components[]; .name == "fastapi")) and
  any(.[]; any(.predicate.components[]; .name == "react"))' dashboard-attestations.jsonl
```

Use the exact reviewed tag in the certificate identity. Cosign validates signatures and digest
subjects; the second command checks that both runtime and frontend inventories were returned.
Compare the decoded CycloneDX predicates with the downloaded release inventories as well as
checking `SHA256SUMS`. BuildKit SBOM/provenance attestations remain separate from these explicitly
signed Dashboard CycloneDX attestations.

Signed publication uses GitHub OIDC on the maintainer-pushed release tag. A branch rehearsal
does not prove that provider identity or publish a signed release. A disposable local-registry
rehearsal can test the digest/signature/SBOM mechanics with a throwaway key; it cannot substitute
for verifying the eventual GHCR release under the expected workflow certificate identity.

With Docker, pinned `cosign`/`syft` from the CI installers and `jq` on `PATH`, run the same bounded
mechanical rehearsal locally against a review-built image:

```bash
timeout 300 sh scripts/ci/rehearse_dashboard_signing.sh knowledge-vault-dashboard:review
```

This pulls the digest-pinned registry test image, binds it only on loopback at a random port,
pushes a local copy of the image, signs it and both SBOMs, verifies their subjects, and rejects
the wrong key and a different unsigned manifest. Cleanup removes only the registry/container IDs,
temporary tags and key files it created. A successful result proves these mechanics, not GitHub
OIDC, GHCR availability, release identity or a live deployment.

Deploy, upgrade, roll back or remove only Dashboard resources through the
[Dashboard runbook](docs/runbooks/dashboard.md). Enabling it requires operator authorization,
private TLS and separately provisioned Secrets; publication itself does not authorize deployment.

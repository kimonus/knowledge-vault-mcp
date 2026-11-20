#!/bin/sh
# Disposable loopback registry and throwaway key: no production registry, OIDC or live cluster.
# Requires cosign, syft, jq and Docker. Bounded by the caller's timeout (CI uses timeout 300).
set -eu
image=${1:?dashboard image required}
registry_image=${2:-registry:3.0.0@sha256:6c5666b861f3505b116bb9aa9b25175e71210414bd010d92035ff64018f9457e}
work=$(mktemp -d)
registry_id=
unsigned_id=
unsigned_image=
local_tag=
unsigned_tag=
cleanup() {
  if [ -n "$unsigned_id" ]; then docker rm "$unsigned_id" > /dev/null; fi
  if [ -n "$registry_id" ]; then docker rm --force "$registry_id" > /dev/null; fi
  if [ -n "$local_tag" ]; then docker image rm "$local_tag" > /dev/null; fi
  if [ -n "$unsigned_tag" ]; then docker image rm "$unsigned_tag" > /dev/null; fi
  if [ -n "$unsigned_image" ] && docker image inspect "$unsigned_image" > /dev/null 2>&1; then
    docker image rm "$unsigned_image" > /dev/null
  fi
  rm -rf "$work"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
registry_id=$(docker run --detach --publish 127.0.0.1::5000 "$registry_image")
port=$(docker inspect "$registry_id" --format '{{(index (index .NetworkSettings.Ports "5000/tcp") 0).HostPort}}')
repository="127.0.0.1:$port/dashboard"
local_tag="$repository:rehearsal"
# Registry startup and all network operations are bounded by the enclosing timeout.
attempt=0
until curl --fail --silent "http://127.0.0.1:$port/v2/" > /dev/null; do
  attempt=$((attempt + 1))
  [ "$attempt" -lt 30 ] || exit 1
  sleep 1
done
docker tag "$image" "$local_tag"
docker push "$local_tag" > /dev/null
subject=$(docker image inspect "$local_tag" --format '{{json .RepoDigests}}' |
  jq -er --arg prefix "$repository@" 'map(select(startswith($prefix))) | .[0]')
digest=${subject##*@sha256:}
export COSIGN_PASSWORD=
cosign generate-key-pair --output-key-prefix "$work/key" > /dev/null
sh scripts/ci/dashboard_sbom.sh "$subject" "$work/sbom"
cosign sign --yes --key "$work/key.key" --use-signing-config=false \
  --tlog-upload=false "$subject" > /dev/null
for inventory in runtime frontend; do
  cosign attest --yes --key "$work/key.key" --use-signing-config=false \
    --tlog-upload=false --type cyclonedx \
    --predicate "$work/sbom/$inventory.sbom.cdx.json" "$subject" > /dev/null
done
cosign verify --key "$work/key.pub" --insecure-ignore-tlog "$subject" > "$work/signature.json"
cosign verify-attestation --key "$work/key.pub" --insecure-ignore-tlog --type cyclonedx \
  "$subject" > "$work/attestations.jsonl"
jq -es --arg digest "$digest" \
  '[.[] | .payload | @base64d | fromjson] |
  all(.[]; any(.subject[]; .digest.sha256 == $digest)) and
  any(.[]; any(.predicate.components[]; .name == "fastapi")) and
  any(.[]; any(.predicate.components[]; .name == "react"))' \
  "$work/attestations.jsonl" > /dev/null
cosign generate-key-pair --output-key-prefix "$work/wrong-key" > /dev/null
if cosign verify --key "$work/wrong-key.pub" --insecure-ignore-tlog "$subject" > /dev/null 2>&1; then
  echo 'Wrong signing key was accepted' >&2
  exit 1
fi
# A distinct, real unsigned manifest must not inherit the dashboard signature or attestations.
unsigned_id=$(docker create "$image")
unsigned_image=$(docker commit --change 'LABEL io.knowledge-vault.signing-rehearsal="unsigned"' "$unsigned_id")
unsigned_tag="127.0.0.1:$port/unsigned:rehearsal"
docker tag "$unsigned_image" "$unsigned_tag"
docker push "$unsigned_tag" > /dev/null
if cosign verify --key "$work/key.pub" --insecure-ignore-tlog "$unsigned_tag" > /dev/null 2>&1; then
  echo 'Unsigned image inherited a signature' >&2
  exit 1
fi
if cosign verify-attestation --key "$work/key.pub" --insecure-ignore-tlog --type cyclonedx \
  "$unsigned_tag" > /dev/null 2>&1; then
  echo 'Unsigned image inherited SBOM attestations' >&2
  exit 1
fi
echo "Verified dashboard signature, runtime/frontend SBOM subjects and wrong-key/unsigned-image rejection: $subject"

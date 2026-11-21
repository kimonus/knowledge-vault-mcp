#!/bin/sh
# Inventory the runtime and locked dependencies bundled into compiled JavaScript separately.
set -eu
image=${1:?dashboard image required}
destination=${2:?output directory required}
mkdir -p "$destination"
syft "$image" -o "cyclonedx-json=$destination/runtime.sbom.cdx.json"
docker run --rm --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges --pids-limit 64 --memory 128m \
  --entrypoint cat "$image" /usr/share/knowledge-vault-dashboard/frontend.sbom.json \
  > "$destination/frontend.sbom.cdx.json"
# Empty inventories must fail rather than appear complete.
jq -e '.bomFormat == "CycloneDX" and
  any(.components[]; .name == "fastapi") and
  any(.components[]; .name == "knowledge-vault-dashboard")' \
  "$destination/runtime.sbom.cdx.json" > /dev/null
jq -e '.bomFormat == "CycloneDX" and
  any(.components[]; .name == "react") and
  any(.components[]; .name == "react-dom") and
  any(.components[]; .name == "react-router-dom")' \
  "$destination/frontend.sbom.cdx.json" > /dev/null

"""Inspect an OCI release index offline; this does not authenticate or fetch content.

This intentionally implements a narrow release policy, not a general OCI parser.
Attestation descriptors are classified only; their contents and identity remain unverified.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

INDEX = "application/vnd.oci.image.index.v1+json"
MANIFEST = "application/vnd.oci.image.manifest.v1+json"
REFERENCE_TYPE = "vnd.docker.reference.type"
REFERENCE_DIGEST = "vnd.docker.reference.digest"
DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
MAX_BYTES = 1024 * 1024
MAX_DESCRIPTORS = 64
PLATFORMS = {("linux", "amd64"), ("linux", "arm64")}


class IndexError(ValueError):
    """The index does not meet the narrow release policy."""


def _digest(value: object) -> str:
    if not isinstance(value, str) or not DIGEST.fullmatch(value):
        raise IndexError("invalid sha256 digest")
    return value


def resolve_index(index: object) -> dict[str, str]:
    """Return platform-to-child digests; inspect no blobs and verify no attestations."""
    if not isinstance(index, dict) or type(index.get("schemaVersion")) is not int:
        raise IndexError("invalid index schema version")
    if index["schemaVersion"] != 2:
        raise IndexError("invalid index schema version")
    if index.get("mediaType") != INDEX:
        raise IndexError("unsupported index media type")
    if "artifactType" in index:
        raise IndexError("artifact index is not a release image index")
    entries = index.get("manifests")
    if not isinstance(entries, list) or len(entries) > MAX_DESCRIPTORS:
        raise IndexError("invalid or oversized manifest list")
    images: dict[str, str] = {}
    references: list[str] = []
    descriptor_digests: set[str] = set()
    for item in entries:
        if not isinstance(item, dict) or item.get("mediaType") != MANIFEST:
            raise IndexError("unsupported manifest descriptor")
        digest = _digest(item.get("digest"))
        if digest in descriptor_digests:
            raise IndexError("duplicate descriptor digest")
        descriptor_digests.add(digest)
        size = item.get("size")
        if isinstance(size, bool) or not isinstance(size, int) or not 0 < size < 2**63:
            raise IndexError("invalid descriptor size")
        platform = item.get("platform")
        if not isinstance(platform, dict):
            raise IndexError("missing platform")
        annotations = item.get("annotations", {})
        if not isinstance(annotations, dict) or any(
            not isinstance(k, str) or not isinstance(v, str) for k, v in annotations.items()
        ):
            raise IndexError("invalid annotations")
        os_name, arch = platform.get("os"), platform.get("architecture")
        if not isinstance(os_name, str) or not isinstance(arch, str):
            raise IndexError("invalid platform")
        reference_type = annotations.get(REFERENCE_TYPE)
        if reference_type is not None or REFERENCE_DIGEST in annotations:
            if reference_type != "attestation-manifest" or (os_name, arch) != (
                "unknown",
                "unknown",
            ):
                raise IndexError("invalid attestation descriptor")
            references.append(_digest(annotations.get(REFERENCE_DIGEST)))
            continue
        if "artifactType" in item:
            raise IndexError("artifact descriptor cannot be a runnable image")
        if set(platform) - {"os", "architecture", "variant"}:
            raise IndexError("unsupported platform constraints")
        variant = platform.get("variant")
        if (os_name, arch) not in PLATFORMS or variant not in (
            (None, "", "v8") if arch == "arm64" else (None, "", "v1")
        ):
            raise IndexError("unexpected platform or variant")
        key = f"{os_name}/{arch}"
        if key in images:
            raise IndexError("duplicate platform")
        images[key] = digest
    if set(images) != {"linux/amd64", "linux/arm64"}:
        raise IndexError("missing required platform")
    if any(reference not in images.values() for reference in references):
        raise IndexError("attestation references an unknown image")
    return images


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise IndexError("duplicate JSON key")
        result[key] = value
    return result


def _invalid_constant(_value: str) -> None:
    raise IndexError("invalid JSON constant")


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: python scripts/ci/oci_index.py INDEX.json", file=sys.stderr)
        return 2
    try:
        with Path(sys.argv[1]).open("rb") as source:
            content = source.read(MAX_BYTES + 1)
        if len(content) > MAX_BYTES:
            raise IndexError("index exceeds byte limit")
        index = json.loads(
            content, object_pairs_hook=_unique_object, parse_constant=_invalid_constant
        )
        print(json.dumps(resolve_index(index), sort_keys=True))
    except (OSError, UnicodeDecodeError, ValueError, RecursionError) as exc:
        print(f"invalid OCI index: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

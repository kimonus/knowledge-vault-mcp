"""Inspect a local OCI export without extracting untrusted paths or authenticating it."""

from __future__ import annotations

import json
import re
import sys
import tarfile
from pathlib import Path

from scripts.ci import oci_index

MAX_MEMBERS = 4096
MAX_STAGED_BYTES = 128 * 1024 * 1024
BLOB_PATH = re.compile(r"blobs/sha256/[0-9a-f]{64}\Z")


def inspect_archive(archive_path: str, staging_path: str) -> dict[str, object]:
    """Stage bounded metadata, resolve the wrapped index, and check attachment consistency.

    The candidate digest is obtained from the archive itself and is not a trust anchor.
    Image layers, configuration, signatures, identity, and predicate contents are not verified.
    """
    staging = Path(staging_path)
    staging.mkdir(mode=0o700)  # A fresh directory is mandatory; never overwrite existing files.
    blobs = staging / "blobs"
    blobs.mkdir(mode=0o700)
    seen: set[str] = set()
    total = 0
    with tarfile.open(archive_path, mode="r:") as archive:
        for count, member in enumerate(archive, start=1):
            if count > MAX_MEMBERS:
                raise oci_index.IndexError("archive has too many members")
            name = member.name.removeprefix("./")
            if member.isdir() and name in (".", "", "blobs", "blobs/sha256"):
                continue
            if not member.isfile() or name in seen:
                raise oci_index.IndexError("unsupported or duplicate archive member")
            seen.add(name)
            if name == "index.json":
                destination = staging / "index.json"
                limit = oci_index.MAX_BYTES
            elif name == "oci-layout":
                destination = staging / "oci-layout"
                limit = oci_index.MAX_BYTES
            elif BLOB_PATH.fullmatch(name):
                if member.size > oci_index.MAX_MANIFEST_BYTES:
                    continue  # Image layers can be large; the helper requires metadata blobs.
                destination = blobs / name.rsplit("/", 1)[1]
                limit = oci_index.MAX_MANIFEST_BYTES
            else:
                raise oci_index.IndexError("unexpected archive path")
            if member.size < 0 or member.size > limit:
                raise oci_index.IndexError("archive metadata exceeds limit")
            total += member.size
            if total > MAX_STAGED_BYTES:
                raise oci_index.IndexError("archive metadata total exceeds limit")
            source = archive.extractfile(member)
            if source is None:
                raise oci_index.IndexError("archive metadata unavailable")
            with source:
                content = source.read(limit + 1)
            if len(content) != member.size:
                raise oci_index.IndexError("truncated archive metadata")
            with destination.open("xb") as output:
                output.write(content)

    layout = oci_index._parse_json(
        oci_index._read_regular(str(staging / "oci-layout"), oci_index.MAX_BYTES)
    )
    if not isinstance(layout, dict) or layout.get("imageLayoutVersion") != "1.0.0":
        raise oci_index.IndexError("invalid OCI layout version")
    root = oci_index._parse_json(
        oci_index._read_regular(str(staging / "index.json"), oci_index.MAX_BYTES)
    )
    if not isinstance(root, dict) or type(root.get("schemaVersion")) is not int:
        raise oci_index.IndexError("invalid OCI export entrypoint")
    entries = root.get("manifests")
    if (
        root["schemaVersion"] != 2
        or root.get("mediaType") != oci_index.INDEX
        or not isinstance(entries, list)
        or len(entries) != 1
    ):
        raise oci_index.IndexError("expected exactly one candidate index")
    candidate_digest, candidate_size = oci_index._descriptor(
        entries[0], oci_index.INDEX, oci_index.MAX_BYTES
    )
    candidate = blobs / candidate_digest.removeprefix("sha256:")
    data = oci_index._read_regular(str(candidate), oci_index.MAX_BYTES)
    if len(data) != candidate_size or oci_index._sha256(data) != candidate_digest:
        raise oci_index.IndexError("candidate index digest or size mismatch")
    images = oci_index.verify_integrity(str(candidate), candidate_digest, str(blobs))
    predicates = oci_index.verify_attestation_bindings(str(candidate), candidate_digest, str(blobs))
    return {"candidate_index": candidate_digest, "images": images, "predicates": predicates}


def main() -> int:
    if len(sys.argv) != 3:
        print(
            "usage: python -m scripts.ci.oci_archive ARCHIVE.tar NEW_STAGING_DIR", file=sys.stderr
        )
        return 2
    try:
        print(json.dumps(inspect_archive(sys.argv[1], sys.argv[2]), sort_keys=True))
    except (OSError, ValueError, RecursionError, tarfile.TarError) as exc:
        print(f"invalid OCI archive: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

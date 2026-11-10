"""Inspect an OCI release index offline; this does not authenticate or fetch content.

This intentionally implements a narrow release policy, not a general OCI parser.
Attestation graph consistency is optional; identity and authenticity remain unverified.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path
from typing import Any

INDEX = "application/vnd.oci.image.index.v1+json"
MANIFEST = "application/vnd.oci.image.manifest.v1+json"
REFERENCE_TYPE = "vnd.docker.reference.type"
REFERENCE_DIGEST = "vnd.docker.reference.digest"
DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
MAX_BYTES = 1024 * 1024
MAX_MANIFEST_BYTES = 8 * 1024 * 1024
MAX_DESCRIPTORS = 64
MAX_ATTESTATION_LAYERS = 32
MAX_ATTESTATION_TOTAL = 64 * 1024 * 1024
PLATFORMS = {("linux", "amd64"), ("linux", "arm64")}
INTOTO = "application/vnd.in-toto+json"
ARTIFACT = "application/vnd.docker.attestation.manifest.v1+json"
OCI_EMPTY = "application/vnd.oci.empty.v1+json"
OCI_CONFIG = "application/vnd.oci.image.config.v1+json"
PREDICATE_ANNOTATION = "in-toto.io/predicate-type"
PREDICATES = frozenset({"https://slsa.dev/provenance/v0.2", "https://spdx.dev/Document"})
STATEMENTS = frozenset({"https://in-toto.io/Statement/v0.1", "https://in-toto.io/Statement/v1"})


class IndexError(ValueError):
    """The index does not meet the narrow release policy."""


def _digest(value: object) -> str:
    if not isinstance(value, str) or not DIGEST.fullmatch(value):
        raise IndexError("invalid sha256 digest")
    return value


def _resolve_descriptors(index: object) -> dict[str, tuple[str, int]]:
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
    images: dict[str, tuple[str, int]] = {}
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
        images[key] = (digest, size)
    if set(images) != {"linux/amd64", "linux/arm64"}:
        raise IndexError("missing required platform")
    if any(reference not in {digest for digest, _ in images.values()} for reference in references):
        raise IndexError("attestation references an unknown image")
    return images


def resolve_index(index: object) -> dict[str, str]:
    """Return platform-to-child digests; inspect no blobs and verify no attestations."""
    return {platform: descriptor[0] for platform, descriptor in _resolve_descriptors(index).items()}


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise IndexError("duplicate JSON key")
        result[key] = value
    return result


def _invalid_constant(_value: str) -> None:
    raise IndexError("invalid JSON constant")


def _read_regular(path: str, limit: int, *, dir_fd: int | None = None) -> bytes:
    """Read bounded exact bytes from a regular file, never following its final symlink."""
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    fd = os.open(path, flags, dir_fd=dir_fd)
    with os.fdopen(fd, "rb") as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise IndexError("file is not regular or exceeds byte limit")
        content = source.read(limit + 1)
        if len(content) > limit:
            raise IndexError("file exceeds byte limit")
        return content


def _sha256(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def verify_integrity(index_path: str, expected_digest: str, blob_directory: str) -> dict[str, str]:
    """Verify explicitly selected local bytes; this does not authenticate their origin."""
    expected_digest = _digest(expected_digest)
    content = _read_regular(index_path, MAX_BYTES)
    if _sha256(content) != expected_digest:
        raise IndexError("index digest mismatch")
    index = json.loads(content, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
    descriptors = _resolve_descriptors(index)
    directory_fd = os.open(blob_directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for digest, size in descriptors.values():
            if size > MAX_MANIFEST_BYTES:
                raise IndexError("manifest exceeds byte limit")
            blob = _read_regular(
                digest.removeprefix("sha256:"), MAX_MANIFEST_BYTES, dir_fd=directory_fd
            )
            if len(blob) != size or _sha256(blob) != digest:
                raise IndexError("manifest size or digest mismatch")
    finally:
        os.close(directory_fd)
    return {platform: descriptor[0] for platform, descriptor in descriptors.items()}


def _parse_json(content: bytes) -> object:
    return json.loads(content, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)


def _descriptor(value: object, media_type: str, limit: int) -> tuple[str, int]:
    if not isinstance(value, dict) or value.get("mediaType") != media_type:
        raise IndexError("unsupported descriptor media type")
    digest = _digest(value.get("digest"))
    size = value.get("size")
    if isinstance(size, bool) or not isinstance(size, int) or not 0 < size <= limit:
        raise IndexError("invalid descriptor size")
    return digest, size


def _checked_blob(directory_fd: int, descriptor: tuple[str, int], limit: int) -> bytes:
    digest, size = descriptor
    content = _read_regular(digest.removeprefix("sha256:"), limit, dir_fd=directory_fd)
    if len(content) != size or _sha256(content) != digest:
        raise IndexError("blob size or digest mismatch")
    return content


def _validate_statement(content: bytes, target: str, annotation: object) -> str:
    statement = _parse_json(content)
    if (
        not isinstance(statement, dict)
        or not isinstance(statement.get("_type"), str)
        or statement["_type"] not in STATEMENTS
    ):
        raise IndexError("unsupported statement version")
    predicate = statement.get("predicateType")
    if not isinstance(predicate, str) or predicate not in PREDICATES:
        raise IndexError("unsupported predicate type")
    if annotation is not None and annotation != predicate:
        raise IndexError("predicate annotation mismatch")
    if not isinstance(statement.get("predicate"), dict):
        raise IndexError("missing predicate object")
    subjects = statement.get("subject")
    if not isinstance(subjects, list) or len(subjects) != 1 or not isinstance(subjects[0], dict):
        raise IndexError("ambiguous statement subject")
    if subjects[0].get("digest") != {"sha256": target.removeprefix("sha256:")}:
        raise IndexError("statement subject mismatch")
    return predicate


def verify_attestation_bindings(
    index_path: str, expected_digest: str, blob_directory: str
) -> dict[str, list[str]]:
    """Check BuildKit byte/subject consistency; do not authenticate any content or source."""
    content = _read_regular(index_path, MAX_BYTES)
    if _sha256(content) != _digest(expected_digest):
        raise IndexError("index digest mismatch")
    index = _parse_json(content)
    descriptors = _resolve_descriptors(index)
    assert isinstance(index, dict)
    entries = index["manifests"]
    assert isinstance(entries, list)
    targets = {digest: (platform, size) for platform, (digest, size) in descriptors.items()}
    predicates: dict[str, set[str]] = {platform: set() for platform in descriptors}
    consumed = 0
    layer_count = 0
    directory_fd = os.open(blob_directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for digest, size in descriptors.values():
            if size > MAX_MANIFEST_BYTES:
                raise IndexError("manifest exceeds byte limit")
            _checked_blob(directory_fd, (digest, size), MAX_MANIFEST_BYTES)
        for entry in entries:
            annotations = entry.get("annotations", {})
            if annotations.get(REFERENCE_TYPE) != "attestation-manifest":
                continue
            target = annotations[REFERENCE_DIGEST]
            platform, target_size = targets[target]
            attestation_descriptor = _descriptor(entry, MANIFEST, MAX_MANIFEST_BYTES)
            manifest_bytes = _checked_blob(directory_fd, attestation_descriptor, MAX_MANIFEST_BYTES)
            consumed += len(manifest_bytes)
            if consumed > MAX_ATTESTATION_TOTAL:
                raise IndexError("attestation total exceeds byte limit")
            manifest = _parse_json(manifest_bytes)
            if (
                not isinstance(manifest, dict)
                or type(manifest.get("schemaVersion")) is not int
                or manifest["schemaVersion"] != 2
                or manifest.get("mediaType") != MANIFEST
            ):
                raise IndexError("invalid attestation manifest")
            artifact_type = manifest.get("artifactType")
            config = manifest.get("config")
            if artifact_type is None:
                _descriptor(config, OCI_CONFIG, MAX_MANIFEST_BYTES)
                if "subject" in manifest:
                    raise IndexError("unsupported legacy manifest subject")
            elif artifact_type == ARTIFACT:
                empty = _descriptor(config, OCI_EMPTY, MAX_MANIFEST_BYTES)
                if empty != (_sha256(b"{}"), 2):
                    raise IndexError("invalid OCI empty config")
                if _descriptor(manifest.get("subject"), MANIFEST, MAX_MANIFEST_BYTES) != (
                    target,
                    target_size,
                ):
                    raise IndexError("manifest subject mismatch")
            else:
                raise IndexError("unsupported attestation artifact type")
            layers = manifest.get("layers")
            if not isinstance(layers, list) or not layers:
                raise IndexError("missing attestation layers")
            for layer in layers:
                layer_count += 1
                if layer_count > MAX_ATTESTATION_LAYERS:
                    raise IndexError("too many attestation layers")
                layer_descriptor = _descriptor(layer, INTOTO, MAX_MANIFEST_BYTES)
                layer_bytes = _checked_blob(directory_fd, layer_descriptor, MAX_MANIFEST_BYTES)
                consumed += len(layer_bytes)
                if consumed > MAX_ATTESTATION_TOTAL:
                    raise IndexError("attestation total exceeds byte limit")
                layer_annotations = layer.get("annotations", {})
                if not isinstance(layer_annotations, dict) or any(
                    not isinstance(key, str) or not isinstance(value, str)
                    for key, value in layer_annotations.items()
                ):
                    raise IndexError("invalid layer annotations")
                predicate = _validate_statement(
                    layer_bytes, target, layer_annotations.get(PREDICATE_ANNOTATION)
                )
                if predicate in predicates[platform]:
                    raise IndexError("duplicate predicate")
                predicates[platform].add(predicate)
    finally:
        os.close(directory_fd)
    if any(found != PREDICATES for found in predicates.values()):
        raise IndexError("missing required predicate")
    return {platform: sorted(found) for platform, found in predicates.items()}


def main() -> int:
    if len(sys.argv) not in (2, 5) or (
        len(sys.argv) == 5 and sys.argv[1] not in ("--verify", "--verify-attestations")
    ):
        print(
            "usage: oci_index.py INDEX.json | --verify|--verify-attestations "
            "INDEX.json EXPECTED_SHA256 BLOBS_DIR",
            file=sys.stderr,
        )
        return 2
    try:
        if len(sys.argv) == 5:
            if sys.argv[1] == "--verify-attestations":
                print(
                    json.dumps(
                        verify_attestation_bindings(sys.argv[2], sys.argv[3], sys.argv[4]),
                        sort_keys=True,
                    )
                )
                return 0
            print(
                json.dumps(verify_integrity(sys.argv[2], sys.argv[3], sys.argv[4]), sort_keys=True)
            )
            return 0
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

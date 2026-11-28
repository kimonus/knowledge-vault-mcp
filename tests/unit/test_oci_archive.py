import hashlib
import io
import json
import tarfile
from pathlib import Path

import pytest
from scripts.ci.oci_archive import inspect_archive
from scripts.ci.oci_index import INDEX, INTOTO, MANIFEST, OCI_CONFIG, IndexError


def _digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _archive(path: Path, *, case: str = "valid") -> None:
    objects: dict[str, bytes] = {}

    def store(data: bytes) -> tuple[str, int]:
        digest = _digest(data)
        objects[digest[7:]] = data
        return digest, len(data)

    entries = []
    for arch in ("amd64", "arm64"):
        child, child_size = store(f"image {arch}".encode())
        entries.append(
            {
                "mediaType": MANIFEST,
                "digest": child,
                "size": child_size,
                "platform": {"os": "linux", "architecture": arch},
            }
        )
        layers = []
        predicates = ("https://slsa.dev/provenance/v0.2", "https://spdx.dev/Document")
        for predicate in predicates[:1] if case == "missing_sbom" else predicates:
            statement = {
                "_type": "https://in-toto.io/Statement/v1",
                "subject": [{"digest": {"sha256": child[7:]}}],
                "predicateType": predicate,
                "predicate": {},
            }
            digest, size = store(json.dumps(statement).encode())
            layers.append({"mediaType": INTOTO, "digest": digest, "size": size})
        manifest = {
            "schemaVersion": 2,
            "mediaType": MANIFEST,
            "config": {"mediaType": OCI_CONFIG, "digest": _digest(b"config"), "size": 6},
            "layers": layers,
        }
        digest, size = store(json.dumps(manifest).encode())
        entries.append(
            {
                "mediaType": MANIFEST,
                "digest": digest,
                "size": size,
                "platform": {"os": "unknown", "architecture": "unknown"},
                "annotations": {
                    "vnd.docker.reference.type": "attestation-manifest",
                    "vnd.docker.reference.digest": child,
                },
            }
        )
    candidate = json.dumps({"schemaVersion": 2, "mediaType": INDEX, "manifests": entries}).encode()
    digest, size = store(candidate)
    if case == "bad_candidate":
        objects[digest[7:]] += b" "
    root = {
        "schemaVersion": 2,
        "mediaType": INDEX,
        "manifests": [{"mediaType": INDEX, "digest": digest, "size": size}],
    }
    with tarfile.open(path, "w") as archive:

        def add(name: str, data: bytes) -> None:
            if case == "dot_prefix":
                name = f"./{name}"
            item = tarfile.TarInfo(name)
            item.size = len(data)
            archive.addfile(item, io.BytesIO(data))

        layout = (
            b'{"imageLayoutVersion":"0.0.0"}'
            if case == "bad_layout"
            else b'{"imageLayoutVersion":"1.0.0"}'
        )
        add("oci-layout", layout)
        add("index.json", json.dumps(root).encode())
        for key, data in objects.items():
            add(f"blobs/sha256/{key}", data)
        if case == "duplicate":
            add("index.json", b"{}")
        if case == "traversal":
            add("../escape", b"bad")
        if case == "symlink":
            item = tarfile.TarInfo("blobs/sha256/" + "f" * 64)
            item.type = tarfile.SYMTYPE
            item.linkname = "/etc/passwd"
            archive.addfile(item)


@pytest.mark.parametrize("case", ["valid", "dot_prefix"])
def test_wrapped_archive_is_checked(tmp_path: Path, case: str) -> None:
    archive = tmp_path / "image.tar"
    _archive(archive, case=case)
    result = inspect_archive(str(archive), str(tmp_path / "staging"))
    assert set(result["images"]) == {"linux/amd64", "linux/arm64"}


@pytest.mark.parametrize(
    "case", ["bad_candidate", "bad_layout", "duplicate", "traversal", "symlink", "missing_sbom"]
)
def test_unsafe_or_incomplete_archive_fails(tmp_path: Path, case: str) -> None:
    archive = tmp_path / "image.tar"
    _archive(archive, case=case)
    with pytest.raises((IndexError, OSError)):
        inspect_archive(str(archive), str(tmp_path / "staging"))
    assert not (tmp_path / "escape").exists()

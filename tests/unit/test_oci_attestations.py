import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from scripts.ci.oci_index import (
    ARTIFACT,
    INDEX,
    INTOTO,
    MANIFEST,
    OCI_CONFIG,
    OCI_EMPTY,
    PREDICATE_ANNOTATION,
    REFERENCE_DIGEST,
    REFERENCE_TYPE,
    IndexError,
    verify_attestation_bindings,
)

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/ci/oci_index.py"
PROVENANCE = "https://slsa.dev/provenance/v0.2"
SPDX = "https://spdx.dev/Document"
Mutator = Callable[[dict[str, Any], str, str], None]


def _digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _graph(
    root: Path,
    *,
    statement_mutator: Mutator | None = None,
    manifest_mutator: Mutator | None = None,
    layer_mutator: Mutator | None = None,
    index_mutator: Mutator | None = None,
) -> tuple[Path, str, Path, dict[str, str]]:
    blobs = root / "blobs"
    blobs.mkdir()

    def store(value: bytes) -> tuple[str, int]:
        digest = _digest(value)
        (blobs / digest.removeprefix("sha256:")).write_bytes(value)
        return digest, len(value)

    entries: list[dict[str, Any]] = []
    targets: dict[str, str] = {}
    for arch in ("amd64", "arm64"):
        child, child_size = store(f"image manifest {arch}".encode())
        targets[arch] = child
        entries.append(
            {
                "mediaType": MANIFEST,
                "digest": child,
                "size": child_size,
                "platform": {"os": "linux", "architecture": arch},
            }
        )
        layers = []
        for predicate in (PROVENANCE, SPDX):
            statement: dict[str, Any] = {
                "_type": (
                    "https://in-toto.io/Statement/v0.1"
                    if predicate == PROVENANCE
                    else "https://in-toto.io/Statement/v1"
                ),
                "subject": [{"name": "_", "digest": {"sha256": child.removeprefix("sha256:")}}],
                "predicateType": predicate,
                "predicate": {},
            }
            if statement_mutator:
                statement_mutator(statement, arch, predicate)
            layer_digest, layer_size = store(json.dumps(statement).encode())
            layer: dict[str, Any] = {
                "mediaType": INTOTO,
                "digest": layer_digest,
                "size": layer_size,
                "annotations": {PREDICATE_ANNOTATION: predicate},
            }
            if layer_mutator:
                layer_mutator(layer, arch, predicate)
            layers.append(layer)
        manifest: dict[str, Any] = {"schemaVersion": 2, "mediaType": MANIFEST, "layers": layers}
        if arch == "amd64":
            manifest["config"] = {
                "mediaType": OCI_CONFIG,
                "digest": _digest(b"unused legacy config"),
                "size": 20,
            }
        else:
            manifest["artifactType"] = ARTIFACT
            manifest["config"] = {"mediaType": OCI_EMPTY, "digest": _digest(b"{}"), "size": 2}
            manifest["subject"] = {"mediaType": MANIFEST, "digest": child, "size": child_size}
        if manifest_mutator:
            manifest_mutator(manifest, arch, child)
        digest, size = store(json.dumps(manifest).encode())
        entries.append(
            {
                "mediaType": MANIFEST,
                "digest": digest,
                "size": size,
                "platform": {"os": "unknown", "architecture": "unknown"},
                "annotations": {REFERENCE_TYPE: "attestation-manifest", REFERENCE_DIGEST: child},
            }
        )
    index: dict[str, Any] = {"schemaVersion": 2, "mediaType": INDEX, "manifests": entries}
    if index_mutator:
        index_mutator(index, "", "")
    data = json.dumps(index).encode()
    path = root / "candidate-index.json"
    path.write_bytes(data)
    return path, _digest(data), blobs, targets


def test_consistent_untrusted_graph_passes(tmp_path: Path) -> None:
    path, expected, blobs, _ = _graph(tmp_path)
    assert verify_attestation_bindings(str(path), expected, str(blobs)) == {
        "linux/amd64": [PROVENANCE, SPDX],
        "linux/arm64": [PROVENANCE, SPDX],
    }
    result = subprocess.run(  # noqa: S603 - fixed script, isolated fixture
        [sys.executable, str(SCRIPT), "--verify-attestations", str(path), expected, str(blobs)],
        capture_output=True,
        check=True,
        text=True,
    )
    assert json.loads(result.stdout)["linux/arm64"] == [PROVENANCE, SPDX]


@pytest.mark.parametrize(
    "case",
    [
        "wrong_subject",
        "multi_subject",
        "type",
        "predicate",
        "annotation",
        "manifest_subject",
        "config",
        "media",
        "duplicate",
        "missing",
        "attestation_missing",
    ],
)
def test_inconsistent_graph_fails_closed(tmp_path: Path, case: str) -> None:
    def change_statement(statement: dict[str, Any], arch: str, predicate: str) -> None:
        if arch != "arm64" or predicate != SPDX:
            return
        if case == "wrong_subject":
            statement["subject"][0]["digest"]["sha256"] = "a" * 64
        elif case == "multi_subject":
            statement["subject"].append(statement["subject"][0])
        elif case == "type":
            statement["_type"] = ["invalid"]
        elif case == "predicate":
            statement["predicateType"] = "https://unknown.invalid/statement"
        elif case == "duplicate":
            statement["predicateType"] = PROVENANCE

    def change_layer(layer: dict[str, Any], arch: str, predicate: str) -> None:
        if arch == "arm64" and predicate == SPDX:
            if case in ("annotation", "duplicate"):
                layer["annotations"][PREDICATE_ANNOTATION] = PROVENANCE
            elif case == "media":
                layer["mediaType"] = "application/octet-stream"

    def change_manifest(manifest: dict[str, Any], arch: str, _target: str) -> None:
        if arch != "arm64":
            return
        if case == "manifest_subject":
            manifest["subject"]["size"] += 1
        elif case == "config":
            manifest["config"]["digest"] = _digest(b"wrong")
        elif case == "missing":
            manifest["layers"].pop()

    def change_index(index: dict[str, Any], _arch: str, _predicate: str) -> None:
        if case == "attestation_missing":
            index["manifests"].pop()

    path, expected, blobs, _ = _graph(
        tmp_path,
        statement_mutator=change_statement,
        layer_mutator=change_layer,
        manifest_mutator=change_manifest,
        index_mutator=change_index,
    )
    with pytest.raises(IndexError):
        verify_attestation_bindings(str(path), expected, str(blobs))


@pytest.mark.parametrize(
    "case", ["index", "child", "manifest", "layer", "missing", "symlink", "fifo"]
)
def test_byte_chain_and_files_fail_closed(tmp_path: Path, case: str) -> None:
    path, expected, blobs, targets = _graph(tmp_path)
    index = json.loads(path.read_bytes())
    attestation = index["manifests"][1]
    attestation_file = blobs / attestation["digest"].removeprefix("sha256:")
    manifest = json.loads(attestation_file.read_bytes())
    layer_file = blobs / manifest["layers"][0]["digest"].removeprefix("sha256:")
    if case == "index":
        path.write_bytes(path.read_bytes() + b" ")
    elif case == "child":
        (blobs / targets["amd64"].removeprefix("sha256:")).write_bytes(b"bad")
    elif case == "manifest":
        attestation_file.write_bytes(attestation_file.read_bytes() + b" ")
    elif case == "layer":
        layer_file.write_bytes(layer_file.read_bytes() + b" ")
    elif case == "missing":
        layer_file.unlink()
    elif case == "symlink":
        layer_file.unlink()
        layer_file.symlink_to(path)
    else:
        layer_file.unlink()
        os.mkfifo(layer_file)
    with pytest.raises((IndexError, OSError)):
        verify_attestation_bindings(str(path), expected, str(blobs))


def test_attestation_budget_is_enforced(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path, expected, blobs, _ = _graph(tmp_path)
    monkeypatch.setattr("scripts.ci.oci_index.MAX_ATTESTATION_TOTAL", 10)
    with pytest.raises(IndexError, match="total exceeds"):
        verify_attestation_bindings(str(path), expected, str(blobs))


def test_attestation_count_is_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path, expected, blobs, _ = _graph(tmp_path)
    monkeypatch.setattr("scripts.ci.oci_index.MAX_ATTESTATION_LAYERS", 1)
    with pytest.raises(IndexError, match="too many"):
        verify_attestation_bindings(str(path), expected, str(blobs))


@pytest.mark.parametrize("case", ["duplicate_manifest_key", "duplicate_statement_key", "nan"])
def test_rejects_invalid_json_even_with_consistent_digests(tmp_path: Path, case: str) -> None:
    path, _, blobs, _ = _graph(tmp_path)
    index = json.loads(path.read_bytes())
    entry = index["manifests"][1]
    manifest = json.loads((blobs / entry["digest"].removeprefix("sha256:")).read_bytes())
    if case == "duplicate_manifest_key":
        manifest_bytes = (
            json.dumps(manifest)
            .encode()
            .replace(b'"schemaVersion": 2', b'"schemaVersion": 2, "schemaVersion": 2', 1)
        )
    else:
        layer = manifest["layers"][0]
        layer_bytes = (blobs / layer["digest"].removeprefix("sha256:")).read_bytes()
        if case == "duplicate_statement_key":
            layer_bytes = layer_bytes.replace(
                b'"predicate": {}', b'"predicate": {}, "predicate": {}', 1
            )
        else:
            layer_bytes = layer_bytes.replace(b'"predicate": {}', b'"predicate": {"x": NaN}', 1)
        layer["digest"] = _digest(layer_bytes)
        layer["size"] = len(layer_bytes)
        (blobs / layer["digest"].removeprefix("sha256:")).write_bytes(layer_bytes)
        manifest_bytes = json.dumps(manifest).encode()
    entry["digest"] = _digest(manifest_bytes)
    entry["size"] = len(manifest_bytes)
    (blobs / entry["digest"].removeprefix("sha256:")).write_bytes(manifest_bytes)
    index_bytes = json.dumps(index).encode()
    path.write_bytes(index_bytes)
    with pytest.raises(IndexError):
        verify_attestation_bindings(str(path), _digest(index_bytes), str(blobs))

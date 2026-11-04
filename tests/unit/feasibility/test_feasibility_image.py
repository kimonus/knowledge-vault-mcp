import hashlib
import json
from pathlib import Path

import pytest
from scripts import feasibility_image


def test_verified_image_and_corrupt_layer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data = b"synthetic layer"
    digest = hashlib.sha256(data).hexdigest()
    manifest = json.dumps({"layers": [{"digest": "sha256:" + digest, "size": len(data)}]}).encode()
    (tmp_path / "manifest.json").write_bytes(manifest)
    (tmp_path / digest).write_bytes(data)
    monkeypatch.setattr(feasibility_image, "PLATFORM_DIGEST", hashlib.sha256(manifest).hexdigest())
    feasibility_image.verify_layers(tmp_path)
    (tmp_path / digest).write_bytes(b"synthetic xayer")
    with pytest.raises(ValueError, match="digest mismatch"):
        feasibility_image.verify_layers(tmp_path)


def test_manifest_mismatch_refused(tmp_path: Path) -> None:
    (tmp_path / "manifest.json").write_text("{}")
    with pytest.raises(ValueError, match="manifest digest mismatch"):
        feasibility_image.verify_layers(tmp_path)


def _write_manifest(tmp_path: Path, payload: object, monkeypatch: pytest.MonkeyPatch) -> bytes:
    raw = json.dumps(payload).encode()
    (tmp_path / "manifest.json").write_bytes(raw)
    monkeypatch.setattr(feasibility_image, "PLATFORM_DIGEST", hashlib.sha256(raw).hexdigest())
    return raw


@pytest.mark.parametrize("payload", [{}, {"layers": None}, {"layers": []}, {"layers": [{}] * 51}])
def test_invalid_layer_collections_are_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, payload: object
) -> None:
    _write_manifest(tmp_path, payload, monkeypatch)
    with pytest.raises(ValueError, match="layer count"):
        feasibility_image.verify_layers(tmp_path)


@pytest.mark.parametrize(
    ("digest", "size", "error"),
    [
        ("sha256:invalid", 0, "layer digest"),
        ("sha256:" + "a" * 64, True, "layer size"),
        ("sha256:" + "a" * 64, -1, "layer size"),
        ("sha256:" + "a" * 64, feasibility_image.MAX_BYTES + 1, "size limit"),
    ],
)
def test_invalid_layer_metadata_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    digest: str,
    size: int,
    error: str,
) -> None:
    _write_manifest(tmp_path, {"layers": [{"digest": digest, "size": size}]}, monkeypatch)
    with pytest.raises(ValueError, match=error):
        feasibility_image.verify_layers(tmp_path)


def test_manifest_and_layer_symlinks_are_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}")
    manifest_link = tmp_path / "manifest-link.json"
    manifest_link.symlink_to(manifest)
    (tmp_path / "manifest.json").unlink()
    manifest_link.rename(manifest)
    with pytest.raises(ValueError, match="manifest missing or symbolic"):
        feasibility_image.verify_layers(tmp_path)
    manifest.unlink()

    data = b"layer"
    digest = hashlib.sha256(data).hexdigest()
    _write_manifest(
        tmp_path,
        {"layers": [{"digest": "sha256:" + digest, "size": len(data)}]},
        monkeypatch,
    )
    target = tmp_path / "target"
    target.write_bytes(data)
    (tmp_path / digest).symlink_to(target)
    with pytest.raises(ValueError, match="layer missing or size mismatch"):
        feasibility_image.verify_layers(tmp_path)


def test_layer_size_and_json_shape_are_checked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = b"layer"
    digest = hashlib.sha256(data).hexdigest()
    _write_manifest(
        tmp_path,
        {"layers": [{"digest": "sha256:" + digest, "size": len(data) + 1}]},
        monkeypatch,
    )
    (tmp_path / digest).write_bytes(data)
    with pytest.raises(ValueError, match="size mismatch"):
        feasibility_image.verify_layers(tmp_path)

    _write_manifest(tmp_path, {"layers": [None]}, monkeypatch)
    with pytest.raises(ValueError, match="layer metadata"):
        feasibility_image.verify_layers(tmp_path)

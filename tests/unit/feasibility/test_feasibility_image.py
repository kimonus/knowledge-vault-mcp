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

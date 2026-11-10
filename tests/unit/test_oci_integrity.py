import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from scripts.ci.oci_index import INDEX, MANIFEST, IndexError, verify_integrity

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/ci/oci_index.py"


def digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


@pytest.fixture
def layout(tmp_path: Path) -> tuple[Path, str, Path, dict[str, str]]:
    blobs = tmp_path / "blobs" / "sha256"
    blobs.mkdir(parents=True)
    manifests = [b'{"schemaVersion":2,"config":{}}', b'{"schemaVersion":2,"config":{"arm":true}}']
    ids = [digest(data) for data in manifests]
    for blob_id, data in zip(ids, manifests, strict=True):
        (blobs / blob_id.removeprefix("sha256:")).write_bytes(data)
    index = {
        "schemaVersion": 2,
        "mediaType": INDEX,
        "manifests": [
            {
                "mediaType": MANIFEST,
                "digest": blob_id,
                "size": len(data),
                "platform": {"os": "linux", "architecture": arch},
            }
            for blob_id, data, arch in zip(ids, manifests, ("amd64", "arm64"), strict=True)
        ],
    }
    content = json.dumps(index).encode()
    path = tmp_path / "candidate-index.json"
    path.write_bytes(content)
    return path, digest(content), blobs, {"linux/amd64": ids[0], "linux/arm64": ids[1]}


def test_verified_index_and_children(layout: tuple[Path, str, Path, dict[str, str]]) -> None:
    path, expected, blobs, ids = layout
    assert verify_integrity(str(path), expected, str(blobs)) == ids
    result = subprocess.run(  # noqa: S603 - fixed interpreter and script, isolated files
        [sys.executable, str(SCRIPT), "--verify", str(path), expected, str(blobs)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(result.stdout) == ids


@pytest.mark.parametrize(
    "bad", ["wrong_index", "corrupt", "missing", "size", "bad_digest", "oversize"]
)
def test_invalid_bytes_fail_closed(
    layout: tuple[Path, str, Path, dict[str, str]], bad: str
) -> None:
    path, expected, blobs, ids = layout
    blob = blobs / ids["linux/amd64"].removeprefix("sha256:")
    if bad == "wrong_index":
        expected = digest(path.read_bytes() + b" ")
    elif bad == "corrupt":
        original = blob.read_bytes()
        blob.write_bytes(b"x" * len(original))
    elif bad == "missing":
        blob.unlink()
    elif bad in ("size", "oversize"):
        index = json.loads(path.read_bytes())
        index["manifests"][0]["size"] = (
            8 * 1024 * 1024 + 1 if bad == "oversize" else blob.stat().st_size + 1
        )
        path.write_text(json.dumps(index))
        expected = digest(path.read_bytes())
    else:
        expected = "sha256:invalid"
    with pytest.raises((IndexError, OSError)):
        verify_integrity(str(path), expected, str(blobs))


def test_symlinks_and_special_files_are_rejected(
    layout: tuple[Path, str, Path, dict[str, str]],
) -> None:
    path, expected, blobs, ids = layout
    link = path.parent / "link.json"
    link.symlink_to(path)
    with pytest.raises(OSError):
        verify_integrity(str(link), expected, str(blobs))
    link_dir = path.parent / "linked-blobs"
    link_dir.symlink_to(blobs, target_is_directory=True)
    with pytest.raises(OSError):
        verify_integrity(str(path), expected, str(link_dir))
    blob = blobs / ids["linux/amd64"].removeprefix("sha256:")
    blob.unlink()
    blob.symlink_to(path)
    with pytest.raises(OSError):
        verify_integrity(str(path), expected, str(blobs))
    blob.unlink()
    os.mkfifo(blob)
    with pytest.raises(IndexError, match="not regular"):
        verify_integrity(str(path), expected, str(blobs))


def test_cli_fails_without_mapping_on_integrity_error(
    layout: tuple[Path, str, Path, dict[str, str]],
) -> None:
    path, _, blobs, _ = layout
    result = subprocess.run(  # noqa: S603 - fixed interpreter and script, isolated files
        [sys.executable, str(SCRIPT), "--verify", str(path), digest(b"wrong"), str(blobs)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert not result.stdout
    assert "index digest mismatch" in result.stderr
    assert "Traceback" not in result.stderr

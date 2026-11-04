"""Verify pinned image layers before unpacking them into an owned workspace."""

import hashlib
import json
import re
from pathlib import Path

PLATFORM_DIGEST = "5686e13b90163a66e3f6949069475ec4e5edb61d4db8cf3806eb98c0f931221e"
MAX_BYTES = 500_000_000


def verify_layers(directory: Path) -> list[tuple[str, int]]:
    manifest_path = directory / "manifest.json"
    if (
        manifest_path.is_symlink()
        or not manifest_path.is_file()
        or manifest_path.stat().st_size > 1_000_000
    ):
        raise ValueError("manifest missing or symbolic")
    raw = manifest_path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != PLATFORM_DIGEST:
        raise ValueError("manifest digest mismatch")
    manifest = json.loads(raw)
    layers = manifest.get("layers")
    if not isinstance(layers, list) or not 1 <= len(layers) <= 50:
        raise ValueError("invalid image layer count")
    total = 0
    names: list[tuple[str, int]] = []
    for layer in layers:
        if not isinstance(layer, dict) or "digest" not in layer or "size" not in layer:
            raise ValueError("invalid layer metadata")
        digest, size = layer["digest"], layer["size"]
        if not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            raise ValueError("invalid layer digest")
        if type(size) is not int or size < 0:
            raise ValueError("invalid layer size")
        total += size
        if total > MAX_BYTES:
            raise ValueError("image exceeds compressed size limit")
        path = directory / digest[7:]
        if path.is_symlink() or not path.is_file() or path.stat().st_size != size:
            raise ValueError("layer missing or size mismatch")
        checksum = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                checksum.update(chunk)
        if checksum.hexdigest() != digest[7:]:
            raise ValueError("layer digest mismatch")
        names.append((digest[7:], size))
    return names

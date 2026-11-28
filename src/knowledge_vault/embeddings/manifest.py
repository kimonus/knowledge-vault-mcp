"""Offline integrity for the existing independently provisioned model-manifest format.

Hashes bind content and a model identity. They do not authenticate an upstream publisher.
This module uses only the standard library so the deployment controller can reuse it.
"""

import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any


class ModelManifestError(ValueError):
    """Fixed nonsecret diagnostics for operator-provisioned model files."""


def verify_model_manifest(
    model: Path, manifest: Path, *, expected_digest: str | None = None, dimensions: int = 384
) -> dict[str, Any]:
    try:
        if model.is_symlink() or not model.is_dir() or model.resolve() != model.absolute():
            raise ModelManifestError("Unsafe model directory")
        if manifest.is_symlink() or not manifest.is_file() or manifest.stat().st_size > 131072:
            raise ModelManifestError("Unsafe or missing model manifest")
        raw = manifest.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if expected_digest is not None and digest != expected_digest:
            raise ModelManifestError("Model manifest identity changed")
        document = json.loads(raw)
        if not isinstance(document, dict) or document.get("dimensions") != dimensions:
            raise ModelManifestError("Model dimensions differ from configuration")
        if not isinstance(document.get("model_id"), str) or not document["model_id"]:
            raise ModelManifestError("Model identity is missing")
        if not isinstance(document.get("revision"), str) or not re.fullmatch(
            r"[0-9a-f]{40}", document["revision"]
        ):
            raise ModelManifestError("Model revision must be a full revision identifier")
        files = document.get("files")
        if not isinstance(files, dict) or not 1 <= len(files) <= 256:
            raise ModelManifestError("Model file inventory is missing or too large")
        for name, checksum in files.items():
            if not isinstance(name, str) or not isinstance(checksum, str):
                raise ModelManifestError("Invalid model inventory")
            relative = PurePosixPath(name)
            if (
                relative.is_absolute()
                or str(relative) != name
                or any(p in (".", "..") for p in relative.parts)
                or "\\" in name
            ):
                raise ModelManifestError("Unsafe model inventory path")
            if not re.fullmatch(r"[0-9a-f]{64}", checksum):
                raise ModelManifestError("Invalid model checksum")
        actual = {}
        total_bytes = 0
        for item in model.rglob("*"):
            if item.is_symlink() or item.is_mount():
                raise ModelManifestError("Model snapshot contains a link or nested mount")
            if item.is_dir():
                continue
            if not item.is_file() or len(actual) >= 256:
                raise ModelManifestError("Unsupported model file inventory")
            hashed = hashlib.sha256()
            with item.open("rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    hashed.update(chunk)
                    total_bytes += len(chunk)
            actual[item.relative_to(model).as_posix()] = hashed.hexdigest()
        if actual != files:
            raise ModelManifestError("Model files differ from the provisioned manifest")
        if (
            "modules.json" not in actual
            or not any(n.endswith("tokenizer_config.json") for n in actual)
            or not any(n.endswith(("model.safetensors", "pytorch_model.bin")) for n in actual)
        ):
            raise ModelManifestError("Model snapshot is incomplete")
        return {
            "manifest_sha256": digest,
            "model_id": document["model_id"],
            "revision": document["revision"],
            "dimensions": dimensions,
            "file_count": len(actual),
            "total_bytes": total_bytes,
        }
    except (OSError, json.JSONDecodeError, UnicodeError):
        raise ModelManifestError("Model manifest or files cannot be read") from None

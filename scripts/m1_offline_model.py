"""Verify an operator-supplied, independent M1 model snapshot without network access."""

import hashlib
import json
import os
import re
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

MODEL_ID = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
DIMENSIONS = 384
# An explicit lab root makes an accidental production cache path impossible.
INDEPENDENT_ROOT = Path("/mnt/data/knowledge-vault-m1-models")


def verify_snapshot(path: Path, manifest_path: Path) -> dict[str, object]:
    root = INDEPENDENT_ROOT
    if not root.is_dir() or root.is_symlink() or root.is_mount():
        raise RuntimeError("independent M1 model root is missing or unsafe")
    if path.parent != root or path.is_symlink() or not path.is_dir() or path.is_mount():
        raise RuntimeError("model must be a direct directory below the independent M1 root")
    if (
        manifest_path != root / f"{path.name}.json"
        or manifest_path.is_symlink()
        or not manifest_path.is_file()
        or manifest_path.is_mount()
    ):
        raise RuntimeError("model manifest must be a regular file beside the independent snapshot")
    if not re.fullmatch(r"[0-9a-f]{40}", path.name):
        raise RuntimeError("snapshot directory must name its full upstream revision")
    try:
        manifest_bytes = manifest_path.read_bytes()
        manifest = json.loads(manifest_bytes)
    except (OSError, ValueError) as exc:
        raise RuntimeError("model manifest is missing or invalid") from exc
    if (
        not isinstance(manifest, dict)
        or manifest.get("model_id") != MODEL_ID
        or manifest.get("revision") != path.name
        or manifest.get("dimensions") != DIMENSIONS
        or not isinstance(manifest.get("files"), dict)
    ):
        raise RuntimeError("model manifest identity, revision, or dimensions are invalid")
    files = manifest["files"]
    actual: dict[str, str] = {}
    for item in path.rglob("*"):
        if item.is_symlink() or item.is_mount():
            raise RuntimeError("model snapshot contains a symlink or mount point")
        if item.is_dir():
            continue
        if not item.is_file():
            raise RuntimeError("model snapshot contains an unsupported filesystem entry")
        relative = item.relative_to(path).as_posix()
        digest = hashlib.sha256()
        with item.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        actual[relative] = digest.hexdigest()
    if not actual or files != actual:
        raise RuntimeError("model snapshot files or SHA-256 checksums differ from manifest")
    if (
        "modules.json" not in actual
        or not any(name.endswith("config.json") for name in actual)
        or not any(name.endswith("tokenizer_config.json") for name in actual)
        or not any(name.endswith(("model.safetensors", "pytorch_model.bin")) for name in actual)
    ):
        raise RuntimeError("model snapshot is incomplete")
    return {
        "model_id": MODEL_ID,
        "revision": path.name,
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "file_count": len(actual),
        "total_bytes": sum((path / name).stat().st_size for name in actual),
    }


@contextmanager
def offline_environment(cache: Path) -> Generator[None, None, None]:
    """Use a fresh empty cache and force all model libraries into offline mode."""
    if cache.exists():
        raise RuntimeError("offline model cache must be fresh")
    cache.mkdir(mode=0o700)
    keys = {
        "HF_HOME": str(cache),
        "HF_HUB_CACHE": str(cache / "hub"),
        "SENTENCE_TRANSFORMERS_HOME": str(cache / "sentence-transformers"),
        "XDG_CACHE_HOME": str(cache),
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "HF_HUB_DISABLE_TELEMETRY": "1",
    }
    previous = {key: os.environ.get(key) for key in keys}
    os.environ.update(keys)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

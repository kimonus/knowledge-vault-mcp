"""Integrity and embedding identity are checked offline before model construction."""

import hashlib
import json
from unittest.mock import Mock

import pytest

from knowledge_vault.embeddings.base import EmbeddingsUnavailableError
from knowledge_vault.embeddings.manifest import ModelManifestError, verify_model_manifest
from knowledge_vault.embeddings.providers import SentenceTransformerProvider


@pytest.fixture
def snapshot(tmp_path):
    model = tmp_path / "model"
    model.mkdir()
    for name in ("modules.json", "tokenizer_config.json", "model.safetensors"):
        (model / name).write_text("synthetic")
    document = {
        "model_id": "independently-provisioned-model",
        "revision": "a" * 40,
        "dimensions": 384,
        "files": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in model.iterdir()},
    }
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(document))
    digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
    bound = tmp_path / digest
    model.rename(bound)
    return bound, manifest, digest, document


def test_content_identity_and_changed_weights(snapshot):
    model, manifest, digest, _ = snapshot
    assert verify_model_manifest(model, manifest, expected_digest=digest)["dimensions"] == 384
    (model / "model.safetensors").write_text("different compatible model")
    with pytest.raises(ModelManifestError, match="differ"):
        verify_model_manifest(model, manifest, expected_digest=digest)
    provider = SentenceTransformerProvider(
        str(model), 384, manifest_path=str(manifest), manifest_sha256=digest
    )
    with pytest.raises(EmbeddingsUnavailableError, match="integrity"):
        provider._load()
    assert provider._instance is None


@pytest.mark.parametrize(
    "mutation",
    [
        {"dimensions": 8},
        {"model_id": ""},
        {"revision": "main"},
        {"files": {}},
        {"files": {"../outside": "a" * 64}},
        {"files": {"/outside": "a" * 64}},
        {"files": {"nested\\file": "a" * 64}},
        {"files": {"modules.json": "bad"}},
        {"files": {"modules.json": 42}},
    ],
)
def test_manifest_validation(snapshot, mutation):
    model, manifest, _, document = snapshot
    manifest.write_text(json.dumps(document | mutation))
    with pytest.raises(ModelManifestError):
        verify_model_manifest(model, manifest)


def test_manifest_identity_and_links_fail_closed(snapshot):
    model, manifest, digest, _ = snapshot
    manifest.write_text(manifest.read_text() + "\n")
    with pytest.raises(ModelManifestError, match="identity changed"):
        verify_model_manifest(model, manifest, expected_digest=digest)
    (model / "link").symlink_to(manifest)
    with pytest.raises(ModelManifestError, match="link"):
        verify_model_manifest(model, manifest)


@pytest.mark.parametrize(
    "manifest_path,digest", [("", "a" * 64), ("manifest.json", ""), ("manifest.json", "b" * 64)]
)
def test_provider_cannot_alias_an_unbound_manifest(manifest_path, digest):
    provider = SentenceTransformerProvider(
        "/models/arbitrary", 384, manifest_path=manifest_path, manifest_sha256=digest
    )
    with pytest.raises(EmbeddingsUnavailableError, match="integrity"):
        provider._verify_integrity()


def test_provider_rechecks_after_construction(snapshot, monkeypatch):
    import sys

    model, manifest, digest, _ = snapshot
    provider = SentenceTransformerProvider(
        str(model), 384, manifest_path=str(manifest), manifest_sha256=digest
    )

    def construction(*args, **kwargs):
        (model / "model.safetensors").write_text("changed during load")
        return object()

    monkeypatch.setitem(
        sys.modules, "sentence_transformers", Mock(SentenceTransformer=construction)
    )
    with pytest.raises(EmbeddingsUnavailableError):
        provider._load()
    assert provider._instance is None

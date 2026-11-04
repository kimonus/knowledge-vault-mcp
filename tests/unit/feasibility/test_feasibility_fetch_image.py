import hashlib
import json
import sys
import urllib.request
from pathlib import Path

import pytest
from scripts import feasibility_fetch_image, feasibility_image
from scripts.feasibility_fetch_image import SafeRedirect


def test_redirect_strips_authorization_on_new_host() -> None:
    request = urllib.request.Request(
        "https://registry-1.docker.io/v2/example", headers={"Authorization": "synthetic"}
    )
    result = SafeRedirect().redirect_request(
        request, None, 302, "redirect", {}, "https://cdn.example.invalid/blob"
    )
    assert result is not None
    assert not result.has_header("Authorization")


def test_redirect_rejects_plaintext() -> None:
    request = urllib.request.Request("https://registry-1.docker.io/v2/example")
    with pytest.raises(RuntimeError, match="non-TLS"):
        SafeRedirect().redirect_request(
            request, None, 302, "redirect", {}, "http://example.invalid"
        )


def test_redirect_preserves_authorization_on_same_host() -> None:
    request = urllib.request.Request(
        "https://registry-1.docker.io/v2/example", headers={"Authorization": "synthetic"}
    )
    result = SafeRedirect().redirect_request(
        request, None, 302, "redirect", {}, "https://registry-1.docker.io/v2/next"
    )
    assert result is not None
    assert result.get_header("Authorization") == "synthetic"


class _Response:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.offset = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = len(self.payload)
        chunk = self.payload[self.offset : self.offset + size]
        self.offset += len(chunk)
        return chunk


def test_main_fetches_only_digest_verified_synthetic_content(tmp_path: Path, monkeypatch) -> None:
    layer = b"synthetic image layer"
    layer_digest = hashlib.sha256(layer).hexdigest()
    platform = json.dumps(
        {"layers": [{"digest": "sha256:" + layer_digest, "size": len(layer)}]},
        separators=(",", ":"),
    ).encode()
    platform_digest = hashlib.sha256(platform).hexdigest()
    index = json.dumps(
        {
            "manifests": [
                {
                    "digest": "sha256:" + platform_digest,
                    "platform": {"os": "linux", "architecture": "amd64"},
                }
            ]
        },
        separators=(",", ":"),
    ).encode()
    index_digest = hashlib.sha256(index).hexdigest()
    monkeypatch.setattr(feasibility_fetch_image, "INDEX_DIGEST", index_digest)
    monkeypatch.setattr(feasibility_fetch_image, "PLATFORM_DIGEST", platform_digest)
    monkeypatch.setattr(feasibility_image, "PLATFORM_DIGEST", platform_digest)
    monkeypatch.setattr(sys, "argv", ["feasibility-fetch-image", str(tmp_path / "image")])

    class _Opener:
        def open(self, request, timeout):
            url = request.full_url if isinstance(request, urllib.request.Request) else request
            if url.startswith("https://auth.docker.io/"):
                return _Response(b'{"token":"synthetic"}')
            if url.endswith("/manifests/sha256:" + index_digest):
                return _Response(index)
            if url.endswith("/manifests/sha256:" + platform_digest):
                return _Response(platform)
            if url.endswith("/blobs/sha256:" + layer_digest):
                return _Response(layer)
            raise AssertionError(f"unexpected synthetic URL: {url}")

    monkeypatch.setattr(urllib.request, "build_opener", lambda *handlers: _Opener())
    feasibility_fetch_image.main()
    assert (tmp_path / "image" / "manifest.json").read_bytes() == platform
    assert (tmp_path / "image" / layer_digest).read_bytes() == layer


def test_main_rejects_nonmatching_platform_without_fetching_layers(
    tmp_path: Path, monkeypatch
) -> None:
    index = json.dumps(
        {
            "manifests": [
                {
                    "digest": "sha256:" + "0" * 64,
                    "platform": {"os": "linux", "architecture": "amd64"},
                }
            ]
        }
    ).encode()
    index_digest = hashlib.sha256(index).hexdigest()
    monkeypatch.setattr(feasibility_fetch_image, "INDEX_DIGEST", index_digest)
    monkeypatch.setattr(sys, "argv", ["feasibility-fetch-image", str(tmp_path / "image")])

    class _Opener:
        def open(self, request, timeout):
            url = request.full_url if isinstance(request, urllib.request.Request) else request
            if url.startswith("https://auth.docker.io/"):
                return _Response(b'{"token":"synthetic"}')
            return _Response(index)

    monkeypatch.setattr(urllib.request, "build_opener", lambda *handlers: _Opener())
    with pytest.raises(RuntimeError, match="platform mismatch"):
        feasibility_fetch_image.main()

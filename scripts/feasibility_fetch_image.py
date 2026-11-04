"""Download the pinned public PostgreSQL image layers without using Docker."""

import argparse
import hashlib
import json
import urllib.parse
import urllib.request
from pathlib import Path

from scripts.feasibility_image import PLATFORM_DIGEST, verify_layers

INDEX_DIGEST = "137f044b0efe3d57f39b972b9b53641b1f2045b99d879e298bbf514a25787dcf"
REGISTRY = "https://registry-1.docker.io/v2/pgvector/pgvector"


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        old = urllib.parse.urlsplit(request.full_url)
        new = urllib.parse.urlsplit(newurl)
        if new.scheme != "https":
            raise RuntimeError("registry redirected to a non-TLS URL")
        redirected = super().redirect_request(request, fp, code, msg, headers, newurl)
        if redirected is not None and new.netloc != old.netloc:
            redirected.remove_header("Authorization")
            redirected.unredirected_hdrs.pop("Authorization", None)
        return redirected


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("destination", type=Path, help="new private output directory")
    args = parser.parse_args()
    args.destination.mkdir(mode=0o700)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), SafeRedirect())
    auth_url = (
        "https://auth.docker.io/token?service=registry.docker.io"
        "&scope=repository:pgvector/pgvector:pull"
    )
    with opener.open(auth_url, timeout=30) as response:
        token = json.loads(response.read(100_000))["token"]

    def fetch_manifest(digest: str) -> bytes:
        request = urllib.request.Request(  # noqa: S310  # Fixed HTTPS registry.
            f"{REGISTRY}/manifests/sha256:{digest}",
            headers={
                "Authorization": "Bearer " + token,
                "Accept": "application/vnd.oci.image.index.v1+json, "
                "application/vnd.oci.image.manifest.v1+json, "
                "application/vnd.docker.distribution.manifest.list.v2+json, "
                "application/vnd.docker.distribution.manifest.v2+json",
            },
        )
        with opener.open(request, timeout=30) as response:
            data = response.read(1_000_001)
        if len(data) > 1_000_000 or hashlib.sha256(data).hexdigest() != digest:
            raise RuntimeError("manifest digest mismatch")
        return data

    index = json.loads(fetch_manifest(INDEX_DIGEST))
    platforms = [
        item
        for item in index["manifests"]
        if item.get("platform", {}).get("os") == "linux"
        and item.get("platform", {}).get("architecture") == "amd64"
    ]
    if len(platforms) != 1 or platforms[0]["digest"] != "sha256:" + PLATFORM_DIGEST:
        raise RuntimeError("pinned image platform mismatch")
    manifest = fetch_manifest(PLATFORM_DIGEST)
    (args.destination / "manifest.json").write_bytes(manifest)
    for layer in json.loads(manifest)["layers"]:
        digest = layer["digest"]
        size = layer["size"]
        request = urllib.request.Request(  # noqa: S310  # Digest from verified manifest.
            f"{REGISTRY}/blobs/{digest}", headers={"Authorization": "Bearer " + token}
        )
        checksum = hashlib.sha256()
        count = 0
        with (
            opener.open(request, timeout=90) as response,
            (args.destination / digest[7:]).open("xb") as output,
        ):
            while chunk := response.read(1024 * 1024):
                count += len(chunk)
                if count > size:
                    raise RuntimeError("image layer exceeds declared size")
                checksum.update(chunk)
                output.write(chunk)
        if count != size or checksum.hexdigest() != digest[7:]:
            raise RuntimeError("image layer digest mismatch")
    verify_layers(args.destination)
    print("pinned PostgreSQL image layers verified")


if __name__ == "__main__":
    main()

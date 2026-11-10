import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest
from scripts.ci.oci_index import INDEX, MANIFEST, REFERENCE_DIGEST, REFERENCE_TYPE, resolve_index
from scripts.ci.oci_index import IndexError as InvalidIndex

A = "sha256:" + "a" * 64
B = "sha256:" + "b" * 64
C = "sha256:" + "c" * 64
SCRIPT = Path(__file__).resolve().parents[2] / "scripts/ci/oci_index.py"


@pytest.fixture
def index() -> dict[str, object]:
    return {
        "schemaVersion": 2,
        "mediaType": INDEX,
        "manifests": [
            {
                "mediaType": MANIFEST,
                "digest": A,
                "size": 50,
                "platform": {"os": "linux", "architecture": "amd64"},
            },
            {
                "mediaType": MANIFEST,
                "digest": B,
                "size": 51,
                "platform": {"os": "linux", "architecture": "arm64"},
            },
            {
                "mediaType": MANIFEST,
                "digest": C,
                "size": 20,
                "platform": {"os": "unknown", "architecture": "unknown"},
                "annotations": {REFERENCE_TYPE: "attestation-manifest", REFERENCE_DIGEST: A},
            },
        ],
    }


def test_valid_index_and_unverified_attestation(index: dict[str, object]) -> None:
    assert resolve_index(index) == {"linux/amd64": A, "linux/arm64": B}


def test_baseline_variants_and_artifact_attestation(index: dict[str, object]) -> None:
    entries = index["manifests"]
    assert isinstance(entries, list)
    entries[0]["platform"]["variant"] = "v1"
    entries[1]["platform"]["variant"] = "v8"
    entries[2]["artifactType"] = "application/vnd.docker.attestation.manifest.v1+json"
    assert resolve_index(index) == {"linux/amd64": A, "linux/arm64": B}
    duplicate = deepcopy(entries[1])
    duplicate["digest"] = "sha256:" + "d" * 64
    del duplicate["platform"]["variant"]
    entries.append(duplicate)
    with pytest.raises(InvalidIndex, match="duplicate platform"):
        resolve_index(index)


@pytest.mark.parametrize(
    "case",
    [
        "missing",
        "duplicate",
        "other",
        "digest",
        "size",
        "reference",
        "spoof",
        "unknown",
        "nested",
        "variant",
        "duplicate_digest",
        "artifact",
        "invalid_platform",
        "platform_constraint",
        "large_size",
        "artifact_index",
    ],
)
def test_rejects_invalid_or_ambiguous_indexes(index: dict[str, object], case: str) -> None:
    entries = index["manifests"]
    assert isinstance(entries, list)
    if case == "missing":
        entries.pop(1)
    elif case == "duplicate":
        entry = deepcopy(entries[0])
        entry["digest"] = "sha256:" + "d" * 64
        entries.append(entry)
    elif case == "other":
        entries[1]["platform"]["architecture"] = "s390x"
    elif case == "digest":
        entries[0]["digest"] = "sha256:abcd"
    elif case == "size":
        entries[0]["size"] = True
    elif case == "reference":
        entries[2]["annotations"][REFERENCE_DIGEST] = "sha256:" + "d" * 64
    elif case == "spoof":
        entries[2]["platform"] = {"os": "linux", "architecture": "arm64"}
    elif case == "unknown":
        del entries[2]["annotations"]
    elif case == "nested":
        entries[0]["mediaType"] = INDEX
    elif case == "variant":
        entries[1]["platform"]["variant"] = "v9"
    elif case == "duplicate_digest":
        entries[1]["digest"] = A
    elif case == "artifact":
        entries[0]["artifactType"] = "application/example"
    elif case == "platform_constraint":
        entries[0]["platform"]["os.features"] = ["unverified"]
    elif case == "large_size":
        entries[0]["size"] = 2**63
    elif case == "artifact_index":
        index["artifactType"] = "application/example"
    else:
        entries[0]["platform"]["architecture"] = []
    with pytest.raises(InvalidIndex):
        resolve_index(index)


def test_cli_rejects_duplicate_keys_and_oversized_input(tmp_path: Path) -> None:
    path = tmp_path / "index.json"
    for data in (
        b'{"schemaVersion":2,"schemaVersion":2}',
        b" " * (1024 * 1024 + 1),
        b'{"x":NaN}',
        b"[" * 2000 + b"]" * 2000,
        b'{"x":' + b"9" * 5000 + b"}",
    ):
        path.write_bytes(data)
        result = subprocess.run(  # noqa: S603 - fixed interpreter and script, isolated input
            [sys.executable, str(SCRIPT), str(path)], capture_output=True, check=False
        )
        assert result.returncode == 1
        assert result.stdout == b""
        assert b"Traceback" not in result.stderr


def test_cli_emits_only_child_mapping(tmp_path: Path, index: dict[str, object]) -> None:
    path = tmp_path / "index.json"
    path.write_text(json.dumps(index))
    result = subprocess.run(  # noqa: S603 - fixed interpreter and script, isolated input
        [sys.executable, str(SCRIPT), str(path)], capture_output=True, text=True, check=True
    )
    assert json.loads(result.stdout) == {"linux/amd64": A, "linux/arm64": B}

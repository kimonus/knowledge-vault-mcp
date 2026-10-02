"""Values that are repeated across files by hand must agree (review finding KV-023)."""

import json
import re
import tomllib
from pathlib import Path

from knowledge_vault.config import Settings

ROOT = Path(__file__).resolve().parents[2]

PGVECTOR_FILES = (
    "README.md",
    "tests/conftest.py",
    "scripts/ci/test_backup_restore.sh",
    ".github/workflows/ci.yml",
    "charts/knowledge-vault/values.yaml",
)


def test_pgvector_image_pin_is_identical_everywhere() -> None:
    tags: dict[str, set[str]] = {}
    digests: dict[str, set[str]] = {}
    for name in PGVECTOR_FILES:
        text = (ROOT / name).read_text(encoding="utf-8")
        tags[name] = set(
            re.findall(r"pgvector/pgvector[\s\S]{0,40}?(\d+\.\d+\.\d+-pg\d+-\w+)", text)
        )
        digests[name] = set(
            re.findall(r"pgvector/pgvector[\s\S]{0,120}?(sha256:[0-9a-f]{64})", text)
        )
        assert tags[name] and digests[name], f"{name} no longer pins the pgvector image"
    assert len(set().union(*tags.values())) == 1, tags
    assert len(set().union(*digests.values())) == 1, digests


def test_version_is_identical_everywhere() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    version = project["project"]["version"]
    chart = (ROOT / "charts/knowledge-vault/Chart.yaml").read_text(encoding="utf-8")
    plugin = json.loads(
        (ROOT / "plugin/knowledge-vault/.codex-plugin/plugin.json").read_text(encoding="utf-8")
    )
    found = {
        "Chart.yaml version": re.search(r"^version: (\S+)$", chart, re.M).group(1),  # type: ignore[union-attr]
        "Chart.yaml appVersion": re.search(r'^appVersion: "([^"]+)"$', chart, re.M).group(1),  # type: ignore[union-attr]
        "plugin.json": plugin["version"],
        # The running service reports the installed package version, not a second literal.
        "Settings().version": Settings(environment="test").version,
        **{
            f"{name} label": match
            for name in ("Dockerfile", "Dockerfile.backup")
            for match in re.findall(
                r'org\.opencontainers\.image\.version="([^"]+)"',
                (ROOT / name).read_text(encoding="utf-8"),
            )
        },
    }
    assert set(found.values()) == {version}, found


def test_release_workflow_publishes_no_latest_tag() -> None:
    """docker/metadata-action adds `latest` to semver releases unless told not to."""
    workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    steps = re.split(r"\n      - ", workflow)
    metadata_steps = [step for step in steps if "docker/metadata-action@" in step]
    assert len(metadata_steps) == 2, "expected one metadata step per published image"
    for step in metadata_steps:
        assert "flavor: latest=false" in step, step
        assert "type=raw" not in step and "latest=true" not in step, step

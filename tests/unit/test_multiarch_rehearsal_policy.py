"""Guard the opt-in rehearsal's public-only, nonpublishing execution path."""

from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parents[2] / ".github/workflows/ci.yml"


def test_multiarch_dispatch_is_public_only_and_does_not_publish() -> None:
    workflow = yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader)  # noqa: S506 - strings only
    inputs = workflow["on"]["workflow_dispatch"]["inputs"]
    assert inputs["multiarch_archives"]["default"] == "false"
    jobs = workflow["jobs"]
    for name in ("quality", "supply-chain", "kubernetes", "inspector"):
        assert jobs[name]["if"] == (
            "github.event_name != 'workflow_dispatch' || "
            "(!inputs.native_images && !inputs.multiarch_archives)"
        )
    job = jobs["multiarch-archive"]
    assert job["if"] == (
        "github.event_name == 'workflow_dispatch' && inputs.multiarch_archives && "
        "!inputs.native_images && github.event.repository.private == false"
    )
    assert job["permissions"] == {"contents": "read"}
    assert job["strategy"]["matrix"]["image"] == ["app", "backup"]
    assert job["env"]["DOCKER_BUILD_RECORD_UPLOAD"] == "false"
    assert job["env"]["DOCKER_BUILD_SUMMARY"] == "false"
    assert [step["uses"].split("@")[0] for step in job["steps"] if "uses" in step] == [
        "actions/checkout",
        "docker/setup-buildx-action",
    ]
    assert job["steps"][0]["with"]["persist-credentials"] == "false"
    assert job["steps"][1]["with"]["cache-binary"] == "false"
    build = job["steps"][2]["run"]
    assert "--platform linux/amd64,linux/arm64" in build
    assert "--provenance=mode=max" in build
    assert "--attest type=sbom,generator=" in build
    assert "type=oci,dest=$RUNNER_TEMP/" in build
    assert "--push" not in build and "--load" not in build
    assert "sha256:" in job["steps"][1]["with"]["driver-opts"]
    assert "sha256:" in build
    validation = job["steps"][3]["run"]
    assert "sudo unshare --net -- setpriv" in validation
    assert "--clear-groups --no-new-privs -- sh -eu -c" in validation
    assert 'test "$(id -u)" != 0' in validation
    assert 'ip -o link show lo | grep -q "state DOWN"' in validation
    assert "exec python3 -m scripts.ci.oci_archive" in validation
    assert "secrets." not in str(job)


def test_optional_rehearsals_cannot_run_together() -> None:
    workflow = yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader)  # noqa: S506 - strings only
    jobs = workflow["jobs"]
    assert "!inputs.multiarch_archives" in jobs["native-image"]["if"]
    assert "inputs.native_images && inputs.multiarch_archives" in jobs["dispatch-selection"]["if"]
    assert "exit 1" in jobs["dispatch-selection"]["steps"][0]["run"]

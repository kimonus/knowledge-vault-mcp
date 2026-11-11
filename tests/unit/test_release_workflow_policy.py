"""Guard the separation between manual rehearsal and tag-triggered publication."""

from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parents[2] / ".github/workflows/release.yml"


def _workflow() -> dict:
    return yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)  # noqa: S506 - BaseLoader constructs strings only


def test_manual_rehearsal_has_no_publishing_permissions_or_storage() -> None:
    workflow = _workflow()
    assert set(workflow["on"]) == {"push", "workflow_dispatch"}
    jobs = workflow["jobs"]
    assert jobs["verify"]["permissions"] == {"contents": "read"}
    uv = next(
        step
        for step in jobs["verify"]["steps"]
        if step.get("uses", "").startswith("astral-sh/setup-uv@")
    )
    assert uv["with"]["enable-cache"] == "${{ github.event_name == 'push' }}"

    job = jobs["rehearse"]
    assert job["if"] == "github.event_name == 'workflow_dispatch'"
    assert job["needs"] == "verify"
    assert job["permissions"] == {"contents": "read"}
    assert job["env"]["DOCKER_BUILD_RECORD_UPLOAD"] == "false"
    assert job["env"]["DOCKER_BUILD_SUMMARY"] == "false"
    steps = job["steps"]
    actions = [step for step in steps if "uses" in step]
    assert [step["uses"].split("@")[0] for step in actions] == [
        "actions/checkout",
        "docker/setup-buildx-action",
        "docker/build-push-action",
        "docker/build-push-action",
    ]
    assert actions[0]["with"]["persist-credentials"] == "false"
    assert actions[1]["with"]["cache-binary"] == "false"
    for step in actions[2:]:
        inputs = step["with"]
        assert inputs["push"] == "false"
        assert inputs["platforms"] == "linux/amd64"
        assert inputs["context"] == "."
        assert not (
            {"cache-from", "cache-to", "secrets", "secret-envs", "secret-files", "ssh", "outputs"}
            & inputs.keys()
        )
    assert actions[3]["with"]["file"] == "Dockerfile.backup"
    assert any('helm" package charts/knowledge-vault' in step.get("run", "") for step in steps)
    assert "secrets." not in str(job)


def test_publication_is_tag_push_only_and_retains_security_gates() -> None:
    workflow = _workflow()
    assert workflow["on"]["push"]["tags"] == ["v*.*.*"]
    publish = workflow["jobs"]["publish"]
    assert publish["if"] == "github.event_name == 'push'"
    assert publish["needs"] == "verify"
    assert publish["permissions"] == {
        "contents": "write",
        "packages": "write",
        "id-token": "write",
    }
    steps = publish["steps"]
    builds = [
        step for step in steps if step.get("uses", "").startswith("docker/build-push-action@")
    ]
    assert len(builds) == 2
    for step in builds:
        assert step["with"]["push"] == "${{ github.event_name == 'push' }}"
        assert step["with"]["platforms"] == "linux/amd64"
        assert step["with"]["provenance"] == "mode=max"
        assert step["with"]["sbom"] == "true"
    names = [step.get("name") for step in steps]
    assert names.index("Scan the published image digests") < names.index(
        "Sign immutable image digests"
    )
    assert names.index("Sign immutable image digests") < names.index("Create GitHub release")
    verify_commands = "\n".join(step.get("run", "") for step in workflow["jobs"]["verify"]["steps"])
    assert "test_backup_restore.sh" in verify_commands
    assert verify_commands.count('"$RUNNER_TEMP/bin/trivy" image') == 2

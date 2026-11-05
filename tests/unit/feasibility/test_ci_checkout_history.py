"""Regression for missing SOURCE ancestors after retry and correction commits."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from scripts.ci import task_0014_guards as guards

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ".github/workflows/task-0014-synthetic-smoke.yml"


def git(root, *args):
    # Only local fixture repositories; no inherited hooks, identity or signing settings.
    env = {
        **{key: value for key, value in os.environ.items() if not key.startswith("GIT_")},
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_AUTHOR_NAME": "Fixture",
        "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
        "GIT_COMMITTER_NAME": "Fixture",
        "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
        "GIT_AUTHOR_DATE": "2000-01-01T00:00:00+00:00",
        "GIT_COMMITTER_DATE": "2000-01-01T00:00:00+00:00",
        "GIT_TERMINAL_PROMPT": "0",
    }
    return subprocess.run(  # noqa: S603  # Fixed Git executable; pytest-owned local paths.
        ["/usr/bin/git", "-C", str(root), *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


@pytest.fixture
def history(tmp_path):
    author = tmp_path / "author"
    author.mkdir()
    assert git(author, "init", "--quiet").returncode == 0
    source = None
    for index in range(7):
        (author / "fixture").write_text(str(index))
        assert git(author, "add", "fixture").returncode == 0
        assert git(author, "commit", "--quiet", "-m", "fixture").returncode == 0
        if index == 1:
            source = git(author, "rev-parse", "HEAD").stdout.strip()
    assert source
    return author, source


def test_git_fixture_does_not_use_inherited_repository_or_hooks(tmp_path, monkeypatch):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    foreign = tmp_path / "foreign"
    monkeypatch.setenv("GIT_DIR", str(foreign))
    monkeypatch.setenv("GIT_WORK_TREE", str(tmp_path / "foreign-worktree"))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.hooksPath")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", str(tmp_path / "foreign-hooks"))
    assert git(checkout, "init", "--quiet").returncode == 0
    assert (checkout / ".git").is_dir()
    assert not foreign.exists()


@pytest.mark.parametrize("depth", [0, 2, 3])
def test_configured_history_preserves_source_after_additional_commits(tmp_path, history, depth):
    author, source = history
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    assert git(checkout, "init", "--quiet").returncode == 0
    args = ["fetch", "--no-tags", "--no-write-fetch-head"]
    if depth:
        args.append(f"--depth={depth}")
    # File path transport only: never a registry, GitHub API or remote Git endpoint.
    args.extend([str(author), "HEAD"])
    assert git(checkout, *args).returncode == 0
    available = git(checkout, "cat-file", "-t", source)
    if depth:
        assert available.returncode != 0
    else:
        assert available.returncode == 0 and available.stdout.strip() == "commit"
        workflow = yaml.load((ROOT / WORKFLOW).read_text(), Loader=yaml.BaseLoader)  # noqa: S506
        checkouts = [
            step["with"]
            for step in workflow["jobs"]["smoke"]["steps"]
            if step.get("uses", "").startswith("actions/checkout@")
        ]
        assert checkouts[0]["fetch-depth"] == str(depth)


@pytest.mark.parametrize("depth", [0, 2, 3])
@pytest.mark.parametrize("target", ["orchestration", "source"])
def test_workflow_guard_rejects_shallow_checkout_history(tmp_path, depth, target):
    workflow_dir = tmp_path / ".github/workflows"
    workflow_dir.mkdir(parents=True)
    for name in ("ci.yml", "release.yml", "task-0014-synthetic-smoke.yml"):
        content = (ROOT / ".github/workflows" / name).read_text()
        if name == "task-0014-synthetic-smoke.yml":
            content = content.replace(
                f"path: {target}\n          fetch-depth: 0",
                f"path: {target}\n          fetch-depth: {depth}",
                1,
            )
        (workflow_dir / name).write_text(content)
    if depth:
        with pytest.raises(AssertionError):
            guards.main(tmp_path)
    else:
        guards.main(tmp_path)


def test_bootstrap_gate_works_without_project_dependencies():
    code = """
import importlib.util
import os
import runpy
import sys
from unittest.mock import patch

assert importlib.util.find_spec('yaml') is None
os.environ['EXPECTED_HEAD'] = ''
with patch('urllib.request.urlopen', side_effect=AssertionError('network forbidden')):
    try:
        runpy.run_path(sys.argv[1], run_name='__main__')
    except SystemExit as exc:
        assert str(exc) == 'BLOCKED: invalid_head'
    else:
        raise AssertionError('invalid input authorized')
"""
    result = subprocess.run(  # noqa: S603  # Fixed isolated interpreter and offline test code.
        [sys.executable, "-I", "-S", "-c", code, str(ROOT / "scripts/ci/task_0014_ci_gate.py")],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    assert result.stdout == result.stderr == ""


def test_project_checkout_guard_uses_locked_python_after_sync_before_oci():
    workflow = yaml.load((ROOT / WORKFLOW).read_text(), Loader=yaml.BaseLoader)  # noqa: S506
    steps = workflow["jobs"]["smoke"]["steps"]
    preparation = next(step for step in steps if step.get("name", "").startswith("Prepare owned"))
    assert "task_0014_guards.py" not in preparation["run"]
    runner = (ROOT / "scripts/ci/task_0014_run.sh").read_text()
    call = (
        "timeout -k 5s 30s .venv/bin/python "
        '"$staging/orchestration/scripts/ci/task_0014_guards.py" '
        '"$staging/orchestration" "$staging/source"'
    )
    assert runner.count(call) == 1
    assert runner.index("uv sync --locked") < runner.index(call) < runner.index("stage=oci_fetch")

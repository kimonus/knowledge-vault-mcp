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


@pytest.fixture
def cleanup(tmp_path):
    """Execute the real cleanup shell with command doubles and private filesystem roots."""
    workflow = yaml.load((ROOT / WORKFLOW).read_text(), Loader=yaml.BaseLoader)  # noqa: S506
    step = next(
        s for s in workflow["jobs"]["smoke"]["steps"] if s.get("name", "").startswith("Stop owned")
    )
    assert step["if"] == "always()" and step["timeout-minutes"] == "2"
    staging = tmp_path / "kv-task-0014.A1b2C3d4"
    staging.mkdir(mode=0o700)
    (staging / "sentinel").write_text("fixture")
    cgroups = tmp_path / "cgroups"
    cgroups.mkdir()
    unit = "task-0014-123.service"
    state = tmp_path / "state"
    calls = tmp_path / "calls"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    doubles = {
        "systemctl": """#!/bin/bash
echo "$*" >> "$CALLS"
case "$1" in
  stop)
    if test "$STOP_EXIT" = 0 && test -n "${AFTER_STOP:-}"; then
      printf '%s' "$AFTER_STOP" > "$STATE"
    fi
    echo 'untrusted failure body' >&2; exit "$STOP_EXIT" ;;
  show) cat "$STATE"; echo 'untrusted response body' >&2; exit "$SHOW_EXIT" ;;
  *) exit 97 ;;
esac
""",
        "sudo": """#!/bin/bash
if test "$1" = systemctl; then exec "$@"; fi
test "$1:$2:$3:$4" = '-u:#1000:-g:#1000' || exit 97
shift 4
test "$1" = python || exit 97
shift
exec "$TEST_PYTHON" "$@"
""",
    }
    for name, content in doubles.items():
        path = bin_dir / name
        path.write_text(content)
        path.chmod(0o700)
    script = tmp_path / "cleanup.sh"
    script_text = (
        step["run"]
        .replace("/sys/fs/cgroup/system.slice", str(cgroups))
        .replace("/var/tmp/kv-task-0014.", str(tmp_path / "kv-task-0014."))  # noqa: S108
    )

    def run(
        state_text="LoadState=not-found\nActiveState=inactive\nControlGroup=\n",
        *,
        fault=None,
        **overrides,
    ):
        state.write_text(state_text)
        code = script_text
        if fault is not None:
            setup = (
                f"fault = {fault!r}\ncgroup_path = {str(cgroups / unit)!r}\n"
                + CGROUP_FAULT_INJECTION
            )
            assert code.count("import os\nimport stat") == 1
            code = code.replace("import os\nimport stat", setup + "\nimport os\nimport stat")
        script.write_text(code)
        env = {
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "TASK_0014_UNIT": unit,
            "TASK_0014_STAGING": str(staging),
            "GITHUB_RUN_ID": "123",
            "STOP_EXIT": "5",
            "SHOW_EXIT": "0",
            "STATE": str(state),
            "CALLS": str(calls),
            "TEST_PYTHON": sys.executable,
            **overrides,
        }
        return subprocess.run(  # noqa: S603  # Actual shell; doubles only, pytest-owned roots.
            ["/bin/bash", str(script)], env=env, capture_output=True, text=True, timeout=10
        )

    return run, staging, cgroups / unit, calls


# Inject failures into the actual embedded implementation, including when pytest runs as root.
# No production environment switch or permission changes are needed for these negative paths.
CGROUP_FAULT_INJECTION = """
import contextlib
import os
from pathlib import Path
real_scandir, real_open, real_read = os.scandir, os.open, os.read
child_path = Path(cgroup_path) / 'child'
def mark():
    (Path(cgroup_path).parent / 'fault-exercised').write_text(fault)
def is_child(fd):
    return os.readlink('/proc/self/fd/' + str(fd)) == str(child_path)
scans = 0
@contextlib.contextmanager
def injected_scandir(fd):
    global scans
    if fault == 'enumeration-error' or (fault == 'directory-permission' and is_child(fd)):
        mark()
        raise PermissionError('UNTRUSTED directory error /private/path')
    if fault == 'changed-inventory' and is_child(fd):
        scans += 1
        if scans == 2:
            mark()
            (child_path / 'new-control-file').write_text('')
    with real_scandir(fd) as entries:
        def items():
            for entry in entries:
                if fault == 'iteration-error':
                    mark()
                    raise OSError('UNTRUSTED iteration error /private/path')
                if fault == 'stat-error':
                    class BrokenEntry:
                        name = entry.name
                        def stat(self, **kwargs):
                            mark()
                            raise OSError('UNTRUSTED stat error /private/path')
                    yield BrokenEntry()
                else:
                    yield entry
        yield items()
def injected_open(path, flags, *args, **kwargs):
    if path == cgroup_path and fault == 'root-disappears':
        mark()
        import shutil
        shutil.rmtree(cgroup_path)
    if path == 'child' and fault == 'directory-disappears':
        mark()
        import shutil
        shutil.rmtree(child_path)
    if path == 'child' and fault == 'directory-replaced':
        mark()
        child_path.rename(child_path.with_name('old-child'))
        child_path.mkdir()
        (child_path / 'cgroup.procs').write_text('')
    if path == 'cgroup.procs':
        child = is_child(kwargs['dir_fd'])
        if fault == 'root-procs-unreadable' or (child and fault == 'procs-unreadable'):
            mark()
            raise PermissionError('UNTRUSTED inventory error /private/path')
        if child and fault == 'procs-disappears':
            mark()
            (child_path / 'cgroup.procs').unlink()
        if child and fault == 'procs-replaced':
            mark()
            procs = child_path / 'cgroup.procs'
            procs.rename(child_path / 'old-procs')
            procs.write_text('')
    return real_open(path, flags, *args, **kwargs)
def injected_read(*args):
    if fault == 'read-error':
        mark()
        raise OSError('UNTRUSTED read error /private/path')
    return real_read(*args)
os.scandir, os.open, os.read = injected_scandir, injected_open, injected_read
"""


def assert_cleanup_blocked(result, staging):
    assert result.returncode != 0
    assert (staging / "sentinel").is_file()
    assert "owner_private_staging_removed=true" not in result.stdout
    assert "unfinished_service_descendants=0" not in result.stdout
    assert str(staging.parent) not in result.stdout + result.stderr
    assert all(
        text not in result.stdout + result.stderr
        for text in (
            "UNTRUSTED",
            "untrusted",
            "/private/path",
            "Traceback",
        )
    )


def test_cleanup_inspects_empty_nested_cgroups(cleanup):
    run, staging, cgroup, _ = cleanup
    for directory in (cgroup, cgroup / "child", cgroup / "child/grandchild"):
        directory.mkdir()
        (directory / "cgroup.procs").write_text("")
    assert run().returncode == 0
    assert not staging.exists()


@pytest.mark.parametrize(
    "fault",
    [
        "directory-permission",
        "procs-unreadable",
        "directory-disappears",
        "procs-disappears",
        "enumeration-error",
        "iteration-error",
        "stat-error",
        "root-disappears",
        "directory-replaced",
        "procs-replaced",
        "changed-inventory",
        "read-error",
    ],
)
def test_cleanup_blocks_incomplete_or_racing_traversal(cleanup, fault):
    run, staging, cgroup, _ = cleanup
    cgroup.mkdir()
    (cgroup / "cgroup.procs").write_text("")
    (cgroup / "child").mkdir()
    (cgroup / "child/cgroup.procs").write_text("")
    result = run(fault=fault)
    assert_cleanup_blocked(result, staging)
    assert "BLOCKED:" in result.stderr
    assert (cgroup.parent / "fault-exercised").read_text() == fault


@pytest.mark.parametrize(
    "kind", ["missing-procs", "directory-symlink", "procs-symlink", "procs-directory"]
)
def test_cleanup_blocks_invalid_descendant_inventory(cleanup, kind):
    run, staging, cgroup, _ = cleanup
    cgroup.mkdir()
    (cgroup / "cgroup.procs").write_text("")
    child = cgroup / "child"
    if kind == "directory-symlink":
        child.symlink_to(cgroup.parent, target_is_directory=True)
    else:
        child.mkdir()
        if kind == "procs-symlink":
            (child / "cgroup.procs").symlink_to(cgroup / "cgroup.procs")
        elif kind == "procs-directory":
            (child / "cgroup.procs").mkdir()
    assert_cleanup_blocked(run(), staging)


@pytest.mark.parametrize("contents", ["123\n", "garbage\n", " \n", "\x00", "0\n", "123"])
def test_cleanup_rejects_every_nonempty_descendant_inventory(cleanup, contents):
    run, staging, cgroup, _ = cleanup
    cgroup.mkdir()
    (cgroup / "cgroup.procs").write_text("")
    (cgroup / "child").mkdir()
    (cgroup / "child/cgroup.procs").write_text(contents)
    assert_cleanup_blocked(run(), staging)


@pytest.mark.parametrize(
    ("load", "active", "stop"),
    [
        ("not-found", "inactive", "5"),
        ("not-found", "inactive", "0"),
        ("loaded", "inactive", "0"),
        ("loaded", "failed", "0"),
    ],
)
def test_cleanup_removes_staging_only_after_settled_unit(cleanup, load, active, stop):
    run, staging, cgroup, calls = cleanup
    cgroup.mkdir()
    (cgroup / "cgroup.procs").write_text("")
    result = run(f"ActiveState={active}\nControlGroup=\nLoadState={load}\n", STOP_EXIT=stop)
    assert result.returncode == 0, result.stderr
    assert not staging.exists()
    assert "unfinished_service_descendants=0 private_tmpfs_released=true" in result.stdout
    assert "owner_private_staging_removed=true" in result.stdout
    assert "untrusted" not in result.stdout + result.stderr
    assert calls.read_text().splitlines() == [
        "stop task-0014-123.service",
        "show task-0014-123.service -p LoadState -p ActiveState -p ControlGroup",
    ]


def test_cleanup_accepts_unloaded_unit_with_absent_cgroup(cleanup):
    run, staging, _, _ = cleanup
    assert run().returncode == 0
    assert not staging.exists()


@pytest.mark.parametrize("before", ["active", "failed"])
@pytest.mark.parametrize("after", ["loaded", "not-found"])
def test_cleanup_loaded_unit_transitions_to_settled_state(cleanup, before, after):
    run, staging, _, _ = cleanup
    result = run(
        f"LoadState=loaded\nActiveState={before}\nControlGroup=/system.slice/task-0014-123.service\n",
        STOP_EXIT="0",
        AFTER_STOP=f"LoadState={after}\nActiveState=inactive\nControlGroup=\n",
    )
    assert result.returncode == 0, result.stderr
    assert not staging.exists()


@pytest.mark.parametrize(
    ("state", "overrides"),
    [
        ("LoadState=loaded\nActiveState=inactive\nControlGroup=\n", {}),
        ("LoadState=not-found\nActiveState=inactive\nControlGroup=\n", {"STOP_EXIT": "1"}),
        ("LoadState=not-found\nActiveState=inactive\nControlGroup=\n", {"STOP_EXIT": "127"}),
        ("LoadState=not-found\nActiveState=inactive\nControlGroup=\n", {"SHOW_EXIT": "1"}),
        ("LoadState=loaded\nActiveState=active\nControlGroup=\n", {"STOP_EXIT": "0"}),
        ("LoadState=loaded\nActiveState=deactivating\nControlGroup=\n", {"STOP_EXIT": "0"}),
        ("LoadState=error\nActiveState=inactive\nControlGroup=\n", {}),
        ("LoadState=not-found\nActiveState=failed\nControlGroup=\n", {}),
        ("LoadState=not-found\nActiveState=inactive\nControlGroup=/foreign\n", {}),
        ("LoadState=not-found\nActiveState=inactive\n", {}),
        ("LoadState=not-found\nActiveState=inactive\nControlGroup=\nLoadState=loaded\n", {}),
        ("LoadState=not-found\nActiveState=inactive\nControlGroup=\nUnexpected=value\n", {}),
        ("", {}),
    ],
)
def test_cleanup_rejects_unsafe_or_malformed_unit_state(cleanup, state, overrides):
    run, staging, _, _ = cleanup
    result = run(state, **overrides)
    assert_cleanup_blocked(result, staging)


@pytest.mark.parametrize("descendant", [False, True])
def test_cleanup_rejects_remaining_processes_even_after_unit_unloaded(cleanup, descendant):
    run, staging, cgroup, _ = cleanup
    cgroup.mkdir()
    (cgroup / "cgroup.procs").write_text("" if descendant else "123\n")
    if descendant:
        (cgroup / "child").mkdir()
        (cgroup / "child/cgroup.procs").write_text("456\n")
    result = run()
    assert_cleanup_blocked(result, staging)
    assert "BLOCKED: unfinished_service_descendants" in result.stderr
    assert staging.exists()


@pytest.mark.parametrize("kind", ["missing-procs", "symlink", "unreadable"])
def test_cleanup_rejects_invalid_cgroup(cleanup, kind):
    run, staging, cgroup, _ = cleanup
    if kind == "symlink":
        cgroup.symlink_to(cgroup.parent / "absent", target_is_directory=True)
    else:
        cgroup.mkdir()
        if kind == "unreadable":
            (cgroup / "cgroup.procs").write_text("")
            (cgroup / "cgroup.procs").chmod(0)
    result = run(fault="root-procs-unreadable" if kind == "unreadable" else None)
    assert_cleanup_blocked(result, staging)
    if kind == "unreadable":
        assert (cgroup.parent / "fault-exercised").read_text() == "root-procs-unreadable"
    assert "BLOCKED:" in result.stderr and "Traceback" not in result.stderr
    assert staging.exists()


@pytest.mark.parametrize("kind", ["mode", "path", "symlink", "owner", "unit", "run-id"])
def test_cleanup_preserves_staging_when_identity_is_invalid(cleanup, kind):
    run, staging, _, calls = cleanup
    overrides = {}
    if kind == "mode":
        staging.chmod(0o755)
    elif kind == "path":
        overrides["TASK_0014_STAGING"] = str(staging.parent)
    elif kind == "symlink":
        target = staging.with_name("target")
        staging.rename(target)
        staging.symlink_to(target, target_is_directory=True)
    elif kind == "owner":
        # No chown/root: double stat's identity result while retaining the actual shell check.
        stat = staging.parent / "bin/stat"
        stat.write_text("#!/bin/sh\necho 700:0:0\n")
        stat.chmod(0o700)
    elif kind == "unit":
        overrides["TASK_0014_UNIT"] = "foreign.service"
    else:
        overrides["GITHUB_RUN_ID"] = "../123"
    result = run(**overrides)
    assert_cleanup_blocked(result, staging)
    if kind in {"unit", "run-id"}:
        assert not calls.exists()


def test_cleanup_patch_preserves_all_other_workflow_contracts():
    before = git(ROOT, "show", f"1597eb61fe09e0445c96b2779c01f89c3e8dd509:{WORKFLOW}")
    assert before.returncode == 0
    original = yaml.load(before.stdout, Loader=yaml.BaseLoader)  # noqa: S506
    revised = yaml.load((ROOT / WORKFLOW).read_text(), Loader=yaml.BaseLoader)  # noqa: S506
    previous_branch = "github.head_ref == 'diag/task-0014-runtime-retry'"
    current_branch = "github.head_ref == 'diag/task-0014-cleanup-v2-retry'"
    assert guards.BRANCH == "diag/task-0014-cleanup-v2-retry"
    original_if = original["jobs"]["smoke"]["if"]
    assert original_if.count(previous_branch) == 1
    expected_if = original_if.replace(previous_branch, current_branch, 1)
    assert revised["jobs"]["smoke"]["if"] == expected_if
    original["jobs"]["smoke"]["if"] = expected_if
    for workflow in (original, revised):
        step = workflow["jobs"]["smoke"]["steps"][-1]
        assert step["name"] == "Stop owned unit and clean only settled owner staging"
        del step["run"]
    assert original == revised

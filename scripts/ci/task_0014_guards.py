"""Offline event truth table and fixed-byte workflow contract, repeated before fetch."""

import hashlib
import itertools
import re
import subprocess
import sys
from pathlib import Path

import yaml

BRANCH = "diag/task-0014-umask-fixture"
SOURCE = "14f9517e51e5221a00ae53833cf03360298f0e7c"
BASE_CI = "ea25e70a377c4844119d8a681d032177f6ab4a86574a1a27d07201716b751fa6"
BASE_RELEASE = "efd61c675a3b9fdb4f6d00f5529d89e6a53ca97075e3eefd08eaf7eb071eaf1b"
GUARD = (
    "    if: ${{ !(github.event_name == 'pull_request' && "
    "github.event.pull_request.head.repo.full_name == github.repository && "
    f"github.head_ref == '{BRANCH}') }}}}\n"
)


def main(root: Path) -> None:
    workflow = root / ".github/workflows"
    ci = (workflow / "ci.yml").read_text()
    assert ci.count(GUARD) == 4
    assert hashlib.sha256(ci.replace(GUARD, "").encode()).hexdigest() == BASE_CI
    assert hashlib.sha256((workflow / "release.yml").read_bytes()).hexdigest() == BASE_RELEASE
    jobs = yaml.load(ci, Loader=yaml.BaseLoader)["jobs"]  # noqa: S506  # Scalars only; no objects.
    assert set(jobs) == {"quality", "supply-chain", "kubernetes", "inspector"}
    expression = GUARD.strip()[len("if: ${{ ") : -len(" }}")]
    cases = 0
    for event, same_repo, branch in itertools.product(
        ("pull_request", "push", "workflow_dispatch"),
        (True, False),
        (BRANCH, "main", "feature/other", "smoke/task-0012-gha-synthetic-e2e"),
    ):
        actual = expression
        for key, value in (
            ("github.event.pull_request.head.repo.full_name", "same" if same_repo else "fork"),
            ("github.repository", "same"),
            ("github.event_name", event),
            ("github.head_ref", branch),
        ):
            actual = actual.replace(key, repr(value))
        actual = actual.replace("&&", "and").replace("!", "not ")
        # Expression equals the fixed reviewed literal above before evaluation.
        result = eval(actual, {"__builtins__": {}}, {})  # noqa: S307
        assert result == (not (event == "pull_request" and same_repo and branch == BRANCH))
        cases += 1
    smoke = yaml.load(
        (workflow / "task-0014-synthetic-smoke.yml").read_text(),
        Loader=yaml.BaseLoader,  # noqa: S506  # Scalars only; no object construction.
    )
    assert smoke["on"] == {
        "pull_request": {
            "types": ["opened"],
            "branches": ["main"],
            "paths": [".github/workflows/task-0014-synthetic-smoke.yml", "scripts/ci/task_0014_*"],
        }
    }
    assert smoke["permissions"] == {"contents": "read"}
    assert smoke["concurrency"] == {
        "group": "task-0014-single-synthetic-smoke",
        "cancel-in-progress": "false",
    }
    assert set(smoke["jobs"]) == {"smoke"}
    job = smoke["jobs"]["smoke"]
    assert job["runs-on"] == "ubuntu-22.04" and job["timeout-minutes"] == "30"
    assert job["if"].strip() == (
        "github.event_name == 'pull_request' && github.event.action == 'opened' && "
        "github.run_attempt == 1 && github.repository == 'kimonus/knowledge-vault-mcp' && "
        "github.event.repository.private == false && "
        "github.event.pull_request.head.repo.full_name == github.repository && "
        f"github.head_ref == '{BRANCH}'"
    )
    smoke_cases = 0
    for event, action, attempt, repo, private, same_repo, branch in itertools.product(
        ("pull_request", "push", "pull_request_target"),
        ("opened", "synchronize", "reopened"),
        (1, 2),
        ("kimonus/knowledge-vault-mcp", "other/repository"),
        (False, True),
        (True, False),
        (BRANCH, "diag/task-0013-safe-startup", "main"),
    ):
        actual = job["if"].strip()
        for key, value in (
            ("github.event.pull_request.head.repo.full_name", repo if same_repo else "fork/repo"),
            ("github.event.repository.private", private),
            ("github.event_name", event),
            ("github.event.action", action),
            ("github.run_attempt", attempt),
            ("github.repository", repo),
            ("github.head_ref", branch),
        ):
            actual = actual.replace(key, repr(value))
        actual = actual.replace("&&", "and").replace("false", "False")
        result = eval(actual, {"__builtins__": {}}, {})  # noqa: S307  # Fixed expression above.
        assert result == (
            event == "pull_request"
            and action == "opened"
            and attempt == 1
            and repo == "kimonus/knowledge-vault-mcp"
            and not private
            and same_repo
            and branch == BRANCH
        )
        smoke_cases += 1
    print(f"smoke_predicate_cases={smoke_cases} passed")
    steps = job["steps"]
    checkouts = [step for step in steps if step.get("uses", "").startswith("actions/checkout@")]
    assert len(checkouts) == 2
    assert checkouts[0]["with"]["ref"] == "${{ github.event.pull_request.head.sha }}"
    assert checkouts[1]["with"]["ref"] == SOURCE
    assert all(step["with"]["persist-credentials"] == "false" for step in checkouts)
    for step in steps:
        if "uses" in step:
            assert re.fullmatch(r"[a-zA-Z0-9_/-]+@[a-f0-9]{40}", step["uses"])
    assert len([step for step in steps if step.get("uses")]) == 4
    assert all("secrets." not in str(step) for step in steps)
    print(f"guard_truth_table_cases={cases} ci_delta=exactly_four_guards workflow_contract=passed")


def checkouts(orchestration: Path, source: Path) -> None:
    def git(root, *args):
        return subprocess.check_output(  # noqa: S603  # Fixed read-only Git operations.
            ["/usr/bin/git", "-C", str(root), *args], text=True
        ).strip()

    baseline = "578f4758a81107e3c517c71afd80bda4d13d95d3"
    expected_source = {
        "scripts/feasibility_diagnostics.py",
        "scripts/feasibility_lifecycle.py",
        "scripts/feasibility_spike.py",
        "scripts/feasibility_support/server.py",
        "scripts/feasibility_support/server.sh",
        "scripts/feasibility_support/startup.py",
        "tests/unit/feasibility/test_feasibility_diagnostics.py",
    }
    expected_orchestration = {
        ".github/workflows/ci.yml",
        ".github/workflows/task-0014-synthetic-smoke.yml",
        *(
            "scripts/ci/task_0014_" + name
            for name in ("bwrap.sh", "ci_gate.py", "guards.py", "offline.py", "run.sh", "verify.py")
        ),
    }
    assert git(source, "rev-parse", "HEAD") == SOURCE
    assert git(orchestration, "rev-parse", "HEAD") != SOURCE
    for root in (orchestration, source):
        assert not git(root, "status", "--porcelain")
    assert set(git(source, "diff", "--name-only", baseline, SOURCE).splitlines()) == expected_source
    assert not git(source, "diff", baseline, SOURCE, "--", "uv.lock", "pyproject.toml")
    assert (
        set(git(orchestration, "diff", "--name-only", SOURCE, "HEAD").splitlines())
        == expected_orchestration
    )
    print("checkout_diff_integrity=passed lockfile_unchanged=true baseline=" + baseline)


if __name__ == "__main__":
    main(Path(sys.argv[1]))
    if len(sys.argv) == 3:
        checkouts(Path(sys.argv[1]), Path(sys.argv[2]))

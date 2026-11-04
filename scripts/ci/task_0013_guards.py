"""Offline event truth table and fixed-byte workflow contract, repeated before fetch."""

import hashlib
import itertools
import re
import sys
from pathlib import Path

import yaml

BRANCH = "diag/task-0013-safe-startup"
SOURCE = "56b72e7ad36c1f158ab5ee97555b7cc8cecb453e"
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
        (workflow / "task-0013-synthetic-smoke.yml").read_text(),
        Loader=yaml.BaseLoader,  # noqa: S506  # Scalars only; no object construction.
    )
    assert smoke["on"] == {
        "pull_request": {
            "types": ["opened"],
            "branches": ["main"],
            "paths": [".github/workflows/task-0013-synthetic-smoke.yml", "scripts/ci/task_0013_*"],
        }
    }
    assert smoke["permissions"] == {"contents": "read"}
    assert smoke["concurrency"] == {
        "group": "task-0013-single-synthetic-smoke",
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


if __name__ == "__main__":
    main(Path(sys.argv[1]))

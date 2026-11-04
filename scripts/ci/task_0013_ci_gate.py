"""Bounded wait for ordinary CI to be skipped; unauthenticated public API only."""

import json
import os
import time
import urllib.request

base = "https://api.github.com/repos/kimonus/knowledge-vault-mcp"
deadline = time.monotonic() + 120


def read(path):
    request = urllib.request.Request(  # noqa: S310  # Fixed public GitHub HTTPS origin.
        base + path, headers={"Accept": "application/vnd.github+json"}
    )
    with urllib.request.urlopen(request, timeout=15) as response:  # noqa: S310
        return json.load(response)


while time.monotonic() < deadline:
    runs = read("/actions/runs?event=pull_request&head_sha=" + os.environ["EXPECTED_HEAD"])[
        "workflow_runs"
    ]
    ci = [
        run
        for run in runs
        if run["name"] == "CI" and run["head_branch"] == "diag/task-0013-safe-startup"
    ]
    assert len(ci) <= 1, "duplicate CI runs"
    if ci:
        jobs = read("/actions/runs/" + str(ci[0]["id"]) + "/jobs")["jobs"]
        if jobs:
            assert {job["name"] for job in jobs} == {
                "quality",
                "supply-chain",
                "kubernetes",
                "inspector",
            }
            assert all(job["conclusion"] == "skipped" and not job["runner_id"] for job in jobs), (
                "ordinary CI did not skip"
            )
            print(
                json.dumps(
                    {
                        "ci_run": ci[0]["html_url"],
                        "ci_jobs": [
                            {key: job[key] for key in ("id", "name", "conclusion", "runner_id")}
                            for job in jobs
                        ],
                    }
                )
            )
            break
    time.sleep(5)
else:
    raise RuntimeError("BLOCKED: CI exclusion evidence unavailable before fetch")

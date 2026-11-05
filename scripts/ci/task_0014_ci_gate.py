"""Bounded wait for ordinary CI to be skipped; unauthenticated public API only."""

import http.client
import json
import os
import re
import time
import urllib.error
import urllib.request

REPOSITORY = "kimonus/knowledge-vault-mcp"
base = "https://api.github.com/repos/" + REPOSITORY
BRANCH = "diag/task-0014-bootstrap-retry"
JOBS = {"quality", "supply-chain", "kubernetes", "inspector"}


class GateError(RuntimeError):
    """Finite, safe failure reason; never includes API bodies or exception text."""


def read(path, *, timeout):
    request = urllib.request.Request(  # noqa: S310  # Fixed public GitHub HTTPS origin.
        base + path, headers={"Accept": "application/vnd.github+json"}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        try:
            return json.load(response)
        except (ValueError, RecursionError):
            raise GateError("BLOCKED: invalid_api_response") from None


def inventory(data, key, limit):
    record(data, {key, "total_count"})
    items, total = data[key], data["total_count"]
    if not isinstance(items, list) or type(total) is not int or not 0 <= len(items) <= total:
        raise GateError("BLOCKED: invalid_inventory")
    if total > limit:
        raise GateError("BLOCKED: inventory_limit")
    required = (
        {"name", "head_branch"}
        if key == "workflow_runs"
        else {
            "id",
            "name",
            "status",
            "conclusion",
            "runner_id",
        }
    )
    for item in items:
        record(item, required)
        if not isinstance(item["name"], str) or (
            key == "workflow_runs" and not isinstance(item["head_branch"], str)
        ):
            raise GateError("BLOCKED: invalid_api_response")
    return items, total


def record(data, keys):
    if not isinstance(data, dict) or not keys <= data.keys():
        raise GateError("BLOCKED: invalid_api_response")
    return data


def wait_for_ci(expected_head, *, request=read, clock=time.monotonic, sleep=time.sleep):
    if not re.fullmatch(r"[0-9a-f]{40}", expected_head):
        raise GateError("BLOCKED: invalid_head")
    deadline = clock() + 120
    selected_run = None
    parent_completed = False
    seen_jobs = {}

    def fetch(path):
        remaining = deadline - clock()
        if remaining <= 0:
            raise GateError("BLOCKED: evidence_timeout")
        try:
            return request(path, timeout=min(15, remaining))
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
            raise  # Classified by the bounded retry loop below.
        except (OSError, http.client.HTTPException):
            raise GateError("BLOCKED: api_transport_error") from None
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise GateError("BLOCKED: invalid_api_response") from None

    while clock() < deadline:
        try:
            runs, run_count = inventory(
                fetch("/actions/runs?event=pull_request&per_page=100&head_sha=" + expected_head),
                "workflow_runs",
                100,
            )
            ci = [run for run in runs if run["name"] == "CI" and run["head_branch"] == BRANCH]
            if len(ci) > 1:
                raise GateError("VIOLATION: duplicate_ci_runs")
            if ci:
                run = record(
                    ci[0],
                    {
                        "id",
                        "head_sha",
                        "event",
                        "run_attempt",
                        "repository",
                        "path",
                        "status",
                        "conclusion",
                    },
                )
                run_id = run["id"]
                if (
                    type(run_id) is not int
                    or run_id <= 0
                    or run["head_sha"] != expected_head
                    or run["event"] != "pull_request"
                    or type(run["run_attempt"]) is not int
                    or run["run_attempt"] != 1
                    or record(run["repository"], {"full_name"})["full_name"] != REPOSITORY
                    or run["path"] != ".github/workflows/ci.yml"
                ):
                    raise GateError("VIOLATION: run_identity")
                if selected_run is not None and selected_run != run_id:
                    raise GateError("VIOLATION: changed_ci_run")
                selected_run = run_id
                if run["status"] == "completed":
                    if run["conclusion"] != "skipped":
                        raise GateError("VIOLATION: parent_not_skipped")
                    parent_completed = True
                elif (
                    run["status"] in ("queued", "in_progress", "waiting", "pending", "requested")
                    and run["conclusion"] is None
                    and not parent_completed
                ):
                    pass  # Valid nonterminal parent: jobs still need inspection for violations.
                else:
                    raise GateError("VIOLATION: inconsistent_parent_state")
                jobs, total = inventory(
                    fetch(f"/actions/runs/{run_id}/jobs?filter=all&per_page=100"),
                    "jobs",
                    100,
                )
                names = [job["name"] for job in jobs]
                if total > 4 or len(names) != len(set(names)) or not set(names) <= JOBS:
                    raise GateError("VIOLATION: unexpected_jobs")
                ids = [job["id"] for job in jobs]
                if any(type(i) is not int or i <= 0 for i in ids) or len(ids) != len(set(ids)):
                    raise GateError("BLOCKED: invalid_job_ids")
                complete = True
                for job in jobs:
                    previous = seen_jobs.get(job["name"])
                    skipped = job["status"] == "completed" and job["conclusion"] == "skipped"
                    if previous is not None and (
                        previous[0] != job["id"] or (previous[1] and not skipped)
                    ):
                        raise GateError("VIOLATION: inconsistent_job_state")
                    if job["runner_id"] is not None or job["status"] == "in_progress":
                        raise GateError("VIOLATION: ordinary_ci_running")
                    seen_jobs[job["name"]] = (job["id"], skipped)
                    if skipped:
                        continue
                    if (
                        job["status"] in ("queued", "waiting", "pending", "requested")
                        and job["conclusion"] is None
                    ):
                        complete = False
                    else:
                        raise GateError("VIOLATION: ordinary_ci_not_skipped")
                if (
                    parent_completed
                    and complete
                    and set(names) == JOBS
                    and total == 4
                    and len(runs) == run_count
                ):
                    if clock() >= deadline:
                        raise GateError("BLOCKED: evidence_timeout")
                    return {
                        "ci_run": f"https://github.com/kimonus/knowledge-vault-mcp/actions/runs/{run_id}",
                        "ci_jobs": [
                            {k: job[k] for k in ("id", "name", "conclusion", "runner_id")}
                            for job in jobs
                        ],
                    }
        except urllib.error.HTTPError as exc:
            if exc.code != 429 and not 500 <= exc.code <= 599:
                raise GateError("BLOCKED: api_http_error") from None
        except (urllib.error.URLError, TimeoutError):
            pass  # Transient unavailability; never permission to proceed.
        sleep(min(5, max(0, deadline - clock())))
    raise GateError("BLOCKED: evidence_timeout")


if __name__ == "__main__":
    try:
        print(json.dumps(wait_for_ci(os.environ.get("EXPECTED_HEAD", ""))))
    except GateError as exc:
        raise SystemExit(str(exc)) from None

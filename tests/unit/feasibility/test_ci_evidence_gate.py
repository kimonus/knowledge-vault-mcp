"""Deterministic CI inventory snapshots; no network, real waits, runner or image."""

import http.client
import io
import json
import runpy
import ssl
import urllib.error
from copy import deepcopy
from unittest.mock import Mock

import pytest
from scripts.ci import task_0014_ci_gate as gate

HEAD = "a" * 40
RUN = {
    "id": 17,
    "name": "CI",
    "head_branch": gate.BRANCH,
    "head_sha": HEAD,
    "event": "pull_request",
    "run_attempt": 1,
    "repository": {"full_name": "kimonus/knowledge-vault-mcp"},
    "path": ".github/workflows/ci.yml",
    "status": "completed",
    "conclusion": "skipped",
}
JOBS = [
    {"id": i, "name": name, "status": "completed", "conclusion": "skipped", "runner_id": None}
    for i, name in enumerate(sorted(gate.JOBS), 1)
]


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    monkeypatch.setattr(gate.urllib.request, "urlopen", Mock(side_effect=AssertionError("network")))


class Clock:
    def __init__(self):
        self.now = 0
        self.waits = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        assert 0 <= seconds <= 5
        self.waits.append(seconds)
        self.now += seconds


def runs(*items, total=None):
    return {"workflow_runs": list(items), "total_count": len(items) if total is None else total}


def jobs(items=JOBS, *, total=None):
    return {"jobs": deepcopy(items), "total_count": len(items) if total is None else total}


def poll(responses, clock=None):
    clock = clock or Clock()
    request = Mock(side_effect=responses)
    result = gate.wait_for_ci(HEAD, request=request, clock=clock, sleep=clock.sleep)
    return result, request, clock


def test_partial_inventory_and_delayed_completion():
    queued = deepcopy(JOBS)
    # Keep the previously observed skipped job stable; another job is still queued.
    queued[-1].update(status="queued", conclusion=None)
    result, request, clock = poll(
        [
            runs(),
            runs(RUN),
            jobs([]),
            runs(RUN),
            jobs(JOBS[:1], total=4),
            runs(RUN),
            jobs(queued),
            runs(RUN),
            jobs(),
        ]
    )
    assert clock.waits == [5] * 4
    assert result["ci_run"].endswith("/17")
    assert {j["name"] for j in result["ci_jobs"]} == gate.JOBS
    assert all(j["conclusion"] == "skipped" and j["runner_id"] is None for j in result["ci_jobs"])
    assert request.call_count == 9
    assert "head_sha=" + HEAD in request.call_args_list[0].args[0]
    assert "filter=all&per_page=100" in request.call_args.args[0]
    assert all(call.kwargs["timeout"] == 15 for call in request.call_args_list)


def test_all_skipped_succeeds_without_wait_or_arbitrary_api_output():
    result, _, clock = poll([runs({**RUN, "html_url": "untrusted"}), jobs()])
    assert not clock.waits
    assert "untrusted" not in json.dumps(result)


@pytest.mark.parametrize("count", range(4))
def test_every_partial_size_waits_for_full_snapshot(count):
    _, _, clock = poll([runs(RUN), jobs(JOBS[:count]), runs(RUN), jobs()])
    assert clock.waits == [5]


def test_partial_snapshots_are_not_merged_into_success():
    _, _, clock = poll(
        [
            runs(RUN),
            jobs(JOBS[:2]),
            runs(RUN),
            jobs(JOBS[2:]),
            runs(RUN),
            jobs(),
        ]
    )
    assert clock.waits == [5, 5]


@pytest.mark.parametrize("status", ["queued", "waiting", "pending", "requested"])
def test_safe_pending_status_waits(status):
    pending = deepcopy(JOBS)
    pending[0].update(status=status, conclusion=None)
    _, _, clock = poll([runs(RUN), jobs(pending), runs(RUN), jobs()])
    assert clock.waits == [5]


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"name": "unexpected"}, "unexpected_jobs"),
        ({"status": "in_progress", "conclusion": None}, "ordinary_ci_running"),
        ({"status": "queued", "conclusion": None, "runner_id": 99}, "ordinary_ci_running"),
        ({"runner_id": 0}, "ordinary_ci_running"),
        *[
            ({"conclusion": c}, "ordinary_ci_not_skipped")
            for c in ("success", "failure", "cancelled", "neutral", "timed_out", None)
        ],
        ({"status": "queued"}, "ordinary_ci_not_skipped"),
        ({"status": "unknown"}, "ordinary_ci_not_skipped"),
    ],
)
def test_violation_in_partial_inventory_fails_immediately(change, reason):
    request = Mock(side_effect=[runs(RUN), jobs([{**JOBS[0], **change}]), runs(RUN), jobs()])
    clock = Clock()
    with pytest.raises(gate.GateError, match="VIOLATION: " + reason):
        gate.wait_for_ci(HEAD, request=request, clock=clock, sleep=clock.sleep)
    assert request.call_count == 2
    assert not clock.waits  # Later successful snapshots cannot erase a violation.


@pytest.mark.parametrize("snapshot", [jobs([*JOBS, JOBS[0]]), jobs([JOBS[0], JOBS[0]])])
def test_duplicate_jobs_rejected(snapshot):
    with pytest.raises(gate.GateError, match="unexpected_jobs"):
        poll([runs(RUN), snapshot])


def test_duplicate_job_ids_rejected():
    bad = deepcopy(JOBS)
    bad[1]["id"] = bad[0]["id"]
    with pytest.raises(gate.GateError, match="invalid_job_ids"):
        poll([runs(RUN), jobs(bad)])


def test_duplicate_runs_rejected_before_jobs_request():
    with pytest.raises(gate.GateError, match="duplicate_ci_runs"):
        poll([runs(RUN, {**RUN, "id": 18})])


def test_replaced_run_cannot_erase_previous_identity():
    with pytest.raises(gate.GateError, match="changed_ci_run"):
        poll([runs(RUN), jobs([]), runs({**RUN, "id": 18})])


@pytest.mark.parametrize("change", [{"head_sha": "b" * 40}, {"event": "push"}, {"run_attempt": 2}])
def test_wrong_run_identity_rejected(change):
    with pytest.raises(gate.GateError, match="run_identity"):
        poll([runs({**RUN, **change})])


@pytest.mark.parametrize(
    "error",
    [
        urllib.error.HTTPError("fixed", code, "private error text", {}, None)
        for code in (429, 500, 503)
    ]
    + [urllib.error.URLError("private error text"), TimeoutError("private error text")],
)
@pytest.mark.parametrize("during_jobs", [False, True])
def test_transient_api_errors_retry_only_inside_deadline(error, during_jobs):
    prefix = [runs(RUN)] if during_jobs else []
    _, _, clock = poll([*prefix, error, runs(RUN), jobs()])
    assert clock.waits == [5]


@pytest.mark.parametrize("code", [401, 403, 404])
def test_permanent_api_errors_are_sanitized_and_fail_closed(code):
    error = urllib.error.HTTPError("private-url", code, "private error text", {}, None)
    with pytest.raises(gate.GateError, match=r"^BLOCKED: api_http_error$"):
        poll([error])


@pytest.mark.parametrize(
    "response",
    [
        {},
        {"workflow_runs": None, "total_count": 1},
        runs(total=-1),
        runs(RUN, total=0),
        runs(total=True),
        runs(total=101),
    ],
)
def test_invalid_or_oversize_inventory_cannot_authorize(response):
    with pytest.raises(gate.GateError, match="BLOCKED:"):
        poll([response])


def test_malformed_json_fails_closed():
    with pytest.raises(gate.GateError, match="invalid_api_response"):
        poll([json.JSONDecodeError("private response", "private", 0)])


@pytest.mark.parametrize("field", ["runner_id", "status", "conclusion"])
def test_missing_job_fields_are_not_assumed_safe(field):
    bad = deepcopy(JOBS)
    del bad[0][field]
    with pytest.raises(gate.GateError, match="invalid_api_response"):
        poll([runs(RUN), jobs(bad)])


@pytest.mark.parametrize("head", ["", "not-a-sha", "a" * 41])
def test_invalid_head_makes_no_api_request(head):
    request = Mock(side_effect=AssertionError("request should not be made"))
    with pytest.raises(gate.GateError, match="invalid_head"):
        gate.wait_for_ci(head, request=request)
    request.assert_not_called()


@pytest.mark.parametrize("phase", ["missing_run", "partial_jobs", "api_error", "partial_runs"])
def test_timeout_is_bounded_without_real_sleep(phase):
    clock = Clock()
    if phase == "api_error":
        request = Mock(side_effect=urllib.error.URLError("private error text"))
    else:

        def respond(path, *, timeout):
            assert 0 < timeout <= 15
            if "/jobs?" in path:
                return jobs(JOBS[:1]) if phase == "partial_jobs" else jobs()
            return (
                runs()
                if phase == "missing_run"
                else runs(RUN, total=2 if phase == "partial_runs" else 1)
            )

        request = Mock(side_effect=respond)
    with pytest.raises(gate.GateError, match=r"^BLOCKED: evidence_timeout$"):
        gate.wait_for_ci(HEAD, request=request, clock=clock, sleep=clock.sleep)
    assert clock.now == 120 and clock.waits == [5] * 24
    assert request.call_count <= 48


def test_late_success_is_rejected_and_request_timeout_uses_remaining_budget():
    clock = Clock()

    def respond(path, *, timeout):
        if "/jobs?" not in path:
            clock.now = 119
            return runs(RUN)
        assert timeout == 1
        clock.now = 120
        return jobs()

    with pytest.raises(gate.GateError, match="evidence_timeout"):
        gate.wait_for_ci(HEAD, request=respond, clock=clock, sleep=clock.sleep)


def test_deadline_between_requests_prevents_jobs_request():
    clock = Clock()

    def respond(path, *, timeout):
        clock.now = 120
        return runs(RUN)

    request = Mock(side_effect=respond)
    with pytest.raises(gate.GateError, match="evidence_timeout"):
        gate.wait_for_ci(HEAD, request=request, clock=clock, sleep=clock.sleep)
    assert request.call_count == 1


def test_cli_success_uses_mocked_transport_only(monkeypatch, capsys):
    monkeypatch.setenv("EXPECTED_HEAD", HEAD)
    transport = Mock(
        side_effect=[
            io.BytesIO(json.dumps(runs(RUN)).encode()),
            io.BytesIO(json.dumps(jobs()).encode()),
        ]
    )
    monkeypatch.setattr(gate.urllib.request, "urlopen", transport)
    runpy.run_path(gate.__file__, run_name="__main__")
    assert len(json.loads(capsys.readouterr().out)["ci_jobs"]) == 4
    assert transport.call_count == 2
    assert all(c.args[0].full_url.startswith(gate.base) for c in transport.call_args_list)


def test_cli_failure_has_no_api_body_or_traceback(monkeypatch, capsys):
    monkeypatch.setenv("EXPECTED_HEAD", HEAD)
    monkeypatch.setattr(
        gate.urllib.request,
        "urlopen",
        Mock(
            side_effect=urllib.error.HTTPError(
                "private-url",
                403,
                "private error text",
                {},
                None,
            )
        ),
    )
    with pytest.raises(SystemExit, match=r"^BLOCKED: api_http_error$"):
        runpy.run_path(gate.__file__, run_name="__main__")
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("status", ["queued", "in_progress", "waiting", "pending", "requested"])
def test_parent_pending_with_all_jobs_skipped_waits_for_parent_transition(status):
    parent = {**RUN, "status": status, "conclusion": None}
    _, request, clock = poll([runs(parent), jobs(), runs(RUN), jobs()])
    assert clock.waits == [5] and request.call_count == 4


def test_queued_parent_partial_jobs_then_running_parent_then_skipped():
    _, _, clock = poll(
        [
            runs({**RUN, "status": "queued", "conclusion": None}),
            jobs(JOBS[:2]),
            runs({**RUN, "status": "in_progress", "conclusion": None}),
            jobs(),
            runs(RUN),
            jobs(),
        ]
    )
    assert clock.waits == [5, 5]


@pytest.mark.parametrize("conclusion", ["failure", "cancelled", "success", "neutral", "timed_out"])
def test_parent_terminal_failure_cannot_be_repaired_by_later_success(conclusion):
    clock = Clock()
    request = Mock(
        side_effect=[
            runs({**RUN, "status": "in_progress", "conclusion": None}),
            jobs(),
            runs({**RUN, "conclusion": conclusion}),
            jobs(),
            runs(RUN),
            jobs(),
        ]
    )
    with pytest.raises(gate.GateError, match="parent_not_skipped"):
        gate.wait_for_ci(HEAD, request=request, clock=clock, sleep=clock.sleep)
    assert request.call_count == 3 and clock.waits == [5]


def test_completed_skipped_parent_with_pending_jobs_waits_for_job_transition():
    pending = deepcopy(JOBS)
    pending[0].update(status="queued", conclusion=None)
    _, _, clock = poll([runs(RUN), jobs(pending), runs(RUN), jobs()])
    assert clock.waits == [5]


@pytest.mark.parametrize(
    "change",
    [
        {"status": None},
        {"status": []},
        {"status": {}},
        {"status": 1},
        {"status": "unknown"},
        {"conclusion": None},
        {"conclusion": []},
        {"status": "in_progress", "conclusion": "skipped"},
        {"status": "queued", "conclusion": "failure"},
    ],
)
def test_malformed_or_contradictory_parent_state_fails_before_jobs(change):
    request = Mock(side_effect=[runs({**RUN, **change}), jobs(), runs(RUN), jobs()])
    clock = Clock()
    with pytest.raises(gate.GateError, match=r"(?:VIOLATION|BLOCKED):"):
        gate.wait_for_ci(HEAD, request=request, clock=clock, sleep=clock.sleep)
    assert request.call_count == 1 and not clock.waits


@pytest.mark.parametrize("field", ["status", "conclusion", "repository", "path"])
def test_missing_parent_fields_fail_closed(field):
    parent = deepcopy(RUN)
    del parent[field]
    with pytest.raises(gate.GateError, match="invalid_api_response"):
        poll([runs(parent), jobs()])


@pytest.mark.parametrize(
    "change",
    [
        {"repository": {"full_name": "untrusted/repository"}},
        {"repository": None},
        {"repository": {}},
        {"path": ".github/workflows/untrusted.yml"},
        {"run_attempt": True},
    ],
)
def test_parent_repository_workflow_and_attempt_are_explicitly_verified(change):
    with pytest.raises(gate.GateError, match=r"(?:VIOLATION|BLOCKED):"):
        poll([runs({**RUN, **change}), jobs()])


def test_completed_parent_cannot_regress_to_running_while_inventory_is_pending():
    with pytest.raises(gate.GateError, match="inconsistent_parent_state"):
        poll(
            [
                runs(RUN),
                jobs(JOBS[:2]),
                runs({**RUN, "status": "in_progress", "conclusion": None}),
                jobs(),
            ]
        )


@pytest.mark.parametrize(
    "change",
    [
        {"name": "unexpected"},
        {"runner_id": 1},
        {"status": "in_progress", "conclusion": None},
        {"conclusion": "failure"},
    ],
)
def test_completed_parent_followed_by_unsafe_job_snapshot_is_terminal(change):
    request = Mock(
        side_effect=[
            runs(RUN),
            jobs(JOBS[:2]),
            runs(RUN),
            jobs([{**JOBS[0], **change}]),
            runs(RUN),
            jobs(),
        ]
    )
    clock = Clock()
    with pytest.raises(gate.GateError, match="VIOLATION:"):
        gate.wait_for_ci(HEAD, request=request, clock=clock, sleep=clock.sleep)
    assert request.call_count == 4 and clock.waits == [5]


def test_running_parent_does_not_hide_unsafe_job():
    with pytest.raises(gate.GateError, match="ordinary_ci_running"):
        poll(
            [
                runs({**RUN, "status": "in_progress", "conclusion": None}),
                jobs([{**JOBS[0], "runner_id": 99}]),
            ]
        )


@pytest.mark.parametrize("status", ["queued", "in_progress"])
def test_parent_never_completes_times_out_despite_four_skipped_jobs(status):
    clock = Clock()
    request = Mock(
        side_effect=lambda path, **kwargs: (
            jobs() if "/jobs?" in path else runs({**RUN, "status": status, "conclusion": None})
        )
    )
    with pytest.raises(gate.GateError, match="evidence_timeout"):
        gate.wait_for_ci(HEAD, request=request, clock=clock, sleep=clock.sleep)
    assert clock.now == 120 and request.call_count == 48


@pytest.mark.parametrize(
    "error",
    [
        OSError("private-url token response-body"),
        ConnectionResetError("private-url token response-body"),
        BrokenPipeError("private-url token response-body"),
        ssl.SSLError("private-url token response-body"),
        http.client.HTTPException("private-url token response-body"),
        http.client.BadStatusLine("private-url token response-body"),
        http.client.IncompleteRead(b"private-response-body", 7),
        http.client.RemoteDisconnected("private-url token response-body"),
    ],
)
def test_cli_low_level_transport_failures_are_finite_and_sanitized(error, monkeypatch, capsys):
    monkeypatch.setenv("EXPECTED_HEAD", HEAD)
    transport = Mock(side_effect=error)
    monkeypatch.setattr(gate.urllib.request, "urlopen", transport)
    with pytest.raises(SystemExit, match=r"^BLOCKED: api_transport_error$"):
        runpy.run_path(gate.__file__, run_name="__main__")
    assert capsys.readouterr() == ("", "")
    assert transport.call_count == 1


@pytest.mark.parametrize(
    "error",
    [
        AssertionError("programming-error"),
        RuntimeError("programming-error"),
        TypeError("programming-error"),
        ValueError("programming-error"),
    ],
)
def test_request_programming_errors_are_not_swallowed(error):
    with pytest.raises(type(error), match="programming-error"):
        poll([error])


@pytest.mark.parametrize(
    "error",
    [
        UnicodeDecodeError("ascii", b"\xff", 0, 1, "private body"),
        json.JSONDecodeError("private body", "private", 0),
    ],
)
def test_cli_malformed_response_decoding_is_sanitized(error, monkeypatch, capsys):
    monkeypatch.setenv("EXPECTED_HEAD", HEAD)
    monkeypatch.setattr(gate.urllib.request, "urlopen", Mock(side_effect=error))
    with pytest.raises(SystemExit, match=r"^BLOCKED: invalid_api_response$"):
        runpy.run_path(gate.__file__, run_name="__main__")
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize(
    "response",
    [
        runs({**RUN, "name": []}),
        runs({**RUN, "head_branch": []}),
        {"workflow_runs": [None], "total_count": 1},
    ],
)
def test_malformed_inventory_rows_are_sanitized(response):
    with pytest.raises(gate.GateError, match="invalid_api_response"):
        poll([response])


def test_malformed_job_status_does_not_escape_as_programming_error():
    with pytest.raises(gate.GateError, match="ordinary_ci_not_skipped"):
        poll([runs(RUN), jobs([{**JOBS[0], "status": []}])])


@pytest.mark.parametrize(
    "change",
    [
        {"status": "queued", "conclusion": None},
        {"id": 999},
    ],
)
def test_seen_completed_job_regression_or_replacement_is_terminal(change):
    running = {**RUN, "status": "in_progress", "conclusion": None}
    request = Mock(
        side_effect=[
            runs(running),
            jobs(),
            runs(RUN),
            jobs([{**JOBS[0], **change}]),
            runs(RUN),
            jobs(),
        ]
    )
    clock = Clock()
    with pytest.raises(gate.GateError, match="inconsistent_job_state"):
        gate.wait_for_ci(HEAD, request=request, clock=clock, sleep=clock.sleep)
    assert request.call_count == 4 and clock.waits == [5]


@pytest.mark.parametrize(
    "body",
    [
        b"not-json private-token https://untrusted.invalid/body",
        b"\xff",
        b"9" * 5000,
        b"[" * 1200 + b"]" * 1200,
    ],
)
def test_cli_actual_json_decoder_failures_are_sanitized(body, monkeypatch, capsys):
    monkeypatch.setenv("EXPECTED_HEAD", HEAD)
    monkeypatch.setattr(gate.urllib.request, "urlopen", Mock(return_value=io.BytesIO(body)))
    with pytest.raises(SystemExit, match=r"^BLOCKED: invalid_api_response$"):
        runpy.run_path(gate.__file__, run_name="__main__")
    assert capsys.readouterr() == ("", "")

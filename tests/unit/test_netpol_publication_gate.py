import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/ci/netpol_publication_gate.py"
spec = importlib.util.spec_from_file_location("netpol_publication_gate", SCRIPT)
assert spec and spec.loader
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


def valid_environment() -> dict[str, str]:
    return {
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REF": "refs/heads/ops/dashboard-netpol-rehearsal",
        "GITHUB_REPOSITORY_OWNER": "owner",
        "GITHUB_ACTOR": "owner",
        "GITHUB_TRIGGERING_ACTOR": "owner",
        "GITHUB_SHA": "a" * 40,
        "EXPECTED_POLICY_SHA": "a" * 40,
    }


def test_owner_must_name_the_exact_reviewed_source() -> None:
    gate.validate(valid_environment())


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("GITHUB_EVENT_NAME", "pull_request"),
        ("GITHUB_REF", "refs/heads/main"),
        ("GITHUB_ACTOR", "another-collaborator"),
        ("GITHUB_TRIGGERING_ACTOR", "rerun-by-another-collaborator"),
        ("GITHUB_TRIGGERING_ACTOR", ""),
        ("GITHUB_REPOSITORY_OWNER", ""),
        ("GITHUB_SHA", "b" * 40),
        ("EXPECTED_POLICY_SHA", ""),
        ("EXPECTED_POLICY_SHA", "$(touch /tmp/untrusted-input)"),
        ("EXPECTED_POLICY_SHA", "a" * 41),
    ],
)
def test_wrong_actor_ref_source_or_untrusted_input_fails_closed(key: str, value: str) -> None:
    environment = valid_environment()
    environment[key] = value
    with pytest.raises(ValueError):
        gate.validate(environment)

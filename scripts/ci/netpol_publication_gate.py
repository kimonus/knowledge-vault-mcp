"""Bind optional infrastructure publication to the owner's exact reviewed source."""

import os
import re


def validate(environment: dict[str, str]) -> None:
    required = {
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REF": "refs/heads/ops/dashboard-netpol-rehearsal",
    }
    if any(environment.get(key) != value for key, value in required.items()):
        raise ValueError("Publication requires the dedicated manual operational workflow")
    owner = environment.get("GITHUB_REPOSITORY_OWNER")
    if (
        not owner
        or environment.get("GITHUB_ACTOR") != owner
        or environment.get("GITHUB_TRIGGERING_ACTOR") != owner
    ):
        raise ValueError("Only the repository owner may publish the operational artifact")
    expected = environment.get("EXPECTED_POLICY_SHA", "")
    if not re.fullmatch("[a-f0-9]{40}", expected) or environment.get("GITHUB_SHA") != expected:
        raise ValueError("Publication source must match the explicitly reviewed immutable commit")


if __name__ == "__main__":
    validate(dict(os.environ))
    print("Owner, dedicated dispatch and exact source binding PASS")

"""The deployment rehearsal must refuse unrelated contexts before any mutation."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/ci/test_dashboard_lifecycle.py"
spec = importlib.util.spec_from_file_location("dashboard_lifecycle", SCRIPT)
assert spec is not None and spec.loader is not None
lifecycle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lifecycle)

DIGEST = "knowledge-vault-dashboard@sha256:" + "a" * 64


def test_dashboard_inserted_before_core_does_not_change_core_comparison() -> None:
    core = "# Source: knowledge-vault/templates/serviceaccounts.yaml\nkind: ServiceAccount\n"
    dashboard = "# Source: knowledge-vault/templates/dashboard-service.yaml\nkind: Service\n"
    assert lifecycle.rendered_core("---\n" + core) == lifecycle.rendered_core(
        "---\n" + dashboard + "---\n" + core
    )


def test_core_comparison_preserves_changes_and_dashboard_text_in_config() -> None:
    core = (
        "# Source: knowledge-vault/templates/configmap.yaml\nkind: ConfigMap\n"
        "data:\n  example: /templates/dashboard\n  revision: one\n"
    )
    assert lifecycle.rendered_core("---\n" + core)
    assert lifecycle.rendered_core("---\n" + core) != lifecycle.rendered_core(
        "---\n" + core.replace("revision: one", "revision: two")
    )


@pytest.mark.parametrize("context", ["minikube", "production", "", "kv-dashboard-pr4-other"])
def test_unapproved_context_never_invokes_kubernetes(
    monkeypatch: pytest.MonkeyPatch, context: str
) -> None:
    monkeypatch.setattr(
        sys, "argv", ["lifecycle", "--context", context, "--dashboard-image", DIGEST]
    )

    def refuse(*args: object, **kwargs: object) -> str:
        pytest.fail("a rejected context reached an external command")

    monkeypatch.setattr(lifecycle, "run", refuse)
    with pytest.raises(SystemExit) as exc:
        lifecycle.main()
    assert exc.value.code == 2


def test_similarly_named_context_cannot_target_another_cluster(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sys, "argv", ["lifecycle", "--context", lifecycle.NAME, "--dashboard-image", DIGEST]
    )
    calls: list[list[str]] = []

    def context_only(args: list[str], **kwargs: object) -> str:
        calls.append(args)
        assert args == ["kubectl", "config", "view", "-o", "json"]
        return json.dumps(
            {"contexts": [{"name": lifecycle.NAME, "context": {"cluster": "production"}}]}
        )

    monkeypatch.setattr(lifecycle, "run", context_only)
    with pytest.raises(SystemExit) as exc:
        lifecycle.main()
    assert exc.value.code == 2
    assert len(calls) == 1


def test_runtime_failures_never_echo_captured_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failed(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            ["kubectl"], 1, "synthetic-sensitive-body", "synthetic-sensitive-token"
        )

    monkeypatch.setattr(lifecycle.subprocess, "run", failed)
    with pytest.raises(RuntimeError, match="kubectl failed with exit code 1") as exc:
        lifecycle.run(["kubectl", "apply", "-f", "-"], "synthetic-sensitive-input")
    assert "synthetic-sensitive" not in str(exc.value)


@pytest.mark.parametrize(
    "logged", ["fixture-only-secret", "Disposable dashboard lifecycle synthetic assertion."]
)
def test_runtime_log_leak_fails_without_printing_payload(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], logged: str
) -> None:
    def captured(args: list[str], **kwargs: object) -> str:
        if "logs" in args:
            return logged
        return json.dumps(
            {
                "items": [
                    {
                        "metadata": {
                            "name": "lab-dashboard",
                            "labels": {"app.kubernetes.io/component": "dashboard"},
                        },
                        "status": {
                            "containerStatuses": [
                                {"name": "dashboard", "ready": True, "restartCount": 0}
                            ]
                        },
                    }
                ]
            }
        )

    monkeypatch.setattr(lifecycle, "run", captured)
    with pytest.raises(AssertionError, match="sensitive data in runtime logs"):
        lifecycle.verify_logs(["kubectl"], ["fixture-only-secret"])
    assert logged not in capsys.readouterr().out

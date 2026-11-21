"""Run the deployment probe's actual HTTP program against real core/BFF services."""

import asyncio
import importlib.util
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from knowledge_vault.api.app import create_app as core_app

pytestmark = [pytest.mark.contract, pytest.mark.integration]
REPO = Path(__file__).resolve().parents[2]


async def test_deployment_auth_probe_matches_real_application_contract(
    integration_container, reader_token, writer_token, monkeypatch, serve
):
    monkeypatch.syspath_prepend(str(REPO / "dashboard/src"))
    from knowledge_vault_dashboard.app import create_app
    from knowledge_vault_dashboard.auth.tokens import Scope, TokenRecord, hash_token
    from knowledge_vault_dashboard.config import Settings

    integration_container.settings.inspection_principals = "reader"
    core = core_app(integration_container)
    browser = "dashboard-lifecycle-synthetic-browser-credential"
    pepper = "dashboard-lifecycle-synthetic-pepper-000000"
    bff = create_app(
        Settings(
            enabled=True,
            host="dashboard.example.test",
            core_host="core.example.test",
            core_url="http://core.default.svc.cluster.local:8000",
            service_token=reader_token,
            pepper=pepper,
            signing_key="dashboard-lifecycle-synthetic-signing-111111",
            records=(TokenRecord("browser", hash_token(browser, pepper), frozenset({Scope.READ})),),
        ),
        transport=httpx.ASGITransport(core),
    )
    core_url, bff_url = await serve(core), await serve(bff)
    spec = importlib.util.spec_from_file_location(
        "dashboard_lifecycle_contract", REPO / "scripts/ci/test_dashboard_lifecycle.py"
    )
    assert spec is not None and spec.loader is not None
    lifecycle = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lifecycle)

    def execute_probe(args, data=None, timeout=360):
        # Replace only ClusterIP addresses. Execute the unchanged request/assertion program
        # in another process while the real HTTP servers and pgvector remain running.
        program = args[-1].replace("http://kv-dashboard-pr4:8000", core_url)
        program = program.replace("http://kv-dashboard-pr4-dashboard:8080", bff_url)
        result = subprocess.run(  # noqa: S603  # repository-owned probe, isolated fixture only
            [sys.executable, "-c", program],
            input=data,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        return result.stdout

    monkeypatch.setattr(lifecycle, "run", execute_probe)
    await asyncio.to_thread(lifecycle.auth_probe, ["kubectl"], browser, reader_token, writer_token)

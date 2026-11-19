"""Real PR1/core + PR2/BFF compatibility, using disposable pgvector and separate credentials."""

import hashlib
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func, select

from knowledge_vault.api.app import create_app as core_app
from knowledge_vault.persistence.tables import AssertionUsageRow, MaintenanceEventRow

pytestmark = [pytest.mark.contract, pytest.mark.integration]
DASHBOARD_SOURCE = str(Path(__file__).resolve().parents[2] / "dashboard/src")


async def test_dashboard_real_core_inspection_and_credential_separation(
    integration_container, reader_token, writer_token, monkeypatch, caplog
):
    # Test-only source path. The core distribution/build never depends on dashboard installation.
    monkeypatch.syspath_prepend(DASHBOARD_SOURCE)
    from knowledge_vault_dashboard.app import PREFIX, create_app
    from knowledge_vault_dashboard.auth.tokens import Scope, TokenRecord, hash_token
    from knowledge_vault_dashboard.config import Settings

    integration_container.settings.inspection_principals = "reader"
    upstream = core_app(integration_container)
    transport = httpx.ASGITransport(upstream)
    browser_credential = "dashboard-browser-only-synthetic-credential"
    pepper = "dashboard-only-synthetic-pepper-00000000"
    dashboard_settings = Settings(
        enabled=True,
        host="dashboard.test",
        core_host="vault.test",
        core_url="http://core.default.svc.cluster.local:8000",
        service_token=reader_token,
        pepper=pepper,
        signing_key="dashboard-session-signing-synthetic-1111111",
        records=(
            TokenRecord(
                "dashboard-browser", hash_token(browser_credential, pepper), frozenset({Scope.READ})
            ),
        ),
    )
    bff = create_app(dashboard_settings, transport=transport)
    same_origin = {"Origin": "https://dashboard.test", "X-Dashboard-Request": "1"}
    async with (
        bff.router.lifespan_context(bff),
        httpx.AsyncClient(transport=transport, base_url="https://vault.test") as core,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(bff), base_url="https://dashboard.test"
        ) as browser,
    ):
        # Existing writer functionality remains available independently of the BFF.
        auth = {"Authorization": "Bearer " + writer_token}
        begin = await core.post(
            "/api/v1/flushes",
            headers=auth,
            json={"idempotency_key": "bff-compatibility", "declared_parts": 1, "declared_items": 1},
        )
        batch = begin.json()["batch_id"]
        content = '<script>alert("synthetic")</script> dashboard inspection evidence'
        append = await core.put(
            f"/api/v1/flushes/{batch}/parts/1",
            headers=auth,
            json={
                "assertions": [
                    {
                        "content": content,
                        "kind": "user_fact",
                        "origin": "user",
                        "topics": ["dashboard"],
                        "sources": [{"url": "https://example.test/evidence"}],
                    }
                ]
            },
        )
        assert append.status_code == 200
        committed = await core.post(f"/api/v1/flushes/{batch}/commit", headers=auth)
        assertion_id = committed.json()["assertion_ids"][0]
        async with integration_container.database.sessions() as session:
            before = [
                await session.scalar(select(func.count()).select_from(table))
                for table in (AssertionUsageRow, MaintenanceEventRow)
            ]
        assert (await browser.get("/health/ready")).status_code == 204
        assert (
            await browser.post(
                PREFIX + "session/login",
                headers=same_origin,
                json={"credential": browser_credential},
            )
        ).status_code == 204
        cookie = browser.cookies.get("__Host-kv_dashboard")
        queries = {
            "assertions/query": {"limit": 1},
            "catalog/query": {"dimension": "topics"},
            "search": {"query": "dashboard inspection", "limit": 5},
            "state/evidence/query": {
                "identity": {
                    "subject": "synthetic",
                    "property": "setting",
                    "scope": "test",
                    "state_type": "fact",
                }
            },
            "state/resolve": {
                "identity": {
                    "subject": "synthetic",
                    "property": "setting",
                    "scope": "test",
                    "state_type": "fact",
                }
            },
        }
        for operation, body in queries.items():
            response = await browser.post(PREFIX + operation, json=body, headers=same_origin)
            assert response.status_code == 200, (operation, response.text)
            assert response.json()["untrusted_data"] is True
        for path in [
            f"assertions/{assertion_id}",
            "topic-aliases",
            *[
                f"assertions/{assertion_id}/{child}"
                for child in ["sources", "artifacts", "identities", "relations"]
            ],
        ]:
            response = await browser.get(PREFIX + path)
            assert response.status_code == 200, (path, response.text)
            if path == f"assertions/{assertion_id}":
                assert response.json()["content"] == content
                assert response.headers["content-type"] == "application/json"
        for path in [f"contexts/{uuid4()}", f"artifacts/{uuid4()}"]:
            assert (await browser.get(PREFIX + path)).status_code == 404
        for credential in [browser_credential, cookie]:
            for path in ["/api/v1/read/capabilities", "/api/v1/statistics", "/mcp"]:
                assert (
                    await core.get(path, headers={"Authorization": "Bearer " + credential})
                ).status_code in {401, 403}
        assert (
            await core.get(
                "/api/v1/read/capabilities", headers={"Cookie": "__Host-kv_dashboard=" + cookie}
            )
        ).status_code == 401
        for path in ["/api/v1/search", "/api/v1/flushes", "/api/v1/forget/preview", "/mcp"]:
            assert (
                await core.post(path, headers={"Authorization": "Bearer " + reader_token}, json={})
            ).status_code == 403
        # A mistakenly supplied writer cannot pass the BFF's dependency check.
        bad = create_app(
            Settings(**{**dashboard_settings.__dict__, "service_token": writer_token}),
            transport=transport,
        )
        async with (
            bad.router.lifespan_context(bad),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(bad), base_url="https://dashboard.test"
            ) as misconfigured,
        ):
            assert (await misconfigured.get("/health/ready")).status_code == 503
        async with integration_container.database.sessions() as session:
            after = [
                await session.scalar(select(func.count()).select_from(table))
                for table in (AssertionUsageRow, MaintenanceEventRow)
            ]
        assert before == after
        assert (await core.get("/health/live")).status_code == 200
        for secret in [
            browser_credential,
            reader_token,
            cookie,
            content,
            hashlib.sha256(content.encode()).hexdigest(),
        ]:
            assert secret not in caplog.text

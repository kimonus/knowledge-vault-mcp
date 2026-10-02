import pytest
from httpx import ASGITransport, AsyncClient

from knowledge_vault.api.app import create_app


@pytest.mark.e2e
@pytest.mark.integration
async def test_authenticated_http_flush_search_and_problem_details(
    integration_container, raw_token: str
) -> None:
    app = create_app(integration_container)
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    headers = {"Authorization": f"Bearer {raw_token}"}
    async with AsyncClient(transport=transport, base_url="https://vault.test") as client:
        unauthorized = await client.post("/api/v1/search", json={"query": "Warsaw"})
        assert unauthorized.status_code == 401
        assert unauthorized.headers["content-type"].startswith("application/problem+json")
        assert unauthorized.json()["type"].endswith(":unauthorized")

        begin = await client.post(
            "/api/v1/flushes",
            headers=headers,
            json={
                "idempotency_key": "http-e2e",
                "declared_parts": 1,
                "declared_items": 1,
            },
        )
        assert begin.status_code == 201
        batch_id = begin.json()["batch_id"]
        append = await client.put(
            f"/api/v1/flushes/{batch_id}/parts/1",
            headers=headers,
            json={
                "assertions": [
                    {
                        "content": "The user uses a Polish keyboard layout.",
                        "kind": "configuration",
                        "origin": "user",
                        "topics": ["keyboard"],
                    }
                ]
            },
        )
        assert append.status_code == 200
        commit = await client.post(f"/api/v1/flushes/{batch_id}/commit", headers=headers)
        assert commit.status_code == 200
        assert commit.json()["counts"]["inserted"] == 1

        search = await client.post(
            "/api/v1/search",
            headers=headers,
            json={"query": "Polish keyboard", "limit": 10},
        )
        assert search.status_code == 200
        assertion_id = search.json()["results"][0]["assertion"]["id"]
        fetched = await client.get(f"/api/v1/assertions/{assertion_id}", headers=headers)
        assert fetched.status_code == 200
        assert fetched.json()["content"] == "The user uses a Polish keyboard layout."

        conflicts = await client.get("/api/v1/conflicts?limit=5", headers=headers)
        statistics = await client.get("/api/v1/statistics", headers=headers)
        assert conflicts.json() == {"conflicts": []}
        assert statistics.json()["assertions_total"] == 1

        correction = await client.post(
            "/api/v1/corrections",
            headers=headers,
            json={
                "idempotency_key": "http-correction",
                "correction": {
                    "content": "The user now uses a US keyboard layout.",
                    "kind": "configuration",
                    "origin": "user",
                    "topics": ["keyboard"],
                    "supersedes_id": assertion_id,
                },
            },
        )
        assert correction.status_code == 200
        correction_id = correction.json()["assertion_ids"][0]
        preview = await client.post(
            "/api/v1/forget/preview",
            headers=headers,
            json={"assertion_ids": [correction_id]},
        )
        assert preview.status_code == 200
        confirmed = await client.post(
            "/api/v1/forget/confirm",
            headers=headers,
            json={"confirmation_token": preview.json()["confirmation_token"]},
        )
        assert confirmed.json()["deleted_count"] == 1
        missing = await client.get(f"/api/v1/assertions/{correction_id}", headers=headers)
        assert missing.status_code == 404

        abandoned = await client.post(
            "/api/v1/flushes",
            headers=headers,
            json={"idempotency_key": "http-abort", "declared_parts": 1, "declared_items": 1},
        )
        aborted = await client.post(
            f"/api/v1/flushes/{abandoned.json()['batch_id']}/abort", headers=headers
        )
        assert aborted.json() == {"batch_id": abandoned.json()["batch_id"], "aborted": True}

        malformed = await client.post("/api/v1/search", headers=headers, json={"query": ""})
        assert malformed.status_code == 422
        assert "query" in malformed.json()["errors"][0]["location"]

        bad_token = await client.post(
            "/api/v1/search",
            headers={"Authorization": "Bearer invalid-token"},
            json={"query": "keyboard"},
        )
        assert bad_token.status_code == 401
        oversized = await client.post(
            "/api/v1/search",
            headers={**headers, "content-length": "2000001"},
            content=b"{}",
        )
        assert oversized.status_code == 413

        live = await client.get("/health/live")
        ready = await client.get("/health/ready")
        metrics = await client.get("/metrics")
        assert live.json() == {"status": "live"}
        assert ready.status_code == 200
        assert "knowledge_vault_http_requests_total" in metrics.text

"""Verify temporal guarantees through authenticated MCP, with matching HTTP contracts."""

from uuid import uuid4

import httpx2
import pytest

from knowledge_vault.api.app import create_app
from knowledge_vault.auth.rate_limit import RateLimitGuard

pytestmark = [pytest.mark.contract, pytest.mark.integration]


async def test_temporal_mcp_import_permutations_and_correction(
    integration_container, raw_token, reader_token, serve, mcp_connect
):
    c = integration_container
    # This test intentionally runs more than one production minute's worth of write calls.
    c.rate_limits = RateLimitGuard(c.settings.model_copy(update={"rate_write_per_minute": 200}))
    base = await serve(create_app(c))
    async with mcp_connect(base, raw_token) as client:

        async def call(name, arguments=None):
            error, data = await client.call(name, arguments)
            assert not error, data
            return data

        async def write(item):
            batch = await call(
                "begin_knowledge_flush",
                {
                    "idempotency_key": str(uuid4()),
                    "declared_parts": 1,
                    "declared_items": 1,
                },
            )
            args = {"batch_id": batch["batch_id"], "part_number": 1, "assertions": [item]}
            assert (await call("append_knowledge", args))["accepted"] == 1
            args = {"batch_id": batch["batch_id"]}
            first = await call("commit_knowledge_flush", args)
            assert first == await call("commit_knowledge_flush", args)
            return first

        for scope, days in [("mcp-forward", [1, 31]), ("mcp-reverse", [31, 1])]:
            identity = {
                "subject": "router",
                "property": "wireguard.endpoint",
                "scope": scope,
                "state_type": "configuration",
            }
            for day in days:
                value = "Router X" if day == 1 else "MikroTik"
                item = {
                    "content": f"WireGuard runs on {value}.",
                    "kind": "configuration",
                    "origin": "user",
                    "evidence_state": "reported",
                    "observed_at": f"2026-01-{day:02}T00:00:00Z",
                    "provenance": [
                        {
                            "kind": "conversation",
                            "locator": f"source:{day}",
                            "conversation_id": str(day),
                            "message_ids": ["message-1"],
                        }
                    ],
                    "temporal": {
                        "identity": identity,
                        "evidence_key": f"{scope}:{day}",
                        "value": value,
                        "mode": "snapshot" if day == 1 else "change",
                    },
                }
                receipt = await write(item)
                assert receipt["items"][0]["evidence_outcome"] == "inserted"
                again = await write(item)
                assert again["items"][0]["evidence_outcome"] == "replayed"
            result = await call("resolve_knowledge_state", {"request": {"identity": identity}})
            assert result["outcome"] == "resolved" and result["values"] == ["MikroTik"]
            past = await call(
                "resolve_knowledge_state",
                {
                    "request": {
                        "identity": identity,
                        "as_of": "2026-01-01T00:00:00Z",
                    }
                },
            )
            assert past["values"] == ["Router X"]
            assert past["history"][0]["assertion"]["provenance"][0]["message_ids"] == ["message-1"]
            correction = {
                "content": "WireGuard actually runs on Router Y.",
                "kind": "configuration",
                "origin": "user",
                "evidence_state": "reported",
                "observed_at": "2026-02-01T00:00:00Z",
                "temporal": {
                    "identity": identity,
                    "evidence_key": f"{scope}:correction",
                    "value": "Router Y",
                    "mode": "correction",
                    "corrects_key": f"{scope}:31",
                },
            }
            correction_args = {"idempotency_key": str(uuid4()), "correction": correction}
            corrected = await call("correct_knowledge", correction_args)
            assert corrected == await call("correct_knowledge", correction_args)
            error, message = await client.call(
                "correct_knowledge",
                {
                    **correction_args,
                    "correction": {**correction, "confidence": 0.5},
                },
            )
            assert error and "conflict" in message
            assert corrected["counts"]["superseded"] == 0
            result = await call("resolve_knowledge_state", {"request": {"identity": identity}})
            assert result["values"] == ["Router Y"]
            assert result["history"][-1]["ordering_at"] == "2026-01-31T00:00:00Z"
            got = await call("get_knowledge", {"assertion_id": corrected["assertion_ids"][0]})
            assert identity in got["temporal_identities"]
        diagnostics = await call("get_knowledge_temporal_diagnostics")
        assert diagnostics["evidence_total"] == 6

    async with (
        mcp_connect(base, reader_token) as reader,
        httpx2.AsyncClient(
            base_url=base, headers={"Authorization": f"Bearer {reader_token}"}
        ) as http,
    ):
        error, actual = await reader.call(
            "resolve_knowledge_state", {"request": {"identity": identity}}
        )
        assert not error and actual["values"] == ["Router Y"]
        response = await http.post("/api/v1/state/resolve", json={"identity": identity})
        assert response.status_code == 200 and response.json() == actual
        assert (await http.get("/api/v1/state/diagnostics")).json() == diagnostics
        error, message = await reader.call(
            "append_knowledge",
            {
                "batch_id": str(uuid4()),
                "part_number": 1,
                "assertions": [correction],
            },
        )
        assert error and "forbidden" in message
    async with httpx2.AsyncClient(base_url=base) as anonymous:
        assert (
            await anonymous.post("/api/v1/state/resolve", json={"identity": identity})
        ).status_code == 401

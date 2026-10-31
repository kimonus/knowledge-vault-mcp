"""Exercise new contracts through authenticated HTTP and Streamable HTTP MCP."""

from uuid import uuid4

import httpx2
import pytest

from knowledge_vault.api.app import create_app

pytestmark = [pytest.mark.contract, pytest.mark.integration]


async def test_maintenance_transports_and_scope_boundaries(
    integration_container, raw_token, reader_token, serve, mcp_connect
):
    c = integration_container
    base = await serve(create_app(c))
    context_id = str(uuid4())
    async with (
        mcp_connect(base, raw_token) as mcp,
        httpx2.AsyncClient(base_url=base, headers={"Authorization": f"Bearer {raw_token}"}) as http,
    ):

        async def call(name, arguments=None):
            error, data = await mcp.call(name, arguments)
            assert not error, data
            return data

        context = {"id": context_id, "name": "Synthetic test room", "aliases": ["test room"]}
        assert (await call("create_knowledge_context", {"context": context}))["id"] == context_id
        assert (await call("get_knowledge_context", {"context_id": context_id}))["name"] == context[
            "name"
        ]
        assert (await http.post("/api/v1/contexts", json=context)).json()["id"] == context_id
        assert (await http.get(f"/api/v1/contexts/{context_id}")).json()["id"] == context_id
        batch = await call(
            "begin_knowledge_flush",
            {
                "idempotency_key": str(uuid4()),
                "declared_parts": 1,
                "declared_items": 2,
            },
        )
        await call(
            "append_knowledge",
            {
                "batch_id": batch["batch_id"],
                "part_number": 1,
                "assertions": [
                    {
                        "content": "Synthetic question about the test room.",
                        "kind": "open_question",
                        "origin": "user",
                        "context_id": context_id,
                        "workflow_state": "unresolved",
                    },
                    {
                        "content": "Synthetic observation answers the room question.",
                        "kind": "artifact_observation",
                        "origin": "user",
                        "context_id": context_id,
                    },
                ],
            },
        )
        ids = (await call("commit_knowledge_flush", {"batch_id": batch["batch_id"]}))[
            "assertion_ids"
        ]
        update = {
            "assertion_id": ids[1],
            "expected_revision": 1,
            "idempotency_key": str(uuid4()),
            "changes": {"confidence": 0.6, "evidence_state": "reported"},
            "dry_run": True,
        }
        assert (await call("update_knowledge_metadata", {"request": update}))["dry_run"]
        update["dry_run"] = False
        assert (await http.post("/api/v1/metadata", json=update)).json()["revision"] == 2
        assert (await call("update_knowledge_metadata", {"request": update}))["replayed"]
        bad_revision = {**update, "idempotency_key": str(uuid4())}
        assert (await http.post("/api/v1/metadata", json=bad_revision)).status_code == 409
        link = {"source_id": ids[0], "target_id": ids[1], "kind": "resolves"}
        assert (await call("link_knowledge", {"link": link}))["kind"] == "resolves"
        assert (await http.post("/api/v1/links", json=link)).json()["kind"] == "resolves"
        assert len((await call("get_related_knowledge", {"assertion_id": ids[0]}))["links"]) == 1
        assert len((await http.get(f"/api/v1/assertions/{ids[0]}/related")).json()["links"]) == 1
        alias = {"alias": "комната", "canonical": "room"}
        assert (await call("add_knowledge_topic_alias", {"alias": alias}))["aliases"] == {
            "комната": "room"
        }
        assert (await http.post("/api/v1/topic-aliases", json=alias)).status_code == 200
        assert (await call("list_knowledge_topic_aliases"))["aliases"] == (
            await http.get("/api/v1/topic-aliases")
        ).json()["aliases"]
        assert (await call("review_knowledge"))["items"] == (
            await http.get("/api/v1/review")
        ).json()["items"]
        assert (await call("find_knowledge_curation_candidates", {"context_id": context_id}))[
            "inspected"
        ] == 2
        assert (
            await http.get("/api/v1/curation-candidates", params={"context_id": context_id})
        ).json()["inspected"] == 2
        report = {"assertion_ids": ids, "event_id": str(uuid4())}
        assert (await call("report_knowledge_usage", {"report": report}))["recorded"] == 2
        assert (await http.post("/api/v1/usage", json=report)).json()["recorded"] == 0
        assert (
            await http.get(f"/api/v1/assertions/{ids[0]}", params={"purpose": "maintenance"})
        ).json()["usage"]["use_count"] == 1
        assert (
            await http.post("/api/v1/metadata", json={**update, "extra": "forbidden"})
        ).status_code == 422

    async with (
        mcp_connect(base, reader_token) as mcp,
        httpx2.AsyncClient(
            base_url=base, headers={"Authorization": f"Bearer {reader_token}"}
        ) as http,
    ):
        for name, arguments in (
            ("create_knowledge_context", {"context": context}),
            ("update_knowledge_metadata", {"request": update}),
            ("link_knowledge", {"link": link}),
            ("add_knowledge_topic_alias", {"alias": alias}),
            ("report_knowledge_usage", {"report": report}),
        ):
            error, result = await mcp.call(name, arguments)
            assert error and "forbidden" in result
        for path, body in (
            ("contexts", context),
            ("metadata", update),
            ("links", link),
            ("topic-aliases", alias),
            ("usage", report),
        ):
            assert (await http.post(f"/api/v1/{path}", json=body)).status_code == 403
        assert (await http.get("/api/v1/review")).status_code == 200

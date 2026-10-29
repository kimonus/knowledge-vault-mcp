"""Authenticated staging acceptance probe. Writes only explicitly supplied synthetic IDs."""

import argparse
import asyncio
import json
import socket
import ssl
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit
from uuid import uuid4

import httpx2
from anyio import Path
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client


async def probe(args: argparse.Namespace) -> None:
    host = urlsplit(args.base_url).hostname or ""
    if not host.startswith("kv-stage-"):
        raise ValueError("probe writes require an explicitly named kv-stage-* staging host")
    if args.connect_host:
        original = socket.getaddrinfo

        def resolve(name, *rest, **kwargs):
            requested = name.decode("ascii") if isinstance(name, bytes) else name
            return original(args.connect_host if requested == host else name, *rest, **kwargs)

        socket.getaddrinfo = resolve
    tls = ssl.create_default_context(cafile=args.ca_file)
    token = (await Path(args.token_file).read_text()).strip()
    async with (
        httpx2.AsyncClient(
            verify=tls, headers={"Authorization": f"Bearer {token}"}, timeout=60, trust_env=False
        ) as http,
        streamable_http_client(args.base_url + "/mcp", http_client=http) as streams,
        ClientSession(streams[0], streams[1]) as client,
    ):
        initialized = await client.initialize()
        assert initialized.server_info.version == "0.5.0"

        async def call(name, values=None):
            result = await client.call_tool(name, values or {})
            assert not result.is_error, f"staging operation failed: {name}"
            return result.structured_content

        if args.verify_only:
            state = json.loads(await Path(args.state_file).read_text())
            for ident in state["ids"]:
                view = await call(
                    "get_knowledge", {"assertion_id": ident, "purpose": "maintenance"}
                )
                assert view["context_id"] == state["context"]
            if args.cleanup:
                preview = await call(
                    "forget_knowledge", {"dry_run": True, "assertion_ids": state["ids"]}
                )
                deleted = await call(
                    "forget_knowledge",
                    {"dry_run": False, "confirmation_token": preview["confirmation_token"]},
                )
                assert deleted["deleted_count"] == len(state["ids"])
            print("staging restart/persistence and synthetic cleanup passed")
            return

        tools = {t.name for t in (await client.list_tools()).tools}
        assert {
            "update_knowledge_metadata",
            "get_related_knowledge",
            "report_knowledge_usage",
        } <= tools
        context_id = str(uuid4())
        context = await call(
            "create_knowledge_context",
            {
                "context": {
                    "id": context_id,
                    "name": "Synthetic staging procedure",
                    "aliases": ["stage-test"],
                }
            },
        )
        assert context["id"] == context_id
        batch = await call(
            "begin_knowledge_flush",
            {"idempotency_key": str(uuid4()), "declared_parts": 1, "declared_items": 5},
        )
        statements = [
            ("Synthetic method: prepare the test surface, then apply test heat.", "procedure"),
            ("For the synthetic method, prepare the test surface first.", "procedure"),
            ("For the synthetic method, apply test heat after preparation.", "procedure"),
            ("Is the synthetic staging procedure documented?", "open_question"),
            (
                "The synthetic procedure documentation was observed during staging.",
                "artifact_observation",
            ),
        ]
        values = [
            {
                "content": content,
                "kind": kind,
                "origin": "user",
                "context_id": context_id,
                "topics": ["stage-procedure"],
                **({"workflow_state": "unresolved"} if kind == "open_question" else {}),
            }
            for content, kind in statements
        ]
        accepted = await call(
            "append_knowledge",
            {"batch_id": batch["batch_id"], "part_number": 1, "assertions": values},
        )
        assert accepted["accepted"] == 5
        committed = await call("commit_knowledge_flush", {"batch_id": batch["batch_id"]})
        ids = committed["assertion_ids"]
        assert len(ids) == 5 and committed["counts"]["rejected"] == 0
        assert (await call("commit_knowledge_flush", {"batch_id": batch["batch_id"]}))[
            "assertion_ids"
        ] == ids
        for number in (1, 2):
            await call(
                "link_knowledge",
                {
                    "link": {
                        "source_id": ids[number],
                        "target_id": ids[0],
                        "kind": "part_of",
                        "position": number,
                    }
                },
            )
        await call(
            "link_knowledge",
            {"link": {"source_id": ids[3], "target_id": ids[4], "kind": "resolves"}},
        )
        related = await call("get_related_knowledge", {"assertion_id": ids[0]})
        assert [r["position"] for r in related["links"]] == [1, 2]
        view = await call("get_knowledge", {"assertion_id": ids[0], "purpose": "maintenance"})
        assert view["usage"]["retrieval_count"] == 0
        request = {
            "assertion_id": ids[0],
            "expected_revision": view["revision"],
            "idempotency_key": str(uuid4()),
            "dry_run": True,
            "changes": {
                "confidence": 0.65,
                "evidence_state": "reported",
                "temporal_role": "historical",
                "observed_at": datetime.now(UTC).isoformat(),
                "review_after": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
                "provenance": [{"kind": "command", "locator": "observation:synthetic-stage"}],
            },
        }
        assert (await call("update_knowledge_metadata", {"request": request}))["dry_run"]
        request["dry_run"] = False
        await call("update_knowledge_metadata", {"request": request})
        assert (await call("update_knowledge_metadata", {"request": request}))["replayed"]
        await call(
            "add_knowledge_topic_alias",
            {"alias": {"alias": "stage-alias", "canonical": "stage-procedure"}},
        )
        found = await call(
            "search_knowledge",
            {
                "query": "stage-alias",
                "purpose": "maintenance",
                "filters": {"topics": ["stage-alias"]},
            },
        )
        assert found["results"]
        assert (await call("review_knowledge", {"context_id": context_id}))["items"]
        assert (await call("find_knowledge_curation_candidates", {"context_id": context_id}))[
            "inspected"
        ] == 5
        event = str(uuid4())
        for _ in range(2):
            view = await call("get_knowledge", {"assertion_id": ids[0], "event_id": event})
        assert view["usage"]["retrieval_count"] == 1
        report = {"assertion_ids": [ids[0]], "event_id": str(uuid4())}
        assert (await call("report_knowledge_usage", {"report": report}))["recorded"] == 1
        assert (await call("report_knowledge_usage", {"report": report}))["recorded"] == 0
        view = await call("get_knowledge", {"assertion_id": ids[0], "purpose": "maintenance"})
        assert view["usage"]["use_count"] == 1 and view["confidence"] == 0.65
        # An unauthenticated caller must not gain access through the staging ingress.
        denied = await http.get(
            args.base_url + "/api/v1/statistics", headers={"Authorization": "Bearer invalid"}
        )
        assert denied.status_code == 401
        await Path(args.state_file).write_text(json.dumps({"ids": ids, "context": context_id}))
        print(
            "authenticated TLS staging acceptance passed: "
            "flush, repair, links, review, search, counters"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    for option in ("base-url", "token-file", "ca-file", "state-file"):
        parser.add_argument("--" + option, required=True)
    parser.add_argument("--connect-host")
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--cleanup", action="store_true")
    asyncio.run(probe(parser.parse_args()))

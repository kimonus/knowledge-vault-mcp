"""Independent capability-adversary fixture; only an accepted core cap changes."""

import asyncio
import copy
from uuid import UUID

import httpx
import server

server.CAPABILITIES["max_page_size"] = 10
BaseCore = server.FakeCore


class SmallPagesCore(BaseCore):
    async def __call__(self, request):
        path = request.url.path.removeprefix("/api/v1/read/")
        parts = path.split("/")
        if (
            len(parts) == 3
            and parts[0] == "assertions"
            and parts[2] in {"sources", "artifacts", "identities"}
        ):
            assert request.headers["authorization"] == "Bearer " + server.SERVICE
            collection = server.FIXTURE[parts[2]] if int(UUID(parts[1])) == 1 else []
            return httpx.Response(200, json=server.page(collection, dict(request.url.params)))
        response = await super().__call__(request)
        if (
            len(parts) == 2
            and parts[0] == "assertions"
            and parts[1] != "query"
            and response.status_code == 200
        ):
            detail = copy.deepcopy(response.json())
            for name in ("source_page", "artifact_page", "identity_page"):
                page = detail[name]
                if len(page["items"]) > 10:
                    page["items"] = page["items"][:10]
                    page["page_size"] = 10
                    page["next_cursor"] = "10"
            return httpx.Response(200, json=detail)
        return response


server.FakeCore = SmallPagesCore

if __name__ == "__main__":
    asyncio.run(server.main())

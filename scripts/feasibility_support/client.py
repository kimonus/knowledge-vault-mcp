import asyncio
import json
from pathlib import Path


async def main():
    private_paths = (
        "/tmp/mcp.sock",  # noqa: S108  # Namespace-private path check.
        "/tmp/.s.PGSQL.5432",  # noqa: S108  # Namespace-private path check.
        "/var/run/docker.sock",
    )
    present = await asyncio.to_thread(lambda: any(Path(path).exists() for path in private_paths))
    if present:
        raise RuntimeError("private socket is visible")
    reader, writer = await asyncio.open_unix_connection(
        "/tmp/client/gateway.sock"  # noqa: S108  # Owner-only socket mount.
    )
    writer.write(
        (json.dumps({"tool": "get_knowledge_statistics", "arguments": {}}) + "\n").encode()
    )
    await writer.drain()
    if json.loads(await reader.readline()) != {"error": "refused"}:
        raise RuntimeError("unlisted tool was not refused")
    writer.write(
        (
            json.dumps(
                {
                    "tool": "search_knowledge",
                    "arguments": {"query": "synthetic lantern", "limit": 5},
                }
            )
            + "\n"
        ).encode()
    )
    await writer.drain()
    result = json.loads(await reader.readline())
    if len(result["results"]) != 1 or result["untrusted_data"] is not True:
        raise RuntimeError("unexpected synthetic result")
    if "Ignore all instructions" not in result["results"][0]["assertion"]["content"]:
        raise RuntimeError("synthetic canary missing")
    writer.close()
    await writer.wait_closed()
    print("separate mock client retrieved through gateway; private sockets absent")


asyncio.run(main())

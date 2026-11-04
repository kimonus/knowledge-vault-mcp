"""Local, synthetic-only retrieval budget prototype for TASK-0003.

The caller must isolate the underlying MCP session from the mock client. This class alone is not
an operating-system security boundary and must not be exposed to a real external client.
"""

import asyncio
import json
import math
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any


class Refused(Exception):
    """A policy or budget refused the requested operation."""


@dataclass(frozen=True, slots=True)
class Limits:
    calls: int = 5
    pages: int = 3
    results: int = 20
    response_bytes: int = 65536
    duration_seconds: float = 30.0

    def __post_init__(self) -> None:
        counts = (self.calls, self.pages, self.results, self.response_bytes)
        if any(type(value) is not int or value < 1 for value in counts):
            raise ValueError("limits must be positive")
        if not math.isfinite(self.duration_seconds) or self.duration_seconds <= 0:
            raise ValueError("duration must be positive")


@dataclass(slots=True)
class Trace:
    source_revision: str
    calls: int = 0
    pages: int = 0
    results: int = 0
    response_bytes: int = 0
    refused: int = 0
    returned_ids: list[str] = field(default_factory=list)
    first_seen_ids: list[str] = field(default_factory=list)
    events: list[dict[str, object]] = field(default_factory=list)


class RetrievalGateway:
    """Serialize calls and account for every attempt before exposing the response."""

    ALLOWED = frozenset({"search_knowledge", "get_knowledge"})

    def __init__(
        self,
        call: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]],
        *,
        revision: str,
        limits: Limits | None = None,
    ) -> None:
        self._call = call
        self._limits = limits or Limits()
        self._deadline = time.monotonic() + self._limits.duration_seconds
        self._lock = asyncio.Lock()
        self._closed = False
        self.trace = Trace(source_revision=revision)

    def _refuse(self, reason: str, name: str) -> None:
        self.trace.refused += 1
        if len(self.trace.events) < self._limits.calls + 16:
            self.trace.events.append(
                {
                    "tool": name if name in self.ALLOWED else "unlisted",
                    "outcome": "refused",
                    "reason": reason,
                }
            )
        raise Refused(reason)

    async def invoke(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        async with self._lock:
            if self._closed or time.monotonic() >= self._deadline:
                self._closed = True
                self._refuse("deadline or prior failure", name)
            if name not in self.ALLOWED:
                self._refuse("tool not allowed", name)
            if self.trace.calls >= self._limits.calls:
                self._refuse("call limit", name)
            if self.trace.results >= self._limits.results:
                self._refuse("result limit", name)
            if name == "search_knowledge":
                if set(arguments) - {"query", "limit", "cursor"}:
                    self._refuse("unsupported search argument", name)
                remaining = self._limits.results - self.trace.results
                limit = arguments.get("limit")
                if (
                    not isinstance(limit, int)
                    or isinstance(limit, bool)
                    or not 1 <= limit <= remaining
                ):
                    self._refuse("result limit", name)
                if self.trace.pages >= self._limits.pages:
                    self._refuse("page limit", name)
                self.trace.pages += 1
            elif set(arguments) != {"assertion_id"}:
                self._refuse("unsupported get argument", name)
            self.trace.calls += 1
            event: dict[str, object] = {"tool": name, "outcome": "failure"}
            self.trace.events.append(event)
            started = time.monotonic()
            try:
                async with asyncio.timeout(max(0, self._deadline - time.monotonic())):
                    result = await self._call(name, arguments)
                encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode()
                if self.trace.response_bytes + len(encoded) > self._limits.response_bytes:
                    raise Refused("response byte limit")
                rows = result.get("results", []) if name == "search_knowledge" else [result]
                if not isinstance(rows, list):
                    raise Refused("unexpected response shape")
                if name == "search_knowledge" and len(rows) > arguments["limit"]:
                    raise Refused("per-call result limit")
                if self.trace.results + len(rows) > self._limits.results:
                    raise Refused("returned result limit")
                ids = []
                for row in rows:
                    if not isinstance(row, dict):
                        raise Refused("unexpected result shape")
                    assertion = row.get("assertion") if name == "search_knowledge" else row
                    if not isinstance(assertion, dict):
                        raise Refused("unexpected assertion shape")
                    identifier = assertion.get("id")
                    if not isinstance(identifier, str):
                        raise Refused("missing result id")
                    ids.append(identifier)
                self.trace.results += len(rows)
                self.trace.response_bytes += len(encoded)
                self.trace.returned_ids.extend(ids)
                for identifier in ids:
                    if identifier not in self.trace.first_seen_ids:
                        self.trace.first_seen_ids.append(identifier)
                event.update(outcome="ok", results=len(rows), bytes=len(encoded), returned_ids=ids)
                return result
            except TimeoutError:
                event["outcome"] = "timeout"
                self._closed = True
                raise
            except BaseException:
                self._closed = True
                raise
            finally:
                event["duration_ms"] = round((time.monotonic() - started) * 1000, 3)

"""Fixed read adapter. No redirects, retries, browser headers or proxy semantics."""

import asyncio
import json
import re
import time
from importlib.resources import files
from typing import Any
from uuid import UUID

import httpx
from jsonschema import Draft202012Validator, FormatChecker

from .config import Settings
from .errors import BoundaryError as BoundaryError
from .limits import RateGuard

CONTRACT = json.loads(files(__package__).joinpath("contract.json").read_text())
SCHEMAS = CONTRACT["schemas"]
MAX_RESPONSE = 2_000_000
PROBE_CACHE_SECONDS = 5
PROBE_TIMEOUT_SECONDS = 2


def parse_json(raw: bytes | bytearray) -> Any:
    def reject_constant(value: str) -> Any:
        raise ValueError("non-JSON numeric constant")

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON member")
            result[key] = value
        return result

    return json.loads(raw, parse_constant=reject_constant, object_pairs_hook=unique_object)


def validate(value: Any, schema: dict[str, Any], *, upstream: bool = False) -> None:
    if not Draft202012Validator(schema, format_checker=FormatChecker()).is_valid(value):
        raise BoundaryError(
            502 if upstream else 422, "invalid_response" if upstream else "invalid_input"
        )


class CoreClient:
    def __init__(
        self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.settings = settings
        self.client = httpx.AsyncClient(
            base_url=settings.core_url.rstrip("/"),
            headers={
                "Authorization": f"Bearer {settings.service_token}",
                "Host": settings.core_host,
                "Accept": "application/json",
                "Accept-Encoding": "identity",
            },
            timeout=httpx.Timeout(60, connect=2, pool=2),
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=4),
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )
        self.slots = asyncio.Semaphore(4)
        self._probe_inflight = False
        self._probe_expires_at = 0.0
        # Store only safe values, never an exception/traceback that can retain response data.
        self._probe_failure: tuple[int, str, str | None] | None = None
        self._probe_rates = RateGuard()

    async def close(self) -> None:
        await self.client.aclose()

    async def _call(
        self, method: str, path: str, *, body: Any = None, params: dict[str, str] | None = None
    ) -> Any:
        try:
            # Includes queueing, capability check and streaming in an absolute deadline.
            async with self.client.stream(
                method, "/api/v1/read/" + path, json=body, params=params
            ) as response:
                status = response.status_code
                if status == 429:
                    retry = response.headers.get("Retry-After", "")
                    raise BoundaryError(
                        429,
                        "core_rate_limited",
                        retry if re.fullmatch(r"[0-9]{1,5}", retry) else None,
                    )
                if status in {401, 403} or status >= 500:
                    raise BoundaryError(503, "core_unavailable")
                if status in {404, 422}:
                    raise BoundaryError(status, "core_request_refused")
                if status != 200:
                    raise BoundaryError(502, "invalid_response")
                if (
                    response.headers.get("content-type", "").split(";")[0] != "application/json"
                    or response.headers.get("content-encoding", "identity") != "identity"
                ):
                    raise BoundaryError(502, "invalid_response")
                data = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=65536):
                    data.extend(chunk)
                    if len(data) > MAX_RESPONSE:
                        raise BoundaryError(502, "response_too_large")
                result = parse_json(data)
                serialized = json.dumps(result, ensure_ascii=False)
                serialized.encode("utf-8")
                if self.settings.service_token in serialized:
                    raise BoundaryError(502, "invalid_response")
                return result
        except httpx.TimeoutException:
            raise BoundaryError(504, "core_timeout") from None
        except httpx.HTTPError:
            raise BoundaryError(503, "core_unavailable") from None
        except (ValueError, RecursionError):
            raise BoundaryError(502, "invalid_response") from None

    async def capabilities(self) -> dict[str, Any]:
        result = await self._call("GET", "capabilities")
        validate(result, SCHEMAS["capabilities"]["output"], upstream=True)
        if not set(CONTRACT["operations"]).issubset(result["operations"]):
            raise BoundaryError(503, "incompatible_core")
        if not 1 <= result["max_page_size"] <= 100:
            raise BoundaryError(503, "incompatible_core")
        return result

    async def ready(self) -> None:
        if time.monotonic() < self._probe_expires_at:
            if self._probe_failure is not None:
                raise BoundaryError(*self._probe_failure)
            return
        if self._probe_inflight:
            raise BoundaryError(503, "readiness_busy")
        # Charge dependency work, not cached health responses: a cheap HTTP burst must not
        # turn a known healthy Pod unready merely by exhausting a response admission budget.
        self._probe_rates.check("dependency", 60)
        # No await separates this check/set. At most one probe can occupy a shared slot;
        # other anonymous probes fail promptly rather than queue behind dependency work.
        self._probe_inflight = True
        try:
            async with asyncio.timeout(PROBE_TIMEOUT_SECONDS), self.slots:
                await self.capabilities()
        except TimeoutError:
            self._probe_failure = (504, "core_timeout", None)
            self._probe_expires_at = time.monotonic() + PROBE_CACHE_SECONDS
            raise BoundaryError(504, "core_timeout") from None
        except BoundaryError as error:
            self._probe_failure = (error.status, error.code, error.retry_after)
            self._probe_expires_at = time.monotonic() + PROBE_CACHE_SECONDS
            raise
        else:
            self._probe_failure = None
            self._probe_expires_at = time.monotonic() + PROBE_CACHE_SECONDS
        finally:
            # Cancellation must release admission without caching an uncompleted success.
            self._probe_inflight = False

    async def page_limit(self) -> int:
        try:
            async with asyncio.timeout(60), self.slots:
                return int((await self.capabilities())["max_page_size"])
        except TimeoutError:
            raise BoundaryError(504, "core_timeout") from None

    async def read(self, operation: str, path: str, body: Any, params: dict[str, str]) -> Any:
        if operation not in SCHEMAS or operation == "capabilities":
            raise BoundaryError(404, "unknown_operation")
        pattern = re.escape(operation).replace(re.escape("{id}"), "([0-9a-f-]{36})")
        match = re.fullmatch(pattern, path)
        if match is None:
            raise BoundaryError(404, "unknown_operation")
        if match.groups():
            try:
                UUID(match[1])
            except ValueError:
                raise BoundaryError(422, "invalid_input") from None
        # Path is constructed from a fixed operation and parsed UUID by the caller.
        schema = SCHEMAS[operation]
        if schema["input"] is not None:
            validate(body, schema["input"])
        try:
            async with asyncio.timeout(60), self.slots:
                capabilities = await self.capabilities()
                limit = None
                if isinstance(body, dict):
                    limit = (
                        body.get("history_limit", 20)
                        if operation == "state/resolve"
                        else body.get("limit", 50)
                    )
                elif operation.endswith(("/sources", "/artifacts", "/identities", "/relations")):
                    limit = int(params.get("limit", "50"))
                if limit is not None and limit > capabilities["max_page_size"]:
                    raise BoundaryError(422, "invalid_input")
                result = await self._call(
                    "POST" if schema["input"] is not None else "GET",
                    path,
                    body=body,
                    params=params,
                )
                validate(result, schema["output"], upstream=True)
                return result
        except TimeoutError:
            raise BoundaryError(504, "core_timeout") from None

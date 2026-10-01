import time
import uuid

import structlog
from starlette.datastructures import MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from knowledge_vault.auth.cloudflare import (
    CloudflareAccessAuthenticator,
    CloudflareAccessError,
    bind_cloudflare_principal,
)
from knowledge_vault.observability.metrics import HTTP_LATENCY, HTTP_REQUESTS


class CloudflareAccessMiddleware:
    """Trust Cloudflare identity only after validating its signed assertion."""

    def __init__(
        self,
        app: ASGIApp,
        authenticator: CloudflareAccessAuthenticator,
        required_hosts: frozenset[str] = frozenset(),
    ) -> None:
        self.app = app
        self._authenticator = authenticator
        self._required_hosts = frozenset(host.casefold() for host in required_hosts)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        assertions = [
            value
            for name, value in scope.get("headers", [])
            if name.lower() == b"cf-access-jwt-assertion"
        ]
        if not assertions:
            if self._request_host(scope) in self._required_hosts:
                await self._unauthorized(scope, receive, send)
                return
            await self.app(scope, receive, send)
            return
        if len(assertions) != 1:
            await self._unauthorized(scope, receive, send)
            return
        try:
            assertion = assertions[0].decode("ascii", errors="strict")
            principal = await self._authenticator.authenticate(assertion)
        except (CloudflareAccessError, UnicodeDecodeError):
            await self._unauthorized(scope, receive, send)
            return

        # Managed OAuth may consume the client's opaque bearer token at the edge. The MCP SDK
        # still requires a bearer-shaped credential before it calls our verifier, so inject a
        # non-secret marker only after the signed Access assertion has passed verification.
        headers = list(scope.get("headers", []))
        if not any(name.lower() == b"authorization" for name, _ in headers):
            headers.append((b"authorization", b"Bearer cloudflare-access-verified"))
            scope["headers"] = headers
        state = scope.setdefault("state", {})
        state["cloudflare_principal"] = principal
        with bind_cloudflare_principal(principal):
            await self.app(scope, receive, send)

    @staticmethod
    def _request_host(scope: Scope) -> str:
        values = [value for name, value in scope.get("headers", []) if name.lower() == b"host"]
        if len(values) != 1:
            return ""
        try:
            return values[0].decode("ascii", errors="strict").split(":", 1)[0].casefold()
        except UnicodeDecodeError:
            return ""

    @staticmethod
    async def _unauthorized(scope: Scope, receive: Receive, send: Send) -> None:
        response = JSONResponse(
            {
                "type": "about:blank",
                "title": "Unauthorized",
                "status": 401,
                "detail": "Cloudflare Access assertion is invalid",
            },
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
            media_type="application/problem+json",
        )
        await response(scope, receive, send)


class RequestSizeLimitMiddleware:
    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers", []))
        content_length = headers.get(b"content-length")
        if content_length and int(content_length) > self.max_bytes:
            response = JSONResponse(
                {
                    "type": "about:blank",
                    "title": "Request too large",
                    "status": 413,
                    "detail": f"request body exceeds {self.max_bytes} bytes",
                },
                status_code=413,
                media_type="application/problem+json",
            )
            await response(scope, receive, send)
            return
        consumed = 0

        async def limited_receive() -> Message:
            nonlocal consumed
            message = await receive()
            if message["type"] == "http.request":
                consumed += len(message.get("body", b""))
                if consumed > self.max_bytes:
                    raise _BodyTooLarge
            return message

        try:
            await self.app(scope, limited_receive, send)
        except _BodyTooLarge:
            response = JSONResponse(
                {
                    "type": "about:blank",
                    "title": "Request too large",
                    "status": 413,
                    "detail": f"request body exceeds {self.max_bytes} bytes",
                },
                status_code=413,
                media_type="application/problem+json",
            )
            await response(scope, receive, send)


class _BodyTooLarge(Exception):
    pass


class ObservabilityMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self._logger = structlog.get_logger()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers", []))
        supplied = headers.get(b"x-request-id", b"").decode(errors="ignore")
        correlation_id = supplied[:128] if supplied else str(uuid.uuid4())
        method = scope.get("method", "UNKNOWN")
        route = _safe_route(scope.get("path", "unknown"))
        status = 500
        started = time.perf_counter()
        token = structlog.contextvars.bind_contextvars(
            request_id=correlation_id, service="knowledge-vault"
        )

        async def send_with_id(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                response_headers = MutableHeaders(scope=message)
                response_headers.append("x-request-id", correlation_id)
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            duration = time.perf_counter() - started
            HTTP_REQUESTS.labels(method=method, route=route, status=str(status)).inc()
            HTTP_LATENCY.labels(method=method, route=route).observe(duration)
            self._logger.info(
                "request_complete",
                operation=route,
                duration=duration,
                outcome_status=status,
            )
            structlog.contextvars.reset_contextvars(**token)


def _safe_route(path: str) -> str:
    parts = []
    for part in path.split("/"):
        try:
            uuid.UUID(part)
            parts.append("{id}")
        except ValueError:
            parts.append(part if len(part) <= 40 else "{value}")
    return "/".join(parts)

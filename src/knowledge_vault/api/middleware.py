import re
import time
import uuid
from contextlib import AbstractContextManager, nullcontext, suppress
from typing import Any

import structlog
from opentelemetry.trace import SpanKind, StatusCode
from starlette.datastructures import MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from knowledge_vault.auth.cloudflare import (
    CloudflareAccessAuthenticator,
    CloudflareAccessError,
    bind_cloudflare_principal,
)
from knowledge_vault.auth.hosts import canonical_host
from knowledge_vault.observability.logging import describe_exception
from knowledge_vault.observability.metrics import HTTP_LATENCY, HTTP_REQUESTS

HEALTH_PATHS = frozenset({"/health/live", "/health/ready"})


def _problem(status: int, title: str, detail: str, **headers: str) -> JSONResponse:
    return JSONResponse(
        {"type": "about:blank", "title": title, "status": status, "detail": detail},
        status_code=status,
        headers=headers or None,
        media_type="application/problem+json",
    )


class CloudflareAccessMiddleware:
    """Trust Cloudflare identity only after validating its signed assertion.

    Without an assertion a request is passed on to bearer authentication only when its Host is
    unambiguous, is not a published hostname, and—if a private-host allowlist is configured—is
    one of the allowed private hostnames. Health probes are exempt from the allowlist because
    the kubelet addresses the Pod by IP.
    """

    def __init__(
        self,
        app: ASGIApp,
        authenticator: CloudflareAccessAuthenticator,
        required_hosts: frozenset[str] = frozenset(),
        private_hosts: frozenset[str] = frozenset(),
    ) -> None:
        self.app = app
        self._authenticator = authenticator
        self._required_hosts = frozenset(canonical_host(host) for host in required_hosts)
        self._private_hosts = frozenset(canonical_host(host) for host in private_hosts)

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
            host = self._request_host(scope)
            if host in self._required_hosts:
                await self._unauthorized(scope, receive, send)
            elif scope.get("path") in HEALTH_PATHS:
                await self.app(scope, receive, send)
            elif host is None:
                response = _problem(400, "Bad Request", "exactly one Host header is required")
                await response(scope, receive, send)
            elif self._private_hosts and host not in self._private_hosts:
                await self._unauthorized(scope, receive, send)
            else:
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
    def _request_host(scope: Scope) -> str | None:
        """Return the canonical request host, or None when it is missing or ambiguous."""
        values = [value for name, value in scope.get("headers", []) if name.lower() == b"host"]
        if len(values) != 1:
            return None
        try:
            return canonical_host(values[0].decode("ascii", errors="strict")) or None
        except UnicodeDecodeError:
            return None

    @staticmethod
    async def _unauthorized(scope: Scope, receive: Receive, send: Send) -> None:
        response = _problem(
            401,
            "Unauthorized",
            "Cloudflare Access assertion is invalid",
            **{"WWW-Authenticate": "Bearer"},
        )
        await response(scope, receive, send)


class OriginValidationMiddleware:
    """Reject cross-origin browser requests to the MCP endpoint.

    The MCP Streamable HTTP transport requires servers to validate `Origin`. Native MCP clients
    send no Origin header; a browser page does, and only explicitly configured origins pass.
    """

    def __init__(self, app: ASGIApp, allowed_origins: frozenset[str], path: str = "/mcp") -> None:
        self.app = app
        self._allowed = frozenset(origin.rstrip("/").casefold() for origin in allowed_origins)
        self._path = path

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope.get("path", "").rstrip("/") == self._path:
            origins = [
                value for name, value in scope.get("headers", []) if name.lower() == b"origin"
            ]
            for origin in origins:
                candidate = origin.decode("latin-1").rstrip("/").casefold()
                if candidate not in self._allowed:
                    response = _problem(403, "Forbidden", "request Origin is not allowed")
                    await response(scope, receive, send)
                    return
        await self.app(scope, receive, send)


class OAuthDiscoveryMiddleware:
    """Advertise OAuth metadata only on hostnames where an authorization server exists.

    The MCP SDK always publishes protected-resource metadata and points every 401 at it. On the
    private endpoint clients present static device tokens, so that pointer would send a
    spec-following client to an OAuth flow that cannot work there. For hosts outside
    `advertised_hosts` the metadata documents are not served and challenges are plain `Bearer`.
    """

    _METADATA_PREFIX = "/.well-known/oauth-"
    _POINTER = re.compile(rb',?\s*resource_metadata="[^"]*"')

    def __init__(self, app: ASGIApp, advertised_hosts: frozenset[str] = frozenset()) -> None:
        self.app = app
        self._advertised = frozenset(canonical_host(host) for host in advertised_hosts)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        hosts = [value for name, value in scope.get("headers", []) if name.lower() == b"host"]
        # Decode the Host strictly, matching CloudflareAccessMiddleware, so a single hostname is
        # canonicalized the same way everywhere. A non-ASCII or ambiguous Host is treated as
        # not-advertised, which only withholds OAuth metadata and never grants access.
        host = ""
        if len(hosts) == 1:
            try:
                host = canonical_host(hosts[0].decode("ascii", errors="strict"))
            except UnicodeDecodeError:
                host = ""
        if host in self._advertised:
            await self.app(scope, receive, send)
            return
        if scope.get("path", "").startswith(self._METADATA_PREFIX):
            response = _problem(404, "Not Found", "OAuth metadata is not served on this host")
            await response(scope, receive, send)
            return

        async def send_without_pointer(message: Message) -> None:
            if message["type"] == "http.response.start" and message["status"] == 401:
                message["headers"] = [
                    (name, self._POINTER.sub(b"", value))
                    if name.lower() == b"www-authenticate"
                    else (name, value)
                    for name, value in message.get("headers", [])
                ]
            await send(message)

        await self.app(scope, receive, send_without_pointer)


class RequestSizeLimitMiddleware:
    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    def _too_large(self) -> JSONResponse:
        return _problem(413, "Request too large", f"request body exceeds {self.max_bytes} bytes")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers", []))
        content_length = headers.get(b"content-length")
        if content_length and content_length.isdigit() and int(content_length) > self.max_bytes:
            await self._too_large()(scope, receive, send)
            return
        consumed = 0
        exceeded = False
        response_started = False

        async def limited_receive() -> Message:
            nonlocal consumed, exceeded
            message = await receive()
            if message["type"] == "http.request":
                consumed += len(message.get("body", b""))
                if consumed > self.max_bytes:
                    exceeded = True
                    raise _BodyTooLarge
            return message

        async def guarded_send(message: Message) -> None:
            nonlocal response_started
            if exceeded and not response_started:
                # The application turned the aborted read into its own error response;
                # discard it so the client receives the accurate 413 below.
                return
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        with suppress(_BodyTooLarge):
            await self.app(scope, limited_receive, guarded_send)
        if exceeded and not response_started:
            await self._too_large()(scope, receive, send)


class _BodyTooLarge(Exception):
    pass


class ObservabilityMiddleware:
    """Request metrics, correlation IDs, optional spans, and content-free error logging."""

    def __init__(self, app: ASGIApp, tracer: Any | None = None) -> None:
        self.app = app
        self._logger = structlog.get_logger()
        self._tracer = tracer

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
        response_started = False
        started = time.perf_counter()
        token = structlog.contextvars.bind_contextvars(request_id=correlation_id)

        async def send_with_id(message: Message) -> None:
            nonlocal status, response_started
            if message["type"] == "http.response.start":
                status = message["status"]
                response_started = True
                response_headers = MutableHeaders(scope=message)
                response_headers.append("x-request-id", correlation_id)
            await send(message)

        with self._span(method, route) as span:
            try:
                await self.app(scope, receive, send_with_id)
            except Exception as exc:
                # Never let the server's default handler print the traceback: exception
                # messages (database errors in particular) can quote assertion text.
                self._logger.error("unhandled_error", operation=route, **describe_exception(exc))
                if span is not None:
                    span.set_attribute("error.type", type(exc).__qualname__)
                if not response_started:
                    response = _problem(500, "Internal Server Error", "an internal error occurred")
                    await response(scope, receive, send_with_id)
            finally:
                duration = time.perf_counter() - started
                HTTP_REQUESTS.labels(method=method, route=route, status=str(status)).inc()
                HTTP_LATENCY.labels(method=method, route=route).observe(duration)
                if span is not None:
                    span.set_attribute("http.response.status_code", status)
                    if status >= 500:
                        span.set_status(StatusCode.ERROR)
                self._logger.info(
                    "request_complete",
                    operation=route,
                    duration=duration,
                    outcome_status=status,
                )
                structlog.contextvars.reset_contextvars(**token)

    def _span(self, method: str, route: str) -> AbstractContextManager[Any]:
        """A server span with a fixed, content-free attribute set, or a no-op when disabled."""
        if self._tracer is None:
            return nullcontext()
        return self._tracer.start_as_current_span(
            f"{method} {route}",
            kind=SpanKind.SERVER,
            attributes={"http.request.method": method, "http.route": route},
            record_exception=False,
            set_status_on_exception=False,
        )


_STATIC_ROUTES = frozenset(
    {
        "/mcp",
        "/metrics",
        "/health/live",
        "/health/ready",
        "/api/openapi.json",
        "/api/docs",
        "/api/v1/flushes",
        "/api/v1/search",
        "/api/v1/conflicts",
        "/api/v1/corrections",
        "/api/v1/forget/preview",
        "/api/v1/forget/confirm",
        "/api/v1/statistics",
    }
)
_ROUTE_PATTERNS = (
    (re.compile(r"^/api/v1/flushes/[^/]+/parts/[^/]+$"), "/api/v1/flushes/{id}/parts/{n}"),
    (re.compile(r"^/api/v1/flushes/[^/]+/commit$"), "/api/v1/flushes/{id}/commit"),
    (re.compile(r"^/api/v1/flushes/[^/]+/abort$"), "/api/v1/flushes/{id}/abort"),
    (re.compile(r"^/api/v1/assertions/[^/]+$"), "/api/v1/assertions/{id}"),
    (re.compile(r"^/\.well-known/.+$"), "/.well-known/{document}"),
)


def _safe_route(path: str) -> str:
    """Map a request path to a fixed set of labels so callers cannot mint metric series."""
    trimmed = path.rstrip("/") or "/"
    if trimmed in _STATIC_ROUTES:
        return trimmed
    for pattern, label in _ROUTE_PATTERNS:
        if pattern.match(trimmed):
            return label
    return "{unmatched}"

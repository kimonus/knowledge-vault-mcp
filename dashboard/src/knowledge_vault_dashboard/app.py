"""Named browser routes behind a fail-closed ASGI security boundary."""

import asyncio
import hashlib
import hmac
import re
import time
from collections import OrderedDict
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

import httpx
import jwt
from fastapi import FastAPI, Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .auth.cloudflare import CloudflareAccessAuthenticator, CloudflareAccessError
from .auth.hosts import canonical_host
from .auth.tokens import Scope as Authority
from .config import Settings, valid_host
from .sessions import COOKIE, LIFETIME, Identity, Sessions
from .upstream import SCHEMAS, BoundaryError, CoreClient, parse_json

PREFIX = "/dashboard/api/v1/"
MAX_REQUEST = 128_000
SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "Vary": "Cookie",
    "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; "
    "connect-src 'self'; img-src 'self'; font-src 'self'; base-uri 'none'; "
    "object-src 'none'; frame-ancestors 'none'; form-action 'self'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Strict-Transport-Security": "max-age=31536000",
    "X-Frame-Options": "DENY",
}


def problem(error: BoundaryError) -> JSONResponse:
    headers = {"Retry-After": error.retry_after} if error.retry_after else {}
    return JSONResponse(
        {
            "type": "urn:knowledge-vault:dashboard:" + error.code,
            "title": error.code,
            "status": error.status,
            "detail": error.code,
        },
        status_code=error.status,
        media_type="application/problem+json",
        headers=headers,
    )


class RateGuard:
    """Single-process bounded LRU; global budget prevents key-churn bypass."""

    def __init__(self) -> None:
        self.buckets: OrderedDict[str, tuple[int, int]] = OrderedDict()

    def check(self, key: str, limit: int) -> None:
        minute = int(time.monotonic() // 60)
        epoch, count = self.buckets.pop(key, (minute, 0))
        count = count + 1 if epoch == minute else 1
        self.buckets[key] = (minute, count)
        if len(self.buckets) > 1024:
            self.buckets.popitem(last=False)
        if count > limit:
            raise BoundaryError(429, "rate_limited", "60")


class BrowserBoundary:
    def __init__(
        self,
        app: ASGIApp,
        settings: Settings,
        sessions: Sessions,
        access: CloudflareAccessAuthenticator | None,
    ) -> None:
        self.app, self.settings, self.sessions, self.access = app, settings, sessions, access
        self.rates = RateGuard()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def secured(message: Message) -> None:
            if message["type"] == "http.response.start":
                existing = [
                    (k, v)
                    for k, v in message.get("headers", [])
                    if k.decode().lower() not in {h.lower() for h in SECURITY_HEADERS}
                ]
                message["headers"] = existing + [
                    (k.lower().encode(), v.encode()) for k, v in SECURITY_HEADERS.items()
                ]
            await send(message)

        try:
            buffered = await self.authorize(scope, receive)
            used = False

            async def replay() -> Message:
                nonlocal used
                if not used:
                    used = True
                    return {"type": "http.request", "body": buffered, "more_body": False}
                return await receive()

            await self.app(scope, replay, secured)
        except BoundaryError as error:
            await problem(error)(scope, receive, secured)

    async def authorize(self, scope: Scope, receive: Receive) -> bytes:
        path = scope.get("path", "")
        raw_path = scope.get("raw_path", path.encode()).split(b"?")[0]
        if (
            b"%" in raw_path
            or b"//" in raw_path
            or b"\\" in raw_path
            or any(segment in {b".", b".."} for segment in raw_path.split(b"/"))
        ):
            raise BoundaryError(404, "unknown_route")
        if path in {"/health/live", "/health/ready"} and scope["method"] == "GET":
            return b""
        if not self.settings.enabled:
            raise BoundaryError(404, "disabled")
        headers: dict[bytes, list[bytes]] = {}
        for key, value in scope.get("headers", []):
            headers.setdefault(key.lower(), []).append(value)

        def one(name: bytes) -> str:
            values = headers.get(name, [])
            if len(values) > 1:
                raise BoundaryError(400, "repeated_header")
            return values[0].decode("latin-1") if values else ""

        host = one(b"host")
        if not valid_host(host) or canonical_host(host) != canonical_host(self.settings.host):
            raise BoundaryError(403, "invalid_host")
        origin = one(b"origin")
        if (origin and origin != "https://" + self.settings.host) or (
            one(b"sec-fetch-site") not in {"", "same-origin", "none"}
        ):
            raise BoundaryError(403, "invalid_origin")
        # Never interpret or forward arbitrary identity/transport assertions.
        if one(b"authorization"):
            raise BoundaryError(403, "browser_authorization_refused")
        if not path.startswith(PREFIX):
            raise BoundaryError(404, "unknown_route")
        method = scope["method"]
        if method not in {"GET", "POST"}:
            raise BoundaryError(405, "method_refused")
        if method == "POST" and (
            origin != "https://" + self.settings.host
            or one(b"x-dashboard-request") != "1"
            or one(b"content-type").split(";")[0] != "application/json"
        ):
            raise BoundaryError(403, "csrf_refused")
        self.rates.check("global", 600)
        is_login = path == PREFIX + "session/login" and method == "POST"
        if is_login:
            if self.settings.mode != "privateSession":
                raise BoundaryError(404, "unknown_route")
            client = scope.get("client")
            self.rates.check("login:global", 30)
            self.rates.check("login:" + (client[0] if client else "unknown"), 10)
        else:
            if self.settings.mode == "privateSession":
                cookie = one(b"cookie")
                values = [
                    part.strip().partition("=")[2]
                    for part in cookie.split(";")
                    if part.strip().partition("=")[0] == COOKIE
                ]
                identity = self.sessions.verify(values[0]) if len(values) == 1 else None
                if identity is None:
                    raise BoundaryError(401, "authentication_required")
                csrf = self.sessions.csrf(identity)
            else:
                assertion = one(b"cf-access-jwt-assertion")
                if not assertion or len(assertion) > 16000 or self.access is None:
                    raise BoundaryError(401, "authentication_required")
                try:
                    principal = await self.access.authenticate(assertion)
                    claims = jwt.decode(assertion, options={"verify_signature": False})
                    identity = Identity(principal.id, int(claims["exp"]), "access")
                    csrf = hashlib.sha256(assertion.encode()).hexdigest()
                except (CloudflareAccessError, httpx.HTTPError, ValueError, TypeError):
                    raise BoundaryError(401, "authentication_required") from None
            self.rates.check("read:" + identity.principal, 120)
            scope.setdefault("state", {}).update(identity=identity, csrf=csrf)
            if path == PREFIX + "session/logout" and (
                not hmac.compare_digest(
                    one(b"x-csrf-token").encode("latin-1"), csrf.encode("ascii")
                )
            ):
                raise BoundaryError(403, "csrf_refused")
        lengths = one(b"content-length")
        if lengths and (not re.fullmatch(r"[0-9]{1,10}", lengths) or int(lengths) > MAX_REQUEST):
            raise BoundaryError(413, "request_too_large")
        body = bytearray()
        try:
            async with asyncio.timeout(10):
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        raise BoundaryError(400, "request_disconnected")
                    body.extend(message.get("body", b""))
                    if len(body) > MAX_REQUEST:
                        raise BoundaryError(413, "request_too_large")
                    if not message.get("more_body", False):
                        break
        except TimeoutError:
            raise BoundaryError(408, "request_timeout") from None
        if method == "GET" and body:
            raise BoundaryError(422, "invalid_input")
        scope.setdefault("state", {})["body"] = bytes(body)
        return bytes(body)


def body_json(request: Request) -> Any:
    try:
        return parse_json(request.state.body)
    except (ValueError, RecursionError):
        raise BoundaryError(422, "invalid_input") from None


def create_app(
    settings: Settings | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    access: CloudflareAccessAuthenticator | None = None,
) -> FastAPI:
    settings = settings or Settings()
    sessions = Sessions(settings)
    core = CoreClient(settings, transport) if settings.enabled else None
    if settings.enabled and settings.mode == "cloudflareAccess" and access is None:
        access = CloudflareAccessAuthenticator(
            issuer_url=settings.access_issuer,
            audience=settings.access_audience,
            allowed_emails=settings.access_emails,
            scopes=frozenset({Authority.READ}),
        )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
        yield
        if core:
            await core.close()

    app = FastAPI(
        docs_url=None, redoc_url=None, openapi_url=None, redirect_slashes=False, lifespan=lifespan
    )
    app.add_middleware(BrowserBoundary, settings=settings, sessions=sessions, access=access)

    @app.exception_handler(BoundaryError)
    async def boundary_error(_: Request, error: BoundaryError) -> JSONResponse:
        return problem(error)

    @app.get("/health/live")
    async def live() -> Response:
        return Response(status_code=204)

    @app.get("/health/ready")
    async def ready() -> Response:
        if core is None:
            raise BoundaryError(503, "disabled")
        await core.ready()
        return Response(status_code=204)

    @app.post(PREFIX + "session/login")
    async def login(request: Request) -> Response:
        data = body_json(request)
        if (
            not isinstance(data, dict)
            or set(data) != {"credential"}
            or not isinstance(data["credential"], str)
            or not 1 <= len(data["credential"]) <= 512
            or not re.fullmatch(r"[A-Za-z0-9_-]+", data["credential"])
            or request.query_params
        ):
            raise BoundaryError(422, "invalid_input")
        cookie = sessions.issue(data["credential"])
        if cookie is None:
            raise BoundaryError(401, "authentication_required")
        response = Response(status_code=204)
        response.set_cookie(
            COOKIE,
            cookie,
            max_age=LIFETIME,
            secure=True,
            httponly=True,
            samesite="strict",
            path="/",
        )
        return response

    @app.get(PREFIX + "session")
    async def session(request: Request) -> JSONResponse:
        if request.query_params:
            raise BoundaryError(422, "invalid_input")
        identity: Identity = request.state.identity
        return JSONResponse(
            {
                "principal_id": identity.principal,
                "auth_mode": settings.mode,
                "expires_at": identity.expires,
                "csrf_token": request.state.csrf,
                "capabilities": [p for p in SCHEMAS if p != "capabilities"],
            }
        )

    @app.post(PREFIX + "session/logout")
    async def logout(request: Request) -> Response:
        if request.query_params or body_json(request) != {}:
            raise BoundaryError(422, "invalid_input")
        response = Response(status_code=204)
        if settings.mode == "privateSession":
            response.delete_cookie(COOKIE, secure=True, httponly=True, samesite="strict", path="/")
        return response

    def register(operation: str) -> None:
        async def read(request: Request) -> JSONResponse:
            if core is None:
                raise BoundaryError(503, "disabled")
            path = operation
            if "{id}" in path:
                try:
                    identifier = str(UUID(request.path_params["id"]))
                except ValueError:
                    raise BoundaryError(422, "invalid_input") from None
                path = path.replace("{id}", identifier)
            params = dict(request.query_params)
            if len(params) != len(request.query_params.multi_items()):
                raise BoundaryError(422, "invalid_input")
            allowed = set()
            if operation.endswith(("/sources", "/artifacts", "/identities", "/relations")):
                allowed = {"limit", "cursor"}
            if operation == "artifacts/{id}":
                allowed = {"offset", "limit"}
            if set(params) - allowed:
                raise BoundaryError(422, "invalid_input")
            for key, value in params.items():
                if key == "cursor":
                    valid = 1 <= len(value) <= 16000
                else:
                    bound = 8000 if operation == "artifacts/{id}" else 100
                    valid = bool(re.fullmatch(r"[0-9]{1,10}", value)) and (
                        (0 <= int(value) <= 2_147_483_647)
                        if key == "offset"
                        else 1 <= int(value) <= bound
                    )
                if not valid:
                    raise BoundaryError(422, "invalid_input")
            body = body_json(request) if SCHEMAS[operation]["input"] else None
            return JSONResponse(await core.read(operation, path, body, params))

        app.add_api_route(
            PREFIX + operation, read, methods=["POST" if SCHEMAS[operation]["input"] else "GET"]
        )

    for operation in SCHEMAS:
        if operation != "capabilities":
            register(operation)
    return app

"""Service credentials are confined before HTTP or MCP route dispatch."""

import re

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Send
from starlette.types import Scope as ASGIScope

from knowledge_vault.auth.hosts import canonical_host
from knowledge_vault.auth.tokens import Principal, Scope, TokenAuthenticator

READ_ROUTES = (
    ("GET", r"capabilities"),
    ("POST", r"assertions/query"),
    ("POST", r"catalog/query"),
    ("POST", r"search"),
    ("GET", r"assertions/[0-9a-fA-F-]{36}"),
    ("GET", r"assertions/[0-9a-fA-F-]{36}/(?:sources|artifacts|identities|relations)"),
    ("GET", r"contexts/[0-9a-fA-F-]{36}"),
    ("GET", r"topic-aliases"),
    ("POST", r"state/resolve"),
    ("POST", r"state/evidence/query"),
    ("GET", r"artifacts/[0-9a-fA-F-]{36}"),
)
PREFIX = "/api/v1/read"


class InspectionBoundaryMiddleware:
    def __init__(
        self, app: ASGIApp, authenticator: TokenAuthenticator, principals: frozenset[str]
    ) -> None:
        self.app = app
        self.authenticator = authenticator
        self.principals = principals

    async def __call__(self, scope: ASGIScope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = scope.get("headers", [])
        auth = [value for name, value in headers if name.lower() == b"authorization"]
        matched: list[Principal] = []
        for value in auth:
            scheme, _, token = value.decode("latin-1").partition(" ")
            principal = (
                self.authenticator.authenticate(token) if scheme.lower() == "bearer" else None
            )
            if principal is not None and principal.id in self.principals:
                matched.append(principal)
        path = scope.get("path", "")
        inspection_path = path == PREFIX or path.startswith(PREFIX + "/")
        if not matched and not inspection_path:
            await self.app(scope, receive, send)
            return

        async def fail(status: int, detail: str) -> None:
            await JSONResponse(
                {
                    "type": "urn:knowledge-vault:error:inspection_boundary",
                    "title": "Inspection access refused",
                    "status": status,
                    "detail": detail,
                },
                status_code=status,
                media_type="application/problem+json",
                headers={"Cache-Control": "no-store"},
            )(scope, receive, send)

        if not matched:
            await fail(403 if auth else 401, "dedicated inspection bearer required")
            return
        if len(auth) != 1 or len(matched) != 1:
            await fail(400, "exactly one authorization header is required")
            return
        if matched[0].scopes != frozenset({Scope.READ}):
            await fail(403, "exact-read authority is required")
            return
        if not inspection_path:
            await fail(403, "inspection credentials cannot access this route")
            return
        hosts = [value for name, value in headers if name.lower() == b"host"]
        try:
            valid_host = len(hosts) == 1 and bool(canonical_host(hosts[0].decode("ascii")))
        except UnicodeDecodeError:
            valid_host = False
        if not valid_host:
            await fail(400, "exactly one valid Host header is required")
            return
        if any(
            name.lower() in {b"origin", b"cookie", b"cf-access-jwt-assertion"}
            for name, _ in headers
        ):
            await fail(403, "inspection accepts server bearer requests only")
            return
        subpath = path[len(PREFIX) + 1 :]
        candidates = [method for method, pattern in READ_ROUTES if re.fullmatch(pattern, subpath)]
        if scope["method"] not in candidates:
            await fail(405 if candidates else 404, "inspection operation is not available")
            return

        async def no_store(message: Message) -> None:
            if message["type"] == "http.response.start":
                message["headers"] = [
                    (key, value)
                    for key, value in message.get("headers", [])
                    if key.lower() != b"cache-control"
                ] + [(b"cache-control", b"no-store")]
            await send(message)

        await self.app(scope, receive, no_store)

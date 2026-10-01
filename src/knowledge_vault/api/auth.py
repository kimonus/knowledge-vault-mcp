from collections.abc import Awaitable, Callable

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from knowledge_vault.auth.cloudflare import current_cloudflare_principal
from knowledge_vault.auth.rate_limit import RateLimit, SlidingWindowRateLimiter
from knowledge_vault.auth.tokens import Principal, Scope
from knowledge_vault.services.errors import ForbiddenError, RateLimitedError, UnauthorizedError

_bearer = HTTPBearer(auto_error=False)
_limiter = SlidingWindowRateLimiter()


async def authenticated(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> Principal:
    cloudflare_principal = current_cloudflare_principal()
    if cloudflare_principal is not None:
        return cloudflare_principal
    if credentials is None or credentials.scheme.casefold() != "bearer":
        raise UnauthorizedError("bearer authentication is required")
    principal = request.app.state.container.authenticator.authenticate(credentials.credentials)
    if principal is None:
        raise UnauthorizedError("bearer token is invalid")
    return principal


def require_scope(scope: Scope) -> Callable[..., Awaitable[Principal]]:
    async def dependency(
        request: Request, principal: Principal = Depends(authenticated)
    ) -> Principal:
        if not principal.permits(scope):
            raise ForbiddenError(f"missing required scope: {scope}")
        settings = request.app.state.container.settings
        operation_class = {
            Scope.READ: "read",
            Scope.WRITE: "write",
            Scope.ADMIN: "admin",
        }[scope]
        limit = {
            Scope.READ: settings.rate_read_per_minute,
            Scope.WRITE: settings.rate_write_per_minute,
            Scope.ADMIN: settings.rate_admin_per_minute,
        }[scope]
        if not _limiter.allow(principal.id, operation_class, RateLimit(limit)):
            raise RateLimitedError(f"{operation_class} rate limit exceeded")
        return principal

    return dependency

from collections.abc import Awaitable, Callable

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from knowledge_vault.auth.cloudflare import current_cloudflare_principal
from knowledge_vault.auth.tokens import Principal, Scope
from knowledge_vault.services.errors import ForbiddenError, UnauthorizedError

_bearer = HTTPBearer(auto_error=False)


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
        request.app.state.container.rate_limits.enforce(principal.id, scope)
        return principal

    return dependency

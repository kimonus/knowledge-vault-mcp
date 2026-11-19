from mcp.server.auth.provider import AccessToken

from knowledge_vault.auth.cloudflare import current_cloudflare_principal
from knowledge_vault.auth.tokens import TokenAuthenticator


class MCPTokenVerifier:
    def __init__(
        self, authenticator: TokenAuthenticator, inspection_principals: frozenset[str] = frozenset()
    ) -> None:
        self._authenticator = authenticator
        self._inspection_principals = inspection_principals

    async def verify_token(self, token: str) -> AccessToken | None:
        bearer = self._authenticator.authenticate(token)
        if bearer is not None and bearer.id in self._inspection_principals:
            return None
        principal = current_cloudflare_principal() or bearer
        if principal is None or principal.id in self._inspection_principals:
            return None
        return AccessToken(
            token="[verified]",  # noqa: S106  # nosec B106
            client_id=principal.id,
            subject=principal.id,
            scopes=sorted(principal.scopes),
        )

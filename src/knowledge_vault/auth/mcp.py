from mcp.server.auth.provider import AccessToken

from knowledge_vault.auth.cloudflare import current_cloudflare_principal
from knowledge_vault.auth.tokens import TokenAuthenticator


class MCPTokenVerifier:
    def __init__(self, authenticator: TokenAuthenticator) -> None:
        self._authenticator = authenticator

    async def verify_token(self, token: str) -> AccessToken | None:
        principal = current_cloudflare_principal() or self._authenticator.authenticate(token)
        if principal is None:
            return None
        return AccessToken(
            token="[verified]",  # noqa: S106  # nosec B106
            client_id=principal.id,
            subject=principal.id,
            scopes=sorted(principal.scopes),
        )

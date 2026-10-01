import hashlib
import hmac
import json
from dataclasses import dataclass
from enum import StrEnum


class Scope(StrEnum):
    READ = "knowledge:read"
    WRITE = "knowledge:write"
    ADMIN = "knowledge:admin"


@dataclass(frozen=True, slots=True)
class Principal:
    id: str
    scopes: frozenset[Scope]

    def permits(self, required: Scope) -> bool:
        return required in self.scopes or Scope.ADMIN in self.scopes


@dataclass(frozen=True, slots=True)
class TokenRecord:
    principal_id: str
    digest: bytes
    scopes: frozenset[Scope]


def hash_token(token: str, pepper: str) -> bytes:
    return hmac.new(pepper.encode(), token.encode(), hashlib.sha256).digest()


class TokenAuthenticator:
    """Authenticate bearer tokens against pre-hashed rotating token records."""

    def __init__(self, records: tuple[TokenRecord, ...], pepper: str) -> None:
        self._records = records
        self._pepper = pepper

    @classmethod
    def from_json(cls, raw: str, pepper: str) -> "TokenAuthenticator":
        if not raw:
            return cls((), pepper)
        decoded = json.loads(raw)
        records = tuple(
            TokenRecord(
                principal_id=item["principal_id"],
                digest=bytes.fromhex(item["sha256"]),
                scopes=frozenset(Scope(scope) for scope in item["scopes"]),
            )
            for item in decoded
        )
        return cls(records, pepper)

    def authenticate(self, token: str) -> Principal | None:
        candidate = hash_token(token, self._pepper)
        match: TokenRecord | None = None
        # Compare every configured digest to avoid exposing its position through timing.
        for record in self._records:
            if hmac.compare_digest(candidate, record.digest):
                match = record
        if match is None:
            return None
        return Principal(match.principal_id, match.scopes)

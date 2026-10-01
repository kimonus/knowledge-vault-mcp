import hashlib
import hmac
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, cast


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
        if not raw.strip():
            return cls((), pepper)
        try:
            decoded: object = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("bootstrap tokens must be valid JSON") from exc
        # `generate_token.py` emits one record object; a single record is accepted as-is.
        items = cast(list[object], decoded) if isinstance(decoded, list) else [decoded]
        records: list[TokenRecord] = []
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("each bootstrap token record must be a JSON object")
            record = cast(dict[str, Any], item)
            try:
                principal_id = record["principal_id"]
                digest = bytes.fromhex(record["sha256"])
                scopes = frozenset(Scope(scope) for scope in record["scopes"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(
                    "a bootstrap token record needs principal_id, a hexadecimal sha256, "
                    "and known scopes"
                ) from exc
            if not isinstance(principal_id, str) or not principal_id.strip():
                raise ValueError("bootstrap token principal_id must be a non-empty string")
            if len(digest) != hashlib.sha256().digest_size:
                raise ValueError("bootstrap token sha256 must be a 32-byte HMAC-SHA256 digest")
            records.append(TokenRecord(principal_id=principal_id, digest=digest, scopes=scopes))
        return cls(tuple(records), pepper)

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

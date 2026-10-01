import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from uuid import UUID

from knowledge_vault.services.errors import KnowledgeVaultError


class InvalidCursorError(KnowledgeVaultError):
    code = "invalid_cursor"


@dataclass(frozen=True, slots=True)
class Cursor:
    query_hash: str
    score: float
    assertion_id: UUID


class CursorCodec:
    def __init__(self, secret: str) -> None:
        self._secret = secret.encode()

    def encode(self, cursor: Cursor) -> str:
        payload = json.dumps(
            {"q": cursor.query_hash, "s": cursor.score, "i": str(cursor.assertion_id)},
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        signature = hmac.new(self._secret, payload, hashlib.sha256).digest()[:16]
        return base64.urlsafe_b64encode(payload + signature).decode().rstrip("=")

    def decode(self, value: str) -> Cursor:
        try:
            padded = value + "=" * (-len(value) % 4)
            decoded = base64.urlsafe_b64decode(padded)
            payload, signature = decoded[:-16], decoded[-16:]
            expected = hmac.new(self._secret, payload, hashlib.sha256).digest()[:16]
            if not hmac.compare_digest(signature, expected):
                raise ValueError("signature mismatch")
            item = json.loads(payload)
            return Cursor(item["q"], float(item["s"]), UUID(item["i"]))
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise InvalidCursorError("cursor is invalid or has been tampered with") from exc

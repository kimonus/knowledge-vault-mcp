"""Versioned, expiring cursors bound to operation, caller, audience and request."""

import base64
import hashlib
import hmac
import json
import re
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, ValidationError

from knowledge_vault.domain.inspection import CursorState, InspectionContext, PageRequest
from knowledge_vault.services.cursors import InvalidCursorError


class InvalidInspectionCursorError(InvalidCursorError):
    status_code = 422


class InspectionCursors:
    def __init__(self, secret: str) -> None:
        self._secret = secret.encode()

    @staticmethod
    def binding(
        operation: str, request: PageRequest, context: InspectionContext, aliases: dict[str, str]
    ) -> str:
        data = {
            "operation": operation,
            "request": request.model_dump(mode="json", exclude={"cursor"}),
            "context": context.model_dump(),
            "aliases": aliases,
        }
        return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()

    def start(self, value: str | None, binding: str) -> CursorState:
        if value is None:
            return CursorState(evaluated_at=datetime.now(UTC))
        try:
            if len(value) > 16000 or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
                raise ValueError("encoding")
            raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
            if base64.urlsafe_b64encode(raw).decode().rstrip("=") != value:
                raise ValueError("noncanonical encoding")
            payload, signature = raw[:-32], raw[-32:]
            expected = hmac.digest(self._secret, payload, "sha256")
            if not hmac.compare_digest(signature, expected):
                raise ValueError("signature")
            envelope = _Envelope.model_validate_json(payload)
            now = datetime.now(UTC)
            if (
                envelope.version != 1
                or envelope.binding != binding
                or envelope.state.evaluated_at.tzinfo is None
                or envelope.state.evaluated_at > now
                or envelope.state.evaluated_at + timedelta(minutes=30) <= now
            ):
                raise ValueError("binding or expiry")
            return envelope.state
        except (ValueError, TypeError, ValidationError) as exc:
            raise InvalidInspectionCursorError(
                "inspection cursor is invalid, expired or belongs elsewhere"
            ) from exc

    def encode(self, binding: str, state: CursorState, key: str) -> str:
        payload = (
            _Envelope(
                version=1,
                binding=binding,
                state=state.model_copy(update={"key": key}),
            )
            .model_dump_json()
            .encode()
        )
        return (
            base64.urlsafe_b64encode(payload + hmac.digest(self._secret, payload, "sha256"))
            .decode()
            .rstrip("=")
        )


class _Envelope(BaseModel):
    version: int
    binding: str
    state: CursorState

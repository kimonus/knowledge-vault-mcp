"""Explicit identity and temporal qualification; no chronology inferred from receipt time."""

import builtins
import hashlib
import json
from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, model_validator

from knowledge_vault.domain.maintenance import SafeModel

Precision = Literal["instant", "day", "month", "year", "unknown"]
Identifier = Annotated[str, Field(min_length=1, max_length=200, pattern=r"^[a-z0-9_.:/-]+$")]
EvidenceKey = Annotated[str, Field(min_length=1, max_length=200)]


class StateIdentity(SafeModel):
    subject: Identifier
    property: Identifier
    scope: Identifier
    state_type: Literal["configuration", "preference", "fact"]

    @builtins.property
    def digest(self) -> str:
        return hashlib.sha256(
            json.dumps(self.model_dump(), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()


class TemporalInput(SafeModel):
    identity: StateIdentity
    evidence_key: EvidenceKey
    value: Annotated[str, Field(min_length=1, max_length=500)]
    mode: Literal["snapshot", "change", "historical", "proposal", "hypothesis", "correction"]
    corrects_key: EvidenceKey | None = None
    observed_precision: Precision = "instant"
    valid_from_precision: Precision = "instant"
    valid_to_precision: Precision = "instant"
    observation_basis: Literal["message", "conversation", "source", "unknown"] = "unknown"

    @model_validator(mode="after")
    def correction_shape(self) -> "TemporalInput":
        if (self.mode == "correction") != (self.corrects_key is not None):
            raise ValueError("only a correction must identify corrects_key")
        if self.corrects_key == self.evidence_key:
            raise ValueError("evidence cannot correct itself")
        if self.value != self.value.strip() or self.evidence_key != self.evidence_key.strip():
            raise ValueError("value and evidence_key must not have surrounding whitespace")
        return self


class ResolutionRequest(SafeModel):
    identity: StateIdentity
    as_of: datetime | None = None
    known_at: datetime | None = None
    history_after: EvidenceKey | None = None
    history_limit: int = Field(default=20, ge=1, le=100)

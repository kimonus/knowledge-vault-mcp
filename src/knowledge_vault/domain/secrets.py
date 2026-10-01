import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SecretPattern:
    label: str
    expression: re.Pattern[str]


_PATTERNS = (
    SecretPattern("private key", re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----")),
    SecretPattern("OpenAI API key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    SecretPattern("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    SecretPattern("AWS access key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    SecretPattern(
        "bearer token", re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{20,}\b", re.IGNORECASE)
    ),
    SecretPattern(
        "password assignment",
        re.compile(r"\b(?:password|passwd|pwd)\s*[:=]\s*[^\s,;]{8,}", re.IGNORECASE),
    ),
    SecretPattern(
        "JWT",
        re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    ),
)


def detect_secret(value: str) -> str | None:
    for pattern in _PATTERNS:
        if pattern.expression.search(value):
            return pattern.label
    return None

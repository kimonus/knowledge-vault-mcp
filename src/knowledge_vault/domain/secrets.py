import re
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SecretPattern:
    label: str
    expression: re.Pattern[str]


_VALUE_CHARS = r"[A-Za-z0-9._~+/=-]"

_PATTERNS = (
    SecretPattern(
        "private key",
        re.compile(r"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY(?: BLOCK)?-----"),
    ),
    SecretPattern("OpenAI API key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    SecretPattern("Stripe API key", re.compile(r"\b[sr]k_(?:live|test)_[A-Za-z0-9]{16,}\b")),
    SecretPattern("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    SecretPattern("GitHub token", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b")),
    SecretPattern("GitLab token", re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}\b")),
    SecretPattern("AWS access key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    SecretPattern("Slack token", re.compile(r"\bxox[abeoprs]-[A-Za-z0-9-]{10,}\b")),
    SecretPattern("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b")),
    SecretPattern("Knowledge Vault device token", re.compile(r"\bkv_[A-Za-z0-9_-]{32,}\b")),
    SecretPattern(
        "bearer token", re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{20,}\b", re.IGNORECASE)
    ),
    SecretPattern(
        "password assignment",
        re.compile(
            r"\b(?:password|passwd|pwd|hasło|haslo|пароль)\s*[:=]\s*[^\s,;]{8,}", re.IGNORECASE
        ),
    ),
    SecretPattern(
        "credential assignment",
        re.compile(
            r"\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|refresh[_-]?token|"
            r"client[_-]?secret|secret[_-]?(?:access[_-]?)?key|aws_secret_access_key|"
            r"private[_-]?key|token|secret|passphrase)\s*[:=]\s*[\"']?"
            rf"(?={_VALUE_CHARS}*\d){_VALUE_CHARS}{{16,}}",
            re.IGNORECASE,
        ),
    ),
    SecretPattern(
        "JWT",
        re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    ),
    # Base64 of a JSON object beginning {"a":" — the shape of a Cloudflare tunnel token.
    SecretPattern("tunnel token", re.compile(r"\beyJhIjoi[A-Za-z0-9+/_-]{40,}")),
    SecretPattern(
        "connection string or URL credentials",
        re.compile(r"\b[a-z][a-z0-9+.-]{1,20}://[^\s/:@]+:[^\s/@]+@", re.IGNORECASE),
    ),
    SecretPattern(
        "URL query credential",
        re.compile(
            r"[?&](?:access_token|id_token|refresh_token|token|api_?key|key|secret|"
            r"client_secret|signature|sig|password|passwd)=[^&\s#]{8,}",
            re.IGNORECASE,
        ),
    ),
    SecretPattern(
        "cookie", re.compile(r"\b(?:set-)?cookie\s*:\s*[^\s=;]+=[^\s;]{8,}", re.IGNORECASE)
    ),
)


def _prepare(value: str) -> str:
    """Fold compatibility forms and drop invisible format characters before matching."""
    folded = unicodedata.normalize("NFKC", value)
    return "".join(character for character in folded if unicodedata.category(character) != "Cf")


def detect_secret(value: str) -> str | None:
    prepared = _prepare(value)
    for pattern in _PATTERNS:
        if pattern.expression.search(prepared):
            return pattern.label
    return None

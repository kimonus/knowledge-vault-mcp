import hashlib
import re
import unicodedata

_WHITESPACE = re.compile(r"\s+")
_TOPIC_INVALID = re.compile(r"[^\w.-]+", re.UNICODE)
_DASHES = re.compile(r"-+")


def normalize_content(value: str) -> str:
    """Normalize Unicode and inconsequential spacing without changing semantics."""
    value = unicodedata.normalize("NFKC", value)
    value = _WHITESPACE.sub(" ", value).strip()
    return value.casefold()


def content_hash(normalized: str) -> str:
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def normalize_topic(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold().strip()
    value = _WHITESPACE.sub("-", value)
    return _DASHES.sub("-", _TOPIC_INVALID.sub("-", value)).strip("-.")

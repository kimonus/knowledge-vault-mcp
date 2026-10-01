import re

_NEGATIONS = re.compile(
    r"\b(?:(?:do|does|did|is|are|was|were)\s+not|not|never|no|nie|nigdy|нет|не|никогда)\b",
    re.IGNORECASE,
)


def polarity_key(normalized_content: str) -> tuple[str, bool]:
    negated = bool(_NEGATIONS.search(normalized_content))
    key = _NEGATIONS.sub("", normalized_content)
    key = " ".join(key.split())
    return key, negated


def looks_contradictory(left: str, right: str) -> bool:
    left_key, left_negated = polarity_key(left)
    right_key, right_negated = polarity_key(right)
    return bool(left_key and left_key == right_key and left_negated != right_negated)

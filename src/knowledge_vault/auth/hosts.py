import unicodedata

# The three Unicode characters that resolvers and browsers also treat as a DNS label separator.
# Folding them to an ASCII dot keeps a visually equivalent hostname from dodging a host rule.
# ideographic (U+3002), fullwidth (U+FF0E), and halfwidth ideographic (U+FF61) full stops
_LABEL_SEPARATORS = str.maketrans(dict.fromkeys((chr(0x3002), chr(0xFF0E), chr(0xFF61)), "."))


def canonical_host(value: str) -> str:
    """Reduce a Host header or configured hostname to a comparable lowercase name.

    Compatibility forms are folded (NFKC) and the alternative IDNA label separators are mapped to
    an ASCII dot so that equivalent spellings of one hostname cannot select different
    authentication rules. The port, surrounding whitespace, and any trailing root-zone dots are
    then removed.
    """
    host = unicodedata.normalize("NFKC", value).translate(_LABEL_SEPARATORS).strip().casefold()
    if host.startswith("["):
        return host.split("]", 1)[0] + "]" if "]" in host else host
    return host.split(":", 1)[0].rstrip(".")

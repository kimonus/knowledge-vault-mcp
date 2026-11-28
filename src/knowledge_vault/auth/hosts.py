import ipaddress
import re
import unicodedata

# The three Unicode characters that resolvers and browsers also treat as a DNS label separator.
# Folding them to an ASCII dot keeps a visually equivalent hostname from dodging a host rule.
# ideographic (U+3002), fullwidth (U+FF0E), and halfwidth ideographic (U+FF61) full stops
_LABEL_SEPARATORS = str.maketrans(dict.fromkeys((chr(0x3002), chr(0xFF0E), chr(0xFF61)), "."))
_DNS_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")


def strict_host(value: str) -> str:
    """Validate an ASCII HTTP authority and return its port-independent hostname."""
    if not value or not value.isascii() or len(value) > 260 or value != value.strip():
        raise ValueError("invalid Host authority")
    host: str
    port: str | None = None
    if value.startswith("["):
        end = value.find("]")
        if end < 0 or "%" in value[1:end]:
            raise ValueError("invalid Host authority")
        try:
            host = f"[{ipaddress.IPv6Address(value[1:end]).compressed}]"
        except ValueError as exc:
            raise ValueError("invalid Host authority") from exc
        suffix = value[end + 1 :]
        if suffix:
            if not suffix.startswith(":"):
                raise ValueError("invalid Host authority")
            port = suffix[1:]
    else:
        if value.count(":") > 1:
            raise ValueError("invalid Host authority")
        host, separator, port_value = value.partition(":")
        if separator:
            port = port_value
        host = host.lower().removesuffix(".")
        if len(host) > 253 or not all(_DNS_LABEL.fullmatch(label) for label in host.split(".")):
            raise ValueError("invalid Host authority")
    if port is not None and (not port.isdecimal() or not 1 <= int(port) <= 65535):
        raise ValueError("invalid Host authority")
    return host


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

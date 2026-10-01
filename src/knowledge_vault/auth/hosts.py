def canonical_host(value: str) -> str:
    """Reduce a Host header or configured hostname to a comparable lowercase name.

    The port, surrounding whitespace, and any trailing root-zone dots are removed so that
    equivalent spellings of one hostname cannot select different authentication rules.
    """
    host = value.strip().casefold()
    if host.startswith("["):
        return host.split("]", 1)[0] + "]" if "]" in host else host
    return host.split(":", 1)[0].rstrip(".")

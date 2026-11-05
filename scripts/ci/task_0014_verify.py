"""Strict offline-testable isolation checks; no networking or write probes."""

import ipaddress
import os
import re


def ipv6_routes(text):
    """Accept only local ::1/128 or explicit lo reject defaults, with no gateway.

    Linux /proc/net/ipv6_route has ten fields. Unknown flags fail closed;
    RTF_REJECT is 0x200, RTF_UP=1, RTF_LOCAL=0x80000000,
    RTF_NONEXTHOP=0x00200000. No source-specific or gateway routes allowed.
    """
    assert text and text.endswith("\n"), "empty/truncated IPv6 inventory"
    outcomes = []
    for line in text.splitlines():
        f = line.split()
        assert len(f) == 10, "unknown IPv6 format"
        for index, width in enumerate((32, 2, 32, 2, 32, 8, 8, 8, 8)):
            assert re.fullmatch(f"[0-9a-fA-F]{{{width}}}", f[index]), "malformed IPv6 field"
        destination, prefix = int(f[0], 16), int(f[1], 16)
        assert prefix <= 128 and int(f[3], 16) <= 128, "invalid prefix"
        # Also enforce canonical destination network bits.
        ipaddress.IPv6Network((destination, prefix), strict=True)
        assert f[9] == "lo", "non-loopback route interface"
        assert int(f[2], 16) == int(f[3], 16) == int(f[4], 16) == 0, "source/gateway route"
        flags = int(f[8], 16)
        assert flags & ~(0x80000000 | 0x00200000 | 0x200 | 1) == 0, "unknown route flags"
        if flags & 0x200:
            assert destination == prefix == 0 and not flags & (0x80000000 | 1), (
                "unknown reject semantics"
            )
            outcomes.append("reject-default")
        else:
            assert destination == 1 and prefix == 128 and flags & 1, "routable IPv6 path"
            outcomes.append("local-loopback")
    return outcomes


def mountinfo(text):
    """Parse mountinfo, including optional fields and Linux octal path escapes."""
    assert text and text.endswith("\n"), "empty/truncated mountinfo"
    rows = []
    for line in text.splitlines():
        f = line.split()
        assert f.count("-") == 1, "invalid mountinfo separator"
        sep = f.index("-")
        assert sep >= 6 and len(f) == sep + 4, "invalid mountinfo fields"
        assert f[0].isdigit() and f[1].isdigit() and re.fullmatch(r"\d+:\d+", f[2])

        def decode(value):
            assert not re.search(r"\\(?!040|011|012|134)", value), "unknown mount escape"
            return re.sub(r"\\(040|011|012|134)", lambda m: chr(int(m[1], 8)), value)

        root, path = decode(f[3]), decode(f[4])
        assert root.startswith("/") and path.startswith("/"), "relative mount path"
        options = set(f[5].split(","))
        assert ("ro" in options) != ("rw" in options), "ambiguous mount mode"
        rows.append((path, options, f[sep + 1], root, f[2]))
    return rows


def mounts(text, resolve=os.path.realpath, statvfs=os.statvfs):
    """Resolve aliases first, then longest covering mount + effective statvfs.

    Ubuntu's /lib -> /usr/lib and /lib64 -> /usr/lib64 may be canonicalized
    by bwrap. An enclosing /usr bind is valid only if the effective filesystem
    is read-only too. Duplicate effective paths/overmount ambiguity fail closed.
    proc/dev/tmp must be distinct exact mounts of their expected filesystem kind.
    """
    rows = mountinfo(text)
    results = []
    for path in ("/usr", "/lib", "/lib64", "/proc", "/dev", "/tmp"):  # noqa: S108  # Validates private mount; no file creation.
        canonical = resolve(path)
        assert canonical.startswith("/") and ".." not in canonical.split("/")
        if path in ("/usr", "/lib", "/lib64"):
            allowed = {
                "/usr": {"/usr"},
                "/lib": {"/lib", "/usr/lib"},
                "/lib64": {"/lib64", "/usr/lib64"},
            }[path]
            assert canonical in allowed, "unexpected system alias"
        else:
            assert canonical == path, "unexpected private mount alias"
        covering = [
            r for r in rows if canonical == r[0] or canonical.startswith(r[0].rstrip("/") + "/")
        ]
        assert covering, "no covering mount"
        longest = max(len(r[0]) for r in covering)
        selected = [r for r in covering if len(r[0]) == longest]
        assert len(selected) == 1, "ambiguous covering mount"
        row = selected[0]
        readonly = bool(statvfs(path).f_flag & os.ST_RDONLY)
        if path in ("/usr", "/lib", "/lib64"):
            assert "ro" in row[1] and readonly, "writable system filesystem"
        else:
            assert (
                row[0] == path
                and row[2] == {"/proc": "proc", "/dev": "tmpfs", "/tmp": "tmpfs"}[path]  # noqa: S108  # Private mount identity only.
            )
            assert row[3] == "/" and "rw" in row[1] and not readonly, "invalid private filesystem"
            assert len({r[4] for r in rows if r[0] in ("/proc", "/dev", "/tmp")}) == 3, (  # noqa: S108  # Private mount identity only.
                "aliased private filesystems"
            )
        results.append(
            {
                "path": path,
                "canonical": canonical,
                "mount": row[0],
                "fstype": row[2],
                "readonly": readonly,
            }
        )
    return results

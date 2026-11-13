"""Probe a synthetic stack from a separate Docker bridge without credentials."""

import socket
import struct
import sys
from pathlib import Path

api, database = sys.argv[1:]
gateway = None
for line in Path("/proc/net/route").read_text().splitlines()[1:]:
    fields = line.split()
    if fields[1] == "00000000":
        gateway = socket.inet_ntoa(struct.pack("<L", int(fields[2], 16)))
        break
if not gateway:
    raise SystemExit("Cannot establish the separate bridge gateway")
for name, address, port in (
    ("loopback-published gateway through host bridge", gateway, 8443),
    ("internal API", api, 8000),
    ("internal database", database, 5432),
):
    try:
        with socket.create_connection((address, port), timeout=3):
            raise SystemExit(f"Unexpected network access to {name}")
    except OSError:
        pass
print("Separate bridge could not reach the host listener, API or database")

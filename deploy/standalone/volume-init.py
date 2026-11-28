"""Provision only a newly allocated Docker credential volume from trusted stdin."""

import json
import os
import sys

FILES = {
    "database-url": 10001,
    "token-pepper": 10001,
    "bootstrap-tokens": 10001,
    "postgres-password": 999,
    "tls-cert.pem": 101,
    "tls-key.pem": 101,
}

os.chdir("/run/kv-secrets")
if os.listdir("."):
    raise SystemExit("Credential volume must be empty")
os.chmod(".", 0o711)  # noqa: S103  # Traverse only; files remain owner-only per service UID.
data = json.load(sys.stdin)
if set(data) != set(FILES):
    raise SystemExit("Unexpected credential files")
for name, uid in FILES.items():
    with open(name, "x", encoding="utf-8") as output:
        os.fchmod(output.fileno(), 0o600)
        output.write(data[name])
        os.fchown(output.fileno(), uid, uid)
        output.flush()
        os.fsync(output.fileno())
descriptor = os.open(".", os.O_RDONLY | os.O_DIRECTORY)
try:
    os.fsync(descriptor)
finally:
    os.close(descriptor)

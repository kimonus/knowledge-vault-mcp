"""Explicit operator configuration. Secrets are read only from mounted files."""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from .auth.hosts import canonical_host
from .auth.tokens import Scope, TokenAuthenticator, TokenRecord, hash_token


def valid_host(value: str) -> bool:
    name, separator, port = value.partition(":")
    return (
        len(value) <= 259
        and all(
            re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
            for label in name.removesuffix(".").split(".")
        )
        and (not separator or (port.isascii() and port.isdigit() and 1 <= int(port) <= 65535))
    )


@dataclass(frozen=True)
class Settings:
    enabled: bool = False
    host: str = ""
    core_url: str = ""
    core_host: str = ""
    service_token: str = field(default="", repr=False)
    mode: str = "privateSession"
    pepper: str = field(default="", repr=False)
    signing_key: str = field(default="", repr=False)
    records: tuple[TokenRecord, ...] = field(default=(), repr=False)
    access_issuer: str = ""
    access_audience: str = ""
    access_emails: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not self.enabled:
            return
        url = urlsplit(self.core_url)
        if (
            not valid_host(self.host)
            or not valid_host(self.core_host)
            or canonical_host(self.host) == canonical_host(self.core_host)
            or url.scheme not in {"http", "https"}
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
            or url.path not in {"", "/"}
            or (url.scheme == "http" and not url.hostname.endswith(".svc.cluster.local"))
            or not re.fullmatch(r"[A-Za-z0-9_-]{32,512}", self.service_token)
        ):
            raise ValueError("invalid dashboard host or fixed core connection configuration")
        if self.mode == "privateSession":
            if (
                len(self.pepper) < 32
                or len(self.signing_key) < 32
                or self.pepper == self.signing_key
                or not self.records
                or self.service_token in {self.pepper, self.signing_key}
                or any(
                    r.scopes != frozenset({Scope.READ})
                    or not r.principal_id
                    or len(r.principal_id) > 128
                    or len(r.digest) != 32
                    or r.digest == hash_token(self.service_token, self.pepper)
                    for r in self.records
                )
            ):
                raise ValueError(
                    "dedicated exact-read browser credentials and separate keys required"
                )
        elif self.mode == "cloudflareAccess":
            issuer = urlsplit(self.access_issuer)
            if (
                issuer.scheme != "https"
                or not issuer.hostname
                or not issuer.hostname.endswith(".cloudflareaccess.com")
                or issuer.path
                or issuer.query
                or issuer.fragment
                or issuer.username
                or issuer.port
                or not self.access_audience
                or not self.access_emails
                or self.records
                or self.pepper
                or self.signing_key
            ):
                raise ValueError("dedicated Access configuration required; no private fallback")
        else:
            raise ValueError("unknown dashboard authentication mode")


def load_settings(path: Path) -> Settings:
    """Load public settings plus Secret file references; never load .env/core settings."""
    data = json.loads(path.read_text())
    for name in ("service_token", "pepper", "signing_key", "records"):
        if name in data:
            raise ValueError("secret values must use file references")
        reference = data.pop(name + "_file", None)
        if reference:
            raw = Path(reference).read_text().strip()
            if name == "records":
                # Use the same reviewed parser as core; Settings checks every record's scope.
                parsed = TokenAuthenticator.from_json(raw, "unused")
                data[name] = parsed.records
            else:
                data[name] = raw
    if "access_emails" in data:
        data["access_emails"] = frozenset(data["access_emails"])
    return Settings(**data)

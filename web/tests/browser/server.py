"""Loopback-only synthetic HTTPS fixture: actual BFF + validating fake core + test Access edge.

Never loaded by application images. No operator configuration, .env or live credentials.
"""

import asyncio
import copy
import json
import secrets
import signal
import ssl
import sys
import tempfile
from collections import Counter, defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import httpx
import jwt
import uvicorn
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.types import ASGIApp, Receive, Scope, Send

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "dashboard/src"))

from knowledge_vault_dashboard.app import create_app  # noqa: E402
from knowledge_vault_dashboard.auth.cloudflare import CloudflareAccessAuthenticator  # noqa: E402
from knowledge_vault_dashboard.auth.tokens import Scope as Authority  # noqa: E402
from knowledge_vault_dashboard.auth.tokens import TokenRecord, hash_token  # noqa: E402
from knowledge_vault_dashboard.config import Settings  # noqa: E402
from knowledge_vault_dashboard.upstream import CONTRACT  # noqa: E402

FIXTURE = json.loads((REPO / "web/tests/fixtures.json").read_text())
CREDENTIAL = "browser-fixture-credential-0000000000000000"
SERVICE = secrets.token_urlsafe(36)
PEPPER = secrets.token_urlsafe(36)
SIGNING = secrets.token_urlsafe(36)
CAPABILITIES = {
    "protocol_version": "1",
    "operations": CONTRACT["operations"],
    "max_page_size": 50,
    "untrusted_data": True,
}
PRIVATE_PORT = 9443
ACCESS_PORT = 9444


def page(items, request, limit=None):
    start = int(request.get("cursor") or 0)
    size = limit or int(request.get("limit", 50))
    selected = items[start : start + size]
    return {
        "items": selected,
        "page_size": int(request.get("limit", 50)),
        "next_cursor": str(start + len(selected)) if start + len(selected) < len(items) else None,
        "evaluated_at": FIXTURE["now"],
        "consistency": "live",
        "untrusted_data": True,
    }


class FakeCore:
    scenario = "normal"

    def __init__(self):
        self.rows = []
        for n in range(205):
            row = copy.deepcopy(FIXTURE["summaries"][n % len(FIXTURE["summaries"])])
            row["id"] = str(UUID(int=n + 1))
            if n >= len(FIXTURE["summaries"]):
                row["excerpt"] = f"Fixture record {n + 1:03d}: {row['excerpt']}"
            self.rows.append(row)

    def filtered(self, body):
        rows = self.rows
        filters = body.get("filters", {})
        for key, field in [
            ("statuses", "status"),
            ("kinds", "kind"),
            ("origins", "origin"),
            ("sensitivities", "sensitivity"),
            ("evidence_states", "evidence_state"),
            ("workflow_states", "workflow_state"),
            ("temporal_roles", "temporal_role"),
        ]:
            if filters.get(key):
                rows = [r for r in rows if r[field] in filters[key]]
        for field in ["context_id", "facet"]:
            if filters.get(field):
                rows = [r for r in rows if r[field] == filters[field]]
        for flag, field in [
            ("missing_topic", "topics"),
            ("missing_context", "context_id"),
            ("missing_facet", "facet"),
        ]:
            if filters.get(flag):
                rows = [r for r in rows if not r[field]]
        if filters.get("topics"):
            topics = {"backups" if t == "backup" else t for t in filters["topics"]}
            rows = [r for r in rows if topics.intersection(r["topics"])]
        if body.get("query"):
            query = body["query"].lower().replace('"', "")
            rows = [r for r in rows if query in r["excerpt"].lower()]
        return rows

    async def __call__(self, request):
        assert request.headers["authorization"] == "Bearer " + SERVICE
        assert request.headers["host"] == "core-private.test"
        assert not {"cookie", "origin", "cf-access-jwt-assertion", "x-forwarded-for"} & set(
            request.headers
        )
        path = request.url.path.removeprefix("/api/v1/read/")
        body = json.loads(request.content) if request.content else {}
        query = dict(request.url.params)
        if path == "capabilities":
            return httpx.Response(200, json=CAPABILITIES)
        if self.scenario == "degraded":
            return httpx.Response(503, json={})
        if self.scenario == "rate":
            return httpx.Response(429, headers={"Retry-After": "30"}, json={})
        if path in {"assertions/query", "search"}:
            if "slow" in body.get("query", ""):
                await asyncio.sleep(0.3)
            rows = [] if self.scenario == "empty" else self.filtered(body)
            if path == "search":
                result = page([{"assertion": r, "score": 0.5} for r in rows[:2]], body)
                result.update(candidate_limited=True, embedding_degraded=True)
            else:
                result = page(rows, body)
        elif path == "catalog/query":
            groups = defaultdict(list)
            dimension = body["dimension"]
            for row in self.filtered(body):
                keys = (
                    (row["topics"] or [None])
                    if dimension == "topics"
                    else [row["context_id"] if dimension == "contexts" else row["facet"]]
                )
                for key in keys:
                    groups[key].append(row)
            items = []
            for key, rows in sorted(groups.items(), key=lambda pair: pair[0] or ""):
                item = {
                    "key": key,
                    "label": "Uncategorized"
                    if key is None
                    else FIXTURE["context"]["name"]
                    if dimension == "contexts"
                    else key,
                    "stored_labels": [key] if key else [],
                    "assertion_count": len(rows),
                }
                for target, field in [
                    ("by_status", "status"),
                    ("by_kind", "kind"),
                    ("by_evidence_state", "evidence_state"),
                    ("by_workflow_state", "workflow_state"),
                ]:
                    item[target] = dict(Counter(row[field] or "unspecified" for row in rows))
                if item["label"].startswith(body.get("prefix") or ""):
                    items.append(item)
            result = {**page(items, body, 2), "grouping_version": "1"}
        elif path == "topic-aliases":
            result = {"aliases": {"backup": "backups"}, "untrusted_data": True}
        elif path.startswith("contexts/"):
            result = FIXTURE["context"]
        elif path.startswith("assertions/"):
            parts = path.split("/")
            identifier = parts[1]
            if len(parts) == 2:
                index = int(UUID(identifier)) - 1
                if index >= len(self.rows):
                    return httpx.Response(404, json={})
                result = {
                    **copy.deepcopy(FIXTURE["details"][index % len(FIXTURE["details"])]),
                    "id": identifier,
                    "content": self.rows[index]["excerpt"],
                }
            else:
                collection = parts[2]
                if collection in {"sources", "artifacts", "identities"}:
                    if query.get("limit") != "20":
                        return httpx.Response(422, json={})
                    result = page(FIXTURE[collection] if int(UUID(identifier)) == 1 else [], query)
                else:
                    relations = []
                    for n in range(103):
                        relations.append(
                            {
                                "type": "link",
                                "edge": {
                                    "source_id": identifier,
                                    "target_id": str(UUID(int=n + 2)),
                                    "kind": "depends_on",
                                    "position": None,
                                },
                            }
                        )
                    relations[0] = {
                        "type": "supersession",
                        "predecessor_id": str(UUID(int=2)),
                        "successor_id": identifier,
                    }
                    relations[1] = {
                        "type": "conflict",
                        "conflict": {
                            "id": str(UUID(int=700)),
                            "left_assertion_id": identifier,
                            "right_assertion_id": str(UUID(int=6)),
                            "reason": "Synthetic competing evidence",
                            "created_at": FIXTURE["now"],
                            "resolved_at": None,
                        },
                    }
                    result = page(relations, query)
        elif path.startswith("artifacts/"):
            offset = int(query.get("offset", 0))
            text = FIXTURE["artifact"]["content"] + "\n" + "Synthetic artifact evidence.\n" * 700
            content = text[offset : offset + 8000]
            result = {
                **FIXTURE["artifact"],
                "artifact_id": path.split("/")[1],
                "offset": offset,
                "total_chars": len(text),
                "content": content,
                "next_offset": offset + len(content) if offset + len(content) < len(text) else None,
            }
        elif path == "state/resolve":
            if body["identity"]["subject"] == "overflow":
                return httpx.Response(422, json={})
            result = copy.deepcopy(FIXTURE["resolution"])
            result["identity"] = body["identity"]
            result["as_of"], result["known_at"] = body.get("as_of"), body.get("known_at")
            subject = body["identity"]["subject"]
            result["outcome"] = (
                subject if subject in {"conflict", "unknown", "ambiguous"} else "resolved"
            )
            if result["outcome"] == "unknown":
                result["values"] = []
            if body.get("history_after"):
                result["history"][0]["evidence_key"] = "backup-001"
                result["history"][0]["disposition"] = "historical"
                result["next_history_after"] = None
        elif path == "state/evidence/query":
            count = 1003 if body["identity"]["subject"] == "overflow" else 23
            evidence = [
                {**FIXTURE["evidence"], "evidence_key": f"backup-{n:04d}", "retired": n % 3 == 0}
                for n in range(count)
            ]
            result = page(evidence, body)
        else:
            return httpx.Response(404, json={})
        return httpx.Response(200, json=result)


class Keys:
    def __init__(self):
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    async def get_signing_key(self, assertion):
        return self.key.public_key()


class Fixture:
    def __init__(self, access=False, *, transport=None, port=None):
        self.access = access
        self.fixed_transport = transport
        self.port = port or (ACCESS_PORT if access else PRIVATE_PORT)
        self.keys = Keys()
        self.reset()

    def reset(self):
        self.core = FakeCore()
        fields = {
            "enabled": True,
            "host": f"localhost:{self.port}",
            "core_host": "core-private.test",
            "core_url": "http://core.default.svc.cluster.local",
            "service_token": SERVICE,
        }
        verifier = None
        if self.access:
            fields.update(
                mode="cloudflareAccess",
                access_issuer="https://fixture.cloudflareaccess.com",
                access_audience="dashboard-fixture",
                access_emails=frozenset({"fixture@example.test"}),
            )
            verifier = CloudflareAccessAuthenticator(
                issuer_url=fields["access_issuer"],
                audience=fields["access_audience"],
                allowed_emails=fields["access_emails"],
                scopes=frozenset({Authority.READ}),
                signing_keys=self.keys,
            )
            now = int(datetime.now(UTC).timestamp())
            self.assertion = jwt.encode(
                {
                    "iss": "https://fixture.cloudflareaccess.com",
                    "aud": "dashboard-fixture",
                    "email": "fixture@example.test",
                    "sub": "fixture",
                    "iat": now,
                    "exp": now + 300,
                },
                self.keys.key,
                algorithm="RS256",
                headers={"kid": "fixture"},
            )
        else:
            fields.update(
                pepper=PEPPER,
                signing_key=SIGNING,
                records=(
                    TokenRecord(
                        "browser-fixture",
                        hash_token(CREDENTIAL, PEPPER),
                        frozenset({Authority.READ}),
                    ),
                ),
            )
        self.app: ASGIApp = create_app(
            Settings(**fields),
            transport=self.fixed_transport or httpx.MockTransport(self.core),
            access=verifier,
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope["path"]
        if path == "/__test__/reset":
            self.reset()
            await JSONResponse({"reset": True})(scope, receive, send)
            return
        if path.startswith("/__test__/scenario/"):
            self.core.scenario = path.rsplit("/", 1)[-1]
            await JSONResponse({"scenario": self.core.scenario})(scope, receive, send)
            return
        if self.access:
            cookies = b";".join(
                value for key, value in scope["headers"] if key == b"cookie"
            ).decode()
            if path == "/__test__/access":
                if scope["method"] == "POST":
                    response = RedirectResponse("/dashboard/", status_code=303)
                    response.set_cookie(
                        "CF_Authorization",
                        "synthetic-grant",
                        secure=True,
                        httponly=True,
                        samesite="lax",
                    )
                else:
                    response = HTMLResponse(
                        '<!doctype html><html lang="en"><title>Test Access provider</title>'
                        '<body><h1>Test Access provider</h1><form method="post">'
                        "<button>Authorize test session</button></form></body></html>"
                    )
                await response(scope, receive, send)
                return
            if "CF_Authorization=synthetic-grant" not in cookies:
                ajax = (b"x-requested-with", b"XMLHttpRequest") in scope["headers"]
                response = (
                    HTMLResponse("<h1>Untrusted edge denial</h1>", status_code=401)
                    if ajax
                    else RedirectResponse("/__test__/access")
                )
                await response(scope, receive, send)
                return
            if "edge_redirect=yes" in cookies and path.startswith("/dashboard/api/"):
                await RedirectResponse("/__test__/access")(scope, receive, send)
                return
            scope = {
                **scope,
                "headers": [
                    (key, value)
                    for key, value in scope["headers"]
                    if key != b"cf-access-jwt-assertion"
                ]
                + [(b"cf-access-jwt-assertion", self.assertion.encode())],
            }
        await self.app(scope, receive, send)


def certificate(directory):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = Path(directory) / "cert.pem", Path(directory) / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    key_path.chmod(0o600)
    return str(cert_path), str(key_path)


class TestServer(uvicorn.Server):
    @contextmanager
    def capture_signals(self) -> Iterator[None]:
        yield


async def main():
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for event in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(event, stop.set)
    with tempfile.TemporaryDirectory(prefix="kv-browser-tls-") as directory:
        cert, key = certificate(directory)
        servers = [
            TestServer(
                uvicorn.Config(
                    Fixture(access),
                    host="127.0.0.1",
                    port=port,
                    access_log=False,
                    log_level="warning",
                    proxy_headers=False,
                    ssl_certfile=cert,
                    ssl_keyfile=key,
                    ssl_version=ssl.PROTOCOL_TLS_SERVER,
                )
            )
            for access, port in [(False, PRIVATE_PORT), (True, ACCESS_PORT)]
        ]
        tasks = [asyncio.create_task(server.serve()) for server in servers]
        await stop.wait()
        for server in servers:
            server.should_exit = True
        await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())

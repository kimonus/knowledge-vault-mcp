#!/usr/bin/env python3
"""Synthetic-only optional-dashboard lifecycle on an explicitly disposable Minikube profile.

No live credentials, data, TLS, DNS, Access or tunnel inputs are accepted. Optional
ingress/browser rehearsal uses an ephemeral fixture certificate, never real Access.
"""

import argparse
import base64
import hashlib
import hmac
import json
import re
import secrets
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

NAME = "kv-dashboard-pr4"


def rendered_core(manifest: str) -> list[str]:
    # Helm starts its stream with '---'. Adding an earlier-sorted Dashboard resource
    # moves that separator away from the first core object; it is not a core change.
    documents = re.split(r"(?:\A|\n)---(?:\n|\Z)", manifest)
    return sorted(
        part.strip()
        for part in documents
        if "# Source:" in part
        and not re.search(r"^# Source: [^\n]+/templates/dashboard", part, re.MULTILINE)
    )


def run(args: list[str], data: str | None = None, timeout: int = 360) -> str:
    result = subprocess.run(  # noqa: S603  # explicit operator CLI, argv only; no shell
        args, input=data, text=True, capture_output=True, check=False, timeout=timeout
    )
    if result.returncode:
        # Never echo captured payloads, arguments, logs or runtime error text.
        if args[0] == "node":
            stage = re.search(
                r"^DASHBOARD_CHECK_FAILED:(input|tls-home|tls-isolation|browser-launch|"
                r"browser-login|cookie|detail|portal|requests|logout)$",
                result.stderr,
                re.MULTILINE,
            )
            if stage:
                raise RuntimeError(f"browser verification failed at {stage[1]}")
        raise RuntimeError(f"{args[0]} failed with exit code {result.returncode}")
    return result.stdout


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", required=True)
    parser.add_argument("--dashboard-image", required=True)
    parser.add_argument("--core-image", default="knowledge-vault:pr4-review")
    parser.add_argument("--backup-image", default="knowledge-vault-backup:pr4-review")
    parser.add_argument("--verify-ingress", action="store_true")
    parser.add_argument("--browser-script", type=Path)
    args = parser.parse_args()
    if args.verify_ingress != bool(args.browser_script):
        parser.error("--verify-ingress requires --browser-script and vice versa")
    if args.browser_script and (
        not args.browser_script.is_absolute() or not args.browser_script.is_file()
    ):
        parser.error("browser callback must be an existing absolute local script path")
    if args.context != NAME:
        parser.error(f"only explicit disposable context {NAME} is permitted")
    if not re.fullmatch(r"[A-Za-z0-9./:_-]+@sha256:[a-f0-9]{64}", args.dashboard_image):
        parser.error("dashboard image must have a locally loaded immutable manifest digest")
    context = json.loads(run(["kubectl", "config", "view", "-o", "json"]))
    chosen = [c for c in context["contexts"] if c["name"] == NAME]
    if len(chosen) != 1 or chosen[0]["context"]["cluster"] != NAME:
        parser.error("context must name the dedicated disposable Minikube cluster")
    k = ["kubectl", "--context", NAME, "--namespace", NAME]
    h = ["helm", "--kube-context", NAME, "--namespace", NAME]

    def apply(obj: dict[str, Any]) -> None:
        body = json.dumps(obj)
        run([*k, "apply", "--dry-run=server", "-f", "-"], body)
        run([*k, "apply", "-f", "-"], body)

    # Refuse an existing namespace: this harness owns exactly one new disposable fixture.
    namespaces = json.loads(run([*k, "get", "namespaces", "-o", "json"]))
    if any(n["metadata"]["name"] == NAME for n in namespaces["items"]):
        parser.error("fixture namespace already exists; inspect/clean the disposable profile first")
    apply({"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": NAME}})

    def secret(name: str, data: dict[str, str]) -> None:
        apply(
            {
                "apiVersion": "v1",
                "kind": "Secret",
                "metadata": {"name": name},
                "type": "Opaque",
                "stringData": data,
            }
        )

    def record(principal: str, token: str, pepper: str, scopes: list[str]) -> dict[str, Any]:
        return {
            "principal_id": principal,
            "sha256": hmac.new(pepper.encode(), token.encode(), hashlib.sha256).hexdigest(),
            "scopes": scopes,
        }

    core_pepper, browser_pepper, signing = (secrets.token_urlsafe(40) for _ in range(3))
    service, browser, writer, password = (secrets.token_urlsafe(40) for _ in range(4))
    secret(
        "lab-auth",
        {
            "token-pepper": core_pepper,
            "bootstrap-tokens": json.dumps(
                [
                    record("lab-inspection", service, core_pepper, ["knowledge:read"]),
                    record(
                        "lab-writer", writer, core_pepper, ["knowledge:read", "knowledge:write"]
                    ),
                ]
            ),
        },
    )
    secret("lab-postgres", {"username": "lab", "password": password, "database": "lab"})
    secret(
        "lab-database",
        {"database-url": f"postgresql+psycopg://lab:{password}@{NAME}-postgresql:5432/lab"},
    )
    secret("lab-service", {"service-token": service})
    secret(
        "lab-browser",
        {
            "pepper": browser_pepper,
            "records": json.dumps(
                [record("lab-browser", browser, browser_pepper, ["knowledge:read"])]
            ),
        },
    )
    secret("lab-signing", {"signing-key": signing})
    invalid_service = secrets.token_urlsafe(40)
    secret("lab-invalid-service", {"service-token": invalid_service})
    core_repository, core_tag = args.core_image.rsplit(":", 1)
    backup_repository, backup_tag = args.backup_image.rsplit(":", 1)
    dashboard_repository, dashboard_digest = args.dashboard_image.rsplit("@", 1)
    values: dict[str, Any] = {
        "fullnameOverride": NAME,
        "image": {"repository": core_repository, "tag": core_tag, "pullPolicy": "Never"},
        "backupImage": {"repository": backup_repository, "tag": backup_tag},
        "auth": {"existingSecret": "lab-auth"},
        "database": {"existingSecret": "lab-database"},
        "internalPostgresql": {"enabled": True, "existingSecret": "lab-postgres", "storage": "1Gi"},
        "config": {
            "embeddingsEnabled": False,
            "publicBaseUrl": "https://core.example.test",
            "authIssuerUrl": "https://core.example.test",
        },
        "extraEnv": {"KNOWLEDGE_VAULT_INSPECTION_PRINCIPALS": "lab-inspection"},
        "networkPolicy": {"apiIngressFrom": [{"podSelector": {"matchLabels": {"lab": "probe"}}}]},
        "dashboard": {
            "enabled": False,
            "host": "dashboard.example.test",
            "coreHost": "core.example.test",
            "image": {
                "repository": dashboard_repository,
                "digest": dashboard_digest,
                "pullPolicy": "Never",
            },
            "serviceTokenSecret": {"name": "lab-service"},
            "privateSession": {"existingSecret": "lab-browser", "signingSecret": "lab-signing"},
            "networkPolicy": {"ingressFrom": [{"podSelector": {"matchLabels": {"lab": "probe"}}}]},
        },
    }
    with tempfile.TemporaryDirectory(prefix="kv-dashboard-pr4-") as directory:
        value_file = Path(directory) / "values.json"
        browser_input: dict[str, str] | None = None
        if args.verify_ingress:
            key = Path(directory) / "lab.key"
            certificate = Path(directory) / "lab.crt"
            run(
                [
                    "openssl",
                    "req",
                    "-x509",
                    "-newkey",
                    "rsa:2048",
                    "-nodes",
                    "-days",
                    "1",
                    "-keyout",
                    str(key),
                    "-out",
                    str(certificate),
                    "-subj",
                    "/CN=dashboard.example.test",
                    "-addext",
                    "subjectAltName=DNS:dashboard.example.test",
                ]
            )
            key.chmod(0o600)
            apply(
                {
                    "apiVersion": "v1",
                    "kind": "Secret",
                    "metadata": {"name": "lab-tls"},
                    "type": "kubernetes.io/tls",
                    "stringData": {"tls.key": key.read_text(), "tls.crt": certificate.read_text()},
                }
            )
            public_key = run(["openssl", "x509", "-in", str(certificate), "-pubkey", "-noout"])
            public_der = subprocess.run(
                ["openssl", "pkey", "-pubin", "-outform", "DER"],  # noqa: S607  # fixed public-key tool
                input=public_key.encode(),
                capture_output=True,
                timeout=10,
                check=True,
            ).stdout
            browser_input = {
                "url": "https://dashboard.example.test/dashboard/",
                "host": "dashboard.example.test",
                "ingress_address": run(["minikube", "-p", NAME, "ip"]).strip(),
                "browser_credential": browser,
                "ca_certificate": certificate.read_text(),
                "tls_spki_sha256": base64.b64encode(hashlib.sha256(public_der).digest()).decode(),
            }
            values["dashboard"]["ingress"] = {
                "enabled": True,
                "className": "nginx",
                "tlsSecret": "lab-tls",
            }
            values["dashboard"]["networkPolicy"]["ingressFrom"].append(
                {
                    "namespaceSelector": {
                        "matchLabels": {"kubernetes.io/metadata.name": "ingress-nginx"}
                    },
                    "podSelector": {"matchLabels": {"app.kubernetes.io/component": "controller"}},
                }
            )

        def write_values() -> None:
            value_file.write_text(json.dumps(values))

        def render() -> str:
            write_values()
            return run([*h, "template", NAME, "charts/knowledge-vault", "-f", str(value_file)])

        def upgrade() -> None:
            write_values()
            manifest = render()
            assert rendered_core(manifest) == baseline_manifest, "core render changed"
            # Real server admission is a separate precondition to each mutation.
            run([*k, "apply", "--dry-run=server", "-f", "-"], manifest)
            run(
                [
                    *h,
                    "upgrade",
                    NAME,
                    "charts/knowledge-vault",
                    "-f",
                    str(value_file),
                    "--no-hooks",
                    "--wait",
                    "--timeout",
                    "5m",
                ]
            )

        def stable_core() -> list[tuple[str, str, tuple[int, ...]]]:
            pods = json.loads(run([*k, "get", "pods", "-o", "json"]))["items"]
            result = []
            for pod in pods:
                component = pod["metadata"].get("labels", {}).get("app.kubernetes.io/component")
                if component in {"api", "worker", "postgresql", "cloudflared"}:
                    result.append(
                        (
                            component,
                            pod["metadata"]["uid"],
                            tuple(
                                c["restartCount"]
                                for c in pod["status"].get("containerStatuses", [])
                            ),
                        )
                    )
            return sorted(result)

        baseline_manifest = rendered_core(render())
        assert "component: dashboard" not in render(), "disabled Dashboard resource"
        write_values()
        # post-install migration must run before waiting for core init containers.
        run(
            [
                *h,
                "install",
                NAME,
                "charts/knowledge-vault",
                "-f",
                str(value_file),
                "--timeout",
                "5m",
            ]
        )
        for resource in (
            f"statefulset/{NAME}-postgresql",
            f"deployment/{NAME}-api",
            f"deployment/{NAME}-worker",
        ):
            run([*k, "rollout", "status", resource, "--timeout=300s"])
        baseline_pods = stable_core()
        assert {p[0] for p in baseline_pods} == {"api", "worker", "postgresql"}
        apply(
            {
                "apiVersion": "v1",
                "kind": "Pod",
                "metadata": {"name": "lab-probe", "labels": {"lab": "probe"}},
                "spec": {
                    "automountServiceAccountToken": False,
                    "restartPolicy": "Never",
                    "securityContext": {
                        "runAsNonRoot": True,
                        "runAsUser": 10001,
                        "seccompProfile": {"type": "RuntimeDefault"},
                    },
                    "containers": [
                        {
                            "name": "probe",
                            "image": args.core_image,
                            "imagePullPolicy": "Never",
                            "command": ["python", "-c", "import time;time.sleep(3600)"],
                            "securityContext": {
                                "allowPrivilegeEscalation": False,
                                "readOnlyRootFilesystem": True,
                                "capabilities": {"drop": ["ALL"]},
                            },
                            "resources": {
                                "requests": {"cpu": "10m", "memory": "32Mi"},
                                "limits": {"cpu": "100m", "memory": "128Mi"},
                            },
                        }
                    ],
                },
            }
        )
        run([*k, "wait", "--for=condition=Ready", "pod/lab-probe", "--timeout=120s"])
        values["dashboard"]["enabled"] = True
        upgrade()
        auth_probe(k, browser, service, writer)
        if browser_input is not None:
            run(["node", str(args.browser_script)], json.dumps(browser_input), timeout=180)
            print("PASS: synthetic TLS ingress/browser callback")
        verify_logs(k, [core_pepper, browser_pepper, signing, service, browser, writer, password])
        missing_secret_probe(k, apply)
        invalid_service_probe(k, apply, browser, invalid_service)
        assert stable_core() == baseline_pods, "enablement restarted core"
        # Independent BFF-only rollout, then rollback to its preceding secret revision.
        values["dashboard"]["secretRevision"] = "2"
        upgrade()
        auth_probe(k, browser, service, writer)
        assert stable_core() == baseline_pods, "BFF upgrade restarted core"
        run([*h, "rollback", NAME, "2", "--no-hooks", "--wait", "--timeout", "5m"])
        auth_probe(k, browser, service, writer)
        assert stable_core() == baseline_pods, "BFF rollback restarted core"
        # Restart BFF only, verify recovery and policy from its actual network namespace.
        run([*k, "rollout", "restart", f"deployment/{NAME}-dashboard"])
        run([*k, "rollout", "status", f"deployment/{NAME}-dashboard", "--timeout=120s"])
        auth_probe(k, browser, service, writer)
        network_probe(k)
        verify_logs(k, [core_pepper, browser_pepper, signing, service, browser, writer, password])
        assert stable_core() == baseline_pods, "BFF recovery restarted core"
        values["dashboard"]["enabled"] = False
        upgrade()
        remaining = json.loads(
            run(
                [
                    *k,
                    "get",
                    "deployment,service,serviceaccount,configmap,networkpolicy,ingress",
                    "-l",
                    "app.kubernetes.io/component=dashboard",
                    "-o",
                    "json",
                ]
            )
        )
        assert not remaining["items"], "Dashboard resource survived removal"
        assert stable_core() == baseline_pods, "removal restarted core"
        auth_probe(k, browser, service, writer, dashboard=False)
        for name in ("lab-service", "lab-browser", "lab-signing"):
            run([*k, "get", "secret", name, "-o", "name"])
        if browser_input is not None:
            run([*k, "get", "secret", "lab-tls", "-o", "name"])
        print(
            "PASS: default-off, enable, BFF rollout/rollback/recovery, authenticated fixed reads,"
        )
        print("service confinement, egress/DB isolation, removal, unchanged core Pods/Secrets.")
        if browser_input is None:
            print("NOT VERIFIED: external TLS ingress/browser (optional callback not supplied).")
        print("NOT VERIFIED: real Access, signed publication, restored-core UI.")
        print(
            f"Fixture retained in disposable context/namespace {NAME}; remove profile after review."
        )


def verify_logs(k: list[str], sensitive_values: list[str]) -> None:
    """Inspect logs privately and expose only a content-free verdict and restart counts."""
    pods = json.loads(run([*k, "get", "pods", "-o", "json"]))["items"]
    for pod in pods:
        component = pod["metadata"].get("labels", {}).get("app.kubernetes.io/component")
        if component not in {"api", "dashboard"} or pod["metadata"]["name"] == "lab-missing-secret":
            continue
        for container in pod["status"].get("containerStatuses", []):
            logs = run([*k, "logs", pod["metadata"]["name"], "-c", container["name"]])
            forbidden = [*sensitive_values, "Disposable dashboard lifecycle synthetic assertion."]
            assert not any(value in logs for value in forbidden), "sensitive data in runtime logs"
            assert container["ready"], "client verification left an unready application container"
            if component == "dashboard":
                assert container["restartCount"] == 0, "unexpected Dashboard container restart"
            print(f"PASS: safe {component} logs inspected; restarts={container['restartCount']}")


def auth_probe(
    k: list[str], browser: str, service: str, writer: str, dashboard: bool = True
) -> None:
    code = r"""
import json, sys, urllib.request, urllib.error, uuid
p = json.load(sys.stdin)
core = "http://kv-dashboard-pr4:8000"
bff = "http://kv-dashboard-pr4-dashboard:8080"
def call(base, path, expected, body=None, method=None, headers=None):
    request = urllib.request.Request(base+path,
        data=None if body is None else json.dumps(body).encode(), method=method,
        headers={"Content-Type":"application/json", **(headers or {})})
    try:
        response = urllib.request.urlopen(request, timeout=10)
    except urllib.error.HTTPError as error:
        response = error
    assert response.status == expected, (path, response.status, expected)
    raw = response.read(2000001)
    assert len(raw) <= 2000000
    return (json.loads(raw) if raw else None), response.headers
service = {"Host":"core.example.test", "Authorization":"Bearer "+p["service"]}
writer = {"Host":"core.example.test", "Authorization":"Bearer "+p["writer"]}
call(core, "/api/v1/read/capabilities", 200, headers=service)
call(core, "/api/v1/statistics", 403, headers=service)
call(core, "/api/v1/flushes", 403, {}, headers=service)
call(core, "/api/v1/statistics", 200, headers=writer)
batch, _ = call(core, "/api/v1/flushes", 201, {"idempotency_key":str(uuid.uuid4()),
    "declared_parts":1, "declared_items":1}, headers=writer)
batch_id = batch["batch_id"]
call(core, "/api/v1/flushes/"+batch_id+"/parts/1", 200, {"assertions":[{
    "content":"Disposable dashboard lifecycle synthetic assertion.",
    "kind":"external_fact", "origin":"external_source", "topics":["pr4-lab"]}]},
    method="PUT", headers=writer)
call(core, "/api/v1/flushes/"+batch_id+"/commit", 200, {}, headers=writer)
# Use the official client through the deployed MCP mount, not only its HTTP adapter.
import asyncio, httpx2
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
async def check_mcp():
    async with httpx2.AsyncClient(headers=writer,timeout=30) as http:
        async with streamable_http_client(core+"/mcp",http_client=http) as streams:
            async with ClientSession(streams[0],streams[1]) as session:
                await session.initialize()
                async def tool(name, arguments):
                    result=await session.call_tool(name,arguments)
                    assert not result.is_error, "MCP operation failed: "+name
                    assert result.structured_content is not None
                    return result.structured_content
                batch=await tool("begin_knowledge_flush",{
                    "idempotency_key":str(uuid.uuid4()),"declared_parts":1,"declared_items":1})
                content=("Disposable dashboard lifecycle synthetic assertion. MCP probe "
                    +str(uuid.uuid4()))
                await tool("append_knowledge",{"batch_id":batch["batch_id"],"part_number":1,
                    "assertions":[{"content":content,"kind":"external_fact",
                    "origin":"external_source","topics":["pr4-lab"]}]})
                committed=await tool("commit_knowledge_flush",{"batch_id":batch["batch_id"]})
                got=await tool("get_knowledge",{"assertion_id":committed["assertion_ids"][0]})
                assert got["content"] == content
asyncio.run(check_mcp())
if p["dashboard"]:
    headers={"Host":"dashboard.example.test", "Origin":"https://dashboard.example.test",
        "X-Dashboard-Request":"1", "X-Requested-With":"XMLHttpRequest"}
    _, response = call(bff, "/dashboard/api/v1/session/login", 204,
        {"credential":p["browser"]}, headers=headers)
    cookie = response["Set-Cookie"]
    for flag in ("Secure", "HttpOnly", "SameSite=strict", "Path=/"):
        assert flag.lower() in cookie.lower()
    headers["Cookie"] = cookie.split(";",1)[0]
    session, _ = call(bff, "/dashboard/api/v1/session", 200, headers=headers)
    headers["X-CSRF-Token"] = session["csrf_token"]
    data, _ = call(bff, "/dashboard/api/v1/assertions/query", 200, {}, headers=headers)
    assert data["items"], "fixture write was not visible through actual BFF/core"
    call(bff, "/dashboard/api/v1/catalog/query", 200, {"dimension":"topics"}, headers=headers)
    call(bff, "/api/v1/flushes", 404, {}, headers=headers)
    call(bff, "/dashboard/api/v1/assertions/query", 403, {},
        headers={**headers,"Origin":"https://sibling.example.test"})
    call(bff, "/dashboard/api/v1/session/logout", 204, {}, headers=headers)
print("PASS: actual ClusterIP HTTP/MCP reads/writes and confinement (not browser/TLS evidence)")
"""
    run(
        [*k, "exec", "-i", "lab-probe", "--", "python", "-c", code],
        json.dumps(
            {"browser": browser, "service": service, "writer": writer, "dashboard": dashboard}
        ),
        timeout=120,
    )
    print("PASS: deployed core HTTP/MCP reads and writes; inspection/BFF authority confined")


def network_probe(k: list[str]) -> None:
    code = r"""
import socket
def connect(host, port, expected):
    try:
        with socket.create_connection((host,port),timeout=3): pass
        success=True
    except (OSError, TimeoutError): success=False
    assert success is expected, (host,port,expected)
connect("kv-dashboard-pr4",8000,True)
connect("kv-dashboard-pr4-postgresql",5432,False)
connect("1.1.1.1",443,False)
print("PASS: Dashboard egress core positive control, DB and external denial")
"""
    # A positive DB control distinguishes policy denial from an unavailable fixture DB.
    run(
        [
            *k,
            "exec",
            f"deployment/{NAME}-api",
            "--",
            "python",
            "-c",
            "import socket; "
            "socket.create_connection(('kv-dashboard-pr4-postgresql',5432),3).close()",
        ],
        timeout=15,
    )
    run([*k, "exec", f"deployment/{NAME}-dashboard", "--", "python", "-c", code], timeout=30)
    # An unmatched peer must not reach dashboard, while DNS still works.
    denied = code.replace(
        'connect("kv-dashboard-pr4",8000,True)', 'connect("kv-dashboard-pr4-dashboard",8080,False)'
    )
    denied = denied.replace('connect("1.1.1.1",443,False)', "")
    run([*k, "label", "pod/lab-probe", "lab=denied", "--overwrite"])
    try:
        run(
            [*k, "exec", "lab-probe", "--", "python", "-c", "import time;time.sleep(2)\n" + denied],
            timeout=30,
        )
    finally:
        run([*k, "label", "pod/lab-probe", "lab=probe", "--overwrite"])


def canary_metadata(deployment: dict[str, Any], name: str) -> dict[str, Any]:
    # A matching orphan Pod can be adopted/deleted by the real ReplicaSet. Retain the
    # workload's policy labels but give this test Pod an explicit live Deployment owner;
    # ReplicaSets must not claim a Pod controlled by a different owner UID.
    return {
        "name": name,
        "labels": deployment["spec"]["template"]["metadata"]["labels"],
        "ownerReferences": [
            {
                "apiVersion": "apps/v1",
                "kind": "Deployment",
                "name": deployment["metadata"]["name"],
                "uid": deployment["metadata"]["uid"],
                "controller": True,
                "blockOwnerDeletion": False,
            }
        ],
    }


def missing_secret_probe(k: list[str], apply: Any) -> None:
    deployment = json.loads(run([*k, "get", "deployment", f"{NAME}-dashboard", "-o", "json"]))
    template = deployment["spec"]["template"]
    for volume in template["spec"]["volumes"]:
        if volume["name"] == "service-token":
            volume["secret"]["secretName"] = "lab-deliberately-absent"
    template["spec"]["restartPolicy"] = "Never"
    apply(
        {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": canary_metadata(deployment, "lab-missing-secret"),
            "spec": template["spec"],
        }
    )
    try:
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            events = json.loads(
                run(
                    [
                        *k,
                        "get",
                        "events",
                        "--field-selector",
                        "involvedObject.name=lab-missing-secret",
                        "-o",
                        "json",
                    ]
                )
            )
            if any(e.get("reason") == "FailedMount" for e in events["items"]):
                pod = json.loads(run([*k, "get", "pod", "lab-missing-secret", "-o", "json"]))
                assert not any(
                    c.get("type") == "Ready" and c.get("status") == "True"
                    for c in pod["status"].get("conditions", [])
                )
                print("PASS: missing service Secret prevents Pod startup/readiness")
                return
            time.sleep(2)
        raise RuntimeError("missing Secret did not produce the required bounded FailedMount")
    finally:
        run([*k, "delete", "pod", "lab-missing-secret", "--wait=true", "--timeout=30s"])


def invalid_service_probe(k: list[str], apply: Any, browser: str, service: str) -> None:
    deployment = json.loads(run([*k, "get", "deployment", f"{NAME}-dashboard", "-o", "json"]))
    template = deployment["spec"]["template"]
    for volume in template["spec"]["volumes"]:
        if volume["name"] == "service-token":
            volume["secret"]["secretName"] = "lab-invalid-service"
    template["spec"]["restartPolicy"] = "Never"
    apply(
        {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": canary_metadata(deployment, "lab-invalid-service"),
            "spec": template["spec"],
        }
    )
    try:
        run(
            [
                *k,
                "wait",
                "--for=jsonpath={.status.phase}=Running",
                "pod/lab-invalid-service",
                "--timeout=60s",
            ]
        )
        code = r"""
import json, sys, time, urllib.request, urllib.error
browser = json.load(sys.stdin)["browser"]
for attempt in range(30):
    try:
        urllib.request.urlopen("http://127.0.0.1:8080/health/ready",timeout=3)
    except urllib.error.HTTPError as error:
        assert error.code == 503, error.code
        break
    except OSError:
        time.sleep(1)
    else:
        raise AssertionError("invalid service authority became ready")
else:
    raise AssertionError("invalid-service canary never served bounded readiness")
headers={"Host":"dashboard.example.test", "Origin":"https://dashboard.example.test",
    "Content-Type":"application/json", "X-Dashboard-Request":"1"}
login=urllib.request.Request("http://127.0.0.1:8080/dashboard/api/v1/session/login",
    data=json.dumps({"credential":browser}).encode(),headers=headers)
with urllib.request.urlopen(login,timeout=5) as response:
    assert response.status == 204
    headers["Cookie"]=response.headers["Set-Cookie"].split(";",1)[0]
session=urllib.request.Request("http://127.0.0.1:8080/dashboard/api/v1/session",headers=headers)
try:
    urllib.request.urlopen(session,timeout=5)
except urllib.error.HTTPError as error:
    assert error.code == 503, error.code
else:
    raise AssertionError("browser authority substituted for invalid service credential")
"""
        run(
            [*k, "exec", "-i", "lab-invalid-service", "--", "python", "-c", code],
            json.dumps({"browser": browser}),
            timeout=90,
        )
        pod = json.loads(run([*k, "get", "pod", "lab-invalid-service", "-o", "json"]))
        assert not any(
            c.get("type") == "Ready" and c.get("status") == "True"
            for c in pod["status"].get("conditions", [])
        )
        logs = run([*k, "logs", "lab-invalid-service", "-c", "dashboard"])
        assert browser not in logs and service not in logs, "canary credentials in runtime logs"
        print("PASS: invalid service bearer refuses readiness/session bootstrap without fallback")
    finally:
        run([*k, "delete", "pod", "lab-invalid-service", "--wait=true", "--timeout=30s"])


if __name__ == "__main__":
    main()

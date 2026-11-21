"""Synthetic bridge/portmap/firewall compatibility proof; refuses every live context."""

from __future__ import annotations

import hashlib
import json
import shlex
import subprocess
import time
from pathlib import Path

import yaml
from netpol_filter_cleanup import cleanup_transaction, owned_set

PROFILE = "kv-policy-only"
NS = "kv-policy-synthetic"
PEER = "kv-policy-peer"
PROBE_IMAGE = (
    "python:3.12.15-slim-bookworm@"
    "sha256:6a0e06f5ba0b373d051aabd3fffb9b873f27f9a2a3a14bcbe37dee6f0e3bbc70"
)
K = ["kubectl", "--context", PROFILE, "--request-timeout=20s"]


def run(args: list[str], data: str | None = None, *, check: bool = True) -> str:
    if args[0] not in {"kubectl", "minikube"}:
        raise ValueError("Unexpected rehearsal executable")
    # Fixed executables, argv arrays and guarded disposable context; no shell evaluation.
    result = subprocess.run(  # noqa: S603
        args, input=data, text=True, capture_output=True, timeout=180
    )
    if check and result.returncode:
        raise RuntimeError("Rehearsal command failed: " + args[0])
    return result.stdout


def apply(document: dict) -> None:
    run([*K, "apply", "-f", "-"], json.dumps(document))


def ssh(command: str) -> str:
    return run(["minikube", "-p", PROFILE, "ssh", "--", command]).replace("\r", "")


def wait_assertion(test, message: str) -> None:
    deadline = time.monotonic() + 75
    while time.monotonic() < deadline:
        if test():
            print("PASS " + message, flush=True)
            return
        time.sleep(2)
    raise RuntimeError("Timed out: " + message)


def probe(namespace: str, pod: str, destination: str, port: int) -> bool:
    code = (
        "import socket; s=socket.create_connection("
        + repr((destination, port))
        + ",timeout=2); s.close()"
    )
    result = subprocess.run(  # noqa: S603 - fixed guarded kubectl exec, literal Python socket probe
        [*K, "exec", "-n", namespace, pod, "--", "python", "-c", code],
        capture_output=True,
        timeout=30,
    )
    return result.returncode == 0


def pod(namespace: str, name: str, ports: tuple[int, ...] = ()) -> dict:
    command = "import time; time.sleep(3600)"
    if ports:
        command = (
            "import socketserver,threading,time\n"
            "class H(socketserver.BaseRequestHandler):\n def handle(self): pass\n"
            "class S(socketserver.ThreadingTCPServer):\n allow_reuse_address=True\n"
            f"for port in {ports!r}:\n"
            " threading.Thread(target=S(('0.0.0.0',port),H).serve_forever,daemon=True).start()\n"
            "time.sleep(3600)"
        )
    capabilities = {"drop": ["ALL"]}
    if 443 in ports:
        capabilities["add"] = ["NET_BIND_SERVICE"]
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {"name": name, "namespace": namespace, "labels": {"role": name}},
        "spec": {
            "automountServiceAccountToken": False,
            "securityContext": {
                "runAsUser": 10001,
                "runAsNonRoot": True,
                "seccompProfile": {"type": "RuntimeDefault"},
            },
            "containers": [
                {
                    "name": "probe",
                    "image": PROBE_IMAGE,
                    "command": ["python", "-c", command],
                    "resources": {
                        "requests": {"cpu": "10m", "memory": "32Mi"},
                        "limits": {"cpu": "100m", "memory": "96Mi"},
                    },
                    "securityContext": {
                        "readOnlyRootFilesystem": True,
                        "allowPrivilegeEscalation": False,
                        "capabilities": capabilities,
                    },
                }
            ],
        },
    }


def policy(namespace: str, name: str, selector: dict, **rules) -> None:
    apply(
        {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": {"namespace": namespace, "name": name},
            "spec": {"podSelector": {"matchLabels": selector}, **rules},
        }
    )


def inspector(*args: str, data: str | None = None) -> str:
    return run([*K, "exec", "-i", "-n", "kube-system", "policy-inspector", "--", *args], data)


def main() -> None:
    identity = json.loads(run([*K, "config", "view", "--minify", "-o", "json"]))
    if identity["contexts"][0]["context"]["cluster"] != PROFILE:
        raise RuntimeError("Refusing a non-disposable cluster")
    nodes = json.loads(run([*K, "get", "nodes", "-o", "json"]))["items"]
    if len(nodes) != 1 or nodes[0]["metadata"]["name"] != PROFILE:
        raise RuntimeError("Unexpected disposable node identity")
    for namespace in (NS, PEER):
        if run([*K, "get", "namespace", namespace, "--ignore-not-found", "-o", "name"]).strip():
            raise RuntimeError("Refusing an existing fixture namespace")
    paths = ssh("sudo find /etc/cni/net.d -maxdepth 1 -name '*.conflist' -type f").splitlines()
    if len(paths) != 1:
        raise RuntimeError("Expected one bridge CNI configuration")
    path = paths[0]
    cni = json.loads(ssh("sudo cat " + shlex.quote(path)))
    types = [entry["type"] for entry in cni["plugins"]]
    if types not in (["bridge", "portmap"], ["bridge", "portmap", "firewall"]):
        raise RuntimeError("Expected a synthetic bridge/portmap/firewall profile")
    if types == ["bridge", "portmap"]:
        cni["plugins"].append({"type": "firewall"})
        ssh(
            "printf %s "
            + shlex.quote(json.dumps(cni))
            + " | sudo tee "
            + shlex.quote(path)
            + " >/dev/null"
        )
        run([*K, "rollout", "restart", "deployment/coredns", "-n", "kube-system"])
        run([*K, "rollout", "status", "deployment/coredns", "-n", "kube-system", "--timeout=120s"])
    cni_hash = hashlib.sha256(ssh("sudo cat " + shlex.quote(path)).encode()).hexdigest()
    print("Bridge/portmap/firewall CNI fixture configured", flush=True)
    docs = list(
        yaml.safe_load_all((Path(__file__).parent / "fixtures/netpol-controller.yaml").read_text())
    )
    daemonset = next(doc for doc in docs if doc["kind"] == "DaemonSet")
    template = daemonset["spec"]["template"]["spec"]
    # Use the actual imported minimal review artifact by immutable containerd digest.
    images = ssh("sudo ctr -n k8s.io images ls").splitlines()
    digest = next(
        line.split()[2]
        for line in images
        if line.split()[0] == "docker.io/library/kv-netpol-only:review"
    )
    if not digest.startswith("sha256:") or len(digest) != 71:
        raise RuntimeError("Invalid imported image digest")
    image = "docker.io/library/kv-netpol-only@" + digest
    ssh("sudo ctr -n k8s.io images tag docker.io/library/kv-netpol-only:review " + image)
    template["containers"][0]["image"] = image
    for namespace in (NS, PEER):
        apply({"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": namespace}})
    for name, ports in [
        ("api", (8000,)),
        ("database", (5432,)),
        ("dashboard", ()),
        ("untrusted", ()),
    ]:
        apply(pod(NS, name, ports))
    apply(pod(PEER, "ingress", (8080, 443)))
    apply(pod(NS, "ingress"))
    run([*K, "wait", "--for=condition=Ready", "pods", "--all", "-n", NS, "--timeout=120s"])
    run([*K, "wait", "--for=condition=Ready", "pods", "--all", "-n", PEER, "--timeout=120s"])
    addresses = {
        name: json.loads(run([*K, "get", "pod", name, "-n", namespace, "-o", "json"]))["status"][
            "podIP"
        ]
        for namespace, name in [(NS, "api"), (NS, "database"), (PEER, "ingress")]
    }
    for name, port in (("api", 8000), ("database", 5432)):
        apply(
            {
                "apiVersion": "v1",
                "kind": "Service",
                "metadata": {"name": name, "namespace": NS},
                "spec": {"selector": {"role": name}, "ports": [{"port": port, "targetPort": port}]},
            }
        )
        addresses[name + "_service"] = json.loads(
            run([*K, "get", "service", name, "-n", NS, "-o", "json"])
        )["spec"]["clusterIP"]
    wait_assertion(
        lambda: probe(NS, "untrusted", addresses["api"], 8000), "pre-enforcer API positive control"
    )
    wait_assertion(
        lambda: probe(NS, "dashboard", addresses["database"], 5432),
        "pre-enforcer database positive control",
    )
    wait_assertion(
        lambda: probe(NS, "untrusted", addresses["ingress"], 443), "HTTPS-port positive control"
    )
    ready_uids = {
        name: json.loads(run([*K, "get", "pod", name, "-n", NS, "-o", "json"]))["metadata"]["uid"]
        for name in ("api", "database")
    }
    api_ingress = [
        {
            "from": [
                {"podSelector": {"matchLabels": {"role": "dashboard"}}},
                {
                    "namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": PEER}},
                    "podSelector": {"matchLabels": {"role": "ingress"}},
                },
            ],
            "ports": [{"protocol": "TCP", "port": 8000}],
        }
    ]
    policy(NS, "api", {"role": "api"}, policyTypes=["Ingress"], ingress=api_ingress)
    policy(
        NS,
        "database",
        {"role": "database"},
        policyTypes=["Ingress"],
        ingress=[
            {
                "from": [{"podSelector": {"matchLabels": {"role": "api"}}}],
                "ports": [{"protocol": "TCP", "port": 5432}],
            }
        ],
    )
    policy(
        NS,
        "dashboard",
        {"role": "dashboard"},
        policyTypes=["Egress"],
        egress=[
            {
                "to": [{"podSelector": {"matchLabels": {"role": "api"}}}],
                "ports": [{"protocol": "TCP", "port": 8000}],
            },
            {
                "to": [
                    {
                        "namespaceSelector": {
                            "matchLabels": {"kubernetes.io/metadata.name": "kube-system"}
                        },
                        "podSelector": {"matchLabels": {"k8s-app": "kube-dns"}},
                    }
                ],
                "ports": [{"protocol": "UDP", "port": 53}, {"protocol": "TCP", "port": 53}],
            },
        ],
    )
    if not probe(NS, "untrusted", addresses["api"], 8000):
        raise RuntimeError("The base CNI unexpectedly already enforces NetworkPolicy")
    # Reuse the same capability-limited image for inspection and scoped rollback.
    apply(
        {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {"name": "policy-inspector", "namespace": "kube-system"},
            "spec": {
                "hostNetwork": True,
                "automountServiceAccountToken": False,
                "containers": [
                    {
                        "name": "inspector",
                        "image": image,
                        "command": ["/bin/sh", "-c", "sleep 1800"],
                        "securityContext": {
                            "allowPrivilegeEscalation": False,
                            "readOnlyRootFilesystem": True,
                            "capabilities": {"drop": ["ALL"], "add": ["NET_ADMIN", "NET_RAW"]},
                        },
                        "resources": {
                            "requests": {"cpu": "10m", "memory": "32Mi"},
                            "limits": {"cpu": "200m", "memory": "128Mi"},
                        },
                        "volumeMounts": [{"name": "lock", "mountPath": "/run/xtables.lock"}],
                    }
                ],
                "volumes": [
                    {
                        "name": "lock",
                        "hostPath": {"path": "/run/xtables.lock", "type": "FileOrCreate"},
                    }
                ],
            },
        }
    )
    run(
        [
            *K,
            "wait",
            "--for=condition=Ready",
            "pod/policy-inspector",
            "-n",
            "kube-system",
            "--timeout=120s",
        ]
    )
    before = inspector("iptables-save", "-t", "filter")
    if cleanup_transaction(before):
        raise RuntimeError("Reserved chains already exist")
    before_sets = set(inspector("ipset", "list", "-name").splitlines())
    if any(owned_set(name) for name in before_sets):
        raise RuntimeError("Reserved policy ipsets already exist")
    inspector("iptables", "-N", "KV-POLICY-SENTINEL")
    inspector("iptables", "-A", "KV-POLICY-SENTINEL", "-j", "RETURN")
    inspector("ipset", "create", "KV-POLICY-SENTINEL", "hash:ip")
    inspector("ipset", "add", "KV-POLICY-SENTINEL", "192.0.2.1")
    for doc in docs:
        apply(doc)
    run([*K, "rollout", "status", "daemonset/kube-router", "-n", "kube-system", "--timeout=180s"])
    wait_assertion(
        lambda: not probe(NS, "untrusted", addresses["api"], 8000), "untrusted API ingress denied"
    )
    checks = [
        (NS, "dashboard", "api", 8000, True),
        (NS, "dashboard", "api_service", 8000, True),
        (PEER, "ingress", "api", 8000, True),
        (NS, "ingress", "api", 8000, False),
        (NS, "api", "database", 5432, True),
        (NS, "api", "database_service", 5432, True),
        (NS, "dashboard", "database", 5432, False),
        (NS, "dashboard", "database_service", 5432, False),
        (NS, "dashboard", "ingress", 443, False),
        (NS, "dashboard", "ingress", 8080, False),
    ]
    for namespace, name, target, port, expected in checks:
        if probe(namespace, name, addresses[target], port) != expected:
            raise RuntimeError(f"Policy assertion failed: {name}->{target}:{port}")
    api_egress = [
        {
            "to": [
                {
                    "namespaceSelector": {
                        "matchLabels": {"kubernetes.io/metadata.name": "kube-system"}
                    },
                    "podSelector": {"matchLabels": {"k8s-app": "kube-dns"}},
                }
            ],
            "ports": [{"protocol": "UDP", "port": 53}, {"protocol": "TCP", "port": 53}],
        },
        {
            "to": [{"podSelector": {"matchLabels": {"role": "database"}}}],
            "ports": [{"protocol": "TCP", "port": 5432}],
        },
        {
            "to": [{"ipBlock": {"cidr": nodes[0]["spec"]["podCIDR"]}}],
            "ports": [{"protocol": "TCP", "port": 443}],
        },
    ]
    policy(NS, "api-egress", {"role": "api"}, policyTypes=["Egress"], egress=api_egress)
    wait_assertion(
        lambda: probe(NS, "api", addresses["ingress"], 443), "CIDR egress positive control"
    )
    api_egress[2]["to"][0]["ipBlock"]["except"] = [addresses["ingress"] + "/32"]
    policy(NS, "api-egress", {"role": "api"}, policyTypes=["Egress"], egress=api_egress)
    wait_assertion(
        lambda: not probe(NS, "api", addresses["ingress"], 443), "CIDR exception blocks destination"
    )
    del api_egress[2]["to"][0]["ipBlock"]["except"]
    policy(NS, "api-egress", {"role": "api"}, policyTypes=["Egress"], egress=api_egress)
    wait_assertion(
        lambda: probe(NS, "api", addresses["ingress"], 443), "CIDR exception removal recovers"
    )
    run(
        [
            *K,
            "exec",
            "-n",
            NS,
            "dashboard",
            "--",
            "python",
            "-c",
            "import socket; socket.getaddrinfo('kubernetes.default.svc.cluster.local',443)",
        ]
    )
    print(
        "PASS namespace+Pod selector AND, API/DB isolation, HTTP/HTTPS denial and DNS", flush=True
    )
    for name, uid in ready_uids.items():
        current = json.loads(run([*K, "get", "pod", name, "-n", NS, "-o", "json"]))
        if current["metadata"]["uid"] != uid or any(
            c["restartCount"] for c in current["status"]["containerStatuses"]
        ):
            raise RuntimeError("Existing synthetic core Pod changed")
    if hashlib.sha256(ssh("sudo cat " + shlex.quote(path)).encode()).hexdigest() != cni_hash:
        raise RuntimeError("Controller changed CNI configuration")
    print("PASS unchanged existing Pod UIDs/restarts and bridge CNI configuration", flush=True)
    run(
        [
            *K,
            "patch",
            "daemonset",
            "kube-router",
            "-n",
            "kube-system",
            "--type=merge",
            "-p",
            json.dumps(
                {"spec": {"template": {"spec": {"nodeSelector": {"kv-policy-disabled": "true"}}}}}
            ),
        ]
    )
    run(
        [
            *K,
            "wait",
            "--for=delete",
            "pod",
            "-l",
            "k8s-app=kube-router",
            "-n",
            "kube-system",
            "--timeout=120s",
        ]
    )
    snapshot = inspector("iptables-save", "-t", "filter")
    transaction = cleanup_transaction(snapshot)
    if not transaction:
        raise RuntimeError("Expected active policy chains")
    inspector("iptables-restore", "--test", "--noflush", data=transaction)
    inspector("iptables-restore", "--wait", "10", "--noflush", data=transaction)
    remaining = inspector("iptables-save", "-t", "filter")
    if cleanup_transaction(remaining):
        raise RuntimeError("Reserved chains remain after cleanup")
    for name in sorted(set(inspector("ipset", "list", "-name").splitlines()) - before_sets):
        if owned_set(name):
            inspector("ipset", "destroy", name)
    if any(owned_set(name) for name in inspector("ipset", "list", "-name").splitlines()):
        raise RuntimeError("Reserved ipsets remain after cleanup")
    inspector("iptables", "-C", "KV-POLICY-SENTINEL", "-j", "RETURN")
    inspector("ipset", "test", "KV-POLICY-SENTINEL", "192.0.2.1")
    # Never call the broad --cleanup-config / IPVS flush.
    for namespace, name, target, port, _ in checks:
        wait_assertion(
            lambda ns=namespace, n=name, t=target, p=port: probe(ns, n, addresses[t], p),
            f"rollback connectivity {name}->{target}:{port}",
        )
    run(
        [
            *K,
            "exec",
            "-n",
            NS,
            "dashboard",
            "--",
            "python",
            "-c",
            "import socket; socket.getaddrinfo('kubernetes.default.svc.cluster.local',443)",
        ]
    )
    print(
        "PASS scoped rollback preserves unrelated sentinel, DNS and Service networking", flush=True
    )
    print("Synthetic policy-only compatibility rehearsal PASS", flush=True)


if __name__ == "__main__":
    main()

"""Render real Helm templates and verify Dashboard isolation, defaults and fail-closed values.

Run with the repository's locked environment; output files are also inputs to kubeconform.
No Kubernetes context, credentials or network are accessed.
"""

import argparse
import copy
import json
import shutil
import subprocess
from pathlib import Path

import yaml

CHART = Path("charts/knowledge-vault")
DIGEST = "sha256:" + "0" * 64
HELM = shutil.which("helm")
PRIVATE = {
    "enabled": True,
    "host": "dashboard.example.test",
    "coreHost": "mcp-lan.example.test",
    "image": {"digest": DIGEST},
    "serviceTokenSecret": {"name": "dashboard-service"},
    "privateSession": {
        "existingSecret": "dashboard-browser",
        "signingSecret": "dashboard-signing",
    },
}
PEER = {
    "namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "ingress-system"}},
    "podSelector": {"matchLabels": {"app": "ingress-controller"}},
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    count = 0

    def render(name: str, overrides: dict, *, valid: bool = True) -> list[dict]:
        nonlocal count
        values = args.output_dir / (name + "-values.json")
        values.write_text(
            json.dumps(
                {
                    "config": {
                        "publicBaseUrl": "https://mcp.example.test",
                        "authIssuerUrl": "https://mcp.example.test",
                    },
                    **overrides,
                }
            )
        )
        result = subprocess.run(  # noqa: S603  # fixed tool and repository-controlled inputs
            [
                HELM or "/missing/helm",
                "template",
                "knowledge-vault",
                str(CHART),
                "--namespace",
                "dashboard-test",
                "-f",
                str(CHART / "values-minikube.yaml"),
                "-f",
                str(values),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        count += 1
        if not valid:
            assert result.returncode != 0, f"invalid configuration accepted: {name}"
            return []
        assert result.returncode == 0, f"{name}: {result.stderr}"
        (args.output_dir / (name + ".yaml")).write_text(result.stdout)
        return [item for item in yaml.safe_load_all(result.stdout) if item]

    def core(items: list[dict]) -> list[dict]:
        return [
            item
            for item in items
            if item["metadata"].get("labels", {}).get("app.kubernetes.io/component") != "dashboard"
        ]

    disabled = render("dashboard-disabled", {})
    assert len(core(disabled)) == len(disabled), "default renders Dashboard resources"
    enabled = render("dashboard-private", {"dashboard": PRIVATE})
    assert core(enabled) == disabled, "enabling Dashboard changed a core resource/checksum/hook"
    resources = [item for item in enabled if item not in core(enabled)]
    assert sorted(item["kind"] for item in resources) == [
        "ConfigMap",
        "Deployment",
        "NetworkPolicy",
        "NetworkPolicy",
        "Service",
        "ServiceAccount",
    ]
    deployment = next(item for item in resources if item["kind"] == "Deployment")
    pod = deployment["spec"]["template"]["spec"]
    assert not pod["automountServiceAccountToken"]
    assert pod["securityContext"] == {
        "runAsNonRoot": True,
        "runAsUser": 10001,
        "runAsGroup": 10001,
        "fsGroup": 10001,
        "seccompProfile": {"type": "RuntimeDefault"},
    }
    container = pod["containers"][0]
    assert len(pod["containers"]) == 1 and "initContainers" not in pod
    assert container["securityContext"] == {
        "allowPrivilegeEscalation": False,
        "readOnlyRootFilesystem": True,
        "capabilities": {"drop": ["ALL"]},
    }
    assert set(container["resources"]) == {"requests", "limits"}
    assert container["livenessProbe"]["httpGet"]["path"] == "/health/live"
    assert container["readinessProbe"]["httpGet"]["path"] == "/health/ready"
    assert "env" not in container and "envFrom" not in container
    assert all(volume.get("secret", {}).get("optional") is not True for volume in pod["volumes"])
    assert all(mount["readOnly"] for mount in container["volumeMounts"])
    assert {v["secret"]["secretName"] for v in pod["volumes"] if "secret" in v} == {
        "dashboard-service",
        "dashboard-browser",
        "dashboard-signing",
    }
    config = json.loads(
        next(item for item in resources if item["kind"] == "ConfigMap")["data"]["config.json"]
    )
    assert (
        config["core_url"]
        == "http://knowledge-vault-knowledge-vault.dashboard-test.svc.cluster.local:8000"
    )
    assert config["core_host"] == PRIVATE["coreHost"]
    policy = next(
        item
        for item in resources
        if item["metadata"]["name"].endswith("-dashboard") and item["kind"] == "NetworkPolicy"
    )
    assert policy["spec"]["ingress"] == []
    assert [rule["ports"] for rule in policy["spec"]["egress"]] == [
        [{"protocol": "UDP", "port": 53}, {"protocol": "TCP", "port": 53}],
        [{"protocol": "TCP", "port": 8000}],
    ]
    service = next(item for item in resources if item["kind"] == "Service")
    assert service["spec"]["type"] == "ClusterIP"

    changed = copy.deepcopy(PRIVATE)
    changed["image"]["digest"] = "sha256:" + "1" * 64
    changed["privateSession"]["signingSecret"] = "dashboard-signing-rotated"
    changed["secretRevision"] = "2"
    update = render("dashboard-upgrade", {"dashboard": changed})
    assert core(update) == disabled
    rollback = render("dashboard-rollback", {"dashboard": PRIVATE})
    assert rollback == enabled
    removal = render("dashboard-removed", {"dashboard": {**changed, "enabled": False}})
    assert removal == disabled

    ingress_values = copy.deepcopy(PRIVATE)
    ingress_values["ingress"] = {
        "enabled": True,
        "className": "private",
        "tlsSecret": "dashboard-tls",
    }
    ingress_values["networkPolicy"] = {"ingressFrom": [PEER]}
    ingress = render("dashboard-ingress", {"dashboard": ingress_values})
    assert core(ingress) == disabled
    route = next(item for item in ingress if item["kind"] == "Ingress")
    assert route["spec"]["rules"][0]["host"] == PRIVATE["host"]
    assert route["spec"]["tls"] == [{"hosts": [PRIVATE["host"]], "secretName": "dashboard-tls"}]
    paths = route["spec"]["rules"][0]["http"]["paths"]
    assert len(paths) == 1 and paths[0]["path"] == "/dashboard"
    assert paths[0]["backend"]["service"]["name"].endswith("-dashboard")
    assert "annotations" not in route["metadata"], "ingress must preserve Host/path without rewrite"

    access = copy.deepcopy(PRIVATE)
    access["mode"] = "cloudflareAccess"
    access["privateSession"] = {"existingSecret": "", "signingSecret": ""}
    access["access"] = {
        "issuerUrl": "https://test.cloudflareaccess.com",
        "audience": "test-aud",
        "allowedEmails": ["operator@example.test"],
        "jwksEgressCidrs": ["104.16.0.0/13"],
    }
    access_resources = render("dashboard-access", {"dashboard": access})
    assert core(access_resources) == disabled
    access_pod = next(
        item
        for item in access_resources
        if item["kind"] == "Deployment" and item["metadata"]["name"].endswith("-dashboard")
    )["spec"]["template"]["spec"]
    assert len(access_pod["volumes"]) == 2, "Access must not mount private session keys"

    cloudflare = {
        "cloudflareAccess": {
            "enabled": True,
            "existingSecret": "core-access",
            "publicHosts": ["mcp.example.test"],
            "privateHosts": [PRIVATE["coreHost"]],
        },
        "cloudflareTunnel": {
            "enabled": True,
            "existingSecret": "tunnel",
            "image": {"digest": DIGEST},
        },
        "backup": {"enabled": True, "existingSecret": "backup"},
        "watchdog": {"enabled": True},
    }
    base_cloudflare = render("dashboard-cloudflare-disabled", cloudflare)
    tunnel = copy.deepcopy(access)
    tunnel["networkPolicy"] = {"allowCloudflared": True}
    with_cloudflare = render("dashboard-cloudflare-enabled", {**cloudflare, "dashboard": tunnel})
    assert core(with_cloudflare) == base_cloudflare
    assert (
        render(
            "dashboard-cloudflare-removed",
            {**cloudflare, "dashboard": {**tunnel, "enabled": False}},
        )
        == base_cloudflare
    )

    invalid = [
        {"enabled": True},
        {**PRIVATE, "host": PRIVATE["coreHost"]},
        {**PRIVATE, "host": "mcp.example.test"},
        {**PRIVATE, "image": {"digest": ""}},
        {**PRIVATE, "serviceTokenSecret": {"name": ""}},
        {**PRIVATE, "privateSession": {"existingSecret": "", "signingSecret": "dashboard-signing"}},
        {
            **PRIVATE,
            "privateSession": {
                "existingSecret": "dashboard-browser",
                "signingSecret": "dashboard-browser",
            },
        },
        {**PRIVATE, "serviceTokenSecret": {"name": "knowledge-vault-auth"}},
        {**PRIVATE, "ingress": {"enabled": True}},
        {**ingress_values, "ingress": {"enabled": True, "className": "private", "tlsSecret": ""}},
        {**PRIVATE, "mode": "unknown"},
        {**access, "privateSession": PRIVATE["privateSession"]},
        {**access, "access": {**access["access"], "jwksEgressCidrs": []}},
        {**access, "access": {**access["access"], "jwksEgressCidrs": ["10.0.0.0/8"]}},
        {**PRIVATE, "resources": {"limits": None}},
        {**PRIVATE, "serviceTokenSecret": {"key": ""}},
        {**PRIVATE, "networkPolicy": {"allowCloudflared": True}},
        {**PRIVATE, "enabeld": True},
    ]
    for index, bad in enumerate(invalid):
        render(f"invalid-{index}", {"dashboard": bad}, valid=False)
    render(
        "invalid-policy", {"dashboard": PRIVATE, "networkPolicy": {"enabled": False}}, valid=False
    )
    render(
        "invalid-core-access-host",
        {**cloudflare, "dashboard": {**PRIVATE, "coreHost": "other.example.test"}},
        valid=False,
    )
    print(
        f"Dashboard chart: {count} renders; "
        "default-off, core equivalence, security and invalid-value gates passed"
    )


if __name__ == "__main__":
    main()

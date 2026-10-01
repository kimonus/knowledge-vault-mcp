# Support

Knowledge Vault is a single-maintainer open-source project. Community support is best effort; no
response-time or remediation SLA is provided.

## Where to ask

- Use a **bug report** for reproducible defects with synthetic data.
- Use a **feature request** for new behavior or a material architecture change.
- Use a regular discussion issue for a focused setup question not answered by the runbooks.
- Follow [SECURITY.md](SECURITY.md) for vulnerabilities or suspected compromise.
- Follow [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) for private conduct reports.

Search existing issues and the [troubleshooting runbook](docs/runbooks/troubleshooting.md) first.
Do not post tokens, passwords, Access assertions, assertion content, private hostnames, database
URLs, logs containing personal data, or screenshots of secret-bearing configuration.

## Supported scope

The maintained reference deployment is the checked-in Python application, PostgreSQL/pgvector,
container images, and Helm chart on a single-node Kubernetes environment. The current `main`
branch is supported until the first stable release; after that, supported versions are listed in
[SECURITY.md](SECURITY.md).

Operators remain responsible for DNS, their selected edge/identity provider (Cloudflare in the
reference profile), private-network routing (WireGuard/LAN in the reference profile), Kubernetes
storage, off-host backups, secret management, client configuration, and capacity planning.
Assistance with unrelated network appliances, identity-provider administration, or third-party
client bugs may be limited to identifying the integration boundary.

## Useful diagnostics

Provide only content-free diagnostics:

- Knowledge Vault version or commit;
- deployment mode and Kubernetes version;
- failing command and sanitized error;
- relevant Pod status and event reason without environment values;
- whether the issue reproduces with embeddings disabled; and
- the smallest synthetic reproduction.

See [CONTRIBUTING.md](CONTRIBUTING.md) before proposing a patch.

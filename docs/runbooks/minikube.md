# Minikube deployment

The supported public deployment path is the Helm chart. Personal host paths, DNS names, ingress
objects, and cluster-wide certificate manifests belong in an operator's private infrastructure
repository and are intentionally excluded from this project.

Minikube is the maintained reference runtime, and Cloudflare + LAN/WireGuard is the maintained
reference connectivity profile. Operators may attach another secure edge or private network. Keep
the origin Service `ClusterIP`, require authenticated TLS at the external boundary, and implement
origin-verifiable authentication rather than trusting a proxy header by convention.

## Inputs

Choose locally:

- a random token pepper and one scoped bearer token per direct client;
- a database username, database name, and randomly generated password;
- private and public hostnames owned by the operator;
- a storage class or pre-provisioned PVC;
- an optional pre-populated embedding-model cache PVC;
- optional Cloudflare Access and Tunnel Secrets;
- an encrypted restic repository and credentials if backups are enabled.

Never place these values in Helm values, manifests, shell history, or Git.

## Build local images

For an isolated Minikube evaluation:

```bash
minikube start --cpus=4 --memory=8192
eval "$(minikube docker-env)"
docker build --pull=false -t knowledge-vault:0.1.0 .
docker build --pull=false -f Dockerfile.backup -t knowledge-vault-backup:0.1.0 .
```

`values-minikube.yaml` points `image.repository` and `backupImage.repository` at these local
names. The chart's own defaults are the published images,
`ghcr.io/kimonus/knowledge-vault-mcp` and `ghcr.io/kimonus/knowledge-vault-mcp-backup`;
production operators set a version and an immutable digest for them in Helm values.

## Create runtime Secrets

Generate token material without printing plaintext into a manifest:

```bash
install -d -m 0700 .local-secrets
TOKEN_PEPPER="$(openssl rand -hex 32)"
uv run python scripts/generate_token.py \
  --principal operator-device \
  --scopes knowledge:read knowledge:write \
  --pepper-stdin \
  --token-output .local-secrets/operator-device.token \
  --record-output .local-secrets/operator-device-record.json \
  <<<"$TOKEN_PEPPER"
```

Create namespace-local Secrets using an operator-controlled secret workflow. The following is a
development example; avoid literal values in reusable automation:

```bash
kubectl create namespace knowledge-vault
kubectl -n knowledge-vault create secret generic knowledge-vault-auth \
  --from-literal=token-pepper="$TOKEN_PEPPER" \
  --from-file=bootstrap-tokens=.local-secrets/operator-device-record.json
kubectl -n knowledge-vault create secret generic knowledge-vault-postgres \
  --from-literal=username=knowledge_vault \
  --from-literal=password='GENERATE_DATABASE_PASSWORD' \
  --from-literal=database=knowledge_vault
kubectl -n knowledge-vault create secret generic knowledge-vault-database \
  --from-literal=database-url='postgresql+psycopg://knowledge_vault:URL_ENCODED_PASSWORD@knowledge-vault-knowledge-vault-postgresql:5432/knowledge_vault'
unset TOKEN_PEPPER
```

The record file holds one JSON object, which the service accepts as the first bootstrap token.
Add further devices with the procedure in [tokens.md](tokens.md); it converts the value to a JSON
array.

Resources are named `RELEASE-knowledge-vault-…`. With the release name `knowledge-vault` used
below, the database Service is `knowledge-vault-knowledge-vault-postgresql`, which is why that
name appears in the database URL. Set `fullnameOverride` to choose different names and adjust the
URL to match.

## Render and install

Review `charts/knowledge-vault/values-minikube.yaml`, then render and validate before installing:

```bash
helm lint charts/knowledge-vault -f charts/knowledge-vault/values-minikube.yaml
helm template knowledge-vault charts/knowledge-vault \
  -f charts/knowledge-vault/values-minikube.yaml > rendered.yaml
kubeconform -strict -summary -kubernetes-version 1.33.0 rendered.yaml
helm upgrade --install knowledge-vault charts/knowledge-vault \
  --namespace knowledge-vault \
  --values charts/knowledge-vault/values-minikube.yaml \
  --set config.embeddingsEnabled=false
```

When the Cloudflare Secrets from
[cloudflare-access.md](cloudflare-access.md) exist, enable both origin JWT validation and the
outbound connector through an operator-local values file:

```yaml
cloudflareAccess:
  enabled: true
  existingSecret: knowledge-vault-cloudflare-access
  # Required. Requests for these hostnames are refused without a signed Access assertion.
  publicHosts: [PUBLIC_MCP_HOST]
  # Recommended. Device bearer tokens are then accepted only for these hostnames; every other
  # hostname (Service DNS names, Pod IPs, port-forwards) requires an assertion.
  privateHosts: [PRIVATE_MCP_HOST]
cloudflareTunnel:
  enabled: true
  existingSecret: knowledge-vault-cloudflared
  image:
    repository: cloudflare/cloudflared
    tag: "RELEASE_FOR_HUMANS"
    digest: "sha256:VERIFIED_IMMUTABLE_DIGEST"
```

Do not pass secret values through `--set`; the chart accepts only Secret names and key names.

Embedding production Pods must use a pre-populated, read-only model cache. Pods load weights from
local files only and never contact the model hub, so with `config.embeddingsEnabled=true` and no
usable cache `/health/ready` reports `"embedding": "degraded"` and search serves text results.
Populate a PVC as described in [embeddings.md](embeddings.md) and set
`modelCache.existingClaim`; the chart mounts it read-only at `/models` and points the Hugging
Face cache there.

## Private ingress

When `ingress.enabled` is set, only `ingress.paths` (`/mcp`, `/api/v1`, and `/.well-known` by
default) are routed. Health, metrics, and the OpenAPI document are not exposed on the private
hostname; reach them through the Service inside the cluster or with `kubectl port-forward`.
Apply the same restriction to an ingress you manage outside this chart.

## Network policy

The chart renders one NetworkPolicy per component: PostgreSQL accepts connections only from the
API, worker, migration, and backup Pods; the worker and Jobs accept no ingress; the API accepts
port 8000 from `networkPolicy.apiIngressFrom` (any namespace by default—narrow it to the ingress
controller's namespace) and from this release's Cloudflared Pods. The API may reach the Cloudflare
key endpoint on 443 only when Access is enabled, and the backup Job uses
`networkPolicy.backupEgress`. These policies take effect only when the cluster's CNI enforces
NetworkPolicy; confirm that for your cluster (Minikube can be started with an enforcing CNI, for
example `minikube start --cni=calico`) before relying on them. They have been validated by schema
and rendering, not yet on an enforcing cluster.

## Verify health and durability

```bash
kubectl -n knowledge-vault get pods,pvc,job
kubectl -n knowledge-vault rollout status statefulset/knowledge-vault-knowledge-vault-postgresql --timeout=180s
kubectl -n knowledge-vault rollout status deployment/knowledge-vault-knowledge-vault-api --timeout=180s
kubectl -n knowledge-vault rollout status deployment/knowledge-vault-knowledge-vault-worker --timeout=180s
kubectl -n knowledge-vault port-forward service/knowledge-vault-knowledge-vault 8000:8000
curl --fail http://127.0.0.1:8000/health/ready
```

The migration Job is a Helm hook that is deleted once it succeeds; the API and worker rollouts
complete only after it has brought the database to the newest revision. Do not pass `--wait` to
`helm install`: the hook runs after the release resources are created, and the Pods wait for it.

Perform an authenticated flush, record its assertion ID, replace only the API and worker Pods, and
fetch the same assertion after their replacements become ready. Confirm that the PostgreSQL PVC
remains bound.

## Configure client ingestion guidance

The MCP server sends fixed workflow rules followed by the operator's extraction guidance during
initialization. The packaged default preserves exact substantive details, reproducible procedures
and alternatives, and requires readback of committed records. Customize it in an operator-local
values file (non-secret text only):

```yaml
config:
  ingestionPolicyVersion: "2026-10-01.1"
  ingestionPolicy: |
    Extract every independently useful durable assertion from available context. Preserve exact
    names, versions, quantities, units, parameters and commands. Keep each procedure self-contained
    with prerequisites, ordered steps, validation, outcomes and caveats. Retain decisions,
    alternatives, reasons, uncertainties and provenance. Do not replace detail with a summary.
```

Set `ingestionPolicy: null` to use the policy packaged with the application; empty or whitespace-only
overrides are invalid. Guidance is limited to 8192 characters. The version is a 1–64 character label
that starts with a letter or digit and contains only letters, digits, dots, underscores or hyphens.
The delivered instructions include that label and an automatic SHA-256 digest. Changing guidance
cannot remove the fixed explicit-trigger, secret/transcript exclusion, idempotency, deletion and
readback rules. These are client instructions; server authorization and validation remain enforced
independently. Restrict edits to trusted operators; no MCP tool can update this policy.

Use the release's existing complete operator values file so unrelated deployment settings are
preserved. Build/load the new application image and pin it in that file before the first upgrade
that introduces these settings. Render and validate before upgrading:

```bash
helm template knowledge-vault charts/knowledge-vault -n knowledge-vault \
  -f /OPERATOR/knowledge-vault-values.yaml > /tmp/knowledge-vault-policy-rendered.yaml
kubeconform -strict -summary /tmp/knowledge-vault-policy-rendered.yaml
kubectl apply --dry-run=server -f /tmp/knowledge-vault-policy-rendered.yaml
helm upgrade knowledge-vault charts/knowledge-vault -n knowledge-vault \
  -f /OPERATOR/knowledge-vault-values.yaml
kubectl -n knowledge-vault rollout status deployment/knowledge-vault-knowledge-vault-api --timeout=180s
```

The chart places the settings in its ConfigMap and changes the deployment configuration checksum,
which rolls the API and worker Pods. Pod environment variables are startup snapshots: editing a
ConfigMap alone does not update running Pods. Reconnect CLI/IDE clients and refresh the ChatGPT
plugin connection after rollout; existing sessions can retain earlier instructions. Do not change
policy midway through a flush. The policy version is informational and is not bound to stored
batches. No live policy-update API, database policy table or background watcher is included.

If a client ignores MCP server instructions, install the flush skill or the fallback agent rules
in [client-setup.md](client-setup.md). Instructions cannot grant the server access to unseen chat
history or guarantee that an LLM extracted every detail; readback confirms persistence and supports
client-side comparison.

## Upgrade and removal

Run the migration checks before upgrading. Never delete the PostgreSQL PVC, PV, namespace, or
underlying storage until an encrypted off-host backup has been restored successfully into a
disposable PostgreSQL instance. Follow [backup-restore.md](backup-restore.md) before claiming a
deployment is production-ready.

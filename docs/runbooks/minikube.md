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

Production operators should publish images through their normal registry pipeline and set both a
version and immutable digest in Helm values.

## Create runtime Secrets

Generate token material without printing plaintext into a manifest:

```bash
install -d -m 0700 .local-secrets
TOKEN_PEPPER="$(openssl rand -hex 32)"
uv run python scripts/generate_token.py \
  --principal-id operator-device \
  --scope knowledge:read \
  --scope knowledge:write \
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
cloudflareTunnel:
  enabled: true
  existingSecret: knowledge-vault-cloudflared
  image:
    repository: cloudflare/cloudflared
    tag: "RELEASE_FOR_HUMANS"
    digest: "sha256:VERIFIED_IMMUTABLE_DIGEST"
```

Do not pass secret values through `--set`; the chart accepts only Secret names and key names.

Embedding production Pods must use a pre-populated, read-only model cache. Tests and startup must
never download model weights.

## Verify health and durability

```bash
kubectl -n knowledge-vault get pods,pvc,job
kubectl -n knowledge-vault rollout status statefulset/knowledge-vault-postgresql --timeout=180s
kubectl -n knowledge-vault wait --for=condition=complete job/knowledge-vault-migration --timeout=180s
kubectl -n knowledge-vault rollout status deployment/knowledge-vault-api --timeout=180s
kubectl -n knowledge-vault rollout status deployment/knowledge-vault-worker --timeout=180s
kubectl -n knowledge-vault port-forward service/knowledge-vault 8000:8000
curl --fail http://127.0.0.1:8000/health/ready
```

Perform an authenticated flush, record its assertion ID, replace only the API and worker Pods, and
fetch the same assertion after their replacements become ready. Confirm that the PostgreSQL PVC
remains bound.

## Upgrade and removal

Run the migration checks before upgrading. Never delete the PostgreSQL PVC, PV, namespace, or
underlying storage until an encrypted off-host backup has been restored successfully into a
disposable PostgreSQL instance. Follow [backup-restore.md](backup-restore.md) before claiming a
deployment is production-ready.

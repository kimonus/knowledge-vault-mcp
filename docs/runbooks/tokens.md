# Token generation and rotation

Tokens are opaque random values. Configuration stores only `HMAC-SHA256(pepper, token)`. The
pepper and token records are separate Secret keys but must be rotated deliberately together.

## Issue a direct-client token

Use a unique principal for every device. Direct Codex, IDE, and automation clients normally need
only read and write scopes; reserve a separate admin token for confirmed deletion operations.

```bash
install -d -m 700 ~/.config/knowledge-vault
work_dir="$(mktemp -d)"
trap 'rm -rf "$work_dir"' EXIT

kubectl -n knowledge-vault get secret knowledge-vault-auth \
  -o jsonpath='{.data.token-pepper}' | base64 --decode | \
  uv run python scripts/generate_token.py \
    --principal codex-DEVICE_NAME \
    --pepper-stdin \
    --scopes knowledge:read knowledge:write \
    --token-output ~/.config/knowledge-vault/codex-DEVICE_NAME.token \
    --record-output "$work_dir/record.json"

kubectl -n knowledge-vault get secret knowledge-vault-auth -o json | \
  uv run python scripts/build_token_secret_patch.py --record-file "$work_dir/record.json" | \
  kubectl -n knowledge-vault patch secret knowledge-vault-auth \
    --type=merge --patch-file=/dev/stdin

kubectl -n knowledge-vault rollout restart deployment/knowledge-vault-api
kubectl -n knowledge-vault rollout status deployment/knowledge-vault-api
```

`bootstrap-tokens` holds a JSON array of records. A Secret that was created from a single
generated record file holds one bare object; the service accepts that form and the patch helper
converts it to an array when the next record is added.

The generator creates the token and digest record as owner-only files and refuses to overwrite an
existing token. It does not print the token when `--token-output` is used. Transfer the token to its
device through a password manager or another authenticated encrypted channel, then delete any
temporary transfer copy. The Kubernetes Secret receives only the digest record; the service never
stores the plaintext token.

For a retrieval-only device, omit `knowledge:write`. Add `knowledge:admin` only when issuing a
separate operator token whose loss must be treated as deletion capability.

## Rotate a token without rotating the pepper

Generate a second token using the existing pepper, temporarily configure both digest records,
restart the API, test the new token, remove the old record, and restart again. Revoke the old client
copy. Never log or pass a token on a command line on shared systems; use a protected input mechanism
there.

## Rotate the pepper

Every token digest changes. In a maintenance window, generate a new pepper and new tokens, update
both Secret keys atomically, restart all API replicas, verify new clients, and revoke every old
token. A lost pepper cannot be recovered from stored digests.

On disclosure, remove/revoke first, then inspect content-free request/audit signals. A token with
admin scope must be treated as deletion capability.

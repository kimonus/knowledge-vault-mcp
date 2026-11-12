#!/bin/sh
set -eu
for name in KV_APP_IMAGE KV_GATEWAY_IMAGE; do
    case "$name" in
        KV_APP_IMAGE) value=${KV_APP_IMAGE:-} ;;
        KV_GATEWAY_IMAGE) value=${KV_GATEWAY_IMAGE:-} ;;
    esac
    if printf '%s\n' "$value" | LC_ALL=C grep -Eq '^[a-zA-Z0-9][a-zA-Z0-9./:_-]*@sha256:[0-9a-f]{64}$'; then
        continue
    fi
    if [ "$name" = KV_APP_IMAGE ] && printf '%s\n' "$value" | LC_ALL=C grep -Eq '^sha256:[0-9a-f]{64}$'; then
        continue
    fi
    printf '%s must be pinned by sha256 digest' "$name" >&2
    if [ "$name" = KV_APP_IMAGE ]; then printf ' or be a full local sha256 image ID' >&2; fi
    printf '\n' >&2
    exit 1
done
: "${KV_SECRETS_DIR:?Set KV_SECRETS_DIR to the operator-created secret directory}"
case "$KV_SECRETS_DIR" in /*) ;; *) echo 'KV_SECRETS_DIR must be absolute' >&2; exit 1;; esac
for file in database-url token-pepper bootstrap-tokens postgres-password tls-cert.pem tls-key.pem; do
    if [ -L "$KV_SECRETS_DIR/$file" ]; then
        printf 'Operator file must not be a symlink: %s\n' "$file" >&2
        exit 1
    fi
    if [ ! -f "$KV_SECRETS_DIR/$file" ] || [ ! -s "$KV_SECRETS_DIR/$file" ]; then
        printf 'Missing or empty operator file: %s\n' "$file" >&2
        exit 1
    fi
done
echo 'References present; engine access and contents have not been verified.'

#!/bin/sh
# Pinned isolated deployment-test tool; never starts or changes a cluster.
set -eu
destination=${1:?destination directory required}
mkdir -p "$destination"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT INT TERM
curl --fail --silent --show-error --location \
  https://github.com/kubernetes/minikube/releases/download/v1.39.0/minikube-linux-amd64 \
  --output "$work/minikube"
printf '%s  %s\n' \
  b738496da01be06bbaf80c688f57ce25acd3849fbb518155f3a88e03ef555aa4 \
  "$work/minikube" | sha256sum --check --status
install -m 0755 "$work/minikube" "$destination/minikube"

#!/bin/sh
set -eu

destination="${1:?destination directory is required}"
mkdir -p "${destination}"
work="$(mktemp -d)"
trap 'rm -rf "${work}"' EXIT INT TERM

fetch_checked() {
  url="$1"
  output="$2"
  expected="$3"
  curl --fail --silent --show-error --location "${url}" --output "${work}/${output}"
  actual="$(sha256sum "${work}/${output}" | cut -d ' ' -f 1)"
  test "${actual}" = "${expected}"
}

fetch_checked https://get.helm.sh/helm-v4.2.4-linux-amd64.tar.gz helm.tar.gz \
  c306b46f719b0a4da32d0f78ee21bf90ce8d602f15b22ab753f0674d1670a7f3
tar -xzf "${work}/helm.tar.gz" -C "${work}"
install -m 0755 "${work}/linux-amd64/helm" "${destination}/helm"

fetch_checked \
  https://github.com/yannh/kubeconform/releases/download/v0.8.0/kubeconform-linux-amd64.tar.gz \
  kubeconform.tar.gz 9bc2bffbf71f261128533edaf912153948b7ff238f9a531ae6d34466ec287883
tar -xzf "${work}/kubeconform.tar.gz" -C "${destination}" kubeconform
chmod 0755 "${destination}/kubeconform"

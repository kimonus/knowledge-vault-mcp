#!/bin/sh
set -eu

destination="${1:?destination directory is required}"
mkdir -p "${destination}"
work="$(mktemp -d)"
trap 'rm -rf "${work}"' EXIT INT TERM

download() {
  url="$1"
  output="$2"
  expected="$3"
  curl --fail --silent --show-error --location "${url}" --output "${work}/${output}"
  actual="$(sha256sum "${work}/${output}" | cut -d ' ' -f 1)"
  test "${actual}" = "${expected}"
}

download \
  https://github.com/gitleaks/gitleaks/releases/download/v8.30.1/gitleaks_8.30.1_linux_x64.tar.gz \
  gitleaks.tar.gz 551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb
tar -xzf "${work}/gitleaks.tar.gz" -C "${destination}" gitleaks

download \
  https://github.com/hadolint/hadolint/releases/download/v2.15.1/hadolint-linux-x86_64 \
  hadolint c7187db94eeeeca956519a6af171adc31453941a1e777961f6e680f697c8c507
install -m 0755 "${work}/hadolint" "${destination}/hadolint"

download \
  https://github.com/aquasecurity/trivy/releases/download/v0.74.0/trivy_0.74.0_Linux-64bit.tar.gz \
  trivy.tar.gz 2ae6fe3ee734b7fdf11335663e18c75ea12dccc76062f09f164a3b0f8be4371a
tar -xzf "${work}/trivy.tar.gz" -C "${destination}" trivy

download \
  https://github.com/anchore/syft/releases/download/v1.51.1/syft_1.51.1_linux_amd64.tar.gz \
  syft.tar.gz 8fcb33017a0dc1058298c923c436d19dfa68ae93968e0b423248542e3afb9fc3
tar -xzf "${work}/syft.tar.gz" -C "${destination}" syft

chmod 0755 "${destination}/gitleaks" "${destination}/trivy" "${destination}/syft"

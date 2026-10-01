# ADR 0005: Reference connectivity profile

- Status: Accepted
- Date: 2026-08-30

## Context

Personal assertions must not be placed on an unauthenticated public endpoint. OpenAI clients need
a supported path to an MCP server that may live behind NAT. MCP itself does not mandate a vendor,
gateway, overlay network, or identity provider, and other installations have different network and
compliance constraints.

## Decision

Use the following two-endpoint profile for the maintained homelab deployment:

- a hosted-client endpoint through an outbound-only Cloudflare Tunnel, protected by Cloudflare
  Access Managed OAuth and independently validated Access JWTs at the origin; and
- a DNS-only private endpoint over LAN/WireGuard, protected by unique scoped device bearer tokens.

The chart disables ingress and Cloudflare Tunnel by default. Enabling either requires explicit
hostnames and existing Kubernetes Secrets. The OpenAI Secure MCP Tunnel described in the original
brief is not the selected production transport.

This is a reference deployment decision, not a requirement for every Knowledge Vault installation.
An alternative transport is acceptable when it provides authenticated TLS, independently validated
identity at the origin, equivalent authorization scopes, bounded exposure, and a recovery path. The
device-bearer endpoint must remain private unless a separate public authentication design is
implemented and reviewed.

## Consequences

Hosted web clients can reach the service without entering the operator's WireGuard network.
Private CLI, automation, and recovery clients avoid browser OAuth callbacks. Operators still own
Cloudflare policy, DNS, tunnel routing, TLS for the private ingress, credential rotation, and
off-host recovery. The chart requires the operator to supply a currently scanned, immutable
Cloudflared image digest; the connector receives its token only through an existing Kubernetes
Secret.

Operators using another architecture own its identity lifecycle, availability, auditability,
credential rotation, and threat-model additions. Transport-specific code belongs in an adapter;
domain and service logic must remain independent of the chosen edge.

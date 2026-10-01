# Cloudflare Tunnel and Managed OAuth

This is the maintained **reference connectivity profile**, chosen for a homelab behind NAT and
hosted clients that cannot enter a device-level private network. Cloudflare is not required by MCP
or Knowledge Vault. Another deployment may use a different authenticated HTTPS gateway, overlay,
identity provider, or managed MCP transport if it preserves the security invariants in the
[threat model](../threat-model.md).

This profile publishes `https://PUBLIC_MCP_HOST/mcp` through an outbound-only Cloudflare Tunnel and
protects it with Cloudflare Access Managed OAuth plus an exact-identity policy. The origin also
validates every `Cf-Access-Jwt-Assertion` signature, issuer, audience, expiry, and allowed email.

Keep `https://PRIVATE_MCP_HOST/mcp` DNS-only and reachable solely over LAN/WireGuard. It uses
scoped per-device bearer tokens and must never be proxied publicly.

## Create the tunnel without a route

In Cloudflare Zero Trust, open **Networking → Tunnels**, create a Cloudflared tunnel, choose the
appropriate connector environment, and copy the tunnel token into a secret manager. Do not add a
public hostname until the Access policy exists.

Never put the tunnel token in Git, shell history, Deployment arguments, a ConfigMap, or Helm
values.

## Configure an identity provider

For Google:

1. Find the Cloudflare team domain, `https://TEAM.cloudflareaccess.com`.
2. Create a Google OAuth client of type **Web application**.
3. Authorize the JavaScript origin `https://TEAM.cloudflareaccess.com`.
4. Authorize the redirect URI
   `https://TEAM.cloudflareaccess.com/cdn-cgi/access/callback`.
5. Add the Google provider in Cloudflare, enable PKCE, save, and run the provider test.

Google establishes identity; the Access application policy establishes authorization.

## Create the Access application

Create an MCP or self-hosted application with:

- destination: `PUBLIC_MCP_HOST`, without a path restriction;
- identity provider: the configured provider;
- allow policy: an exact user email or equivalent exact identity selector;
- no `Everyone`, email-domain wildcard, bypass, or reusable service-token rule;
- an appropriately bounded session duration.

Enable Managed OAuth and configure:

- dynamic client registration;
- localhost and loopback clients only if direct OAuth clients need them;
- hosted-client redirect URIs required by the clients being used;
- short-lived access tokens, normally 10–20 minutes;
- refresh/grant duration matching the Access session policy.

For ChatGPT developer mode, permit its documented hosted callback pattern. Review current client
documentation before broadening redirect patterns. Copy the application's Audience (`AUD`) tag.

## Create Kubernetes Secrets

The running application needs:

- Cloudflare team issuer URL;
- application AUD;
- exact allowed identity list;
- tunnel token for the Cloudflared Deployment.

Create these through an operator-controlled secret workflow. Example names used by the chart are
configured through `cloudflareAccess.existingSecret` and `cloudflareTunnel.existingSecret`. The
tunnel Secret must contain the token under `cloudflareTunnel.tokenKey` (default: `token`). Do not
commit rendered Secrets.

Enable the connector with operator-local values:

```yaml
cloudflareTunnel:
  enabled: true
  existingSecret: knowledge-vault-cloudflared
  image:
    repository: cloudflare/cloudflared
    tag: "RELEASE_FOR_HUMANS"
    digest: "sha256:VERIFIED_IMMUTABLE_DIGEST"
```

Before deployment, scan that exact image and reject known fixable HIGH or CRITICAL findings. The
chart refuses to enable the tunnel without an immutable digest, runs Cloudflared without root or a
service-account token, and limits tunnel egress to DNS, the API Pod, and Cloudflare transport port
7844. The chart intentionally does not ship a release digest because upstream security state
changes independently of this project.

## Deploy before publishing DNS

Deploy the API and Cloudflared connector first. Confirm:

- API and tunnel Pods are ready;
- the origin Service remains `ClusterIP`;
- the private LAN/WireGuard endpoint still requires a device bearer token;
- origin configuration fails closed when issuer, audience, or allowed identities are missing.

## Publish the protected route

Add a tunnel-published application route:

- hostname: `PUBLIC_MCP_HOST`;
- service: `http://knowledge-vault:8000` or the chart's actual ClusterIP service DNS name;
- HTTP Host header: leave unchanged;
- Access JWT validation: enabled;
- Access application: the exact MCP application created above.

Tunnel validation is defense in depth. Never remove origin-side JWT validation.

From outside the private network:

```bash
curl --include https://PUBLIC_MCP_HOST/mcp
curl --fail https://PUBLIC_MCP_HOST/.well-known/oauth-authorization-server
```

The MCP request must return `401` with OAuth discovery, not origin content or an unprotected
redirect. Complete a client authorization and verify the expected application, identity, and allow
policy in Cloudflare Access logs.

## Rollback

Disable or remove the tunnel's published application route. Continue using the private endpoint
over LAN/WireGuard while diagnosing. Do not point the public hostname directly at the private
ingress because that bypasses the Access boundary.

## References

- [Cloudflare Managed OAuth](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/managed-oauth/)
- [Cloudflare Access JWT validation](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/authorization-cookie/validating-json/)
- [Cloudflare Tunnel on Kubernetes](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/deployment-guides/kubernetes/)
- [Cloudflare Google identity provider](https://developers.cloudflare.com/cloudflare-one/integrations/identity-providers/google/)

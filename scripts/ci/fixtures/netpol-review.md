# Synthetic policy-only compatibility rehearsal

This branch-only manual CI rehearsal is an operational experiment, not a Dashboard dependency or
production deployment. It refuses other contexts/nodes and existing fixture namespaces. It uses
no production credentials, database, source URLs or operator knowledge. Existing application CI
jobs remain unchanged and run alongside it.

The pinned upstream kube-router v2.12.0 firewall-only configuration disables routing, service
proxying and CNI installation. The fixture additionally drops all capabilities except NET_ADMIN
and NET_RAW (needed by iptables/libipset),
uses a read-only root filesystem without host PID, bounds resources and gives the service account
only read/list/watch permissions. These changes require actual compatibility testing; upstream
defaults alone are not evidence that this reduced configuration works.
The first run exposed a required NET_RAW capability: without it libipset could not open its
socket, rule programming failed and the real denial assertion correctly refused acceptance.
The corrected capability set remains explicit; broad privileged mode is not restored.

The upstream image's main controller, iptables wrapper and Alpine package inventory scanned clean
under the fixed HIGH/CRITICAL gate. Its separately compiled CNI helpers and BGP debug CLI contain
fixable Go vulnerabilities. The review Dockerfile omits those unused executables and flattens the
final filesystem so their vulnerable files/layers are absent. It preserves the actual controller
and packet tools and is scanned independently. No vulnerability exclusion or VEX waiver is used.
The artifact is built for review by default. A separate owner-only manual publication input is
disabled by default; it requires an explicitly named immutable reviewed commit and all seven
verification jobs. It publishes to a separate infrastructure package and keylessly signs its
digest and runtime inventory. It downloads the exact tested image from the same workflow run and
attempt, checks its source revision, and never rebuilds it for publication. Tags include the
source commit, run ID and attempt so a retry never replaces an earlier published image. The
agent does not invoke this publication input: the maintainer
owns publication. The default experiment never publishes or authorizes installation. A deployment
needs a reviewed published immutable digest,
trusted signatures/inventory and operator approval for the cluster-wide policy effect.

The test uses bridge/portmap/firewall, existing synthetic API/database Pods, controlled HTTP/HTTPS
destinations and namespace/Pod selectors. It checks failed enforcement before the controller,
positive and negative controls afterwards, DNS, unchanged CNI configuration and unchanged existing
Pod UIDs/restarts, Service ClusterIP paths and dynamic CIDR exceptions. Scoped rollback detaches
only references into reserved netpol filter chains,
then flushes/deletes only those chains in a --noflush transaction. It never invokes upstream
--cleanup-config: that function also flushes all IPVS service definitions. An unrelated sentinel
chain and ipset, DNS and post-rollback connectivity must survive. Reserved chains and ipsets must
be absent before installation; malformed snapshots fail closed. Only newly created reserved
policy ipsets are destroyed after their chains disappear. A production deployment still needs
a controller image trusted for that environment.

The optional publisher authenticates to GHCR only in its own job, with scoped package/OIDC
permissions; no credential enters a build layer. Verify the certificate identity against this
exact workflow/operational branch and its GitHub workflow SHA extension against the reviewed
commit, alongside the digest and runtime SBOM. The first GHCR package may be private: its owner
must explicitly make it publicly pullable or approve a dedicated image-pull credential before
any deployment. This package is independent of the three application release images and never
changes their tags, the release version, core manifests, DNS, Secrets or a Kubernetes cluster.

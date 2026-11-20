{{- define "knowledge-vault.dashboard.labels" -}}
{{ include "knowledge-vault.labels" . }}
app.kubernetes.io/component: dashboard
{{- end -}}
{{- define "knowledge-vault.dashboard.selector" -}}
{{ include "knowledge-vault.selectorLabels" . }}
app.kubernetes.io/component: dashboard
{{- end -}}
{{- define "knowledge-vault.dashboard.canonicalHost" -}}
{{- regexReplaceAll ":[0-9]+$" (lower .) "" | trimSuffix "." -}}
{{- end -}}
{{- define "knowledge-vault.dashboard.requirements" -}}
{{- $d := .Values.dashboard -}}
{{- if not .Values.networkPolicy.enabled }}{{ fail "Dashboard requires networkPolicy.enabled" }}{{ end -}}
{{- if or (not $d.host) (not $d.coreHost) (not $d.image.digest) (not $d.serviceTokenSecret.name) }}{{ fail "Dashboard requires host, coreHost, immutable image digest and serviceTokenSecret.name" }}{{ end -}}
{{- $host := include "knowledge-vault.dashboard.canonicalHost" $d.host -}}
{{- $coreHost := include "knowledge-vault.dashboard.canonicalHost" $d.coreHost -}}
{{- $coreHosts := concat (list $coreHost .Values.ingress.host ((urlParse .Values.config.publicBaseUrl).hostname) ((urlParse .Values.config.authIssuerUrl).hostname)) .Values.cloudflareAccess.publicHosts .Values.cloudflareAccess.privateHosts -}}
{{- range $candidate := $coreHosts -}}
{{- if eq $host (include "knowledge-vault.dashboard.canonicalHost" (default "" $candidate)) }}{{ fail "Dashboard hostname must be distinct from every core/MCP hostname" }}{{ end -}}
{{- end -}}
{{- if .Values.cloudflareAccess.enabled -}}
{{- $privateHosts := list -}}
{{- range .Values.cloudflareAccess.privateHosts -}}{{- $privateHosts = append $privateHosts (include "knowledge-vault.dashboard.canonicalHost" .) -}}{{- end -}}
{{- if not (has $coreHost $privateHosts) }}{{ fail "Dashboard coreHost must be a configured core private host when Cloudflare Access is enabled" }}{{ end -}}
{{- end -}}
{{- $forbidden := list .Values.auth.existingSecret .Values.database.existingSecret .Values.internalPostgresql.existingSecret .Values.backup.existingSecret .Values.cloudflareTunnel.existingSecret .Values.cloudflareAccess.existingSecret .Values.watchdog.webhook.existingSecret -}}
{{- $secrets := list $d.serviceTokenSecret.name -}}
{{- if eq $d.mode "privateSession" -}}
{{- if or (not $d.privateSession.existingSecret) (not $d.privateSession.signingSecret) }}{{ fail "Dashboard privateSession requires browser credential and separate signing Secrets" }}{{ end -}}
{{- if or (eq $d.privateSession.existingSecret $d.privateSession.signingSecret) (eq $d.serviceTokenSecret.name $d.privateSession.existingSecret) (eq $d.serviceTokenSecret.name $d.privateSession.signingSecret) }}{{ fail "Dashboard service, browser credential and session signing Secrets must be distinct" }}{{ end -}}
{{- $secrets = concat $secrets (list $d.privateSession.existingSecret $d.privateSession.signingSecret) -}}
{{- if or $d.access.issuerUrl $d.access.audience $d.access.allowedEmails $d.access.jwksEgressCidrs }}{{ fail "privateSession must not configure Access" }}{{ end -}}
{{- else -}}
{{- if or $d.privateSession.existingSecret $d.privateSession.signingSecret }}{{ fail "Access mode must not configure private fallback Secrets" }}{{ end -}}
{{- if or (not $d.access.issuerUrl) (not $d.access.audience) (not $d.access.allowedEmails) (not $d.access.jwksEgressCidrs) }}{{ fail "Dashboard Access requires issuerUrl, audience, allowedEmails and explicit JWKS egress CIDRs" }}{{ end -}}
{{- range $cidr := $d.access.jwksEgressCidrs -}}
{{- $parts := splitList "/" $cidr -}}{{- $octets := splitList "." (first $parts) -}}
{{- $first := atoi (index $octets 0) -}}{{- $second := atoi (index $octets 1) -}}{{- $prefix := atoi (last $parts) -}}
{{- range $octet := $octets -}}{{- if gt (atoi $octet) 255 }}{{ fail "Dashboard JWKS egress requires valid public IPv4 CIDRs" }}{{ end -}}{{- end -}}
{{- if or (eq $first 0) (eq $first 10) (eq $first 127) (ge $first 224) (and (eq $first 100) (or (lt $prefix 16) (and (ge $second 64) (le $second 127)))) (and (eq $first 169) (or (lt $prefix 16) (eq $second 254))) (and (eq $first 172) (or (lt $prefix 12) (and (ge $second 16) (le $second 31)))) (and (eq $first 192) (or (lt $prefix 16) (eq $second 168))) }}{{ fail "Dashboard JWKS egress must exclude private, loopback, link-local and non-unicast ranges" }}{{ end -}}
{{- end -}}
{{- end -}}
{{- range $secret := $secrets -}}
{{- if has $secret $forbidden }}{{ fail "Dashboard must not mount a core/database/tunnel/backup Secret" }}{{ end -}}
{{- end -}}
{{- if and $d.ingress.enabled (or (not $d.ingress.className) (not $d.ingress.tlsSecret) (not $d.networkPolicy.ingressFrom)) }}{{ fail "Dashboard ingress requires className, tlsSecret and explicit controller ingressFrom peers" }}{{ end -}}
{{- if and $d.networkPolicy.allowCloudflared (or (not .Values.cloudflareTunnel.enabled) (ne $d.mode "cloudflareAccess")) }}{{ fail "Dashboard tunnel peer requires the existing tunnel enabled and cloudflareAccess mode" }}{{ end -}}
{{- end -}}
{{- define "knowledge-vault.dashboard.config" -}}
{{- $d := .Values.dashboard -}}
{{- $config := dict "enabled" true "host" $d.host "core_host" $d.coreHost "core_url" (printf "http://%s.%s.svc.cluster.local:%d" (include "knowledge-vault.fullname" .) .Release.Namespace (int .Values.service.port)) "mode" $d.mode "service_token_file" "/run/secrets/service/token" -}}
{{- if eq $d.mode "privateSession" -}}
{{- $_ := set $config "pepper_file" "/run/secrets/browser/pepper" -}}
{{- $_ := set $config "records_file" "/run/secrets/browser/records" -}}
{{- $_ := set $config "signing_key_file" "/run/secrets/session/key" -}}
{{- else -}}
{{- $_ := set $config "access_issuer" $d.access.issuerUrl -}}
{{- $_ := set $config "access_audience" $d.access.audience -}}
{{- $_ := set $config "access_emails" $d.access.allowedEmails -}}
{{- end -}}
{{- $config | mustToJson -}}
{{- end -}}

{{- define "knowledge-vault.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "knowledge-vault.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name (include "knowledge-vault.name" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{- define "knowledge-vault.labels" -}}
app.kubernetes.io/name: {{ include "knowledge-vault.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- end -}}

{{- define "knowledge-vault.selectorLabels" -}}
app.kubernetes.io/name: {{ include "knowledge-vault.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "knowledge-vault.image" -}}
{{- if .digest -}}{{ printf "%s@%s" .repository .digest }}{{- else -}}{{ printf "%s:%s" .repository .tag }}{{- end -}}
{{- end -}}

{{- define "knowledge-vault.requirements" -}}
{{- if not .Values.auth.existingSecret }}{{ fail "auth.existingSecret is required" }}{{ end -}}
{{- if not .Values.database.existingSecret }}{{ fail "database.existingSecret is required" }}{{ end -}}
{{- if not .Values.config.publicBaseUrl }}{{ fail "config.publicBaseUrl is required" }}{{ end -}}
{{- if not .Values.config.authIssuerUrl }}{{ fail "config.authIssuerUrl is required" }}{{ end -}}
{{- if and .Values.cloudflareAccess.enabled (not .Values.cloudflareAccess.existingSecret) }}{{ fail "cloudflareAccess.existingSecret is required when Cloudflare Access is enabled" }}{{ end -}}
{{- if and .Values.internalPostgresql.enabled (not .Values.internalPostgresql.existingSecret) }}{{ fail "internalPostgresql.existingSecret is required when enabled" }}{{ end -}}
{{- if and .Values.cloudflareTunnel.enabled (or (not .Values.cloudflareTunnel.existingSecret) (not .Values.cloudflareTunnel.image.repository) (not .Values.cloudflareTunnel.image.digest)) }}{{ fail "cloudflareTunnel existingSecret, image repository, and immutable digest are required when Cloudflare Tunnel is enabled" }}{{ end -}}
{{- if and .Values.ingress.enabled (or (not .Values.ingress.host) (not .Values.ingress.tlsSecret)) }}{{ fail "ingress host and tlsSecret are required when enabled" }}{{ end -}}
{{- if and .Values.backup.enabled (not .Values.backup.existingSecret) }}{{ fail "backup.existingSecret is required when backup is enabled" }}{{ end -}}
{{- end -}}

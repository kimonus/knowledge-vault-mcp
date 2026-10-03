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
{{- if and .Values.cloudflareAccess.enabled (not .Values.cloudflareAccess.publicHosts) }}{{ fail "cloudflareAccess.publicHosts must list the published hostname(s) when Cloudflare Access is enabled" }}{{ end -}}
{{- if and .Values.cloudflareAccess.enabled (not .Values.cloudflareAccess.privateHosts) }}{{ fail "cloudflareAccess.privateHosts must list the device-bearer hostname(s) when Cloudflare Access is enabled, so bearer tokens are not accepted for every unpublished Host" }}{{ end -}}
{{- if and .Values.ingress.enabled (or (not .Values.ingress.host) (not .Values.ingress.tlsSecret) (not .Values.ingress.className) (not .Values.ingress.paths)) }}{{ fail "ingress host, tlsSecret, className, and paths are required when enabled" }}{{ end -}}
{{- if and .Values.backup.enabled (not .Values.backup.existingSecret) }}{{ fail "backup.existingSecret is required when backup is enabled" }}{{ end -}}
{{- if and .Values.backup.databaseFromInternalPostgresql (not .Values.internalPostgresql.enabled) }}{{ fail "backup.databaseFromInternalPostgresql requires internalPostgresql.enabled" }}{{ end -}}
{{- end -}}

{{/* Wait until the database is at the newest revision shipped in this image. */}}
{{- define "knowledge-vault.waitForMigration" -}}
- name: wait-for-migration
  image: {{ include "knowledge-vault.image" .Values.image }}
  imagePullPolicy: {{ .Values.image.pullPolicy }}
  command: ["/bin/sh", "-ec"]
  args: ["until alembic current 2>/dev/null | grep -q '(head)'; do sleep 2; done"]
  env:
    - name: KNOWLEDGE_VAULT_ENVIRONMENT
      value: development
    - name: KNOWLEDGE_VAULT_DATABASE_URL
      valueFrom: {secretKeyRef: {name: {{ .Values.database.existingSecret | quote }}, key: {{ .Values.database.urlKey | quote }}}}
  securityContext: {allowPrivilegeEscalation: false, readOnlyRootFilesystem: true, capabilities: {drop: ["ALL"]}}
  resources: {{- toYaml .Values.resources.init | nindent 4 }}
{{- end -}}

{{/* Model weights are read from the mounted cache only; Pods never contact the model hub. */}}
{{- define "knowledge-vault.modelEnv" -}}
- {name: HF_HUB_OFFLINE, value: "1"}
- {name: TRANSFORMERS_OFFLINE, value: "1"}
{{- if .Values.modelCache.existingClaim }}
- {name: HF_HOME, value: /models}
{{- end }}
{{- end -}}

{{- define "knowledge-vault.scheduling" -}}
{{- with .Values.nodeSelector }}
nodeSelector: {{- toYaml . | nindent 2 }}
{{- end }}
{{- with .Values.tolerations }}
tolerations: {{- toYaml . | nindent 2 }}
{{- end }}
{{- with .Values.affinity }}
affinity: {{- toYaml . | nindent 2 }}
{{- end }}
{{- end -}}

{{- define "knowledge-vault.egressDns" -}}
- to:
    - namespaceSelector: {}
  ports:
    - {protocol: UDP, port: 53}
    - {protocol: TCP, port: 53}
{{- end -}}

{{- define "knowledge-vault.egressDatabase" -}}
{{- if .Values.internalPostgresql.enabled }}
- to:
    - podSelector:
        matchLabels:
          {{- include "knowledge-vault.selectorLabels" . | nindent 10 }}
          app.kubernetes.io/component: postgresql
  ports: [{protocol: TCP, port: 5432}]
{{- else }}
- ports: [{protocol: TCP, port: 5432}]
{{- end }}
{{- end -}}

{{/* Additional non-secret KNOWLEDGE_VAULT_* settings for the API and worker. */}}
{{- define "knowledge-vault.extraEnv" -}}
{{- range $name, $value := .Values.extraEnv }}
- {name: {{ $name | quote }}, value: {{ $value | quote }}}
{{- end }}
{{- end -}}

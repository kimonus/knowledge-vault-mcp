# Monitoring without a monitoring stack

Temporal resolution exposes `knowledge_vault_temporal_resolutions_total` with fixed outcomes
resolved/ambiguous/conflict/unknown/overflow. These count lookups, not unique conflicting properties.
Inspect reasons through authenticated resolution and use temporal diagnostics for metadata
coverage. Overflow returns no partial winner. No asynchronous projection or new duty needs a
watchdog heartbeat. See [temporal limits](../temporal-knowledge.md).

Knowledge Vault exposes Prometheus metrics, but a single-node personal deployment often has no
Prometheus or Alertmanager. The watchdog covers the failures that matter there: it makes them
visible in the cluster and can notify you.

## What is watched

Background duties record a heartbeat in the database when they succeed:

| Heartbeat | Written by | Reported as a problem when |
|---|---|---|
| `worker` | the worker, after each maintenance pass (every five minutes by default) | older than `watchdog.workerMaxAgeSeconds` (15 minutes) or missing |
| `backup` | the backup job, after a snapshot is written, retention is applied, and `restic check` passes | `backup.enabled` and older than `watchdog.backupMaxAgeSeconds` (36 hours) or missing |

The watchdog also reports embedding jobs in the `dead` state.

Heartbeat ages are returned by `get_knowledge_statistics` and `/api/v1/statistics` under
`operations`, and exported as `knowledge_vault_heartbeat_age_seconds{name}` on the API's
`/metrics`. They contain no assertion content.

## Enable the watchdog

```yaml
watchdog:
  enabled: true
```

A CronJob runs `knowledge-vault-watchdog` every fifteen minutes. A run that finds a problem
fails, so `kubectl -n knowledge-vault get jobs` shows it even with no notification configured:

```bash
kubectl -n knowledge-vault get cronjob,jobs
kubectl -n knowledge-vault logs job/NAME_OF_FAILED_JOB
```

The log line `watchdog_evaluated` lists the problem codes: `worker_stale`, `backup_stale`,
`embedding_jobs_dead`.

A container's log is gone once the node restarts or removes the container, while the failed Job
stays. The end of a failed run's log is also kept in the Pod status:

```bash
kubectl -n knowledge-vault get pods -l app.kubernetes.io/component=watchdog \
  -o custom-columns='POD:.metadata.name,MESSAGE:.status.containerStatuses[0].state.terminated.message'
```

A run that has no `watchdog_evaluated` line failed before it checked anything, usually because
the database was unreachable. A run more than two minutes late, such as one missed while the
cluster was down, is skipped rather than started while the node is still booting.

## Notifications

Put the notification URL in a Secret—it usually embeds a token—and reference it:

```bash
read -rs WEBHOOK_URL
kubectl -n knowledge-vault create secret generic knowledge-vault-alerts \
  --from-literal=url="$WEBHOOK_URL"
unset WEBHOOK_URL
```

```yaml
watchdog:
  enabled: true
  webhook:
    existingSecret: knowledge-vault-alerts
    format: json   # or: text
```

- `json` posts `{"title": …, "message": …, "problems": [codes]}`; this suits Home Assistant
  webhooks and generic automation endpoints.
- `text` posts the message as the body with a `Title` header; this suits ntfy topics.

A notification is sent when the set of problems changes, again when everything recovers, and as a
reminder once a day while a problem persists. A delivery that fails is retried on the next run.
Messages state which duty is late and never contain assertion content.

The watchdog's NetworkPolicy allows egress on TCP 443 through `networkPolicy.watchdogEgress`;
adjust it for an in-cluster or plain-HTTP target.

## Responding

- `worker_stale`: check the worker Pod and its logs; a `worker_iteration_failed` event names the
  exception type. The API keeps serving and search falls back to text.
- `backup_stale`: inspect the last backup Job. For a host-path repository, the usual cause is an
  unmounted drive: the Pod then fails to start with a missing-directory event.
- `embedding_jobs_dead`: fix the cause (see [troubleshooting.md](troubleshooting.md)), then
  requeue with `knowledge-vault-reembed`.
# Usage telemetry (0.5.0)

knowledge_vault_usage_events_total{outcome="failed"} counts optional accounting failures. Reads
continue with usage.available=false; those zero counters are not authoritative. Inspect database
lock pressure before retrying. Disable normal accounting via config.usageTrackingEnabled if needed.
Worker maintenance purges expired retry/audit records before recording its existing heartbeat.

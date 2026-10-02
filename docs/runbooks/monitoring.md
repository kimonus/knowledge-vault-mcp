# Monitoring without a monitoring stack

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

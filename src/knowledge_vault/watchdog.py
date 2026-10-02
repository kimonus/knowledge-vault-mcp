"""Periodic health evaluation for deployments without a monitoring stack.

The watchdog reads only content-free operational state (heartbeats and job counters), exits
non-zero while something is wrong so a failed Job is visible in the cluster, and can post a short
notification to an operator-supplied webhook when the set of problems changes.
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx
import structlog

from knowledge_vault.config import Settings, get_settings
from knowledge_vault.container import Container, build_container
from knowledge_vault.observability.logging import configure_logging, describe_exception
from knowledge_vault.services.operations import WATCHDOG

Notifier = Callable[[str, str, list[str]], Awaitable[None]]
_HEALTHY = "ok"


@dataclass(frozen=True, slots=True)
class Finding:
    code: str
    message: str


def _stale(age: float | None, limit: int) -> bool:
    return age is None or age > limit


async def evaluate(container: Container) -> list[Finding]:
    settings = container.settings
    statistics = await container.administration.statistics()
    operations = statistics["operations"]
    findings: list[Finding] = []
    if _stale(operations["worker_heartbeat_age_seconds"], settings.watchdog_worker_max_age_seconds):
        findings.append(
            Finding(
                "worker_stale",
                "the worker has not completed a maintenance pass within "
                f"{settings.watchdog_worker_max_age_seconds} seconds",
            )
        )
    if settings.watchdog_expect_backup and _stale(
        operations["backup_heartbeat_age_seconds"], settings.watchdog_backup_max_age_seconds
    ):
        findings.append(
            Finding(
                "backup_stale",
                "no backup has succeeded within "
                f"{settings.watchdog_backup_max_age_seconds} seconds",
            )
        )
    dead_jobs = int(statistics["embedding_jobs"].get("dead", 0))
    if dead_jobs:
        findings.append(Finding("embedding_jobs_dead", f"{dead_jobs} embedding jobs are dead"))
    return findings


def webhook_notifier(settings: Settings) -> Notifier | None:
    if not settings.alert_webhook_url:
        return None

    async def notify(title: str, message: str, codes: list[str]) -> None:
        async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
            if settings.alert_webhook_format == "text":
                response = await client.post(
                    settings.alert_webhook_url, content=message, headers={"Title": title}
                )
            else:
                response = await client.post(
                    settings.alert_webhook_url,
                    json={"title": title, "message": message, "problems": codes},
                )
            response.raise_for_status()

    return notify


async def run(container: Container, notify: Notifier | None) -> int:
    """Evaluate once. Return 1 while there are problems, otherwise 0."""
    logger = structlog.get_logger()
    findings = await evaluate(container)
    codes = sorted(finding.code for finding in findings)
    signature = ",".join(codes) or _HEALTHY
    previous = (await container.operations.heartbeats()).get(WATCHDOG)
    changed = previous is None or previous.detail != signature
    reminder_due = (
        previous is not None
        and bool(findings)
        and previous.age_seconds > container.settings.watchdog_renotify_seconds
    )
    # The first evaluation of a healthy system is not news.
    announce = (changed and not (previous is None and not findings)) or reminder_due
    logger.info("watchdog_evaluated", operation="watchdog", problems=codes, announced=announce)
    if announce and notify is not None:
        if findings:
            title = f"Knowledge Vault: {len(findings)} problem(s)"
            message = "; ".join(finding.message for finding in findings)
        else:
            title = "Knowledge Vault: recovered"
            message = "all watchdog checks pass again"
        try:
            await notify(title, message, codes)
        except Exception as exc:
            # Leave the stored state untouched so the next run tries to deliver it again.
            logger.error("watchdog_notification_failed", **describe_exception(exc))
            return 1
    if changed or reminder_due:
        await container.operations.beat(WATCHDOG, signature)
    return 1 if findings else 0


async def _main() -> int:
    settings = get_settings()
    configure_logging(settings.log_level, service=settings.service_name, version=settings.version)
    container = build_container(settings)
    try:
        return await run(container, webhook_notifier(settings))
    finally:
        await container.database.close()


def run_watchdog() -> None:
    raise SystemExit(asyncio.run(_main()))

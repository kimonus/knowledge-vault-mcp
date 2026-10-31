"""Run source-first gold cases against an isolated Knowledge Vault service container."""

import hashlib
import json
import math
import os
import platform
import random
import time
from collections import defaultdict
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, text

from knowledge_vault.container import Container
from knowledge_vault.domain.enums import EmbeddingState
from knowledge_vault.domain.temporal import ResolutionRequest
from knowledge_vault.embeddings.providers import (
    DeterministicFakeProvider,
    SentenceTransformerProvider,
)
from knowledge_vault.persistence.tables import AssertionRow
from knowledge_vault.services.search import SearchService
from knowledge_vault.worker.jobs import EmbeddingWorker

from .corpus import Case


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = math.ceil(percentile * len(ordered)) - 1
    return ordered[max(0, index)]


def _ndcg(ranked: list[str], relevant: set[str], k: int = 10) -> float:
    if not relevant:
        return 0.0
    dcg = sum(1.0 / math.log2(rank + 2) for rank, key in enumerate(ranked[:k]) if key in relevant)
    ideal = sum(1.0 / math.log2(rank + 2) for rank in range(min(k, len(relevant))))
    return dcg / ideal


def _corpus_digest(cases: tuple[Case, ...]) -> str:
    payload = json.dumps([asdict(case) for case in cases], sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


def _bootstrap_interval(values: list[float]) -> list[float] | None:
    """Fixed-seed case bootstrap; diagnostic only for template-derived cases."""
    if not values:
        return None
    rng = random.Random(0)  # noqa: S311 - reproducible resampling, not a security primitive
    sample_count = len(values)
    estimates = sorted(sum(rng.choices(values, k=sample_count)) / sample_count for _ in range(2000))
    return [round(estimates[49], 4), round(estimates[1949], 4)]


async def run_benchmark(
    container: Container,
    cases: tuple[Case, ...],
    *,
    mode: str = "text",
    distractors: int = 0,
) -> dict[str, Any]:
    """Seed only a disposable DB, then score retrieval and temporal resolution.

    The caller owns the disposable database lifecycle. No production URL is accepted here by
    the CLI. Extraction and answer quality need separately reviewed client observations.
    """
    if mode not in {"text", "fake", "local"}:
        raise ValueError("mode must be text, fake, or local")
    if not cases or not 0 <= distractors <= 1000:
        raise ValueError("cases must be nonempty and distractors must be between 0 and 1000")

    keys: dict[UUID, str] = {}
    for case in cases:
        if not case.claims:
            continue
        batch = await container.ingestion.begin(
            "test-user", f"quality-{case.id}", 1, len(case.claims)
        )
        payloads = [claim.payload for claim in case.claims]
        accepted, rejected, _ = await container.ingestion.append("test-user", batch.id, 1, payloads)
        if accepted != len(payloads) or rejected:
            raise AssertionError(f"fixture {case.id} was rejected: {[r.code for r in rejected]}")
        result = await container.ingestion.commit("test-user", batch.id)
        if len(result.items) != len(case.claims):
            raise AssertionError(f"fixture {case.id} did not commit every gold claim")
        for claim, item in zip(case.claims, result.items, strict=True):
            if item.assertion_id in keys:
                raise AssertionError(f"fixture {case.id} reused another gold assertion")
            keys[item.assertion_id] = claim.key

    if distractors:
        for start in range(0, distractors, 100):
            length = min(100, distractors - start)
            batch = await container.ingestion.begin(
                "test-user", f"quality-distractors-{start}", 1, length
            )
            payloads = [
                {
                    "content": (
                        f"Synthetic unrelated catalogue note {number}: an ordinary blue folder."
                    ),
                    "kind": "external_fact",
                    "origin": "external_source",
                }
                for number in range(start, start + length)
            ]
            accepted, rejected, _ = await container.ingestion.append(
                "test-user", batch.id, 1, payloads
            )
            if accepted != length or rejected:
                raise AssertionError("distractor fixture was rejected")
            await container.ingestion.commit("test-user", batch.id)

    if mode == "text":
        settings = container.settings.model_copy(update={"embeddings_enabled": False})
        search = SearchService(container.database.sessions, settings, None)
        provider_name = "none"
    else:
        if mode == "fake":
            provider = DeterministicFakeProvider(dimensions=container.settings.embedding_dimensions)
            provider_name = "deterministic-fake-not-semantic"
        else:
            provider = SentenceTransformerProvider(
                container.settings.embedding_model,
                container.settings.embedding_dimensions,
                allow_download=False,
            )
            provider_name = container.settings.embedding_model
        worker = EmbeddingWorker(container.database.sessions, container.settings, provider)
        # Fail closed on a missing local model instead of reporting text fallback as a semantic run.
        await provider.embed(["quality model availability probe"])
        while await worker.process_once():
            pass
        search = SearchService(container.database.sessions, container.settings, provider)

    rows: list[dict[str, Any]] = []
    by_category: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    by_split: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for case in cases:
        row: dict[str, Any] = {"id": case.id, "category": case.category, "split": case.split}
        if case.query is not None:
            start = time.perf_counter()
            page = await search.search(case.query, limit=10, purpose="maintenance")
            latency = (time.perf_counter() - start) * 1000
            found = [keys.get(hit.assertion.id, "unrelated") for hit in page.results]
            relevant = set(case.relevant)
            recall = len(relevant.intersection(found)) / len(relevant) if relevant else None
            ndcg = _ndcg(found, relevant) if relevant else None
            empty_correct = len(found) == 0 if not relevant else None
            row["retrieval"] = {
                "found_keys": found,
                "required_keys": sorted(relevant),
                "recall_at_10": recall,
                "ndcg_at_10": ndcg,
                "empty_result_correct": empty_correct,
                "latency_ms": round(latency, 3),
                "embedding_degraded": page.embedding_degraded,
            }
            if recall is not None:
                by_category[case.category]["recall_at_10"].append(recall)
                by_category[case.category]["ndcg_at_10"].append(ndcg or 0.0)
                by_split[case.split]["recall_at_10"].append(recall)
                by_split[case.split]["ndcg_at_10"].append(ndcg or 0.0)
            if empty_correct is not None:
                by_category[case.category]["empty_result_accuracy"].append(float(empty_correct))
                by_split[case.split]["empty_result_accuracy"].append(float(empty_correct))
            by_category[case.category]["latency_ms"].append(latency)
            by_split[case.split]["latency_ms"].append(latency)
        if case.state is not None:
            actual = await container.temporal.resolve(
                ResolutionRequest.model_validate(case.state.request)
            )
            correct = actual.outcome == case.state.outcome and set(actual.values) == set(
                case.state.values
            )
            row["temporal"] = {
                "expected_outcome": case.state.outcome,
                "actual_outcome": actual.outcome,
                "correct": correct,
            }
            by_category[case.category]["temporal_correct"].append(float(correct))
            by_split[case.split]["temporal_correct"].append(float(correct))
        rows.append(row)

    async with container.database.sessions() as session:
        migration = await session.scalar(text("SELECT version_num FROM alembic_version"))
        total_assertions = await session.scalar(select(func.count()).select_from(AssertionRow))
        ready_embeddings = await session.scalar(
            select(func.count())
            .select_from(AssertionRow)
            .where(
                AssertionRow.embedding_state == EmbeddingState.READY,
                AssertionRow.embedding_model == container.settings.embedding_model,
            )
        )
    retrievals = [row["retrieval"] for row in rows if "retrieval" in row]
    temporals = [row["temporal"] for row in rows if "temporal" in row]
    scored_retrievals = [r for r in retrievals if r["recall_at_10"] is not None]
    missing = [r for r in retrievals if r["empty_result_correct"] is not None]
    latencies = [r["latency_ms"] for r in retrievals]
    return {
        "schema_version": 1,
        "metadata": {
            "run_at": datetime.now(UTC).isoformat(),
            "git_revision": os.environ.get("KNOWLEDGE_VAULT_QUALITY_REVISION", "unavailable"),
            "migration": migration,
            "corpus_sha256": _corpus_digest(cases),
            "case_count": len(cases),
            "abstention_required_case_count": sum(case.requires_abstention for case in cases),
            "template_derived": True,
            "split_counts": {
                split: sum(case.split == split for case in cases)
                for split in sorted({case.split for case in cases})
            },
            "provider": provider_name,
            "mode": mode,
            "distractors": distractors,
            "index_readiness": {
                "ready": ready_embeddings,
                "total": total_assertions,
                "mode": mode,
            },
            "machine": platform.machine(),
            "python": platform.python_version(),
            "extraction": "unmeasured: requires independently judged client output",
            "answer": "unmeasured: requires independently judged client output",
        },
        "summary": {
            "retrieval_case_count": len(scored_retrievals),
            "recall_at_10": _mean([r["recall_at_10"] for r in scored_retrievals]),
            "recall_at_10_case_bootstrap_95": _bootstrap_interval(
                [r["recall_at_10"] for r in scored_retrievals]
            ),
            "ndcg_at_10": _mean([r["ndcg_at_10"] for r in scored_retrievals]),
            "missing_case_count": len(missing),
            "empty_result_accuracy": _mean([float(r["empty_result_correct"]) for r in missing]),
            "empty_result_accuracy_case_bootstrap_95": _bootstrap_interval(
                [float(r["empty_result_correct"]) for r in missing]
            ),
            "temporal_case_count": len(temporals),
            "temporal_correct": _mean([float(r["correct"]) for r in temporals]),
            "temporal_correct_case_bootstrap_95": _bootstrap_interval(
                [float(r["correct"]) for r in temporals]
            ),
            "latency_p50_ms": _percentile(latencies, 0.50),
            "latency_p95_ms": _percentile(latencies, 0.95),
        },
        "by_category": {
            category: {metric: _mean(values) for metric, values in metrics.items()}
            for category, metrics in sorted(by_category.items())
        },
        "by_split": {
            split: {metric: _mean(values) for metric, values in metrics.items()}
            for split, metrics in sorted(by_split.items())
        },
        "cases": rows,
    }

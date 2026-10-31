"""Disposable PostgreSQL quality smoke; full corpus is an explicit CLI run."""

import asyncio
import json
import os
from pathlib import Path

import pytest

from knowledge_vault.domain.models import AssertionInput

from .corpus import cases
from .runner import run_benchmark

pytestmark = pytest.mark.integration


def _selected_cases():
    corpus = cases()
    if os.environ.get("KNOWLEDGE_VAULT_QUALITY_FULL") == "1":
        return corpus
    selected = {}
    for case in corpus:
        selected.setdefault(case.category, case)
    return tuple(selected.values())


async def test_source_first_quality_baseline(integration_container):
    selected = _selected_cases()
    for case in selected:
        assert case.source and case.id and case.split in {"dev", "held_out"}
        for claim in case.claims:
            AssertionInput.model_validate(claim.payload)
    mode = os.environ.get("KNOWLEDGE_VAULT_QUALITY_MODE", "text")
    distractors = int(os.environ.get("KNOWLEDGE_VAULT_QUALITY_DISTRACTORS", "0"))
    report = await run_benchmark(
        integration_container, selected, mode=mode, distractors=distractors
    )
    path = os.environ.get("KNOWLEDGE_VAULT_QUALITY_REPORT")
    if path:
        await asyncio.to_thread(
            Path(path).write_text, json.dumps(report, indent=2, ensure_ascii=False) + "\n"
        )
    assert report["metadata"]["case_count"] == len(selected)
    assert report["summary"]["temporal_correct"] == 1.0
    assert report["summary"]["retrieval_case_count"] > 0
    assert report["metadata"]["extraction"].startswith("unmeasured")

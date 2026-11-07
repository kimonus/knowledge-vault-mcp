"""Synthetic lifecycle: logical roles are not independent human reviewers."""

import json
from pathlib import Path

import pytest
from scripts.prospective_synthetic import freeze

from knowledge_vault.api.app import create_app

pytestmark = [pytest.mark.contract, pytest.mark.integration]


def evaluator(source: list[dict[str, str]]) -> dict[str, str]:
    return {item["subject"]: item["value"] for item in source}


def custodian(source: list[dict[str, str]], gold: dict[str, str], directory: Path) -> Path:
    source_file, gold_file, frozen = (
        directory / name for name in ("source.json", "gold.json", "gold.jsonl")
    )
    source_file.write_text(
        json.dumps(
            {
                "synthetic": True,
                "source_id": "synthetic-0123456789abcdef",
                "excerpt": json.dumps(source),
            }
        ),
        encoding="utf-8",
    )
    claims = [
        {
            "id": key,
            "kind": "user_fact",
            "state": value,
            "scope": key,
            "effective_date": "unknown",
            "question": key,
            "answerable": True,
        }
        for key, value in gold.items()
    ]
    gold_file.write_text(json.dumps({"claims": claims}), encoding="utf-8")
    freeze(source_file, gold_file, frozen)
    return frozen


def extract(source: list[dict[str, str]]) -> list[dict[str, str]]:
    return [
        {
            "content": f"{item['subject']} has colour {item['value']}.",
            "kind": "user_fact",
            "origin": "user",
        }
        for item in source
    ]


def assess(expected: dict[str, str], actual: dict[str, str]) -> dict[str, int]:
    return {
        "expected": len(expected),
        "matched": sum(actual.get(k) == v for k, v in expected.items()),
        "missing": len(expected.keys() - actual.keys()),
        "incorrect": sum(k in actual and actual[k] != v for k, v in expected.items()),
        "extra": len(actual.keys() - expected.keys()),
    }


def parse_assertions(assertions: list[str]) -> dict[str, str]:
    result = {}
    for content in assertions:
        subject, value = content.removesuffix(".").split(" has colour ", 1)
        assert subject not in result
        result[subject] = value
    return result


def test_stage_measurement_negative_controls() -> None:
    expected = {"a": "red", "b": "blue"}
    assert assess(expected, {"a": "red"}) == {
        "expected": 2,
        "matched": 1,
        "missing": 1,
        "incorrect": 0,
        "extra": 0,
    }
    assert assess(expected, {"a": "red", "b": "green", "c": "red"}) == {
        "expected": 2,
        "matched": 1,
        "missing": 0,
        "incorrect": 1,
        "extra": 1,
    }


async def test_manual_synthetic_lifecycle(
    tmp_path, integration_container, serve, mcp_connect, writer_token, reader_token
):
    source = [
        {"subject": f"synthetic-object-{i}", "value": v} for i, v in enumerate(("blue", "green"))
    ]
    gold = evaluator(source)
    frozen = custodian(source, gold, tmp_path)
    frozen_bytes = frozen.read_bytes()
    assertions = extract(source)
    extraction = assess(gold, parse_assertions([item["content"] for item in assertions]))
    assert extraction == {"expected": 2, "matched": 2, "missing": 0, "incorrect": 0, "extra": 0}
    base_url = await serve(create_app(integration_container))
    async with mcp_connect(base_url, writer_token) as writer:
        failed, begun = await writer.call(
            "begin_knowledge_flush",
            {
                "idempotency_key": "synthetic-lifecycle",
                "declared_parts": 1,
                "declared_items": len(assertions),
            },
        )
        assert not failed
        failed, appended = await writer.call(
            "append_knowledge",
            {"batch_id": begun["batch_id"], "part_number": 1, "assertions": assertions},
        )
        assert not failed and appended["accepted"] == 2
        failed, committed = await writer.call(
            "commit_knowledge_flush", {"batch_id": begun["batch_id"]}
        )
        assert not failed and committed["counts"]["inserted"] == 2
        assert len(committed["assertion_ids"]) == 2
    async with mcp_connect(base_url, reader_token) as reader:
        stored = []
        for assertion_id in committed["assertion_ids"]:
            failed, item = await reader.call("get_knowledge", {"assertion_id": assertion_id})
            assert not failed
            stored.append(item["content"])
        persistence = assess(
            parse_assertions([item["content"] for item in assertions]), parse_assertions(stored)
        )
        assert persistence == {
            "expected": 2,
            "matched": 2,
            "missing": 0,
            "incorrect": 0,
            "extra": 0,
        }
        retrieved = {}
        for subject in gold:
            failed, results = await reader.call("search_knowledge", {"query": subject, "limit": 10})
            assert not failed
            for item in results["results"]:
                assertion = item["assertion"]
                if assertion["id"] in committed["assertion_ids"]:
                    retrieved.update(parse_assertions([assertion["content"]]))
        retrieval = assess(gold, retrieved)
        assert retrieval == {"expected": 2, "matched": 2, "missing": 0, "incorrect": 0, "extra": 0}
    assert frozen.read_bytes() == frozen_bytes

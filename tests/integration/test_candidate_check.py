"""Checking planned assertions against the vault before a flush."""

import re
from hashlib import sha256
from typing import Any

import pytest

from knowledge_vault.domain.enums import AssertionStatus
from knowledge_vault.domain.models import AssertionInput
from knowledge_vault.services.errors import InvalidRequestError
from knowledge_vault.services.search import MAX_CHECK_CANDIDATES, MAX_CHECK_MATCHES, SearchService
from knowledge_vault.worker.jobs import EmbeddingWorker

P = "test-user"
CHANNEL = "The access point uses channel 36 on the 5 GHz band."
ROUTER = "The router model is MikroTik hEX S."
OLD_SUBNET = "The LAN subnet is 192.168.1.0/24."
NEW_SUBNET = "The LAN subnet is 192.168.88.0/24."
STEP = "copy the archive offsite; "
LONG = "The backup procedure has these steps: " + STEP * 30


class WordBagProvider:
    """Embeds a text as the bag of its words, so sentences sharing words are close."""

    def __init__(self, dimensions: int = 384) -> None:
        self._dimensions = dimensions
        self.calls = 0

    @property
    def model_id(self) -> str:
        return "word-bag"

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        output: list[list[float]] = []
        for text in texts:
            vector = [0.0] * self._dimensions
            for word in re.findall(r"\w+", text.casefold()):
                vector[int.from_bytes(sha256(word.encode()).digest()[:4]) % self._dimensions] += 1.0
            norm = sum(value * value for value in vector) ** 0.5 or 1.0
            output.append([value / norm for value in vector])
        return output


class FailingProvider(WordBagProvider):
    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("model unavailable")


def item(content: str, **overrides: object) -> dict[str, Any]:
    raw: dict[str, Any] = {"content": content, "kind": "configuration", "origin": "user"}
    raw.update(overrides)
    return raw


async def flush(container, key: str, items: list[dict[str, Any]]):
    batch = await container.ingestion.begin(P, key, 1, len(items))
    await container.ingestion.append(P, batch.id, 1, items)
    return await container.ingestion.commit(P, batch.id)


async def seeded(container) -> tuple[SearchService, WordBagProvider, dict[str, Any]]:
    """Store a small network description, one fact of it corrected, and embed everything."""
    stored = await flush(
        container, "seed", [item(CHANNEL), item(ROUTER), item(OLD_SUBNET), item(LONG)]
    )
    correction = await container.administration.correct(
        P,
        "subnet-moved",
        AssertionInput.model_validate(item(NEW_SUBNET, supersedes_id=str(stored.assertion_ids[2]))),
    )
    provider = WordBagProvider()
    worker = EmbeddingWorker(
        container.database.sessions, container.settings, provider, worker_id="check"
    )
    assert await worker.process_once() == 5
    ids = {
        "channel": stored.assertion_ids[0],
        "router": stored.assertion_ids[1],
        "old_subnet": stored.assertion_ids[2],
        "long": stored.assertion_ids[3],
        "new_subnet": correction.assertion_ids[0],
    }
    return SearchService(container.database.sessions, container.settings, provider), provider, ids


@pytest.mark.integration
async def test_each_candidate_gets_its_closest_stored_assertions(integration_container) -> None:
    search, provider, ids = await seeded(integration_container)
    calls_before = provider.calls
    page = await search.check_candidates(
        [
            "the access point  uses channel 36 on the 5 ghz band.",
            "On the 5 GHz band the access point uses channel 36 with 80 MHz width.",
            "The user enjoys hiking in the mountains every summer.",
            "password=correct-horse-battery-staple",
            "   ",
            OLD_SUBNET,
            LONG + STEP,
        ]
    )
    # One embedding call serves the whole request.
    assert provider.calls == calls_before + 1
    assert page.embedding_degraded is False
    assert [result.index for result in page.results] == list(range(7))
    same, reworded, unrelated, secret, blank, superseded, long = page.results

    # Identical after normalization: submitting it would only confirm the stored record.
    assert [(m.id, m.exact, m.similarity) for m in same.matches] == [(ids["channel"], True, 1.0)]

    # Reworded and extended: found by meaning, and marked as not identical.
    assert reworded.matches[0].id == ids["channel"]
    assert reworded.matches[0].exact is False
    assert reworded.matches[0].similarity is not None
    assert 0.65 <= reworded.matches[0].similarity < 1.0
    assert reworded.matches[0].content == CHANNEL
    assert reworded.matches[0].kind == "configuration"

    assert unrelated.matches == [] and unrelated.error is None

    # An unusable candidate has its own error and does not fail the others.
    assert secret.error is not None and secret.error.code == "secret_detected"
    assert "correct-horse" not in secret.error.message
    assert blank.error is not None and blank.error.code == "invalid_candidate"
    assert secret.matches == [] and blank.matches == []

    # The retired wording is reported as an exact but superseded record, followed by the
    # current assertion that replaced it; other superseded records are never offered.
    assert [(m.id, m.exact, m.status) for m in superseded.matches[:2]] == [
        (ids["old_subnet"], True, AssertionStatus.SUPERSEDED),
        (ids["new_subnet"], False, AssertionStatus.CURRENT),
    ]
    near_new = await search.check_candidates(["The LAN subnet is now 192.168.88.0/24."])
    assert ids["old_subnet"] not in [m.id for m in near_new.results[0].matches]

    # A long stored assertion is returned as an excerpt.
    assert long.matches[0].id == ids["long"]
    assert long.matches[0].truncated is True
    assert len(long.matches[0].content) == 400


@pytest.mark.integration
async def test_limits_bound_the_request_and_the_response(integration_container) -> None:
    search, _, ids = await seeded(integration_container)
    wide = await search.check_candidates(["The LAN subnet is 10.0.0.0/8."], min_similarity=0.0)
    assert len(wide.results[0].matches) == 3
    one = await search.check_candidates(
        ["The LAN subnet is 10.0.0.0/8."], limit=1, min_similarity=0.0
    )
    assert [m.id for m in one.results[0].matches] == [ids["new_subnet"]]
    strict = await search.check_candidates(["The LAN subnet is 10.0.0.0/8."], min_similarity=1.0)
    assert strict.results[0].matches == []

    for arguments in (
        {"candidates": []},
        {"candidates": ["x"] * (MAX_CHECK_CANDIDATES + 1)},
        {"candidates": ["x"], "limit": 0},
        {"candidates": ["x"], "limit": MAX_CHECK_MATCHES + 1},
        {"candidates": ["x"], "min_similarity": 1.5},
    ):
        with pytest.raises(InvalidRequestError):
            await search.check_candidates(**arguments)

    oversized = await search.check_candidates(["x" * 16_001, "nul\x00byte", 7])  # type: ignore[list-item]
    assert [result.error.code for result in oversized.results if result.error] == [
        "invalid_candidate"
    ] * 3


@pytest.mark.integration
@pytest.mark.parametrize("provider", [None, FailingProvider()])
async def test_without_embeddings_candidates_are_matched_by_words(
    integration_container, provider
) -> None:
    _, _, ids = await seeded(integration_container)
    search = SearchService(
        integration_container.database.sessions, integration_container.settings, provider
    )
    page = await search.check_candidates(
        [
            "Channel 36 is what the access point uses.",
            CHANNEL,
            "Zebras gallop quietly.",
            "token=ghp_0123456789abcdefghijklmnopqrstuvwxyzAB",
        ]
    )
    assert page.embedding_degraded is True
    reworded, same, unrelated, secret = page.results
    assert reworded.matches[0].id == ids["channel"]
    assert reworded.matches[0].similarity is None and reworded.matches[0].exact is False
    assert (same.matches[0].id, same.matches[0].exact) == (ids["channel"], True)
    assert unrelated.matches == []
    assert secret.error is not None and secret.matches == []
    assert ids["old_subnet"] not in [
        m.id for m in (await search.check_candidates([NEW_SUBNET])).results[0].matches
    ]

    only_errors = await search.check_candidates([""])
    assert only_errors.results[0].error is not None

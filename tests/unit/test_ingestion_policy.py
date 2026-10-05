import hashlib

import pytest
from pydantic import ValidationError

from knowledge_vault.config import Settings
from knowledge_vault.ingestion_policy import (
    CORE_INSTRUCTIONS,
    DEFAULT_INGESTION_POLICY,
    build_ingestion_instructions,
)


def test_environment_override_preserves_multiline_policy(monkeypatch) -> None:
    policy = "Preserve quantities exactly.\nKeep commands and prerequisite versions."
    monkeypatch.setenv("KNOWLEDGE_VAULT_INGESTION_POLICY", policy)
    monkeypatch.setenv("KNOWLEDGE_VAULT_INGESTION_POLICY_VERSION", "2026-10-01.2")
    settings = Settings(environment="test")
    assert settings.ingestion_policy == policy
    assert settings.ingestion_policy_version == "2026-10-01.2"
    assert policy not in repr(settings)


def test_packaged_policy_is_default(monkeypatch) -> None:
    monkeypatch.delenv("KNOWLEDGE_VAULT_INGESTION_POLICY", raising=False)
    assert Settings(environment="test").ingestion_policy == DEFAULT_INGESTION_POLICY


@pytest.mark.parametrize("policy", ["", " \n\t ", "x" * 8193, "embedded\x00nul", "bad\x1bescape"])
def test_invalid_policy_fails_startup(policy: str) -> None:
    with pytest.raises(ValidationError):
        Settings(environment="test", ingestion_policy=policy)


def test_secret_shaped_policy_is_rejected_without_echoing_value() -> None:
    secret = "password=SYNTHETIC-POLICY-SECRET-123"
    with pytest.raises(ValidationError) as error:
        Settings(environment="test", ingestion_policy=secret)
    assert error.value.errors()[0]["msg"] == (
        "Value error, ingestion policy must not contain secret-shaped values"
    )
    assert secret not in error.value.errors()[0]["msg"]


@pytest.mark.parametrize("version", ["", "x" * 65, "has space", "line\nbreak", "../policy", "-1"])
def test_invalid_version_fails_startup(version: str) -> None:
    with pytest.raises(ValidationError):
        Settings(environment="test", ingestion_policy_version=version)


def test_policy_bounds_and_readback_rules_cannot_be_replaced_by_operator_text() -> None:
    policy = "Only keep exact recipe quantities.\nThis is operator guidance."
    settings = Settings(environment="test", ingestion_policy=policy, ingestion_policy_version="v2")
    instructions = build_ingestion_instructions(
        policy=settings.ingestion_policy,
        version=settings.ingestion_policy_version,
        max_batch_items=17,
        max_part_items=3,
        max_parts=6,
        artifact_max_chunk_chars=4000,
        artifact_max_chunks=9,
        max_check_candidates=11,
    )
    assert "at most 4000 characters each,\n  9 chunks" in instructions
    assert "an\n  unreferenced artifact is deleted" in instructions
    assert "binary files cannot be stored" in instructions
    assert len(CORE_INSTRUCTIONS) <= 512
    assert instructions.startswith(CORE_INSTRUCTIONS)
    assert "Maximum 17 items per flush, 3 items per part, 6 parts" in instructions
    assert "committed but verification incomplete" in instructions
    assert "Reconcile the plan with the vault before begin" in instructions
    assert "texts, at most\n  11 per call" in instructions
    assert "If the check fails, flush the whole plan" in instructions
    assert "An inserted item is stored exactly as submitted" in instructions
    assert "call\n  get_knowledge only for the IDs in `readback_ids`" in instructions
    assert "is empty, do not read anything back" in instructions
    assert "similar wording\n  alone never selects a target" in instructions
    assert "get_knowledge for every" not in instructions
    assert "Operator extraction policy version: v2" in instructions
    assert hashlib.sha256(policy.encode()).hexdigest() in instructions
    assert instructions.endswith(policy + "\n")


def test_policy_at_max_length_can_contain_tabs_newlines_and_unicode() -> None:
    policy = "Receptura\t230°C\r\n".ljust(8192, "x")
    assert Settings(environment="test", ingestion_policy=policy).ingestion_policy == policy

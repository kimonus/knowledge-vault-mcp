"""Client-visible guidance covers validation dependencies observed in the actual M1 pilot."""

import json

from knowledge_vault.config import Settings
from knowledge_vault.domain.models import AssertionInput
from knowledge_vault.ingestion_policy import build_ingestion_instructions


def instructions():
    settings = Settings(
        environment="test", ingestion_policy="Operator-specific extraction guidance."
    )
    return build_ingestion_instructions(
        policy=settings.ingestion_policy,
        version=settings.ingestion_policy_version,
        max_batch_items=100,
        max_part_items=20,
        max_parts=5,
        artifact_max_chunk_chars=4000,
        artifact_max_chunks=8,
        max_check_candidates=20,
    )


def test_minimal_metadata_example_is_a_valid_decision_without_state_or_fake_locator():
    text = instructions()
    marker = "Minimal assertion example (metadata is optional):\n"
    assert marker in text
    payload = json.loads(text.split(marker, 1)[1].splitlines()[0])
    assertion = AssertionInput.model_validate(payload)
    assert assertion.kind == "decision"
    assert assertion.temporal is None and assertion.provenance == []


def test_fixed_guidance_explains_active_reports_and_validator_dependencies():
    text = instructions()
    assert "status=current means active evidence, not verified truth" in text
    assert "do not mark both disputed merely because their values differ" in text
    assert "omit provenance or use kind=unknown" in text
    assert (
        "configuration -> configuration; preference -> preference; fact -> user_fact/external_fact"
        in text
    )
    assert "Do not attach temporal to decisions, procedures or rejected options" in text
    assert "An old observation date alone is not a historical recollection" in text
    assert "lowercase ASCII identifiers" in text
    assert "report_knowledge_usage.report.event_id must be a UUID" in text
    assert text.index("Minimal assertion example") < text.index(
        "Operator extraction policy version:"
    )
    assert "Stop and report unresolved discrepancies" in text


def test_tool_schema_describes_provenance_and_temporal_dependencies():
    schema = AssertionInput.model_json_schema()
    assert "locator" in schema["$defs"]["Provenance"]["properties"]["kind"]["description"]
    assert "Do not invent" in schema["$defs"]["Provenance"]["properties"]["locator"]["description"]
    assert "not verified truth" in schema["properties"]["status"]["description"]
    assert "decisions" in schema["properties"]["temporal"]["description"]
    assert (
        "kind must match"
        in schema["$defs"]["StateIdentity"]["properties"]["state_type"]["description"]
    )

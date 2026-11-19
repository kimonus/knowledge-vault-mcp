import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from knowledge_vault.api.app import create_app
from knowledge_vault.auth.mcp import MCPTokenVerifier
from knowledge_vault.auth.tokens import Scope, TokenAuthenticator, hash_token
from knowledge_vault.config import Settings
from knowledge_vault.container import build_container
from knowledge_vault.domain.inspection import (
    BrowseFilters,
    BrowseRequest,
    CatalogRequest,
    CursorState,
    InspectionContext,
    PageRequest,
)
from knowledge_vault.services.cursors import InvalidCursorError
from knowledge_vault.services.inspection_cursors import InspectionCursors


@pytest.mark.parametrize(
    "value",
    [
        {"mode": "lexical"},
        {"query": "words"},
        {"mode": "lexical", "query": "  "},
        {"mode": "lexical", "query": "a\x00b"},
        {"limit": 0},
        {"limit": 101},
        {"cursor": ""},
        {"purpose": "normal"},
    ],
)
def test_browse_rejects_invalid_contract(value):
    with pytest.raises(ValidationError):
        BrowseRequest.model_validate(value)


def test_filters_and_catalog_require_explicit_scope():
    assert BrowseRequest().filters.statuses == []
    assert BrowseRequest(mode="lexical", query="  two   words  ").query == "two words"
    for values in [
        {"topics": ["topic"], "missing_topic": True},
        {"context_id": "00000000-0000-0000-0000-000000000001", "missing_context": True},
        {"facet": "topic", "missing_facet": True},
    ]:
        with pytest.raises(ValidationError):
            BrowseFilters.model_validate(values)
    with pytest.raises(ValidationError):
        CatalogRequest(dimension="facets")
    assert CatalogRequest(dimension="facets", unscoped=True)


def test_cursor_integrity_binding_expiry_and_version():
    codec = InspectionCursors("synthetic-key")
    context = InspectionContext(principal="inspection", audience="private.example")
    request = BrowseRequest()
    binding = codec.binding("browse", request, context, {})
    state = codec.start(None, binding)
    encoded = codec.encode(binding, state, "boundary")
    assert codec.start(encoded, binding).key == "boundary"
    assert codec.start(encoded, binding).evaluated_at == state.evaluated_at
    for wrong in ["!", encoded[:-1], "x" + encoded[1:]]:
        with pytest.raises(InvalidCursorError):
            codec.start(wrong, binding)
    for altered in [
        codec.binding("different", request, context, {}),
        codec.binding("browse", PageRequest(limit=1), context, {}),
        codec.binding("browse", request, context.model_copy(update={"principal": "other"}), {}),
        codec.binding("browse", request, context.model_copy(update={"audience": "other"}), {}),
        codec.binding("browse", request, context, {"alias": "canonical"}),
    ]:
        with pytest.raises(InvalidCursorError):
            codec.start(encoded, altered)
    for when in [datetime.now(UTC) - timedelta(minutes=31), datetime.now(UTC) + timedelta(days=1)]:
        with pytest.raises(InvalidCursorError):
            codec.start(codec.encode(binding, CursorState(evaluated_at=when), "x"), binding)
    for value in [
        {"version": 2, "binding": binding, "state": state.model_dump(mode="json")},
        {"version": 1, "binding": binding, "state": {"evaluated_at": "2020-01-01T00:00:00"}},
        [],
    ]:
        payload = json.dumps(value).encode()
        token = (
            base64.urlsafe_b64encode(payload + hmac.digest(b"synthetic-key", payload, "sha256"))
            .decode()
            .rstrip("=")
        )
        with pytest.raises(InvalidCursorError):
            codec.start(token, binding)


def test_inspection_configuration_fails_closed(settings):
    for updates in [
        {"inspection_principals": "absent"},
        {"inspection_principals": "writer"},
        {"inspection_principals": "test-user"},
    ]:
        with pytest.raises(ValueError, match="exact-read"):
            create_app(build_container(settings.model_copy(update=updates)))
    for principal in ["cloudflare:someone", "x" * 129]:
        record = {
            "principal_id": principal,
            "sha256": hash_token("t", "p").hex(),
            "scopes": [Scope.READ],
        }
        auth = TokenAuthenticator.from_json(json.dumps(record), "p")
        with pytest.raises(ValueError):
            auth.validate_inspection_principals(frozenset({principal}))
    records = [
        {
            "principal_id": principal,
            "sha256": hashlib.sha256(b"same").hexdigest(),
            "scopes": [Scope.READ],
        }
        for principal in ["inspection", "ordinary"]
    ]
    auth = TokenAuthenticator.from_json(json.dumps(records), "p")
    with pytest.raises(ValueError, match="shared"):
        auth.validate_inspection_principals(frozenset({"inspection"}))


async def test_mcp_verifier_independently_refuses_inspection(settings, reader_token):
    container = build_container(settings.model_copy(update={"inspection_principals": "reader"}))
    verifier = MCPTokenVerifier(container.authenticator, frozenset({"reader"}))
    assert await verifier.verify_token(reader_token) is None
    assert container.inspection.search_service._embedder is container.embedder
    await container.database.close()


def test_environment_principal_list():
    assert Settings(
        environment="test", inspection_principals=" first, second, first "
    ).inspection_principal_set == frozenset({"first", "second"})


async def test_worker_container_needs_no_inspection_credentials(settings):
    # The chart's shared extraEnv reaches workers, which do not mount API token records.
    container = build_container(
        settings.model_copy(
            update={
                "inspection_principals": "read-service",
                "bootstrap_tokens": "",
            }
        )
    )
    with pytest.raises(ValueError, match="exact-read"):
        create_app(container)
    await container.database.close()

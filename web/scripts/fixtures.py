"""Generate synthetic UI fixtures through actual PR1 response models; never use vault data."""

import json
import sys
from pathlib import Path
from uuid import UUID

from knowledge_vault.domain.models import AssertionInput
from knowledge_vault.domain.responses import (
    ArtifactContentResponse,
    AssertionDetailResponse,
    AssertionSummary,
    CatalogPage,
    InspectionPage,
    RawTemporalEvidence,
    StateResolutionResponse,
)

NOW = "2026-11-19T12:00:00Z"
CONTEXT = str(UUID(int=900))
IDENTITY = {
    "subject": "homelab",
    "property": "backup",
    "scope": "personal",
    "state_type": "configuration",
}
XSS = (
    "<script>window.__vault_xss=1</script>"
    '<img src="https://attack.invalid/pixel" onerror="window.__vault_xss=2">'
)


def page(items, limit=50, cursor=None):
    return {
        "items": items,
        "page_size": limit,
        "next_cursor": cursor,
        "evaluated_at": NOW,
        "consistency": "live",
        "untrusted_data": True,
    }


def fixtures():
    sources = [
        {
            "url": f"https://example.test/source/{n:03d}",
            "title": f"Evidence source {n}",
            "publisher": "Synthetic source",
            "retrieved_at": NOW,
        }
        for n in range(43)
    ]
    sources[0] = {**sources[0], "url": "javascript:window.__vault_xss=3", "title": XSS}
    artifacts = [
        {
            "id": str(UUID(int=1000 + n)),
            "filename": f"evidence-{n}.txt",
            "media_type": "image/svg+xml",
            "size_bytes": 16020,
            "description": "Synthetic inert text",
        }
        for n in range(22)
    ]
    identities = [
        {**IDENTITY, "subject": "homelab" if n == 0 else f"homelab-{n:03d}"} for n in range(35)
    ]
    rows = []
    choices = [
        (
            "configuration",
            "current",
            "Nightly backup configuration retained from operator evidence.",
            "reported",
            None,
        ),
        (
            "configuration",
            "superseded",
            "Earlier backup configuration, retained as historical evidence.",
            "reported",
            None,
        ),
        (
            "rejected_option",
            "current",
            "Rejected option: public database access was rejected.",
            "reported",
            None,
        ),
        (
            "plan",
            "current",
            "Proposed plan: rehearse the restore procedure.",
            "unknown",
            "proposed",
        ),
        (
            "user_fact",
            "uncertain",
            "Uncertain note: keyboard preference needs confirmation.",
            "unknown",
            None,
        ),
        (
            "external_fact",
            "disputed",
            "Disputed report: competing evidence is retained.",
            "reported",
            None,
        ),
        ("preference", "current", "Operator prefers reproducible evidence.", "verified", None),
        (
            "considered_option",
            "current",
            "Considered option: an alternative archive format.",
            "inferred",
            None,
        ),
        ("artifact_observation", "current", XSS, "unknown", None),
    ]
    for n, (kind, status, content, evidence, workflow) in enumerate(choices, 1):
        raw = {
            "id": str(UUID(int=n)),
            "content": content,
            "kind": kind,
            "origin": "user",
            "status": status,
            "confidence": 0.85,
            "topics": ["backups", "homelab"] if n < 5 else [],
            "sensitivity": "sensitive" if n == 9 else "private" if n == 7 else "normal",
            "valid_from": None,
            "valid_to": None,
            "observed_at": NOW,
            "created_at": NOW,
            "updated_at": NOW,
            "last_confirmed_at": NOW,
            "confirmation_count": 3,
            "usage": {"available": False},
            "supersedes_id": str(UUID(int=2)) if n == 1 else None,
            "embedding_state": "disabled",
            "context_id": CONTEXT if n < 5 else None,
            "facet": "configuration" if n < 3 else None,
            "evidence_state": evidence,
            "temporal_role": "historical" if n == 2 else "ongoing",
            "workflow_state": workflow,
            "last_verified_at": NOW if evidence == "verified" else None,
            "provenance": [
                {
                    "kind": "command",
                    "locator": XSS,
                    "title": "Synthetic provenance",
                    "timestamp_precision": "day",
                    "timestamp_basis": "source",
                }
            ],
            "source_page": page(sources[:20] if n == 1 else [], 20, "20" if n == 1 else None),
            "artifact_page": page(artifacts[:20] if n == 1 else [], 20, "20" if n == 1 else None),
            "identity_page": page(identities[:20] if n == 1 else [], 20, "20" if n == 1 else None),
            "sources": sources[:20] if n == 1 else [],
            "artifacts": artifacts[:20] if n == 1 else [],
            "temporal_identities": identities[:20] if n == 1 else [],
            "temporal_identities_truncated": n == 1,
        }
        rows.append(AssertionDetailResponse.model_validate(raw).model_dump(mode="json"))
    summaries = [
        AssertionSummary.model_validate(
            {**row, "excerpt": row["content"][:400], "content_truncated": False}
        ).model_dump(mode="json")
        for row in rows
    ]
    evidence_input = AssertionInput.model_validate(
        {
            "content": "Retained synthetic backup evidence.",
            "kind": "configuration",
            "origin": "user",
            "evidence_state": "reported",
            "temporal": {
                "identity": IDENTITY,
                "evidence_key": "backup-000",
                "value": "nightly",
                "mode": "snapshot",
                "observed_precision": "day",
                "valid_from_precision": "month",
                "observation_basis": "source",
            },
        }
    ).model_dump(mode="json")
    raw = RawTemporalEvidence.model_validate(
        {
            "evidence_key": "backup-000",
            "assertion_id": rows[0]["id"],
            "recorded_at": NOW,
            "assertion": evidence_input,
            "retired": False,
        }
    ).model_dump(mode="json")
    resolved = StateResolutionResponse.model_validate(
        {
            "identity": IDENTITY,
            "as_of": None,
            "known_at": None,
            "outcome": "resolved",
            "values": ["nightly"],
            "supporting_keys": ["backup-000"],
            "competing_keys": [],
            "reasons": ["supporting_evidence"],
            "evidence_total": 23,
            "history": [
                {k: v for k, v in {**raw, "disposition": "supporting"}.items() if k != "retired"}
            ],
            "next_history_after": "backup-000",
        }
    ).model_dump(mode="json")
    catalog_item = {
        "key": "backups",
        "label": "backups",
        "stored_labels": ["backups", "backup"],
        "assertion_count": 4,
        "by_status": {"current": 3, "superseded": 1},
        "by_kind": {"configuration": 2, "rejected_option": 1, "plan": 1},
        "by_evidence_state": {"reported": 3, "unknown": 1},
        "by_workflow_state": {"unspecified": 3, "proposed": 1},
    }
    catalog = CatalogPage.model_validate(
        page(
            [
                catalog_item,
                {**catalog_item, "key": None, "label": "Uncategorized", "stored_labels": []},
            ]
        )
    ).model_dump(mode="json")
    artifact = ArtifactContentResponse.model_validate(
        {
            "artifact_id": artifacts[0]["id"],
            "filename": "evidence.svg",
            "media_type": "image/svg+xml",
            "description": "Inert synthetic artifact",
            "size_bytes": 16020,
            "sha256": "0" * 64,
            "total_chars": 16020,
            "offset": 0,
            "content": XSS,
            "next_offset": 8000,
        }
    ).model_dump(mode="json")
    return {
        "now": NOW,
        "context": {
            "id": CONTEXT,
            "name": "Homelab operations",
            "aliases": ["Home infrastructure"],
            "untrusted_data": True,
        },
        "identity": IDENTITY,
        "summaries": summaries,
        "details": rows,
        "sources": sources,
        "artifacts": artifacts,
        "identities": identities,
        "resolution": resolved,
        "evidence": raw,
        "catalog": catalog,
        "artifact": artifact,
        "page": InspectionPage[AssertionSummary]
        .model_validate(page(summaries[:2], 50, "2"))
        .model_dump(mode="json"),
    }


if __name__ == "__main__":
    target = Path("web/tests/fixtures.json")
    data = fixtures()
    if "--check" in sys.argv:
        if json.loads(target.read_text()) != data:
            raise SystemExit("Frontend fixtures differ from current domain model projections")
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")

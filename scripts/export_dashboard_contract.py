"""Export reviewed protocol-1 schemas without installing core into the dashboard.

Run from repository root. --check rejects drift in CI.
"""

import argparse
import json
from pathlib import Path

from pydantic import BaseModel

from knowledge_vault.auth.inspection import READ_ROUTES
from knowledge_vault.domain.inspection import (
    BrowseRequest,
    CatalogRequest,
    EvidenceRequest,
    RankedRequest,
)
from knowledge_vault.domain.models import ArtifactRef, SourceView
from knowledge_vault.domain.responses import (
    AliasResponse,
    ArtifactContentResponse,
    AssertionDetailResponse,
    AssertionSummary,
    CatalogPage,
    ContextResponse,
    InspectionCapabilities,
    InspectionPage,
    InspectionRelation,
    RankedAssertionPage,
    RawTemporalEvidence,
    StateResolutionResponse,
)
from knowledge_vault.domain.temporal import ResolutionRequest, StateIdentity


def export() -> str:
    pairs: dict[str, tuple[type[BaseModel] | None, type[BaseModel]]] = {
        "capabilities": (None, InspectionCapabilities),
        "assertions/query": (BrowseRequest, InspectionPage[AssertionSummary]),
        "catalog/query": (CatalogRequest, CatalogPage),
        "search": (RankedRequest, RankedAssertionPage),
        "assertions/{id}": (None, AssertionDetailResponse),
        "assertions/{id}/sources": (None, InspectionPage[SourceView]),
        "assertions/{id}/artifacts": (None, InspectionPage[ArtifactRef]),
        "assertions/{id}/identities": (None, InspectionPage[StateIdentity]),
        "assertions/{id}/relations": (None, InspectionPage[InspectionRelation]),
        "contexts/{id}": (None, ContextResponse),
        "topic-aliases": (None, AliasResponse),
        "state/resolve": (ResolutionRequest, StateResolutionResponse),
        "state/evidence/query": (EvidenceRequest, InspectionPage[RawTemporalEvidence]),
        "artifacts/{id}": (None, ArtifactContentResponse),
    }
    contract = {
        "operations": [f"{method} {path}" for method, path in READ_ROUTES],
        "schemas": {
            path: {
                "input": request.model_json_schema() if request else None,
                "output": response.model_json_schema(mode="serialization"),
            }
            for path, (request, response) in pairs.items()
        },
    }
    return json.dumps(contract, ensure_ascii=True, indent=2, sort_keys=True) + "\n"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    target = Path("dashboard/src/knowledge_vault_dashboard/contract.json")
    rendered = export()
    if args.check:
        if target.read_text() != rendered:
            raise SystemExit("dashboard contract drift; run scripts/export_dashboard_contract.py")
    else:
        target.write_text(rendered)

"""Dedicated inspection adapter; no browser routes, mutations or usage accounting."""

from uuid import UUID

from fastapi import Depends, FastAPI, Query, Request

from knowledge_vault.api.auth import require_scope
from knowledge_vault.auth.hosts import canonical_host
from knowledge_vault.auth.inspection import READ_ROUTES
from knowledge_vault.auth.tokens import Principal, Scope
from knowledge_vault.container import Container
from knowledge_vault.domain.inspection import (
    BrowseRequest,
    CatalogRequest,
    EvidenceRequest,
    InspectionContext,
    PageRequest,
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
from knowledge_vault.mcp.server import artifact_page
from knowledge_vault.services.inspection import bounded


def register_read_routes(app: FastAPI, container: Container) -> None:
    service = container.inspection

    async def context(
        request: Request, principal: Principal = Depends(require_scope(Scope.READ))
    ) -> InspectionContext:
        # Outer inspection boundary verified exact authority, one Host and service-only headers.
        return InspectionContext(
            principal=principal.id, audience=canonical_host(request.headers["host"])
        )

    def page(
        limit: int = Query(default=50, ge=1, le=100),
        cursor: str | None = Query(default=None, min_length=1, max_length=16000),
    ) -> PageRequest:
        return PageRequest(limit=limit, cursor=cursor)

    @app.get("/api/v1/read/capabilities")
    async def capabilities(ctx: InspectionContext = Depends(context)) -> InspectionCapabilities:
        del ctx
        return InspectionCapabilities(
            operations=[f"{method} {path}" for method, path in READ_ROUTES],
            max_page_size=min(100, container.settings.max_page_size),
        )

    @app.post("/api/v1/read/assertions/query")
    async def assertions(
        body: BrowseRequest, ctx: InspectionContext = Depends(context)
    ) -> InspectionPage[AssertionSummary]:
        return await service.browse.assertions(body, ctx)

    @app.post("/api/v1/read/catalog/query")
    async def catalog(
        body: CatalogRequest, ctx: InspectionContext = Depends(context)
    ) -> CatalogPage:
        return await service.browse.catalog(body, ctx)

    @app.post("/api/v1/read/search")
    async def ranked(
        body: RankedRequest, ctx: InspectionContext = Depends(context)
    ) -> RankedAssertionPage:
        return await service.ranked(body, ctx)

    @app.get("/api/v1/read/assertions/{assertion_id}")
    async def detail(
        assertion_id: UUID, ctx: InspectionContext = Depends(context)
    ) -> AssertionDetailResponse:
        return await service.browse.detail(assertion_id, ctx)

    @app.get("/api/v1/read/assertions/{assertion_id}/sources")
    async def sources(
        assertion_id: UUID,
        body: PageRequest = Depends(page),
        ctx: InspectionContext = Depends(context),
    ) -> InspectionPage[SourceView]:
        return await service.browse.sources(assertion_id, body, ctx)

    @app.get("/api/v1/read/assertions/{assertion_id}/artifacts")
    async def artifacts(
        assertion_id: UUID,
        body: PageRequest = Depends(page),
        ctx: InspectionContext = Depends(context),
    ) -> InspectionPage[ArtifactRef]:
        return await service.browse.artifacts(assertion_id, body, ctx)

    @app.get("/api/v1/read/assertions/{assertion_id}/identities")
    async def identities(
        assertion_id: UUID,
        body: PageRequest = Depends(page),
        ctx: InspectionContext = Depends(context),
    ) -> InspectionPage[StateIdentity]:
        return await service.browse.identities(assertion_id, body, ctx)

    @app.get("/api/v1/read/assertions/{assertion_id}/relations")
    async def relations(
        assertion_id: UUID,
        body: PageRequest = Depends(page),
        ctx: InspectionContext = Depends(context),
    ) -> InspectionPage[InspectionRelation]:
        return await service.browse.relations(assertion_id, body, ctx)

    @app.post("/api/v1/read/state/evidence/query")
    async def evidence(
        body: EvidenceRequest, ctx: InspectionContext = Depends(context)
    ) -> InspectionPage[RawTemporalEvidence]:
        return await service.browse.evidence(body, ctx)

    @app.post("/api/v1/read/state/resolve")
    async def resolve(
        body: ResolutionRequest, ctx: InspectionContext = Depends(context)
    ) -> StateResolutionResponse:
        del ctx
        return await service.resolve(body)

    @app.get("/api/v1/read/contexts/{context_id}")
    async def get_context(
        context_id: UUID, ctx: InspectionContext = Depends(context)
    ) -> ContextResponse:
        del ctx
        result = await service.maintenance.get_context(context_id)
        bounded(result)
        return result

    @app.get("/api/v1/read/topic-aliases")
    async def aliases(ctx: InspectionContext = Depends(context)) -> AliasResponse:
        del ctx
        return await service.maintenance.aliases()

    @app.get("/api/v1/read/artifacts/{artifact_id}")
    async def artifact(
        artifact_id: UUID,
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=8000, ge=1, le=100000),
        ctx: InspectionContext = Depends(context),
    ) -> ArtifactContentResponse:
        del ctx
        return artifact_page(await service.artifacts.read(artifact_id, offset=offset, limit=limit))

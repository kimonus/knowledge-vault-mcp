"""HTTP maintenance endpoints sharing the MCP domain contracts and services."""

from uuid import UUID

from fastapi import Depends, FastAPI, Query

from knowledge_vault.api.auth import require_scope
from knowledge_vault.auth.tokens import Principal, Scope
from knowledge_vault.container import Container
from knowledge_vault.domain.maintenance import (
    ContextInput,
    LinkInput,
    MetadataUpdate,
    TopicAliasInput,
    UsageReport,
)
from knowledge_vault.domain.responses import (
    AliasResponse,
    ContextResponse,
    CurationResponse,
    LinkView,
    MutationResponse,
    RelatedResponse,
    ReviewResponse,
    UsageReportResponse,
)


def register_maintenance_routes(app: FastAPI, container: Container) -> None:
    @app.post("/api/v1/metadata")
    async def metadata(
        body: MetadataUpdate, principal: Principal = Depends(require_scope(Scope.WRITE))
    ) -> MutationResponse:
        return await container.maintenance.update(principal.id, body)

    @app.post("/api/v1/contexts")
    async def context(
        body: ContextInput, principal: Principal = Depends(require_scope(Scope.WRITE))
    ) -> ContextResponse:
        del principal
        return await container.maintenance.context(body)

    @app.get("/api/v1/contexts/{context_id}")
    async def get_context(
        context_id: UUID, principal: Principal = Depends(require_scope(Scope.READ))
    ) -> ContextResponse:
        del principal
        return await container.maintenance.get_context(context_id)

    @app.post("/api/v1/links")
    async def link(
        body: LinkInput, principal: Principal = Depends(require_scope(Scope.WRITE))
    ) -> LinkView:
        del principal
        return await container.maintenance.link(body)

    @app.get("/api/v1/assertions/{assertion_id}/related")
    async def related(
        assertion_id: UUID,
        limit: int = Query(default=50, ge=1, le=100),
        principal: Principal = Depends(require_scope(Scope.READ)),
    ) -> RelatedResponse:
        del principal
        return await container.maintenance.related(assertion_id, limit)

    @app.post("/api/v1/topic-aliases")
    async def alias(
        body: TopicAliasInput, principal: Principal = Depends(require_scope(Scope.WRITE))
    ) -> AliasResponse:
        del principal
        return await container.maintenance.alias(body)

    @app.get("/api/v1/topic-aliases")
    async def aliases(principal: Principal = Depends(require_scope(Scope.READ))) -> AliasResponse:
        del principal
        return await container.maintenance.aliases()

    @app.get("/api/v1/review")
    async def review(
        limit: int = Query(default=50, ge=1, le=100),
        after: UUID | None = None,
        context_id: UUID | None = None,
        principal: Principal = Depends(require_scope(Scope.READ)),
    ) -> ReviewResponse:
        del principal
        return await container.maintenance.review(limit=limit, after=after, context_id=context_id)

    @app.get("/api/v1/curation-candidates")
    async def candidates(
        context_id: UUID | None = None,
        topic: str | None = None,
        limit: int = Query(default=50, ge=1, le=100),
        principal: Principal = Depends(require_scope(Scope.READ)),
    ) -> CurationResponse:
        del principal
        return await container.maintenance.candidates(
            context_id=context_id, topic=topic, limit=limit
        )

    @app.post("/api/v1/usage")
    async def usage(
        body: UsageReport, principal: Principal = Depends(require_scope(Scope.WRITE))
    ) -> UsageReportResponse:
        return UsageReportResponse(
            recorded=await container.usage.record(
                body.assertion_ids, principal=principal.id, event_id=body.event_id, operation="use"
            )
        )

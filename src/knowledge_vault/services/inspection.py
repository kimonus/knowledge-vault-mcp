"""Non-recording service facade reusing the core's read logic and embedding provider."""

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from knowledge_vault.config import Settings
from knowledge_vault.domain.inspection import InspectionContext, RankedRequest
from knowledge_vault.domain.responses import (
    AssertionSummary,
    RankedAssertionPage,
    RankedAssertionSummary,
    StateResolutionResponse,
)
from knowledge_vault.domain.temporal import ResolutionRequest
from knowledge_vault.embeddings.base import EmbeddingProvider
from knowledge_vault.services.artifacts import ArtifactService
from knowledge_vault.services.browse import MAX_RESPONSE_BYTES, BrowseService
from knowledge_vault.services.errors import InvalidRequestError
from knowledge_vault.services.maintenance import MaintenanceService
from knowledge_vault.services.search import SearchService
from knowledge_vault.services.temporal import TemporalService


def bounded(response: BaseModel) -> None:
    if len(response.model_dump_json().encode()) > MAX_RESPONSE_BYTES:
        raise InvalidRequestError("inspection response exceeds the byte bound")


class InspectionService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        settings: Settings,
        embedder: EmbeddingProvider | None,
    ) -> None:
        self.browse = BrowseService(sessions, settings)
        self.search_service = SearchService(sessions, settings, embedder)
        self.temporal = TemporalService(sessions)
        self.artifacts = ArtifactService(sessions, settings)
        self.maintenance = MaintenanceService(sessions)
        self._settings = settings

    async def ranked(
        self, request: RankedRequest, context: InspectionContext
    ) -> RankedAssertionPage:
        aliases = await self.browse.aliases()
        binding, state = self.browse.start("ranked", request, context, aliases)
        page = await self.search_service.search(
            request.query,
            request.filters,
            limit=request.limit,
            cursor=state.key,
            purpose="maintenance",
            principal=context.principal,
            include_related=False,
        )
        result = RankedAssertionPage(
            page_size=request.limit,
            items=[
                RankedAssertionSummary(
                    assertion=AssertionSummary(
                        **hit.assertion.model_dump(),
                        excerpt=hit.assertion.content[:400],
                        content_truncated=len(hit.assertion.content) > 400,
                    ),
                    score=hit.score,
                )
                for hit in page.results
            ],
            evaluated_at=state.evaluated_at,
            embedding_degraded=page.embedding_degraded,
            next_cursor=(
                self.browse.cursors.encode(binding, state, page.next_cursor)
                if page.next_cursor
                else None
            ),
        )
        bounded(result)
        return result

    async def resolve(self, request: ResolutionRequest) -> StateResolutionResponse:
        if request.history_limit > min(100, self._settings.max_page_size):
            raise InvalidRequestError("history page exceeds the configured maximum")
        result = await self.temporal.resolve(request)
        while (
            len(result.model_dump_json().encode()) > MAX_RESPONSE_BYTES and len(result.history) > 1
        ):
            result.history.pop()
            result.next_history_after = result.history[-1].evidence_key
        bounded(result)
        return result

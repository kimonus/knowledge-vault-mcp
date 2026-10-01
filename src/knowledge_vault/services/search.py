import hashlib
import json
import time
from uuid import UUID

from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from knowledge_vault.config import Settings
from knowledge_vault.domain.enums import (
    AssertionKind,
    AssertionOrigin,
    AssertionStatus,
    EmbeddingState,
    Sensitivity,
)
from knowledge_vault.domain.models import (
    AssertionView,
    SearchFilters,
    SearchHit,
    SearchPage,
    SourceView,
)
from knowledge_vault.embeddings.base import EmbeddingProvider
from knowledge_vault.observability.metrics import SEARCH_LATENCY
from knowledge_vault.persistence.tables import AssertionRow
from knowledge_vault.services.cursors import Cursor, CursorCodec, InvalidCursorError
from knowledge_vault.services.errors import InvalidRequestError, NotFoundError
from knowledge_vault.services.ranking import reciprocal_rank_fusion

# Enrichment can attach more sources over time; responses stay bounded.
_MAX_SOURCES_PER_VIEW = 32


def _query_hash(query: str, filters: SearchFilters) -> str:
    value = json.dumps(
        {"query": query, "filters": filters.model_dump(mode="json")},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(value.encode()).hexdigest()[:24]


def _view(row: AssertionRow) -> AssertionView:
    return AssertionView(
        id=row.id,
        content=row.content,
        kind=AssertionKind(row.kind),
        origin=AssertionOrigin(row.origin),
        status=AssertionStatus(row.status),
        confidence=row.confidence,
        topics=row.topics,
        sensitivity=Sensitivity(row.sensitivity),
        valid_from=row.valid_from,
        valid_to=row.valid_to,
        observed_at=row.observed_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
        last_confirmed_at=row.last_confirmed_at,
        confirmation_count=row.confirmation_count,
        supersedes_id=row.supersedes_id,
        embedding_state=EmbeddingState(row.embedding_state),
        sources=[
            SourceView(
                url=source.url,
                title=source.title,
                publisher=source.publisher,
                retrieved_at=source.retrieved_at,
            )
            for source in sorted(row.sources, key=lambda source: source.url)[:_MAX_SOURCES_PER_VIEW]
        ],
    )


class SearchService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        settings: Settings,
        embedder: EmbeddingProvider | None,
    ) -> None:
        self._sessions = sessions
        self._settings = settings
        self._embedder = embedder
        self._cursors = CursorCodec(settings.token_pepper or "development-only-cursor-key")

    @staticmethod
    def _filtered(filters: SearchFilters) -> Select[tuple[AssertionRow]]:
        statement = select(AssertionRow)
        conditions = [AssertionRow.confidence >= filters.minimum_confidence]
        if filters.kinds:
            conditions.append(AssertionRow.kind.in_(filters.kinds))
        if filters.origins:
            conditions.append(AssertionRow.origin.in_(filters.origins))
        if filters.statuses:
            conditions.append(AssertionRow.status.in_(filters.statuses))
        if filters.topics:
            conditions.append(AssertionRow.topics.overlap(filters.topics))
        if filters.sensitivities:
            conditions.append(AssertionRow.sensitivity.in_(filters.sensitivities))
        if filters.valid_at:
            conditions.extend(
                [
                    or_(
                        AssertionRow.valid_from.is_(None),
                        AssertionRow.valid_from <= filters.valid_at,
                    ),
                    or_(AssertionRow.valid_to.is_(None), AssertionRow.valid_to > filters.valid_at),
                ]
            )
        if filters.created_after:
            conditions.append(AssertionRow.created_at >= filters.created_after)
        if filters.created_before:
            conditions.append(AssertionRow.created_at < filters.created_before)
        return statement.where(and_(*conditions))

    async def search(
        self,
        query: str,
        filters: SearchFilters | None = None,
        *,
        limit: int = 20,
        cursor: str | None = None,
    ) -> SearchPage:
        started = time.perf_counter()
        query = " ".join(query.split())
        if not query or len(query) > 2000 or "\x00" in query:
            raise InvalidRequestError(
                "query must contain 1 through 2000 characters and no NUL characters"
            )
        if not 1 <= limit <= self._settings.max_page_size:
            raise InvalidRequestError(f"limit must be between 1 and {self._settings.max_page_size}")
        effective_filters = filters or SearchFilters()
        query_hash = _query_hash(query, effective_filters)
        boundary = self._cursors.decode(cursor) if cursor else None
        if boundary and boundary.query_hash != query_hash:
            raise InvalidCursorError("cursor does not belong to this query and filter set")

        base = self._filtered(effective_filters)
        candidate_limit = min(max(limit * 5, 50), 250)
        tsquery = func.websearch_to_tsquery("simple", query)
        text_statement = (
            base.add_columns(func.ts_rank_cd(AssertionRow.search_vector, tsquery).label("rank"))
            .where(AssertionRow.search_vector.op("@@")(tsquery))
            .order_by(func.ts_rank_cd(AssertionRow.search_vector, tsquery).desc(), AssertionRow.id)
            .limit(candidate_limit)
        )
        degraded = False
        query_vector: list[float] | None = None
        if self._embedder and self._settings.embeddings_enabled:
            try:
                query_vector = (await self._embedder.embed([query]))[0]
            except Exception:
                degraded = True
        else:
            degraded = True

        async with self._sessions() as session:
            text_rows = list((await session.execute(text_statement)).tuples().all())
            vector_rows: list[tuple[AssertionRow, float]] = []
            if query_vector is not None:
                distance = AssertionRow.embedding.cosine_distance(query_vector)
                vector_statement = (
                    base.add_columns(distance.label("distance"))
                    .where(
                        AssertionRow.embedding_state == EmbeddingState.READY,
                        AssertionRow.embedding.is_not(None),
                        # Vectors from another model live in a different space; ignore them
                        # until the rebuild for the configured model has replaced them.
                        AssertionRow.embedding_model == self._settings.embedding_model,
                    )
                    .order_by(distance, AssertionRow.id)
                    .limit(candidate_limit)
                )
                vector_rows = list((await session.execute(vector_statement)).tuples().all())

        rows_by_id: dict[UUID, AssertionRow] = {row.id: row for row, _ in text_rows}
        rows_by_id.update({row.id: row for row, _ in vector_rows})
        fused = reciprocal_rank_fusion(
            [row.id for row, _ in text_rows], [row.id for row, _ in vector_rows]
        )
        if boundary:
            fused = [
                item
                for item in fused
                if (item.score < boundary.score)
                or (item.score == boundary.score and str(item.item) > str(boundary.assertion_id))
            ]
        page_items = fused[: limit + 1]
        has_more = len(page_items) > limit
        page_items = page_items[:limit]
        results = [
            SearchHit(assertion=_view(rows_by_id[item.item]), score=item.score)
            for item in page_items
        ]
        next_cursor = None
        if has_more and page_items:
            final = page_items[-1]
            next_cursor = self._cursors.encode(Cursor(query_hash, final.score, final.item))
        SEARCH_LATENCY.labels(mode="text" if query_vector is None else "hybrid").observe(
            time.perf_counter() - started
        )
        return SearchPage(results=results, next_cursor=next_cursor, embedding_degraded=degraded)

    async def get(self, assertion_id: UUID) -> AssertionView:
        async with self._sessions() as session:
            row = await session.scalar(
                select(AssertionRow)
                .options(selectinload(AssertionRow.sources))
                .where(AssertionRow.id == assertion_id)
            )
        if not row:
            raise NotFoundError("assertion was not found")
        return _view(row)

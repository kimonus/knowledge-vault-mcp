import hashlib
import json
import time
from uuid import UUID

from sqlalchemy import Select, Text, and_, cast, func, or_, select
from sqlalchemy.dialects.postgresql import TSQUERY
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
    ArtifactRef,
    AssertionView,
    CandidateCheck,
    CandidateCheckPage,
    CandidateError,
    CandidateMatch,
    SearchFilters,
    SearchHit,
    SearchPage,
    SourceView,
)
from knowledge_vault.domain.normalization import content_hash, normalize_content
from knowledge_vault.domain.secrets import detect_secret
from knowledge_vault.embeddings.base import EmbeddingProvider
from knowledge_vault.observability.metrics import SEARCH_LATENCY
from knowledge_vault.persistence.tables import AssertionRow
from knowledge_vault.services.cursors import Cursor, CursorCodec, InvalidCursorError
from knowledge_vault.services.errors import InvalidRequestError, NotFoundError
from knowledge_vault.services.ranking import reciprocal_rank_fusion

# Enrichment can attach more sources over time; responses stay bounded.
_MAX_SOURCES_PER_VIEW = 32

# Bounds of a candidate check: candidates per call, matches per candidate, and how much of a
# stored assertion is returned. Together they cap the response at a few thousand words.
MAX_CHECK_CANDIDATES = 50
MAX_CHECK_MATCHES = 5
_CHECK_EXCERPT_CHARS = 400
_MAX_CANDIDATE_CHARS = 16_000
# Words-only matching, used when embeddings are unavailable, reads this much of a candidate.
_LEXICAL_CANDIDATE_CHARS = 1_000
# ...and accepts a stored assertion that contains at least this share (2/5) of its words.
_LEXICAL_SHARE_NUMERATOR = 2
_LEXICAL_SHARE_DENOMINATOR = 5
# A candidate is compared with assertions a client could still confirm, refine or supersede.
_CHECKED_STATUSES = (
    AssertionStatus.CURRENT,
    AssertionStatus.UNCERTAIN,
    AssertionStatus.DISPUTED,
)


def _candidate_error(candidate: object) -> CandidateError | None:
    if not isinstance(candidate, str) or not candidate.strip():
        return CandidateError(
            code="invalid_candidate", message="a candidate must be non-empty text"
        )
    if len(candidate) > _MAX_CANDIDATE_CHARS or "\x00" in candidate:
        return CandidateError(
            code="invalid_candidate",
            message=f"a candidate must have at most {_MAX_CANDIDATE_CHARS} characters and no NUL",
        )
    secret = detect_secret(candidate)
    if secret:
        return CandidateError(
            code="secret_detected",
            message=f"suspected {secret}; a secret value must not be submitted or stored",
        )
    return None


def _match(row: AssertionRow, *, exact: bool, similarity: float | None) -> CandidateMatch:
    return CandidateMatch(
        id=row.id,
        content=row.content[:_CHECK_EXCERPT_CHARS],
        truncated=len(row.content) > _CHECK_EXCERPT_CHARS,
        kind=AssertionKind(row.kind),
        status=AssertionStatus(row.status),
        exact=exact,
        similarity=similarity,
    )


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
        artifacts=[
            ArtifactRef(
                id=artifact.id,
                filename=artifact.filename,
                media_type=artifact.media_type,
                size_bytes=artifact.size_bytes or 0,
                description=artifact.description,
            )
            for artifact in sorted(row.artifacts, key=lambda artifact: str(artifact.id))
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

    async def check_candidates(
        self,
        candidates: list[str],
        *,
        limit: int = 3,
        min_similarity: float = 0.65,
    ) -> CandidateCheckPage:
        """Find, for each planned assertion, the stored assertions closest to it.

        Nothing is written and nothing is merged: the caller decides whether a candidate is
        already stored, refines a record, or replaces one. An unusable candidate gets an error
        of its own and never fails the others.
        """
        started = time.perf_counter()
        if not 1 <= len(candidates) <= MAX_CHECK_CANDIDATES:
            raise InvalidRequestError(
                f"provide 1 through {MAX_CHECK_CANDIDATES} candidates per call"
            )
        if not 1 <= limit <= MAX_CHECK_MATCHES:
            raise InvalidRequestError(f"limit must be between 1 and {MAX_CHECK_MATCHES}")
        if not 0.0 <= min_similarity <= 1.0:
            raise InvalidRequestError("min_similarity must be between 0 and 1")

        errors = [_candidate_error(candidate) for candidate in candidates]
        usable = [index for index, error in enumerate(errors) if error is None]
        hashes = {index: content_hash(normalize_content(candidates[index])) for index in usable}

        vectors: dict[int, list[float]] = {}
        degraded = not (self._embedder and self._settings.embeddings_enabled)
        if usable and self._embedder and not degraded:
            try:
                embedded = await self._embedder.embed([candidates[index] for index in usable])
                vectors = dict(zip(usable, embedded, strict=True))
                degraded = False
            except Exception:
                degraded = True

        results: list[CandidateCheck] = []
        async with self._sessions() as session:
            exact_rows = {
                row.content_hash: row
                for row in (
                    await session.scalars(
                        select(AssertionRow).where(
                            AssertionRow.content_hash.in_(set(hashes.values()))
                        )
                    )
                ).all()
            }
            for index, error in enumerate(errors):
                if error is not None:
                    results.append(CandidateCheck(index=index, error=error))
                    continue
                matches: list[CandidateMatch] = []
                exact = exact_rows.get(hashes[index])
                if exact is not None:
                    matches.append(_match(exact, exact=True, similarity=1.0))
                near = select(AssertionRow).where(
                    AssertionRow.status.in_(_CHECKED_STATUSES),
                    AssertionRow.content_hash != hashes[index],
                )
                if index in vectors:
                    distance = AssertionRow.embedding.cosine_distance(vectors[index])
                    rows = (
                        await session.execute(
                            near.add_columns(distance.label("distance"))
                            .where(
                                AssertionRow.embedding_state == EmbeddingState.READY,
                                AssertionRow.embedding.is_not(None),
                                AssertionRow.embedding_model == self._settings.embedding_model,
                                distance <= 1.0 - min_similarity,
                            )
                            .order_by(distance, AssertionRow.id)
                            .limit(limit)
                        )
                    ).tuples()
                    matches.extend(
                        _match(row, exact=False, similarity=round(1.0 - float(found), 3))
                        for row, found in rows
                    )
                else:
                    # Rank by how many distinct words a stored assertion shares with the
                    # candidate, and require a fair share of them: term frequency would let a
                    # long record full of common words outrank the one that says the same thing.
                    excerpt = candidates[index][:_LEXICAL_CANDIDATE_CHARS]
                    words = func.tsvector_to_array(func.to_tsvector("simple", excerpt))
                    any_word = cast(
                        func.replace(cast(func.plainto_tsquery("simple", excerpt), Text), "&", "|"),
                        TSQUERY,
                    )
                    stored_word = func.unnest(
                        func.tsvector_to_array(AssertionRow.search_vector)
                    ).column_valued("word")
                    shared = (
                        select(func.count()).where(stored_word == func.any(words)).scalar_subquery()
                    )
                    rows_by_words = (
                        await session.scalars(
                            near.where(
                                AssertionRow.search_vector.op("@@")(any_word),
                                shared * _LEXICAL_SHARE_DENOMINATOR
                                >= func.cardinality(words) * _LEXICAL_SHARE_NUMERATOR,
                            )
                            .order_by(shared.desc(), AssertionRow.id)
                            .limit(limit)
                        )
                    ).all()
                    matches.extend(
                        _match(row, exact=False, similarity=None) for row in rows_by_words
                    )
                results.append(CandidateCheck(index=index, matches=matches))
        SEARCH_LATENCY.labels(mode="check_text" if degraded else "check").observe(
            time.perf_counter() - started
        )
        return CandidateCheckPage(results=results, embedding_degraded=degraded)

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

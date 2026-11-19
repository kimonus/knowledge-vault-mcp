"""Exhaustive inspection queries. No ranking cap, inference, writes or corpus materialization."""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, TypeVar
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import (
    Select,
    String,
    case,
    cast,
    distinct,
    exists,
    func,
    or_,
    select,
    text,
    union_all,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import raiseload

from knowledge_vault.config import Settings
from knowledge_vault.domain.enums import AssertionKind, AssertionStatus
from knowledge_vault.domain.inspection import (
    BrowseFilters,
    BrowseRequest,
    CatalogRequest,
    CursorState,
    EvidenceRequest,
    InspectionContext,
    PageRequest,
)
from knowledge_vault.domain.maintenance import UsageView
from knowledge_vault.domain.models import ArtifactRef, AssertionInput, SourceView
from knowledge_vault.domain.responses import (
    AssertionDetailResponse,
    AssertionSummary,
    CatalogItem,
    CatalogPage,
    InspectionPage,
    InspectionRelation,
    RawTemporalEvidence,
)
from knowledge_vault.domain.temporal import StateIdentity
from knowledge_vault.persistence.tables import (
    ArtifactRow,
    AssertionArtifactRow,
    AssertionLinkRow,
    AssertionRow,
    ConflictRow,
    KnowledgeContextRow,
    SourceRow,
    TemporalEvidenceRow,
    TemporalRetirementRow,
    TopicAliasRow,
)
from knowledge_vault.services.errors import InvalidRequestError, NotFoundError
from knowledge_vault.services.inspection_cursors import InspectionCursors
from knowledge_vault.services.maintenance import UsageService
from knowledge_vault.services.search import SearchService, assertion_view

T = TypeVar("T", bound=BaseModel)
MAX_RESPONSE_BYTES = 2_000_000
SUMMARY_FIELDS = tuple(
    name for name in AssertionSummary.model_fields if name not in {"excerpt", "content_truncated"}
)


class BrowseService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], settings: Settings) -> None:
        self._sessions = sessions
        self._settings = settings
        self.cursors = InspectionCursors(settings.token_pepper or "development-only-inspection")

    async def aliases(self) -> dict[str, str]:
        async with self._sessions() as session:
            return dict(
                (
                    await session.execute(
                        select(TopicAliasRow)
                        .with_only_columns(TopicAliasRow.alias, TopicAliasRow.canonical)
                        .order_by(TopicAliasRow.alias)
                        .limit(1000)
                    )
                )
                .tuples()
                .all()
            )

    def start(
        self,
        operation: str,
        request: PageRequest,
        context: InspectionContext,
        aliases: dict[str, str] | None = None,
    ) -> tuple[str, CursorState]:
        if request.limit > min(100, self._settings.max_page_size):
            raise InvalidRequestError("inspection page size exceeds the configured maximum")
        binding = self.cursors.binding(operation, request, context, aliases or {})
        return binding, self.cursors.start(request.cursor, binding)

    @staticmethod
    def _filtered(
        filters: BrowseFilters, aliases: dict[str, str], evaluated_at: datetime
    ) -> Select[tuple[AssertionRow]]:
        topics = set(filters.topics)
        for alias, canonical in aliases.items():
            if alias in topics or canonical in topics:
                topics.update((alias, canonical))
        expanded = filters.model_copy(update={"topics": sorted(topics), "review_due": False})
        statement = SearchService.filtered(expanded)
        if filters.review_due:
            statement = statement.where(AssertionRow.review_after <= evaluated_at)
        if filters.missing_topic:
            statement = statement.where(func.cardinality(AssertionRow.topics) == 0)
        if filters.missing_context:
            statement = statement.where(AssertionRow.context_id.is_(None))
        if filters.missing_facet:
            statement = statement.where(AssertionRow.facet.is_(None))
        return statement

    async def _page(
        self,
        statement: Select[Any],
        request: PageRequest,
        binding: str,
        state: CursorState,
        convert: Callable[[RowMapping], T],
        *,
        uuid_key: bool = False,
    ) -> InspectionPage[T]:
        key = statement.selected_columns["_key"]
        order_key = cast(key, String).collate("C") if not uuid_key else key
        if state.key is not None:
            boundary: UUID | str = state.key
            if uuid_key:
                try:
                    boundary = UUID(state.key)
                except ValueError as exc:
                    raise InvalidRequestError("invalid UUID page boundary") from exc
            statement = statement.where(order_key > boundary)
        async with self._sessions() as session:
            if isinstance(request, CatalogRequest):
                # Measured 50k catalogs spent >300 ms in JIT compilation. This bounded
                # aggregate benefits from ordinary execution; SET LOCAL cannot leak to writers.
                await session.execute(text("SET LOCAL jit = off"))
            rows = (
                (await session.execute(statement.order_by(order_key).limit(request.limit + 1)))
                .mappings()
                .all()
            )
        selected = list(rows[: request.limit])
        while True:
            page = InspectionPage[T](
                items=[convert(row) for row in selected],
                page_size=request.limit,
                evaluated_at=state.evaluated_at,
                next_cursor=(
                    self.cursors.encode(binding, state, str(selected[-1]["_key"]))
                    if selected and len(rows) > len(selected)
                    else None
                ),
            )
            if len(page.model_dump_json().encode()) <= MAX_RESPONSE_BYTES:
                return page
            if len(selected) <= 1:
                raise InvalidRequestError("one retained item exceeds the inspection response bound")
            selected.pop()

    async def assertions(
        self, request: BrowseRequest, context: InspectionContext
    ) -> InspectionPage[AssertionSummary]:
        aliases = await self.aliases()
        binding, state = self.start("assertions", request, context, aliases)
        statement = self._filtered(request.filters, aliases, state.evaluated_at)
        if request.mode == "lexical":
            statement = statement.where(
                AssertionRow.search_vector.op("@@")(
                    func.websearch_to_tsquery("simple", request.query)
                )
            )
        statement = statement.with_only_columns(
            AssertionRow.id.label("_key"),
            *(getattr(AssertionRow, field) for field in SUMMARY_FIELDS),
            func.left(AssertionRow.content, 400).label("excerpt"),
            (func.length(AssertionRow.content) > 400).label("content_truncated"),
        )
        return await self._page(
            statement,
            request,
            binding,
            state,
            lambda row: AssertionSummary.model_validate(dict(row)),
            uuid_key=True,
        )

    async def _exists(self, assertion_id: UUID) -> None:
        async with self._sessions() as session:
            if not await session.scalar(
                select(AssertionRow.id).where(AssertionRow.id == assertion_id)
            ):
                raise NotFoundError("assertion was not found")

    async def sources(
        self, assertion_id: UUID, request: PageRequest, context: InspectionContext
    ) -> InspectionPage[SourceView]:
        binding, state = self.start(f"sources:{assertion_id}", request, context)
        await self._exists(assertion_id)
        statement = select(
            SourceRow.url.label("_key"),
            SourceRow.url,
            SourceRow.title,
            SourceRow.publisher,
            SourceRow.retrieved_at,
        ).where(SourceRow.assertion_id == assertion_id)
        return await self._page(
            statement, request, binding, state, lambda row: SourceView.model_validate(dict(row))
        )

    async def artifacts(
        self, assertion_id: UUID, request: PageRequest, context: InspectionContext
    ) -> InspectionPage[ArtifactRef]:
        binding, state = self.start(f"artifacts:{assertion_id}", request, context)
        await self._exists(assertion_id)
        statement = (
            select(
                ArtifactRow.id.label("_key"),
                ArtifactRow.id,
                ArtifactRow.filename,
                ArtifactRow.media_type,
                ArtifactRow.size_bytes,
                ArtifactRow.description,
            )
            .join(AssertionArtifactRow, AssertionArtifactRow.artifact_id == ArtifactRow.id)
            .where(AssertionArtifactRow.assertion_id == assertion_id)
        )
        return await self._page(
            statement,
            request,
            binding,
            state,
            lambda row: ArtifactRef.model_validate(dict(row)),
            uuid_key=True,
        )

    async def identities(
        self, assertion_id: UUID, request: PageRequest, context: InspectionContext
    ) -> InspectionPage[StateIdentity]:
        binding, state = self.start(f"identities:{assertion_id}", request, context)
        await self._exists(assertion_id)
        identity = TemporalEvidenceRow.payload["temporal"]["identity"]
        key = func.concat(
            identity["subject"].as_string(),
            "|",
            identity["property"].as_string(),
            "|",
            identity["scope"].as_string(),
            "|",
            identity["state_type"].as_string(),
        )
        identities = (
            select(key.label("_key"), identity.label("identity"))
            .where(TemporalEvidenceRow.assertion_id == assertion_id)
            .distinct()
            .subquery()
        )
        return await self._page(
            select(identities),
            request,
            binding,
            state,
            lambda row: StateIdentity.model_validate(row["identity"]),
        )

    async def detail(
        self, assertion_id: UUID, context: InspectionContext
    ) -> AssertionDetailResponse:
        async with self._sessions() as session:
            row = await session.scalar(
                select(AssertionRow).options(raiseload("*")).where(AssertionRow.id == assertion_id)
            )
            if row is None:
                raise NotFoundError("assertion was not found")
            view = assertion_view(row, include_related=False)
        usage = await UsageService(self._sessions).views([assertion_id])
        view.usage = usage.get(assertion_id, UsageView())
        page_request = PageRequest(limit=min(20, self._settings.max_page_size))
        sources = await self.sources(assertion_id, page_request, context)
        artifacts = await self.artifacts(assertion_id, page_request, context)
        identities = await self.identities(assertion_id, page_request, context)
        result = AssertionDetailResponse(
            **view.model_dump(
                exclude={
                    "sources",
                    "artifacts",
                    "temporal_identities",
                    "temporal_identities_truncated",
                }
            ),
            sources=sources.items,
            artifacts=artifacts.items,
            temporal_identities=identities.items,
            temporal_identities_truncated=identities.next_cursor is not None,
            source_page=sources,
            artifact_page=artifacts,
            identity_page=identities,
        )
        if len(result.model_dump_json().encode()) > MAX_RESPONSE_BYTES:
            raise InvalidRequestError("assertion detail exceeds the inspection response bound")
        return result

    async def evidence(
        self, request: EvidenceRequest, context: InspectionContext
    ) -> InspectionPage[RawTemporalEvidence]:
        binding, state = self.start("evidence", request, context)
        known_at = request.known_at or datetime.now(UTC)
        retired = exists().where(
            TemporalRetirementRow.evidence_key == TemporalEvidenceRow.evidence_key,
            TemporalRetirementRow.retired_at <= known_at,
        )
        statement = select(
            TemporalEvidenceRow.evidence_key.label("_key"),
            TemporalEvidenceRow.evidence_key,
            TemporalEvidenceRow.assertion_id,
            TemporalEvidenceRow.recorded_at,
            TemporalEvidenceRow.payload,
            retired.label("retired"),
        ).where(TemporalEvidenceRow.identity_hash == request.identity.digest)
        if request.known_at:
            statement = statement.where(TemporalEvidenceRow.recorded_at <= request.known_at)

        def evidence(row: RowMapping) -> RawTemporalEvidence:
            return RawTemporalEvidence(
                **{
                    name: row[name]
                    for name in ("evidence_key", "assertion_id", "recorded_at", "retired")
                },
                assertion=AssertionInput.model_validate(row["payload"]),
            )

        return await self._page(statement, request, binding, state, evidence)

    async def relations(
        self, assertion_id: UUID, request: PageRequest, context: InspectionContext
    ) -> InspectionPage[InspectionRelation]:
        binding, state = self.start(f"relations:{assertion_id}", request, context)
        await self._exists(assertion_id)
        links = select(
            func.concat(
                "link:",
                AssertionLinkRow.source_id,
                ":",
                AssertionLinkRow.target_id,
                ":",
                AssertionLinkRow.kind,
            ).label("_key"),
            func.jsonb_build_object(
                "type",
                "link",
                "edge",
                func.jsonb_build_object(
                    "source_id",
                    AssertionLinkRow.source_id,
                    "target_id",
                    AssertionLinkRow.target_id,
                    "kind",
                    AssertionLinkRow.kind,
                    "position",
                    AssertionLinkRow.position,
                ),
            ).label("item"),
        ).where(
            or_(
                AssertionLinkRow.source_id == assertion_id,
                AssertionLinkRow.target_id == assertion_id,
            )
        )
        supersession = select(
            func.concat("supersession:", AssertionRow.id).label("_key"),
            func.jsonb_build_object(
                "type",
                "supersession",
                "predecessor_id",
                AssertionRow.supersedes_id,
                "successor_id",
                AssertionRow.id,
            ).label("item"),
        ).where(
            AssertionRow.supersedes_id.is_not(None),
            or_(AssertionRow.id == assertion_id, AssertionRow.supersedes_id == assertion_id),
        )
        conflicts = select(
            func.concat("conflict:", ConflictRow.id).label("_key"),
            func.jsonb_build_object(
                "type",
                "conflict",
                "conflict",
                func.jsonb_build_object(
                    "id",
                    ConflictRow.id,
                    "left_assertion_id",
                    ConflictRow.left_assertion_id,
                    "right_assertion_id",
                    ConflictRow.right_assertion_id,
                    "reason",
                    ConflictRow.reason,
                    "created_at",
                    ConflictRow.created_at,
                    "resolved_at",
                    ConflictRow.resolved_at,
                ),
            ).label("item"),
        ).where(
            or_(
                ConflictRow.left_assertion_id == assertion_id,
                ConflictRow.right_assertion_id == assertion_id,
            )
        )
        edges = union_all(links, supersession, conflicts).subquery()
        return await self._page(
            select(edges),
            request,
            binding,
            state,
            lambda row: InspectionRelation.model_validate(row["item"]),
        )

    async def catalog(self, request: CatalogRequest, context: InspectionContext) -> CatalogPage:
        aliases = await self.aliases()
        binding, state = self.start("catalog", request, context, aliases)
        eligible = (
            self._filtered(request.filters, aliases, state.evaluated_at)
            .with_only_columns(
                AssertionRow.id,
                AssertionRow.topics,
                AssertionRow.context_id,
                AssertionRow.facet,
                AssertionRow.status,
                AssertionRow.kind,
                AssertionRow.evidence_state,
                AssertionRow.workflow_state,
            )
            .cte("eligible")
        )
        topics = None
        topic_labels = None
        if request.dimension == "topics":
            topics = select(
                eligible,
                func.unnest(
                    case(
                        (func.cardinality(eligible.c.topics) == 0, cast([""], ARRAY(String))),
                        else_=eligible.c.topics,
                    )
                ).label("topic"),
            ).cte("topics")
            key = func.coalesce(TopicAliasRow.canonical, topics.c.topic)
            canonical = (
                select(topics, key.label("canonical"))
                .outerjoin(TopicAliasRow, TopicAliasRow.alias == topics.c.topic)
                .cte("canonical_topics")
            )
            members = (
                select(
                    canonical.c.id,
                    canonical.c.canonical.label("key"),
                    canonical.c.canonical.label("label"),
                    canonical.c.status,
                    canonical.c.kind,
                    canonical.c.evidence_state,
                    canonical.c.workflow_state,
                )
                .distinct()
                .cte("members")
            )
            topic_labels = (
                select(
                    canonical.c.canonical,
                    func.array_agg(distinct(canonical.c.topic)).label("stored_labels"),
                )
                .group_by(canonical.c.canonical)
                .cte("topic_labels")
            )
        else:
            key = (
                func.coalesce(cast(eligible.c.context_id, String), "")
                if request.dimension == "contexts"
                else func.coalesce(eligible.c.facet, "")
            )
            label = (
                func.coalesce(KnowledgeContextRow.name, "")
                if request.dimension == "contexts"
                else key
            )
            statement = select(
                eligible.c.id,
                key.label("key"),
                label.label("label"),
                eligible.c.status,
                eligible.c.kind,
                eligible.c.evidence_state,
                eligible.c.workflow_state,
            )
            if request.dimension == "contexts":
                statement = statement.outerjoin(
                    KnowledgeContextRow, KnowledgeContextRow.id == eligible.c.context_id
                )
            members = statement.cte("members")

        def counts(field: str, values: list[str]) -> Any:
            return func.jsonb_build_object(
                *(
                    element
                    for value in values
                    for element in (
                        value,
                        func.count().filter(
                            func.coalesce(getattr(members.c, field), "unspecified") == value
                        ),
                    )
                )
            ).label(f"by_{field}")

        grouped = (
            select(
                members.c.key.label("_key"),
                members.c.label,
                func.count(distinct(members.c.id)).label("assertion_count"),
                counts("status", [value.value for value in AssertionStatus]),
                counts("kind", [value.value for value in AssertionKind]),
                counts("evidence_state", ["unknown", "reported", "inferred", "verified"]),
                counts(
                    "workflow_state",
                    [
                        "proposed",
                        "active",
                        "completed",
                        "cancelled",
                        "unresolved",
                        "resolved",
                        "unspecified",
                    ],
                ),
            )
            .group_by(members.c.key, members.c.label)
            .subquery()
        )
        statement = select(grouped)
        if topic_labels is not None:
            statement = statement.add_columns(topic_labels.c.stored_labels).join(
                topic_labels, topic_labels.c.canonical == grouped.c._key
            )
        if request.prefix is not None:
            statement = statement.where(grouped.c.label.startswith(request.prefix, autoescape=True))

        def item(row: RowMapping) -> CatalogItem:
            key = row["_key"]
            labels = sorted(row["stored_labels"]) if request.dimension == "topics" and key else []
            return CatalogItem(
                **{
                    name: row[name]
                    for name in (
                        "assertion_count",
                        "by_status",
                        "by_kind",
                        "by_evidence_state",
                        "by_workflow_state",
                    )
                },
                key=key or None,
                label=row["label"] or "Uncategorized",
                stored_labels=labels,
            )

        page = await self._page(statement, request, binding, state, item)
        return CatalogPage(**page.model_dump())

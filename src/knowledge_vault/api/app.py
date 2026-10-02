from contextlib import asynccontextmanager, suppress
from uuid import UUID

from fastapi import Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy.pool import QueuePool

from knowledge_vault.api.auth import require_scope
from knowledge_vault.api.middleware import (
    CloudflareAccessMiddleware,
    OAuthDiscoveryMiddleware,
    ObservabilityMiddleware,
    OriginValidationMiddleware,
    RequestSizeLimitMiddleware,
)
from knowledge_vault.api.schemas import (
    AppendPartRequest,
    BeginFlushRequest,
    CorrectionRequest,
    ForgetConfirmRequest,
    ForgetPreviewRequest,
    SearchRequest,
)
from knowledge_vault.auth.tokens import Principal, Scope
from knowledge_vault.container import Container
from knowledge_vault.domain.models import CommitResult, ProblemDetail
from knowledge_vault.domain.responses import (
    AbortResponse,
    AppendResponse,
    AssertionResponse,
    BeginFlushResponse,
    ConflictListResponse,
    ForgetPreviewResponse,
    ForgetResultResponse,
    SearchResponse,
    StatisticsResponse,
)
from knowledge_vault.mcp.server import create_mcp_server
from knowledge_vault.observability.metrics import DB_POOL, HEARTBEAT_AGE
from knowledge_vault.services.errors import KnowledgeVaultError, RateLimitedError


def create_app(container: Container) -> FastAPI:
    mcp_server = create_mcp_server(container)
    mcp_app = mcp_server.streamable_http_app(
        streamable_http_path="/mcp",
        stateless_http=False,
        json_response=False,
        max_request_body_size=container.settings.max_request_bytes,
        host="0.0.0.0",  # noqa: S104  # nosec B104
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        del app
        async with mcp_app.router.lifespan_context(mcp_app):
            yield
        await container.database.close()

    app = FastAPI(
        title="Knowledge Vault internal API",
        version=container.settings.version,
        lifespan=lifespan,
        docs_url="/api/docs" if container.settings.environment != "production" else None,
        openapi_url="/api/openapi.json",
    )
    app.state.container = container
    # Middleware added later wraps middleware added earlier, so the request passes through
    # CORS, observability, Origin validation, Access validation, OAuth discovery scoping, then
    # the size limit.
    app.add_middleware(RequestSizeLimitMiddleware, max_bytes=container.settings.max_request_bytes)
    app.add_middleware(
        OAuthDiscoveryMiddleware,
        # Only the edge-published hostname has an OAuth authorization server in front of it.
        advertised_hosts=(
            container.settings.cloudflare_access_public_host_set
            if container.cloudflare_authenticator is not None
            else frozenset()
        ),
    )
    if container.cloudflare_authenticator is not None:
        app.add_middleware(
            CloudflareAccessMiddleware,
            authenticator=container.cloudflare_authenticator,
            required_hosts=container.settings.cloudflare_access_public_host_set,
            private_hosts=container.settings.cloudflare_access_private_host_set,
        )
    app.add_middleware(
        OriginValidationMiddleware,
        allowed_origins=frozenset(container.settings.cors_origin_list),
    )
    app.add_middleware(ObservabilityMiddleware, tracer=container.tracer)
    if container.settings.cors_origin_list:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=container.settings.cors_origin_list,
            allow_methods=["GET", "POST", "PUT"],
            allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
            allow_credentials=False,
        )

    @app.exception_handler(KnowledgeVaultError)
    async def domain_error(request: Request, exc: KnowledgeVaultError) -> JSONResponse:
        problem = ProblemDetail(
            type=f"urn:knowledge-vault:error:{exc.code}",
            title=exc.code.replace("_", " ").title(),
            status=exc.status_code,
            detail=str(exc),
            instance=str(request.url.path),
        )
        headers: dict[str, str] | None = None
        if exc.status_code == 401:
            headers = {"WWW-Authenticate": "Bearer"}
        elif isinstance(exc, RateLimitedError):
            headers = {"Retry-After": str(exc.retry_after_seconds)}
        return JSONResponse(
            problem.model_dump(exclude_none=True),
            status_code=exc.status_code,
            headers=headers,
            media_type="application/problem+json",
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        fields = [
            {"location": ".".join(str(part) for part in error["loc"]), "code": error["type"]}
            for error in exc.errors()
        ]
        problem = ProblemDetail(
            type="urn:knowledge-vault:error:validation",
            title="Validation error",
            status=422,
            detail="request fields failed validation",
            instance=str(request.url.path),
            errors=fields,
        )
        return JSONResponse(
            problem.model_dump(exclude_none=True),
            status_code=422,
            media_type="application/problem+json",
        )

    @app.get("/health/live", include_in_schema=False)
    async def live() -> dict[str, str]:
        return {"status": "live"}

    @app.get("/health/ready", include_in_schema=False)
    async def ready() -> JSONResponse:
        database_ready = await container.database.ready()
        status = 200 if database_ready else 503
        embedding = "enabled" if container.settings.embeddings_enabled else "disabled"
        if embedding == "enabled" and getattr(container.embedder, "degraded", False):
            # Serving continues on text search; this only reports the reduced capability.
            embedding = "degraded"
        return JSONResponse(
            {
                "status": "ready" if database_ready else "not_ready",
                "database": database_ready,
                "embedding": embedding,
            },
            status_code=status,
        )

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        pool = container.database.engine.pool
        if isinstance(pool, QueuePool):
            DB_POOL.labels(state="checkedin").set(pool.checkedin())
            DB_POOL.labels(state="checkedout").set(pool.checkedout())
            DB_POOL.labels(state="overflow").set(pool.overflow())
        # A scrape must still answer when the database is down.
        with suppress(Exception):
            for name, heartbeat in (await container.operations.heartbeats()).items():
                HEARTBEAT_AGE.labels(name=name).set(heartbeat.age_seconds)
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.post("/api/v1/flushes", status_code=201)
    async def begin_flush(
        body: BeginFlushRequest,
        principal: Principal = Depends(require_scope(Scope.WRITE)),
    ) -> BeginFlushResponse:
        batch = await container.ingestion.begin(
            principal.id,
            body.idempotency_key,
            body.declared_parts,
            body.declared_items,
        )
        return BeginFlushResponse(
            batch_id=str(batch.id),
            state=batch.state,
            declared_parts=batch.declared_parts,
            declared_items=batch.declared_items,
            replayed=batch.replayed,
        )

    @app.put("/api/v1/flushes/{batch_id}/parts/{part_number}")
    async def append_part(
        batch_id: UUID,
        part_number: int,
        body: AppendPartRequest,
        principal: Principal = Depends(require_scope(Scope.WRITE)),
    ) -> AppendResponse:
        accepted, rejected, replayed = await container.ingestion.append(
            principal.id, batch_id, part_number, body.assertions
        )
        return AppendResponse(
            batch_id=str(batch_id),
            part_number=part_number,
            accepted=accepted,
            rejected=rejected,
            replayed=replayed,
        )

    @app.post("/api/v1/flushes/{batch_id}/commit")
    async def commit_flush(
        batch_id: UUID,
        principal: Principal = Depends(require_scope(Scope.WRITE)),
    ) -> CommitResult:
        return await container.ingestion.commit(principal.id, batch_id)

    @app.post("/api/v1/flushes/{batch_id}/abort")
    async def abort_flush(
        batch_id: UUID,
        principal: Principal = Depends(require_scope(Scope.WRITE)),
    ) -> AbortResponse:
        aborted = await container.ingestion.abort(principal.id, batch_id)
        return AbortResponse(batch_id=str(batch_id), aborted=aborted)

    @app.post("/api/v1/search")
    async def search_knowledge(
        body: SearchRequest,
        principal: Principal = Depends(require_scope(Scope.READ)),
    ) -> SearchResponse:
        del principal
        page = await container.search.search(
            body.query, body.filters, limit=body.limit, cursor=body.cursor
        )
        return SearchResponse(**page.model_dump())

    @app.get("/api/v1/assertions/{assertion_id}")
    async def get_assertion(
        assertion_id: UUID,
        principal: Principal = Depends(require_scope(Scope.READ)),
    ) -> AssertionResponse:
        del principal
        view = await container.search.get(assertion_id)
        return AssertionResponse(**view.model_dump())

    @app.get("/api/v1/conflicts")
    async def conflicts(
        limit: int = Query(default=50, ge=1, le=100),
        principal: Principal = Depends(require_scope(Scope.READ)),
    ) -> ConflictListResponse:
        del principal
        return ConflictListResponse.model_validate(
            {"conflicts": await container.administration.list_conflicts(limit=limit)}
        )

    @app.post("/api/v1/corrections")
    async def correct(
        body: CorrectionRequest,
        principal: Principal = Depends(require_scope(Scope.WRITE)),
    ) -> CommitResult:
        return await container.administration.correct(
            principal.id, body.idempotency_key, body.correction
        )

    @app.post("/api/v1/forget/preview")
    async def forget_preview(
        body: ForgetPreviewRequest,
        principal: Principal = Depends(require_scope(Scope.ADMIN)),
    ) -> ForgetPreviewResponse:
        return ForgetPreviewResponse.model_validate(
            await container.administration.preview_forgetting(principal.id, body.assertion_ids)
        )

    @app.post("/api/v1/forget/confirm")
    async def forget_confirm(
        body: ForgetConfirmRequest,
        principal: Principal = Depends(require_scope(Scope.ADMIN)),
    ) -> ForgetResultResponse:
        return ForgetResultResponse.model_validate(
            await container.administration.confirm_forgetting(principal.id, body.confirmation_token)
        )

    @app.get("/api/v1/statistics")
    async def statistics(
        principal: Principal = Depends(require_scope(Scope.READ)),
    ) -> StatisticsResponse:
        del principal
        return StatisticsResponse.model_validate(await container.administration.statistics())

    # Keep this catch-all mount last so ordinary HTTP routes remain authoritative.
    app.mount("/", mcp_app)
    return app

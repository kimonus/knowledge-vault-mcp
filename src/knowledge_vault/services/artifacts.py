import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import CursorResult, delete, exists, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from knowledge_vault.config import Settings
from knowledge_vault.domain.enums import ArtifactState
from knowledge_vault.domain.models import ArtifactBeginInput
from knowledge_vault.domain.secrets import detect_secret
from knowledge_vault.observability.metrics import ARTIFACT_OUTCOMES
from knowledge_vault.persistence.tables import (
    ArtifactChunkRow,
    ArtifactRow,
    AssertionArtifactRow,
)
from knowledge_vault.services.errors import (
    BatchIncompleteError,
    ConflictError,
    InvalidRequestError,
    NotFoundError,
    SecretDetectedError,
)

# A secret can straddle two chunks, so each chunk is scanned together with this much of its
# neighbours. The assembled text is scanned once more on commit.
_BOUNDARY_CHARS = 512
_STORED_CONTENT_INDEX = "uq_artifacts_stored_content"


def _idempotency_hash(principal_id: str, key: str) -> str:
    return hashlib.sha256(f"{principal_id}\0{key}".encode()).hexdigest()


def _lost_content_race(error: IntegrityError) -> bool:
    diagnostics = getattr(error.orig, "diag", None)
    return getattr(diagnostics, "constraint_name", None) == _STORED_CONTENT_INDEX


@dataclass(frozen=True, slots=True)
class StoredArtifact:
    artifact_id: UUID
    filename: str
    media_type: str
    size_bytes: int
    sha256: str
    deduplicated: bool
    replayed: bool


@dataclass(frozen=True, slots=True)
class ArtifactContent:
    artifact_id: UUID
    filename: str
    media_type: str
    description: str | None
    size_bytes: int
    sha256: str
    total_chars: int
    offset: int
    content: str
    next_offset: int | None


class ArtifactService:
    """Chunked, idempotent upload of text artifacts and bounded reads of their content."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession], settings: Settings) -> None:
        self._sessions = sessions
        self._settings = settings

    async def begin(
        self,
        principal_id: str,
        idempotency_key: str,
        filename: str,
        media_type: str,
        declared_chunks: int,
        description: str | None = None,
    ) -> ArtifactRow:
        if not idempotency_key or len(idempotency_key) > 200:
            raise InvalidRequestError("idempotency_key must contain 1 through 200 characters")
        try:
            metadata = ArtifactBeginInput(
                filename=filename,
                media_type=media_type,
                declared_chunks=declared_chunks,
                description=description or None,
            )
        except ValidationError as exc:
            issue = exc.errors(include_input=False)[0]
            message = str(issue["msg"]).removeprefix("Value error, ")
            if "suspected" in message:
                raise SecretDetectedError(message) from None
            location = ".".join(str(part) for part in issue["loc"])
            raise InvalidRequestError(f"{location}: {message}" if location else message) from None
        if metadata.declared_chunks > self._settings.artifact_max_chunks:
            raise InvalidRequestError(
                f"declared_chunks must be between 1 and {self._settings.artifact_max_chunks}"
            )
        key_hash = _idempotency_hash(principal_id, idempotency_key)
        async with self._sessions.begin() as session:
            existing = await session.scalar(
                select(ArtifactRow).where(
                    ArtifactRow.principal_id == principal_id,
                    ArtifactRow.idempotency_hash == key_hash,
                )
            )
            if existing:
                if (
                    existing.filename != metadata.filename
                    or existing.media_type != metadata.media_type
                    or existing.declared_chunks != metadata.declared_chunks
                    or existing.description != metadata.description
                ):
                    raise ConflictError("idempotency key was already used for another artifact")
                existing.replayed = True
                return existing
            artifact = ArtifactRow(
                principal_id=principal_id,
                idempotency_hash=key_hash,
                filename=metadata.filename,
                media_type=metadata.media_type,
                description=metadata.description,
                declared_chunks=metadata.declared_chunks,
                expires_at=datetime.now(UTC)
                + timedelta(seconds=self._settings.staging_ttl_seconds),
            )
            session.add(artifact)
        return artifact

    async def append(
        self, principal_id: str, artifact_id: UUID, chunk_number: int, text: str
    ) -> tuple[int, bool]:
        """Stage one chunk. Return (characters accepted, whether this repeated an earlier call)."""
        limit = self._settings.artifact_max_chunk_chars
        if not 1 <= len(text) <= limit:
            raise InvalidRequestError(f"a chunk must contain 1 through {limit} characters")
        if "\x00" in text:
            raise InvalidRequestError("text must not contain NUL characters")
        payload_hash = hashlib.sha256(text.encode()).hexdigest()
        secret: str | None = None
        expired = False
        async with self._sessions.begin() as session:
            artifact = await self._locked(session, principal_id, artifact_id)
            if artifact.state != ArtifactState.OPEN:
                raise ConflictError(f"cannot append to an artifact in {artifact.state} state")
            if artifact.expires_at <= datetime.now(UTC):
                expired = True
                await session.delete(artifact)
            else:
                if not 1 <= chunk_number <= artifact.declared_chunks:
                    raise ConflictError("chunk_number is outside the declared range")
                prior = await session.get(ArtifactChunkRow, (artifact_id, chunk_number))
                if prior:
                    if prior.payload_hash != payload_hash:
                        raise ConflictError(
                            "chunk number was already accepted with a different payload"
                        )
                    return len(text), True
                secret = await self._scan_with_neighbours(session, artifact_id, chunk_number, text)
                if secret:
                    # Nothing of a rejected artifact is kept, including the chunks already staged.
                    await session.delete(artifact)
                else:
                    session.add(
                        ArtifactChunkRow(
                            artifact_id=artifact_id,
                            chunk_number=chunk_number,
                            payload_hash=payload_hash,
                            content=text,
                        )
                    )
        # Raised only after the transaction that discarded the artifact has committed.
        if expired:
            ARTIFACT_OUTCOMES.labels(outcome="expired").inc()
            raise ConflictError("artifact upload has expired")
        if secret:
            ARTIFACT_OUTCOMES.labels(outcome="rejected_secret").inc()
            raise SecretDetectedError(
                f"suspected {secret}; the artifact was discarded. Store only a reference to the "
                "secret"
            )
        return len(text), False

    async def commit(self, principal_id: str, artifact_id: UUID) -> StoredArtifact:
        for _ in range(2):
            try:
                return await self._commit_once(principal_id, artifact_id)
            except IntegrityError as error:
                # Another upload stored the same content first; the rerun records a duplicate.
                if not _lost_content_race(error):
                    raise
        raise ConflictError("commit conflicted with a concurrent upload; retry the same commit")

    async def _commit_once(self, principal_id: str, artifact_id: UUID) -> StoredArtifact:
        secret: str | None = None
        result: StoredArtifact | None = None
        async with self._sessions.begin() as session:
            artifact = await self._locked(session, principal_id, artifact_id)
            if artifact.state != ArtifactState.OPEN:
                stored = await self._canonical(session, artifact)
                return self._result(stored, deduplicated=stored.id != artifact.id, replayed=True)
            if artifact.expires_at <= datetime.now(UTC):
                await session.delete(artifact)
            else:
                chunks = (
                    await session.execute(
                        select(ArtifactChunkRow.chunk_number, ArtifactChunkRow.content)
                        .where(ArtifactChunkRow.artifact_id == artifact_id)
                        .order_by(ArtifactChunkRow.chunk_number)
                    )
                ).all()
                received = {number for number, _ in chunks}
                missing = sorted(set(range(1, artifact.declared_chunks + 1)) - received)
                if missing:
                    raise BatchIncompleteError(f"artifact is incomplete: missing chunks={missing}")
                content = "".join(text for _, text in chunks)
                encoded = content.encode()
                if len(encoded) > self._settings.artifact_max_bytes:
                    raise InvalidRequestError(
                        f"artifact exceeds {self._settings.artifact_max_bytes} bytes"
                    )
                secret = detect_secret(content)
                if secret:
                    await session.delete(artifact)
                else:
                    digest = hashlib.sha256(encoded).hexdigest()
                    await session.execute(
                        delete(ArtifactChunkRow).where(ArtifactChunkRow.artifact_id == artifact_id)
                    )
                    existing = await session.scalar(
                        select(ArtifactRow).where(
                            ArtifactRow.state == ArtifactState.STORED,
                            ArtifactRow.content_sha256 == digest,
                        )
                    )
                    if existing:
                        artifact.state = ArtifactState.DUPLICATE
                        artifact.duplicate_of = existing.id
                        ARTIFACT_OUTCOMES.labels(outcome="duplicate").inc()
                        return self._result(existing, deduplicated=True, replayed=False)
                    artifact.state = ArtifactState.STORED
                    artifact.content_text = content
                    artifact.content_sha256 = digest
                    artifact.size_bytes = len(encoded)
                    artifact.stored_at = datetime.now(UTC)
                    await session.flush()
                    result = self._result(artifact, deduplicated=False, replayed=False)
        # Raised only after the transaction that discarded the artifact has committed.
        if secret:
            ARTIFACT_OUTCOMES.labels(outcome="rejected_secret").inc()
            raise SecretDetectedError(
                f"suspected {secret}; the artifact was discarded. Store only a reference to the "
                "secret"
            )
        if result is None:
            ARTIFACT_OUTCOMES.labels(outcome="expired").inc()
            raise ConflictError("artifact upload has expired")
        ARTIFACT_OUTCOMES.labels(outcome="stored").inc()
        return result

    async def read(
        self, artifact_id: UUID, *, offset: int = 0, limit: int = 8000
    ) -> ArtifactContent:
        maximum = self._settings.artifact_max_chunk_chars
        if offset < 0:
            raise InvalidRequestError("offset must not be negative")
        if not 1 <= limit <= maximum:
            raise InvalidRequestError(f"limit must be between 1 and {maximum}")
        async with self._sessions() as session:
            artifact = await session.get(ArtifactRow, artifact_id)
            if artifact is not None and artifact.state != ArtifactState.OPEN:
                artifact = await self._canonical(session, artifact)
            if artifact is None or artifact.state != ArtifactState.STORED:
                raise NotFoundError("artifact was not found")
            # Slice in the database so that a page never loads the whole artifact.
            content, total = (
                await session.execute(
                    select(
                        func.substr(ArtifactRow.content_text, offset + 1, limit),
                        func.char_length(func.coalesce(ArtifactRow.content_text, "")),
                    ).where(ArtifactRow.id == artifact.id)
                )
            ).one()
        end = offset + len(content)
        return ArtifactContent(
            artifact_id=artifact.id,
            filename=artifact.filename,
            media_type=artifact.media_type,
            description=artifact.description,
            size_bytes=artifact.size_bytes or 0,
            sha256=artifact.content_sha256 or "",
            total_chars=total,
            offset=offset,
            content=content,
            next_offset=end if content and end < total else None,
        )

    async def purge(self) -> tuple[int, int]:
        """Discard abandoned uploads and stored artifacts that no assertion describes.

        Return (expired uploads, unreferenced artifacts). An artifact is kept for the staging
        retention period after it is stored, so that a flush has time to link it.
        """
        now = datetime.now(UTC)
        cutoff = now - timedelta(seconds=self._settings.staging_retention_seconds)
        linked = exists().where(AssertionArtifactRow.artifact_id == ArtifactRow.id)
        async with self._sessions.begin() as session:
            expired = await session.execute(
                delete(ArtifactRow).where(
                    ArtifactRow.state == ArtifactState.OPEN, ArtifactRow.expires_at <= now
                )
            )
            await session.execute(
                delete(ArtifactRow).where(
                    ArtifactRow.state == ArtifactState.DUPLICATE, ArtifactRow.updated_at < cutoff
                )
            )
            unreferenced = await session.execute(
                delete(ArtifactRow).where(
                    ArtifactRow.state == ArtifactState.STORED,
                    ArtifactRow.stored_at < cutoff,
                    ~linked,
                )
            )
        expired_count = int(cast(CursorResult[Any], expired).rowcount or 0)
        unreferenced_count = int(cast(CursorResult[Any], unreferenced).rowcount or 0)
        if expired_count:
            ARTIFACT_OUTCOMES.labels(outcome="expired").inc(expired_count)
        if unreferenced_count:
            ARTIFACT_OUTCOMES.labels(outcome="unreferenced_purged").inc(unreferenced_count)
        return expired_count, unreferenced_count

    @staticmethod
    async def _locked(session: AsyncSession, principal_id: str, artifact_id: UUID) -> ArtifactRow:
        artifact = await session.scalar(
            select(ArtifactRow)
            .where(ArtifactRow.id == artifact_id, ArtifactRow.principal_id == principal_id)
            .with_for_update()
        )
        if not artifact:
            raise NotFoundError("artifact was not found")
        return artifact

    @staticmethod
    async def _canonical(session: AsyncSession, artifact: ArtifactRow) -> ArtifactRow:
        if artifact.duplicate_of is None:
            return artifact
        stored = await session.get(ArtifactRow, artifact.duplicate_of)
        if stored is None:  # pragma: no cover - the foreign key removes duplicates with it
            raise NotFoundError("artifact was not found")
        return stored

    @staticmethod
    async def _scan_with_neighbours(
        session: AsyncSession, artifact_id: UUID, chunk_number: int, text: str
    ) -> str | None:
        neighbours = dict(
            (
                await session.execute(
                    select(ArtifactChunkRow.chunk_number, ArtifactChunkRow.content).where(
                        ArtifactChunkRow.artifact_id == artifact_id,
                        ArtifactChunkRow.chunk_number.in_([chunk_number - 1, chunk_number + 1]),
                    )
                )
            )
            .tuples()
            .all()
        )
        before = neighbours.get(chunk_number - 1, "")[-_BOUNDARY_CHARS:]
        after = neighbours.get(chunk_number + 1, "")[:_BOUNDARY_CHARS]
        return detect_secret(before + text + after)

    @staticmethod
    def _result(artifact: ArtifactRow, *, deduplicated: bool, replayed: bool) -> StoredArtifact:
        return StoredArtifact(
            artifact_id=artifact.id,
            filename=artifact.filename,
            media_type=artifact.media_type,
            size_bytes=artifact.size_bytes or 0,
            sha256=artifact.content_sha256 or "",
            deduplicated=deduplicated,
            replayed=replayed,
        )

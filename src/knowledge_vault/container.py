from dataclasses import dataclass

from knowledge_vault.auth.cloudflare import CloudflareAccessAuthenticator
from knowledge_vault.auth.tokens import TokenAuthenticator
from knowledge_vault.config import Settings
from knowledge_vault.embeddings.base import EmbeddingProvider
from knowledge_vault.embeddings.providers import SentenceTransformerProvider
from knowledge_vault.persistence.database import Database
from knowledge_vault.services.administration import AdministrationService
from knowledge_vault.services.ingestion import IngestionService
from knowledge_vault.services.search import SearchService


@dataclass(slots=True)
class Container:
    settings: Settings
    database: Database
    authenticator: TokenAuthenticator
    cloudflare_authenticator: CloudflareAccessAuthenticator | None
    ingestion: IngestionService
    search: SearchService
    administration: AdministrationService
    embedder: EmbeddingProvider | None


def build_container(settings: Settings, *, embedder: EmbeddingProvider | None = None) -> Container:
    database = Database(settings.database_url)
    authenticator = TokenAuthenticator.from_json(settings.bootstrap_tokens, settings.token_pepper)
    cloudflare_authenticator = None
    if settings.cloudflare_access_enabled:
        cloudflare_authenticator = CloudflareAccessAuthenticator(
            issuer_url=settings.cloudflare_access_issuer_url,
            audience=settings.cloudflare_access_audience,
            allowed_emails=settings.cloudflare_access_email_set,
            scopes=settings.cloudflare_access_scope_set,
        )
    effective_embedder = embedder
    if effective_embedder is None and settings.embeddings_enabled:
        effective_embedder = SentenceTransformerProvider(
            settings.embedding_model, settings.embedding_dimensions
        )
    ingestion = IngestionService(database.sessions, settings)
    search = SearchService(database.sessions, settings, effective_embedder)
    administration = AdministrationService(database.sessions, ingestion)
    return Container(
        settings=settings,
        database=database,
        authenticator=authenticator,
        cloudflare_authenticator=cloudflare_authenticator,
        ingestion=ingestion,
        search=search,
        administration=administration,
        embedder=effective_embedder,
    )

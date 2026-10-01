from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from knowledge_vault.auth.hosts import canonical_host
from knowledge_vault.auth.tokens import Scope
from knowledge_vault.domain.secrets import detect_secret
from knowledge_vault.ingestion_policy import DEFAULT_INGESTION_POLICY


def _host_set(raw: str) -> frozenset[str]:
    return frozenset(canonical_host(item) for item in raw.split(",") if item.strip())


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="KNOWLEDGE_VAULT_", env_file=".env", extra="ignore"
    )

    environment: Literal["production", "development", "test"] = "production"
    service_name: str = "knowledge-vault"
    version: str = "0.1.0"
    database_url: str = "postgresql://knowledge_vault@localhost/knowledge_vault"
    token_pepper: str = Field(default="", repr=False)
    bootstrap_tokens: str = Field(default="", repr=False)
    cors_origins: str = ""
    public_base_url: str = "https://knowledge-vault.invalid"
    auth_issuer_url: str = "https://knowledge-vault.invalid"
    ingestion_policy_version: str = Field(
        default="1", min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$"
    )
    ingestion_policy: str = Field(
        default=DEFAULT_INGESTION_POLICY, min_length=1, max_length=8192, repr=False
    )
    cloudflare_access_enabled: bool = False
    cloudflare_access_issuer_url: str = ""
    cloudflare_access_audience: str = Field(default="", repr=False)
    cloudflare_access_allowed_emails: str = Field(default="", repr=False)
    cloudflare_access_scopes: str = "knowledge:read,knowledge:write"
    # Hostnames published through the edge. Requests for them always need a signed assertion.
    # Defaults to the host of public_base_url.
    cloudflare_access_public_hosts: str = ""
    # Optional allowlist of private hostnames. When set, bearer tokens are accepted only for
    # these hosts and every other host requires a signed assertion.
    cloudflare_access_private_hosts: str = ""
    embedding_provider: str = "sentence_transformers"
    embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    embedding_dimensions: int = Field(default=384, ge=8, le=4096)
    embeddings_enabled: bool = True
    # Model weights are loaded from local files only unless a developer opts in explicitly.
    embedding_allow_download: bool = False
    max_request_bytes: int = Field(default=2_000_000, ge=1024)
    max_batch_items: int = Field(default=1000, ge=1, le=10_000)
    max_part_items: int = Field(default=100, ge=1, le=1000)
    max_parts: int = Field(default=100, ge=1, le=1000)
    max_page_size: int = Field(default=50, ge=1, le=200)
    staging_ttl_seconds: int = Field(default=86_400, ge=60)
    staging_retention_seconds: int = Field(default=604_800, ge=60)
    maintenance_interval_seconds: float = Field(default=300.0, ge=1, le=86_400)
    worker_poll_seconds: float = Field(default=2.0, ge=0.1, le=60)
    worker_metrics_port: int = Field(default=0, ge=0, le=65_535)
    embedding_batch_size: int = Field(default=32, ge=1, le=256)
    embedding_max_attempts: int = Field(default=5, ge=1, le=20)
    embedding_claim_timeout_seconds: int = Field(default=900, ge=30, le=86_400)
    rate_read_per_minute: int = Field(default=120, ge=1)
    rate_write_per_minute: int = Field(default=30, ge=1)
    rate_admin_per_minute: int = Field(default=10, ge=1)
    log_level: str = "INFO"
    # Emit request and MCP message spans. Requires the OpenTelemetry SDK and OTLP exporter in
    # the image; see observability/tracing.py.
    otel_enabled: bool = False

    @field_validator("ingestion_policy")
    @classmethod
    def validate_ingestion_policy(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("ingestion policy must contain non-whitespace guidance")
        if any(ord(character) < 32 and character not in "\n\r\t" for character in value):
            raise ValueError("ingestion policy must not contain control characters")
        if detect_secret(value):
            raise ValueError("ingestion policy must not contain secret-shaped values")
        return value

    @model_validator(mode="after")
    def validate_cloudflare_access(self) -> "Settings":
        if not self.cloudflare_access_enabled:
            return self
        missing = [
            name
            for name, value in (
                ("CLOUDFLARE_ACCESS_ISSUER_URL", self.cloudflare_access_issuer_url),
                ("CLOUDFLARE_ACCESS_AUDIENCE", self.cloudflare_access_audience),
                ("CLOUDFLARE_ACCESS_ALLOWED_EMAILS", self.cloudflare_access_allowed_emails),
            )
            if not value.strip()
        ]
        if missing:
            joined = ", ".join(f"KNOWLEDGE_VAULT_{name}" for name in missing)
            raise ValueError(f"Cloudflare Access requires {joined}")
        issuer = self.cloudflare_access_issuer_url.rstrip("/")
        if not issuer.startswith("https://") or not issuer.endswith(".cloudflareaccess.com"):
            raise ValueError(
                "KNOWLEDGE_VAULT_CLOUDFLARE_ACCESS_ISSUER_URL must be an HTTPS "
                "cloudflareaccess.com team domain"
            )
        scopes = self.cloudflare_access_scope_set
        if not scopes:
            raise ValueError("KNOWLEDGE_VAULT_CLOUDFLARE_ACCESS_SCOPES cannot be empty")
        if Scope.ADMIN in scopes:
            raise ValueError("Cloudflare Access identities cannot receive knowledge:admin")
        public_hosts = self.cloudflare_access_public_host_set
        if not public_hosts or any(host.endswith(".invalid") for host in public_hosts):
            raise ValueError(
                "Cloudflare Access requires the published hostname: set "
                "KNOWLEDGE_VAULT_CLOUDFLARE_ACCESS_PUBLIC_HOSTS or a real "
                "KNOWLEDGE_VAULT_PUBLIC_BASE_URL"
            )
        if public_hosts & self.cloudflare_access_private_host_set:
            raise ValueError("a hostname cannot be both a Cloudflare public and a private host")
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @property
    def cloudflare_access_email_set(self) -> frozenset[str]:
        return frozenset(
            item.strip().casefold()
            for item in self.cloudflare_access_allowed_emails.split(",")
            if item.strip()
        )

    @property
    def cloudflare_access_scope_set(self) -> frozenset[Scope]:
        return frozenset(
            Scope(item.strip()) for item in self.cloudflare_access_scopes.split(",") if item.strip()
        )

    @property
    def cloudflare_access_public_host_set(self) -> frozenset[str]:
        explicit = _host_set(self.cloudflare_access_public_hosts)
        if explicit:
            return explicit
        derived = urlsplit(self.public_base_url).hostname
        return frozenset({canonical_host(derived)}) if derived else frozenset()

    @property
    def cloudflare_access_private_host_set(self) -> frozenset[str]:
        return _host_set(self.cloudflare_access_private_hosts)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    if settings.environment == "production" and not settings.token_pepper:
        raise RuntimeError("KNOWLEDGE_VAULT_TOKEN_PEPPER is required in production")
    return settings

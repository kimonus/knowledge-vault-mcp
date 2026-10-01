from functools import lru_cache

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from knowledge_vault.auth.tokens import Scope


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="KNOWLEDGE_VAULT_", env_file=".env", extra="ignore"
    )

    environment: str = "production"
    service_name: str = "knowledge-vault"
    version: str = "0.1.0"
    database_url: str = "postgresql://knowledge_vault@localhost/knowledge_vault"
    token_pepper: str = Field(default="", repr=False)
    bootstrap_tokens: str = Field(default="", repr=False)
    cors_origins: str = ""
    public_base_url: str = "https://knowledge-vault.invalid"
    auth_issuer_url: str = "https://knowledge-vault.invalid"
    cloudflare_access_enabled: bool = False
    cloudflare_access_issuer_url: str = ""
    cloudflare_access_audience: str = Field(default="", repr=False)
    cloudflare_access_allowed_emails: str = Field(default="", repr=False)
    cloudflare_access_scopes: str = "knowledge:read,knowledge:write"
    embedding_provider: str = "sentence_transformers"
    embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    embedding_dimensions: int = Field(default=384, ge=8, le=4096)
    embeddings_enabled: bool = True
    max_request_bytes: int = Field(default=2_000_000, ge=1024)
    max_batch_items: int = Field(default=1000, ge=1, le=10_000)
    max_part_items: int = Field(default=100, ge=1, le=1000)
    max_parts: int = Field(default=100, ge=1, le=1000)
    max_page_size: int = Field(default=50, ge=1, le=200)
    staging_ttl_seconds: int = Field(default=86_400, ge=60)
    staging_retention_seconds: int = Field(default=604_800, ge=60)
    worker_poll_seconds: float = Field(default=2.0, ge=0.1, le=60)
    embedding_batch_size: int = Field(default=32, ge=1, le=256)
    embedding_max_attempts: int = Field(default=5, ge=1, le=20)
    rate_read_per_minute: int = Field(default=120, ge=1)
    rate_write_per_minute: int = Field(default=30, ge=1)
    rate_admin_per_minute: int = Field(default=10, ge=1)
    log_level: str = "INFO"
    otel_enabled: bool = False

    @field_validator("token_pepper")
    @classmethod
    def require_pepper_in_production(cls, value: str, info: object) -> str:
        # Final fail-closed validation happens at application construction after all fields exist.
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


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    if settings.environment == "production" and not settings.token_pepper:
        raise RuntimeError("KNOWLEDGE_VAULT_TOKEN_PEPPER is required in production")
    return settings

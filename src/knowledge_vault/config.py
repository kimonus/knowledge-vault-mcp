import os
import stat
from functools import lru_cache
from importlib import metadata
from typing import Any, Literal, cast
from urllib.parse import urlsplit

from pydantic import Field, ValidationError, field_validator, model_validator
from pydantic.fields import FieldInfo
from pydantic_core import InitErrorDetails
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

from knowledge_vault.auth.hosts import canonical_host, strict_host
from knowledge_vault.auth.tokens import Scope
from knowledge_vault.domain.secrets import detect_secret
from knowledge_vault.ingestion_policy import DEFAULT_INGESTION_POLICY


def _package_version() -> str:
    try:
        return metadata.version("knowledge-vault")
    except metadata.PackageNotFoundError:
        return "0.0.0+unknown"


def _host_set(raw: str) -> frozenset[str]:
    return frozenset(canonical_host(item) for item in raw.split(",") if item.strip())


_SECRET_FILE_FIELDS = ("database_url", "token_pepper", "bootstrap_tokens")
_SECRET_FILE_MAX_BYTES = 128 * 1024


class _SafeConfigError(ValueError):
    """A configuration diagnostic built only from fixed, nonsecret text."""


def _read_secret_file(path: object, field_name: str) -> str:
    """Read exact UTF-8 content from a bounded regular file without following its final symlink."""
    try:
        if not isinstance(path, str) or not path or "\0" in path:
            raise ValueError
        fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as source:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                raise ValueError
            value = source.read(_SECRET_FILE_MAX_BYTES + 1)
        if not value or len(value) > _SECRET_FILE_MAX_BYTES or b"\0" in value:
            raise ValueError
        return value.decode("utf-8", errors="strict")
    except (OSError, ValueError, UnicodeError):
        pass
    # Raise outside the handler so __context__ cannot retain a path-bearing OS error.
    raise RuntimeError(f"KNOWLEDGE_VAULT_{field_name.upper()}_FILE cannot be read") from None


class _FileSettingsSource(PydanticBaseSettingsSource):
    """Resolve explicit _FILE inputs once per construction, before normal sources validate."""

    def __init__(
        self, settings_cls: type[BaseSettings], sources: tuple[PydanticBaseSettingsSource, ...]
    ) -> None:
        super().__init__(settings_cls)
        self._sources = sources
        self.snapshots: list[dict[str, Any]] = []

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:
        self.snapshots = [source() for source in self._sources]
        resolved: dict[str, Any] = {}
        for name in _SECRET_FILE_FIELDS:
            file_name = f"{name}_file"
            paths = [
                source[file_name] for source in self.snapshots if source.get(file_name) is not None
            ]
            if not paths:
                continue
            if any(name in source for source in self.snapshots):
                raise RuntimeError(
                    f"KNOWLEDGE_VAULT_{name.upper()} and _FILE are mutually exclusive"
                )
            resolved[name] = _read_secret_file(paths[0], name)
        return resolved


class _SnapshotSettingsSource(PydanticBaseSettingsSource):
    """Reuse each source's one-time snapshot without retaining secret-file paths in Settings."""

    def __init__(
        self, settings_cls: type[BaseSettings], files: _FileSettingsSource, source_index: int
    ) -> None:
        super().__init__(settings_cls)
        self._files = files
        self._source_index = source_index

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:
        hidden = {f"{name}_file" for name in _SECRET_FILE_FIELDS}
        return {
            key: value
            for key, value in self._files.snapshots[self._source_index].items()
            if key not in hidden
        }


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="KNOWLEDGE_VAULT_", env_file=".env", extra="ignore", hide_input_in_errors=True
    )

    environment: Literal["production", "development", "test"] = "production"
    service_name: str = "knowledge-vault"
    # The installed package is the single source of the version.
    version: str = Field(default_factory=_package_version)
    database_url: str = Field(
        default="postgresql://knowledge_vault@localhost/knowledge_vault", repr=False
    )
    token_pepper: str = Field(default="", repr=False)
    bootstrap_tokens: str = Field(default="", repr=False)
    database_url_file: str | None = Field(default=None, repr=False, exclude=True)
    token_pepper_file: str | None = Field(default=None, repr=False, exclude=True)
    bootstrap_tokens_file: str | None = Field(default=None, repr=False, exclude=True)
    cors_origins: str = ""
    # Explicit opt-in for standalone deployments; never use an empty or wildcard host list.
    standalone_mode: bool = False
    standalone_allowed_hosts: str = ""
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
    usage_tracking_enabled: bool = True
    # Model weights are loaded from local files only unless a developer opts in explicitly.
    embedding_allow_download: bool = False
    max_request_bytes: int = Field(default=2_000_000, ge=1024)
    max_batch_items: int = Field(default=1000, ge=1, le=10_000)
    max_part_items: int = Field(default=100, ge=1, le=1000)
    max_parts: int = Field(default=100, ge=1, le=1000)
    max_page_size: int = Field(default=50, ge=1, le=200)
    # Text artifacts are uploaded in numbered chunks because hosted clients cap the size of a
    # single tool argument; the product of the two limits bounds one artifact.
    artifact_max_chunk_chars: int = Field(default=16_000, ge=256, le=100_000)
    artifact_max_chunks: int = Field(default=64, ge=1, le=1000)
    artifact_max_bytes: int = Field(default=1_048_576, ge=1024, le=16_777_216)
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
    # Watchdog thresholds. A heartbeat older than these is reported as a problem.
    watchdog_worker_max_age_seconds: int = Field(default=900, ge=60)
    watchdog_backup_max_age_seconds: int = Field(default=129_600, ge=3600)
    # Only deployments that run the backup job should be told when its heartbeat is missing.
    watchdog_expect_backup: bool = False
    # A problem that persists is announced again after this long.
    watchdog_renotify_seconds: int = Field(default=86_400, ge=600)
    # Optional notification target for the watchdog. It may embed a token, so it is a secret.
    alert_webhook_url: str = Field(default="", repr=False)
    alert_webhook_format: Literal["json", "text"] = "json"
    log_level: str = "INFO"
    # Emit request and MCP message spans. Requires the OpenTelemetry SDK and OTLP exporter in
    # the image; see observability/tracing.py.
    otel_enabled: bool = False

    def __init__(self, **values: Any) -> None:
        sanitized: ValidationError | None = None
        try:
            super().__init__(**values)
        except ValidationError as exc:
            # Model-level validation errors normally retain the complete input mapping, including
            # credentials, and nested validator exceptions may retain their own contexts.
            details = exc.errors(include_url=False)
            for detail in details:
                detail["input"] = None
                context = detail.get("ctx")
                if context is not None and "error" in context:
                    original = context["error"]
                    message = (
                        str(original)
                        if isinstance(original, _SafeConfigError)
                        else "invalid configuration"
                    )
                    context["error"] = ValueError(message)
            sanitized = ValidationError.from_exception_data(
                exc.title, cast(list[InitErrorDetails], details), hide_input=True
            )
        # Raising outside the handler avoids retaining the original error in __context__.
        if sanitized is not None:
            raise sanitized from None

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        sources = (init_settings, env_settings, dotenv_settings, file_secret_settings)
        files = _FileSettingsSource(settings_cls, sources)
        return (
            files,
            *(_SnapshotSettingsSource(settings_cls, files, i) for i in range(len(sources))),
        )

    @field_validator("ingestion_policy")
    @classmethod
    def validate_ingestion_policy(cls, value: str) -> str:
        if not value.strip():
            raise _SafeConfigError("ingestion policy must contain non-whitespace guidance")
        if any(ord(character) < 32 and character not in "\n\r\t" for character in value):
            raise _SafeConfigError("ingestion policy must not contain control characters")
        if detect_secret(value):
            raise _SafeConfigError("ingestion policy must not contain secret-shaped values")
        return value

    @field_validator("alert_webhook_url")
    @classmethod
    def validate_alert_webhook_url(cls, value: str) -> str:
        try:
            valid_scheme = not value or urlsplit(value).scheme in {"http", "https"}
        except ValueError:
            # urllib may include credential-bearing authority text in its exception.
            valid_scheme = False
        if not valid_scheme:
            raise _SafeConfigError("alert webhook URL must be an http or https URL")
        return value

    @model_validator(mode="after")
    def validate_standalone_hosts(self) -> "Settings":
        if self.standalone_mode:
            hosts = self.standalone_allowed_hosts.split(",")
            if not self.standalone_allowed_hosts.strip() or any(not host.strip() for host in hosts):
                raise _SafeConfigError("standalone mode requires an explicit host allowlist")
            for host in hosts:
                strict_host(host.strip())
        return self

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
            raise _SafeConfigError(f"Cloudflare Access requires {joined}")
        issuer = self.cloudflare_access_issuer_url.rstrip("/")
        if not issuer.startswith("https://") or not issuer.endswith(".cloudflareaccess.com"):
            raise _SafeConfigError(
                "KNOWLEDGE_VAULT_CLOUDFLARE_ACCESS_ISSUER_URL must be an HTTPS "
                "cloudflareaccess.com team domain"
            )
        scopes = self.cloudflare_access_scope_set
        if not scopes:
            raise _SafeConfigError("KNOWLEDGE_VAULT_CLOUDFLARE_ACCESS_SCOPES cannot be empty")
        if Scope.ADMIN in scopes:
            raise _SafeConfigError("Cloudflare Access identities cannot receive knowledge:admin")
        public_hosts = self.cloudflare_access_public_host_set
        if not public_hosts or any(host.endswith(".invalid") for host in public_hosts):
            raise _SafeConfigError(
                "Cloudflare Access requires the published hostname: set "
                "KNOWLEDGE_VAULT_CLOUDFLARE_ACCESS_PUBLIC_HOSTS or a real "
                "KNOWLEDGE_VAULT_PUBLIC_BASE_URL"
            )
        # Fail closed on the bearer surface: without a private-host allowlist, device bearer
        # tokens are accepted for every hostname that is not published, so an attacker who can
        # reach the origin directly with an arbitrary Host header only needs a bearer token.
        # Requiring the allowlist confines bearer acceptance to the declared private hostname(s).
        private_hosts = self.cloudflare_access_private_host_set
        if not private_hosts:
            raise _SafeConfigError(
                "Cloudflare Access requires the private hostname allowlist: set "
                "KNOWLEDGE_VAULT_CLOUDFLARE_ACCESS_PRIVATE_HOSTS to the device-bearer hostname(s) "
                "so bearer tokens are not accepted for every unpublished Host"
            )
        if any(host.endswith(".invalid") for host in private_hosts):
            raise _SafeConfigError(
                "KNOWLEDGE_VAULT_CLOUDFLARE_ACCESS_PRIVATE_HOSTS must list real hostnames"
            )
        if public_hosts & private_hosts:
            raise _SafeConfigError(
                "a hostname cannot be both a Cloudflare public and a private host"
            )
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @property
    def standalone_allowed_host_set(self) -> frozenset[str]:
        return frozenset(
            strict_host(host.strip()) for host in self.standalone_allowed_hosts.split(",")
        )

    @property
    def cloudflare_access_email_set(self) -> frozenset[str]:
        return frozenset(
            item.strip().casefold()
            for item in self.cloudflare_access_allowed_emails.split(",")
            if item.strip()
        )

    @property
    def cloudflare_access_scope_set(self) -> frozenset[Scope]:
        try:
            return frozenset(
                Scope(item.strip())
                for item in self.cloudflare_access_scopes.split(",")
                if item.strip()
            )
        except ValueError:
            pass
        raise _SafeConfigError("invalid Cloudflare Access scope") from None

    @property
    def cloudflare_access_public_host_set(self) -> frozenset[str]:
        explicit = _host_set(self.cloudflare_access_public_hosts)
        if explicit:
            return explicit
        try:
            derived = urlsplit(self.public_base_url).hostname
        except ValueError:
            # Leave the handler before raising to avoid retaining the authority in __context__.
            derived = None
            invalid_url = True
        else:
            invalid_url = False
        if invalid_url:
            raise _SafeConfigError("invalid public base URL") from None
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

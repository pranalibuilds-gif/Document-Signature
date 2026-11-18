from __future__ import annotations

from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = Field(min_length=1)
    migration_database_url: str | None = None
    jwt_secret_key: SecretStr = Field(min_length=32)
    environment: Literal["development", "test", "production"] = "development"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = Field(default=60, ge=5, le=1440)
    trust_proxy_headers: bool = False
    cors_allowed_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ]
    )
    storage_backend: Literal["local", "s3", "supabase"] = "local"
    local_storage_dir: Path = BACKEND_DIR / "storage_data"
    s3_bucket: str | None = None
    s3_region: str = "us-east-1"
    s3_endpoint_url: str | None = None
    s3_access_key_id: SecretStr | None = None
    s3_secret_access_key: SecretStr | None = None
    max_upload_size_bytes: int = Field(default=10 * 1024 * 1024, gt=0)
    frontend_base_url: str = "http://localhost:5173"
    signing_link_expire_hours: int = Field(default=168, ge=1, le=720)
    email_backend: Literal["console", "resend"] = "console"
    email_from_address: str = "noreply@example.com"
    resend_api_key: SecretStr | None = None

    @field_validator(
        "migration_database_url",
        "s3_bucket",
        "s3_endpoint_url",
        mode="before",
    )
    @classmethod
    def empty_optional_strings_to_none(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator(
        "s3_access_key_id",
        "s3_secret_access_key",
        "resend_api_key",
        mode="before",
    )
    @classmethod
    def empty_optional_secrets_to_none(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("cors_allowed_origins")
    @classmethod
    def normalize_cors_origins(cls, origins: list[str]) -> list[str]:
        normalized = [origin.strip().rstrip("/") for origin in origins]
        if any(not origin or origin == "*" for origin in normalized):
            raise ValueError("CORS origins must be explicit, non-empty origins")
        return list(dict.fromkeys(normalized))

    @model_validator(mode="after")
    def validate_provider_configuration(self) -> Settings:
        if (self.s3_access_key_id is None) != (self.s3_secret_access_key is None):
            raise ValueError(
                "S3_ACCESS_KEY_ID and S3_SECRET_ACCESS_KEY must be configured together"
            )
        if self.storage_backend in {"s3", "supabase"} and not self.s3_bucket:
            raise ValueError("S3_BUCKET is required for object storage")
        if self.storage_backend == "supabase" and (
            not self.s3_endpoint_url
            or self.s3_access_key_id is None
            or self.s3_secret_access_key is None
        ):
            raise ValueError(
                "Supabase storage requires its S3 endpoint and S3 credentials"
            )
        if self.email_backend == "resend" and (
            self.resend_api_key is None
            or not self.resend_api_key.get_secret_value()
        ):
            raise ValueError("RESEND_API_KEY is required when EMAIL_BACKEND=resend")

        if self.environment != "production":
            return self

        if self.jwt_secret_key.get_secret_value().lower().startswith(
            ("replace-with-", "change-me")
        ):
            raise ValueError("Production JWT_SECRET_KEY must not use a sample value")
        database_scheme = urlsplit(self.database_url).scheme
        if not database_scheme.startswith("postgresql"):
            raise ValueError("Production DATABASE_URL must use PostgreSQL")
        if self.migration_database_url and not urlsplit(
            self.migration_database_url
        ).scheme.startswith("postgresql"):
            raise ValueError("Production MIGRATION_DATABASE_URL must use PostgreSQL")
        if self.storage_backend == "local":
            raise ValueError("Production storage must use S3 or Supabase object storage")
        if self.email_backend != "resend":
            raise ValueError("Production email delivery must use Resend")
        if not self.email_from_address or self.email_from_address.endswith("@example.com"):
            raise ValueError(
                "Production EMAIL_FROM_ADDRESS must use a verified sender domain"
            )
        if not self.cors_allowed_origins:
            raise ValueError("Production must configure CORS_ALLOWED_ORIGINS")
        if any(
            urlsplit(origin).scheme != "https"
            or not urlsplit(origin).netloc
            or urlsplit(origin).path
            or urlsplit(origin).query
            or urlsplit(origin).fragment
            for origin in self.cors_allowed_origins
        ):
            raise ValueError("Production CORS origins must be HTTPS origins only")
        frontend = urlsplit(self.frontend_base_url)
        if frontend.scheme != "https" or not frontend.netloc:
            raise ValueError("Production FRONTEND_BASE_URL must use HTTPS")
        if self.storage_backend == "s3" and self.s3_endpoint_url:
            endpoint = urlsplit(self.s3_endpoint_url)
            if endpoint.scheme != "https" or not endpoint.netloc:
                raise ValueError("Production S3_ENDPOINT_URL must use HTTPS")
        if self.storage_backend == "supabase":
            endpoint = urlsplit(self.s3_endpoint_url or "")
            if endpoint.scheme != "https" or not endpoint.netloc:
                raise ValueError("Production Supabase S3 endpoint must use HTTPS")
        return self


settings = Settings()

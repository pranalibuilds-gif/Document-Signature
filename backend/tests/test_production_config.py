from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def valid_production_settings(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "database_url": "postgresql+psycopg://runtime:secret@db.example.test/signature",
        "jwt_secret_key": "secure-production-secret-value-32-bytes-minimum",
        "environment": "production",
        "cors_allowed_origins": ["https://sign.example.test"],
        "frontend_base_url": "https://sign.example.test",
        "storage_backend": "s3",
        "s3_bucket": "private-signature-documents",
        "email_backend": "resend",
        "email_from_address": "signatures@example.test",
        "resend_api_key": "re_test_key",
    }
    values.update(overrides)
    return values


def test_production_settings_accept_secure_provider_configuration() -> None:
    settings = Settings(_env_file=None, **valid_production_settings())

    assert settings.environment == "production"
    assert settings.storage_backend == "s3"
    assert settings.email_backend == "resend"
    assert settings.cors_allowed_origins == ["https://sign.example.test"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"database_url": "sqlite:///production.db"},
        {"migration_database_url": "sqlite:///migrations.db"},
        {"storage_backend": "local"},
        {"email_backend": "console"},
        {"cors_allowed_origins": ["*"]},
        {"cors_allowed_origins": ["http://sign.example.test"]},
        {"frontend_base_url": "http://sign.example.test"},
        {"jwt_secret_key": "replace-with-a-random-secret-at-least-32-characters"},
    ],
)
def test_production_settings_reject_insecure_configuration(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **valid_production_settings(**overrides))


def test_empty_optional_s3_credentials_fall_back_to_iam() -> None:
    settings = Settings(
        _env_file=None,
        database_url="sqlite://",
        jwt_secret_key="development-secret-value-with-more-than-32-bytes",
        storage_backend="s3",
        s3_bucket="private-signature-documents",
        s3_access_key_id="",
        s3_secret_access_key="",
    )

    assert settings.s3_access_key_id is None
    assert settings.s3_secret_access_key is None


def test_supabase_requires_endpoint_and_s3_credentials() -> None:
    with pytest.raises(ValidationError, match="Supabase storage requires"):
        Settings(
            _env_file=None,
            database_url="sqlite://",
            jwt_secret_key="development-secret-value-with-more-than-32-bytes",
            storage_backend="supabase",
            s3_bucket="private-signature-documents",
        )

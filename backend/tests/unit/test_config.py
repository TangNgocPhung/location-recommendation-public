import pytest
from pydantic import ValidationError

from app.config import Settings


def test_development_settings_parse_cors_origins() -> None:
    settings = Settings(
        app_env="development",
        allowed_origins="http://localhost:3000, http://localhost:8081",
    )

    assert settings.cors_origins == ["http://localhost:3000", "http://localhost:8081"]


def test_production_rejects_development_database_credentials() -> None:
    with pytest.raises(ValidationError, match="development/test credentials"):
        Settings(
            app_env="production",
            database_url="postgresql://nearby:nearby@database:5432/nearby",
            allowed_origins="https://nearby.example.com",
        )


def test_production_rejects_wildcard_cors() -> None:
    with pytest.raises(ValidationError, match="explicit trusted origins"):
        Settings(
            app_env="production",
            database_url="postgresql://app:strong-secret@database:5432/nearby",
            allowed_origins="*",
        )

"""Application configuration loaded from environment variables."""

from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # Database — accepts a standard postgresql:// URL and auto-converts for async
    database_url: str = "postgresql://rubinscout:rubinscout@localhost:5432/rubinscout"

    # ALeRCE
    alerce_api_url: str = "https://api.alerce.online"
    alerce_kafka_bootstrap: str = ""
    alerce_kafka_username: str = ""
    alerce_kafka_password: str = ""
    alerce_kafka_group_id: str = "rubin-scout-consumer"

    # Pitt-Google
    pittgoogle_project_id: str = "ardent-cycling-243415"

    # App
    app_env: str = "development"
    app_port: int = 8000
    app_host: str = "0.0.0.0"
    cors_origins: str = "http://localhost:3000,http://localhost:5173"
    log_level: str = "INFO"

    # Ingestion
    ingestion_interval_seconds: int = 900
    ingestion_lookback_days: int = 1
    min_classification_probability: float = 0.5

    # Notifications
    slack_webhook_url: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    notification_from_email: str = "alerts@rubinscout.dev"

    # Security
    # Admin API key for write endpoints (seed, subscriptions).
    # Generate with: python -c "import secrets; print(secrets.token_urlsafe(32))"
    # Leave empty in development to allow unrestricted access.
    admin_api_key: str = ""

    @property
    def async_database_url(self) -> str:
        """Convert standard postgresql:// to asyncpg driver URL."""
        url = self.database_url
        if url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        elif url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql+asyncpg://", 1)
        return url

    @property
    def sync_database_url(self) -> str:
        """Ensure standard postgresql:// for sync connections."""
        url = self.database_url
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql://", 1)
        if "+asyncpg" in url:
            url = url.replace("+asyncpg", "")
        return url

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",")]

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


@lru_cache
def get_settings() -> Settings:
    return Settings()

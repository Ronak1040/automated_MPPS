from functools import lru_cache
from typing import Optional
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration settings loaded from environment variables and .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Core Application Settings
    app_name: str = Field(
        default="Automated Multi-Platform Posting System",
        description="Name of the application",
    )
    app_env: str = Field(
        default="development",
        description="Environment (development, staging, production)",
    )
    debug: bool = Field(
        default=True,
        description="Debug mode toggle",
    )
    host: str = Field(
        default="0.0.0.0",
        description="Host address to bind the server",
    )
    port: int = Field(
        default=4000,
        description="Port on which the server listens",
    )
    api_v1_prefix: str = Field(
        default="/api/v1",
        description="Prefix for API version 1 routes",
    )
    log_level: str = Field(
        default="INFO",
        description="Application logging level",
    )

    # Platform Credentials (Optional for base server setup; checked on adapter runtime)
    telegram_bot_token: Optional[str] = Field(default=None)
    telegram_chat_id: Optional[str] = Field(default=None)

    discord_webhook_url: Optional[str] = Field(default=None)

    mastodon_instance_url: Optional[str] = Field(default="https://mastodon.social")
    mastodon_access_token: Optional[str] = Field(default=None)

    bluesky_handle: Optional[str] = Field(default=None)
    bluesky_app_password: Optional[str] = Field(default=None)
    bluesky_pds_url: Optional[str] = Field(default="https://bsky.social")

    devto_api_key: Optional[str] = Field(default=None)

    @field_validator(
        "telegram_bot_token",
        "telegram_chat_id",
        "discord_webhook_url",
        "mastodon_access_token",
        "bluesky_handle",
        "bluesky_app_password",
        "devto_api_key",
        mode="before",
    )
    @classmethod
    def clean_empty_strings(cls, v: Optional[str]) -> Optional[str]:
        """Convert empty strings to None."""
        if isinstance(v, str) and not v.strip():
            return None
        return v


@lru_cache
def get_settings() -> Settings:
    """Return cached singleton instance of Settings."""
    return Settings()

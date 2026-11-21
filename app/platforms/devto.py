from typing import Optional
import httpx

from app.core.config import get_settings
from app.core.exceptions import (
    PlatformAuthError,
    PlatformPayloadError,
    PlatformRateLimitError,
    PlatformServerError,
)
from app.core.logging import get_logger
from app.core.sanitizer import sanitize_headers
from app.platforms.base import BasePlatformIntegration, PublishOutcome

logger = get_logger(__name__)


class DevToIntegration(BasePlatformIntegration):
    """
    Dev.to (Forem API v1) integration.
    Publishes markdown articles and developer posts using the official REST API.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        super().__init__(timeout_seconds=timeout_seconds)
        settings = get_settings()
        self.api_key = api_key or settings.devto_api_key

    @property
    def name(self) -> str:
        return "Dev.to"

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)

    @property
    def is_publicly_accessible(self) -> bool:
        return True

    async def _publish_impl(
        self,
        client: httpx.AsyncClient,
        message: str,
        cta_url: Optional[str] = None,
    ) -> PublishOutcome:
        if not self.api_key:
            raise PlatformAuthError(self.name, "Dev.to API key is not configured.")

        endpoint = "https://dev.to/api/articles"
        headers = {
            "api-key": self.api_key,
            "accept": "application/vnd.forem.api-v1+json",
            "Content-Type": "application/json",
        }

        # Dev.to requires a title. Extract a title from the first line/sentence or use default.
        first_sentence = message.split(".")[0].strip()
        title = first_sentence[:80] if first_sentence else "Automated Platform Update"

        payload = {
            "article": {
                "title": title,
                "body_markdown": message,
                "published": True,
            }
        }

        logger.debug(
            "Submitting article to Dev.to at %s with headers %s",
            endpoint,
            sanitize_headers(headers),
        )
        response = await client.post(endpoint, json=payload, headers=headers)

        if response.status_code in (200, 201):
            data = response.json()
            article_url = data.get("url")
            content_id = str(data.get("id", ""))
            published = data.get("published", True)
            if article_url:
                return PublishOutcome(
                    posted_url=article_url,
                    content_id=content_id,
                    public_access=bool(published),
                )
            raise PlatformPayloadError(
                platform=self.name,
                message="Dev.to article created but response lacked 'url' property.",
                status_code=response.status_code,
            )

        if response.status_code in (401, 403):
            raise PlatformAuthError(
                platform=self.name,
                message="Invalid or missing Dev.to API key.",
                status_code=response.status_code,
                raw_error=response.text,
            )

        if response.status_code == 429:
            raise PlatformRateLimitError(
                platform=self.name,
                message="Dev.to rate limit exceeded.",
                raw_error=response.text,
            )

        if response.status_code == 422:
            raise PlatformPayloadError(
                platform=self.name,
                message=f"Dev.to unprocessable entity: {response.text}",
                status_code=422,
                raw_error=response.text,
            )

        if response.status_code >= 500:
            raise PlatformServerError(
                platform=self.name,
                message=f"Dev.to server error (HTTP {response.status_code})",
                status_code=response.status_code,
                raw_error=response.text,
            )

        raise PlatformPayloadError(
            platform=self.name,
            message=f"Unexpected Dev.to response (HTTP {response.status_code}): {response.text}",
            status_code=response.status_code,
            raw_error=response.text,
        )

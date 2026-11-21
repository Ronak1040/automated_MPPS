import uuid
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


class MastodonIntegration(BasePlatformIntegration):
    """
    Mastodon REST API v1 integration (ActivityPub network).
    Publishes statuses to a Mastodon instance with native idempotency headers.
    """

    def __init__(
        self,
        instance_url: Optional[str] = None,
        access_token: Optional[str] = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        super().__init__(timeout_seconds=timeout_seconds)
        settings = get_settings()
        self.instance_url = (instance_url or settings.mastodon_instance_url or "https://mastodon.social").rstrip("/")
        self.access_token = access_token or settings.mastodon_access_token

    @property
    def name(self) -> str:
        return "Mastodon"

    @property
    def is_configured(self) -> bool:
        return bool(self.access_token and self.instance_url)

    @property
    def is_publicly_accessible(self) -> bool:
        return True

    async def _publish_impl(
        self,
        client: httpx.AsyncClient,
        message: str,
        cta_url: Optional[str] = None,
    ) -> PublishOutcome:
        if not self.instance_url or not self.access_token:
            raise PlatformAuthError(self.name, "Mastodon instance URL or access token is not configured.")

        instance = self.instance_url.rstrip("/")
        endpoint = f"{instance}/api/v1/statuses"
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Idempotency-Key": uuid.uuid4().hex,
            "Content-Type": "application/json",
        }
        payload = {
            "status": message,
            "visibility": "public",
        }

        logger.debug(
            "Posting to Mastodon endpoint %s with headers %s",
            endpoint,
            sanitize_headers(headers),
        )
        response = await client.post(endpoint, json=payload, headers=headers)

        if response.status_code in (200, 201):
            data = response.json()
            post_url = data.get("url")
            content_id = str(data.get("id", ""))
            visibility = data.get("visibility", "public")
            public_access = visibility in ("public", "unlisted")

            if post_url:
                return PublishOutcome(
                    posted_url=post_url,
                    content_id=content_id,
                    public_access=public_access,
                )
            raise PlatformPayloadError(
                platform=self.name,
                message="Mastodon response missing public 'url' attribute.",
                status_code=response.status_code,
            )

        if response.status_code in (401, 403):
            raise PlatformAuthError(
                platform=self.name,
                message="Invalid or expired Mastodon access token with insufficient scopes.",
                status_code=response.status_code,
                raw_error=response.text,
            )

        if response.status_code == 429:
            raise PlatformRateLimitError(
                platform=self.name,
                message="Mastodon rate limit exceeded (HTTP 429).",
                raw_error=response.text,
            )

        if response.status_code == 422:
            raise PlatformPayloadError(
                platform=self.name,
                message=f"Mastodon unprocessable entity: {response.text}",
                status_code=422,
                raw_error=response.text,
            )

        if response.status_code >= 500:
            raise PlatformServerError(
                platform=self.name,
                message=f"Mastodon instance server error (HTTP {response.status_code})",
                status_code=response.status_code,
                raw_error=response.text,
            )

        raise PlatformPayloadError(
            platform=self.name,
            message=f"Unexpected Mastodon response (HTTP {response.status_code}): {response.text}",
            status_code=response.status_code,
            raw_error=response.text,
        )

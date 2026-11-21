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
from app.core.sanitizer import sanitize_url
from app.platforms.base import BasePlatformIntegration, PublishOutcome

logger = get_logger(__name__)


class DiscordIntegration(BasePlatformIntegration):
    """
    Discord Webhook API integration.
    Posts messages to Discord channels via Webhooks with ?wait=true to capture direct message jump links.
    """

    def __init__(
        self,
        webhook_url: Optional[str] = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        super().__init__(timeout_seconds=timeout_seconds)
        settings = get_settings()
        self.webhook_url = webhook_url or settings.discord_webhook_url

    @property
    def name(self) -> str:
        return "Discord"

    @property
    def is_configured(self) -> bool:
        return bool(self.webhook_url)

    @property
    def is_publicly_accessible(self) -> bool:
        # Discord messages require server access / authentication
        return False

    async def _publish_impl(
        self,
        client: httpx.AsyncClient,
        message: str,
        cta_url: Optional[str] = None,
    ) -> PublishOutcome:
        if not self.webhook_url:
            raise PlatformAuthError(self.name, "Discord webhook URL is not configured.")

        # Enforce wait=true so Discord returns the full message payload with IDs
        if "wait=true" not in self.webhook_url:
            delimiter = "&" if "?" in self.webhook_url else "?"
            endpoint = f"{self.webhook_url}{delimiter}wait=true"
        else:
            endpoint = self.webhook_url

        payload = {"content": message}

        logger.debug("Executing webhook on %s", sanitize_url(endpoint))
        response = await client.post(endpoint, json=payload)

        if response.status_code in (200, 201):
            data = response.json()
            message_id = str(data.get("id", ""))
            channel_id = str(data.get("channel_id", ""))
            guild_id = str(data.get("guild_id", "@me"))
            posted_url = f"https://discord.com/channels/{guild_id}/{channel_id}/{message_id}"
            return PublishOutcome(
                posted_url=posted_url,
                content_id=message_id,
                public_access=False,  # Discord requires authentication/guild membership
            )

        if response.status_code in (401, 403, 404):
            raise PlatformAuthError(
                platform=self.name,
                message="Discord webhook is invalid, unauthorized, or deleted.",
                status_code=response.status_code,
                raw_error=response.text,
            )

        if response.status_code == 429:
            data = response.json() if response.text else {}
            retry_after = data.get("retry_after")
            raise PlatformRateLimitError(
                platform=self.name,
                message=f"Discord rate limit exceeded. Retry after {retry_after}s.",
                retry_after=float(retry_after) if retry_after is not None else None,
                raw_error=response.text,
            )

        if response.status_code == 400:
            raise PlatformPayloadError(
                platform=self.name,
                message=f"Discord rejected message content: {response.text}",
                status_code=400,
                raw_error=response.text,
            )

        if response.status_code >= 500:
            raise PlatformServerError(
                platform=self.name,
                message=f"Discord upstream server error (HTTP {response.status_code})",
                status_code=response.status_code,
                raw_error=response.text,
            )

        raise PlatformPayloadError(
            platform=self.name,
            message=f"Unexpected Discord response (HTTP {response.status_code}): {response.text}",
            status_code=response.status_code,
            raw_error=response.text,
        )

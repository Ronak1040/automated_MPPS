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


class TelegramIntegration(BasePlatformIntegration):
    """
    Telegram Bot API integration.
    Posts messages to public channels or chats and extracts the public message URL.
    """

    def __init__(
        self,
        bot_token: Optional[str] = None,
        chat_id: Optional[str] = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        super().__init__(timeout_seconds=timeout_seconds)
        settings = get_settings()
        self.bot_token = bot_token or settings.telegram_bot_token
        self.chat_id = chat_id or settings.telegram_chat_id

    @property
    def name(self) -> str:
        return "Telegram"

    @property
    def is_configured(self) -> bool:
        return bool(self.bot_token and self.chat_id)

    @property
    def is_publicly_accessible(self) -> bool:
        # Public if posted to a channel with a username
        if self.chat_id and str(self.chat_id).startswith("@"):
            return True
        return False

    async def _publish_impl(
        self,
        client: httpx.AsyncClient,
        message: str,
        cta_url: Optional[str] = None,
    ) -> PublishOutcome:
        if not self.bot_token or not self.chat_id:
            raise PlatformAuthError(self.name, "Telegram bot token or chat ID is not configured.")

        endpoint = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": message,
            "disable_web_page_preview": False,
        }

        logger.debug("Dispatching request to %s", sanitize_url(endpoint))
        response = await client.post(endpoint, json=payload)

        if response.status_code == 200:
            data = response.json()
            result = data.get("result", {})
            message_id = str(result.get("message_id", ""))
            chat = result.get("chat", {})
            username = chat.get("username")

            # Public accessibility: Telegram channel messages are publicly viewable
            # on the web (t.me/<username>/<id>) only if the chat has a public username.
            if username:
                posted_url = f"https://t.me/{username}/{message_id}"
                public_access = True
            else:
                raw_chat_id = str(chat.get("id", self.chat_id))
                if raw_chat_id.startswith("-100"):
                    posted_url = f"https://t.me/c/{raw_chat_id[4:]}/{message_id}"
                else:
                    posted_url = f"https://t.me/{self.chat_id}/{message_id}"
                public_access = False

            return PublishOutcome(
                posted_url=posted_url,
                content_id=message_id,
                public_access=public_access,
            )

        if response.status_code in (401, 404):
            raise PlatformAuthError(
                platform=self.name,
                message="Invalid Telegram bot token or chat ID not found.",
                status_code=response.status_code,
                raw_error=response.text,
            )

        if response.status_code == 429:
            data = response.json() if response.text else {}
            retry_after = data.get("parameters", {}).get("retry_after")
            raise PlatformRateLimitError(
                platform=self.name,
                message=f"Telegram rate limit hit. Retry after {retry_after}s.",
                retry_after=retry_after,
                raw_error=response.text,
            )

        if response.status_code == 400:
            data = response.json() if response.text else {}
            description = data.get("description", "Bad Request")
            raise PlatformPayloadError(
                platform=self.name,
                message=f"Telegram rejected message: {description}",
                status_code=400,
                raw_error=response.text,
            )

        if response.status_code >= 500:
            raise PlatformServerError(
                platform=self.name,
                message=f"Telegram internal service error (HTTP {response.status_code})",
                status_code=response.status_code,
                raw_error=response.text,
            )

        raise PlatformPayloadError(
            platform=self.name,
            message=f"Unexpected Telegram response (HTTP {response.status_code}): {response.text}",
            status_code=response.status_code,
            raw_error=response.text,
        )

from datetime import datetime, timezone
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


class BlueskyIntegration(BasePlatformIntegration):
    """
    Bluesky AT Protocol XRPC integration.
    Authenticates via App Password session and publishes records to com.atproto.repo.createRecord.
    """

    def __init__(
        self,
        handle: Optional[str] = None,
        app_password: Optional[str] = None,
        pds_url: Optional[str] = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        super().__init__(timeout_seconds=timeout_seconds)
        settings = get_settings()
        self.handle = handle or settings.bluesky_handle
        self.app_password = app_password or settings.bluesky_app_password
        self.pds_url = (pds_url or settings.bluesky_pds_url or "https://bsky.social").rstrip("/")

    @property
    def name(self) -> str:
        return "Bluesky"

    @property
    def is_configured(self) -> bool:
        return bool(self.handle and self.app_password)

    @property
    def is_publicly_accessible(self) -> bool:
        return True

    async def _publish_impl(
        self,
        client: httpx.AsyncClient,
        message: str,
        cta_url: Optional[str] = None,
    ) -> PublishOutcome:
        if not self.handle or not self.app_password:
            raise PlatformAuthError(self.name, "Bluesky handle or app password is not configured.")

        # Step 1: Session Handshake (com.atproto.server.createSession)
        pds = self.pds_url.rstrip("/")
        session_endpoint = f"{pds}/xrpc/com.atproto.server.createSession"
        session_payload = {
            "identifier": self.handle,
            "password": self.app_password,
        }

        logger.debug("Requesting Bluesky session for %s", self.handle)
        session_res = await client.post(session_endpoint, json=session_payload)

        if session_res.status_code == 401:
            raise PlatformAuthError(
                platform=self.name,
                message="Invalid Bluesky handle or app password.",
                status_code=401,
                raw_error=session_res.text,
            )
        if session_res.status_code == 429:
            raise PlatformRateLimitError(
                platform=self.name,
                message="Bluesky rate limit exceeded on session handshake.",
                raw_error=session_res.text,
            )
        if session_res.status_code != 200:
            raise PlatformAuthError(
                platform=self.name,
                message=f"Failed to create Bluesky session (HTTP {session_res.status_code}): {session_res.text}",
                status_code=session_res.status_code,
                raw_error=session_res.text,
            )

        session_data = session_res.json()
        access_jwt = session_data.get("accessJwt")
        user_did = session_data.get("did", self.handle)

        # Step 2: Create Post Record (com.atproto.repo.createRecord)
        post_endpoint = f"{pds}/xrpc/com.atproto.repo.createRecord"
        headers = {
            "Authorization": f"Bearer {access_jwt}",
            "Content-Type": "application/json",
        }
        # Build AT Protocol rich text facets for clickable links if cta_url is present
        record: dict = {
            "$type": "app.bsky.feed.post",
            "text": message,
            "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        if cta_url and cta_url in message:
            start_char = message.find(cta_url)
            byte_start = len(message[:start_char].encode("utf-8"))
            byte_end = byte_start + len(cta_url.encode("utf-8"))
            record["facets"] = [
                {
                    "index": {"byteStart": byte_start, "byteEnd": byte_end},
                    "features": [{"$type": "app.bsky.richtext.facet#link", "uri": cta_url}],
                }
            ]

        post_payload = {
            "repo": user_did,
            "collection": "app.bsky.feed.post",
            "record": record,
        }

        logger.debug(
            "Creating Bluesky post record at %s with headers %s",
            post_endpoint,
            sanitize_headers(headers),
        )
        post_res = await client.post(post_endpoint, json=post_payload, headers=headers)

        if post_res.status_code in (200, 201):
            record_data = post_res.json()
            at_uri = record_data.get("uri", "")
            # URI format: at://did:plc:.../app.bsky.feed.post/<rkey>
            rkey = at_uri.split("/")[-1] if "/" in at_uri else ""
            posted_url = f"https://bsky.app/profile/{self.handle}/post/{rkey}" if rkey else f"https://bsky.app/profile/{self.handle}"
            return PublishOutcome(
                posted_url=posted_url,
                content_id=rkey,
                public_access=True,  # Bluesky posts are publicly accessible across the AT Protocol
            )

        if post_res.status_code in (401, 403):
            raise PlatformAuthError(
                platform=self.name,
                message="Unauthorized to create record on Bluesky repository.",
                status_code=post_res.status_code,
                raw_error=post_res.text,
            )

        if post_res.status_code == 429:
            raise PlatformRateLimitError(
                platform=self.name,
                message="Bluesky rate limit exceeded while creating post record.",
                raw_error=post_res.text,
            )

        if post_res.status_code == 400:
            raise PlatformPayloadError(
                platform=self.name,
                message=f"Bluesky rejected post record: {post_res.text}",
                status_code=400,
                raw_error=post_res.text,
            )

        if post_res.status_code >= 500:
            raise PlatformServerError(
                platform=self.name,
                message=f"Bluesky PDS server error (HTTP {post_res.status_code})",
                status_code=post_res.status_code,
                raw_error=post_res.text,
            )

        raise PlatformPayloadError(
            platform=self.name,
            message=f"Unexpected Bluesky response (HTTP {post_res.status_code}): {post_res.text}",
            status_code=post_res.status_code,
            raw_error=post_res.text,
        )

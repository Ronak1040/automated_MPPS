from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, Union
import httpx

from app.core.exceptions import (
    PlatformAuthError,
    PlatformIntegrationError,
    PlatformNetworkError,
    PlatformPayloadError,
    PlatformRateLimitError,
    PlatformServerError,
)
from app.core.logging import get_logger
from app.core.sanitizer import sanitize_url
from app.schemas.response import PlatformPostingResult

logger = get_logger(__name__)


@dataclass
class PublishOutcome:
    """Encapsulates the successful publishing outcome from a platform adapter."""

    posted_url: str
    content_id: str
    public_access: bool = True


class BasePlatformIntegration(ABC):
    """
    Abstract Base Class for all social/messaging platform integrations.
    Provides a standardized asynchronous template method for post publishing with
    resilience, timeout enforcement, rate-limit detection, and secret masking.
    """

    def __init__(self, timeout_seconds: float = 10.0) -> None:
        self.timeout_seconds = timeout_seconds

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable display name of the platform (e.g., 'Telegram')."""
        pass

    @property
    @abstractmethod
    def is_configured(self) -> bool:
        """Check whether all required credentials and settings are present."""
        pass

    @property
    def is_publicly_accessible(self) -> bool:
        """Whether posts published to this platform can be viewed publicly without auth."""
        return True

    @abstractmethod
    async def _publish_impl(
        self,
        client: httpx.AsyncClient,
        message: str,
        cta_url: Optional[str] = None,
    ) -> Union[str, PublishOutcome]:
        """
        Platform-specific API publishing logic.

        Args:
            client: Pre-configured AsyncClient with timeout.
            message: Validated message content.
            cta_url: Validated CTA URL.

        Returns:
            The public web URL or PublishOutcome of the successfully published post.

        Raises:
            PlatformIntegrationError: Any domain-specific error.
        """
        pass

    async def publish(
        self,
        message: str,
        cta_url: Optional[str] = None,
        client: Optional[httpx.AsyncClient] = None,
    ) -> PlatformPostingResult:
        """
        Consistent asynchronous publishing interface.
        Executes platform publication with strict error isolation, timeout enforcement,
        and secret-safe logging. Never raises exceptions to the orchestrator.
        """
        # 1. Configuration check
        if not self.is_configured:
            error_msg = f"Platform '{self.name}' is not configured. Missing required API credentials."
            logger.warning(error_msg)
            return PlatformPostingResult.failure(
                platform=self.name,
                error=error_msg,
                public_access=False,
            )

        # 2. Execution with managed or injected AsyncClient
        try:
            if client is not None:
                outcome = await self._execute_with_client(client, message, cta_url)
            else:
                async with httpx.AsyncClient(timeout=self.timeout_seconds) as managed_client:
                    outcome = await self._execute_with_client(managed_client, message, cta_url)

            if isinstance(outcome, PublishOutcome):
                posted_url = outcome.posted_url
                content_id = outcome.content_id
                public_access = outcome.public_access
            else:
                posted_url = str(outcome)
                content_id = str(outcome)
                public_access = self.is_publicly_accessible

            logger.info("Successfully published post to %s [id=%s]: %s", self.name, content_id, posted_url)
            return PlatformPostingResult.success(
                platform=self.name,
                posted_url=posted_url,
                content_id=content_id,
                public_access=public_access,
            )

        except PlatformAuthError as e:
            logger.error("Authentication error on platform %s: %s", self.name, e.message)
            return PlatformPostingResult.failure(
                platform=self.name,
                error=f"Authentication failed: {e.message}",
                public_access=False,
            )

        except PlatformRateLimitError as e:
            logger.warning("Rate limit exceeded on platform %s: %s", self.name, e.message)
            return PlatformPostingResult.failure(
                platform=self.name,
                error=f"Rate limit exceeded: {e.message}",
                public_access=self.is_publicly_accessible,
            )

        except PlatformNetworkError as e:
            logger.error("Network failure communicating with %s: %s", self.name, e.message)
            return PlatformPostingResult.failure(
                platform=self.name,
                error=f"Network failure: {e.message}",
                public_access=self.is_publicly_accessible,
            )

        except PlatformPayloadError as e:
            logger.error("Payload rejected by %s: %s", self.name, e.message)
            return PlatformPostingResult.failure(
                platform=self.name,
                error=f"Content rejected: {e.message}",
                public_access=self.is_publicly_accessible,
            )

        except PlatformServerError as e:
            logger.error("Remote server error on %s: %s", self.name, e.message)
            return PlatformPostingResult.failure(
                platform=self.name,
                error=f"Remote platform error: {e.message}",
                public_access=self.is_publicly_accessible,
            )

        except PlatformIntegrationError as e:
            logger.error("Integration error on %s: %s", self.name, e.message)
            return PlatformPostingResult.failure(
                platform=self.name,
                error=e.message,
                public_access=self.is_publicly_accessible,
            )

        except Exception as e:
            logger.exception("Unexpected unhandled exception publishing to %s", self.name)
            return PlatformPostingResult.failure(
                platform=self.name,
                error=f"Unexpected error: {str(e)}",
                public_access=False,
            )

    async def _execute_with_client(
        self,
        client: httpx.AsyncClient,
        message: str,
        cta_url: Optional[str] = None,
    ) -> Union[str, PublishOutcome]:
        """Internal execution helper mapping low-level HTTPX network errors."""
        try:
            return await self._publish_impl(client, message, cta_url)
        except httpx.TimeoutException as e:
            url_str = sanitize_url(str(getattr(e.request, "url", "")))
            raise PlatformNetworkError(
                platform=self.name,
                message=f"Request timed out after {self.timeout_seconds}s to {url_str}",
            ) from e
        except httpx.NetworkError as e:
            url_str = sanitize_url(str(getattr(e.request, "url", "")))
            err_detail = sanitize_url(str(e))
            raise PlatformNetworkError(
                platform=self.name,
                message=f"Network connectivity error connecting to {url_str}: {err_detail}",
            ) from e

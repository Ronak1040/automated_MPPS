"""
Centralized Platform Registry service.
Manages discovery, registration, and credential inspection of third-party platform integrations.
Decouples platform availability and configuration from the posting orchestration engine.
"""

from functools import lru_cache
from typing import Dict, List, Optional, Sequence

from app.core.logging import get_logger
from app.platforms.base import BasePlatformIntegration
from app.platforms.bluesky import BlueskyIntegration
from app.platforms.devto import DevToIntegration
from app.platforms.discord import DiscordIntegration
from app.platforms.mastodon import MastodonIntegration
from app.platforms.telegram import TelegramIntegration

logger = get_logger(__name__)


class PlatformRegistry:
    """
    Central registry for platform integrations.
    Supports dynamic registration, default discovery, and platform status querying.
    """

    def __init__(
        self,
        integrations: Optional[Sequence[BasePlatformIntegration]] = None,
        default_timeout: float = 10.0,
    ) -> None:
        self._default_timeout = default_timeout
        self._platforms: Dict[str, BasePlatformIntegration] = {}

        if integrations is not None:
            for integration in integrations:
                self.register(integration)
        else:
            self.register_defaults()

    def register(self, integration: BasePlatformIntegration) -> None:
        """Register a platform integration instance."""
        key = integration.name.lower()
        self._platforms[key] = integration
        logger.debug("Registered platform: %s (configured=%s)", integration.name, integration.is_configured)

    def register_defaults(self) -> None:
        """Register the 5 official platform integrations with default timeout."""
        self.register(TelegramIntegration(timeout_seconds=self._default_timeout))
        self.register(DiscordIntegration(timeout_seconds=self._default_timeout))
        self.register(MastodonIntegration(timeout_seconds=self._default_timeout))
        self.register(BlueskyIntegration(timeout_seconds=self._default_timeout))
        self.register(DevToIntegration(timeout_seconds=self._default_timeout))

    def get_registered_platforms(self) -> List[BasePlatformIntegration]:
        """Return all registered platform integration instances in registration order."""
        return list(self._platforms.values())

    def get_configured_platforms(self) -> List[BasePlatformIntegration]:
        """Return only platforms with valid credentials configured in the environment."""
        configured = [p for p in self._platforms.values() if p.is_configured]
        logger.info(
            "Identified %d configured integration(s): %s",
            len(configured),
            [p.name for p in configured],
        )
        return configured

    def get_platform(self, name: str) -> Optional[BasePlatformIntegration]:
        """Look up a platform by its display name (case-insensitive)."""
        return self._platforms.get(name.strip().lower())

    def clear(self) -> None:
        """Clear all registered platforms (useful for test isolation)."""
        self._platforms.clear()


@lru_cache
def get_platform_registry() -> PlatformRegistry:
    """Return a cached singleton instance of the PlatformRegistry."""
    return PlatformRegistry()

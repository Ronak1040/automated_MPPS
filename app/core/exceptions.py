"""
Centralized domain exception hierarchy for third-party platform integrations.
Provides structured, platform-attributed errors for authentication failures,
rate limits, network timeouts, payload rejections, and upstream server faults.
"""

from typing import Optional


class PlatformIntegrationError(Exception):
    """Base exception for all third-party platform integration failures."""

    def __init__(
        self,
        platform: str,
        message: str,
        status_code: Optional[int] = None,
        raw_error: Optional[str] = None,
    ) -> None:
        self.platform = platform
        self.message = message
        self.status_code = status_code
        self.raw_error = raw_error
        super().__init__(f"[{platform}] {message}")


class PlatformAuthError(PlatformIntegrationError):
    """Raised when authentication fails (HTTP 401, 403, or invalid token/key)."""
    pass


class PlatformRateLimitError(PlatformIntegrationError):
    """Raised when rate limits are exceeded (HTTP 429 Too Many Requests)."""

    def __init__(
        self,
        platform: str,
        message: str,
        retry_after: Optional[float] = None,
        status_code: Optional[int] = 429,
        raw_error: Optional[str] = None,
    ) -> None:
        super().__init__(platform, message, status_code=status_code, raw_error=raw_error)
        self.retry_after = retry_after


class PlatformNetworkError(PlatformIntegrationError):
    """Raised when network connectivity fails (connection refused, DNS error, timeout)."""
    pass


class PlatformPayloadError(PlatformIntegrationError):
    """Raised when the platform rejects the payload formatting or content (HTTP 400, 422)."""
    pass


class PlatformServerError(PlatformIntegrationError):
    """Raised when the platform experiences an upstream server error (HTTP 5xx)."""
    pass

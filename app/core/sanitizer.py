import re
from typing import Dict, Any, Optional

# Regex patterns to sanitize sensitive tokens from URLs in logs
TELEGRAM_BOT_REGEX = re.compile(r"(api\.telegram\.org/bot)([^/]+)")
DISCORD_WEBHOOK_REGEX = re.compile(r"(discord\.com/api/webhooks/\d+/)([^/?]+)")

SENSITIVE_HEADER_KEYS = {
    "authorization",
    "api-key",
    "x-api-key",
    "token",
    "x-auth-token",
    "password",
    "secret",
}


def mask_secret(secret: Optional[str], visible_prefix: int = 4, visible_suffix: int = 4) -> str:
    """Mask sensitive string, showing only leading and trailing characters."""
    if not secret:
        return "[EMPTY]"
    if len(secret) <= (visible_prefix + visible_suffix):
        return "[REDACTED]"
    return f"{secret[:visible_prefix]}...{secret[-visible_suffix:]}"


def sanitize_url(url: str) -> str:
    """Mask embedded credential tokens from URL strings for safe logging."""
    if not url:
        return ""
    # Mask Telegram Bot Token: /bot<token>/
    sanitized = TELEGRAM_BOT_REGEX.sub(r"\1[REDACTED]", url)
    # Mask Discord Webhook Token: /webhooks/<id>/<token>
    sanitized = DISCORD_WEBHOOK_REGEX.sub(r"\1[REDACTED]", sanitized)
    return sanitized


def sanitize_headers(headers: Dict[str, Any]) -> Dict[str, str]:
    """Return a copy of request/response headers with sensitive tokens redacted."""
    safe_headers: Dict[str, str] = {}
    for k, v in headers.items():
        if k.lower() in SENSITIVE_HEADER_KEYS:
            safe_headers[k] = "[REDACTED]"
        else:
            safe_headers[k] = str(v)
    return safe_headers

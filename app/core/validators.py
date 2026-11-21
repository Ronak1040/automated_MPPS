"""
URL extraction and RFC-compliant syntax validators for post messages.
Enforces single CTA URL constraint, domain syntax (RFC 1123), IPv4 structure,
TCP port range boundaries (1-65535), and HTTP/HTTPS scheme restrictions.
"""

import re
from typing import List
from urllib.parse import urlparse
from pydantic import HttpUrl, TypeAdapter, ValidationError

# Regex to detect candidate URIs (scheme://, www., or specific schemes)
URI_CANDIDATE_REGEX = re.compile(
    r'(?i)\b(?:'
    r'[a-z][a-z0-9+.-]*://[^\s<>"]*'
    r'|www\.[^\s<>"]+'
    r'|(?:mailto|javascript|data|file|tel):[^\s<>"]+'
    r')'
)

# Label pattern conforming to RFC 1123 / RFC 952
DOMAIN_LABEL_REGEX = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$")

_http_url_adapter = TypeAdapter(HttpUrl)


def clean_surrounding_punctuation(url: str) -> str:
    """Trim common opening/closing sentence punctuation from candidate URLs."""
    while url and url[0] in "([{'\"<":
        url = url[1:]
    while url and url[-1] in ".,!?:;)]}'\">":
        url = url[:-1]
    return url


def is_valid_ipv4(host: str) -> bool:
    """Check if the given host string is a valid IPv4 address."""
    parts = host.split(".")
    if len(parts) == 4 and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts):
        return True
    return False


def is_valid_domain(host: str) -> bool:
    """Validate host syntax: supports localhost, IPv4, and multi-label RFC domains."""
    if not host:
        return False
    if host.lower() == "localhost":
        return True
    if is_valid_ipv4(host):
        return True
    if ".." in host or host.startswith(".") or host.endswith("."):
        return False

    parts = host.split(".")
    if len(parts) < 2:
        return False

    for label in parts:
        if not DOMAIN_LABEL_REGEX.match(label):
            return False

    # Top-level domain (TLD) must not be purely numeric
    if parts[-1].isdigit():
        return False

    return True


def find_candidate_urls(message: str) -> List[str]:
    """Scan message text and extract all candidate URL or URI strings."""
    return URI_CANDIDATE_REGEX.findall(message)


def extract_and_validate_cta_url(message: str) -> str:
    """
    Validate that the message contains exactly one valid HTTP/HTTPS CTA URL.

    Raises:
        ValueError: If no URL is found, multiple URLs are found, non-HTTP schemes are used,
                    or the URL is malformed.
    Returns:
        The validated CTA URL string.
    """
    candidates = find_candidate_urls(message)

    if len(candidates) == 0:
        raise ValueError("Message does not contain a CTA URL. Exactly one CTA URL is required.")

    if len(candidates) > 1:
        raise ValueError(
            f"Message contains multiple URLs ({len(candidates)} detected). Exactly one CTA URL is permitted."
        )

    raw_url = candidates[0]
    candidate = clean_surrounding_punctuation(raw_url)

    if candidate.lower().startswith("www."):
        raise ValueError("URL must include an HTTP or HTTPS scheme (e.g., 'https://example.com').")

    try:
        parsed = urlparse(candidate)
    except Exception as e:
        raise ValueError(f"Malformed URL: unable to parse '{candidate}'.") from e

    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https"):
        raise ValueError(f"Disallowed URL scheme '{scheme}'. Only HTTP and HTTPS URLs are permitted.")

    if not parsed.netloc:
        raise ValueError(f"Malformed URL: '{candidate}' is missing a valid domain or host.")

    try:
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as e:
        raise ValueError(f"Malformed URL: invalid port specification in '{candidate}'.") from e

    if port is not None and not (1 <= port <= 65535):
        raise ValueError(f"Malformed URL: port must be between 1 and 65535 in '{candidate}'.")

    if not hostname:
        raise ValueError(f"Malformed URL: '{candidate}' is missing a valid hostname.")

    if not is_valid_domain(hostname):
        raise ValueError(f"Malformed URL: domain '{hostname}' is invalid.")

    try:
        _http_url_adapter.validate_python(candidate)
    except ValidationError as e:
        raise ValueError(f"Malformed URL: {e.errors()[0]['msg']}") from e

    return candidate

"""
Validation Test Suite for Automated Multi-Platform Posting System (AMPPS).

Consolidates all request validation and schema enforcement tests:
- Part 1: Canonical, boundary, and edge-case validation tests (30 tests)
- Part 2: QA validation scenarios 1 through 7 (20 tests)
Total: 50 tests.
"""

from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient
from httpx import AsyncClient

from app.schemas.response import PlatformPostingResult
from app.services.posting_service import ConsolidatedPostingResponse, PostingService


# ============================================================================
# PART 1: VALID INPUT TESTS (HTTP 200 OK)
# ============================================================================

@pytest.mark.asyncio
async def test_posting_valid_canonical_request(async_client: AsyncClient) -> None:
    """Test the exact canonical request provided in the assignment prompt."""
    payload = {
        "message": "Check out our latest business platform. https://example.com"
    }
    response = await async_client.post("/posting", json=payload)
    assert response.status_code == 200

    data = response.json()
    assert data["message"] == payload["message"]
    assert data["cta_url"] == "https://example.com"
    assert data["character_count"] == len(payload["message"])
    assert data["status"] in ("success", "failed")
    assert "results" in data


@pytest.mark.asyncio
async def test_posting_valid_http_scheme(async_client: AsyncClient) -> None:
    """Verify that HTTP (non-secure) URLs are also permitted alongside HTTPS."""
    payload = {"message": "Documentation portal: http://docs.example.org"}
    response = await async_client.post("/posting", json=payload)
    assert response.status_code == 200
    assert response.json()["cta_url"] == "http://docs.example.org"


@pytest.mark.asyncio
async def test_posting_valid_url_with_query_and_fragment(async_client: AsyncClient) -> None:
    """Verify URLs with query strings, parameters, and fragments are handled."""
    payload = {
        "message": "View analytics: https://analytics.example.com/reports?user=42&sort=asc#metrics"
    }
    response = await async_client.post("/posting", json=payload)
    assert response.status_code == 200
    assert response.json()["cta_url"] == "https://analytics.example.com/reports?user=42&sort=asc#metrics"


@pytest.mark.asyncio
async def test_posting_valid_localhost_and_ports(async_client: AsyncClient) -> None:
    """Verify localhost and custom port numbers are allowed."""
    payload = {"message": "Test server active at http://localhost:4000/docs"}
    response = await async_client.post("/posting", json=payload)
    assert response.status_code == 200
    assert response.json()["cta_url"] == "http://localhost:4000/docs"


@pytest.mark.asyncio
async def test_posting_valid_ipv4_host(async_client: AsyncClient) -> None:
    """Verify direct IPv4 addresses are accepted."""
    payload = {"message": "Direct link http://127.0.0.1:8080/status"}
    response = await async_client.post("/posting", json=payload)
    assert response.status_code == 200
    assert response.json()["cta_url"] == "http://127.0.0.1:8080/status"


@pytest.mark.asyncio
async def test_posting_valid_exactly_200_characters(async_client: AsyncClient) -> None:
    """Verify boundary condition: message with exactly 200 characters is accepted."""
    url = "https://example.com"
    prefix = "A" * (200 - len(url) - 1)
    message = f"{prefix} {url}"
    assert len(message) == 200

    response = await async_client.post("/posting", json={"message": message})
    assert response.status_code == 200
    assert response.json()["character_count"] == 200


@pytest.mark.asyncio
async def test_posting_valid_trailing_sentence_punctuation(async_client: AsyncClient) -> None:
    """Verify URL followed by sentence punctuation (e.g. '.') is parsed without modifying text."""
    payload = {"message": "Check out our site at https://example.com."}
    response = await async_client.post("/posting", json=payload)
    assert response.status_code == 200

    # Message must NOT be silently modified
    assert response.json()["message"] == payload["message"]
    # Clean CTA URL is extracted
    assert response.json()["cta_url"] == "https://example.com"


@pytest.mark.asyncio
async def test_posting_valid_parentheses_enclosed_url(async_client: AsyncClient) -> None:
    """Verify URL enclosed in parentheses is handled properly."""
    payload = {"message": "Follow this guide (https://example.com/guide) for details."}
    response = await async_client.post("/posting", json=payload)
    assert response.status_code == 200
    assert response.json()["cta_url"] == "https://example.com/guide"


@pytest.mark.asyncio
async def test_posting_message_not_silently_modified(async_client: AsyncClient) -> None:
    """Confirm user message preserves original internal spaces and formatting."""
    message = "Special   announcement:   https://example.com/launch"
    response = await async_client.post("/posting", json={"message": message})
    assert response.status_code == 200
    assert response.json()["message"] == message


@pytest.mark.asyncio
async def test_posting_versioned_endpoint_accessible(async_client: AsyncClient) -> None:
    """Verify POST /api/v1/posting is also accessible and functions identically."""
    payload = {"message": "Release v1.0 https://example.com"}
    response = await async_client.post("/api/v1/posting", json=payload)
    assert response.status_code == 200
    assert response.json()["cta_url"] == "https://example.com"


# ============================================================================
# PART 1 (CONT.): INVALID INPUT TESTS (HTTP 422 UNPROCESSABLE ENTITY)
# ============================================================================

@pytest.mark.asyncio
async def test_posting_reject_missing_message_field(async_client: AsyncClient) -> None:
    """Requirement 1: Message is mandatory."""
    response = await async_client.post("/posting", json={})
    assert response.status_code == 422
    errors = response.json()["detail"]
    assert any("message" in str(err["loc"]) for err in errors)


@pytest.mark.asyncio
async def test_posting_reject_empty_message(async_client: AsyncClient) -> None:
    """Requirement 9: Reject empty messages."""
    response = await async_client.post("/posting", json={"message": ""})
    assert response.status_code == 422
    assert "cannot be empty" in response.text


@pytest.mark.asyncio
async def test_posting_reject_whitespace_only_message(async_client: AsyncClient) -> None:
    """Requirement 9: Reject whitespace-only messages."""
    response = await async_client.post("/posting", json={"message": "   \n\t   "})
    assert response.status_code == 422
    assert "cannot be empty or contain only whitespace" in response.text


@pytest.mark.asyncio
async def test_posting_reject_exceeding_200_characters(async_client: AsyncClient) -> None:
    """Requirement 2: Message must not exceed 200 characters."""
    url = "https://example.com"
    prefix = "A" * (201 - len(url) - 1)
    message = f"{prefix} {url}"
    assert len(message) == 201

    response = await async_client.post("/posting", json={"message": message})
    assert response.status_code == 422
    assert "exceeds maximum allowed length of 200 characters" in response.text


@pytest.mark.asyncio
async def test_posting_reject_message_without_url(async_client: AsyncClient) -> None:
    """Requirement 8: Reject messages without a URL."""
    payload = {"message": "This is a great product with no link anywhere."}
    response = await async_client.post("/posting", json=payload)
    assert response.status_code == 422
    assert "does not contain a CTA URL" in response.text


@pytest.mark.asyncio
async def test_posting_reject_multiple_urls(async_client: AsyncClient) -> None:
    """Requirement 7: Reject messages containing multiple URLs."""
    payload = {
        "message": "Visit our main site https://main.com and store https://store.com"
    }
    response = await async_client.post("/posting", json=payload)
    assert response.status_code == 422
    assert "contains multiple URLs" in response.text


@pytest.mark.asyncio
async def test_posting_reject_multiple_mixed_urls(async_client: AsyncClient) -> None:
    """Requirement 7: Reject messages with multiple URLs even if one is non-HTTP."""
    payload = {
        "message": "See https://example.com or download ftp://example.com/file"
    }
    response = await async_client.post("/posting", json=payload)
    assert response.status_code == 422
    assert "multiple URLs" in response.text or "Disallowed URL scheme" in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "disallowed_url,scheme",
    [
        ("ftp://files.example.com/archive.zip", "ftp"),
        ("mailto:support@example.com", "mailto"),
        ("javascript:alert('xss')", "javascript"),
        ("file:///etc/passwd", "file"),
        ("data:text/plain;base64,SGVsbG8=", "data"),
    ],
)
async def test_posting_reject_disallowed_schemes(
    async_client: AsyncClient, disallowed_url: str, scheme: str
) -> None:
    """Requirement 5: Only HTTP and HTTPS URLs are permitted."""
    payload = {"message": f"Check this resource: {disallowed_url}"}
    response = await async_client.post("/posting", json=payload)
    assert response.status_code == 422
    assert "Only HTTP and HTTPS URLs are permitted" in response.text


@pytest.mark.asyncio
async def test_posting_reject_missing_scheme_www(async_client: AsyncClient) -> None:
    """Requirement 5: Reject URLs without explicit http:// or https://."""
    payload = {"message": "Visit www.example.com for more info"}
    response = await async_client.post("/posting", json=payload)
    assert response.status_code == 422
    assert "must include an HTTP or HTTPS scheme" in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "malformed_message,expected_err",
    [
        ("Visit incomplete http:// now", "missing a valid domain or host"),
        ("Visit incomplete https:// now", "missing a valid domain or host"),
        ("Visit empty host http:///path/to now", "missing a valid domain or host"),
        ("Visit bad dots https://example..com now", "domain 'example..com' is invalid"),
        ("Visit bad label https://-invalid.com now", "domain '-invalid.com' is invalid"),
        ("Visit bad port https://example.com:abc now", "invalid port specification"),
        ("Visit port out of range https://example.com:99999 now", "invalid port specification"),
    ],
)
async def test_posting_reject_malformed_urls(
    async_client: AsyncClient, malformed_message: str, expected_err: str
) -> None:
    """Requirement 4 & 6: Validate URL syntax and reject malformed URLs."""
    response = await async_client.post("/posting", json={"message": malformed_message})
    assert response.status_code == 422
    assert expected_err in response.text


# ============================================================================
# PART 2: QA SCENARIOS 1 THROUGH 7 (FASTAPI TESTCLIENT)
# ============================================================================

def test_scenario_01_valid_message_with_one_url(test_client: TestClient) -> None:
    """Scenario 1: Ingesting a valid message under 200 characters containing exactly one HTTP/HTTPS URL."""
    payload = {
        "message": "Excited to launch our new product! Visit https://example.com/launch for details."
    }

    mock_result = PlatformPostingResult.success(
        platform="Telegram",
        posted_url="https://t.me/example/101",
        content_id="101",
        public_access=True,
    )
    consolidated = ConsolidatedPostingResponse(
        success=True,
        status="success",
        message=payload["message"],
        cta_url="https://example.com/launch",
        character_count=len(payload["message"]),
        total_platforms=1,
        successful_count=1,
        failed_count=0,
        results=[mock_result],
    )

    with patch.object(PostingService, "publish", return_value=consolidated):
        response = test_client.post("/posting", json=payload)

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["status"] == "success"
    assert data["message"] == payload["message"]
    assert data["cta_url"] == "https://example.com/launch"
    assert data["character_count"] == len(payload["message"])
    assert len(data["results"]) == 1
    assert data["results"][0]["platform"] == "Telegram"
    assert data["results"][0]["status"] == "success"


@pytest.mark.parametrize(
    "empty_content",
    [
        "",
        "   ",
        "\t\n   \r\n",
    ],
)
def test_scenario_02_empty_message(test_client: TestClient, empty_content: str) -> None:
    """Scenario 2: Reject empty or whitespace-only messages with HTTP 422."""
    response = test_client.post("/posting", json={"message": empty_content})
    assert response.status_code == 422
    data = response.json()
    assert "detail" in data
    assert any("empty" in str(err.get("msg", "")).lower() for err in data["detail"])


def test_scenario_03_message_exceeding_200_characters(test_client: TestClient) -> None:
    """Scenario 3: Reject messages that exceed 200 characters with HTTP 422."""
    long_msg = "A" * 175 + " https://example.com" + "B" * 6
    assert len(long_msg) == 201

    response = test_client.post("/posting", json={"message": long_msg})
    assert response.status_code == 422
    data = response.json()
    assert "detail" in data
    error_msg = str(data["detail"][0]["msg"])
    assert "exceeds maximum allowed length of 200 characters" in error_msg
    assert "length: 201" in error_msg


def test_scenario_04_missing_url(test_client: TestClient) -> None:
    """Scenario 4: Reject messages without any URL with HTTP 422."""
    payload = {
        "message": "Here is an important update for all our users without any link."
    }
    response = test_client.post("/posting", json=payload)
    assert response.status_code == 422
    data = response.json()
    error_msg = str(data["detail"][0]["msg"])
    assert "Message does not contain a CTA URL. Exactly one CTA URL is required." in error_msg


def test_scenario_05_multiple_urls(test_client: TestClient) -> None:
    """Scenario 5: Reject messages containing more than one URL with HTTP 422."""
    payload = {
        "message": "Visit our main site https://site1.com and blog https://site2.org"
    }
    response = test_client.post("/posting", json=payload)
    assert response.status_code == 422
    data = response.json()
    error_msg = str(data["detail"][0]["msg"])
    assert "multiple URLs (2 detected)" in error_msg


@pytest.mark.parametrize(
    "invalid_msg",
    [
        "Check our site https://bad..domain.com for news.",
        "Check out https://-leadinghyphen.com for tools.",
        "Check out https://trailinghyphen-.com for updates.",
        "Check out https://999.999.999.999 for access.",
        "Check out https://example.com:70000 for server.",
        "Check out https://example.com:0 for port 0.",
        "Check out https://example.com:abc for invalid port.",
    ],
)
def test_scenario_06_invalid_url(test_client: TestClient, invalid_msg: str) -> None:
    """Scenario 6: Reject malformed hostnames, invalid IPs, or out-of-range ports with HTTP 422."""
    response = test_client.post("/posting", json={"message": invalid_msg})
    assert response.status_code == 422
    data = response.json()
    assert "detail" in data
    error_msg = str(data["detail"][0]["msg"])
    assert "Malformed URL" in error_msg or "port" in error_msg.lower() or "domain" in error_msg.lower()


@pytest.mark.parametrize(
    "unsupported_scheme_msg",
    [
        "Download package from ftp://files.example.com/setup",
        "Send mail to mailto:support@example.com now",
        "Execute code at javascript:alert(1)",
        "Call us at tel:+1234567890 for help",
        "Clone repo at git://github.com/repo/test.git",
        "Open file at file:///C:/secret/password.txt",
    ],
)
def test_scenario_07_unsupported_url_scheme(test_client: TestClient, unsupported_scheme_msg: str) -> None:
    """Scenario 7: Reject non-HTTP/HTTPS schemes (ftp, file, mailto, javascript, git) with HTTP 422."""
    response = test_client.post("/posting", json={"message": unsupported_scheme_msg})
    assert response.status_code == 422
    data = response.json()
    error_msg = str(data["detail"][0]["msg"])
    assert "Unsupported URL scheme" in error_msg or "Only HTTP and HTTPS URLs are permitted" in error_msg

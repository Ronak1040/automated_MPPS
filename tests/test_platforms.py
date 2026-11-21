"""
Platform Integrations Test Suite for Automated Multi-Platform Posting System (AMPPS).

Consolidates all platform architecture, adapter implementations, and resilience tests:
- Part 1: Architecture, base adapter fault isolation, and sanitization (11 tests)
- Part 2: Concrete third-party platform API implementations (11 tests)
- Part 3: QA platform scenarios 8-11, 15, security, and configuration (8 tests)
Total: 30 tests.
"""

import asyncio
from datetime import datetime, timezone
import json
from typing import Any, Dict, List, Optional
import httpx
import pytest

from app.core.config import Settings, get_settings
from app.core.exceptions import (
    PlatformAuthError,
    PlatformNetworkError,
    PlatformPayloadError,
    PlatformRateLimitError,
    PlatformServerError,
)
from app.core.sanitizer import mask_secret, sanitize_headers, sanitize_url
from app.platforms.base import BasePlatformIntegration, PublishOutcome
from app.platforms.bluesky import BlueskyIntegration
from app.platforms.devto import DevToIntegration
from app.platforms.discord import DiscordIntegration
from app.platforms.mastodon import MastodonIntegration
from app.platforms.telegram import TelegramIntegration
from app.schemas.response import PlatformPostingResult
from app.services.posting_service import PostingService


# ============================================================================
# PART 1: STANDARDIZED POSTING RESULT MODEL & SANITIZATION TESTS
# ============================================================================

def test_posting_result_success_schema() -> None:
    """Verify success model adheres to exact field types and format requirements."""
    fixed_dt = datetime(2026, 11, 16, 18, 30, 45, tzinfo=timezone.utc)
    result = PlatformPostingResult.success(
        platform="Telegram",
        posted_url="https://t.me/channel/123",
        public_access=True,
        dt=fixed_dt,
    )

    data = result.model_dump()
    assert data["platform"] == "Telegram"
    assert data["status"] == "success"
    assert data["posted_url"] == "https://t.me/channel/123"
    assert data["public_access"] is True
    assert data["date"] == "2026-11-16"
    assert data["time"] == "18:30:45"
    assert data["error"] is None


def test_posting_result_failure_schema() -> None:
    """Verify failure model adheres to required null fields and error description."""
    result = PlatformPostingResult.failure(
        platform="Bluesky",
        error="Authentication failed: Invalid app password.",
        public_access=False,
    )

    data = result.model_dump()
    assert data["platform"] == "Bluesky"
    assert data["status"] == "failed"
    assert data["posted_url"] is None
    assert data["public_access"] is False
    assert data["date"] is None
    assert data["time"] is None
    assert data["error"] == "Authentication failed: Invalid app password."


def test_secret_sanitizer_masking() -> None:
    """Verify secrets are masked to prevent accidental log leaks."""
    assert mask_secret("1234567890abcdef") == "1234...cdef"
    assert mask_secret("short") == "[REDACTED]"
    assert mask_secret(None) == "[EMPTY]"


def test_url_sanitizer_removes_tokens() -> None:
    """Verify Telegram and Discord tokens are stripped from loggable URLs."""
    raw_tg = "https://api.telegram.org/bot123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11/sendMessage"
    assert sanitize_url(raw_tg) == "https://api.telegram.org/bot[REDACTED]/sendMessage"

    raw_discord = "https://discord.com/api/webhooks/123456789/SuperSecretToken123"
    assert sanitize_url(raw_discord) == "https://discord.com/api/webhooks/123456789/[REDACTED]"


def test_headers_sanitizer_redacts_auth() -> None:
    """Verify sensitive HTTP header values are redacted."""
    headers = {
        "Authorization": "Bearer secret_jwt_token",
        "api-key": "secret_devto_key",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    sanitized = sanitize_headers(headers)
    assert sanitized["Authorization"] == "[REDACTED]"
    assert sanitized["api-key"] == "[REDACTED]"
    assert sanitized["Content-Type"] == "application/json"


# ============================================================================
# PART 1 (CONT.): BASE ADAPTER EXCEPTION ISOLATION & TIMEOUT TESTS
# ============================================================================

class MockSuccessIntegration(BasePlatformIntegration):
    @property
    def name(self) -> str:
        return "MockSuccess"

    @property
    def is_configured(self) -> bool:
        return True

    async def _publish_impl(
        self, client: httpx.AsyncClient, message: str, cta_url: Optional[str] = None
    ) -> str:
        return "https://example.com/posts/100"


class MockUnconfiguredIntegration(BasePlatformIntegration):
    @property
    def name(self) -> str:
        return "MockUnconfigured"

    @property
    def is_configured(self) -> bool:
        return False

    async def _publish_impl(
        self, client: httpx.AsyncClient, message: str, cta_url: Optional[str] = None
    ) -> str:
        return "https://example.com/never"


class MockTimeoutIntegration(BasePlatformIntegration):
    def __init__(self) -> None:
        super().__init__(timeout_seconds=0.01)

    @property
    def name(self) -> str:
        return "MockTimeout"

    @property
    def is_configured(self) -> bool:
        return True

    async def _publish_impl(
        self, client: httpx.AsyncClient, message: str, cta_url: Optional[str] = None
    ) -> str:
        request = httpx.Request("POST", "https://api.example.com/slow")
        raise httpx.ReadTimeout("Read timed out", request=request)


class MockRateLimitIntegration(BasePlatformIntegration):
    @property
    def name(self) -> str:
        return "MockRateLimit"

    @property
    def is_configured(self) -> bool:
        return True

    async def _publish_impl(
        self, client: httpx.AsyncClient, message: str, cta_url: Optional[str] = None
    ) -> str:
        raise PlatformRateLimitError(platform=self.name, message="Rate limit exceeded", retry_after=5.0)


class MockAuthErrorIntegration(BasePlatformIntegration):
    @property
    def name(self) -> str:
        return "MockAuthError"

    @property
    def is_configured(self) -> bool:
        return True

    async def _publish_impl(
        self, client: httpx.AsyncClient, message: str, cta_url: Optional[str] = None
    ) -> str:
        raise PlatformAuthError(platform=self.name, message="Invalid token", status_code=401)


@pytest.mark.asyncio
async def test_base_adapter_unconfigured_fails_gracefully() -> None:
    """Unconfigured integrations must fail gracefully without raising exceptions."""
    adapter = MockUnconfiguredIntegration()
    result = await adapter.publish("Hello https://example.com")
    assert result.status == "failed"
    assert result.posted_url is None
    assert result.error is not None
    assert "not configured" in result.error


@pytest.mark.asyncio
async def test_base_adapter_timeout_handling() -> None:
    """Request timeout must be caught and mapped to a failed PlatformPostingResult."""
    adapter = MockTimeoutIntegration()
    result = await adapter.publish("Hello https://example.com")
    assert result.status == "failed"
    assert result.error is not None
    assert "Network failure" in result.error
    assert "timed out" in result.error


@pytest.mark.asyncio
async def test_base_adapter_rate_limit_handling() -> None:
    """Rate limit exceptions must be mapped to a clean failure result."""
    adapter = MockRateLimitIntegration()
    result = await adapter.publish("Hello https://example.com")
    assert result.status == "failed"
    assert result.error is not None
    assert "Rate limit exceeded" in result.error


@pytest.mark.asyncio
async def test_base_adapter_auth_error_handling() -> None:
    """Authentication errors must be caught and cleanly described."""
    adapter = MockAuthErrorIntegration()
    result = await adapter.publish("Hello https://example.com")
    assert result.status == "failed"
    assert result.error is not None
    assert "Authentication failed" in result.error


@pytest.mark.asyncio
async def test_orchestrator_fault_isolation() -> None:
    """
    CRITICAL: Confirm that a timeout or error on one platform NEVER interrupts,
    cancels, or corrupts results from other platforms.
    """
    success_adapter = MockSuccessIntegration()
    timeout_adapter = MockTimeoutIntegration()
    rate_limit_adapter = MockRateLimitIntegration()
    auth_adapter = MockAuthErrorIntegration()

    service = PostingService(
        integrations=[success_adapter, timeout_adapter, rate_limit_adapter, auth_adapter]
    )

    response = await service.publish(message="Check our platform https://example.com")
    results = response.results
    assert len(results) == 4

    # Map by platform name
    results_by_name = {r.platform: r for r in results}

    # 1. Success adapter must succeed with complete URL and timestamp
    res_success = results_by_name["MockSuccess"]
    assert res_success.status == "success"
    assert res_success.posted_url == "https://example.com/posts/100"
    assert res_success.date is not None
    assert res_success.time is not None
    assert res_success.error is None

    # 2. Timeout adapter must fail independently
    res_timeout = results_by_name["MockTimeout"]
    assert res_timeout.status == "failed"
    assert res_timeout.posted_url is None
    assert res_timeout.error is not None
    assert "timed out" in res_timeout.error

    # 3. Rate limit adapter must fail independently
    res_ratelimit = results_by_name["MockRateLimit"]
    assert res_ratelimit.status == "failed"
    assert res_ratelimit.error is not None
    assert "Rate limit" in res_ratelimit.error

    # 4. Auth error adapter must fail independently
    res_auth = results_by_name["MockAuthError"]
    assert res_auth.status == "failed"
    assert res_auth.error is not None
    assert "Authentication failed" in res_auth.error


@pytest.mark.asyncio
async def test_posting_endpoint_returns_standardized_results(async_client: httpx.AsyncClient) -> None:
    """Verify POST /posting returns standardized results list across platforms."""
    payload = {
        "message": "Launch update https://example.com",
    }
    response = await async_client.post("/posting", json=payload)
    assert response.status_code == 200

    data = response.json()
    assert "results" in data
    assert isinstance(data["results"], list)
    assert len(data["results"]) >= 5  # Telegram, Discord, Mastodon, Bluesky, Dev.to

    # Verify every returned platform result adheres strictly to the required schema
    for res in data["results"]:
        assert "platform" in res
        assert res["status"] in ("success", "failed")
        assert "posted_url" in res
        assert "public_access" in res
        assert "date" in res
        assert "time" in res
        assert "error" in res
        if res["status"] == "failed":
            assert res["posted_url"] is None
            assert res["date"] is None
            assert res["time"] is None
            assert res["error"] is not None


# ============================================================================
# PART 2: CONCRETE THIRD-PARTY PLATFORM API IMPLEMENTATIONS
# ============================================================================

@pytest.mark.asyncio
async def test_telegram_publish_public_channel_success() -> None:
    """Verify Telegram adapter extracts public URL and message ID when chat has username."""
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert "/bot12345:TOKEN/sendMessage" in str(request.url)
        body = json.loads(request.read())
        assert body["chat_id"] == "@mychannel"
        assert body["text"] == "Hello from automated test https://example.com"

        resp_data = {
            "ok": True,
            "result": {
                "message_id": 9876,
                "chat": {
                    "id": -1001234567890,
                    "title": "My Public Channel",
                    "username": "mychannel",
                    "type": "channel",
                },
                "text": body["text"],
            },
        }
        return httpx.Response(200, json=resp_data)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        adapter = TelegramIntegration(bot_token="12345:TOKEN", chat_id="@mychannel")
        result = await adapter.publish(
            message="Hello from automated test https://example.com",
            cta_url="https://example.com",
            client=client,
        )

    assert result.status == "success"
    assert result.posted_url == "https://t.me/mychannel/9876"
    assert result.content_id == "9876"
    assert result.public_access is True
    assert result.error is None


@pytest.mark.asyncio
async def test_telegram_publish_private_group_non_public() -> None:
    """Verify Telegram adapter sets public_access=False when chat has no username."""
    def handler(request: httpx.Request) -> httpx.Response:
        resp_data = {
            "ok": True,
            "result": {
                "message_id": 555,
                "chat": {"id": -1009988776655, "title": "Private Team", "type": "supergroup"},
            },
        }
        return httpx.Response(200, json=resp_data)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        adapter = TelegramIntegration(bot_token="TOKEN", chat_id="-1009988776655")
        result = await adapter.publish("Private update https://example.com", client=client)

    assert result.status == "success"
    assert result.posted_url == "https://t.me/c/9988776655/555"
    assert result.content_id == "555"
    assert result.public_access is False


@pytest.mark.asyncio
async def test_telegram_auth_error() -> None:
    """Verify Telegram 401 response maps to authentication failure."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"ok": False, "description": "Unauthorized"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        adapter = TelegramIntegration(bot_token="INVALID_TOKEN", chat_id="@mychannel")
        result = await adapter.publish("Test message https://example.com", client=client)

    assert result.status == "failed"
    assert result.posted_url is None
    assert result.error is not None
    assert "Authentication failed" in result.error


@pytest.mark.asyncio
async def test_discord_publish_success() -> None:
    """Verify Discord adapter captures ?wait=true response and formats channel jump URL."""
    webhook_url = "https://discord.com/api/webhooks/123456789/token_secret"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert "wait=true" in str(request.url)
        body = json.loads(request.read())
        assert body["content"] == "Discord notification https://example.com"

        resp_data = {
            "id": "112233445566778899",
            "channel_id": "998877665544332211",
            "guild_id": "445566778899001122",
            "content": body["content"],
        }
        return httpx.Response(200, json=resp_data)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        adapter = DiscordIntegration(webhook_url=webhook_url)
        result = await adapter.publish(
            message="Discord notification https://example.com", client=client
        )

    assert result.status == "success"
    assert result.posted_url == "https://discord.com/channels/445566778899001122/998877665544332211/112233445566778899"
    assert result.content_id == "112233445566778899"
    assert result.public_access is False  # Discord requires server authentication


@pytest.mark.asyncio
async def test_discord_rate_limit() -> None:
    """Verify Discord 429 response maps to rate limit error."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"message": "You are being rate limited.", "retry_after": 2.5})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        adapter = DiscordIntegration(webhook_url="https://discord.com/api/webhooks/123/tok")
        result = await adapter.publish("Throttled msg https://example.com", client=client)

    assert result.status == "failed"
    assert result.error is not None
    assert "Rate limit exceeded" in result.error
    assert "2.5" in result.error


@pytest.mark.asyncio
async def test_mastodon_publish_public_status_success() -> None:
    """Verify Mastodon adapter extracts status URL and sets public_access=True for public statuses."""
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert str(request.url) == "https://mastodon.social/api/v1/statuses"
        assert request.headers["Authorization"] == "Bearer MOCK_TOKEN"
        assert "Idempotency-Key" in request.headers

        body = json.loads(request.read())
        assert body["visibility"] == "public"

        resp_data = {
            "id": "111999888777666555",
            "url": "https://mastodon.social/@alice/111999888777666555",
            "visibility": "public",
            "created_at": "2026-11-16T12:00:00.000Z",
        }
        return httpx.Response(200, json=resp_data)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        adapter = MastodonIntegration(instance_url="https://mastodon.social", access_token="MOCK_TOKEN")
        result = await adapter.publish("Federated post https://example.com", client=client)

    assert result.status == "success"
    assert result.posted_url == "https://mastodon.social/@alice/111999888777666555"
    assert result.content_id == "111999888777666555"
    assert result.public_access is True


@pytest.mark.asyncio
async def test_mastodon_content_rejection() -> None:
    """Verify Mastodon 422 Unprocessable Entity maps to content rejected error."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"error": "Validation failed: Text is too long"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        adapter = MastodonIntegration(instance_url="https://mastodon.social", access_token="MOCK_TOKEN")
        result = await adapter.publish("Too long post https://example.com", client=client)

    assert result.status == "failed"
    assert result.error is not None
    assert "Content rejected" in result.error


@pytest.mark.asyncio
async def test_bluesky_session_and_create_record_success() -> None:
    """Verify Bluesky 2-step flow: createSession handshake + createRecord with facets."""
    call_log = []

    def handler(request: httpx.Request) -> httpx.Response:
        call_log.append(str(request.url))
        # Step 1: Session handshake
        if "com.atproto.server.createSession" in str(request.url):
            body = json.loads(request.read())
            assert body["identifier"] == "alice.bsky.social"
            assert body["password"] == "app-pass-123"
            return httpx.Response(
                200,
                json={
                    "accessJwt": "jwt_token_abc",
                    "did": "did:plc:alice123456",
                    "handle": "alice.bsky.social",
                },
            )

        # Step 2: Post creation
        if "com.atproto.repo.createRecord" in str(request.url):
            assert request.headers["Authorization"] == "Bearer jwt_token_abc"
            body = json.loads(request.read())
            assert body["repo"] == "did:plc:alice123456"
            assert body["collection"] == "app.bsky.feed.post"
            record = body["record"]
            assert record["text"] == "Hello Bluesky! https://example.com"
            assert "facets" in record
            assert record["facets"][0]["features"][0]["uri"] == "https://example.com"

            return httpx.Response(
                200,
                json={
                    "uri": "at://did:plc:alice123456/app.bsky.feed.post/3k6b4y7w6cs2b",
                    "cid": "bafyreidfzample74hxampleq3jxxample5q",
                },
            )

        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        adapter = BlueskyIntegration(
            handle="alice.bsky.social",
            app_password="app-pass-123",
            pds_url="https://bsky.social",
        )
        result = await adapter.publish(
            message="Hello Bluesky! https://example.com",
            cta_url="https://example.com",
            client=client,
        )

    assert result.status == "success"
    assert result.posted_url == "https://bsky.app/profile/alice.bsky.social/post/3k6b4y7w6cs2b"
    assert result.content_id == "3k6b4y7w6cs2b"
    assert result.public_access is True
    assert len(call_log) == 2


@pytest.mark.asyncio
async def test_bluesky_session_invalid_credentials() -> None:
    """Verify invalid App Password raises authentication error."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "AuthenticationRequired", "message": "Invalid password"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        adapter = BlueskyIntegration(handle="alice.bsky.social", app_password="wrong-password")
        result = await adapter.publish("Post text https://example.com", client=client)

    assert result.status == "failed"
    assert result.error is not None
    assert "Authentication failed" in result.error


@pytest.mark.asyncio
async def test_devto_publish_article_success() -> None:
    """Verify Dev.to adapter formats article payload, includes api-key header, and extracts URL."""
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert str(request.url) == "https://dev.to/api/articles"
        assert request.headers["api-key"] == "DEVTO_API_KEY_123"
        assert request.headers["accept"] == "application/vnd.forem.api-v1+json"

        body = json.loads(request.read())
        assert "article" in body
        assert body["article"]["published"] is True
        assert body["article"]["body_markdown"] == "Tech announcement https://example.com"

        resp_data = {
            "type_of": "article",
            "id": 7654321,
            "title": body["article"]["title"],
            "url": "https://dev.to/techuser/tech-announcement-slug",
            "published": True,
        }
        return httpx.Response(201, json=resp_data)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        adapter = DevToIntegration(api_key="DEVTO_API_KEY_123")
        result = await adapter.publish("Tech announcement https://example.com", client=client)

    assert result.status == "success"
    assert result.posted_url == "https://dev.to/techuser/tech-announcement-slug"
    assert result.content_id == "7654321"
    assert result.public_access is True


@pytest.mark.asyncio
async def test_devto_invalid_api_key() -> None:
    """Verify Dev.to 401 response produces authentication error."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "unauthorized", "status": 401})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        adapter = DevToIntegration(api_key="BAD_KEY")
        result = await adapter.publish("Tech post https://example.com", client=client)

    assert result.status == "failed"
    assert result.error is not None
    assert "Authentication failed" in result.error


# ============================================================================
# PART 3: QA SCENARIOS (PLATFORMS, AUTH, TIMEOUT, SECURITY, CONFIG)
# ============================================================================

@pytest.mark.asyncio
async def test_scenario_08_successful_publishing() -> None:
    """Scenario 8: All 5 platforms successfully authenticate, publish, and return permalinks."""
    msg = "Exciting release announced! https://example.com"
    cta = "https://example.com"

    # 1. Telegram
    def tg_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "ok": True,
                "result": {
                    "message_id": 1001,
                    "chat": {"id": -100999, "username": "announcements"},
                },
            },
        )

    # 2. Discord
    def discord_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"id": "555111222", "channel_id": "777333"},
        )

    # 3. Mastodon
    def mastodon_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"id": "888444", "url": "https://mastodon.social/@test/888444"},
        )

    # 4. Bluesky
    def bluesky_handler(request: httpx.Request) -> httpx.Response:
        if "createSession" in str(request.url):
            return httpx.Response(
                200,
                json={"accessJwt": "jwt_token", "did": "did:plc:qa999", "handle": "qa.bsky.social"},
            )
        return httpx.Response(
            200,
            json={"uri": "at://did:plc:qa999/app.bsky.feed.post/3kxyzqa"},
        )

    # 5. Dev.to
    def devto_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            201,
            json={"id": 654321, "url": "https://dev.to/tester/qa-article-654321", "published": True},
        )

    # Execute all 5 platforms with their mock clients
    async with httpx.AsyncClient(transport=httpx.MockTransport(tg_handler)) as tg_client:
        tg = TelegramIntegration(bot_token="tok", chat_id="@announcements")
        r_tg = await tg.publish(msg, cta, client=tg_client)

    async with httpx.AsyncClient(transport=httpx.MockTransport(discord_handler)) as disc_client:
        disc = DiscordIntegration(webhook_url="https://discord.com/api/webhooks/123/token")
        r_disc = await disc.publish(msg, cta, client=disc_client)

    async with httpx.AsyncClient(transport=httpx.MockTransport(mastodon_handler)) as mast_client:
        mast = MastodonIntegration(instance_url="https://mastodon.social", access_token="tok")
        r_mast = await mast.publish(msg, cta, client=mast_client)

    async with httpx.AsyncClient(transport=httpx.MockTransport(bluesky_handler)) as bsky_client:
        bsky = BlueskyIntegration(handle="qa.bsky.social", app_password="pw")
        r_bsky = await bsky.publish(msg, cta, client=bsky_client)

    async with httpx.AsyncClient(transport=httpx.MockTransport(devto_handler)) as dev_client:
        dev = DevToIntegration(api_key="key")
        r_dev = await dev.publish(msg, cta, client=dev_client)

    # Verify all succeeded
    all_results = [r_tg, r_disc, r_mast, r_bsky, r_dev]
    for res in all_results:
        assert res.status == "success"
        assert res.posted_url is not None
        assert res.date is not None
        assert res.time is not None
        assert res.error is None

    assert r_tg.posted_url == "https://t.me/announcements/1001"
    assert r_tg.public_access is True

    assert r_disc.posted_url is not None
    assert "555111222" in r_disc.posted_url
    assert r_disc.public_access is False  # Discord is closed/authenticated

    assert r_mast.posted_url == "https://mastodon.social/@test/888444"
    assert r_mast.public_access is True

    assert r_bsky.posted_url == "https://bsky.app/profile/qa.bsky.social/post/3kxyzqa"
    assert r_bsky.public_access is True

    assert r_dev.posted_url == "https://dev.to/tester/qa-article-654321"
    assert r_dev.public_access is True


@pytest.mark.asyncio
async def test_scenario_09_authentication_failure() -> None:
    """Scenario 9: Upstream platforms returning HTTP 401/403 are mapped to failure without leaking credentials."""
    def auth_fail_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"ok": False, "error_code": 401, "description": "Unauthorized: invalid bot token"})

    transport = httpx.MockTransport(auth_fail_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        adapter = TelegramIntegration(bot_token="secret_token_12345", chat_id="@mychannel")
        result = await adapter.publish("Test message https://example.com", client=client)

    assert result.status == "failed"
    assert result.posted_url is None
    assert result.public_access is False
    assert result.error is not None
    assert "Authentication failed" in result.error
    assert "secret_token_12345" not in result.error


@pytest.mark.asyncio
async def test_scenario_10_rate_limit_response() -> None:
    """Scenario 10: Upstream platforms returning HTTP 429 are mapped to rate-limit failure cleanly."""
    def rate_limit_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            json={"message": "You are being rate limited.", "retry_after": 30.5, "global": False},
            headers={"Retry-After": "31"},
        )

    transport = httpx.MockTransport(rate_limit_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        adapter = DiscordIntegration(webhook_url="https://discord.com/api/webhooks/123/token")
        result = await adapter.publish("Test message https://example.com", client=client)

    assert result.status == "failed"
    assert result.posted_url is None
    assert result.error is not None
    assert "Rate limit exceeded" in result.error


@pytest.mark.asyncio
async def test_scenario_11_network_timeout() -> None:
    """Scenario 11: Network connection or read timeouts are handled with zero server crash."""
    def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("Connection read timed out while waiting for gateway")

    transport = httpx.MockTransport(timeout_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        adapter = MastodonIntegration(instance_url="https://mastodon.social", access_token="tok")
        result = await adapter.publish("Test message https://example.com", client=client)

    assert result.status == "failed"
    assert result.posted_url is None
    assert result.error is not None
    assert "timed out" in result.error.lower()


@pytest.mark.asyncio
async def test_scenario_15_unexpected_api_response_malformed_html() -> None:
    """Scenario 15A: Upstream returns HTTP 200 with HTML error page (Cloudflare/proxy) instead of JSON."""
    def html_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text="<html><head><title>Cloudflare 502 Bad Gateway</title></head><body>Error</body></html>",
            headers={"Content-Type": "text/html"},
        )

    transport = httpx.MockTransport(html_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        adapter = TelegramIntegration(bot_token="123:TOKEN", chat_id="@channel")
        result = await adapter.publish("Hello https://example.com", client=client)

    assert result.status == "failed"
    assert result.posted_url is None
    assert result.error is not None
    assert "Invalid JSON" in result.error or "Unexpected" in result.error


@pytest.mark.asyncio
async def test_scenario_15_unexpected_api_response_missing_fields() -> None:
    """Scenario 15B: Upstream returns HTTP 200 JSON but omits documented required fields."""
    def missing_field_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": 12345, "title": "Test"})

    transport = httpx.MockTransport(missing_field_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        adapter = DevToIntegration(api_key="valid_key")
        result = await adapter.publish("Test message https://example.com", client=client)

    assert result.status == "failed"
    assert result.posted_url is None
    assert result.error is not None
    assert "lacked 'url'" in result.error.lower() or "missing" in result.error.lower()


def test_security_secret_redaction() -> None:
    """Verify that credentials, tokens, and authorization headers are never exposed."""
    raw_tg = "https://api.telegram.org/bot123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11/sendMessage"
    assert "bot123456:ABC-DEF" not in sanitize_url(raw_tg)
    assert "/bot[REDACTED]/sendMessage" in sanitize_url(raw_tg)

    raw_discord = "https://discord.com/api/webhooks/123456789/SuperSecretWebhookTokenString"
    assert "SuperSecretWebhookTokenString" not in sanitize_url(raw_discord)
    assert "/api/webhooks/123456789/[REDACTED]" in sanitize_url(raw_discord)

    headers = {
        "Authorization": "Bearer my_super_secret_jwt_token",
        "api-key": "my_devto_secret_key",
        "Content-Type": "application/json",
    }
    sanitized = sanitize_headers(headers)
    assert sanitized["Authorization"] == "[REDACTED]"
    assert sanitized["api-key"] == "[REDACTED]"
    assert sanitized["Content-Type"] == "application/json"


def test_configuration_management() -> None:
    """Verify configuration loads cleanly, cleans empty strings to None, and provides proper defaults."""
    settings = Settings(
        app_name="Test MPPS",
        port=4000,
        telegram_bot_token="   ",
        discord_webhook_url="https://discord.com/webhook",
    )
    assert settings.app_name == "Test MPPS"
    assert settings.port == 4000
    assert settings.telegram_bot_token is None
    assert settings.discord_webhook_url == "https://discord.com/webhook"

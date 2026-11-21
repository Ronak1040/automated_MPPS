"""
Posting Orchestration & Health Test Suite for Automated Multi-Platform Posting System (AMPPS).

Consolidates all health check, API ingress endpoint, and PostingService orchestration tests:
- Part 1: Health check & OpenAPI/Swagger documentation endpoints (4 tests)
- Part 2: POST /posting endpoint handling, TestClient verification, and error mapping (6 tests)
- Part 3: PostingService orchestration, timeouts, and concurrent dispatch (6 tests)
- Part 4: QA orchestration scenarios 12-14 and parallel timing benchmark (4 tests)
Total: 20 tests.
"""

import asyncio
import time
from typing import Any, List, Optional
from fastapi.testclient import TestClient
import httpx
from httpx import AsyncClient
import pytest

from app.core.exceptions import (
    PlatformAuthError,
    PlatformNetworkError,
    PlatformRateLimitError,
    PlatformServerError,
)
from app.platforms.base import BasePlatformIntegration, PublishOutcome
from app.schemas.response import PlatformPostingResult
from app.services.posting_service import ConsolidatedPostingResponse, PostingService


# ============================================================================
# PART 1: HEALTH CHECK & SWAGGER / OPENAPI SPECIFICATION TESTS
# ============================================================================

@pytest.mark.asyncio
async def test_root_health_check(async_client: AsyncClient) -> None:
    """Verify that root GET /health returns 200 OK and valid health payload."""
    response = await async_client.get("/health")
    assert response.status_code == 200

    data = response.json()
    assert data["status"] == "healthy"
    assert "app_name" in data
    assert "environment" in data
    assert data["version"] == "1.0.0"
    assert "timestamp" in data
    assert data["details"]["port"] == 4000


@pytest.mark.asyncio
async def test_v1_health_check(async_client: AsyncClient) -> None:
    """Verify that versioned GET /api/v1/health returns 200 OK."""
    response = await async_client.get("/api/v1/health")
    assert response.status_code == 200

    data = response.json()
    assert data["status"] == "healthy"
    assert data["version"] == "1.0.0"


@pytest.mark.asyncio
async def test_swagger_documentation_accessible(async_client: AsyncClient) -> None:
    """Verify that Swagger UI documentation is available at /docs."""
    response = await async_client.get("/docs")
    assert response.status_code == 200
    assert "swagger-ui" in response.text.lower() or "html" in response.headers.get("content-type", "")


@pytest.mark.asyncio
async def test_openapi_schema_accessible(async_client: AsyncClient) -> None:
    """Verify that the OpenAPI JSON specification is generated properly."""
    response = await async_client.get("/openapi.json")
    assert response.status_code == 200

    schema = response.json()
    assert "openapi" in schema
    assert "/health" in schema["paths"]
    assert "/api/v1/health" in schema["paths"]


# ============================================================================
# PART 2: MAIN POSTING ENDPOINT TESTS (FASTAPI TESTCLIENT)
# ============================================================================

def test_main_posting_endpoint_valid_request(test_client: TestClient) -> None:
    """Verify POST /posting with canonical valid request using FastAPI TestClient."""
    payload = {
        "message": "Check out our latest business platform. https://example.com"
    }

    response = test_client.post("/posting", json=payload)
    assert response.status_code == 200

    data = response.json()
    assert "success" in data
    assert isinstance(data["success"], bool)
    assert "results" in data
    assert isinstance(data["results"], list)
    assert len(data["results"]) >= 5

    # Verify response structure for every platform
    for res in data["results"]:
        assert "platform" in res
        assert res["status"] in ("success", "failed")
        assert "posted_url" in res
        assert "public_access" in res
        assert "date" in res
        assert "time" in res
        assert "error" in res


def test_main_posting_endpoint_rejects_platform_selection(test_client: TestClient) -> None:
    """Verify that client cannot pass platform selection (service auto-targets platforms)."""
    payload = {
        "message": "Announcement https://example.com",
        "platforms": ["Telegram"],  # Disallowed parameter
    }
    response = test_client.post("/posting", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert len(data["results"]) >= 5


def test_main_posting_endpoint_validation_errors(test_client: TestClient) -> None:
    """Verify HTTP 422 is returned for invalid payloads using TestClient."""
    # 1. Missing message field
    r1 = test_client.post("/posting", json={})
    assert r1.status_code == 422

    # 2. Missing CTA URL
    r2 = test_client.post("/posting", json={"message": "Plain message without any link"})
    assert r2.status_code == 422
    assert "does not contain a CTA URL" in r2.text

    # 3. Exceeds 200 characters
    r3 = test_client.post(
        "/posting",
        json={"message": "A" * 190 + " https://example.com"},
    )
    assert r3.status_code == 422
    assert "exceeds maximum allowed length" in r3.text


def test_main_posting_endpoint_partial_success_mocked(
    test_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Verify endpoint accurately returns partial success results when 3 platforms succeed
    and 2 platforms fail.
    """
    mock_results: List[PlatformPostingResult] = [
        PlatformPostingResult.success("Telegram", "https://t.me/channel/1", True, content_id="1"),
        PlatformPostingResult.success("Discord", "https://discord.com/channels/1/2/3", False, content_id="3"),
        PlatformPostingResult.success("Mastodon", "https://mastodon.social/@user/5", True, content_id="5"),
        PlatformPostingResult.failure("Bluesky", "Rate limit exceeded (HTTP 429)"),
        PlatformPostingResult.failure("Dev.to", "Authentication failed (HTTP 401)"),
    ]

    mock_consolidated = ConsolidatedPostingResponse(
        message="Check out our latest business platform. https://example.com",
        cta_url="https://example.com",
        character_count=59,
        status="success",
        success=True,
        total_platforms=5,
        successful_count=3,
        failed_count=2,
        results=mock_results,
    )

    class MockPostingService:
        async def publish(self, message: str) -> ConsolidatedPostingResponse:
            return mock_consolidated

    # Patch get_posting_service in app.api.routes
    monkeypatch.setattr("app.api.routes.get_posting_service", lambda: MockPostingService())

    payload = {"message": "Check out our latest business platform. https://example.com"}
    response = test_client.post("/posting", json=payload)
    assert response.status_code == 200

    data = response.json()
    assert data["success"] is True
    assert data["status"] == "success"
    assert data["successful_count"] == 3
    assert data["failed_count"] == 2
    assert len(data["results"]) == 5

    results_by_platform = {r["platform"]: r for r in data["results"]}
    assert results_by_platform["Telegram"]["status"] == "success"
    assert results_by_platform["Telegram"]["posted_url"] == "https://t.me/channel/1"
    assert results_by_platform["Bluesky"]["status"] == "failed"
    assert results_by_platform["Bluesky"]["posted_url"] is None
    assert "Rate limit" in results_by_platform["Bluesky"]["error"]


def test_main_posting_endpoint_unexpected_error_returns_500(
    test_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Requirement 5: Handle unexpected application errors gracefully with HTTP 500."""
    class BrokenPostingService:
        async def publish(self, message: str) -> ConsolidatedPostingResponse:
            raise RuntimeError("Database connection suddenly dropped")

    monkeypatch.setattr("app.api.routes.get_posting_service", lambda: BrokenPostingService())

    payload = {"message": "Check out our platform https://example.com"}
    response = test_client.post("/posting", json=payload)
    assert response.status_code == 500
    assert "Database connection suddenly dropped" in response.json()["detail"]


def test_main_posting_endpoint_swagger_openapi_schema(test_client: TestClient) -> None:
    """Requirement 6 & 7: Verify OpenAPI documentation and Swagger UI configuration."""
    response = test_client.get("/openapi.json")
    assert response.status_code == 200

    schema = response.json()
    assert "/posting" in schema["paths"]
    posting_post_op = schema["paths"]["/posting"]["post"]

    assert posting_post_op["summary"] == "Publish Post to All Configured Platforms"
    assert "Posting" in posting_post_op["tags"]
    assert "200" in posting_post_op["responses"]
    assert "422" in posting_post_op["responses"]
    assert "500" in posting_post_op["responses"]

    docs_resp = test_client.get("/docs")
    assert docs_resp.status_code == 200


# ============================================================================
# PART 3: POSTINGSERVICE UNIT TESTS & MOCK PLATFORMS
# ============================================================================

class MockConfiguredIntegration(BasePlatformIntegration):
    def __init__(
        self,
        name: str,
        return_url: str,
        content_id: str,
        public_access: bool = True,
        delay_seconds: float = 0.0,
    ) -> None:
        super().__init__(timeout_seconds=5.0)
        self._name = name
        self.return_url = return_url
        self.mock_content_id = content_id
        self.mock_public_access = public_access
        self.delay_seconds = delay_seconds

    @property
    def name(self) -> str:
        return self._name

    @property
    def is_configured(self) -> bool:
        return True

    async def _publish_impl(
        self, client: httpx.AsyncClient, message: str, cta_url: Optional[str] = None
    ) -> PublishOutcome:
        if self.delay_seconds > 0:
            await asyncio.sleep(self.delay_seconds)
        return PublishOutcome(
            posted_url=self.return_url,
            content_id=self.mock_content_id,
            public_access=self.mock_public_access,
        )


class MockFailingIntegration(BasePlatformIntegration):
    def __init__(self, name: str, error_type: str = "rate_limit") -> None:
        super().__init__(timeout_seconds=5.0)
        self._name = name
        self.error_type = error_type

    @property
    def name(self) -> str:
        return self._name

    @property
    def is_configured(self) -> bool:
        return True

    async def _publish_impl(
        self, client: httpx.AsyncClient, message: str, cta_url: Optional[str] = None
    ) -> PublishOutcome:
        if self.error_type == "rate_limit":
            raise PlatformRateLimitError(platform=self.name, message="Too Many Requests: hit 429 limit", retry_after=10.0)
        elif self.error_type == "auth":
            raise PlatformAuthError(platform=self.name, message="Invalid Bearer Token (HTTP 401)", status_code=401)
        elif self.error_type == "timeout":
            req = httpx.Request("POST", "https://mock.platform.com/api")
            raise httpx.ReadTimeout("Socket read timed out", request=req)
        else:
            raise RuntimeError("Unknown critical error")


class MockUnconfiguredPlatform(BasePlatformIntegration):
    def __init__(self, name: str) -> None:
        super().__init__(timeout_seconds=5.0)
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def is_configured(self) -> bool:
        return False

    async def _publish_impl(
        self, client: httpx.AsyncClient, message: str, cta_url: Optional[str] = None
    ) -> PublishOutcome:
        raise NotImplementedError("Should never be called when unconfigured")


def test_posting_service_automatically_identifies_configured_platforms() -> None:
    """Requirement 2: Automatically identify all configured platform integrations."""
    p1 = MockConfiguredIntegration("Platform1", "https://p1.com/1", "1")
    p2 = MockUnconfiguredPlatform("Platform2")
    p3 = MockConfiguredIntegration("Platform3", "https://p3.com/3", "3")
    p4 = MockUnconfiguredPlatform("Platform4")

    service = PostingService(integrations=[p1, p2, p3, p4])

    configured = service.identify_configured_integrations()
    assert len(configured) == 2
    configured_names = [i.name for i in configured]
    assert "Platform1" in configured_names
    assert "Platform3" in configured_names
    assert "Platform2" not in configured_names
    assert "Platform4" not in configured_names


@pytest.mark.asyncio
async def test_posting_service_partial_success_example() -> None:
    """
    CRITICAL REQUIREMENT:
    Support partial success scenario:
    - 3 platforms succeed
    - 2 platforms fail
    - Overall request must still return all 5 results
    - Define overall success based on whether at least one platform published successfully.
    """
    telegram_mock = MockConfiguredIntegration(
        name="Telegram",
        return_url="https://t.me/business/101",
        content_id="101",
        public_access=True,
    )
    discord_mock = MockConfiguredIntegration(
        name="Discord",
        return_url="https://discord.com/channels/1/2/3",
        content_id="3",
        public_access=False,
    )
    mastodon_mock = MockConfiguredIntegration(
        name="Mastodon",
        return_url="https://mastodon.social/@brand/777",
        content_id="777",
        public_access=True,
    )

    bluesky_mock = MockFailingIntegration(name="Bluesky", error_type="rate_limit")
    devto_mock = MockFailingIntegration(name="Dev.to", error_type="auth")

    service = PostingService(
        integrations=[telegram_mock, discord_mock, mastodon_mock, bluesky_mock, devto_mock]
    )

    msg = "Check out our newest analytics portal. https://analytics.example.com"
    response: ConsolidatedPostingResponse = await service.publish(message=msg)

    assert response.total_platforms == 5
    assert len(response.results) == 5
    assert response.successful_count == 3
    assert response.failed_count == 2
    assert response.success is True
    assert response.status == "success"

    results_map = {r.platform: r for r in response.results}

    tg_res = results_map["Telegram"]
    assert tg_res.status == "success"
    assert tg_res.posted_url == "https://t.me/business/101"
    assert tg_res.content_id == "101"
    assert tg_res.public_access is True
    assert tg_res.date is not None
    assert tg_res.time is not None
    assert tg_res.error is None

    dc_res = results_map["Discord"]
    assert dc_res.status == "success"
    assert dc_res.posted_url == "https://discord.com/channels/1/2/3"
    assert dc_res.content_id == "3"
    assert dc_res.public_access is False
    assert dc_res.date is not None
    assert dc_res.time is not None
    assert dc_res.error is None

    md_res = results_map["Mastodon"]
    assert md_res.status == "success"
    assert md_res.posted_url == "https://mastodon.social/@brand/777"
    assert md_res.content_id == "777"
    assert md_res.public_access is True
    assert md_res.error is None

    bs_res = results_map["Bluesky"]
    assert bs_res.status == "failed"
    assert bs_res.posted_url is None
    assert bs_res.date is None
    assert bs_res.time is None
    assert bs_res.error is not None
    assert "Rate limit exceeded" in bs_res.error

    dv_res = results_map["Dev.to"]
    assert dv_res.status == "failed"
    assert dv_res.posted_url is None
    assert dv_res.date is None
    assert dv_res.time is None
    assert dv_res.error is not None
    assert "Authentication failed" in dv_res.error


@pytest.mark.asyncio
async def test_posting_service_complete_failure() -> None:
    """Verify that when 0 platforms succeed, overall status is 'failed' and success is False."""
    f1 = MockFailingIntegration("Platform1", error_type="rate_limit")
    f2 = MockFailingIntegration("Platform2", error_type="auth")
    f3 = MockFailingIntegration("Platform3", error_type="timeout")

    service = PostingService(integrations=[f1, f2, f3])
    response = await service.publish("Launch event: https://example.com/event")

    assert response.total_platforms == 3
    assert response.successful_count == 0
    assert response.failed_count == 3
    assert response.success is False
    assert response.status == "failed"


@pytest.mark.asyncio
async def test_posting_service_complete_success() -> None:
    """Verify that when all platforms succeed, overall status is 'success'."""
    s1 = MockConfiguredIntegration("Platform1", "https://p1.com/1", "1")
    s2 = MockConfiguredIntegration("Platform2", "https://p2.com/2", "2")

    service = PostingService(integrations=[s1, s2])
    response = await service.publish("Success message: https://example.com/ok")

    assert response.total_platforms == 2
    assert response.successful_count == 2
    assert response.failed_count == 0
    assert response.success is True
    assert response.status == "success"


@pytest.mark.asyncio
async def test_posting_service_timeout_isolation() -> None:
    """
    Verify that a slow / hanging platform times out without blocking
    or delaying the fast successful platforms.
    """
    fast_adapter = MockConfiguredIntegration(
        name="FastPlatform",
        return_url="https://fast.com/post/1",
        content_id="1",
        delay_seconds=0.01,
    )
    slow_timeout_adapter = MockFailingIntegration(
        name="SlowPlatform",
        error_type="timeout",
    )

    service = PostingService(
        integrations=[fast_adapter, slow_timeout_adapter],
        default_timeout=3.0,
    )

    response = await service.publish("Speed test https://example.com")

    assert response.total_platforms == 2
    assert response.successful_count == 1
    assert response.failed_count == 1
    assert response.success is True
    assert response.status == "success"

    results_by_name = {r.platform: r for r in response.results}
    assert results_by_name["FastPlatform"].status == "success"
    assert results_by_name["SlowPlatform"].status == "failed"
    assert results_by_name["SlowPlatform"].error is not None
    assert "timed out" in results_by_name["SlowPlatform"].error


@pytest.mark.asyncio
async def test_posting_service_asyncio_gather_concurrent_timing() -> None:
    """
    Verify that platforms run in parallel (asyncio.gather), not sequentially.
    Two tasks taking 0.2s each should complete in ~0.2s, not >= 0.4s.
    """
    p1 = MockConfiguredIntegration("P1", "https://p1.com", "1", delay_seconds=0.2)
    p2 = MockConfiguredIntegration("P2", "https://p2.com", "2", delay_seconds=0.2)

    service = PostingService(integrations=[p1, p2])

    async with httpx.AsyncClient() as client:
        start_time = asyncio.get_event_loop().time()
        response = await service.publish("Parallel test https://example.com", client=client)
        elapsed = asyncio.get_event_loop().time() - start_time

    assert response.successful_count == 2
    assert elapsed < 0.35


# ============================================================================
# PART 4: QA SCENARIOS 12-14 AND CONCURRENCY VERIFICATION
# ============================================================================

@pytest.mark.asyncio
async def test_scenario_12_one_platform_failing_while_others_succeed() -> None:
    """Scenario 12: 4 platforms succeed, 1 platform fails. The orchestrator returns overall success."""
    class MockAdapter(BasePlatformIntegration):
        def __init__(self, name: str, should_fail: bool) -> None:
            super().__init__()
            self._name = name
            self.should_fail = should_fail

        @property
        def name(self) -> str:
            return self._name

        @property
        def is_configured(self) -> bool:
            return True

        async def _publish_impl(self, client: httpx.AsyncClient, message: str, cta_url: Any = None) -> PublishOutcome:
            if self.should_fail:
                raise PlatformRateLimitError(self.name, "Rate limit hit", status_code=429)
            return PublishOutcome(
                posted_url=f"https://{self.name.lower()}.com/post/100",
                content_id="100",
                public_access=True,
            )

    adapters = [
        MockAdapter("Telegram", False),
        MockAdapter("Discord", True),
        MockAdapter("Mastodon", False),
        MockAdapter("Bluesky", False),
        MockAdapter("Dev.to", False),
    ]

    service = PostingService(integrations=adapters)
    response = await service.publish("Test update https://example.com")

    assert response.success is True
    assert response.status == "success"
    assert response.total_platforms == 5
    assert response.successful_count == 4
    assert response.failed_count == 1

    discord_res = next(r for r in response.results if r.platform == "Discord")
    assert discord_res.status == "failed"
    assert discord_res.error is not None
    assert "Rate limit" in discord_res.error

    tg_res = next(r for r in response.results if r.platform == "Telegram")
    assert tg_res.status == "success"
    assert tg_res.posted_url == "https://telegram.com/post/100"


@pytest.mark.asyncio
async def test_scenario_13_all_platforms_failing() -> None:
    """Scenario 13: When all platforms fail, response has success=False, status='failed', 0 successful."""
    class FailingAdapter(BasePlatformIntegration):
        def __init__(self, name: str, error_type: str) -> None:
            super().__init__()
            self._name = name
            self.error_type = error_type

        @property
        def name(self) -> str:
            return self._name

        @property
        def is_configured(self) -> bool:
            return True

        async def _publish_impl(self, client: httpx.AsyncClient, message: str, cta_url: Any = None) -> PublishOutcome:
            if self.error_type == "auth":
                raise PlatformAuthError(self.name, "Bad API Key")
            elif self.error_type == "timeout":
                raise PlatformNetworkError(self.name, "Socket timeout")
            elif self.error_type == "server":
                raise PlatformServerError(self.name, "Internal server error")
            raise PlatformRateLimitError(self.name, "429 Too Many Requests")

    adapters = [
        FailingAdapter("Telegram", "auth"),
        FailingAdapter("Discord", "rate"),
        FailingAdapter("Mastodon", "timeout"),
        FailingAdapter("Bluesky", "server"),
        FailingAdapter("Dev.to", "auth"),
    ]

    service = PostingService(integrations=adapters)
    response = await service.publish("Status test https://example.com")

    assert response.success is False
    assert response.status == "failed"
    assert response.total_platforms == 5
    assert response.successful_count == 0
    assert response.failed_count == 5
    assert len(response.results) == 5
    for r in response.results:
        assert r.status == "failed"
        assert r.posted_url is None
        assert r.error is not None


@pytest.mark.asyncio
async def test_scenario_14_partial_success() -> None:
    """Scenario 14: 2 platforms succeed and 3 fail. Verifies partial success contract."""
    class ControlledAdapter(BasePlatformIntegration):
        def __init__(self, name: str, succeed: bool) -> None:
            super().__init__()
            self._name = name
            self.succeed = succeed

        @property
        def name(self) -> str:
            return self._name

        @property
        def is_configured(self) -> bool:
            return True

        async def _publish_impl(self, client: httpx.AsyncClient, message: str, cta_url: Any = None) -> PublishOutcome:
            if not self.succeed:
                raise PlatformServerError(self.name, "Gateway down", status_code=502)
            return PublishOutcome(
                posted_url=f"https://{self.name.lower()}.social/post/888",
                content_id="888",
                public_access=True,
            )

    adapters = [
        ControlledAdapter("Telegram", True),
        ControlledAdapter("Discord", False),
        ControlledAdapter("Mastodon", False),
        ControlledAdapter("Bluesky", True),
        ControlledAdapter("Dev.to", False),
    ]

    service = PostingService(integrations=adapters)
    response = await service.publish("Multi check https://example.com")

    assert response.success is True
    assert response.status == "success"
    assert response.total_platforms == 5
    assert response.successful_count == 2
    assert response.failed_count == 3
    assert len(response.results) == 5


@pytest.mark.asyncio
async def test_async_parallel_concurrency() -> None:
    """Verify that platforms execute concurrently, not serially."""
    class DelayAdapter(BasePlatformIntegration):
        def __init__(self, name: str, delay: float) -> None:
            super().__init__()
            self._name = name
            self.delay = delay

        @property
        def name(self) -> str:
            return self._name

        @property
        def is_configured(self) -> bool:
            return True

        async def _publish_impl(self, client: httpx.AsyncClient, message: str, cta_url: Any = None) -> PublishOutcome:
            await asyncio.sleep(self.delay)
            return PublishOutcome(
                posted_url=f"https://{self.name.lower()}.com/post/1",
                content_id="1",
                public_access=True,
            )

    adapters = [DelayAdapter("Platform1", 0.15), DelayAdapter("Platform2", 0.15), DelayAdapter("Platform3", 0.15)]
    service = PostingService(integrations=adapters)

    async with httpx.AsyncClient() as shared_client:
        start = time.perf_counter()
        response = await service.publish("Parallel test https://example.com", client=shared_client)
        elapsed = time.perf_counter() - start

    assert response.success is True
    assert response.successful_count == 3
    assert elapsed < 0.35, f"Execution took {elapsed:.2f}s, expected parallel execution < 0.35s"

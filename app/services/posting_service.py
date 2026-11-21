"""
Main posting orchestration service.
Coordinates concurrent, non-blocking publishing across registered platform integrations
discovered via the PlatformRegistry, enforces timeouts, isolates failures,
and returns a consolidated response supporting partial success.
"""

import asyncio
from typing import List, Optional, Sequence
import httpx
from pydantic import BaseModel, Field

from app.core.logging import get_logger
from app.core.validators import extract_and_validate_cta_url
from app.platforms.base import BasePlatformIntegration
from app.schemas.response import PlatformPostingResult
from app.services.platform_registry import PlatformRegistry, get_platform_registry

logger = get_logger(__name__)


class ConsolidatedPostingResponse(BaseModel):
    """
    Consolidated response model for multi-platform posting operations.
    Maintains complete audit visibility into partial success states.
    """

    message: str = Field(description="The validated user message.")
    cta_url: str = Field(description="Extracted and verified HTTP/HTTPS CTA URL.")
    character_count: int = Field(description="Total character count of the message.")
    status: str = Field(
        description="Overall status: 'success' if at least one platform published successfully, 'failed' otherwise."
    )
    success: bool = Field(
        description="True if at least one platform published successfully, False otherwise."
    )
    total_platforms: int = Field(description="Total number of platforms targeted.")
    successful_count: int = Field(description="Number of platforms that published successfully.")
    failed_count: int = Field(description="Number of platforms that failed.")
    results: List[PlatformPostingResult] = Field(
        default_factory=list,
        description="Standardized posting results across all platforms.",
    )


class PostingService:
    """
    Main posting orchestration service.
    Coordinates concurrent, non-blocking publishing across all registered platform integrations,
    automatically detects configured platforms, enforces timeouts, isolates failures,
    and returns a consolidated response supporting partial success.
    """

    def __init__(
        self,
        registry: Optional[PlatformRegistry] = None,
        integrations: Optional[Sequence[BasePlatformIntegration]] = None,
        default_timeout: float = 10.0,
    ) -> None:
        self.default_timeout = default_timeout
        if integrations is not None:
            self._integrations = list(integrations)
            self._registry = None
        else:
            self._registry = registry or get_platform_registry()
            self._integrations = self._registry.get_registered_platforms()

    @property
    def registered_integrations(self) -> List[BasePlatformIntegration]:
        """Return all platform integrations available in the system."""
        if self._registry is not None:
            return self._registry.get_registered_platforms()
        return list(self._integrations)

    def identify_configured_integrations(self) -> List[BasePlatformIntegration]:
        """
        Automatically identify and return all platform integrations that have
        valid configuration / credentials supplied in the environment.
        """
        if self._registry is not None:
            return self._registry.get_configured_platforms()
        configured = [i for i in self._integrations if i.is_configured]
        logger.debug(
            "Identified %d configured integration(s): %s",
            len(configured),
            [i.name for i in configured],
        )
        return configured

    async def _safe_execute_platform(
        self,
        integration: BasePlatformIntegration,
        message: str,
        cta_url: str,
        client: Optional[httpx.AsyncClient] = None,
    ) -> PlatformPostingResult:
        """
        Execute publishing on a single platform with timeout enforcement and exception trapping.
        Guarantees that no exception escapes to interrupt other concurrent platform tasks.
        """
        try:
            # Apply an async timeout buffer slightly above adapter timeout
            return await asyncio.wait_for(
                integration.publish(message=message, cta_url=cta_url, client=client),
                timeout=self.default_timeout + 1.0,
            )
        except asyncio.TimeoutError:
            logger.error("Platform %s publishing operation timed out at service layer", integration.name)
            return PlatformPostingResult.failure(
                platform=integration.name,
                error=f"Publishing operation timed out after {self.default_timeout}s.",
                public_access=False,
            )
        except Exception as e:
            logger.exception("Unexpected error in platform execution task for %s", integration.name)
            return PlatformPostingResult.failure(
                platform=integration.name,
                error=f"Execution error: {str(e)}",
                public_access=False,
            )

    async def _dispatch_tasks(
        self,
        target_integrations: Sequence[BasePlatformIntegration],
        message: str,
        cta_url: str,
        client: httpx.AsyncClient,
    ) -> List[PlatformPostingResult]:
        """Dispatch publishing tasks concurrently with asyncio.gather."""
        tasks = [
            self._safe_execute_platform(
                integration=integration,
                message=message,
                cta_url=cta_url,
                client=client,
            )
            for integration in target_integrations
        ]

        gather_results = await asyncio.gather(*tasks, return_exceptions=True)

        collected_results: List[PlatformPostingResult] = []
        for integration, res in zip(target_integrations, gather_results):
            if isinstance(res, Exception):
                logger.error("Gather caught raw exception from %s: %s", integration.name, res)
                collected_results.append(
                    PlatformPostingResult.failure(
                        platform=integration.name,
                        error=f"Uncaught task exception: {str(res)}",
                        public_access=False,
                    )
                )
            elif isinstance(res, PlatformPostingResult):
                collected_results.append(res)
            else:
                collected_results.append(
                    PlatformPostingResult.failure(
                        platform=integration.name,
                        error="Integration returned unexpected payload format.",
                        public_access=False,
                    )
                )
        return collected_results

    async def publish(
        self,
        message: str,
        client: Optional[httpx.AsyncClient] = None,
    ) -> ConsolidatedPostingResponse:
        """
        Main orchestration entry point:
        1. Validates and extracts the CTA URL.
        2. Automatically targets all platform integrations without accepting user selection.
        3. Concurrently publishes via asyncio.gather with return_exceptions=True.
        4. Isolates individual platform failures.
        5. Computes overall success based on whether at least one platform succeeded.
        6. Returns a consolidated response with accurate per-platform details.
        """
        # Step 1: Extract and validate CTA URL from the validated message
        cta_url = extract_and_validate_cta_url(message)
        character_count = len(message)

        # Step 2: Target all registered integrations automatically (ignoring any external platform selection)
        target_integrations = self.registered_integrations

        logger.info(
            "Initiating multi-platform dispatch across %d platforms: %s",
            len(target_integrations),
            [i.name for i in target_integrations],
        )

        # Step 3: Fan out asynchronously using asyncio.gather with shared client pool
        if client is not None:
            collected_results = await self._dispatch_tasks(
                target_integrations=target_integrations,
                message=message,
                cta_url=cta_url,
                client=client,
            )
        else:
            async with httpx.AsyncClient(timeout=self.default_timeout) as managed_client:
                collected_results = await self._dispatch_tasks(
                    target_integrations=target_integrations,
                    message=message,
                    cta_url=cta_url,
                    client=managed_client,
                )

        # Step 4: Evaluate partial success & define overall status
        successful_count = sum(1 for r in collected_results if r.status == "success")
        failed_count = sum(1 for r in collected_results if r.status == "failed")

        # "Define overall success based on whether at least one platform published successfully."
        overall_success = successful_count > 0
        overall_status = "success" if overall_success else "failed"

        logger.info(
            "Multi-platform dispatch completed: total=%d, succeeded=%d, failed=%d, overall_status=%s",
            len(collected_results),
            successful_count,
            failed_count,
            overall_status,
        )

        return ConsolidatedPostingResponse(
            message=message,
            cta_url=cta_url,
            character_count=character_count,
            status=overall_status,
            success=overall_success,
            total_platforms=len(collected_results),
            successful_count=successful_count,
            failed_count=failed_count,
            results=collected_results,
        )


_default_posting_service: Optional[PostingService] = None


def get_posting_service() -> PostingService:
    """Retrieve singleton instance of PostingService."""
    global _default_posting_service
    if _default_posting_service is None:
        _default_posting_service = PostingService()
    return _default_posting_service

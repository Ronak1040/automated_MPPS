"""
Consolidated application API routes.
Mounts root and versioned endpoints for health checks and multi-platform publishing.
"""

from fastapi import APIRouter, HTTPException, status

from app import __version__
from app.core.config import get_settings
from app.core.logging import get_logger
from app.schemas.request import PostingRequest
from app.schemas.response import HealthCheckResponse, PostingResponse
from app.services.posting_service import get_posting_service

logger = get_logger(__name__)

api_router = APIRouter()


@api_router.get(
    "/health",
    response_model=HealthCheckResponse,
    status_code=status.HTTP_200_OK,
    tags=["Health"],
    summary="Service Health Check",
    description="Verify service availability and runtime operational metadata.",
)
async def health_check() -> HealthCheckResponse:
    """Return health check and environment metadata."""
    settings = get_settings()
    return HealthCheckResponse(
        status="healthy",
        app_name=settings.app_name,
        environment=settings.app_env,
        version=__version__,
        details={
            "debug_mode": settings.debug,
            "port": settings.port,
        },
    )


@api_router.post(
    "/posting",
    response_model=PostingResponse,
    status_code=status.HTTP_200_OK,
    tags=["Posting"],
    summary="Publish Post to All Configured Platforms",
    description=(
        "Validates the incoming message (max 200 characters, exactly one valid HTTP/HTTPS CTA URL), "
        "automatically discovers all configured platform integrations via PlatformRegistry, "
        "and publishes the post concurrently with isolated fault tolerance."
    ),
    response_description="Consolidated publishing results including per-platform URLs and status.",
    responses={
        200: {
            "description": "Post processed and dispatched across platforms (supports partial success).",
            "content": {
                "application/json": {
                    "example": {
                        "success": True,
                        "status": "success",
                        "message": "Check out our latest business platform. https://example.com",
                        "cta_url": "https://example.com",
                        "character_count": 59,
                        "total_platforms": 5,
                        "successful_count": 3,
                        "failed_count": 2,
                        "results": [
                            {
                                "platform": "Telegram",
                                "status": "success",
                                "posted_url": "https://t.me/mychannel/123",
                                "content_id": "123",
                                "public_access": True,
                                "date": "2026-11-20",
                                "time": "12:00:00",
                                "error": None,
                            },
                            {
                                "platform": "Discord",
                                "status": "failed",
                                "posted_url": None,
                                "content_id": None,
                                "public_access": False,
                                "date": None,
                                "time": None,
                                "error": "Rate limit exceeded: Too Many Requests",
                            },
                        ],
                    }
                }
            },
        },
        422: {
            "description": "Validation error: missing message, exceeded 200 characters, missing or multiple URLs, or malformed URL syntax."
        },
        500: {
            "description": "Internal server error: unexpected failure during orchestration."
        },
    },
)
async def post_posting(payload: PostingRequest) -> PostingResponse:
    """
    Validate incoming request, invoke PostingService, and return consolidated platform results.
    """
    try:
        posting_service = get_posting_service()
        consolidated = await posting_service.publish(message=payload.message)

        return PostingResponse(
            success=consolidated.success,
            status=consolidated.status,
            message=consolidated.message,
            cta_url=consolidated.cta_url,
            character_count=consolidated.character_count,
            total_platforms=consolidated.total_platforms,
            successful_count=consolidated.successful_count,
            failed_count=consolidated.failed_count,
            results=consolidated.results,
        )
    except Exception as e:
        logger.exception("Unexpected error occurred while processing POST /posting")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An unexpected internal error occurred during post orchestration: {str(e)}",
        ) from e

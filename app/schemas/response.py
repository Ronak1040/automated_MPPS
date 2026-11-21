"""
Outbound response data transfer objects (DTOs) and standardized result models.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field


class HealthCheckResponse(BaseModel):
    """Standardized response schema for service health status."""

    status: str = Field(
        default="healthy",
        description="Current operational status of the service",
    )
    app_name: str = Field(
        description="Name of the running application service",
    )
    environment: str = Field(
        description="Active deployment environment mode",
    )
    version: str = Field(
        description="Application semver string",
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp of the health check evaluation",
    )
    details: Dict[str, Any] = Field(
        default_factory=dict,
        description="Additional diagnostic operational metadata",
    )


class PlatformPostingResult(BaseModel):
    """
    Standardized result model for a platform posting attempt.
    Complies with the required schema:
    {
      "platform": "Platform Name",
      "status": "success" | "failed",
      "posted_url": "https://..." | null,
      "public_access": true | false,
      "date": "YYYY-MM-DD" | null,
      "time": "HH:MM:SS" | null,
      "error": null | "error description"
    }
    """

    platform: str = Field(description="Display name of the target platform")
    status: Literal["success", "failed"] = Field(
        description="Outcome status of the publishing operation"
    )
    posted_url: Optional[str] = Field(
        default=None,
        description="Public URL of the newly created post if successful, null otherwise",
    )
    content_id: Optional[str] = Field(
        default=None,
        description="Native identifier of the post assigned by the platform",
    )
    public_access: bool = Field(
        default=False,
        description="Indicates whether the post is publicly accessible without authentication",
    )
    date: Optional[str] = Field(
        default=None,
        description="Date of publication in YYYY-MM-DD format (null on failure)",
    )
    time: Optional[str] = Field(
        default=None,
        description="Time of publication in HH:MM:SS format (null on failure)",
    )
    error: Optional[str] = Field(
        default=None,
        description="Human-readable diagnostic error message if the operation failed",
    )

    @classmethod
    def success(
        cls,
        platform: str,
        posted_url: str,
        public_access: bool = True,
        content_id: Optional[str] = None,
        dt: Optional[datetime] = None,
    ) -> "PlatformPostingResult":
        """Factory method to generate a standardized successful post result."""
        current_dt = dt or datetime.now(timezone.utc)
        return cls(
            platform=platform,
            status="success",
            posted_url=posted_url,
            content_id=content_id,
            public_access=public_access,
            date=current_dt.strftime("%Y-%m-%d"),
            time=current_dt.strftime("%H:%M:%S"),
            error=None,
        )

    @classmethod
    def failure(
        cls,
        platform: str,
        error: str,
        public_access: bool = False,
    ) -> "PlatformPostingResult":
        """Factory method to generate a standardized failed post result."""
        return cls(
            platform=platform,
            status="failed",
            posted_url=None,
            content_id=None,
            public_access=public_access,
            date=None,
            time=None,
            error=error,
        )


class PostingResponse(BaseModel):
    """Consolidated response returned upon post validation and multi-platform publishing."""

    message: str = Field(description="The verified post message.")
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

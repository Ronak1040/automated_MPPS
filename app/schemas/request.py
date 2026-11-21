"""
Inbound request data transfer objects (DTOs) with strict Pydantic v2 validation.
"""

from pydantic import BaseModel, Field, field_validator

from app.core.validators import extract_and_validate_cta_url


class PostingRequest(BaseModel):
    """
    Incoming request model for post creation with strict validation.
    Does not accept platform selection from the client.
    """

    message: str = Field(
        ...,
        description="The post message content containing exactly one valid HTTP/HTTPS CTA URL. Max 200 characters.",
    )

    @field_validator("message", mode="after")
    @classmethod
    def validate_message_rules(cls, v: str) -> str:
        """
        Validate all message constraints:
        1. Reject empty or whitespace-only messages.
        2. Reject messages exceeding 200 characters.
        3. Reject messages without a CTA URL or with multiple URLs.
        4. Validate that the URL uses only HTTP or HTTPS and is well-formed.
        5. Does not silently modify the user message.
        """
        if not v or not v.strip():
            raise ValueError("Message cannot be empty or contain only whitespace.")

        if len(v) > 200:
            raise ValueError(
                f"Message exceeds maximum allowed length of 200 characters (length: {len(v)})."
            )

        # Validation logic is decoupled into the core validator
        extract_and_validate_cta_url(v)

        # Return original, unmodified string
        return v

    @property
    def cta_url(self) -> str:
        """Extract the validated CTA URL from the message."""
        return extract_and_validate_cta_url(self.message)

    @property
    def character_count(self) -> int:
        """Return the character count of the message."""
        return len(self.message)

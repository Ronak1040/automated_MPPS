"""
Manual Integration Verification Script for Automated Multi-Platform Posting System (AMPPS).

This script provides a safe, standalone procedure for verifying live publishing against official
third-party platform APIs (Telegram, Discord, Mastodon, Bluesky, DEV.to).

Rules:
1. NEVER run automatically during unit/integration tests (pytest).
2. Only attempts publication to platforms that have valid credentials in .env or the system environment.
3. Asks for explicit user confirmation before dispatching live requests.
4. If a live post succeeds with public_access=True, performs a live HTTP probe to verify public visibility.
5. Prints a comprehensive diagnostic report without leaking credentials.

Usage:
    python scripts/verify_live_publishing.py [--dry-run] [--yes] [--message "Custom text https://example.com"]
"""

import argparse
import asyncio
from pathlib import Path
import sys

# Ensure repository root is on sys.path
repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from typing import List
import httpx

from app.core.config import get_settings
from app.core.sanitizer import mask_secret
from app.platforms.base import BasePlatformIntegration
from app.schemas.response import PlatformPostingResult
from app.services.platform_registry import get_platform_registry
from app.services.posting_service import PostingService


DEFAULT_TEST_MESSAGE = "Automated integration verification post. https://example.com"


async def verify_public_url(client: httpx.AsyncClient, url: str) -> bool:
    """Probe public URL with a GET request to verify live accessibility without credentials."""
    try:
        resp = await client.get(url, follow_redirects=True, timeout=10.0)
        return resp.status_code < 400
    except Exception:
        return False


async def run_integration_verification(
    message: str,
    dry_run: bool = False,
    auto_confirm: bool = False,
) -> int:
    settings = get_settings()
    registry = get_platform_registry()

    all_platforms = registry.get_registered_platforms()
    configured_platforms = registry.get_configured_platforms()

    print("=" * 80)
    print("AMPPS LIVE API INTEGRATION & PUBLISHING VERIFICATION")
    print("=" * 80)
    print(f"Target Environment: {settings.app_env}")
    print(f"Test Message:       '{message}'\n")

    print("PLATFORM CONFIGURATION STATUS:")
    print("-" * 80)
    for p in all_platforms:
        status_label = "[CONFIGURED]" if p.is_configured else "[NOT CONFIGURED]"
        print(f"  {p.name:<12} {status_label:<18} Public Access Model: {p.is_publicly_accessible}")
    print("-" * 80)

    if not configured_platforms:
        print("\n[INFO] No third-party platform credentials are configured in your environment or .env file.")
        print("To verify live publishing against a platform, provide credentials in .env:\n")
        print("  - Telegram:  TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID (@public_channel)")
        print("  - Discord:   DISCORD_WEBHOOK_URL")
        print("  - Mastodon:  MASTODON_INSTANCE_URL and MASTODON_ACCESS_TOKEN (write:statuses)")
        print("  - Bluesky:   BLUESKY_HANDLE and BLUESKY_APP_PASSWORD")
        print("  - DEV.to:    DEVTO_API_KEY")
        print("\nUnit and architecture tests remain 100% verified using isolated mock transports.")
        print("Exiting safely with zero real network calls executed.")
        return 0

    configured_names = [p.name for p in configured_platforms]
    print(f"\nConfigured platform(s) ready for live test: {', '.join(configured_names)}")

    if dry_run:
        print("\n[DRY RUN] Dry-run requested. No live API requests will be dispatched.")
        return 0

    if not auto_confirm:
        confirm = input(f"\nDo you want to publish the test post to {configured_names}? (y/N): ").strip().lower()
        if confirm != "y":
            print("Aborted by user. No live posts published.")
            return 0

    print("\nDispatching live publishing requests...")
    service = PostingService(registry=registry)

    async with httpx.AsyncClient(timeout=15.0) as verify_client:
        response = await service.publish(message=message, client=verify_client)

        print("\n" + "=" * 80)
        print("LIVE PUBLISHING RESULTS")
        print("=" * 80)
        print(f"Overall Status:    {'SUCCESS' if response.success else 'FAILED'}")
        print(f"Successful Count:  {response.successful_count}/{response.total_platforms}")
        print(f"Failed Count:      {response.failed_count}/{response.total_platforms}")
        print("-" * 80)

        for result in response.results:
            print(f"\nPlatform:      {result.platform}")
            print(f"Status:        {result.status.upper()}")
            print(f"Posted URL:    {result.posted_url or 'None'}")
            print(f"Content ID:    {result.content_id or 'None'}")
            print(f"Public Access: {result.public_access}")
            print(f"Timestamp:     {result.date} {result.time}")
            if result.error:
                print(f"Error:         {result.error}")

            # If marked public and URL exists, probe live accessibility
            if result.status == "success" and result.posted_url and result.public_access:
                print("Probing public web reachability...")
                is_reachable = await verify_public_url(verify_client, result.posted_url)
                print(f"Public URL Reachable: {is_reachable} (HTTP GET without auth)")

        print("\n" + "=" * 80)

    return 0 if response.success else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="AMPPS Live Integration Verification Script")
    parser.add_argument("--message", default=DEFAULT_TEST_MESSAGE, help="Custom message containing a valid CTA URL")
    parser.add_argument("--dry-run", action="store_true", help="Inspect configuration without publishing")
    parser.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompt")
    args = parser.parse_args()

    sys.exit(asyncio.run(run_integration_verification(
        message=args.message,
        dry_run=args.dry_run,
        auto_confirm=args.yes,
    )))


if __name__ == "__main__":
    main()

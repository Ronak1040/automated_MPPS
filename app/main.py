from contextlib import asynccontextmanager
from typing import AsyncGenerator
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api.routes import api_router
from app.core.config import get_settings
from app.core.logging import get_logger, setup_logging

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan manager for startup and shutdown events."""
    setup_logging()
    settings = get_settings()
    logger.info(
        "Starting %s [env=%s, debug=%s, port=%s]",
        settings.app_name,
        settings.app_env,
        settings.debug,
        settings.port,
    )
    yield
    logger.info("Shutting down %s", settings.app_name)


def create_application() -> FastAPI:
    """Application factory for configuring and instantiating the FastAPI application."""
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        description=(
            "Backend engine for automated, resilient, multi-platform post publishing "
            "across Telegram, Discord, Mastodon, Bluesky, Dev.to, and more."
        ),
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    # Cross-Origin Resource Sharing (CORS) Middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Register consolidated API routes at root (/health, /posting)
    app.include_router(api_router)

    # Also register at versioned prefix (/api/v1/health, /api/v1/posting) for backward compatibility
    app.include_router(api_router, prefix=settings.api_v1_prefix)

    return app


app = create_application()

if __name__ == "__main__":
    current_settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=current_settings.host,
        port=current_settings.port,
        reload=current_settings.debug,
    )

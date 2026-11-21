from typing import AsyncGenerator, Generator
import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from app.main import app


@pytest.fixture
async def async_client() -> AsyncGenerator[AsyncClient, None]:
    """Async test client fixture connected to the FastAPI application."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest.fixture
def test_client() -> Generator[TestClient, None, None]:
    """Synchronous FastAPI TestClient fixture for endpoint verification."""
    with TestClient(app) as client:
        yield client

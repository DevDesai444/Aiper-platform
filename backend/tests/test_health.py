"""Smoke test: health endpoint responds 200 with no database running.

Uses httpx's ASGI transport directly WITHOUT entering the client context manager,
which means the lifespan (and therefore init_db / Qdrant connect) is never
triggered.  This lets the test pass in environments where Postgres/Qdrant are
absent — exactly the case for the CI backend job before other lanes add DB tests.
"""

import httpx
import pytest

from app.main import app


@pytest.mark.asyncio
async def test_health_returns_200_without_db() -> None:
    """GET /health → 200 ok, no DB connection required."""
    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://test")
    try:
        response = await client.get("/health")
    finally:
        await client.aclose()

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "backend"
    assert "mock" in body

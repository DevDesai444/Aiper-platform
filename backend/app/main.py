"""Application entrypoint."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.config import settings
from app.core.startup import ensure_auth_configured
from app.db.base import init_db
from app.rag.store import ensure_collection
from app.services.seed import seed_templates


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Fail closed: refuse to serve unless an authentication mode is configured
    # (Supabase for production, or the legacy dev/demo login flag).
    ensure_auth_configured()

    await init_db()
    await seed_templates()
    try:
        await ensure_collection()
    except Exception:  # noqa: BLE001 - Qdrant is retried on first use
        pass
    yield


app = FastAPI(
    title="aiper",
    description="Agentic document generation and compliance analysis for the space sector.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)


@app.get("/health", tags=["meta"])
async def health() -> dict:
    return {
        "status": "ok",
        "service": "backend",
        "mock": False,
        "azure_configured": settings.azure_configured,
    }

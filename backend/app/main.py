"""Application entrypoint."""

import logging
import traceback
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.router import api_router
from app.config import settings
from app.core.body_limit import BodySizeLimitMiddleware
from app.core.logging_config import configure_logging
from app.core.request_id import RequestIDMiddleware
from app.core.startup import ensure_auth_configured
from app.db.base import verify_schema
from app.rag.store import ensure_collection
from app.services.seed import seed_templates

# As early as an import-time call can run: before the app object exists, the
# lifespan runs, or any request is served, so every record this process
# emits from here on — ours and uvicorn's — is one redacted JSON line.
configure_logging()

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Fail closed: refuse to serve unless an authentication mode is configured
    # (Supabase for production, or the legacy dev/demo login flag).
    ensure_auth_configured()

    settings.check_production_secrets()
    # The schema belongs to Alembic; the entrypoint migrates before we serve.
    await verify_schema()
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

# Ceiling on any request body: the largest legal upload plus multipart
# framing. Added before CORS so CORS wraps it and a 413 still carries the
# headers a browser needs to read it.
app.add_middleware(
    BodySizeLimitMiddleware, max_bytes=(settings.max_upload_mb + 1) * 1024 * 1024
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# Added last so it is outermost (Starlette wraps in reverse add_middleware
# order): the request id is live before CORS or the body cap run, and the
# access log sees the true final status code, including a CORS rejection or
# a 413.
app.add_middleware(RequestIDMiddleware)

app.include_router(api_router)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error(
        "Unhandled exception on %s %s: %s",
        request.method,
        request.url.path,
        traceback.format_exc(),
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )


@app.get("/health", tags=["meta"])
async def health() -> dict:
    return {
        "status": "ok",
        "service": "backend",
        "mock": False,
        "azure_configured": settings.azure_configured,
    }

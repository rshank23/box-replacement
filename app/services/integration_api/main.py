from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.libs.common.config import get_settings
from app.libs.common.db import get_engine
from app.libs.common.logging_config import bind_correlation_id, configure_logging, get_logger
from app.libs.common.vault_factory import close_vault_client

from .routers import admin, audit, dashboard, failures, mappings, mbox, poc, transfers

settings = get_settings()
configure_logging(settings.log_level, service="integration-api")
log = get_logger("integration-api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("api_startup", vault_client=settings.vault_client, mbox_root=settings.mbox_root)
    yield
    await close_vault_client()
    log.info("api_shutdown")


app = FastAPI(
    title="MBox -> VTMF Integration API",
    version="1.0.0",
    description="Post-study archival of clinical trial documents from MBox into Veeva Vault TMF.",
    lifespan=lifespan,
)

if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-Correlation-Id"],
    )


@app.middleware("http")
async def correlation_middleware(request: Request, call_next):
    header = request.headers.get("X-Correlation-Id")
    try:
        cid = uuid.UUID(header) if header else uuid.uuid4()
    except ValueError:
        cid = uuid.uuid4()
    bind_correlation_id(cid)
    response = await call_next(request)
    response.headers["X-Correlation-Id"] = str(cid)
    return response


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Never leak internals to the caller; the detail stays in the structured log."""
    log.error("unhandled_exception", path=request.url.path, error=str(exc), exc_info=True)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


app.include_router(mbox.router, prefix="/api/mbox", tags=["mbox"])
app.include_router(transfers.router, prefix="/api/transfers", tags=["transfers"])
app.include_router(failures.router, prefix="/api/failures", tags=["failures"])
app.include_router(mappings.router, prefix="/api/mappings", tags=["mappings"])
app.include_router(audit.router, prefix="/api/audit", tags=["audit"])
app.include_router(dashboard.router, prefix="/api/dashboard", tags=["dashboard"])
app.include_router(admin.router, prefix="/api/admin", tags=["admin"])
app.include_router(poc.router, prefix="/api/poc", tags=["poc"])


@app.get("/health", tags=["ops"], summary="Liveness probe")
async def health() -> dict:
    return {"status": "ok", "service": "integration-api", "time": datetime.now(UTC).isoformat()}


@app.get("/health/ready", tags=["ops"], summary="Readiness probe (checks the database)")
async def readiness() -> JSONResponse:
    try:
        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:
        log.error("readiness_failed", error=str(exc))
        return JSONResponse(status_code=503, content={"status": "unavailable", "dependency": "database"})
    return JSONResponse(status_code=200, content={"status": "ready"})


if settings.prometheus_enabled:
    from prometheus_fastapi_instrumentator import Instrumentator

    Instrumentator(excluded_handlers=["/metrics", "/health", "/health/ready"]).instrument(app).expose(
        app, endpoint="/metrics", include_in_schema=False
    )

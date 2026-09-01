"""FastAPI entrypoint. Agent A owns routing beyond /api/health."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import auth, db, scheduler
from app.api.auth_router import router as auth_router
from app.api.router import router as api_router
from app.config import settings
from app.models import ConnectionCreate
from app.snapshots import store

log = logging.getLogger("vcf_doctor")


def ensure_demo_connection() -> None:
    """Demo mode: one fixture connection exists and has been scanned once."""
    if not settings.demo_mode or store.list_connections():
        return
    conn = store.create_connection(
        ConnectionCreate(
            name="Demo Workload Domain",
            host="fixture",
            username="demo",
            password="",
            kind="fixture",
            interval_minutes=15,
        )
    )
    scheduler.run_scan(conn.id, "manual", label="Initial demo capture")


@asynccontextmanager
async def lifespan(application: FastAPI):
    db.connect()
    auth.bootstrap_from_env()
    ensure_demo_connection()
    scheduler.start()
    try:
        yield
    finally:
        scheduler.shutdown()


app = FastAPI(
    title="VCF Doctor",
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)


@app.middleware("http")
async def require_session(request: Request, call_next):
    if auth.requires_auth(request.url.path) and not auth.is_authenticated(request):
        return JSONResponse({"detail": "authentication required"}, status_code=401)
    return await call_next(request)


app.include_router(auth_router)


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "demo_mode": settings.demo_mode,
        "version": app.version,
        "scheduler": scheduler.running(),
    }


app.include_router(api_router)

try:
    from app.assistant.router import router as assistant_router

    app.include_router(assistant_router, prefix="/api/assistant")
except ImportError as exc:  # Agent E not landed yet
    log.warning("assistant router not mounted: %s", exc)


def mount_frontend(application: FastAPI) -> None:
    static = Path(settings.static_dir) if settings.static_dir else None
    if not static or not (static / "index.html").exists():
        return
    application.mount("/assets", StaticFiles(directory=static / "assets"), name="assets")

    root = static.resolve()

    @application.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str) -> FileResponse:
        if full_path.startswith("api/"):
            raise HTTPException(404, "not found")
        candidate = (root / full_path).resolve()
        if full_path and candidate.is_relative_to(root) and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(root / "index.html")


mount_frontend(app)

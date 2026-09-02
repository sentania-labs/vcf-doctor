"""FastAPI entrypoint. Agent A owns routing beyond /api/health."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import auth, db, scheduler
from app.api.auth_router import router as auth_router
from app.api.events_router import router as events_router
from app.api.router import router as api_router
from app.config import settings
from app.snapshots import store

log = logging.getLogger("vcf_doctor")


@asynccontextmanager
async def lifespan(application: FastAPI):
    db.connect()
    interrupted = store.reconcile_interrupted_runs()
    if interrupted:
        log.warning("marked %d interrupted scan run(s) as error", interrupted)
    auth.bootstrap_from_env()
    scheduler.startup_maintenance()
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


# The built UI loads only same-origin scripts, styles, images and API calls;
# style-src keeps 'unsafe-inline' because React and Tailwind set inline
# style attributes. Anything not listed here is blocked by the browser.
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "font-src 'self' data:; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Content-Security-Policy": CONTENT_SECURITY_POLICY,
}
HSTS = "max-age=31536000"


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """Registered after require_session so it is the outer layer: the 401
    responses above, and an unhandled-exception 500, carry the same headers
    as everything else. call_next re-raises framework exceptions instead of
    returning a response, so those are caught here rather than left to
    Starlette's outer ServerErrorMiddleware, which never sees this layer."""
    try:
        response = await call_next(request)
    except Exception:
        log.exception("unhandled error while serving %s", request.url.path)
        response = JSONResponse({"detail": "internal server error"}, status_code=500)
    for name, value in SECURITY_HEADERS.items():
        response.headers[name] = value
    if request.url.path.startswith("/api/"):
        # API bodies hold inventory and credentials metadata; never let a
        # shared browser or proxy keep a copy.
        response.headers["Cache-Control"] = "no-store"
    if request.url.scheme == "https":
        # uvicorn runs with --proxy-headers, so the scheme reflects the
        # ingress's X-Forwarded-Proto. Plain-http deployments never see HSTS,
        # which would otherwise lock a browser out of them for a year.
        response.headers["Strict-Transport-Security"] = HSTS
    return response


app.include_router(auth_router)


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "version": app.version,
        "scheduler": scheduler.running(),
    }


app.include_router(api_router)
app.include_router(events_router)

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

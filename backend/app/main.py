"""FastAPI entrypoint, middleware, and router registration."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import auth, db, migrate, proxies, scheduler, vault
from app._version import BUILD_INFO
from app.api.auth_router import router as auth_router
from app.api.encryption_router import router as encryption_router
from app.api.environment_router import router as environment_router
from app.api.events_router import router as events_router
from app.api.findings_related import router as findings_related_router
from app.api.health_score_router import router as health_score_router
from app.api.proxies_router import router as proxies_router
from app.api.router import router as api_router
from app.config import settings
from app.snapshots import store

log = logging.getLogger("vcf_doctor")


@asynccontextmanager
async def lifespan(application: FastAPI):
    # An unreachable database must not crash-loop the container: a PostgreSQL
    # failover is exactly when the console should still come up and say the
    # database is unavailable. Everything below tolerates that and logs it.
    try:
        applied = migrate.upgrade()
        if applied:
            log.info("applied %d schema migration(s): %s", len(applied), ", ".join(applied))
    except Exception:
        log.exception("startup: schema migration failed; the database is not usable yet")
    try:
        # Rotation first: a secret still under the previous key must move to
        # the current one before migrate_plaintext can judge what is plaintext.
        vault.rekey_at_startup()
    except Exception:
        log.exception("startup: encryption key rotation failed; stored secrets are unchanged")
    try:
        vault.migrate_plaintext()
    except Exception:
        log.exception("startup: secret migration failed; plaintext rows are still readable")
    try:
        for cid, start in store.backfill_log_since().items():
            log.info("change log coverage for %s starts %s", cid, start.isoformat())
        interrupted = store.reconcile_interrupted_runs()
        if interrupted:
            log.warning("marked %d interrupted scan run(s) as error", interrupted)
    except Exception:
        log.exception("startup: recovering change log coverage and scan runs failed")
    try:
        auth.bootstrap_from_env()
    except Exception:
        log.exception("startup: seeding the operator password failed")
    try:
        scheduler.startup_maintenance()
        scheduler.start()
    except Exception:
        log.exception("startup: the scheduler did not start; scheduled scans are not running")
    try:
        yield
    finally:
        scheduler.shutdown()
        db.close()


app = FastAPI(
    title="VCF Doctor",
    version=BUILD_INFO.version,
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
        # The scheme reflects X-Forwarded-Proto only when it came from a
        # trusted proxy (app/proxies.py). Plain-http deployments never see
        # HSTS, which would otherwise lock a browser out of them for a year.
        response.headers["Strict-Transport-Security"] = HSTS
    return response


# Outermost layer: decides what "client address" and "scheme" mean before
# anything above looks at them. Registered last so it wraps the http
# middlewares defined above.
app.add_middleware(proxies.ForwardedHeadersMiddleware)

app.include_router(auth_router)


@app.exception_handler(vault.KeyUnavailable)
async def key_unavailable(request: Request, exc: vault.KeyUnavailable):
    """Saving a secret with no usable encryption key is refused, not crashed."""
    return JSONResponse({"detail": str(exc)}, status_code=503)


# Liveness and readiness are different questions and they are answered
# separately, because they lead to opposite actions.
#
# Liveness: is this process alive. It touches nothing external, so it stays
# green while PostgreSQL is unreachable. Restarting a console whose database is
# down fixes nothing and a restart loop makes the outage worse.
#
# Readiness: can this instance actually serve. It goes red the moment the
# database is unreachable or a migration is pending, so an orchestrator takes
# the pod out of rotation instead of routing people to pages that cannot work.
# Sign-in and everything behind it need the database, so "reachable API, dead
# database" is not a state to send traffic to.
#
# /api/health is the older name and keeps answering the older question,
# liveness, so a manifest that has not been repointed yet behaves as it always
# has instead of restart-looping through an outage.


def _readiness() -> tuple[dict, int]:
    database, detail = db.healthy()
    if detail:
        # Server-side only. The diagnostic comes from libpq or names the
        # configured secret path, and readiness needs no session, so the public
        # body says whether this instance can serve and nothing more.
        log.warning("readiness: the database is not usable: %s", detail)
    body = {
        "status": "ok" if database else "degraded",
        "version": app.version,
        "scheduler": scheduler.running(),
        "database": database,
    }
    return body, 200 if database else 503


@app.get("/api/health/live")
def health_live() -> dict:
    """Public liveness. 200 while the process is answering, whatever the
    database is doing. This is what the container HEALTHCHECK and a Kubernetes
    livenessProbe should use."""
    return {"status": "ok", "version": app.version}


@app.get("/api/health/ready")
def health_ready() -> JSONResponse:
    """Public readiness. 503 while the database is unreachable or a migration
    is pending. This is what a Kubernetes readinessProbe should use."""
    body, status = _readiness()
    return JSONResponse(body, status_code=status)


@app.get("/api/health")
def health() -> dict:
    """Public. The liveness answer under the name the deployment contract has
    always used, unchanged: 200 while the process is answering, whatever the
    database is doing. The manifest lives in another repository and cannot be
    repointed in lockstep with this change, so this path keeps its old meaning
    rather than turning a database outage into a restart loop. Readiness is
    /api/health/ready."""
    return {"status": "ok", "version": app.version, "scheduler": scheduler.running()}


@app.get("/api/version")
def version() -> dict[str, str]:
    return BUILD_INFO.as_dict()


app.include_router(api_router)
app.include_router(events_router)
app.include_router(health_score_router)
app.include_router(environment_router)
app.include_router(encryption_router)
app.include_router(findings_related_router)
app.include_router(proxies_router)

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

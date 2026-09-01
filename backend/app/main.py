"""FastAPI entrypoint. Agent A owns routing beyond /api/health."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings

app = FastAPI(title="VCF Doctor", version="0.1.0")


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "demo_mode": settings.demo_mode, "version": app.version}


def mount_frontend(application: FastAPI) -> None:
    static = Path(settings.static_dir) if settings.static_dir else None
    if not static or not (static / "index.html").exists():
        return
    application.mount("/assets", StaticFiles(directory=static / "assets"), name="assets")

    @application.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str) -> FileResponse:
        candidate = static / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(static / "index.html")


mount_frontend(app)

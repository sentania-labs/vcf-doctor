"""Assistant HTTP surface. Agent A mounts this at /api/assistant.

POST ""        AssistantRequest -> SSE stream (delta / done / error events)
GET  /status   AssistantStatus
GET  /settings AssistantSettings (never includes the key)
PUT  /settings partial AssistantSettings plus optional "api_key"
POST /test     tiny live call -> {ok, message}
"""

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import ValidationError
from sse_starlette.sse import EventSourceResponse

from app.assistant import settings as assistant_settings
from app.assistant.base import ProviderUnavailable
from app.assistant.prompt import evidence_counts
from app.models import AssistantRequest, AssistantSettings, AssistantStatus

log = logging.getLogger(__name__)

router = APIRouter()

DISABLED_REASON = "Assistant is disabled in Settings."


async def _status() -> AssistantStatus:
    s = assistant_settings.get_settings()
    provider = assistant_settings.get_provider()
    if not s.enabled:
        return AssistantStatus(
            available=False, provider=provider.name, model=provider.model, reason=DISABLED_REASON
        )
    return await provider.status()


@router.get("/status", response_model=AssistantStatus)
async def status() -> AssistantStatus:
    return await _status()


@router.get("/settings", response_model=AssistantSettings)
async def read_settings() -> AssistantSettings:
    return assistant_settings.get_settings()


@router.put("/settings", response_model=AssistantSettings)
async def write_settings(request: Request) -> AssistantSettings:
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="Body must be a JSON object.")
    try:
        return assistant_settings.update_settings(payload)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=e.errors()) from e


@router.post("/test")
async def test_connection() -> dict[str, Any]:
    st = await _status()
    if not st.available:
        return {"ok": False, "message": st.reason or "Assistant unavailable."}
    provider = assistant_settings.get_provider()
    ok, message = await provider.ping()  # both providers implement ping
    return {"ok": ok, "message": message}


def _event(name: str, data: dict[str, Any]) -> dict[str, str]:
    return {"event": name, "data": json.dumps(data)}


async def _generate(request: AssistantRequest) -> AsyncIterator[dict[str, str]]:
    counts = evidence_counts(request)
    st = await _status()
    if not st.available:
        yield _event("error", {"message": st.reason or "Assistant unavailable."})
        return
    provider = assistant_settings.get_provider()
    try:
        async for text in provider.stream(request):
            if text:
                yield _event("delta", {"text": text})
    except ProviderUnavailable as e:
        yield _event("error", {"message": str(e)})
        return
    except Exception:  # never leak internals (or a key) to the browser
        log.exception("assistant stream failed")
        yield _event("error", {"message": "Assistant request failed. See server logs."})
        return
    stop = getattr(provider, "last_stop_reason", None) or "end_turn"
    yield _event("done", {"stop_reason": stop, "evidence": counts})


@router.post("")
async def ask(request: AssistantRequest) -> EventSourceResponse:
    return EventSourceResponse(_generate(request), ping=15)

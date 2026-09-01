"""Model list for the Settings pulldown."""

import logging

import anthropic

from app.assistant import settings as assistant_settings

log = logging.getLogger(__name__)

# Curated fallback, most capable first. Fable 5 is org-gated so it is not listed
# by default; it still works if typed or returned by the live Models API.
CURATED = [
    {"id": "claude-opus-5", "display_name": "Claude Opus 5", "recommended": True},
    {"id": "claude-sonnet-5", "display_name": "Claude Sonnet 5", "recommended": False},
    {"id": "claude-opus-4-8", "display_name": "Claude Opus 4.8", "recommended": False},
    {"id": "claude-opus-4-7", "display_name": "Claude Opus 4.7", "recommended": False},
    {"id": "claude-sonnet-4-6", "display_name": "Claude Sonnet 4.6", "recommended": False},
    {"id": "claude-haiku-4-5", "display_name": "Claude Haiku 4.5", "recommended": False},
]


async def list_models() -> tuple[list[dict], str]:
    key = assistant_settings.resolve_api_key()
    if not key:
        return [dict(m) for m in CURATED], "curated"
    try:
        client = anthropic.AsyncAnthropic(api_key=key)
        seen: list[dict] = []
        async for m in client.models.list():
            seen.append(
                {
                    "id": m.id,
                    "display_name": getattr(m, "display_name", None) or m.id,
                    "recommended": m.id == "claude-opus-5",
                }
            )
        if seen:
            # Newest first as returned; keep recommended on top.
            seen.sort(key=lambda m: not m["recommended"])
            return seen, "live"
    except Exception as exc:  # noqa: BLE001  fall back rather than break Settings
        log.warning("assistant: could not list models live (%s); using curated list", exc)
    return [dict(m) for m in CURATED], "curated"

"""Assistant settings: GUI-editable, stored in SQLite via db.get_setting.

Key resolution: the ANTHROPIC_API_KEY environment variable wins, then the
"assistant_api_key" setting row. The key is stored but never returned;
AssistantSettings.api_key_set reflects whether one resolves.
"""

import os
from typing import Any

from app import db
from app.assistant.base import LLMProvider
from app.assistant.providers import AnthropicProvider, MockProvider
from app.config import settings as cfg
from app.models import AssistantSettings

SETTINGS_KEY = "assistant"
API_KEY_KEY = "assistant_api_key"
ENV_KEY = "ANTHROPIC_API_KEY"

_PERSISTED_FIELDS = ("enabled", "provider", "model")


def resolve_api_key() -> str | None:
    """A key entered in Settings wins; ANTHROPIC_API_KEY is the deployment default."""
    stored = db.get_setting(API_KEY_KEY)
    if isinstance(stored, str) and stored.strip():
        return stored.strip()
    env = os.environ.get(ENV_KEY, "").strip()
    return env or None


def get_settings() -> AssistantSettings:
    raw = db.get_setting(SETTINGS_KEY) or {}
    merged: dict[str, Any] = {"model": cfg.llm_model}
    merged.update({k: v for k, v in raw.items() if k in _PERSISTED_FIELDS})
    merged["api_key_set"] = resolve_api_key() is not None
    return AssistantSettings(**merged)


def update_settings(payload: dict[str, Any]) -> AssistantSettings:
    """Apply a partial update. "api_key" is stored separately and never echoed."""
    payload = dict(payload)
    if "api_key" in payload:
        key = payload.pop("api_key")
        if key is None or str(key).strip() == "":
            db.set_setting(API_KEY_KEY, "")
        else:
            db.set_setting(API_KEY_KEY, str(key).strip())
    payload.pop("api_key_set", None)

    current = get_settings().model_dump()
    current.update({k: v for k, v in payload.items() if k in _PERSISTED_FIELDS})
    validated = AssistantSettings(**current)  # raises on bad provider/model types
    db.set_setting(SETTINGS_KEY, {k: getattr(validated, k) for k in _PERSISTED_FIELDS})
    return get_settings()


def get_provider() -> LLMProvider:
    """Pick the provider. Mock is used only when selected or in demo mode with no key."""
    s = get_settings()
    if s.provider == "mock":
        return MockProvider()
    key = resolve_api_key()
    if key is None and cfg.demo_mode:
        return MockProvider()
    return AnthropicProvider(model=s.model, api_key=key)

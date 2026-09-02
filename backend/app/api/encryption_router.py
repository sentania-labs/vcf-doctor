"""Encryption status for the Settings page. Reports which key source is
active and what cannot be read; never the key itself."""

from fastapi import APIRouter
from pydantic import BaseModel

from app import vault
from app.assistant import settings as assistant_settings
from app.snapshots import store

router = APIRouter(prefix="/api")


class EncryptionStatus(BaseModel):
    enabled: bool = True
    key_source: str  # "env" or "file"
    key_env_var: str = vault.ENV_KEY
    key_file: str | None = None  # path only; the key itself is never returned
    # Set when no usable key exists (corrupt or unreadable key file, malformed
    # env value). Reads degrade to "needs credentials"; saving secrets fails.
    key_error: str | None = None
    unreadable_connections: list[str]  # connection ids needing a re-entered password
    assistant_key_unreadable: bool
    # The stored assistant key is unreadable but ANTHROPIC_API_KEY covers for it.
    assistant_env_fallback: bool = False


@router.get("/settings/encryption", response_model=EncryptionStatus)
def get_encryption_status():
    source = vault.key_source()
    unreadable = assistant_settings.stored_key_unreadable()
    return EncryptionStatus(
        key_source=source,
        key_file=str(vault.key_file_path()) if source == "file" else None,
        key_error=vault.key_error(),
        unreadable_connections=[c.id for c in store.list_connections() if c.credentials_unreadable],
        assistant_key_unreadable=unreadable,
        assistant_env_fallback=unreadable and assistant_settings.resolve_api_key() is not None,
    )

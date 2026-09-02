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
    unreadable_connections: list[str]  # connection ids needing a re-entered password
    assistant_key_unreadable: bool


@router.get("/settings/encryption", response_model=EncryptionStatus)
def get_encryption_status():
    source = vault.key_source()
    return EncryptionStatus(
        key_source=source,
        key_file=str(vault.key_file_path()) if source == "file" else None,
        unreadable_connections=[c.id for c in store.list_connections() if c.credentials_unreadable],
        assistant_key_unreadable=assistant_settings.stored_key_unreadable(),
    )

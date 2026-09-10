"""Encryption status for the Settings page, and the one-click rotation off a
generated key file a deployment left behind. Reports which key source is active
and what cannot be read; never the key itself, which the interface never
accepts either."""

from dataclasses import asdict

from fastapi import APIRouter
from pydantic import BaseModel

from app import vault
from app.assistant import settings as assistant_settings
from app.snapshots import store

router = APIRouter(prefix="/api")


class RekeyOutcome(BaseModel):
    """The result of a rotation. Carries no key material."""

    at: str
    source: str
    rewritten: int
    unreadable: int
    error: str | None = None
    # Composed once, by vault.RekeyOutcome, so the card never has to rebuild it.
    message: str


class EncryptionStatus(BaseModel):
    enabled: bool = True
    key_source: str  # "env" or "file"
    key_env_var: str = vault.ENV_KEY
    key_previous_env_var: str = vault.ENV_PREVIOUS_KEY
    key_file: str | None = None  # path only; the key itself is never returned
    # A generated key file still sitting on the volume while an env key
    # is active: the previous key, available for a one-click rotation. Path only.
    previous_key_file: str | None = None
    # Set when no usable key exists (corrupt or unreadable key file, malformed
    # env value). Reads degrade to "needs credentials"; saving secrets fails.
    key_error: str | None = None
    unreadable_connections: list[str]  # connection ids needing credential recovery
    assistant_key_unreadable: bool
    # The stored assistant key is unreadable but ANTHROPIC_API_KEY covers for it.
    assistant_env_fallback: bool = False
    # Outcome of the last rotation, whether it ran at startup or from Settings.
    last_rekey: RekeyOutcome | None = None


class RekeyResult(BaseModel):
    ok: bool
    message: str
    rewritten: int
    unreadable: int
    status: EncryptionStatus


def _unreadable() -> tuple[list[str], bool]:
    """Connection ids whose password the current key cannot open, and whether
    the stored assistant key is in the same state."""
    connections = [c.id for c in store.list_connections() if c.credentials_unreadable]
    return connections, assistant_settings.stored_key_unreadable()


def _status() -> EncryptionStatus:
    source = vault.key_source()
    connections, unreadable = _unreadable()
    last = vault.last_rekey()
    leftover = vault.previous_key_file()
    return EncryptionStatus(
        key_source=source,
        key_file=str(vault.key_file_path()) if source == "file" else None,
        previous_key_file=str(leftover) if leftover else None,
        key_error=vault.key_error(),
        unreadable_connections=connections,
        assistant_key_unreadable=unreadable,
        assistant_env_fallback=unreadable and assistant_settings.resolve_api_key() is not None,
        last_rekey=RekeyOutcome(**asdict(last), message=last.message) if last else None,
    )


@router.get("/settings/encryption", response_model=EncryptionStatus)
def get_encryption_status():
    return _status()


@router.post("/settings/encryption/rekey", response_model=RekeyResult)
def rekey():
    """Re-encrypt secrets left behind by a key rotation, without re-entering
    them, from the generated key file still on the volume. No key material
    reaches the browser, and a key that opens nothing leaves credentials
    untouched. Rotating from an arbitrary key is a deployment action:
    VCF_DOCTOR_SECRET_KEY_PREVIOUS at startup."""
    outcome = vault.rekey(vault.read_previous_key_file(), "the generated key file")
    return RekeyResult(
        ok=outcome.error is None,
        message=outcome.message,
        rewritten=outcome.rewritten,
        unreadable=outcome.unreadable,
        status=_status(),
    )

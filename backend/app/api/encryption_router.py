"""Encryption status for the Settings page, and the in-place key rotation
behind its Rotate control. Reports which key source is active and what cannot
be read; never the key itself."""

from dataclasses import asdict

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app import auth, proxies, vault
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


class EncryptionStatus(BaseModel):
    enabled: bool = True
    key_source: str  # "env" or "file"
    key_env_var: str = vault.ENV_KEY
    key_previous_env_var: str = vault.ENV_PREVIOUS_KEY
    key_file: str | None = None  # path only; the key itself is never returned
    # A generated key file still sitting next to the database while an env key
    # is active: the previous key, available for a one-click rotation. Path only.
    previous_key_file: str | None = None
    # Set when no usable key exists (corrupt or unreadable key file, malformed
    # env value). Reads degrade to "needs credentials"; saving secrets fails.
    key_error: str | None = None
    unreadable_connections: list[str]  # connection ids needing a re-entered password
    assistant_key_unreadable: bool
    # The stored assistant key is unreadable but ANTHROPIC_API_KEY covers for it.
    assistant_env_fallback: bool = False
    # Outcome of the last rotation, whether it ran at startup or from Settings.
    last_rekey: RekeyOutcome | None = None


class RekeyBody(BaseModel):
    # The key stored secrets were last encrypted under. Used once and never
    # written anywhere: only the secrets it opens are rewritten.
    previous_key: str = ""
    # Instead of a pasted key, rotate from the generated key file still on the
    # volume, so moving to a sealed secret needs no key material in the browser.
    use_key_file: bool = False


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
        last_rekey=RekeyOutcome(**asdict(last)) if last else None,
    )


@router.get("/settings/encryption", response_model=EncryptionStatus)
def get_encryption_status():
    return _status()


@router.post("/settings/encryption/rekey", response_model=RekeyResult)
def rekey(body: RekeyBody, request: Request):
    """Re-encrypt secrets left behind by a key rotation, without re-entering
    them. A pasted key is a password check like any other, so it shares the
    login backoff; a key that opens nothing changes nothing."""
    pasted = body.previous_key.strip()
    if body.use_key_file and pasted:
        raise HTTPException(400, "supply either the previous key or the key file, not both")
    if not body.use_key_file and not pasted:
        raise HTTPException(400, "enter the previous encryption key")
    nothing_to_do = "Nothing to do: every stored secret already opens with the current key."
    if body.use_key_file:
        # No key material was guessed, so this attempt is not rate limited.
        previous, source = vault.read_previous_key_file(), "the generated key file"
        ip, stamp = None, 0.0
    else:
        # With nothing to open, no key can prove itself right or wrong, so the
        # pasted value is not checked at all and the limiter is left untouched.
        connections, assistant_unreadable = _unreadable()
        if not connections and not assistant_unreadable:
            return RekeyResult(
                ok=True, message=nothing_to_do, rewritten=0, unreadable=0, status=_status()
            )
        previous, source = pasted, "the Settings page"
        ip = proxies.client_ip(request)
        wait, stamp = auth.begin_attempt(ip)
        if wait:
            return auth.too_many_response(wait)
    try:
        outcome = vault.rekey(previous, source)
    except vault.KeyUnavailable:
        if ip:
            auth.finish_attempt(ip, stamp, True)  # not a wrong-key attempt
        raise
    # Only a key that opened at least one secret was the right key; anything
    # else counts against the limiter like a wrong password.
    if ip:
        auth.finish_attempt(ip, stamp, outcome.rewritten > 0)
        if not outcome.rewritten:
            # Same as the login page: say now that the next try would be
            # refused, so the card can start its countdown without a request.
            wait = auth.login_blocked(ip)
            if wait:
                return auth.too_many_response(wait)
    if outcome.rewritten:
        moved = (
            f"Re-encrypted {outcome.rewritten} stored secret"
            f"{'' if outcome.rewritten == 1 else 's'} under the current key."
        )
        message = f"{moved} {outcome.error}" if outcome.error else moved
    elif outcome.error:
        message = outcome.error
    else:
        message = nothing_to_do
    return RekeyResult(
        ok=outcome.error is None,
        message=message,
        rewritten=outcome.rewritten,
        unreadable=outcome.unreadable,
        status=_status(),
    )

"""Single shared operator password with a signed session cookie.

Deliberately simple: one password, hashed with PBKDF2 and stored in the
settings table; sessions are HMAC-signed timestamps in an HttpOnly cookie.
The signing secret is generated once and kept in the settings table, so it
survives restarts on the /data volume. VCF_DOCTOR_AUTH=off disables all of
it for deployments that front the app with ingress authentication.
"""

import base64
import hashlib
import hmac
import os
import secrets
import time

from fastapi import HTTPException, Request

from app import db
from app.config import settings

COOKIE = "vcfdoctor_session"
SESSION_TTL = 7 * 24 * 3600
MIN_PASSWORD = 8
_HASH_KEY = "auth_password_hash"
_SECRET_KEY = "auth_session_secret"
_ITER = 200_000


def enabled() -> bool:
    return settings.auth.lower() not in ("off", "false", "0", "disabled")


def configured() -> bool:
    return bool(db.get_setting(_HASH_KEY))


def _hash(password: str, salt: bytes) -> str:
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITER)
    return f"pbkdf2${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def set_password(password: str) -> None:
    if len(password) < MIN_PASSWORD:
        raise HTTPException(400, f"password must be at least {MIN_PASSWORD} characters")
    db.set_setting(_HASH_KEY, _hash(password, secrets.token_bytes(16)))


def verify_password(password: str) -> bool:
    stored = db.get_setting(_HASH_KEY)
    if not stored:
        return False
    try:
        _, salt_b64, _ = stored.split("$", 2)
    except ValueError:
        return False
    candidate = _hash(password, base64.b64decode(salt_b64))
    return hmac.compare_digest(candidate, stored)


def bootstrap_from_env() -> None:
    """VCF_DOCTOR_ADMIN_PASSWORD seeds the password on first boot only."""
    if configured():
        return
    seed = os.environ.get("VCF_DOCTOR_ADMIN_PASSWORD", "")
    if len(seed) >= MIN_PASSWORD:
        set_password(seed)


def _secret() -> bytes:
    s = db.get_setting(_SECRET_KEY)
    if not s:
        s = secrets.token_hex(32)
        db.set_setting(_SECRET_KEY, s)
    return s.encode()


def issue_token() -> str:
    payload = str(int(time.time())).encode()
    sig = hmac.new(_secret(), payload, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(payload + b"." + sig).decode()


def token_valid(token: str | None) -> bool:
    if not token:
        return False
    try:
        raw = base64.urlsafe_b64decode(token.encode())
        payload, sig = raw.rsplit(b".", 1)
        issued = int(payload)
    except (ValueError, TypeError):
        return False
    expected = hmac.new(_secret(), payload, hashlib.sha256).digest()
    if not hmac.compare_digest(sig, expected):
        return False
    return time.time() - issued < SESSION_TTL


def is_authenticated(request: Request) -> bool:
    if not enabled():
        return True
    return token_valid(request.cookies.get(COOKIE))


# Paths under /api that never require a session.
PUBLIC_PREFIXES = ("/api/health", "/api/auth/")


def requires_auth(path: str) -> bool:
    return path.startswith("/api/") and not path.startswith(PUBLIC_PREFIXES)

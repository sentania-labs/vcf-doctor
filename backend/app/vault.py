"""Secrets at rest: vCenter passwords and the Anthropic API key.

(Named vault rather than secrets so it never shadows the stdlib module.)

Stored values are Fernet tokens (AES-128-CBC plus HMAC-SHA256, authenticated)
carrying a version prefix so a plaintext row from an older build is
recognisable and migrated on startup.

Key source, in order:
  1. VCF_DOCTOR_SECRET_KEY in the environment (in production a Kubernetes
     SealedSecret, so it survives redeploys). A 44 character Fernet key is
     used as is; any other string is stretched with SHA-256 so a passphrase
     works too.
  2. A key file next to the SQLite database (<db name>.key, mode 0600),
     generated on first start so a fresh install runs with no setup.

Losing the key means the stored passwords cannot be read. The app keeps
running: those connections are flagged as needing credentials and the
operator re-enters the password, which is then stored under the current key.
Nothing else is affected. Rotation is the same operation on purpose: set the
new key, restart, re-enter.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import stat
import threading
from pathlib import Path
from typing import Literal

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings as cfg

log = logging.getLogger("vcf_doctor.vault")

ENV_KEY = "VCF_DOCTOR_SECRET_KEY"
PREFIX = "enc1:"
KeySource = Literal["env", "file"]


class SecretUnreadable(Exception):
    """The stored value is encrypted but the current key cannot open it."""


_lock = threading.Lock()
# (env value, db path) -> (Fernet, source, key file path). Re-derived when either
# input changes, which is what tests do when they point db at a temp file.
_cache: tuple[tuple[str, str], tuple[Fernet, KeySource, Path | None]] | None = None


def key_file_path() -> Path:
    db = Path(cfg.db_path)
    return db.with_name(db.stem + ".key")


def _normalise(raw: str) -> bytes:
    raw = raw.strip()
    try:
        Fernet(raw.encode())
        return raw.encode()
    except (ValueError, TypeError):
        # Not a Fernet key: treat it as a passphrase and stretch it.
        return base64.urlsafe_b64encode(hashlib.sha256(raw.encode()).digest())


def _read_key_file(path: Path) -> bytes:
    data = path.read_text().strip().encode()
    Fernet(data)  # raises if the file is corrupt; better to fail loudly here
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        log.warning("encryption key file %s is readable by others; expected mode 0600", path)
    return data


def _load_or_create_key_file(path: Path) -> bytes:
    if path.exists():
        return _read_key_file(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key()
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        # Another process (a second replica, a race at first boot) won; use its key.
        return _read_key_file(path)
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(key.decode() + "\n")
    except Exception:
        path.unlink(missing_ok=True)
        raise
    log.warning(
        "generated encryption key file %s; back it up or set %s in the environment",
        path,
        ENV_KEY,
    )
    return key


def _resolve() -> tuple[Fernet, KeySource, Path | None]:
    global _cache
    env = os.environ.get(ENV_KEY, "")
    ident = (env, cfg.db_path)
    with _lock:
        if _cache is not None and _cache[0] == ident:
            return _cache[1]
        if env.strip():
            resolved: tuple[Fernet, KeySource, Path | None] = (Fernet(_normalise(env)), "env", None)
        else:
            path = key_file_path()
            resolved = (Fernet(_load_or_create_key_file(path)), "file", path)
        _cache = (ident, resolved)
        return resolved


def key_source() -> KeySource:
    return _resolve()[1]


def is_encrypted(stored: str | None) -> bool:
    return isinstance(stored, str) and stored.startswith(PREFIX)


def encrypt(plain: str) -> str:
    fernet, _, _ = _resolve()
    return PREFIX + fernet.encrypt(plain.encode()).decode()


def decrypt(stored: str) -> str:
    """Open a stored value. Plaintext (no prefix) passes through unchanged so a
    row written by an older build still works until startup migration runs."""
    if not is_encrypted(stored):
        return stored
    fernet, _, _ = _resolve()
    try:
        return fernet.decrypt(stored[len(PREFIX) :].encode()).decode()
    except InvalidToken as exc:
        raise SecretUnreadable("stored secret cannot be decrypted with the current key") from exc


def readable(stored: str | None) -> bool:
    if stored is None:
        return True
    try:
        decrypt(stored)
        return True
    except SecretUnreadable:
        return False


def migrate_plaintext() -> int:
    """Encrypt any plaintext secret rows. One transaction, idempotent, safe to
    run on every startup. Returns the number of rows rewritten."""
    import json

    from app import db

    rewritten = 0
    with db.transaction() as c:
        for row in c.execute("SELECT id, password FROM connections").fetchall():
            if not is_encrypted(row["password"]):
                c.execute(
                    "UPDATE connections SET password = ? WHERE id = ?",
                    (encrypt(row["password"]), row["id"]),
                )
                rewritten += 1
        row = c.execute(
            "SELECT value FROM settings WHERE key = ?", ("assistant_api_key",)
        ).fetchone()
        if row is not None:
            try:
                value = json.loads(row["value"])
            except ValueError:
                value = None
            if isinstance(value, str) and value and not is_encrypted(value):
                c.execute(
                    "UPDATE settings SET value = ? WHERE key = ?",
                    (json.dumps(encrypt(value)), "assistant_api_key"),
                )
                rewritten += 1
    if rewritten:
        log.info("encrypted %d plaintext secret row(s)", rewritten)
    return rewritten


def reset_for_tests() -> None:
    global _cache
    with _lock:
        _cache = None

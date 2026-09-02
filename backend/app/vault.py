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
# Settings row set once the first migration has run. Before it exists every
# stored secret is legacy plaintext, whatever it looks like, so a plaintext
# password that happens to start with the prefix is still migrated correctly.
MIGRATED_KEY = "vault_migrated"
_KDF_SALT = b"vcf-doctor-vault-v1"
_KDF_N = 2**15
KeySource = Literal["env", "file"]


class SecretUnreadable(Exception):
    """The stored value is encrypted but the current key cannot open it."""


class KeyUnavailable(Exception):
    """No usable encryption key: the key file is corrupt or unreadable, or the
    environment value is malformed. Reads degrade to "needs credentials";
    writes are refused (API maps this to 503) until the operator fixes the key."""


_lock = threading.Lock()
# (env value, db path) -> (Fernet, source, key file path). Re-derived when either
# input changes, which is what tests do when they point db at a temp file.
_cache: dict[tuple[str, str], tuple[Fernet, KeySource, Path | None]] = {}


def key_file_path() -> Path:
    db = Path(cfg.db_path)
    return db.with_name(db.stem + ".key")


def _normalise(raw: str) -> bytes:
    raw = raw.strip()
    try:
        Fernet(raw.encode())
        return raw.encode()
    except (ValueError, TypeError):
        # Not a Fernet key: treat it as a passphrase. scrypt with a work factor
        # makes offline guessing against a copied database expensive; the salt
        # is fixed per application because the only place to keep a random one
        # would be the same database an attacker already holds. Runs once per
        # process (the result is cached), so the cost is paid at startup.
        derived = hashlib.scrypt(
            raw.encode(), salt=_KDF_SALT, n=_KDF_N, r=8, p=1, maxmem=64 * 1024 * 1024, dklen=32
        )
        return base64.urlsafe_b64encode(derived)


def _read_key_file(path: Path) -> bytes:
    try:
        data = path.read_text().strip().encode()
        Fernet(data)
    except (OSError, ValueError) as exc:
        raise KeyUnavailable(
            f"encryption key file {path} is unreadable or corrupt ({exc.__class__.__name__}); "
            "restore it from backup, or remove it to generate a new key and re-enter credentials"
        ) from exc
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        log.warning("encryption key file %s is readable by others; expected mode 0600", path)
    return data


def _load_or_create_key_file(path: Path) -> bytes:
    if path.exists():
        return _read_key_file(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key()
    # Write a private temp file, fsync it, then link it into place. The link
    # is atomic and exclusive, so a crash mid-write never leaves a half key
    # file behind and two processes racing at first boot agree on one key.
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(key.decode() + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        try:
            os.link(tmp, path)
        except FileExistsError:
            return _read_key_file(path)
    except OSError as exc:
        raise KeyUnavailable(f"cannot create encryption key file {path}: {exc}") from exc
    finally:
        tmp.unlink(missing_ok=True)
    log.warning(
        "generated encryption key file %s; back it up or set %s in the environment",
        path,
        ENV_KEY,
    )
    return key


def _resolve() -> tuple[Fernet, KeySource, Path | None]:
    env = os.environ.get(ENV_KEY, "")
    ident = (env, cfg.db_path)
    with _lock:
        if ident in _cache:
            return _cache[ident]
        if env.strip():
            resolved: tuple[Fernet, KeySource, Path | None] = (Fernet(_normalise(env)), "env", None)
        else:
            path = key_file_path()
            resolved = (Fernet(_load_or_create_key_file(path)), "file", path)
        _cache.clear()
        _cache[ident] = resolved
        return resolved


def key_error() -> str | None:
    """Why no key is usable, or None when everything is fine."""
    try:
        _resolve()
        return None
    except KeyUnavailable as exc:
        return str(exc)


def key_source() -> KeySource:
    return "env" if os.environ.get(ENV_KEY, "").strip() else "file"


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
    try:
        fernet, _, _ = _resolve()
        return fernet.decrypt(stored[len(PREFIX) :].encode()).decode()
    except (InvalidToken, KeyUnavailable) as exc:
        raise SecretUnreadable("stored secret cannot be decrypted with the current key") from exc


def readable(stored: str | None) -> bool:
    if stored is None:
        return True
    try:
        decrypt(stored)
        return True
    except SecretUnreadable:
        return False


def _genuine_token(stored: str) -> bool:
    """True only for a value this key produced; a plaintext that merely starts
    with the prefix fails the authentication check and is treated as plaintext."""
    if not is_encrypted(stored):
        return False
    try:
        decrypt(stored)
        return True
    except SecretUnreadable:
        return False


def migrate_plaintext() -> int:
    """Encrypt legacy plaintext secret rows. One transaction, idempotent, safe to
    run on every startup. Returns the number of rows rewritten.

    First run on a database (no MIGRATED_KEY row): every value that is not a
    token this key can open is plaintext and gets encrypted, then the marker is
    written in the same transaction. Later runs only touch unprefixed values,
    which can only appear if an older build wrote to the database afterwards.
    """
    import json

    from app import db

    first_run = db.get_setting(MIGRATED_KEY) is None

    def needs_encrypting(value: str) -> bool:
        return not _genuine_token(value) if first_run else not is_encrypted(value)

    rewritten = 0
    with db.transaction() as c:
        for row in c.execute("SELECT id, password FROM connections").fetchall():
            if needs_encrypting(row["password"]):
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
            if isinstance(value, str) and value and needs_encrypting(value):
                c.execute(
                    "UPDATE settings SET value = ? WHERE key = ?",
                    (json.dumps(encrypt(value)), "assistant_api_key"),
                )
                rewritten += 1
        if first_run:
            c.execute(
                "INSERT INTO settings(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (MIGRATED_KEY, json.dumps(1)),
            )
    if rewritten:
        log.info("encrypted %d plaintext secret row(s)", rewritten)
    return rewritten


def reset_for_tests() -> None:
    with _lock:
        _cache.clear()

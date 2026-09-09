"""Secrets at rest: vCenter passwords and the Anthropic API key.

(Named vault rather than secrets so it never shadows the stdlib module.)

Stored values are Fernet tokens (AES-128-CBC plus HMAC-SHA256, authenticated)
carrying a version prefix so a plaintext row from an older build is
recognisable and migrated on startup.

Key source, in order:
  1. VCF_DOCTOR_SECRET_KEY in the environment (in production a Kubernetes
     SealedSecret, so it survives redeploys). A 44 character Fernet key is
     used as is; any other string is stretched with scrypt so a passphrase
     works too.
  2. A key file next to the SQLite database (<db name>.key, mode 0600),
     generated on first start so a fresh install runs with no setup.

Losing the key means the stored passwords cannot be read. The app keeps
running: those connections are flagged as needing credentials and the
operator re-enters the password, which is then stored under the current key.
Nothing else is affected.

Rotation does not have to cost a re-entry. Given the previous key, `rekey`
re-encrypts every stored secret under the current one inside a single
transaction. It runs from VCF_DOCTOR_SECRET_KEY_PREVIOUS at startup, or from
one click on the generated key file a deployment left behind when it moved to
an env key. That second one is never automatic, so an env key set by mistake
stays recoverable by unsetting it. The key itself is never entered through the
interface. See docs/SECURITY.md for the operator procedure and recovery
contract.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import stat
import threading
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings as cfg

log = logging.getLogger("vcf_doctor.vault")

ENV_KEY = "VCF_DOCTOR_SECRET_KEY"
# Set alongside a new ENV_KEY for one restart: every secret still encrypted
# under it is moved to the new key at startup, so a rotation costs no re-entry.
ENV_PREVIOUS_KEY = "VCF_DOCTOR_SECRET_KEY_PREVIOUS"
PREFIX = "enc1:"
# Settings row set once the first migration has run. Before it exists every
# stored secret is legacy plaintext, whatever it looks like, so a plaintext
# password that happens to start with the prefix is still migrated correctly.
MIGRATED_KEY = "vault_migrated"
# Settings row holding the outcome of the last rekey, for the Settings card.
REKEY_KEY = "vault_rekey_last"
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


# ---- rotation -------------------------------------------------------------


@dataclass(frozen=True)
class RekeyOutcome:
    """What a rotation did. Persisted so the Settings encryption card can say
    what happened at the last startup, not only what is broken now."""

    at: str
    source: str
    rewritten: int
    unreadable: int
    error: str | None = None


def _previous_fernet(raw: str) -> Fernet:
    if not raw.strip():
        raise KeyUnavailable("no previous encryption key was supplied")
    return Fernet(_normalise(raw))


def _open_with(fernet: Fernet, stored: str) -> str | None:
    """Plaintext of a stored token under the given key, or None when the key
    does not authenticate it."""
    try:
        return fernet.decrypt(stored[len(PREFIX) :].encode()).decode()
    except InvalidToken:
        return None


def rekey(previous_key: str, source: str) -> RekeyOutcome:
    """Re-encrypt every stored secret the current key cannot open, using the
    supplied previous key.

    Recoverable rows and the outcome commit in one transaction, so an
    interrupted rotation cannot commit only some of its planned rewrites.
    Rows neither key opens remain untouched and are reported as unreadable.
    Rows the current key already opens are left alone, and legacy plaintext
    is left to migrate_plaintext.
    """
    from app import db

    _resolve()  # KeyUnavailable when there is no current key to move secrets to
    previous = _previous_fernet(previous_key)
    rewritten = 0
    unreadable = 0

    def moved(stored: str) -> str | None:
        """The value re-encrypted under the current key, or None to leave it."""
        nonlocal unreadable
        if not is_encrypted(stored):
            return None  # legacy plaintext; migrate_plaintext owns it
        if _genuine_token(stored):
            return None  # the current key already opens it
        plain = _open_with(previous, stored)
        if plain is None:
            unreadable += 1
            return None
        return encrypt(plain)

    with db.transaction() as c:
        for row in c.execute("SELECT id, password FROM connections").fetchall():
            value = moved(row["password"])
            if value is not None:
                c.execute(
                    "UPDATE connections SET password = ? WHERE id = ?", (value, row["id"])
                )
                rewritten += 1
        row = c.execute(
            "SELECT value FROM settings WHERE key = ?", ("assistant_api_key",)
        ).fetchone()
        if row is not None:
            try:
                stored = json.loads(row["value"])
            except ValueError:
                stored = None
            if isinstance(stored, str) and stored:
                value = moved(stored)
                if value is not None:
                    c.execute(
                        "UPDATE settings SET value = ? WHERE key = ?",
                        (json.dumps(value), "assistant_api_key"),
                    )
                    rewritten += 1
        error = None
        if unreadable:
            noun = "secret is" if unreadable == 1 else "secrets are"
            error = (
                f"{unreadable} stored {noun} encrypted with a key that was not supplied, "
                "so they were left untouched. Try the previous key they were stored "
                "under, or re-enter those credentials."
            )
        outcome = RekeyOutcome(
            at=datetime.now(UTC).isoformat(timespec="seconds"),
            source=source,
            rewritten=rewritten,
            unreadable=unreadable,
            error=error,
        )
        # Recorded in the same transaction as the rows it describes, so the
        # card never reports a rotation that was rolled back.
        if rewritten or unreadable:
            c.execute(
                "INSERT INTO settings(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (REKEY_KEY, json.dumps(asdict(outcome))),
            )
    if rewritten:
        log.info(
            "re-encrypted %d stored secret(s) under the current key (previous key from %s)",
            rewritten,
            source,
        )
    if error:
        log.warning("rotation from %s: %s", source, error)
    return outcome


def last_rekey() -> RekeyOutcome | None:
    """The recorded outcome of the last rotation, or None if none has run."""
    from app import db

    stored = db.get_setting(REKEY_KEY)
    if not isinstance(stored, dict):
        return None
    try:
        return RekeyOutcome(**stored)
    except TypeError:  # a row written by a different build
        return None


def previous_key_file() -> Path | None:
    """The generated key file left next to the database after a deployment has
    moved to an env key. Offered as a one-click rotation in Settings so moving
    to a sealed secret costs no re-entry, but never applied on its own: an env
    key set by mistake must stay recoverable by unsetting it again.
    """
    if key_source() != "env":
        return None
    path = key_file_path()
    return path if path.exists() else None


def read_previous_key_file() -> str:
    """The leftover key file's contents, for a rotation the operator asked for.
    Raises KeyUnavailable when there is nothing usable to read."""
    path = previous_key_file()
    if path is None:
        raise KeyUnavailable(
            "there is no generated key file next to the database to rotate from"
        )
    return _read_key_file(path).decode()


def rekey_at_startup() -> RekeyOutcome | None:
    """Rotate without re-entry when the deployment handed us the previous key in
    VCF_DOCTOR_SECRET_KEY_PREVIOUS. Returns None when it is not set. Runs before
    migrate_plaintext so a token under the old key is never mistaken for
    plaintext and encrypted twice.
    """
    raw = os.environ.get(ENV_PREVIOUS_KEY, "").strip()
    if not raw:
        return None
    return rekey(raw, ENV_PREVIOUS_KEY)


def reset_for_tests() -> None:
    with _lock:
        _cache.clear()
